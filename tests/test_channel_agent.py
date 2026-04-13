"""Tests for the channel agent."""

import asyncio
from datetime import datetime, timezone

import pytest
from pydantic import BaseModel

from chat_to_cop.agent.channel_agent import ChannelAgent, _is_noise
from chat_to_cop.models.cop_update import CoPUpdate, EntityUpdate, UpdateType
from chat_to_cop.models.messages import IRCMessage


def _msg(
    content: str,
    sender: str = "Hydro_Tank",
    channel: str = "#c2_coord",
    hour: int = 14,
    minute: int = 10,
    second: int = 0,
) -> IRCMessage:
    return IRCMessage(
        timestamp=datetime(2025, 9, 23, hour, minute, second, tzinfo=timezone.utc),
        channel=channel,
        sender=sender,
        content=content,
    )


class FakeBackend:
    """Mock backend that returns a preconfigured CoPUpdate."""

    def __init__(self, update_type: UpdateType = UpdateType.FUEL, confidence: float = 0.85):
        self.update_type = update_type
        self.confidence = confidence
        self.call_count = 0
        self.last_messages: list[dict] = []

    async def extract(self, messages: list[dict], schema: type[BaseModel]) -> CoPUpdate:
        self.call_count += 1
        self.last_messages = messages
        return CoPUpdate(
            update_type=self.update_type,
            confidence=self.confidence,
            extraction_method="llm",
            entities=[EntityUpdate(callsign="RR15", fuel_state="F+40")],
            source_channel="",
            source_speaker="",
            source_message="",
            timestamp=datetime(2025, 9, 23, 14, 0, 0, tzinfo=timezone.utc),
        )


class FakeNoneBackend:
    """Mock backend that always returns update_type=NONE."""

    async def extract(self, messages: list[dict], schema: type[BaseModel]) -> CoPUpdate:
        return CoPUpdate(
            update_type=UpdateType.NONE,
            confidence=0.0,
            extraction_method="llm",
            entities=[],
            source_channel="",
            source_speaker="",
            source_message="",
            timestamp=datetime(2025, 9, 23, 14, 0, 0, tzinfo=timezone.utc),
        )


class FakeErrorBackend:
    """Mock backend that always raises."""

    async def extract(self, messages: list[dict], schema: type[BaseModel]) -> CoPUpdate:
        raise RuntimeError("LLM is down")


class TestChannelAgentBasic:
    """Test basic agent behavior."""

    def test_process_returns_updates(self):
        async def run():
            agent = ChannelAgent("#c2_coord", FakeBackend())
            updates = await agent.process_message(_msg("RR15 F+40, RL36 F+50"))
            assert len(updates) == 1
            assert updates[0].update_type == UpdateType.FUEL
            assert updates[0].source_channel == "#c2_coord"
            assert updates[0].source_speaker == "Hydro_Tank"
            assert updates[0].source_message == "RR15 F+40, RL36 F+50"

        asyncio.run(run())

    def test_none_update_filtered(self):
        """ACK messages should return empty list."""

        async def run():
            agent = ChannelAgent("#c2_coord", FakeNoneBackend())
            updates = await agent.process_message(_msg("c"))
            assert updates == []

        asyncio.run(run())

    def test_error_returns_passthrough(self):
        """Backend errors should produce a passthrough, not crash."""

        async def run():
            agent = ChannelAgent("#c2_coord", FakeErrorBackend())
            updates = await agent.process_message(_msg("some message"))
            assert len(updates) == 1
            assert updates[0].update_type == UpdateType.NONE
            assert updates[0].confidence == 0.0
            assert updates[0].extraction_method == "error"
            assert "LLM is down" in updates[0].reasoning

        asyncio.run(run())

    def test_message_count_increments(self):
        async def run():
            agent = ChannelAgent("#c2_coord", FakeBackend())
            assert agent.message_count == 0
            await agent.process_message(_msg("msg 1"))
            assert agent.message_count == 1
            await agent.process_message(_msg("msg 2"))
            assert agent.message_count == 2

        asyncio.run(run())


class TestConversationWindow:
    """Test that the conversation window provides context."""

    def test_window_grows(self):
        async def run():
            backend = FakeBackend()
            agent = ChannelAgent("#c2_coord", backend)

            await agent.process_message(_msg("first message", second=0))
            await agent.process_message(_msg("second message", second=10))
            await agent.process_message(_msg("third message", second=20))

            assert len(agent._window) == 3

        asyncio.run(run())

    def test_window_capped_by_size(self):
        async def run():
            agent = ChannelAgent("#c2_coord", FakeBackend(), window_size=3)

            for i in range(5):
                await agent.process_message(_msg(f"msg {i}", second=i))

            assert len(agent._window) == 3
            # Should have the last 3
            contents = [m.content for m in agent._window]
            assert contents == ["msg 2", "msg 3", "msg 4"]

        asyncio.run(run())

    def test_context_included_in_prompt(self):
        """After multiple messages, the LLM should see conversation context."""

        async def run():
            backend = FakeBackend()
            agent = ChannelAgent("#c2_coord", backend)

            await agent.process_message(_msg("STARTEX", sender="WF_Clark", second=0))
            await agent.process_message(_msg("RR15 F+40", sender="Hydro_Tank", second=10))

            # System prompt + user message = at least 2 messages sent to backend
            assert len(backend.last_messages) == 2
            user_msg = backend.last_messages[-1]["content"]
            # Context should include both the prior message content AND the speaker
            assert "STARTEX" in user_msg, "Prior message content should appear in context"

        asyncio.run(run())

    def test_context_messages_in_output(self):
        """CoPUpdate should include recent context for audit."""

        async def run():
            backend = FakeBackend()
            agent = ChannelAgent("#c2_coord", backend)

            await agent.process_message(_msg("first", second=0))
            updates = await agent.process_message(_msg("second", second=10))

            assert len(updates) == 1
            # After 2 messages, context should contain at least the first message
            assert len(updates[0].context_messages) >= 1
            assert any("first" in c for c in updates[0].context_messages), \
                "Prior message content should appear in context_messages"

        asyncio.run(run())


class TestNoiseDetectionRadioChecks:
    """Radio check messages should be filtered as noise."""

    @pytest.mark.parametrize(
        "msg",
        [
            "radio check",
            "Radio Check Radio Check C2 cord.",
            "Jarhead you're loud and clear the Vegas SL how me?",
            "How do you hear me?",
            "All right, let's do some radio tracks. How do you hear me?",
            "loud and clear",
            "Lima Charlie",
            "say again",
        ],
    )
    def test_radio_check_is_noise(self, msg):
        assert _is_noise(msg), f"Expected noise: {msg!r}"

    @pytest.mark.parametrize(
        "msg",
        [
            "over and out",
            "You can't go over and out.",
        ],
    )
    def test_over_and_out_is_noise(self, msg):
        assert _is_noise(msg), f"Expected noise: {msg!r}"


class TestNoiseDetectionBanter:
    """Banter and greetings should be filtered as noise."""

    @pytest.mark.parametrize(
        "msg",
        [
            "Buh-bye now.",
            "buhbye",
            "see ya",
            "Oh, did you finish talking and you didn't say over, over",
            "No, my transmission was over, but I'm not out, I'm over",
            "Hey, what you want girl?",
            "Talk a little louder, sorry.",
            "copy all",
        ],
    )
    def test_banter_is_noise(self, msg):
        assert _is_noise(msg), f"Expected noise: {msg!r}"


class TestNoiseDetectionOperationalNotFiltered:
    """Operational messages containing radio-check-like phrases must NOT be filtered."""

    @pytest.mark.parametrize(
        "msg",
        [
            "loud and clear, SITREP follows: ZEUS12 RTB",
            "copy all, ZEUS12 shot down",
            "say again, TM636 bearing 315 range 80",
            "RR15 F+40, RL36 F+50",
            "ZEUS 12,13,14 shot down by TTG",
            "tacrep, 4th-gen sam active, cigar 316/398, jtn TM677",
            "ORCA01 gadget bent, RTB",
        ],
    )
    def test_operational_not_noise(self, msg):
        assert not _is_noise(msg), f"Should NOT be noise: {msg!r}"


class TestNoiseDetectionIntegration:
    """Test that radio check and banter messages are filtered in the full agent pipeline."""

    def test_radio_check_skips_llm(self):
        async def run():
            backend = FakeBackend()
            agent = ChannelAgent("#stt_c2", backend)
            updates = await agent.process_message(_msg("Jarhead you're loud and clear the Vegas SL how me?"))
            assert updates == []
            assert backend.call_count == 0  # LLM should NOT be called

        asyncio.run(run())

    def test_banter_skips_llm(self):
        async def run():
            backend = FakeBackend()
            agent = ChannelAgent("#stt_c2", backend)
            updates = await agent.process_message(_msg("Buh-bye now."))
            assert updates == []
            assert backend.call_count == 0

        asyncio.run(run())

    def test_operational_message_reaches_llm(self):
        async def run():
            backend = FakeBackend()
            agent = ChannelAgent("#stt_c2", backend)
            updates = await agent.process_message(_msg("copy all, ZEUS12 shot down by TTG"))
            assert len(updates) == 1
            assert backend.call_count == 1

        asyncio.run(run())

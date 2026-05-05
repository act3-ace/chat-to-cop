"""Tests for speaker models: incremental learning, prompt formatting, agent integration, persistence."""

import asyncio
from datetime import datetime, timezone

from pydantic import BaseModel

from chat_to_cop.agent.channel_agent import _SPEAKER_FIRST_INFERENCE_AT, ChannelAgent
from chat_to_cop.models.cop_update import CoPUpdate, EntityUpdate, UpdateType
from chat_to_cop.models.messages import IRCMessage
from chat_to_cop.models.speaker import SpeakerInference, SpeakerModel, SpeakerRegistry
from chat_to_cop.output.store import WorldStateStore


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


def _now() -> datetime:
    return datetime(2025, 9, 23, 14, 0, 0, tzinfo=timezone.utc)


class FakeBackend:
    """Mock backend that returns a preconfigured CoPUpdate."""

    def __init__(self, update_type: UpdateType = UpdateType.FUEL, confidence: float = 0.85):
        self.update_type = update_type
        self.confidence = confidence
        self.call_count = 0
        self.last_messages: list[dict] = []
        self.last_schema: type[BaseModel] | None = None

    async def extract(self, messages: list[dict], schema: type[BaseModel]) -> BaseModel:
        self.call_count += 1
        self.last_messages = messages
        self.last_schema = schema

        if schema is SpeakerInference:
            return SpeakerInference(
                role="tanker controller",
                area_of_interest="Hydro BMA",
                jargon_notes=["F+XX = fuel above frag"],
                topics=["fuel state", "tanker ops"],
                goals=["manage aerial refueling"],
                sdac_category="sensing",
            )

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

    async def extract(self, messages: list[dict], schema: type[BaseModel]) -> BaseModel:
        if schema is SpeakerInference:
            return SpeakerInference()
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


# =============================================================================
# SpeakerModel unit tests
# =============================================================================


class TestSpeakerModelBasic:
    def test_create_speaker(self):
        model = SpeakerModel(username="Hydro_Tank", first_seen=_now(), last_seen=_now())
        assert model.username == "Hydro_Tank"
        assert model.message_count == 0
        assert model.role is None
        assert model.reliability == 0.5
        assert model.primary_sdac_role is None

    def test_update_from_message_increments(self):
        model = SpeakerModel(username="Hydro_Tank", first_seen=_now(), last_seen=_now())
        msg = _msg("RR15 F+40")
        model.update_from_message(msg)
        assert model.message_count == 1
        assert model.last_seen == msg.timestamp
        assert len(model.recent_messages) == 1
        assert model.recent_messages[0] == "RR15 F+40"

    def test_update_from_message_caps_recent(self):
        model = SpeakerModel(username="Hydro_Tank", first_seen=_now(), last_seen=_now())
        for i in range(15):
            model.update_from_message(_msg(f"msg {i}", second=i))
        assert model.message_count == 15
        assert len(model.recent_messages) == 10
        assert model.recent_messages[0] == "msg 5"
        assert model.recent_messages[-1] == "msg 14"

    def test_update_from_inference(self):
        model = SpeakerModel(username="Hydro_Tank", first_seen=_now(), last_seen=_now())
        model.update_from_inference(
            role="tanker controller",
            area_of_interest="Hydro BMA",
            jargon_notes=["F+XX = fuel above frag"],
            topics=["fuel state"],
            goals=["manage AR"],
            sdac_category="sensing",
        )
        assert model.role == "tanker controller"
        assert model.area_of_interest == "Hydro BMA"
        assert "F+XX = fuel above frag" in model.jargon_notes
        assert "fuel state" in model.topics
        assert "manage AR" in model.goals
        assert model.sensing_count == 1

    def test_inference_merges_not_replaces(self):
        model = SpeakerModel(username="Hydro_Tank", first_seen=_now(), last_seen=_now())
        model.update_from_inference(jargon_notes=["F+XX = fuel above frag"])
        model.update_from_inference(jargon_notes=["RTB = return to base", "F+XX = fuel above frag"])
        assert len(model.jargon_notes) == 2
        assert "F+XX = fuel above frag" in model.jargon_notes
        assert "RTB = return to base" in model.jargon_notes

    def test_sdac_categories(self):
        model = SpeakerModel(username="Hydro_Tank", first_seen=_now(), last_seen=_now())
        model.update_from_inference(sdac_category="sensing")
        model.update_from_inference(sdac_category="sensing")
        model.update_from_inference(sdac_category="acting")
        assert model.sensing_count == 2
        assert model.acting_count == 1
        assert model.primary_sdac_role == "sensing"

    def test_primary_sdac_role_none_when_empty(self):
        model = SpeakerModel(username="Hydro_Tank", first_seen=_now(), last_seen=_now())
        assert model.primary_sdac_role is None


class TestSpeakerModelPromptFormatting:
    def test_minimal_format(self):
        model = SpeakerModel(username="Hydro_Tank", first_seen=_now(), last_seen=_now(), message_count=5)
        formatted = model.format_for_prompt()
        assert "Hydro_Tank" in formatted
        assert "msgs=5" in formatted
        assert "reliability=0.5" in formatted

    def test_full_format(self):
        model = SpeakerModel(
            username="Hydro_Tank",
            first_seen=_now(),
            last_seen=_now(),
            message_count=10,
            role="tanker controller",
            area_of_interest="Hydro BMA",
            topics=["fuel state", "tanker ops"],
            jargon_notes=["F+XX"],
            sensing_count=5,
            acting_count=2,
        )
        formatted = model.format_for_prompt()
        assert "role=tanker controller" in formatted
        assert "area=Hydro BMA" in formatted
        assert "fuel state" in formatted
        assert "F+XX" in formatted
        assert "primary_activity=sensing" in formatted


# =============================================================================
# SpeakerRegistry tests
# =============================================================================


class TestSpeakerRegistry:
    def test_get_or_create_new(self):
        registry = SpeakerRegistry()
        model = registry.get_or_create("Hydro_Tank", _now())
        assert model.username == "Hydro_Tank"
        assert model.message_count == 0

    def test_get_or_create_existing(self):
        registry = SpeakerRegistry()
        m1 = registry.get_or_create("Hydro_Tank", _now())
        m1.message_count = 5
        m2 = registry.get_or_create("Hydro_Tank", _now())
        assert m2.message_count == 5
        assert m1 is m2

    def test_get_missing(self):
        registry = SpeakerRegistry()
        assert registry.get("NONEXISTENT") is None

    def test_format_all_for_prompt(self):
        registry = SpeakerRegistry()
        s1 = registry.get_or_create("Hydro_Tank", _now())
        s1.message_count = 3
        s1.role = "tanker controller"
        s2 = registry.get_or_create("WF_Clark", _now())
        s2.message_count = 5
        s2.role = "white cell"

        formatted = registry.format_all_for_prompt()
        assert "Hydro_Tank" in formatted
        assert "WF_Clark" in formatted
        assert "tanker controller" in formatted

    def test_format_empty_registry(self):
        registry = SpeakerRegistry()
        assert registry.format_all_for_prompt() == ""

    def test_load_speaker(self):
        registry = SpeakerRegistry()
        model = SpeakerModel(
            username="Hydro_Tank", first_seen=_now(), last_seen=_now(), message_count=10, role="tanker"
        )
        registry.load_speaker(model)
        loaded = registry.get("Hydro_Tank")
        assert loaded is not None
        assert loaded.role == "tanker"
        assert loaded.message_count == 10

    def test_json_roundtrip(self):
        registry = SpeakerRegistry()
        s1 = registry.get_or_create("Hydro_Tank", _now())
        s1.message_count = 5
        s1.role = "tanker controller"
        s1.jargon_notes = ["F+XX"]

        json_data = registry.to_json_list()
        restored = SpeakerRegistry.from_json_list(json_data)
        restored_model = restored.get("Hydro_Tank")
        assert restored_model is not None
        assert restored_model.role == "tanker controller"
        assert restored_model.message_count == 5
        assert "F+XX" in restored_model.jargon_notes


# =============================================================================
# SpeakerInference schema tests
# =============================================================================


class TestSpeakerInference:
    def test_minimal(self):
        inf = SpeakerInference()
        assert inf.role is None
        assert inf.sdac_category is None

    def test_full(self):
        inf = SpeakerInference(
            role="tanker controller",
            area_of_interest="Hydro BMA",
            jargon_notes=["F+XX"],
            topics=["fuel"],
            goals=["manage AR"],
            cues=["fuel requests"],
            expectancies=["tanker positions"],
            typical_actions=["direct refueling"],
            sdac_category="sensing",
        )
        assert inf.role == "tanker controller"
        assert inf.sdac_category == "sensing"


# =============================================================================
# Agent integration: speaker models in ChannelAgent
# =============================================================================


class TestAgentSpeakerIntegration:
    def test_speaker_model_created_on_first_message(self):
        async def run():
            backend = FakeBackend()
            agent = ChannelAgent("#c2_coord", backend, use_speaker_models=True)
            await agent.process_message(_msg("RR15 F+40", sender="Hydro_Tank"))

            model = agent.speakers.get("Hydro_Tank")
            assert model is not None
            assert model.message_count == 1
            assert "RR15 F+40" in model.recent_messages

        asyncio.run(run())

    def test_speaker_model_updates_on_each_message(self):
        async def run():
            backend = FakeBackend()
            agent = ChannelAgent("#c2_coord", backend, use_speaker_models=True)

            for i in range(5):
                await agent.process_message(_msg(f"msg {i}", sender="Hydro_Tank", second=i))

            model = agent.speakers.get("Hydro_Tank")
            assert model is not None
            assert model.message_count == 5
            assert len(model.recent_messages) == 5

        asyncio.run(run())

    def test_multiple_speakers_tracked(self):
        async def run():
            backend = FakeBackend()
            agent = ChannelAgent("#c2_coord", backend, use_speaker_models=True)

            await agent.process_message(_msg("msg 1", sender="Hydro_Tank", second=0))
            await agent.process_message(_msg("msg 2", sender="WF_Clark", second=1))
            await agent.process_message(_msg("msg 3", sender="Hydro_Tank", second=2))

            assert agent.speakers.get("Hydro_Tank") is not None
            assert agent.speakers.get("WF_Clark") is not None
            assert agent.speakers.get("Hydro_Tank").message_count == 2
            assert agent.speakers.get("WF_Clark").message_count == 1

        asyncio.run(run())

    def test_speaker_context_in_prompt(self):
        async def run():
            backend = FakeBackend()
            agent = ChannelAgent("#c2_coord", backend, use_speaker_models=True)

            # Process enough messages to populate speaker model
            for i in range(3):
                await agent.process_message(_msg(f"RR15 F+{40 + i}", sender="Hydro_Tank", second=i))

            # The system prompt should include KNOWN SPEAKERS
            system_msg = backend.last_messages[0]["content"]
            assert "KNOWN SPEAKERS" in system_msg
            assert "Hydro_Tank" in system_msg

        asyncio.run(run())

    def test_speaker_context_excluded_when_disabled(self):
        async def run():
            backend = FakeBackend()
            agent = ChannelAgent("#c2_coord", backend, use_speaker_models=False)

            for i in range(5):
                await agent.process_message(_msg(f"msg {i}", sender="Hydro_Tank", second=i))

            # No speaker models should be tracked
            assert agent.speakers.get("Hydro_Tank") is None

            # System prompt should NOT include KNOWN SPEAKERS
            system_msg = backend.last_messages[0]["content"]
            assert "KNOWN SPEAKERS" not in system_msg

        asyncio.run(run())

    def test_speaker_inference_triggers_at_threshold(self):
        async def run():
            backend = FakeBackend()
            agent = ChannelAgent("#c2_coord", backend, use_speaker_models=True)

            # Process exactly _SPEAKER_FIRST_INFERENCE_AT messages
            for i in range(_SPEAKER_FIRST_INFERENCE_AT):
                await agent.process_message(_msg(f"msg {i}", sender="Hydro_Tank", second=i))

            model = agent.speakers.get("Hydro_Tank")
            assert model is not None
            # After inference, role should be set by FakeBackend
            assert model.role == "tanker controller"
            assert model.area_of_interest == "Hydro BMA"
            assert model.sensing_count == 1

        asyncio.run(run())

    def test_speaker_inference_failure_does_not_block(self):
        """If speaker inference fails, message extraction still works."""

        class FailingSpeakerBackend:
            def __init__(self):
                self.call_count = 0

            async def extract(self, messages, schema):
                self.call_count += 1
                if schema is SpeakerInference:
                    raise RuntimeError("Speaker inference failed")
                return CoPUpdate(
                    update_type=UpdateType.FUEL,
                    confidence=0.85,
                    extraction_method="llm",
                    entities=[EntityUpdate(callsign="RR15", fuel_state="F+40")],
                    source_channel="",
                    source_speaker="",
                    source_message="",
                    timestamp=datetime(2025, 9, 23, 14, 0, 0, tzinfo=timezone.utc),
                )

        async def run():
            backend = FailingSpeakerBackend()
            agent = ChannelAgent("#c2_coord", backend, use_speaker_models=True)

            # Process enough to trigger inference
            for i in range(_SPEAKER_FIRST_INFERENCE_AT):
                updates = await agent.process_message(_msg(f"msg {i}", sender="Hydro_Tank", second=i))

            # Extraction should still work despite speaker inference failure
            assert len(updates) == 1
            assert updates[0].update_type == UpdateType.FUEL

            # Speaker model should still have basic data
            model = agent.speakers.get("Hydro_Tank")
            assert model is not None
            assert model.message_count == _SPEAKER_FIRST_INFERENCE_AT
            assert model.role is None  # Inference failed, so no role

        asyncio.run(run())

    def test_existing_tests_still_pass_with_speakers(self):
        """Verify backward compatibility: existing agent tests still work."""

        async def run():
            agent = ChannelAgent("#c2_coord", FakeBackend(), use_speaker_models=True)
            updates = await agent.process_message(_msg("RR15 F+40, RL36 F+50"))
            assert len(updates) == 1
            assert updates[0].update_type == UpdateType.FUEL
            assert updates[0].source_channel == "#c2_coord"
            assert updates[0].source_speaker == "Hydro_Tank"

        asyncio.run(run())

    def test_none_update_filtered_with_speakers(self):
        async def run():
            agent = ChannelAgent("#c2_coord", FakeNoneBackend(), use_speaker_models=True)
            updates = await agent.process_message(_msg("c"))
            assert updates == []

        asyncio.run(run())


# =============================================================================
# Store persistence tests
# =============================================================================


class TestStoreSpeakerPersistence:
    def test_write_and_read_speaker(self):
        async def run():
            async with WorldStateStore(":memory:") as store:
                model = SpeakerModel(
                    username="Hydro_Tank",
                    first_seen=_now(),
                    last_seen=_now(),
                    message_count=10,
                    role="tanker controller",
                    area_of_interest="Hydro BMA",
                    jargon_notes=["F+XX"],
                )
                await store.write_speaker(model, "#c2_coord")

                loaded = await store.get_speaker("Hydro_Tank", "#c2_coord")
                assert loaded is not None
                assert loaded.username == "Hydro_Tank"
                assert loaded.role == "tanker controller"
                assert loaded.message_count == 10
                assert "F+XX" in loaded.jargon_notes

        asyncio.run(run())

    def test_upsert_speaker(self):
        async def run():
            async with WorldStateStore(":memory:") as store:
                model1 = SpeakerModel(username="Hydro_Tank", first_seen=_now(), last_seen=_now(), message_count=5)
                await store.write_speaker(model1, "#c2_coord")

                model2 = SpeakerModel(
                    username="Hydro_Tank",
                    first_seen=_now(),
                    last_seen=_now(),
                    message_count=15,
                    role="tanker controller",
                )
                await store.write_speaker(model2, "#c2_coord")

                loaded = await store.get_speaker("Hydro_Tank", "#c2_coord")
                assert loaded is not None
                assert loaded.message_count == 15
                assert loaded.role == "tanker controller"

        asyncio.run(run())

    def test_get_missing_speaker(self):
        async def run():
            async with WorldStateStore(":memory:") as store:
                result = await store.get_speaker("NONEXISTENT", "#c2_coord")
                assert result is None

        asyncio.run(run())

    def test_get_speakers_by_channel(self):
        async def run():
            async with WorldStateStore(":memory:") as store:
                m1 = SpeakerModel(username="Hydro_Tank", first_seen=_now(), last_seen=_now(), message_count=10)
                m2 = SpeakerModel(username="WF_Clark", first_seen=_now(), last_seen=_now(), message_count=20)
                m3 = SpeakerModel(username="Fires_SL", first_seen=_now(), last_seen=_now(), message_count=5)
                await store.write_speaker(m1, "#c2_coord")
                await store.write_speaker(m2, "#c2_coord")
                await store.write_speaker(m3, "#fires")

                c2_speakers = await store.get_speakers("#c2_coord")
                assert len(c2_speakers) == 2
                # Ordered by message_count DESC
                assert c2_speakers[0].username == "WF_Clark"
                assert c2_speakers[1].username == "Hydro_Tank"

                fires_speakers = await store.get_speakers("#fires")
                assert len(fires_speakers) == 1
                assert fires_speakers[0].username == "Fires_SL"

        asyncio.run(run())

    def test_speaker_same_user_different_channels(self):
        async def run():
            async with WorldStateStore(":memory:") as store:
                m1 = SpeakerModel(
                    username="Hydro_Tank", first_seen=_now(), last_seen=_now(), message_count=10, role="tanker"
                )
                m2 = SpeakerModel(
                    username="Hydro_Tank", first_seen=_now(), last_seen=_now(), message_count=3, role="coordinator"
                )
                await store.write_speaker(m1, "#c2_coord")
                await store.write_speaker(m2, "#fires")

                s1 = await store.get_speaker("Hydro_Tank", "#c2_coord")
                s2 = await store.get_speaker("Hydro_Tank", "#fires")
                assert s1 is not None and s2 is not None
                assert s1.role == "tanker"
                assert s2.role == "coordinator"

        asyncio.run(run())

    def test_roundtrip_preserves_cta_fields(self):
        async def run():
            async with WorldStateStore(":memory:") as store:
                model = SpeakerModel(
                    username="WF_Clark",
                    first_seen=_now(),
                    last_seen=_now(),
                    message_count=20,
                    role="white cell",
                    goals=["run the exercise"],
                    cues=["timing marks"],
                    expectancies=["phase transitions"],
                    typical_actions=["issue inject", "call STARTEX"],
                    sensing_count=3,
                    deciding_count=8,
                    acting_count=5,
                    collaborating_count=4,
                )
                await store.write_speaker(model, "#c2_coord")

                loaded = await store.get_speaker("WF_Clark", "#c2_coord")
                assert loaded is not None
                assert loaded.goals == ["run the exercise"]
                assert loaded.cues == ["timing marks"]
                assert loaded.expectancies == ["phase transitions"]
                assert loaded.typical_actions == ["issue inject", "call STARTEX"]
                assert loaded.sensing_count == 3
                assert loaded.deciding_count == 8
                assert loaded.primary_sdac_role == "deciding"

        asyncio.run(run())

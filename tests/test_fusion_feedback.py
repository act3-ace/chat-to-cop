"""Tests for fusion-to-channel feedback loop: cross-channel corroboration improves speaker models."""

import asyncio
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel

from chat_to_cop.agent.channel_agent import ChannelAgent
from chat_to_cop.agent.fusion_agent import FusionAgent
from chat_to_cop.agent.supervisor import Supervisor
from chat_to_cop.models.cop_update import CoPUpdate, EntityUpdate, UpdateType
from chat_to_cop.models.feedback import FusionFeedback
from chat_to_cop.models.messages import IRCMessage
from chat_to_cop.models.speaker import SpeakerInference, SpeakerModel, SpeakerRegistry


def _now() -> datetime:
    return datetime(2025, 9, 23, 14, 0, 0, tzinfo=timezone.utc)


def _update(
    track: str | None = "44504",
    callsign: str | None = None,
    channel: str = "#c2_coord",
    speaker: str = "Hydro_SL",
    confidence: float = 0.8,
    update_type: UpdateType = UpdateType.ENTITY_ID,
    status: str | None = None,
    affiliation: str | None = None,
    message: str = "test message",
    seconds_offset: int = 0,
) -> CoPUpdate:
    """Helper to create a CoPUpdate for testing."""
    entities = []
    if track or callsign:
        entities.append(
            EntityUpdate(
                track_number=track,
                callsign=callsign,
                operational_status=status,
                affiliation=affiliation,
            )
        )
    base = datetime(2025, 9, 23, 14, 0, 0, tzinfo=timezone.utc)
    return CoPUpdate(
        update_type=update_type,
        confidence=confidence,
        extraction_method="llm",
        entities=entities,
        source_channel=channel,
        source_speaker=speaker,
        source_message=message,
        timestamp=base + timedelta(seconds=seconds_offset),
    )


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

    async def extract(self, messages: list[dict], schema: type[BaseModel]) -> BaseModel:
        if schema is SpeakerInference:
            return SpeakerInference()
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


# =============================================================================
# FusionFeedback data class tests
# =============================================================================


class TestFusionFeedbackCreation:
    def test_fusion_agent_generates_feedback_on_corroboration(self):
        """FusionAgent should produce FusionFeedback when two channels
        report the same entity — testing production code, not dataclass init."""
        from chat_to_cop.agent.fusion_agent import FusionAgent
        from chat_to_cop.models.cop_update import CoPUpdate, EntityUpdate, UpdateType

        updates = [
            CoPUpdate(
                update_type=UpdateType.STATUS_CHANGE,
                confidence=0.8,
                extraction_method="llm",
                entities=[EntityUpdate(track_number="44504", operational_status="DESTROYED")],
                source_channel="#c2_coord",
                source_speaker="Alice",
                source_message="44504 destroyed",
                timestamp=datetime(2025, 9, 23, 14, 0, 0, tzinfo=timezone.utc),
            ),
            CoPUpdate(
                update_type=UpdateType.STATUS_CHANGE,
                confidence=0.7,
                extraction_method="llm",
                entities=[EntityUpdate(track_number="44504", operational_status="DESTROYED")],
                source_channel="#fires",
                source_speaker="Bob",
                source_message="confirmed 44504 down",
                timestamp=datetime(2025, 9, 23, 14, 0, 5, tzinfo=timezone.utc),
            ),
        ]
        agent = FusionAgent()
        agent.process_updates(updates)
        feedback = agent.generate_feedback(updates)
        # Two channels reporting the same entity should generate corroboration feedback
        assert len(feedback) >= 1
        assert any(fb.feedback_type == "corroborated" for fb in feedback)

    def test_defaults(self):
        fb = FusionFeedback(
            update_id="test",
            source_channel="#ch",
            source_speaker="Bob",
            feedback_type="deduplicated",
        )
        assert fb.corroborating_channels == []
        assert fb.confidence_delta == 0.0

    def test_all_feedback_types_accepted(self):
        """All three feedback types should be constructible (API contract)."""
        for ft in ("corroborated", "contradicted", "deduplicated"):
            fb = FusionFeedback(
                update_id="test",
                source_channel="#ch",
                source_speaker="Bob",
                feedback_type=ft,
            )
            assert fb.feedback_type == ft


# =============================================================================
# SpeakerModel reliability update tests
# =============================================================================


class TestSpeakerReliabilityFromFeedback:
    def test_corroboration_increases_reliability(self):
        model = SpeakerModel(username="Alice", first_seen=_now(), last_seen=_now())
        assert model.reliability_score == 0.5

        fb = FusionFeedback(
            update_id="test",
            source_channel="#c2_coord",
            source_speaker="Alice",
            feedback_type="corroborated",
            corroborating_channels=["#fires"],
            confidence_delta=0.1,
        )
        model.update_from_fusion_feedback(fb)
        # EMA: 0.9 * 0.5 + 0.1 * 1.0 = 0.55
        assert model.reliability_score == 0.55
        assert model.cross_channel_corroborations == 1
        assert model.cross_channel_contradictions == 0

    def test_contradiction_decreases_reliability(self):
        model = SpeakerModel(username="Bob", first_seen=_now(), last_seen=_now())
        assert model.reliability_score == 0.5

        fb = FusionFeedback(
            update_id="test",
            source_channel="#fires",
            source_speaker="Bob",
            feedback_type="contradicted",
            corroborating_channels=["#c2_coord"],
            confidence_delta=-0.2,
        )
        model.update_from_fusion_feedback(fb)
        # EMA: 0.9 * 0.5 + 0.1 * 0.0 = 0.45
        assert model.reliability_score == 0.45
        assert model.cross_channel_corroborations == 0
        assert model.cross_channel_contradictions == 1

    def test_multiple_corroborations_converge_toward_one(self):
        model = SpeakerModel(username="Alice", first_seen=_now(), last_seen=_now())
        for _ in range(20):
            fb = FusionFeedback(
                update_id="test",
                source_channel="#c2_coord",
                source_speaker="Alice",
                feedback_type="corroborated",
            )
            model.update_from_fusion_feedback(fb)
        assert model.reliability_score > 0.85
        assert model.cross_channel_corroborations == 20

    def test_multiple_contradictions_converge_toward_zero(self):
        model = SpeakerModel(username="Bob", first_seen=_now(), last_seen=_now())
        for _ in range(20):
            fb = FusionFeedback(
                update_id="test",
                source_channel="#fires",
                source_speaker="Bob",
                feedback_type="contradicted",
            )
            model.update_from_fusion_feedback(fb)
        assert model.reliability_score < 0.15
        assert model.cross_channel_contradictions == 20

    def test_reliability_stays_bounded_at_zero(self):
        model = SpeakerModel(username="Bob", first_seen=_now(), last_seen=_now(), reliability_score=0.0)
        fb = FusionFeedback(
            update_id="test",
            source_channel="#fires",
            source_speaker="Bob",
            feedback_type="contradicted",
        )
        model.update_from_fusion_feedback(fb)
        assert model.reliability_score >= 0.0

    def test_reliability_stays_bounded_at_one(self):
        model = SpeakerModel(username="Alice", first_seen=_now(), last_seen=_now(), reliability_score=1.0)
        fb = FusionFeedback(
            update_id="test",
            source_channel="#c2_coord",
            source_speaker="Alice",
            feedback_type="corroborated",
        )
        model.update_from_fusion_feedback(fb)
        assert model.reliability_score <= 1.0

    def test_deduplication_does_not_change_reliability(self):
        model = SpeakerModel(username="Alice", first_seen=_now(), last_seen=_now())
        original_score = model.reliability_score
        fb = FusionFeedback(
            update_id="test",
            source_channel="#c2_coord",
            source_speaker="Alice",
            feedback_type="deduplicated",
        )
        model.update_from_fusion_feedback(fb)
        assert model.reliability_score == original_score
        assert model.cross_channel_corroborations == 0
        assert model.cross_channel_contradictions == 0


# =============================================================================
# FusionAgent.generate_feedback() tests
# =============================================================================


class TestFusionAgentGeneratesFeedback:
    def test_corroborated_updates_generate_feedback(self):
        """Cross-channel agreement should generate corroboration feedback."""
        agent = FusionAgent()
        updates = [
            _update(track="44504", channel="#c2_coord", speaker="Alice", status="DESTROYED"),
            _update(track="44504", channel="#fires", speaker="Bob", status="DESTROYED"),
        ]
        agent.process_updates(updates)
        feedback = agent.generate_feedback(updates)

        assert len(feedback) >= 2  # One per speaker involved
        corroborated = [f for f in feedback if f.feedback_type == "corroborated"]
        assert len(corroborated) == 2
        speakers = {f.source_speaker for f in corroborated}
        assert speakers == {"Alice", "Bob"}

    def test_contradicted_updates_generate_feedback(self):
        """Cross-channel contradiction should generate mixed feedback."""
        agent = FusionAgent()
        updates = [
            _update(track="44504", channel="#c2_coord", speaker="Alice", confidence=0.9, status="OPERATIONAL"),
            _update(track="44504", channel="#fires", speaker="Bob", confidence=0.5, status="DESTROYED"),
        ]
        agent.process_updates(updates)
        feedback = agent.generate_feedback(updates)

        # Alice (primary, higher confidence) should get corroborated
        alice_fb = [f for f in feedback if f.source_speaker == "Alice"]
        assert len(alice_fb) == 1
        assert alice_fb[0].feedback_type == "corroborated"

        # Bob (contradicted) should get contradicted
        bob_fb = [f for f in feedback if f.source_speaker == "Bob"]
        assert len(bob_fb) == 1
        assert bob_fb[0].feedback_type == "contradicted"

    def test_deduplicated_updates_generate_feedback(self):
        """Same-channel duplicates should generate deduplication feedback."""
        agent = FusionAgent()
        updates = [
            _update(track="44504", channel="#c2_coord", speaker="Alice"),
            _update(track="44504", channel="#c2_coord", speaker="Bob"),
        ]
        agent.process_updates(updates)
        feedback = agent.generate_feedback(updates)

        dedup = [f for f in feedback if f.feedback_type == "deduplicated"]
        assert len(dedup) == 2

    def test_no_feedback_for_single_update(self):
        """Single update with no group merge should generate no feedback."""
        agent = FusionAgent()
        updates = [_update(track="44504", channel="#c2_coord", speaker="Alice")]
        agent.process_updates(updates)
        feedback = agent.generate_feedback(updates)
        assert len(feedback) == 0

    def test_feedback_cleared_between_calls(self):
        """Each process_updates() should clear prior feedback."""
        agent = FusionAgent()
        updates1 = [
            _update(track="44504", channel="#c2_coord", speaker="Alice", status="DESTROYED"),
            _update(track="44504", channel="#fires", speaker="Bob", status="DESTROYED"),
        ]
        agent.process_updates(updates1)
        fb1 = agent.generate_feedback(updates1)
        assert len(fb1) > 0

        # Empty batch should clear feedback
        agent.process_updates([])
        fb2 = agent.generate_feedback([])
        assert len(fb2) == 0


# =============================================================================
# Pipeline integration: feedback reaches channel agent speaker models
# =============================================================================


class TestPipelineFeedbackRouting:
    def test_feedback_reaches_speaker_model(self):
        """End-to-end: fusion feedback should update channel agent speaker models."""

        async def run():
            backend = FakeBackend()
            agent = ChannelAgent("#c2_coord", backend, use_speaker_models=True)

            # Send a message so the speaker model gets created
            await agent.process_message(_msg("RR15 F+40", sender="Alice", channel="#c2_coord"))

            speaker = agent.speakers.get("Alice")
            assert speaker is not None
            original_score = speaker.reliability_score

            # Simulate feedback from fusion
            fb = FusionFeedback(
                update_id="tn:44504#0",
                source_channel="#c2_coord",
                source_speaker="Alice",
                feedback_type="corroborated",
                corroborating_channels=["#fires"],
                confidence_delta=0.1,
            )
            agent.receive_fusion_feedback(fb)

            assert speaker.reliability_score > original_score
            assert speaker.cross_channel_corroborations == 1

        asyncio.run(run())

    def test_feedback_for_unknown_speaker_is_silent(self):
        """Feedback for a speaker not in the registry should not raise."""

        async def run():
            backend = FakeBackend()
            agent = ChannelAgent("#c2_coord", backend)

            fb = FusionFeedback(
                update_id="tn:44504#0",
                source_channel="#c2_coord",
                source_speaker="NonexistentUser",
                feedback_type="corroborated",
            )
            # Should not raise
            agent.receive_fusion_feedback(fb)

        asyncio.run(run())

    def test_feedback_skipped_when_speaker_models_disabled(self):
        """When use_speaker_models=False, feedback is silently ignored."""

        async def run():
            backend = FakeBackend()
            agent = ChannelAgent("#c2_coord", backend, use_speaker_models=False)

            fb = FusionFeedback(
                update_id="tn:44504#0",
                source_channel="#c2_coord",
                source_speaker="Alice",
                feedback_type="corroborated",
            )
            # Should not raise
            agent.receive_fusion_feedback(fb)

        asyncio.run(run())

    def test_supervisor_routes_feedback_to_correct_agent(self):
        """Supervisor.route_feedback should forward to the right channel agent."""

        async def run():
            from chat_to_cop.config import AgentConfig

            def backend_factory():
                return FakeBackend()

            cfg = AgentConfig(use_speaker_models=True)
            supervisor = Supervisor(backend_factory=backend_factory, agent_config=cfg)
            supervisor.start_agent("#c2_coord")
            supervisor.start_agent("#fires")

            # Process messages so speaker models exist
            await supervisor.route_message(_msg("RR15 F+40", sender="Alice", channel="#c2_coord"))
            await supervisor.route_message(_msg("splash 44504", sender="Bob", channel="#fires"))

            # Create feedback targeting both channels
            feedback = [
                FusionFeedback(
                    update_id="tn:44504#0",
                    source_channel="#c2_coord",
                    source_speaker="Alice",
                    feedback_type="corroborated",
                    corroborating_channels=["#fires"],
                ),
                FusionFeedback(
                    update_id="tn:44504#0",
                    source_channel="#fires",
                    source_speaker="Bob",
                    feedback_type="corroborated",
                    corroborating_channels=["#c2_coord"],
                ),
            ]
            supervisor.route_feedback(feedback)

            # Verify speaker models got updated
            c2_agent = supervisor.agents["#c2_coord"]
            fires_agent = supervisor.agents["#fires"]

            alice = c2_agent.speakers.get("Alice")
            bob = fires_agent.speakers.get("Bob")
            assert alice is not None
            assert bob is not None
            assert alice.cross_channel_corroborations == 1
            assert bob.cross_channel_corroborations == 1
            assert alice.reliability_score > 0.5
            assert bob.reliability_score > 0.5

        asyncio.run(run())

    def test_supervisor_feedback_for_missing_channel_is_silent(self):
        """Feedback for a channel without an agent should not raise."""

        def backend_factory():
            return FakeBackend()

        supervisor = Supervisor(backend_factory=backend_factory)

        feedback = [
            FusionFeedback(
                update_id="test",
                source_channel="#nonexistent",
                source_speaker="Alice",
                feedback_type="corroborated",
            )
        ]
        # Should not raise
        supervisor.route_feedback(feedback)


# =============================================================================
# Full loop integration: fusion -> feedback -> speaker model
# =============================================================================


class TestFullFeedbackLoop:
    def test_end_to_end_fusion_feedback_loop(self):
        """Full pipeline: fusion detects corroboration, feedback updates speaker models."""

        async def run():
            from chat_to_cop.config import AgentConfig

            def backend_factory():
                return FakeBackend()

            cfg = AgentConfig(use_speaker_models=True)
            supervisor = Supervisor(backend_factory=backend_factory, agent_config=cfg)
            fusion = FusionAgent()

            # Ensure agents exist with speakers
            await supervisor.route_message(_msg("RR15 F+40", sender="Alice", channel="#c2_coord"))
            await supervisor.route_message(_msg("splash 44504", sender="Bob", channel="#fires"))

            # Create corroborating updates (simulating what the agents would produce)
            updates = [
                _update(track="44504", channel="#c2_coord", speaker="Alice", status="DESTROYED"),
                _update(track="44504", channel="#fires", speaker="Bob", status="DESTROYED"),
            ]

            # Process through fusion
            fusion.process_updates(updates)
            feedback = fusion.generate_feedback(updates)
            assert len(feedback) > 0

            # Route feedback back to agents
            supervisor.route_feedback(feedback)

            # Verify speaker models were updated
            alice = supervisor.agents["#c2_coord"].speakers.get("Alice")
            bob = supervisor.agents["#fires"].speakers.get("Bob")
            assert alice is not None and bob is not None
            assert alice.cross_channel_corroborations >= 1
            assert bob.cross_channel_corroborations >= 1
            assert alice.reliability_score > 0.5
            assert bob.reliability_score > 0.5

        asyncio.run(run())

    def test_contradiction_feedback_penalizes_speaker(self):
        """Contradiction feedback should decrease the contradicted speaker's reliability."""

        async def run():
            from chat_to_cop.config import AgentConfig

            def backend_factory():
                return FakeBackend()

            cfg = AgentConfig(use_speaker_models=True)
            supervisor = Supervisor(backend_factory=backend_factory, agent_config=cfg)
            fusion = FusionAgent()

            await supervisor.route_message(_msg("44504 operational", sender="Alice", channel="#c2_coord"))
            await supervisor.route_message(_msg("44504 destroyed", sender="Bob", channel="#fires"))

            updates = [
                _update(track="44504", channel="#c2_coord", speaker="Alice", confidence=0.9, status="OPERATIONAL"),
                _update(track="44504", channel="#fires", speaker="Bob", confidence=0.5, status="DESTROYED"),
            ]

            fusion.process_updates(updates)
            feedback = fusion.generate_feedback(updates)
            supervisor.route_feedback(feedback)

            alice = supervisor.agents["#c2_coord"].speakers.get("Alice")
            bob = supervisor.agents["#fires"].speakers.get("Bob")
            assert alice is not None and bob is not None
            # Alice wins (higher confidence), so she gets corroborated
            assert alice.reliability_score > 0.5
            # Bob gets contradicted
            assert bob.reliability_score < 0.5

        asyncio.run(run())


# =============================================================================
# Speaker model serialization roundtrip with new fields
# =============================================================================


class TestSpeakerModelNewFieldsSerialization:
    def test_new_fields_in_json_roundtrip(self):
        registry = SpeakerRegistry()
        speaker = registry.get_or_create("Alice", _now())
        speaker.message_count = 5
        speaker.cross_channel_corroborations = 3
        speaker.cross_channel_contradictions = 1
        speaker.reliability_score = 0.72

        json_data = registry.to_json_list()
        restored = SpeakerRegistry.from_json_list(json_data)
        loaded = restored.get("Alice")
        assert loaded is not None
        assert loaded.cross_channel_corroborations == 3
        assert loaded.cross_channel_contradictions == 1
        assert abs(loaded.reliability_score - 0.72) < 0.001

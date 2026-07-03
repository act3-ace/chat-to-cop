"""Tests for per-UpdateType cascade thresholds (issue #67).

Focused on integration paths NOT covered by test_degrading_backend.py
(which has unit tests for _threshold_for, load_cascade_thresholds, and
the DegradingBackend cascade loop) or test_config.py (which tests the
config field plumbing). This file adds:

1. ChannelAgent + DegradingBackend integration: a high-risk extraction
   with low confidence actually escalates through the full agent path.
2. Config enable/disable flag: cascade_thresholds_enabled=False
   suppresses per-type thresholds in the production builder.
3. End-to-end config plumbing for the enable flag through
   _make_degrading_backend.
"""

import asyncio
from datetime import datetime, timezone

import pytest
from pydantic import BaseModel

from chat_to_cop.agent.channel_agent import ChannelAgent
from chat_to_cop.backend.degrading import DegradingBackend
from chat_to_cop.models.cop_update import CoPUpdate, EntityUpdate, UpdateType
from chat_to_cop.models.messages import IRCMessage

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _msg(content: str, sender: str = "JPRC_COORD", channel: str = "#jprc") -> IRCMessage:
    return IRCMessage(
        timestamp=datetime(2025, 9, 23, 14, 10, 0, tzinfo=timezone.utc),
        channel=channel,
        sender=sender,
        content=content,
    )


class _TypedBackend:
    """Returns CoPUpdate with configurable update_type and confidence."""

    def __init__(self, update_type: UpdateType, confidence: float, name: str = "typed"):
        self.model = name
        self.call_count = 0
        self._update_type = update_type
        self._confidence = confidence

    async def extract(self, messages: list[dict], schema: type[BaseModel]) -> CoPUpdate:
        self.call_count += 1
        return CoPUpdate(
            update_type=self._update_type,
            confidence=self._confidence,
            extraction_method="llm",
            entities=[EntityUpdate(callsign="ZEUS14", operational_status="PILOT_EJECTED")],
            source_channel="",
            source_speaker="",
            source_message="",
            timestamp=datetime(2025, 9, 23, 14, 0, 0, tzinfo=timezone.utc),
        )


# ---------------------------------------------------------------------------
# ChannelAgent integration with per-type cascade
# ---------------------------------------------------------------------------


class TestChannelAgentCascadeIntegration:
    """Wire DegradingBackend with per-type thresholds through ChannelAgent.

    This covers the path: IRCMessage -> ChannelAgent.process_message ->
    DegradingBackend.extract (with per-type cascade) -> CoPUpdate.
    """

    PER_TYPE = {"csar": 0.85, "fuel": 0.55, "default": 0.70}

    def test_high_risk_low_confidence_escalates_through_agent(self):
        """CSAR at 0.60 < 0.85 threshold must escalate to the bigger model."""
        small = _TypedBackend(UpdateType.CSAR, 0.60, name="small-7b")
        big = _TypedBackend(UpdateType.CSAR, 0.95, name="big-70b")
        degrading = DegradingBackend(
            backends=[small, big],
            timeouts=[5.0, 5.0],
            cascade_thresholds=self.PER_TYPE,
        )
        agent = ChannelAgent("#jprc", degrading)

        updates = asyncio.run(agent.process_message(_msg("ZEUS14 pilot ejected, initiating CSAR")))

        assert len(updates) == 1
        assert updates[0].update_type == UpdateType.CSAR
        assert updates[0].confidence == 0.95
        assert small.call_count == 1
        assert big.call_count == 1

    def test_low_risk_same_confidence_does_not_escalate(self):
        """FUEL at 0.60 >= 0.55 threshold must NOT escalate."""
        small = _TypedBackend(UpdateType.FUEL, 0.60, name="small-7b")
        big = _TypedBackend(UpdateType.FUEL, 0.99, name="big-70b")
        degrading = DegradingBackend(
            backends=[small, big],
            timeouts=[5.0, 5.0],
            cascade_thresholds=self.PER_TYPE,
        )
        agent = ChannelAgent("#c2_coord", degrading)

        updates = asyncio.run(agent.process_message(_msg("RR15 F+40", sender="Hydro_Tank", channel="#c2_coord")))

        assert len(updates) == 1
        assert updates[0].confidence == 0.60
        assert small.call_count == 1
        assert big.call_count == 0

    def test_high_risk_high_confidence_does_not_escalate(self):
        """CSAR at 0.90 >= 0.85 threshold must NOT escalate."""
        small = _TypedBackend(UpdateType.CSAR, 0.90, name="small-7b")
        big = _TypedBackend(UpdateType.CSAR, 0.99, name="big-70b")
        degrading = DegradingBackend(
            backends=[small, big],
            timeouts=[5.0, 5.0],
            cascade_thresholds=self.PER_TYPE,
        )
        agent = ChannelAgent("#jprc", degrading)

        updates = asyncio.run(agent.process_message(_msg("CSAR in progress")))

        assert len(updates) == 1
        assert updates[0].confidence == 0.90
        assert small.call_count == 1
        assert big.call_count == 0

    def test_agent_fills_source_fields_after_cascade(self):
        """After cascade escalation, ChannelAgent must still stamp source fields."""
        small = _TypedBackend(UpdateType.FIRE_MISSION, 0.50, name="small")
        big = _TypedBackend(UpdateType.FIRE_MISSION, 0.95, name="big")
        degrading = DegradingBackend(
            backends=[small, big],
            timeouts=[5.0, 5.0],
            cascade_thresholds={"fire_mission": 0.80, "default": 0.70},
        )
        agent = ChannelAgent("#fires", degrading)

        msg = _msg("fire mission TGT AQ1234, 2x JDAM", sender="FIRES_COORD", channel="#fires")
        updates = asyncio.run(agent.process_message(msg))

        assert len(updates) == 1
        assert updates[0].source_channel == "#fires"
        assert updates[0].source_speaker == "FIRES_COORD"
        assert updates[0].source_message == "fire mission TGT AQ1234, 2x JDAM"


# ---------------------------------------------------------------------------
# Config enable/disable flag
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Scrub CHAT_TO_COP_* env vars so each test starts from defaults."""
    import os

    for key in list(os.environ):
        if key.startswith("CHAT_TO_COP_"):
            monkeypatch.delenv(key, raising=False)


class TestCascadeThresholdsEnabledFlag:
    """cascade_thresholds_enabled config field controls whether per-type thresholds load."""

    def test_enabled_by_default(self):
        from chat_to_cop.config import DegradingConfig

        cfg = DegradingConfig()
        assert cfg.cascade_thresholds_enabled is True

    def test_env_override_disables(self, monkeypatch):
        from chat_to_cop.config import DegradingConfig

        monkeypatch.setenv("CHAT_TO_COP_CASCADE_THRESHOLDS_ENABLED", "false")
        cfg = DegradingConfig()
        assert cfg.cascade_thresholds_enabled is False

    def test_disabled_flag_suppresses_per_type_in_builder(self, monkeypatch, tmp_path):
        """When cascade_thresholds_enabled=False, _make_degrading_backend
        must NOT load per-type thresholds, even if the JSON file exists.

        This is the !88-style config plumbing test: env var -> config ->
        consumer behavior.
        """
        from chat_to_cop.config import PipelineConfig
        from chat_to_cop.replay import _make_degrading_backend

        custom = tmp_path / "thresholds.json"
        custom.write_text('{"csar": 0.95, "default": 0.70}', encoding="utf-8")
        monkeypatch.setenv("CHAT_TO_COP_CASCADE_THRESHOLDS_PATH", str(custom))
        monkeypatch.setenv("CHAT_TO_COP_CASCADE_THRESHOLDS_ENABLED", "false")

        cfg = PipelineConfig()
        backend = _make_degrading_backend(cfg)

        assert backend._cascade_thresholds == {}, "cascade_thresholds_enabled=False must suppress per-type map"

    def test_enabled_flag_loads_thresholds(self, monkeypatch, tmp_path):
        """When enabled (default), the per-type map loads and reaches DegradingBackend."""
        from chat_to_cop.config import PipelineConfig
        from chat_to_cop.replay import _make_degrading_backend

        custom = tmp_path / "thresholds.json"
        custom.write_text('{"csar": 0.95, "default": 0.70}', encoding="utf-8")
        monkeypatch.setenv("CHAT_TO_COP_CASCADE_THRESHOLDS_PATH", str(custom))
        # cascade_thresholds_enabled defaults to True, don't set it

        cfg = PipelineConfig()
        backend = _make_degrading_backend(cfg)

        assert backend._cascade_thresholds == {"csar": 0.95, "default": 0.70}

    def test_disabled_flag_means_no_escalation(self):
        """With an empty map (disabled), cascade falls back to scalar threshold.
        Default scalar is 0.0, so no escalation occurs even for low-confidence
        high-risk types.
        """
        small = _TypedBackend(UpdateType.CSAR, 0.30, name="small")
        big = _TypedBackend(UpdateType.CSAR, 0.99, name="big")
        # cascade_thresholds={} simulates disabled flag
        degrading = DegradingBackend(
            backends=[small, big],
            timeouts=[5.0, 5.0],
            cascade_thresholds={},
            # cascade_threshold defaults to 0.0 — no escalation
        )

        result = asyncio.run(degrading.extract([{"role": "user", "content": "test"}], CoPUpdate))
        assert result.confidence == 0.30
        assert small.call_count == 1
        assert big.call_count == 0

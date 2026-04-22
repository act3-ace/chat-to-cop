"""Tests for the silent-failure alarm (issue #68).

Tests the HealthAlarm rolling-window health monitor and its config plumbing
from env vars through PipelineConfig to the Supervisor constructor.
"""

import time
from datetime import datetime, timezone

import pytest

from chat_to_cop.agent.supervisor import HealthAlarm, PipelineHealthStatus, Supervisor
from chat_to_cop.metrics import metrics
from chat_to_cop.models.cop_update import CoPUpdate, EntityUpdate, UpdateType
from chat_to_cop.models.messages import IRCMessage


def _update(
    extraction_method: str = "llm",
    confidence: float = 0.85,
    update_type: UpdateType = UpdateType.FUEL,
) -> CoPUpdate:
    return CoPUpdate(
        update_type=update_type,
        confidence=confidence,
        extraction_method=extraction_method,
        entities=[EntityUpdate(callsign="RR15", fuel_state="F+40")],
        source_channel="#c2_coord",
        source_speaker="Hydro_Tank",
        source_message="RR15 F+40",
        timestamp=datetime(2025, 9, 23, 14, 0, 0, tzinfo=timezone.utc),
    )


def _msg(content: str = "test", channel: str = "#c2_coord") -> IRCMessage:
    return IRCMessage(
        timestamp=datetime(2025, 9, 23, 14, 0, 0, tzinfo=timezone.utc),
        channel=channel,
        sender="Hydro_Tank",
        content=content,
    )


# ---------- HealthAlarm status classification ----------


class TestHealthAlarmClassification:
    def test_healthy_with_good_extractions(self):
        alarm = HealthAlarm(window_minutes=5.0)
        for _ in range(10):
            alarm.record(_update(extraction_method="llm", confidence=0.85))
        report = alarm.compute()
        assert report["status"] == PipelineHealthStatus.HEALTHY
        assert report["passthrough_rate"] == 0.0
        assert report["messages_in_window"] == 10

    def test_critical_high_passthrough_rate(self):
        alarm = HealthAlarm(window_minutes=5.0, passthrough_threshold=0.7)
        # 8 passthrough out of 10 = 80% > 70% threshold
        for _ in range(2):
            alarm.record(_update(extraction_method="llm", confidence=0.85))
        for _ in range(8):
            alarm.record(_update(extraction_method="passthrough", confidence=0.0))
        report = alarm.compute()
        assert report["status"] == PipelineHealthStatus.CRITICAL
        assert report["passthrough_rate"] == 0.8

    def test_critical_low_confidence(self):
        alarm = HealthAlarm(window_minutes=5.0)
        for _ in range(10):
            alarm.record(_update(extraction_method="llm", confidence=0.2))
        report = alarm.compute()
        assert report["status"] == PipelineHealthStatus.CRITICAL
        assert report["avg_confidence"] == pytest.approx(0.2, abs=0.01)

    def test_degraded_moderate_passthrough(self):
        alarm = HealthAlarm(window_minutes=5.0, passthrough_threshold=0.7)
        # 4 passthrough out of 10 = 40%, between 0.3 and 0.7
        for _ in range(6):
            alarm.record(_update(extraction_method="llm", confidence=0.85))
        for _ in range(4):
            alarm.record(_update(extraction_method="passthrough", confidence=0.0))
        report = alarm.compute()
        assert report["status"] == PipelineHealthStatus.DEGRADED

    def test_degraded_moderate_confidence(self):
        alarm = HealthAlarm(window_minutes=5.0)
        # avg confidence 0.4 -- between 0.3 and 0.5
        for _ in range(10):
            alarm.record(_update(extraction_method="llm", confidence=0.4))
        report = alarm.compute()
        assert report["status"] == PipelineHealthStatus.DEGRADED

    def test_healthy_when_barely_above_thresholds(self):
        alarm = HealthAlarm(window_minutes=5.0, passthrough_threshold=0.7)
        # 2 passthrough out of 10 = 20% < 30%, avg confidence = (8*0.7 + 2*0.0)/10 = 0.56 > 0.5
        for _ in range(8):
            alarm.record(_update(extraction_method="llm", confidence=0.7))
        for _ in range(2):
            alarm.record(_update(extraction_method="passthrough", confidence=0.0))
        report = alarm.compute()
        assert report["status"] == PipelineHealthStatus.HEALTHY

    def test_error_method_counts_as_passthrough(self):
        alarm = HealthAlarm(window_minutes=5.0, passthrough_threshold=0.7)
        for _ in range(8):
            alarm.record(_update(extraction_method="error", confidence=0.0))
        for _ in range(2):
            alarm.record(_update(extraction_method="llm", confidence=0.85))
        report = alarm.compute()
        assert report["status"] == PipelineHealthStatus.CRITICAL
        assert report["error_count"] == 8

    def test_shed_method_counts_as_passthrough(self):
        alarm = HealthAlarm(window_minutes=5.0, passthrough_threshold=0.7)
        for _ in range(8):
            alarm.record(_update(extraction_method="shed", confidence=0.0))
        for _ in range(2):
            alarm.record(_update(extraction_method="llm", confidence=0.85))
        report = alarm.compute()
        assert report["status"] == PipelineHealthStatus.CRITICAL

    def test_empty_window_healthy_before_first_message(self):
        alarm = HealthAlarm(window_minutes=5.0)
        report = alarm.compute()
        # No messages ever received -- not idle, just not started
        assert report["status"] == PipelineHealthStatus.HEALTHY
        assert report["messages_in_window"] == 0


# ---------- Rolling window pruning ----------


class TestHealthAlarmWindow:
    def test_old_records_pruned(self):
        alarm = HealthAlarm(window_minutes=0.01)  # ~0.6 seconds
        alarm.record(_update())
        # Wait for the record to expire
        time.sleep(0.7)
        report = alarm.compute()
        assert report["messages_in_window"] == 0

    def test_recent_records_kept(self):
        alarm = HealthAlarm(window_minutes=5.0)
        for _ in range(5):
            alarm.record(_update())
        report = alarm.compute()
        assert report["messages_in_window"] == 5


# ---------- Idle detection ----------


class TestHealthAlarmIdle:
    def test_critical_when_idle_too_long(self):
        alarm = HealthAlarm(window_minutes=0.01, idle_timeout_seconds=0.3)
        alarm.record(_update())
        time.sleep(0.8)  # Records expire AND idle timeout passes
        report = alarm.compute()
        assert report["status"] == PipelineHealthStatus.CRITICAL

    def test_not_idle_if_never_started(self):
        alarm = HealthAlarm(window_minutes=5.0, idle_timeout_seconds=0.1)
        time.sleep(0.2)
        report = alarm.compute()
        # Never received a message -- not idle, just hasn't started
        assert report["status"] == PipelineHealthStatus.HEALTHY


# ---------- Log emission ----------


class TestHealthAlarmLogging:
    def test_emit_log_healthy(self, capfd):
        alarm = HealthAlarm(window_minutes=5.0)
        for _ in range(5):
            alarm.record(_update(extraction_method="llm", confidence=0.85))
        # Should not raise
        alarm.emit_log(active_agents=3)

    def test_emit_log_critical(self, capfd):
        alarm = HealthAlarm(window_minutes=5.0, passthrough_threshold=0.7)
        for _ in range(10):
            alarm.record(_update(extraction_method="passthrough", confidence=0.0))
        alarm.emit_log(active_agents=1)


# ---------- Custom passthrough threshold ----------


class TestHealthAlarmCustomThreshold:
    def test_custom_threshold_changes_classification(self):
        # With threshold 0.5, 60% passthrough is CRITICAL
        alarm_strict = HealthAlarm(window_minutes=5.0, passthrough_threshold=0.5)
        for _ in range(4):
            alarm_strict.record(_update(extraction_method="llm", confidence=0.85))
        for _ in range(6):
            alarm_strict.record(_update(extraction_method="passthrough", confidence=0.0))
        assert alarm_strict.compute()["status"] == PipelineHealthStatus.CRITICAL

        # With threshold 0.7, 60% passthrough is DEGRADED (between 0.3 and 0.7)
        alarm_lenient = HealthAlarm(window_minutes=5.0, passthrough_threshold=0.7)
        for _ in range(4):
            alarm_lenient.record(_update(extraction_method="llm", confidence=0.85))
        for _ in range(6):
            alarm_lenient.record(_update(extraction_method="passthrough", confidence=0.0))
        assert alarm_lenient.compute()["status"] == PipelineHealthStatus.DEGRADED


# ---------- Integration with Supervisor ----------


class FakeBackend:
    """Returns a configurable CoPUpdate."""

    def __init__(self, update_type=UpdateType.FUEL, confidence=0.85, extraction_method="llm"):
        self.update_type = update_type
        self.confidence = confidence
        self.extraction_method = extraction_method

    async def extract(self, messages, schema):
        return CoPUpdate(
            update_type=self.update_type,
            confidence=self.confidence,
            extraction_method=self.extraction_method,
            entities=[EntityUpdate(callsign="RR15", fuel_state="F+40")],
            source_channel="",
            source_speaker="",
            source_message="",
            timestamp=datetime(2025, 9, 23, 14, 0, 0, tzinfo=timezone.utc),
        )


class TestSupervisorHealthAlarmIntegration:
    def setup_method(self):
        metrics.enable()
        metrics.reset()

    def test_route_message_feeds_alarm(self):
        """Updates from route_message are recorded in the health alarm."""
        import asyncio

        sup = Supervisor(backend_factory=lambda: FakeBackend())
        sup.start_agent("#c2_coord")

        asyncio.run(sup.route_message(_msg("RR15 F+40")))

        report = sup._health_alarm.compute()
        assert report["messages_in_window"] >= 1

    def test_error_route_feeds_alarm(self):
        """Routing errors are also recorded in the alarm."""
        import asyncio

        sup = Supervisor(backend_factory=lambda: FakeBackend())
        sup.start_agent("#c2_coord")

        async def _explode(msg):
            raise RuntimeError("boom")

        sup._agents["#c2_coord"].process_message = _explode

        asyncio.run(sup.route_message(_msg("test")))

        report = sup._health_alarm.compute()
        assert report["messages_in_window"] >= 1
        assert report["error_count"] >= 1

    def test_shed_route_feeds_alarm(self):
        """Shed channel passthroughs are recorded in the alarm."""
        import asyncio

        sup = Supervisor(backend_factory=lambda: FakeBackend())
        sup.start_agent("#vegas_internal")
        sup._shed_channels.add("#vegas_internal")
        sup._health["#vegas_internal"].is_active = False

        asyncio.run(sup.route_message(_msg("noise", channel="#vegas_internal")))

        report = sup._health_alarm.compute()
        assert report["messages_in_window"] >= 1
        assert report["passthrough_rate"] > 0

    def test_system_health_includes_alarm_fields(self):
        """system_health() now includes pipeline_health alarm metrics."""
        sup = Supervisor(backend_factory=lambda: FakeBackend())
        sup.start_agent("#c2_coord")
        health = sup.system_health()
        assert "pipeline_health" in health
        assert "passthrough_rate" in health
        assert "avg_confidence" in health
        assert "messages_in_window" in health

    def test_alarm_config_plumbed_to_supervisor(self):
        """Constructor kwargs for alarm config reach the HealthAlarm instance."""
        sup = Supervisor(
            backend_factory=lambda: FakeBackend(),
            health_alarm_interval=30.0,
            health_alarm_window_minutes=2.0,
            passthrough_alarm_threshold=0.5,
        )
        assert sup._health_alarm_interval == 30.0
        assert sup._health_alarm._window_seconds == pytest.approx(120.0)
        assert sup._health_alarm._passthrough_threshold == 0.5


# ---------- Config plumbing (env var -> PipelineConfig -> Supervisor) ----------


class TestHealthAlarmConfigPlumbing:
    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        import os

        for key in list(os.environ):
            if key.startswith("CHAT_TO_COP_"):
                monkeypatch.delenv(key, raising=False)

    def test_defaults(self):
        from chat_to_cop.config import SupervisorConfig

        cfg = SupervisorConfig()
        assert cfg.health_alarm_interval == 60.0
        assert cfg.health_alarm_window_minutes == 5.0
        assert cfg.passthrough_alarm_threshold == 0.7

    def test_env_overrides_reach_config(self, monkeypatch):
        from chat_to_cop.config import SupervisorConfig

        monkeypatch.setenv("CHAT_TO_COP_HEALTH_ALARM_INTERVAL", "15")
        monkeypatch.setenv("CHAT_TO_COP_HEALTH_ALARM_WINDOW_MINUTES", "2.5")
        monkeypatch.setenv("CHAT_TO_COP_PASSTHROUGH_ALARM_THRESHOLD", "0.5")
        cfg = SupervisorConfig()
        assert cfg.health_alarm_interval == 15.0
        assert cfg.health_alarm_window_minutes == 2.5
        assert cfg.passthrough_alarm_threshold == 0.5

    def test_env_overrides_reach_supervisor_via_pipeline_config(self, monkeypatch):
        """Full plumbing path: env var -> PipelineConfig -> Supervisor -> HealthAlarm.

        This is the test that catches config-bypass bugs (lesson from issue #52).
        """
        from chat_to_cop.config import PipelineConfig

        monkeypatch.setenv("CHAT_TO_COP_HEALTH_ALARM_INTERVAL", "20")
        monkeypatch.setenv("CHAT_TO_COP_HEALTH_ALARM_WINDOW_MINUTES", "3.0")
        monkeypatch.setenv("CHAT_TO_COP_PASSTHROUGH_ALARM_THRESHOLD", "0.6")
        config = PipelineConfig()

        sup = Supervisor(
            backend_factory=lambda: FakeBackend(),
            health_alarm_interval=config.supervisor.health_alarm_interval,
            health_alarm_window_minutes=config.supervisor.health_alarm_window_minutes,
            passthrough_alarm_threshold=config.supervisor.passthrough_alarm_threshold,
        )
        assert sup._health_alarm_interval == 20.0
        assert sup._health_alarm._window_seconds == pytest.approx(180.0)
        assert sup._health_alarm._passthrough_threshold == 0.6

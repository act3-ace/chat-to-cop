"""Tests for the supervisor: agent lifecycle, health monitoring, and load shedding."""

import asyncio
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from chat_to_cop.agent.channel_agent import ChannelAgent
from chat_to_cop.agent.supervisor import Supervisor, create_health_app
from chat_to_cop.metrics import metrics
from chat_to_cop.models.cop_update import CoPUpdate, EntityUpdate, UpdateType
from chat_to_cop.models.messages import ChannelPriority, IRCMessage


def _msg(
    content: str = "test message",
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
    """Mock backend that returns a configurable CoPUpdate."""

    def __init__(self, update_type: UpdateType = UpdateType.FUEL, confidence: float = 0.85):
        self.update_type = update_type
        self.confidence = confidence
        self.call_count = 0

    async def extract(self, messages: list[dict], schema: type[BaseModel]) -> CoPUpdate:
        self.call_count += 1
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


class FakeErrorBackend:
    """Mock backend that always raises."""

    async def extract(self, messages: list[dict], schema: type[BaseModel]) -> CoPUpdate:
        raise RuntimeError("LLM is down")


class FakeSlowBackend:
    """Mock backend that simulates high latency via metrics."""

    def __init__(self):
        self.call_count = 0

    async def extract(self, messages: list[dict], schema: type[BaseModel]) -> CoPUpdate:
        self.call_count += 1
        return CoPUpdate(
            update_type=UpdateType.FUEL,
            confidence=0.5,
            extraction_method="llm",
            entities=[],
            source_channel="",
            source_speaker="",
            source_message="",
            timestamp=datetime(2025, 9, 23, 14, 0, 0, tzinfo=timezone.utc),
        )


def _make_supervisor(**kwargs) -> Supervisor:
    """Create a supervisor with a FakeBackend factory."""
    return Supervisor(backend_factory=lambda: FakeBackend(), **kwargs)


def _make_error_supervisor(**kwargs) -> Supervisor:
    """Create a supervisor whose agents use FakeErrorBackend."""
    return Supervisor(backend_factory=lambda: FakeErrorBackend(), **kwargs)


# ---------- Agent lifecycle ----------


class TestAgentLifecycle:
    def setup_method(self):
        metrics.enable()
        metrics.reset()

    def test_start_agent(self):
        sup = _make_supervisor()
        agent = sup.start_agent("#c2_coord")
        assert isinstance(agent, ChannelAgent)
        assert "#c2_coord" in sup.agents
        assert "#c2_coord" in sup.active_channels

    def test_start_agent_uses_default_priority(self):
        sup = _make_supervisor()
        sup.start_agent("#c2_coord")
        assert sup._health["#c2_coord"].priority == ChannelPriority.HIGH

        sup.start_agent("#vegas_internal")
        assert sup._health["#vegas_internal"].priority == ChannelPriority.LOW

    def test_start_agent_custom_priority(self):
        sup = _make_supervisor()
        sup.start_agent("#custom", priority=ChannelPriority.HIGH)
        assert sup._health["#custom"].priority == ChannelPriority.HIGH

    def test_start_agent_replaces_existing(self):
        sup = _make_supervisor()
        agent1 = sup.start_agent("#c2_coord")
        agent2 = sup.start_agent("#c2_coord")
        assert agent1 is not agent2
        assert len(sup.agents) == 1

    def test_stop_agent(self):
        sup = _make_supervisor()
        sup.start_agent("#c2_coord")
        sup.stop_agent("#c2_coord")
        assert "#c2_coord" not in sup.agents
        assert sup.active_channels == []

    def test_stop_nonexistent_agent(self):
        sup = _make_supervisor()
        sup.stop_agent("#nonexistent")  # Should not raise

    def test_restart_agent_preserves_priority(self):
        sup = _make_supervisor()
        sup.start_agent("#c2_coord", priority=ChannelPriority.HIGH)
        agent2 = sup.restart_agent("#c2_coord")
        assert isinstance(agent2, ChannelAgent)
        assert sup._health["#c2_coord"].priority == ChannelPriority.HIGH

    def test_restart_resets_agent_state(self):
        sup = _make_supervisor()
        sup.start_agent("#c2_coord")

        async def run():
            await sup.route_message(_msg("msg 1"))

        asyncio.run(run())
        assert sup.agents["#c2_coord"].message_count == 1

        sup.restart_agent("#c2_coord")
        assert sup.agents["#c2_coord"].message_count == 0


# ---------- Message routing ----------


class TestMessageRouting:
    def setup_method(self):
        metrics.enable()
        metrics.reset()

    def test_route_message_basic(self):
        sup = _make_supervisor()
        sup.start_agent("#c2_coord")

        async def run():
            updates = await sup.route_message(_msg("RR15 F+40"))
            assert len(updates) == 1
            assert updates[0].update_type == UpdateType.FUEL
            assert updates[0].source_channel == "#c2_coord"

        asyncio.run(run())

    def test_dynamic_channel_spawn(self):
        """New channel mid-exercise spawns an agent automatically."""
        sup = _make_supervisor()

        async def run():
            updates = await sup.route_message(_msg("intel update", channel="#new_channel"))
            assert "#new_channel" in sup.agents
            assert len(updates) == 1

        asyncio.run(run())

    def test_shed_channel_returns_passthrough(self):
        """Messages to shed channels get a passthrough response."""
        sup = _make_supervisor()
        sup.start_agent("#vegas_internal")
        sup._shed_channels.add("#vegas_internal")
        sup._health["#vegas_internal"].is_active = False

        async def run():
            updates = await sup.route_message(_msg("noise", channel="#vegas_internal"))
            assert len(updates) == 1
            assert updates[0].extraction_method == "shed"
            assert updates[0].confidence == 0.0

        asyncio.run(run())

    def test_route_error_returns_passthrough(self):
        """Unhandled agent errors produce a passthrough, not a crash."""
        sup = _make_error_supervisor()
        # Start agent manually so error backend is set — but the error is caught
        # inside ChannelAgent.process_message, so let's test the supervisor-level catch
        # by monkeypatching
        sup.start_agent("#c2_coord")

        async def _explode(msg):
            raise RuntimeError("unexpected boom")

        sup._agents["#c2_coord"].process_message = _explode

        async def run():
            updates = await sup.route_message(_msg("test"))
            assert len(updates) == 1
            assert updates[0].extraction_method == "error"
            assert updates[0].reasoning is not None and "unexpected boom" in updates[0].reasoning

        asyncio.run(run())


# ---------- Health monitoring ----------


class TestHealthMonitoring:
    def setup_method(self):
        metrics.enable()
        metrics.reset()

    def test_health_check_syncs_message_count(self):
        sup = _make_supervisor()
        sup.start_agent("#c2_coord")

        async def run():
            await sup.route_message(_msg("msg 1"))
            await sup.route_message(_msg("msg 2"))

        asyncio.run(run())

        sup.health_check()
        assert sup._health["#c2_coord"].messages_processed == 2

    def test_health_check_syncs_errors(self):
        """Error count from metrics is synced to health."""
        sup = _make_supervisor(max_restart_attempts=0)  # Disable auto-restart for this test
        sup.start_agent("#c2_coord")

        # Simulate errors via metrics
        metrics.inc("agent_extraction_errors_total", labels={"channel": "#c2_coord"})
        metrics.inc("agent_extraction_errors_total", labels={"channel": "#c2_coord"})
        metrics.inc("agent_messages_received_total", labels={"channel": "#c2_coord"})
        metrics.inc("agent_messages_received_total", labels={"channel": "#c2_coord"})

        sup.health_check()
        assert sup._health["#c2_coord"].errors == 2

    def test_system_health_reports_healthy(self):
        sup = _make_supervisor()
        sup.start_agent("#c2_coord")
        health = sup.system_health()
        assert health["status"] == "healthy"
        assert health["total_agents"] == 1
        assert health["active_agents"] == 1
        assert health["shed_channels"] == []

    def test_system_health_reports_degraded(self):
        sup = _make_supervisor()
        sup.start_agent("#c2_coord")
        sup.start_agent("#vegas_internal")
        sup._shed_channels.add("#vegas_internal")
        health = sup.system_health()
        assert health["status"] == "degraded"
        assert health["active_agents"] == 1
        assert health["shed_channels"] == ["#vegas_internal"]


# ---------- Load shedding ----------


class TestLoadShedding:
    def setup_method(self):
        metrics.enable()
        metrics.reset()

    def _simulate_overloaded(self, sup: Supervisor, channels: list[str]) -> None:
        """Simulate high latency for given channels by injecting metric values."""
        for ch in channels:
            metrics.observe("agent_extraction_latency_seconds", 10.0, labels={"channel": ch})
            metrics.inc("agent_messages_received_total", labels={"channel": ch})

    def test_shed_low_first(self):
        """When overloaded, LOW channels are shed first."""
        sup = _make_supervisor(latency_threshold=5.0)
        sup.start_agent("#c2_coord", priority=ChannelPriority.HIGH)
        sup.start_agent("#jprc", priority=ChannelPriority.MEDIUM)
        sup.start_agent("#vegas_internal", priority=ChannelPriority.LOW)

        # Make all agents look slow
        self._simulate_overloaded(sup, ["#c2_coord", "#jprc", "#vegas_internal"])

        sup.health_check()

        assert "#vegas_internal" in sup.shed_channels
        assert sup._health["#vegas_internal"].is_active is False
        # HIGH must never be shed
        assert "#c2_coord" not in sup.shed_channels

    def test_shed_medium_if_still_overloaded(self):
        """If shedding LOW isn't enough, shed MEDIUM too."""
        sup = _make_supervisor(latency_threshold=5.0)
        sup.start_agent("#c2_coord", priority=ChannelPriority.HIGH)
        sup.start_agent("#fires", priority=ChannelPriority.HIGH)
        sup.start_agent("#jprc", priority=ChannelPriority.MEDIUM)
        sup.start_agent("#vegas_internal", priority=ChannelPriority.LOW)

        # All channels overloaded
        self._simulate_overloaded(sup, ["#c2_coord", "#fires", "#jprc", "#vegas_internal"])

        sup.health_check()

        assert "#vegas_internal" in sup.shed_channels
        assert "#jprc" in sup.shed_channels
        assert "#c2_coord" not in sup.shed_channels
        assert "#fires" not in sup.shed_channels

    def test_restore_when_recovered(self):
        """Shed channels are restored when system is no longer overloaded."""
        sup = _make_supervisor(latency_threshold=5.0)
        sup.start_agent("#c2_coord", priority=ChannelPriority.HIGH)
        sup.start_agent("#vegas_internal", priority=ChannelPriority.LOW)

        # Force shed
        sup._shed_channels.add("#vegas_internal")
        sup._health["#vegas_internal"].is_active = False

        # No overload metrics -> system is healthy -> should restore
        sup.health_check()

        assert "#vegas_internal" not in sup.shed_channels
        assert sup._health["#vegas_internal"].is_active is True

    def test_restore_one_tier_per_cycle(self):
        """Restore only one priority tier per health check cycle."""
        sup = _make_supervisor(latency_threshold=5.0)
        sup.start_agent("#c2_coord", priority=ChannelPriority.HIGH)
        sup.start_agent("#jprc", priority=ChannelPriority.MEDIUM)
        sup.start_agent("#vegas_internal", priority=ChannelPriority.LOW)

        # Shed both MEDIUM and LOW
        sup._shed_channels.update(["#jprc", "#vegas_internal"])
        sup._health["#jprc"].is_active = False
        sup._health["#vegas_internal"].is_active = False

        # First cycle restores MEDIUM (higher priority restored first)
        sup.health_check()
        assert "#jprc" not in sup.shed_channels
        assert "#vegas_internal" in sup.shed_channels

        # Second cycle restores LOW
        sup.health_check()
        assert "#vegas_internal" not in sup.shed_channels

    def test_no_shed_when_healthy(self):
        """No shedding when system is within thresholds."""
        sup = _make_supervisor(latency_threshold=5.0)
        sup.start_agent("#c2_coord", priority=ChannelPriority.HIGH)
        sup.start_agent("#vegas_internal", priority=ChannelPriority.LOW)

        # Low latency
        metrics.observe("agent_extraction_latency_seconds", 0.5, labels={"channel": "#c2_coord"})
        metrics.observe("agent_extraction_latency_seconds", 0.3, labels={"channel": "#vegas_internal"})
        metrics.inc("agent_messages_received_total", labels={"channel": "#c2_coord"})
        metrics.inc("agent_messages_received_total", labels={"channel": "#vegas_internal"})

        sup.health_check()

        assert sup.shed_channels == set()


# ---------- Auto-restart on high error rate ----------


class TestAutoRestart:
    def setup_method(self):
        metrics.enable()
        metrics.reset()

    def test_restart_on_high_error_rate(self):
        """Agent with error rate > threshold gets restarted."""
        sup = _make_supervisor(error_rate_threshold=0.1, max_restart_attempts=3)
        agent1 = sup.start_agent("#c2_coord")

        # Simulate 50% error rate
        for _ in range(10):
            metrics.inc("agent_messages_received_total", labels={"channel": "#c2_coord"})
        for _ in range(5):
            metrics.inc("agent_extraction_errors_total", labels={"channel": "#c2_coord"})

        sup.health_check()

        # Agent should have been restarted (new instance)
        assert sup.agents["#c2_coord"] is not agent1
        assert sup._restart_counts["#c2_coord"] >= 1

    def test_respects_max_restart_attempts(self):
        """Stop restarting after max attempts."""
        sup = _make_supervisor(error_rate_threshold=0.1, max_restart_attempts=2)
        sup.start_agent("#c2_coord")

        # Exhaust restart attempts
        sup._restart_counts["#c2_coord"] = 2

        # Simulate high error rate
        for _ in range(10):
            metrics.inc("agent_messages_received_total", labels={"channel": "#c2_coord"})
        for _ in range(5):
            metrics.inc("agent_extraction_errors_total", labels={"channel": "#c2_coord"})

        agent_before = sup.agents["#c2_coord"]
        sup.health_check()
        # Should NOT have restarted
        assert sup.agents["#c2_coord"] is agent_before


# ---------- Supervisor run loop ----------


class TestRunLoop:
    def setup_method(self):
        metrics.enable()
        metrics.reset()

    def test_run_and_stop(self):
        """Supervisor run loop can be started and stopped."""
        sup = _make_supervisor(health_check_interval=0.05)

        async def run():
            task = asyncio.create_task(sup.run())
            await asyncio.sleep(0.15)  # ~3 health checks
            sup.stop()
            await asyncio.sleep(0.1)
            assert task.done() or task.cancelled() or not sup._running

        asyncio.run(run())
        assert metrics.get_counter("supervisor_health_checks_total") >= 1


# ---------- FastAPI endpoints ----------


class TestHealthEndpoints:
    def setup_method(self):
        metrics.enable()
        metrics.reset()

    def test_health_endpoint(self):
        sup = _make_supervisor()
        sup.start_agent("#c2_coord")
        app = create_health_app(sup)
        client = TestClient(app)

        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["total_agents"] == 1

    def test_agents_endpoint(self):
        sup = _make_supervisor()
        sup.start_agent("#c2_coord", priority=ChannelPriority.HIGH)
        app = create_health_app(sup)
        client = TestClient(app)

        resp = client.get("/agents")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["channel"] == "#c2_coord"
        assert data[0]["priority"] == "HIGH"
        assert data[0]["is_active"] is True

    def test_agents_endpoint_shows_shed(self):
        sup = _make_supervisor()
        sup.start_agent("#vegas_internal", priority=ChannelPriority.LOW)
        sup._shed_channels.add("#vegas_internal")
        sup._health["#vegas_internal"].is_active = False

        app = create_health_app(sup)
        client = TestClient(app)

        resp = client.get("/agents")
        data = resp.json()
        assert data[0]["is_active"] is False

    def test_health_endpoint_degraded(self):
        sup = _make_supervisor()
        sup.start_agent("#c2_coord")
        sup.start_agent("#vegas_internal")
        sup._shed_channels.add("#vegas_internal")

        app = create_health_app(sup)
        client = TestClient(app)

        resp = client.get("/health")
        data = resp.json()
        assert data["status"] == "degraded"
        assert "#vegas_internal" in data["shed_channels"]


class TestSupervisorAgentConfigPlumbing:
    """Regression tests for issue #52: AgentConfig fields must reach ChannelAgent.

    Background: Before the fix, Supervisor.start_agent() constructed
    ChannelAgent(channel=..., backend=...) with no other arguments, so
    `use_speaker_models`, `window_size`, and `window_minutes` defaulted in
    ChannelAgent regardless of the AgentConfig (which reads CHAT_TO_COP_*
    env vars). The 7B (April 6) and 14B (April 10) "A/B" experiments were
    invalid because both arms ran with speakers ON.

    These tests assert that the env-var path actually flips behavior in the
    supervisor-spawned agent, not just in an isolated AgentConfig object.
    """

    @pytest.fixture(autouse=True)
    def _scrub_chat_to_cop_env(self, monkeypatch):
        """Clear CHAT_TO_COP_* env vars before each test.

        These tests assert behavior of the env-var plumbing path. If the
        ambient shell already has e.g. CHAT_TO_COP_USE_SPEAKER_MODELS=false
        set (as the Run B Narwhal script does), pydantic-settings reads it
        eagerly and the "default" tests fail. Scrub the namespace so each
        test starts from a clean slate and only sees env vars it explicitly
        sets via monkeypatch.setenv.
        """
        for var in (
            "CHAT_TO_COP_USE_SPEAKER_MODELS",
            "CHAT_TO_COP_WINDOW_SIZE",
            "CHAT_TO_COP_WINDOW_MINUTES",
        ):
            monkeypatch.delenv(var, raising=False)

    def test_default_config_disables_speaker_models(self):
        sup = Supervisor(backend_factory=FakeBackend)
        agent = sup.start_agent("#c2_coord")
        assert agent.use_speaker_models is False

    def test_explicit_agent_config_disables_speaker_models(self):
        from chat_to_cop.config import AgentConfig

        cfg = AgentConfig(use_speaker_models=False)
        sup = Supervisor(backend_factory=FakeBackend, agent_config=cfg)
        agent = sup.start_agent("#c2_coord")
        assert agent.use_speaker_models is False

    def test_env_var_disables_speaker_models_in_production_path(self, monkeypatch):
        """The CHAT_TO_COP_USE_SPEAKER_MODELS env var must reach ChannelAgent
        when Supervisor is constructed without an explicit AgentConfig."""
        monkeypatch.setenv("CHAT_TO_COP_USE_SPEAKER_MODELS", "false")
        sup = Supervisor(backend_factory=FakeBackend)
        agent = sup.start_agent("#c2_coord")
        assert agent.use_speaker_models is False

    def test_env_var_enables_speaker_models_in_production_path(self, monkeypatch):
        monkeypatch.setenv("CHAT_TO_COP_USE_SPEAKER_MODELS", "true")
        sup = Supervisor(backend_factory=FakeBackend)
        agent = sup.start_agent("#c2_coord")
        assert agent.use_speaker_models is True

    def test_window_size_plumbed_through(self, monkeypatch):
        monkeypatch.setenv("CHAT_TO_COP_WINDOW_SIZE", "17")
        sup = Supervisor(backend_factory=FakeBackend)
        agent = sup.start_agent("#c2_coord")
        assert agent.window_size == 17

    def test_window_minutes_plumbed_through(self, monkeypatch):
        monkeypatch.setenv("CHAT_TO_COP_WINDOW_MINUTES", "7.5")
        sup = Supervisor(backend_factory=FakeBackend)
        agent = sup.start_agent("#c2_coord")
        assert agent.window_minutes == 7.5

    def test_replay_threads_agent_config_into_supervisor(self, monkeypatch):
        """Trace the production path: PipelineConfig -> Supervisor -> ChannelAgent.

        This is the test that would have caught the original bug. Before the
        fix, this assertion failed because replay.py constructed Supervisor
        without passing config.agent.
        """
        from chat_to_cop.config import PipelineConfig

        monkeypatch.setenv("CHAT_TO_COP_USE_SPEAKER_MODELS", "false")
        config = PipelineConfig()
        assert config.agent.use_speaker_models is False  # config object reads env

        sup = Supervisor(backend_factory=FakeBackend, agent_config=config.agent)
        agent = sup.start_agent("#c2_coord")
        assert agent.use_speaker_models is False  # and so does the spawned agent

    def test_calibration_model_plumbed_to_channel_agent(self):
        """The calibration model passed to Supervisor must reach ChannelAgent.

        Regression for the second invalidating bug found in the audit:
        replay.py never loaded config.calibration_model, so all production
        runs used raw uncalibrated confidence even when a calibration JSON
        was configured.
        """
        from chat_to_cop.calibration import CalibrationModel

        cal = CalibrationModel()
        cal.fit(confidences=[0.9, 0.9, 0.8], correct=[True, False, True])

        sup = Supervisor(backend_factory=FakeBackend, calibration_model=cal)
        agent = sup.start_agent("#c2_coord")
        assert agent.calibration_model is cal

    def test_calibration_model_default_is_none(self):
        sup = Supervisor(backend_factory=FakeBackend)
        agent = sup.start_agent("#c2_coord")
        assert agent.calibration_model is None

    def test_supervisor_thresholds_honor_constructor_args(self):
        """SupervisorConfig fields must reach Supervisor when threaded by replay.py.

        Regression for the third audit finding: replay.py constructed Supervisor
        without passing latency_threshold, error_rate_threshold,
        health_check_interval, or max_restart_attempts.
        """
        sup = Supervisor(
            backend_factory=FakeBackend,
            latency_threshold=12.5,
            error_rate_threshold=0.42,
            health_check_interval=99.0,
            max_restart_attempts=7,
        )
        assert sup._latency_threshold == 12.5
        assert sup._error_rate_threshold == 0.42
        assert sup._health_check_interval == 99.0
        assert sup._max_restart_attempts == 7

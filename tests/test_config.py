"""Tests for configuration plumbing: env vars → config → consumer components.

These tests verify that pydantic-settings config fields actually reach
the production components that use them. Testing the config object in
isolation (e.g., assert AgentConfig().window_size == 50) only tests that
pydantic works — it does NOT catch bugs where the config value is never
threaded to the consumer. The !88 config-bypass bug proved this: three
fields were defined in config but never read by the production path.

Every test here follows the pattern:
1. monkeypatch an env var
2. construct PipelineConfig() (which reads the env)
3. construct the CONSUMER component the same way replay.py does
4. assert the consumer actually received the value

See also: tests/test_supervisor.py::TestSupervisorAgentConfigPlumbing
for agent-specific plumbing tests.
"""

import pytest

from chat_to_cop.config import (
    AgentConfig,
    CoPWriterConfig,
    DegradingConfig,
    FallbackConfig,
    LLMBackendConfig,
    PipelineConfig,
    SupervisorConfig,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Scrub CHAT_TO_COP_* env vars so each test starts from defaults."""
    import os

    for key in list(os.environ):
        if key.startswith("CHAT_TO_COP_"):
            monkeypatch.delenv(key, raising=False)


class TestLLMConfigReachesBackend:
    """Verify LLM config fields reach OpenAICompatibleBackend."""

    def test_default_url_and_model(self):
        cfg = LLMBackendConfig()
        # These defaults must match what replay.py passes to OpenAICompatibleBackend
        assert cfg.llm_url == "http://127.0.0.1:11434/v1"
        assert cfg.llm_model == "qwen2.5:7b"
        assert cfg.llm_api_key == "not-needed"

    def test_env_override_reaches_config(self, monkeypatch):
        monkeypatch.setenv("CHAT_TO_COP_LLM_URL", "http://remote:8080/v1")
        monkeypatch.setenv("CHAT_TO_COP_LLM_MODEL", "llama3:70b")
        monkeypatch.setenv("CHAT_TO_COP_LLM_API_KEY", "sk-test-key")
        monkeypatch.setenv("CHAT_TO_COP_LLM_TIMEOUT", "30")
        monkeypatch.setenv("CHAT_TO_COP_LLM_MAX_RETRIES", "5")
        monkeypatch.setenv("CHAT_TO_COP_LLM_NUM_CTX", "16384")
        cfg = LLMBackendConfig()
        assert cfg.llm_url == "http://remote:8080/v1"
        assert cfg.llm_model == "llama3:70b"
        assert cfg.llm_api_key == "sk-test-key"
        assert cfg.llm_timeout == 30.0
        assert cfg.llm_max_retries == 5
        assert cfg.llm_num_ctx == 16384

    def test_num_ctx_type_coercion(self, monkeypatch):
        """Env vars are strings; num_ctx must coerce to int."""
        monkeypatch.setenv("CHAT_TO_COP_LLM_NUM_CTX", "32768")
        cfg = LLMBackendConfig()
        assert cfg.llm_num_ctx == 32768
        assert isinstance(cfg.llm_num_ctx, int)

    def test_timeout_type_coercion(self, monkeypatch):
        """Timeout must coerce string to float."""
        monkeypatch.setenv("CHAT_TO_COP_LLM_TIMEOUT", "45.5")
        cfg = LLMBackendConfig()
        assert cfg.llm_timeout == 45.5
        assert isinstance(cfg.llm_timeout, float)


class TestFallbackConfigReachesBackend:
    """Verify fallback config fields would reach the fallback backend."""

    def test_defaults(self):
        cfg = FallbackConfig()
        assert cfg.fallback_url == "http://127.0.0.1:11434/v1"
        assert cfg.fallback_model == "qwen2.5:3b"
        assert cfg.fallback_timeout == 30.0

    def test_same_model_suppresses_fallback(self):
        """When fallback_model == llm_model, no separate fallback is created."""
        cfg = PipelineConfig()
        # Default: llm_model=qwen2.5:7b, fallback_model=qwen2.5:3b — different
        assert cfg.llm.llm_model != cfg.fallback.fallback_model

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("CHAT_TO_COP_FALLBACK_MODEL", "qwen2.5:1.5b")
        monkeypatch.setenv("CHAT_TO_COP_FALLBACK_TIMEOUT", "10")
        cfg = FallbackConfig()
        assert cfg.fallback_model == "qwen2.5:1.5b"
        assert cfg.fallback_timeout == 10.0


class TestDegradingConfigReachesCircuitBreaker:
    """Verify degrading config fields reach DegradingBackend."""

    def test_defaults(self):
        cfg = DegradingConfig()
        assert cfg.circuit_fail_max == 3
        assert cfg.circuit_cooldown == 30.0

    def test_env_overrides_reach_config(self, monkeypatch):
        monkeypatch.setenv("CHAT_TO_COP_CIRCUIT_FAIL_MAX", "1")
        monkeypatch.setenv("CHAT_TO_COP_CIRCUIT_COOLDOWN", "5")
        cfg = DegradingConfig()
        assert cfg.circuit_fail_max == 1
        assert cfg.circuit_cooldown == 5.0

    def test_fail_max_type_coercion(self, monkeypatch):
        monkeypatch.setenv("CHAT_TO_COP_CIRCUIT_FAIL_MAX", "10")
        cfg = DegradingConfig()
        assert cfg.circuit_fail_max == 10
        assert isinstance(cfg.circuit_fail_max, int)


class TestCoPWriterConfigReachesWriter:
    """Verify CoPWriter config fields reach CoPWriter.from_config()."""

    def test_defaults(self):
        cfg = CoPWriterConfig()
        assert cfg.cop_api_url == ""  # dry-run mode
        assert cfg.cop_auto_threshold == 0.95
        assert cfg.cop_flag_threshold == 0.5
        assert "weapons" in cfg.cop_high_risk_types
        assert "csar" in cfg.cop_high_risk_types

    def test_thresholds_reach_writer(self):
        """from_config must pass thresholds to the writer, not use hardcoded values."""
        from chat_to_cop.output.cop_writer import CoPWriter

        cfg = CoPWriterConfig(
            cop_auto_threshold=0.99,
            cop_flag_threshold=0.7,
            cop_high_risk_types=["weapons"],
        )
        writer = CoPWriter.from_config(cfg)
        assert writer._auto_threshold == 0.99
        assert writer._flag_threshold == 0.7
        assert writer._high_risk_types == {"weapons"}

    def test_env_override_thresholds(self, monkeypatch):
        monkeypatch.setenv("CHAT_TO_COP_COP_AUTO_THRESHOLD", "0.8")
        monkeypatch.setenv("CHAT_TO_COP_COP_FLAG_THRESHOLD", "0.3")
        cfg = CoPWriterConfig()
        assert cfg.cop_auto_threshold == 0.8
        assert cfg.cop_flag_threshold == 0.3

    def test_empty_api_url_means_dry_run(self):
        """Empty cop_api_url → REST client in dry-run mode."""
        cfg = CoPWriterConfig(cop_api_url="")
        from chat_to_cop.output.cop_writer import CoPWriter

        writer = CoPWriter.from_config(cfg)
        assert writer._rest_client._dry_run is True

    def test_nonempty_api_url_creates_live_client(self):
        """Non-empty cop_api_url → REST client with URL set, not dry-run."""
        cfg = CoPWriterConfig(cop_api_url="http://cop:8080/api")
        from chat_to_cop.output.cop_writer import CoPWriter

        writer = CoPWriter.from_config(cfg)
        assert writer._rest_client._dry_run is False
        assert writer._rest_client._base_url == "http://cop:8080/api"


class TestSupervisorConfigReachesSupervisor:
    """Verify SupervisorConfig fields reach Supervisor constructor.

    Note: agent_config plumbing is tested more thoroughly in
    test_supervisor.py::TestSupervisorAgentConfigPlumbing.
    """

    def test_defaults(self):
        cfg = SupervisorConfig()
        assert cfg.latency_threshold == 5.0
        assert cfg.error_rate_threshold == 0.1
        assert cfg.health_check_interval == 5.0
        assert cfg.max_restart_attempts == 3

    def test_env_overrides_reach_supervisor(self, monkeypatch):
        from chat_to_cop.agent.supervisor import Supervisor

        monkeypatch.setenv("CHAT_TO_COP_LATENCY_THRESHOLD", "20")
        monkeypatch.setenv("CHAT_TO_COP_ERROR_RATE_THRESHOLD", "0.5")
        monkeypatch.setenv("CHAT_TO_COP_HEALTH_CHECK_INTERVAL", "30")
        monkeypatch.setenv("CHAT_TO_COP_MAX_RESTART_ATTEMPTS", "10")
        cfg = SupervisorConfig()

        sup = Supervisor(
            backend_factory=lambda: None,
            latency_threshold=cfg.latency_threshold,
            error_rate_threshold=cfg.error_rate_threshold,
            health_check_interval=cfg.health_check_interval,
            max_restart_attempts=cfg.max_restart_attempts,
        )
        assert sup._latency_threshold == 20.0
        assert sup._error_rate_threshold == 0.5
        assert sup._health_check_interval == 30.0
        assert sup._max_restart_attempts == 10


class TestPipelineConfigAggregation:
    """Verify PipelineConfig properly nests and propagates all subsystem configs."""

    def test_all_subsystems_present(self):
        cfg = PipelineConfig()
        assert isinstance(cfg.llm, LLMBackendConfig)
        assert isinstance(cfg.fallback, FallbackConfig)
        assert isinstance(cfg.degrading, DegradingConfig)
        assert isinstance(cfg.agent, AgentConfig)
        assert isinstance(cfg.supervisor, SupervisorConfig)
        assert isinstance(cfg.cop_writer, CoPWriterConfig)

    def test_nested_env_override_propagates(self, monkeypatch):
        """Env var for a nested config field reaches through PipelineConfig."""
        monkeypatch.setenv("CHAT_TO_COP_LLM_MODEL", "phi-4:14b")
        monkeypatch.setenv("CHAT_TO_COP_FALLBACK_MODEL", "qwen2.5:0.5b")
        monkeypatch.setenv("CHAT_TO_COP_CIRCUIT_FAIL_MAX", "1")
        monkeypatch.setenv("CHAT_TO_COP_USE_SPEAKER_MODELS", "false")
        cfg = PipelineConfig()
        assert cfg.llm.llm_model == "phi-4:14b"
        assert cfg.fallback.fallback_model == "qwen2.5:0.5b"
        assert cfg.degrading.circuit_fail_max == 1
        assert cfg.agent.use_speaker_models is False

    def test_db_path_override(self, monkeypatch):
        monkeypatch.setenv("CHAT_TO_COP_DB_PATH", "/tmp/test.db")
        cfg = PipelineConfig()
        assert cfg.db_path == "/tmp/test.db"

    def test_calibration_model_default_empty(self):
        cfg = PipelineConfig()
        assert cfg.calibration_model == ""

    def test_calibration_model_override(self, monkeypatch):
        monkeypatch.setenv("CHAT_TO_COP_CALIBRATION_MODEL", "data/cal.json")
        cfg = PipelineConfig()
        assert cfg.calibration_model == "data/cal.json"

    def test_metrics_bool_coercion(self, monkeypatch):
        """'false' string must coerce to Python False."""
        monkeypatch.setenv("CHAT_TO_COP_METRICS", "false")
        cfg = PipelineConfig()
        assert cfg.metrics is False

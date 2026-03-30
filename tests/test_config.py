"""Tests for pydantic-settings configuration."""

from chat_to_cop.config import (
    AgentConfig,
    DegradingConfig,
    FallbackConfig,
    LLMBackendConfig,
    PipelineConfig,
    SupervisorConfig,
)


class TestLLMBackendConfig:
    def test_defaults(self):
        cfg = LLMBackendConfig()
        assert cfg.llm_url == "http://127.0.0.1:11434/v1"
        assert cfg.llm_model == "qwen2.5:7b"
        assert cfg.llm_timeout == 120.0
        assert cfg.llm_max_retries == 2

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("CHAT_TO_COP_LLM_URL", "http://remote:8080/v1")
        monkeypatch.setenv("CHAT_TO_COP_LLM_MODEL", "llama3:70b")
        cfg = LLMBackendConfig()
        assert cfg.llm_url == "http://remote:8080/v1"
        assert cfg.llm_model == "llama3:70b"


class TestFallbackConfig:
    def test_defaults(self):
        cfg = FallbackConfig()
        assert cfg.fallback_model == "qwen2.5:3b"
        assert cfg.fallback_timeout == 30.0


class TestDegradingConfig:
    def test_defaults(self):
        cfg = DegradingConfig()
        assert cfg.circuit_fail_max == 3
        assert cfg.circuit_cooldown == 30.0

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("CHAT_TO_COP_CIRCUIT_FAIL_MAX", "5")
        monkeypatch.setenv("CHAT_TO_COP_CIRCUIT_COOLDOWN", "60")
        cfg = DegradingConfig()
        assert cfg.circuit_fail_max == 5
        assert cfg.circuit_cooldown == 60.0


class TestAgentConfig:
    def test_defaults(self):
        cfg = AgentConfig()
        assert cfg.window_size == 50
        assert cfg.window_minutes == 15.0
        assert cfg.use_speaker_models is True

    def test_disable_speaker_models(self, monkeypatch):
        monkeypatch.setenv("CHAT_TO_COP_USE_SPEAKER_MODELS", "false")
        cfg = AgentConfig()
        assert cfg.use_speaker_models is False


class TestSupervisorConfig:
    def test_defaults(self):
        cfg = SupervisorConfig()
        assert cfg.latency_threshold == 5.0
        assert cfg.error_rate_threshold == 0.1
        assert cfg.max_restart_attempts == 3


class TestPipelineConfig:
    def test_defaults(self):
        cfg = PipelineConfig()
        assert cfg.db_path == "data/world_state.db"
        assert cfg.metrics is True
        assert cfg.llm.llm_url == "http://127.0.0.1:11434/v1"
        assert cfg.fallback.fallback_model == "qwen2.5:3b"
        assert cfg.degrading.circuit_fail_max == 3
        assert cfg.agent.window_size == 50
        assert cfg.supervisor.latency_threshold == 5.0

    def test_env_override_top_level(self, monkeypatch):
        monkeypatch.setenv("CHAT_TO_COP_DB_PATH", "/tmp/test.db")
        monkeypatch.setenv("CHAT_TO_COP_METRICS", "false")
        cfg = PipelineConfig()
        assert cfg.db_path == "/tmp/test.db"
        assert cfg.metrics is False

    def test_nested_env_override(self, monkeypatch):
        monkeypatch.setenv("CHAT_TO_COP_LLM_MODEL", "phi-4:14b")
        cfg = PipelineConfig()
        assert cfg.llm.llm_model == "phi-4:14b"

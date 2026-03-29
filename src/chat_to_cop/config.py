"""Configuration management for the chat-to-cop pipeline.

Uses pydantic-settings for type-safe config with environment variable overrides.
All settings have sensible defaults for local development with Ollama.

Environment variables use the CHAT_TO_COP_ prefix:
    CHAT_TO_COP_LLM_URL=http://127.0.0.1:11434/v1
    CHAT_TO_COP_LLM_MODEL=qwen2.5:7b
    CHAT_TO_COP_FALLBACK_MODEL=qwen2.5:3b
    CHAT_TO_COP_DB_PATH=data/world_state.db
    CHAT_TO_COP_METRICS=true
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMBackendConfig(BaseSettings):
    """Configuration for a single LLM backend endpoint."""

    model_config = SettingsConfigDict(env_prefix="CHAT_TO_COP_")

    llm_url: str = Field(
        default="http://127.0.0.1:11434/v1",
        description="OpenAI-compatible API base URL",
    )
    llm_model: str = Field(
        default="qwen2.5:7b",
        description="Primary model name",
    )
    llm_api_key: str = Field(
        default="not-needed",
        description="API key (not needed for Ollama)",
    )
    llm_timeout: float = Field(
        default=10.0,
        description="Timeout in seconds per LLM call",
    )
    llm_max_retries: int = Field(
        default=2,
        description="Max retries for schema validation failures (instructor)",
    )


class FallbackConfig(BaseSettings):
    """Configuration for the fallback/smaller model."""

    model_config = SettingsConfigDict(env_prefix="CHAT_TO_COP_")

    fallback_url: str = Field(
        default="http://127.0.0.1:11434/v1",
        description="Fallback LLM endpoint (can be same as primary)",
    )
    fallback_model: str = Field(
        default="qwen2.5:3b",
        description="Smaller/faster fallback model",
    )
    fallback_timeout: float = Field(
        default=5.0,
        description="Timeout for fallback model (shorter = faster degradation)",
    )


class DegradingConfig(BaseSettings):
    """Configuration for the degrading backend's circuit breaker behavior."""

    model_config = SettingsConfigDict(env_prefix="CHAT_TO_COP_")

    circuit_fail_max: int = Field(
        default=3,
        description="Consecutive failures before circuit breaker opens",
    )
    circuit_cooldown: float = Field(
        default=30.0,
        description="Seconds before circuit breaker allows retry",
    )


class AgentConfig(BaseSettings):
    """Configuration for channel agents."""

    model_config = SettingsConfigDict(env_prefix="CHAT_TO_COP_")

    window_size: int = Field(
        default=50,
        description="Max messages in conversation window",
    )
    window_minutes: float = Field(
        default=15.0,
        description="Max age of messages in conversation window (minutes)",
    )
    use_speaker_models: bool = Field(
        default=True,
        description="Enable online speaker model learning (A/B toggle)",
    )


class SupervisorConfig(BaseSettings):
    """Configuration for the supervisor."""

    model_config = SettingsConfigDict(env_prefix="CHAT_TO_COP_")

    latency_threshold: float = Field(
        default=5.0,
        description="Seconds — agents slower than this trigger health warnings",
    )
    error_rate_threshold: float = Field(
        default=0.1,
        description="Error rate (0-1) that triggers health warnings",
    )
    health_check_interval: float = Field(
        default=5.0,
        description="Seconds between health check sweeps",
    )
    max_restart_attempts: int = Field(
        default=3,
        description="Max restarts before giving up on an agent",
    )


class PipelineConfig(BaseSettings):
    """Top-level configuration aggregating all subsystem configs.

    Load with: config = PipelineConfig()
    Override with env vars: CHAT_TO_COP_LLM_URL=... CHAT_TO_COP_DB_PATH=...
    """

    model_config = SettingsConfigDict(env_prefix="CHAT_TO_COP_")

    # Store
    db_path: str = Field(
        default="data/world_state.db",
        description="SQLite database path",
    )

    # Metrics
    metrics: bool = Field(
        default=True,
        description="Enable instrumentation (counters, histograms, timers)",
    )

    # Calibration
    calibration_model: str = Field(
        default="",
        description="Path to calibration model JSON. Empty = no calibration applied.",
    )

    # Subsystem configs
    llm: LLMBackendConfig = Field(default_factory=LLMBackendConfig)
    fallback: FallbackConfig = Field(default_factory=FallbackConfig)
    degrading: DegradingConfig = Field(default_factory=DegradingConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    supervisor: SupervisorConfig = Field(default_factory=SupervisorConfig)

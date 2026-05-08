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
        default=120.0,
        description="Timeout in seconds per LLM call (generous to handle model cold starts)",
    )
    llm_max_retries: int = Field(
        default=2,
        description="Max retries for schema validation failures (instructor)",
    )
    llm_num_ctx: int = Field(
        default=8192,
        description="Context window size for Ollama models (num_ctx option)",
    )
    llm_is_ollama: bool | None = Field(
        default=None,
        description=(
            "Explicit Ollama detection override. True = always send num_ctx, "
            "False = never send. None (default) = auto-detect from URL."
        ),
    )
    llm_azure_endpoint: str = Field(
        default="",
        description=(
            "Azure OpenAI endpoint (e.g. https://myresource.openai.azure.com). "
            "Non-empty activates Azure mode; llm_url is ignored."
        ),
    )
    llm_azure_api_version: str = Field(
        default="2024-10-21",
        description="Azure OpenAI API version.",
    )
    # Issue #75 — hard wall-clock cap on a single extract() call, wrapping the
    # full instructor retry chain. Without this a pathological message can burn
    # llm_timeout * (llm_max_retries + 1) seconds (up to 12 minutes observed on
    # Blueback 2026-04-22). 45s keeps the 99% case alive on 14B models while
    # bounding the worst case. DegradingBackend converts the timeout into a
    # fall-through to the next backend slot.
    llm_extract_hard_timeout: float = Field(
        default=45.0,
        description="Hard wall-clock cap (seconds) on a single extract call, independent of instructor retries",
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
        default=30.0,
        description="Timeout for fallback model",
    )
    fallback_is_ollama: bool | None = Field(
        default=None,
        description="Explicit Ollama detection override for the fallback backend.",
    )
    # Issue #75 — see llm_extract_hard_timeout. Tighter default for the
    # fallback slot so a flaky fallback can't by itself consume the primary's
    # budget on top of the primary's own hard timeout.
    fallback_extract_hard_timeout: float = Field(
        default=20.0,
        description="Hard wall-clock cap (seconds) on a single fallback extract call",
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
    # Issue #67 — per-UpdateType cascade thresholds. Points to a JSON file
    # mapping update_type values (and "default") to floats; consumed by
    # _make_degrading_backend in replay.py and passed to DegradingBackend.
    # Default path is relative to the repo root so `python -m chat_to_cop.replay`
    # from the repo CWD picks it up. Absolute paths also work. Missing file
    # degrades to scalar cascade_threshold only (see load_cascade_thresholds).
    cascade_thresholds_path: str = Field(
        default="config/cascade_thresholds.json",
        description=(
            "Path to JSON file with per-UpdateType confidence thresholds. "
            "Missing file falls back to scalar cascade_threshold."
        ),
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
        default=False,
        description="Enable online speaker model learning (default OFF per RQ1 N=100 results)",
    )
    glossary_file: str = Field(
        default="",
        description="Path to supplemental glossary file to append to DEFAULT_GLOSSARY",
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

    # Silent-failure alarm settings (issue #68)
    health_alarm_interval: float = Field(
        default=60.0,
        description="Seconds between health alarm log emissions",
    )
    health_alarm_window_minutes: float = Field(
        default=5.0,
        description="Rolling window (minutes) for health alarm metrics",
    )
    passthrough_alarm_threshold: float = Field(
        default=0.7,
        description="Passthrough rate above this triggers CRITICAL status",
    )


class CoPWriterConfig(BaseSettings):
    """Configuration for the CoP REST API writer."""

    model_config = SettingsConfigDict(env_prefix="CHAT_TO_COP_")

    cop_api_url: str = Field(
        default="",
        description="CoP REST API base URL. Empty string = dry-run mode (log only, no HTTP).",
    )
    cop_auto_threshold: float = Field(
        default=0.85,
        description=(
            "Minimum confidence for AUTO write authority (written immediately). "
            "Lowered from 0.95 to 0.85 for MASH: at 0.95, the 7B model's valid "
            "extractions in the 0.85-0.94 range were silently filtered, making "
            "the dashboard appear dead during low-entity-density traffic. The "
            "design philosophy is 'even 70%% is great' -- 0.85 balances precision "
            "against the operational cost of a silent system. When a "
            "CalibrationModel is loaded, the *calibrated* confidence is used, so "
            "this threshold operates on the calibrated value."
        ),
    )
    cop_flag_threshold: float = Field(
        default=0.5,
        description=(
            "Minimum confidence for FLAGGED write authority. Below this -> HUMAN review. "
            "Raised from 0.4 to 0.5 based on calibration: the 7B model's mid-range "
            "confidence bucket ([0.1-0.8)) is essentially empty, so a 0.4 threshold "
            "was gating on noise. 0.5 is a cleaner split."
        ),
    )
    cop_high_risk_types: list[str] = Field(
        default_factory=lambda: ["weapons", "csar", "fire_mission", "cyber_ew"],
        description="Update types that always require human review regardless of confidence.",
    )
    cop_max_queue_size: int = Field(
        default=1000,
        description="Max size of pause queue and human review queue.",
    )
    cop_write_timeout: float = Field(
        default=10.0,
        description="HTTP timeout in seconds for CoP REST API writes.",
    )
    cop_retry_attempts: int = Field(
        default=3,
        description="Number of retry attempts for transient HTTP failures.",
    )
    # Issue #70 — schema adapter spec. "passthrough" ships the record's own
    # model_dump as payload (pre-#70 default). At MASH, flip to
    # "jsonschema:<schema_path>:<mapping_path>" once the contractor hands
    # over the CoP schema and we write a mapping config. Consumer:
    # CoPWriter.from_config → CoPRESTClient(adapter=build_adapter(spec)).
    schema_adapter: str = Field(
        default="passthrough",
        description=("Schema adapter spec. 'passthrough' (default) or 'jsonschema:<schema_path>:<mapping_path>'."),
    )


class ReferenceDataConfig(BaseSettings):
    """Configuration for optional reference data (entity catalogs, theater geometry)."""

    model_config = SettingsConfigDict(env_prefix="CHAT_TO_COP_")

    entity_catalog_dir: str = Field(
        default="",
        description="Directory containing schema_assets.csv and dash_target_taxonomy.csv. Empty = disabled.",
    )
    geometry_path: str = Field(
        default="",
        description="Path to theater_geometry_polygons.geojson. Empty = disabled.",
    )
    equipment_catalog_path: str = Field(
        default="",
        description="Path to equipment/weapon catalog JSON or CSV. Empty = disabled.",
    )


class PipelineConfig(BaseSettings):
    """Top-level configuration aggregating all subsystem configs.

    Load with: config = PipelineConfig()
    Override with env vars: CHAT_TO_COP_LLM_URL=... CHAT_TO_COP_DB_PATH=...
    """

    model_config = SettingsConfigDict(env_prefix="CHAT_TO_COP_")

    # IRC live mode
    irc_url: str | None = Field(
        default=None,
        description="IRC WebSocket URL for live mode (e.g. ws://10.5.185.72:8097). None = file replay.",
    )
    irc_channels: str | None = Field(
        default=None,
        description="Comma-separated IRC channels to join (e.g. '#c2_coord,#fires'). None = DASH 3 defaults.",
    )
    irc_discover_channels: bool = Field(
        default=True,
        description="Enable periodic IRC LIST to auto-join new channels.",
    )
    irc_max_channels: int = Field(
        default=50,
        description="Cap on total IRC channels to prevent resource exhaustion.",
    )

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
    cop_writer: CoPWriterConfig = Field(default_factory=CoPWriterConfig)
    reference: ReferenceDataConfig = Field(default_factory=ReferenceDataConfig)

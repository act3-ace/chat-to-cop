"""Configuration management for the chat-to-cop pipeline.

All config is file-based (TOML/JSON) with environment variable overrides.
No vendor-specific configuration — backend selection is just a URL + model name.
"""

from __future__ import annotations


# TODO: Implement Config
# - Load from config/ directory (channels, patterns, backend URLs)
# - Environment variable overrides (CHAT_TO_COP_LLM_URL, etc.)
# - Backend configuration: list of (url, model, timeout) tuples
# - Channel priorities
# - Conversation window size
# - Circuit breaker thresholds

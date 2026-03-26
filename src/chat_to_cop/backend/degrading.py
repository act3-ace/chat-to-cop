"""Degrading backend: tries backends in order with circuit breakers.

This is what channel agents actually use. It wraps a priority-ordered
list of backends and handles:
- Timeout-based fallback (try next backend if current is too slow)
- Circuit breaker (3 consecutive failures disables a backend temporarily)
- Automatic recovery (re-enable backend after cooldown period)
- Metrics (track which backend handled each request)

Degradation order (configurable):
1. Primary LLM (e.g., 70B model via Ollama)
2. Fallback LLM (e.g., 8B model via Ollama)
3. Regex extraction
4. Passthrough (raw message, confidence 0.0)
"""

from __future__ import annotations


# TODO: Implement DegradingBackend
# - __init__(backends: list[LLMBackend], timeouts: list[float])
# - async extract(messages, schema) -> BaseModel
# - Circuit breaker state per backend
# - Cooldown timer for re-enabling failed backends
# - Metrics: which backend succeeded, latency, failure count

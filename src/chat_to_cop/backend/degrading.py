"""Degrading backend: tries backends in order with circuit breakers.

This is what channel agents actually use. It wraps a priority-ordered
list of backends and handles:
- Timeout-based fallback (try next backend if current is too slow)
- Circuit breaker (consecutive failures disable a backend temporarily)
- Automatic recovery (re-enable backend after cooldown period)
- Metrics (track which backend handled each request)

Degradation order (configurable):
1. Primary LLM (e.g., 70B model via Ollama)
2. Fallback LLM (e.g., 8B model via Ollama)
3. Regex extraction
4. Passthrough (raw message, confidence 0.0)
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from loguru import logger
from pybreaker import CircuitBreaker, CircuitBreakerError
from pydantic import BaseModel

from chat_to_cop.backend.base import LLMBackend
from chat_to_cop.metrics import metrics
from chat_to_cop.models.cop_update import CoPUpdate, UpdateType

# pybreaker uses datetime.now(UTC) internally
_UTC = timezone.utc


def load_cascade_thresholds(path: str | Path) -> dict[str, float]:
    """Load a per-UpdateType cascade-threshold map from a JSON file.

    The file is expected to contain a flat JSON object mapping update_type
    values (and a ``"default"`` entry) to floats in [0.0, 1.0]. Keys starting
    with an underscore (e.g. ``"_description"``, ``"_related_issue"``) are
    treated as human-readable annotations and ignored. Non-numeric values
    are dropped with a warning rather than raising, so a stray string in
    the JSON doesn't crash the pipeline at startup.

    Returns an empty dict if the path is missing. Callers combine this with
    the scalar ``cascade_threshold`` fallback when constructing a
    ``DegradingBackend``.
    """
    p = Path(path)
    if not p.exists():
        logger.warning("cascade_thresholds file not found: {} — scalar fallback only", p)
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        logger.error("cascade_thresholds file malformed: {} — {}", p, exc)
        raise
    if not isinstance(raw, dict):
        logger.error("cascade_thresholds file not a JSON object: {}", p)
        return {}
    cleaned: dict[str, float] = {}
    for key, val in raw.items():
        if key.startswith("_"):
            continue
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            logger.warning("cascade_thresholds[{}] is not numeric ({!r}) — skipping", key, val)
            continue
        cleaned[key] = float(val)
    return cleaned


class _BackendSlot:
    """Internal wrapper: one backend + its circuit breaker + per-slot timeout."""

    def __init__(self, backend: LLMBackend, timeout: float, fail_max: int, cooldown: float) -> None:
        self.backend = backend
        self.timeout = timeout
        self.breaker = CircuitBreaker(fail_max=fail_max, reset_timeout=cooldown)
        self.name = _backend_name(backend)
        # Track when the breaker last opened (for time-to-adapt KPP)
        self.last_opened: float | None = None


def _backend_name(backend: LLMBackend) -> str:
    """Derive a short name for metrics/logging."""
    cls = type(backend).__name__
    if hasattr(backend, "model"):
        return f"{cls}({backend.model})"
    return cls


def _fail() -> None:
    """Sentinel function passed to breaker.call() to register a failure."""
    raise _FailureMarker()


def _is_breaker_open(slot: _BackendSlot) -> bool:
    """Check whether a slot's breaker is blocking calls.

    Handles the open → half-open transition that pybreaker normally does
    inside ``breaker.call()``, which we can't use directly because our
    backend calls are async.
    """
    state = slot.breaker.current_state
    if state != "open":
        return False

    # Replicate pybreaker's timeout check for the open → half-open transition
    opened_at = slot.breaker._state_storage.opened_at
    if opened_at is None:
        return True
    timeout_td = timedelta(seconds=slot.breaker.reset_timeout)
    if datetime.now(_UTC) < opened_at + timeout_td:
        return True  # Timeout not elapsed — still open

    # Timeout elapsed — transition to half-open (allows one test call)
    slot.breaker.half_open()
    return False


class DegradingBackend:
    """Wraps a priority-ordered list of backends with circuit breakers.

    Tries each backend in order. If a backend raises or times out, the
    failure is recorded in its circuit breaker and the next backend is tried.
    After ``fail_max`` consecutive failures a circuit breaker opens, skipping
    that backend until the cooldown expires.

    A passthrough fallback is always appended: it returns the raw message
    as a CoPUpdate with confidence 0.0 — **Pattern B: never drop data**.
    """

    def __init__(
        self,
        backends: list[LLMBackend],
        timeouts: list[float] | None = None,
        fail_max: int = 3,
        cooldown: float = 30.0,
        cascade_threshold: float = 0.0,
        cascade_thresholds: dict[str, float] | None = None,
    ) -> None:
        if not backends:
            raise ValueError("At least one backend is required")

        if timeouts is None:
            timeouts = [10.0] * len(backends)
        if len(timeouts) != len(backends):
            raise ValueError(f"Got {len(backends)} backends but {len(timeouts)} timeouts")

        self._slots = [_BackendSlot(b, t, fail_max=fail_max, cooldown=cooldown) for b, t in zip(backends, timeouts)]
        self._fail_max = fail_max
        self._cooldown = cooldown
        # Confidence-aware cascading (FrugalGPT pattern):
        # If > 0, a successful extraction with confidence below this threshold
        # triggers escalation to the next backend instead of returning immediately.
        # Default 0.0 preserves existing behavior (any success returns immediately).
        self._cascade_threshold = cascade_threshold
        # Per-UpdateType escalation thresholds (issue #67). A fire_mission /
        # csar extraction should demand higher confidence than a fuel report
        # before accepting a cheap model's output. When non-empty, the
        # per-type map takes precedence over the scalar cascade_threshold for
        # every result whose schema carries an `update_type`; unknown types
        # fall back to the map's "default" entry, which itself falls back to
        # the scalar cascade_threshold. Empty map preserves the pre-#67
        # behavior (scalar threshold only). Filter out non-numeric entries
        # so a "_description" key or similar annotations in a JSON source
        # don't trip a runtime comparison.
        self._cascade_thresholds: dict[str, float] = {
            k: float(v)
            for k, v in (cascade_thresholds or {}).items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)
        }

    def _threshold_for(self, result: BaseModel) -> float:
        """Resolve the escalation threshold that applies to this result.

        Priority: per-type map entry > per-type map "default" > scalar
        cascade_threshold. Returning 0.0 means "cascade disabled for this
        result" (caller checks ``effective > 0`` before escalating).
        """
        if not self._cascade_thresholds:
            return self._cascade_threshold

        update_type = getattr(result, "update_type", None)
        if update_type is not None:
            type_key = getattr(update_type, "value", update_type)
            if isinstance(type_key, str) and type_key in self._cascade_thresholds:
                return self._cascade_thresholds[type_key]

        # Unknown type or schema without update_type — use map default,
        # then scalar fallback. Use `.get` with a sentinel rather than
        # `in`+lookup to keep the hot path to one dict access.
        return self._cascade_thresholds.get("default", self._cascade_threshold)

    async def extract(
        self,
        messages: list[dict],
        schema: type[BaseModel],
    ) -> BaseModel:
        """Try each backend in order; fall through to passthrough on total failure.

        If cascade_threshold > 0, a successful extraction with confidence below
        the threshold will trigger escalation to the next backend instead of
        returning immediately. The best result seen across all backends is kept
        as a safety net. (FrugalGPT / confidence-aware cascading pattern.)
        """
        metrics.inc("degrading_calls_total")
        best_cascade_result: BaseModel | None = None
        best_cascade_confidence: float = -1.0

        for slot in self._slots:
            # Gate: check circuit breaker state (handles open → half-open transition)
            if _is_breaker_open(slot):
                metrics.inc("backend_circuit_open_skips", labels={"backend": slot.name})
                logger.debug("Skipping {} — circuit breaker open", slot.name)
                continue

            try:
                async with metrics.async_timer("extraction_latency_seconds", labels={"backend": slot.name}):
                    result = await asyncio.wait_for(
                        slot.backend.extract(messages, schema),
                        timeout=slot.timeout,
                    )

                # Register success with the breaker
                slot.breaker.call(lambda: None)

                # If this backend previously had its breaker open, record
                # time-to-adapt (KPP-A)
                if slot.last_opened is not None:
                    tta = time.monotonic() - slot.last_opened
                    metrics.observe("time_to_adapt_seconds", tta, labels={"backend": slot.name})
                    logger.info("{} recovered after {:.1f}s (time-to-adapt)", slot.name, tta)
                    slot.last_opened = None

                # Confidence-aware cascading: if result confidence is below
                # threshold and there are more backends to try, escalate.
                # Track the best result seen so far as a safety net.
                result_confidence = getattr(result, "confidence", 1.0)
                if result_confidence > best_cascade_confidence:
                    best_cascade_result = result
                    best_cascade_confidence = result_confidence

                effective_threshold = self._threshold_for(result)
                if effective_threshold > 0 and result_confidence < effective_threshold:
                    metrics.inc("confidence_cascade_escalations", labels={"backend": slot.name})
                    logger.info(
                        "Confidence cascade: {} returned {:.2f} < {:.2f} threshold, escalating",
                        slot.name,
                        result_confidence,
                        effective_threshold,
                    )
                    continue

                metrics.inc("backend_selected_total", labels={"backend": slot.name})
                return result

            except Exception as exc:
                # Record the failure in the breaker
                try:
                    slot.breaker.call(_fail)
                except (CircuitBreakerError, _FailureMarker):
                    pass

                # If the breaker just opened, record the timestamp
                if slot.breaker.current_state == "open" and slot.last_opened is None:
                    slot.last_opened = time.monotonic()
                    metrics.inc("degradation_events_total", labels={"backend": slot.name})
                    logger.warning("Circuit breaker opened for {}", slot.name)

                metrics.inc("backend_failures_total", labels={"backend": slot.name, "reason": type(exc).__name__})
                logger.debug("Backend {} failed: {}", slot.name, exc)
                continue

        # All backends exhausted — return best cascade result if we have one,
        # otherwise passthrough (Pattern B: never drop data)
        if best_cascade_result is not None:
            metrics.inc("backend_selected_total", labels={"backend": "cascade_best"})
            logger.info(
                "All backends tried — returning best cascade result (confidence={:.2f})",
                best_cascade_confidence,
            )
            return best_cascade_result
        return self._passthrough(messages)

    @staticmethod
    def _passthrough(messages: list[dict]) -> CoPUpdate:
        """Last-resort fallback: wrap the raw message as a CoPUpdate."""
        content = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                break

        metrics.inc("backend_selected_total", labels={"backend": "passthrough"})
        metrics.inc("passthrough_total")
        logger.info("All backends exhausted — passthrough for: {:.80s}", content)

        return CoPUpdate(
            update_type=UpdateType.NONE,
            confidence=0.0,
            extraction_method="passthrough",
            entities=[],
            source_channel="",
            source_speaker="",
            source_message=content,
            timestamp=datetime.now(timezone.utc),
            reasoning="All backends unavailable — raw passthrough (Pattern B)",
        )

    @property
    def slots(self) -> list[_BackendSlot]:
        """Expose slots for inspection (testing, monitoring)."""
        return list(self._slots)

    def __repr__(self) -> str:
        names = [s.name for s in self._slots]
        return f"DegradingBackend(backends={names})"


class _FailureMarker(Exception):
    """Internal marker raised inside breaker.call() to register a failure."""

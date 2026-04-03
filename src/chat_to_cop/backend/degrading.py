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
import time
from datetime import datetime, timedelta, timezone

from loguru import logger
from pybreaker import CircuitBreaker, CircuitBreakerError
from pydantic import BaseModel

from chat_to_cop.backend.base import LLMBackend
from chat_to_cop.metrics import metrics
from chat_to_cop.models.cop_update import CoPUpdate, UpdateType

# pybreaker uses datetime.now(UTC) internally
_UTC = timezone.utc


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

                if self._cascade_threshold > 0 and result_confidence < self._cascade_threshold:
                    metrics.inc("confidence_cascade_escalations", labels={"backend": slot.name})
                    logger.info(
                        "Confidence cascade: {} returned {:.2f} < {:.2f} threshold, escalating",
                        slot.name, result_confidence, self._cascade_threshold,
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

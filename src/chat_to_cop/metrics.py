"""Toggleable instrumentation for the chat-to-cop pipeline.

When enabled (CHAT_TO_COP_METRICS=true), records timing, counters, and
histograms for every extraction call. When disabled, all decorators and
context managers are true no-ops with near-zero overhead.

Usage:
    from chat_to_cop.metrics import metrics, timed

    # Decorate async functions to time them
    @timed("extraction")
    async def extract_something():
        ...

    # Use context manager for inline timing
    async with metrics.timer("backend_call"):
        result = await backend.extract(...)

    # Increment counters
    metrics.inc("messages_received_total", labels={"channel": "#c2_coord"})

    # Record values to histograms
    metrics.observe("confidence", 0.85, labels={"update_type": "fuel"})

    # Get a snapshot of all metrics
    snapshot = metrics.snapshot()
"""

from __future__ import annotations

import os
import time
from collections import defaultdict
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from functools import wraps
from typing import Any

from loguru import logger


def _is_enabled() -> bool:
    return os.environ.get("CHAT_TO_COP_METRICS", "true").lower() in ("true", "1", "yes")


@dataclass
class HistogramBucket:
    """Tracks min/max/sum/count for a named metric."""

    count: int = 0
    total: float = 0.0
    min_val: float = float("inf")
    max_val: float = float("-inf")

    def observe(self, value: float) -> None:
        self.count += 1
        self.total += value
        self.min_val = min(self.min_val, value)
        self.max_val = max(self.max_val, value)

    @property
    def mean(self) -> float:
        return self.total / self.count if self.count > 0 else 0.0

    def to_dict(self) -> dict[str, float]:
        return {
            "count": self.count,
            "total": self.total,
            "min": self.min_val if self.count > 0 else 0.0,
            "max": self.max_val if self.count > 0 else 0.0,
            "mean": self.mean,
        }


def _label_key(labels: dict[str, str] | None) -> str:
    """Convert labels dict to a stable string key."""
    if not labels:
        return ""
    return ",".join(f"{k}={v}" for k, v in sorted(labels.items()))


class MetricsRegistry:
    """Central metrics collection with toggle support.

    When disabled, all methods are fast no-ops. When enabled, records
    counters and histograms in memory, with optional loguru output.
    """

    def __init__(self) -> None:
        self._enabled = _is_enabled()
        self._counters: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._histograms: dict[str, dict[str, HistogramBucket]] = defaultdict(lambda: defaultdict(HistogramBucket))

    @property
    def enabled(self) -> bool:
        return self._enabled

    def enable(self) -> None:
        self._enabled = True

    def disable(self) -> None:
        self._enabled = False

    def inc(self, name: str, value: int = 1, labels: dict[str, str] | None = None) -> None:
        """Increment a counter."""
        if not self._enabled:
            return
        key = _label_key(labels)
        self._counters[name][key] += value

    def observe(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        """Record a value to a histogram."""
        if not self._enabled:
            return
        key = _label_key(labels)
        self._histograms[name][key].observe(value)

    @contextmanager
    def timer(self, name: str, labels: dict[str, str] | None = None):
        """Synchronous context manager that times a block and records to histogram."""
        if not self._enabled:
            yield
            return
        start = time.perf_counter()
        yield
        elapsed = time.perf_counter() - start
        self.observe(name, elapsed, labels=labels)

    @asynccontextmanager
    async def async_timer(self, name: str, labels: dict[str, str] | None = None):
        """Async context manager that times a block and records to histogram."""
        if not self._enabled:
            yield
            return
        start = time.perf_counter()
        yield
        elapsed = time.perf_counter() - start
        self.observe(name, elapsed, labels=labels)

    def get_counter(self, name: str, labels: dict[str, str] | None = None) -> int:
        """Get current value of a counter."""
        key = _label_key(labels)
        return self._counters.get(name, {}).get(key, 0)

    def get_histogram(self, name: str, labels: dict[str, str] | None = None) -> dict[str, float]:
        """Get histogram summary for a metric."""
        key = _label_key(labels)
        bucket = self._histograms.get(name, {}).get(key)
        if bucket is None:
            return {"count": 0, "total": 0.0, "min": 0.0, "max": 0.0, "mean": 0.0}
        return bucket.to_dict()

    def snapshot(self) -> dict[str, Any]:
        """Return a snapshot of all metrics."""
        return {
            "counters": {name: dict(labels) for name, labels in self._counters.items()},
            "histograms": {
                name: {lbl: bucket.to_dict() for lbl, bucket in labels.items()}
                for name, labels in self._histograms.items()
            },
        }

    def reset(self) -> None:
        """Clear all metrics. Useful for testing."""
        self._counters.clear()
        self._histograms.clear()

    def log_summary(self) -> None:
        """Log a human-readable summary of all metrics."""
        if not self._enabled:
            return
        snap = self.snapshot()
        logger.info("--- Metrics Summary ---")
        for name, labels in snap["counters"].items():
            for lbl, val in labels.items():
                suffix = f" [{lbl}]" if lbl else ""
                logger.info(f"  {name}{suffix}: {val}")
        for name, labels in snap["histograms"].items():
            for lbl, stats in labels.items():
                suffix = f" [{lbl}]" if lbl else ""
                logger.info(
                    f"  {name}{suffix}: count={stats['count']}, "
                    f"mean={stats['mean']:.4f}, min={stats['min']:.4f}, max={stats['max']:.4f}"
                )


# Global singleton — import this everywhere
metrics = MetricsRegistry()


def timed(metric_name: str, labels: dict[str, str] | None = None):
    """Decorator that times an async function and records to the global metrics.

    When metrics are disabled, this is a true no-op — the function is
    returned unwrapped with zero overhead.

    Usage:
        @timed("extraction_latency", labels={"backend": "ollama"})
        async def extract(messages, schema):
            ...
    """

    def decorator(fn):
        if not metrics.enabled:
            return fn

        @wraps(fn)
        async def wrapper(*args, **kwargs):
            start = time.perf_counter()
            try:
                return await fn(*args, **kwargs)
            finally:
                elapsed = time.perf_counter() - start
                metrics.observe(metric_name, elapsed, labels=labels)

        return wrapper

    return decorator

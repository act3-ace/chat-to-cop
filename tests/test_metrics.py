"""Tests for the instrumentation/metrics module."""

import asyncio
import time

from chat_to_cop.metrics import MetricsRegistry, timed


class TestMetricsRegistry:
    """Test counter and histogram operations."""

    def test_counter_increment(self):
        m = MetricsRegistry()
        m.enable()
        m.inc("messages_total")
        m.inc("messages_total")
        m.inc("messages_total", value=3)
        assert m.get_counter("messages_total") == 5

    def test_counter_with_labels(self):
        m = MetricsRegistry()
        m.enable()
        m.inc("messages_total", labels={"channel": "#c2_coord"})
        m.inc("messages_total", labels={"channel": "#fires"})
        m.inc("messages_total", labels={"channel": "#c2_coord"})
        assert m.get_counter("messages_total", labels={"channel": "#c2_coord"}) == 2
        assert m.get_counter("messages_total", labels={"channel": "#fires"}) == 1

    def test_counter_missing_returns_zero(self):
        m = MetricsRegistry()
        m.enable()
        assert m.get_counter("nonexistent") == 0

    def test_histogram_observe(self):
        m = MetricsRegistry()
        m.enable()
        m.observe("latency", 0.5)
        m.observe("latency", 1.5)
        m.observe("latency", 1.0)
        stats = m.get_histogram("latency")
        assert stats["count"] == 3
        assert stats["min"] == 0.5
        assert stats["max"] == 1.5
        assert abs(stats["mean"] - 1.0) < 0.001

    def test_histogram_missing_returns_zeros(self):
        m = MetricsRegistry()
        m.enable()
        stats = m.get_histogram("nonexistent")
        assert stats["count"] == 0
        assert stats["mean"] == 0.0

    def test_timer_sync(self):
        m = MetricsRegistry()
        m.enable()
        with m.timer("sync_op"):
            time.sleep(0.01)
        stats = m.get_histogram("sync_op")
        assert stats["count"] == 1
        assert stats["min"] >= 0.01

    def test_timer_async(self):
        m = MetricsRegistry()
        m.enable()

        async def run():
            async with m.async_timer("async_op"):
                await asyncio.sleep(0.01)

        asyncio.run(run())
        stats = m.get_histogram("async_op")
        assert stats["count"] == 1
        assert stats["min"] >= 0.01

    def test_snapshot(self):
        m = MetricsRegistry()
        m.enable()
        m.inc("a", labels={"x": "1"})
        m.observe("b", 42.0)
        snap = m.snapshot()
        assert "a" in snap["counters"]
        assert "b" in snap["histograms"]

    def test_reset_clears_everything(self):
        m = MetricsRegistry()
        m.enable()
        m.inc("counter")
        m.observe("hist", 1.0)
        m.reset()
        assert m.get_counter("counter") == 0
        assert m.get_histogram("hist")["count"] == 0


class TestDisabledMetrics:
    """Verify that disabled metrics are true no-ops."""

    def test_disabled_counter_noop(self):
        m = MetricsRegistry()
        m.disable()
        m.inc("should_not_record")
        assert m.get_counter("should_not_record") == 0

    def test_disabled_observe_noop(self):
        m = MetricsRegistry()
        m.disable()
        m.observe("should_not_record", 99.0)
        assert m.get_histogram("should_not_record")["count"] == 0

    def test_disabled_timer_still_executes_block(self):
        m = MetricsRegistry()
        m.disable()
        executed = False
        with m.timer("noop"):
            executed = True
        assert executed
        assert m.get_histogram("noop")["count"] == 0

    def test_disabled_async_timer_still_executes_block(self):
        m = MetricsRegistry()
        m.disable()
        executed = False

        async def run():
            nonlocal executed
            async with m.async_timer("noop"):
                executed = True

        asyncio.run(run())
        assert executed
        assert m.get_histogram("noop")["count"] == 0

    def test_disabled_overhead_is_minimal(self):
        """Disabled metrics should add negligible overhead."""
        m = MetricsRegistry()
        m.disable()
        iterations = 100_000
        start = time.perf_counter()
        for _ in range(iterations):
            m.inc("overhead_test")
        elapsed = time.perf_counter() - start
        per_call_ns = (elapsed / iterations) * 1e9
        # Should be well under 1 microsecond per call
        assert per_call_ns < 1000, f"Disabled inc() took {per_call_ns:.0f}ns per call"


class TestTimedDecorator:
    """Test the @timed decorator."""

    def test_timed_decorator_records(self):
        # Need to test with a fresh registry that's enabled
        import chat_to_cop.metrics as mod

        old = mod.metrics
        mod.metrics = MetricsRegistry()
        mod.metrics.enable()
        try:

            @timed("decorated_fn")
            async def my_func():
                await asyncio.sleep(0.01)
                return 42

            result = asyncio.run(my_func())
            assert result == 42
            stats = mod.metrics.get_histogram("decorated_fn")
            assert stats["count"] == 1
            assert stats["min"] >= 0.01
        finally:
            mod.metrics = old

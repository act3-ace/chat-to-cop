"""Tests for the degrading backend with circuit breakers."""

import asyncio

import pytest
from pydantic import BaseModel

from chat_to_cop.backend.degrading import DegradingBackend
from chat_to_cop.metrics import metrics
from chat_to_cop.models.cop_update import CoPUpdate, UpdateType

# --- Fake backends for testing ---


class SimpleResult(BaseModel):
    value: str = "ok"


class FakeBackend:
    """Always succeeds, returning a SimpleResult."""

    def __init__(self, name: str = "fake"):
        self.model = name
        self.call_count = 0

    async def extract(self, messages: list[dict], schema: type[BaseModel]) -> BaseModel:
        self.call_count += 1
        return SimpleResult(value=f"from-{self.model}")


class FailingBackend:
    """Always raises an exception."""

    def __init__(self, name: str = "failing", error: Exception | None = None):
        self.model = name
        self.call_count = 0
        self._error = error or RuntimeError(f"{name} is down")

    async def extract(self, messages: list[dict], schema: type[BaseModel]) -> BaseModel:
        self.call_count += 1
        raise self._error


class SlowBackend:
    """Takes longer than the timeout to respond."""

    def __init__(self, delay: float = 5.0, name: str = "slow"):
        self.model = name
        self.delay = delay
        self.call_count = 0

    async def extract(self, messages: list[dict], schema: type[BaseModel]) -> BaseModel:
        self.call_count += 1
        await asyncio.sleep(self.delay)
        return SimpleResult(value="too-late")


class ConfidenceBackend:
    """Returns a CoPUpdate with a specific confidence value. For cascade testing."""

    def __init__(self, confidence: float, name: str = "conf"):
        self.model = name
        self.call_count = 0
        self._confidence = confidence

    async def extract(self, messages: list[dict], schema: type[BaseModel]) -> BaseModel:
        self.call_count += 1
        return CoPUpdate(
            update_type=UpdateType.FUEL,
            confidence=self._confidence,
            source_message="test",
            extraction_method="llm",
        )


class FailNTimesBackend:
    """Fails N times, then succeeds."""

    def __init__(self, fail_count: int, name: str = "flaky"):
        self.model = name
        self.call_count = 0
        self._fail_count = fail_count

    async def extract(self, messages: list[dict], schema: type[BaseModel]) -> BaseModel:
        self.call_count += 1
        if self.call_count <= self._fail_count:
            raise RuntimeError(f"Failure #{self.call_count}")
        return SimpleResult(value=f"recovered-{self.model}")


SAMPLE_MESSAGES = [{"role": "user", "content": "RR15 F+40 bullseye 270/50"}]


# --- Construction tests ---


class TestConstruction:
    def test_requires_at_least_one_backend(self):
        with pytest.raises(ValueError, match="(?i)at least one"):
            DegradingBackend(backends=[])

    def test_timeout_count_must_match(self):
        with pytest.raises(ValueError, match="timeouts"):
            DegradingBackend(backends=[FakeBackend()], timeouts=[1.0, 2.0])

    def test_default_timeouts(self):
        db = DegradingBackend(backends=[FakeBackend(), FakeBackend()])
        assert len(db.slots) == 2
        assert all(s.timeout == 10.0 for s in db.slots)

    def test_repr(self):
        db = DegradingBackend(backends=[FakeBackend("primary")])
        assert "primary" in repr(db)


# --- Happy path ---


class TestHappyPath:
    def test_first_backend_used_when_healthy(self):
        primary = FakeBackend("primary")
        fallback = FakeBackend("fallback")
        db = DegradingBackend(backends=[primary, fallback], timeouts=[5.0, 5.0])

        result = asyncio.run(db.extract(SAMPLE_MESSAGES, SimpleResult))
        assert isinstance(result, SimpleResult)
        assert result.value == "from-primary"
        assert primary.call_count == 1
        assert fallback.call_count == 0

    def test_single_backend_works(self):
        backend = FakeBackend("solo")
        db = DegradingBackend(backends=[backend], timeouts=[5.0])

        result = asyncio.run(db.extract(SAMPLE_MESSAGES, SimpleResult))
        assert result.value == "from-solo"


# --- Fallback chain ---


class TestFallbackChain:
    def test_falls_through_to_second_backend(self):
        primary = FailingBackend("primary")
        fallback = FakeBackend("fallback")
        db = DegradingBackend(backends=[primary, fallback], timeouts=[5.0, 5.0])

        result = asyncio.run(db.extract(SAMPLE_MESSAGES, SimpleResult))
        assert result.value == "from-fallback"
        assert primary.call_count == 1
        assert fallback.call_count == 1

    def test_falls_through_multiple_failures(self):
        b1 = FailingBackend("b1")
        b2 = FailingBackend("b2")
        b3 = FakeBackend("b3")
        db = DegradingBackend(backends=[b1, b2, b3], timeouts=[5.0, 5.0, 5.0])

        result = asyncio.run(db.extract(SAMPLE_MESSAGES, SimpleResult))
        assert result.value == "from-b3"

    def test_timeout_triggers_fallback(self):
        slow = SlowBackend(delay=5.0, name="slow")
        fast = FakeBackend("fast")
        db = DegradingBackend(backends=[slow, fast], timeouts=[0.05, 5.0])

        result = asyncio.run(db.extract(SAMPLE_MESSAGES, SimpleResult))
        assert result.value == "from-fast"
        assert slow.call_count == 1


# --- Passthrough fallback ---


class TestPassthrough:
    def test_passthrough_when_all_fail(self):
        b1 = FailingBackend("b1")
        b2 = FailingBackend("b2")
        db = DegradingBackend(backends=[b1, b2], timeouts=[5.0, 5.0])

        result = asyncio.run(db.extract(SAMPLE_MESSAGES, SimpleResult))
        assert isinstance(result, CoPUpdate)
        assert result.confidence == 0.0
        assert result.extraction_method == "passthrough"
        assert result.update_type == UpdateType.NONE
        assert "RR15 F+40" in result.source_message
        assert "Pattern B" in result.reasoning

    def test_passthrough_preserves_message_content(self):
        msg = "HADES31 splash 2 TM636 destroyed"
        db = DegradingBackend(backends=[FailingBackend()], timeouts=[5.0])

        result = asyncio.run(db.extract([{"role": "user", "content": msg}], SimpleResult))
        assert result.source_message == msg

    def test_passthrough_handles_empty_messages(self):
        db = DegradingBackend(backends=[FailingBackend()], timeouts=[5.0])
        result = asyncio.run(db.extract([], SimpleResult))
        assert isinstance(result, CoPUpdate)
        assert result.source_message == ""


# --- Circuit breaker ---


class TestCircuitBreaker:
    def test_breaker_opens_after_consecutive_failures(self):
        failing = FailingBackend("llm")
        fallback = FakeBackend("regex")
        db = DegradingBackend(
            backends=[failing, fallback],
            timeouts=[5.0, 5.0],
            fail_max=3,
            cooldown=60.0,
        )

        # First 3 calls: failing backend is tried each time, falls through to regex
        for _ in range(3):
            result = asyncio.run(db.extract(SAMPLE_MESSAGES, SimpleResult))
            assert result.value == "from-regex"

        # After 3 failures, the breaker should be open
        assert db.slots[0].breaker.current_state == "open"

        # 4th call: failing backend is skipped entirely
        failing.call_count = 0
        result = asyncio.run(db.extract(SAMPLE_MESSAGES, SimpleResult))
        assert result.value == "from-regex"
        assert failing.call_count == 0  # Skipped!

    def test_breaker_with_custom_fail_max(self):
        failing = FailingBackend("llm")
        fallback = FakeBackend("regex")
        db = DegradingBackend(
            backends=[failing, fallback],
            timeouts=[5.0, 5.0],
            fail_max=5,
        )

        # After 4 failures, breaker should still be closed (fail_max=5)
        for _ in range(4):
            asyncio.run(db.extract(SAMPLE_MESSAGES, SimpleResult))
        assert db.slots[0].breaker.current_state != "open"

        # 5th failure opens it
        asyncio.run(db.extract(SAMPLE_MESSAGES, SimpleResult))
        assert db.slots[0].breaker.current_state == "open"

    def test_breaker_cooldown_allows_retry(self):
        """After cooldown, the breaker should move to half-open and allow a retry."""
        failing = FailingBackend("llm")
        fallback = FakeBackend("regex")
        db = DegradingBackend(
            backends=[failing, fallback],
            timeouts=[5.0, 5.0],
            fail_max=3,
            cooldown=0.1,  # Very short cooldown for testing
        )

        # Trip the breaker
        for _ in range(3):
            asyncio.run(db.extract(SAMPLE_MESSAGES, SimpleResult))
        assert db.slots[0].breaker.current_state == "open"

        # Wait for cooldown
        import time

        time.sleep(0.15)

        # Next call should try the failing backend again (half-open)
        failing.call_count = 0
        asyncio.run(db.extract(SAMPLE_MESSAGES, SimpleResult))
        # Backend was attempted (half-open allows one try)
        assert failing.call_count >= 1


# --- Metrics ---


class TestMetrics:
    def setup_method(self):
        metrics.enable()
        metrics.reset()

    def test_records_degrading_calls(self):
        db = DegradingBackend(backends=[FakeBackend()], timeouts=[5.0])
        asyncio.run(db.extract(SAMPLE_MESSAGES, SimpleResult))
        assert metrics.get_counter("degrading_calls_total") >= 1

    def test_records_backend_selection(self):
        primary = FakeBackend("primary")
        db = DegradingBackend(backends=[primary], timeouts=[5.0])
        asyncio.run(db.extract(SAMPLE_MESSAGES, SimpleResult))
        assert metrics.get_counter("backend_selected_total", labels={"backend": "FakeBackend(primary)"}) >= 1

    def test_records_failure_counts(self):
        failing = FailingBackend("llm")
        fallback = FakeBackend("regex")
        db = DegradingBackend(backends=[failing, fallback], timeouts=[5.0, 5.0])
        asyncio.run(db.extract(SAMPLE_MESSAGES, SimpleResult))
        labels = {"backend": "FailingBackend(llm)", "reason": "RuntimeError"}
        assert metrics.get_counter("backend_failures_total", labels=labels) >= 1

    def test_records_passthrough(self):
        db = DegradingBackend(backends=[FailingBackend()], timeouts=[5.0])
        asyncio.run(db.extract(SAMPLE_MESSAGES, SimpleResult))
        assert metrics.get_counter("passthrough_total") >= 1

    def test_records_circuit_breaker_skips(self):
        failing = FailingBackend("llm")
        fallback = FakeBackend("regex")
        db = DegradingBackend(
            backends=[failing, fallback],
            timeouts=[5.0, 5.0],
            fail_max=3,
            cooldown=60.0,
        )

        # Trip the breaker
        for _ in range(3):
            asyncio.run(db.extract(SAMPLE_MESSAGES, SimpleResult))

        # Next call should record a skip
        metrics.reset()
        asyncio.run(db.extract(SAMPLE_MESSAGES, SimpleResult))
        assert metrics.get_counter("backend_circuit_open_skips", labels={"backend": "FailingBackend(llm)"}) >= 1

    def test_records_degradation_event(self):
        failing = FailingBackend("llm")
        fallback = FakeBackend("regex")
        db = DegradingBackend(
            backends=[failing, fallback],
            timeouts=[5.0, 5.0],
            fail_max=3,
        )

        for _ in range(3):
            asyncio.run(db.extract(SAMPLE_MESSAGES, SimpleResult))

        assert metrics.get_counter("degradation_events_total", labels={"backend": "FailingBackend(llm)"}) >= 1


# --- Provenance ---


class TestProvenance:
    def test_passthrough_records_extraction_method(self):
        db = DegradingBackend(backends=[FailingBackend()], timeouts=[5.0])
        result = asyncio.run(db.extract(SAMPLE_MESSAGES, SimpleResult))
        assert isinstance(result, CoPUpdate)
        assert result.extraction_method == "passthrough"

    def test_successful_backend_result_preserved(self):
        """The backend's result is returned as-is — provenance comes from the backend itself."""
        backend = FakeBackend("llm-70b")
        db = DegradingBackend(backends=[backend], timeouts=[5.0])
        result = asyncio.run(db.extract(SAMPLE_MESSAGES, SimpleResult))
        assert result.value == "from-llm-70b"


# --- Edge cases ---


class TestEdgeCases:
    def test_all_backends_circuit_broken_gives_passthrough(self):
        b1 = FailingBackend("b1")
        b2 = FailingBackend("b2")
        db = DegradingBackend(
            backends=[b1, b2],
            timeouts=[5.0, 5.0],
            fail_max=3,
            cooldown=60.0,
        )

        # Trip both breakers
        for _ in range(3):
            asyncio.run(db.extract(SAMPLE_MESSAGES, SimpleResult))

        assert db.slots[0].breaker.current_state == "open"
        assert db.slots[1].breaker.current_state == "open"

        # Both open — passthrough
        result = asyncio.run(db.extract(SAMPLE_MESSAGES, SimpleResult))
        assert isinstance(result, CoPUpdate)
        assert result.extraction_method == "passthrough"

    def test_multiple_messages_only_last_user_used_for_passthrough(self):
        messages = [
            {"role": "system", "content": "You are a military chat interpreter."},
            {"role": "user", "content": "first message"},
            {"role": "user", "content": "second message"},
        ]
        db = DegradingBackend(backends=[FailingBackend()], timeouts=[5.0])
        result = asyncio.run(db.extract(messages, SimpleResult))
        assert result.source_message == "second message"

    def test_system_only_messages_passthrough(self):
        messages = [{"role": "system", "content": "system prompt only"}]
        db = DegradingBackend(backends=[FailingBackend()], timeouts=[5.0])
        result = asyncio.run(db.extract(messages, SimpleResult))
        assert result.source_message == ""


# ---------------------------------------------------------------------------
# Confidence-aware cascading tests (#43)
# ---------------------------------------------------------------------------


class TestConfidenceCascading:
    """Test FrugalGPT-style confidence-based escalation in DegradingBackend."""

    messages = [{"role": "user", "content": "RR15 F+40"}]

    def test_cascade_disabled_by_default(self):
        """With default cascade_threshold=0.0, any success returns immediately."""
        low = ConfidenceBackend(0.3, name="low")
        high = ConfidenceBackend(0.9, name="high")
        db = DegradingBackend(backends=[low, high], timeouts=[5.0, 5.0])
        result = asyncio.run(db.extract(self.messages, CoPUpdate))
        # Low backend returned 0.3 but cascade is disabled, so it's accepted
        assert result.confidence == 0.3
        assert low.call_count == 1
        assert high.call_count == 0

    def test_cascade_escalates_low_confidence(self):
        """With cascade_threshold=0.5, a 0.3 result triggers escalation to next backend."""
        low = ConfidenceBackend(0.3, name="low")
        high = ConfidenceBackend(0.8, name="high")
        db = DegradingBackend(backends=[low, high], timeouts=[5.0, 5.0], cascade_threshold=0.5)
        result = asyncio.run(db.extract(self.messages, CoPUpdate))
        # Low was tried first, escalated, high returned 0.8 >= threshold
        assert result.confidence == 0.8
        assert low.call_count == 1
        assert high.call_count == 1

    def test_cascade_returns_best_when_all_below_threshold(self):
        """When all backends return confidence below threshold, return the best one."""
        b1 = ConfidenceBackend(0.2, name="worst")
        b2 = ConfidenceBackend(0.4, name="better")
        b3 = ConfidenceBackend(0.3, name="middle")
        db = DegradingBackend(backends=[b1, b2, b3], timeouts=[5.0, 5.0, 5.0], cascade_threshold=0.5)
        result = asyncio.run(db.extract(self.messages, CoPUpdate))
        # All below 0.5, returns best (0.4 from b2)
        assert result.confidence == 0.4
        assert b1.call_count == 1
        assert b2.call_count == 1
        assert b3.call_count == 1

    def test_cascade_accepts_above_threshold(self):
        """A result at or above the threshold is accepted immediately."""
        b1 = ConfidenceBackend(0.5, name="exact")
        b2 = ConfidenceBackend(0.9, name="unused")
        db = DegradingBackend(backends=[b1, b2], timeouts=[5.0, 5.0], cascade_threshold=0.5)
        result = asyncio.run(db.extract(self.messages, CoPUpdate))
        assert result.confidence == 0.5
        assert b1.call_count == 1
        assert b2.call_count == 0

    def test_cascade_skips_failed_backends(self):
        """Failed backends are skipped; cascade continues to the next."""
        failing = FailingBackend(name="down")
        high = ConfidenceBackend(0.8, name="high")
        db = DegradingBackend(backends=[failing, high], timeouts=[5.0, 5.0], cascade_threshold=0.5)
        result = asyncio.run(db.extract(self.messages, CoPUpdate))
        assert result.confidence == 0.8
        assert failing.call_count == 1
        assert high.call_count == 1

    def test_cascade_increments_escalation_metric(self):
        """The confidence_cascade_escalations metric fires on each escalation."""
        metrics.reset()
        low = ConfidenceBackend(0.2, name="low")
        high = ConfidenceBackend(0.8, name="high")
        db = DegradingBackend(backends=[low, high], timeouts=[5.0, 5.0], cascade_threshold=0.5)
        asyncio.run(db.extract(self.messages, CoPUpdate))
        assert metrics.get_counter("confidence_cascade_escalations", labels={"backend": "ConfidenceBackend(low)"}) >= 1

    def test_cascade_preserves_pattern_b(self):
        """Even with cascade, if all backends fail AND no cascade result, passthrough works."""
        failing1 = FailingBackend(name="down1")
        failing2 = FailingBackend(name="down2")
        db = DegradingBackend(backends=[failing1, failing2], timeouts=[5.0, 5.0], cascade_threshold=0.5)
        result = asyncio.run(db.extract(self.messages, CoPUpdate))
        # Passthrough: confidence 0.0, Pattern B preserved
        assert result.confidence == 0.0
        assert "Pattern B" in (result.reasoning or "")

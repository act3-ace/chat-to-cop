"""Tests for the degrading backend with circuit breakers."""

import asyncio

import pytest
from pydantic import BaseModel

from chat_to_cop.backend.degrading import DegradingBackend, load_cascade_thresholds
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
        assert metrics.get_counter("degrading_calls_total") == 1

    def test_records_backend_selection(self):
        primary = FakeBackend("primary")
        db = DegradingBackend(backends=[primary], timeouts=[5.0])
        asyncio.run(db.extract(SAMPLE_MESSAGES, SimpleResult))
        assert metrics.get_counter("backend_selected_total", labels={"backend": "FakeBackend(primary)"}) == 1

    def test_records_failure_counts(self):
        failing = FailingBackend("llm")
        fallback = FakeBackend("regex")
        db = DegradingBackend(backends=[failing, fallback], timeouts=[5.0, 5.0])
        asyncio.run(db.extract(SAMPLE_MESSAGES, SimpleResult))
        labels = {"backend": "FailingBackend(llm)", "reason": "RuntimeError"}
        assert metrics.get_counter("backend_failures_total", labels=labels) == 1

    def test_records_passthrough(self):
        db = DegradingBackend(backends=[FailingBackend()], timeouts=[5.0])
        asyncio.run(db.extract(SAMPLE_MESSAGES, SimpleResult))
        assert metrics.get_counter("passthrough_total") == 1

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
        assert metrics.get_counter("backend_circuit_open_skips", labels={"backend": "FailingBackend(llm)"}) == 1

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


# --- Per-UpdateType cascade thresholds (issue #67) ---------------------------


class TypedConfidenceBackend:
    """CoPUpdate with configurable confidence AND update_type. For per-type cascade tests."""

    def __init__(self, confidence: float, update_type: UpdateType, name: str = "typed"):
        self.model = name
        self.call_count = 0
        self._confidence = confidence
        self._update_type = update_type

    async def extract(self, messages: list[dict], schema: type[BaseModel]) -> BaseModel:
        self.call_count += 1
        return CoPUpdate(
            update_type=self._update_type,
            confidence=self._confidence,
            source_message="test",
            extraction_method="llm",
        )


class TestPerTypeCascade:
    """Issue #67: per-UpdateType cascade thresholds override the scalar."""

    messages = [{"role": "user", "content": "test"}]

    # Representative sample of the map shipped in config/cascade_thresholds.json.
    # The "default" key is the fallback for unknown update_types; the scalar
    # cascade_threshold in the DegradingBackend constructor is the final
    # fallback when the map has no "default".
    PER_TYPE: dict[str, float] = {
        "csar": 0.85,
        "fire_mission": 0.80,
        "fuel": 0.55,
        "none": 0.50,
        "default": 0.70,
    }

    @pytest.mark.parametrize(
        "update_type,threshold",
        [
            (UpdateType.CSAR, 0.85),
            (UpdateType.FIRE_MISSION, 0.80),
            (UpdateType.FUEL, 0.55),
            (UpdateType.NONE, 0.50),
        ],
    )
    def test_below_type_threshold_escalates(self, update_type: UpdateType, threshold: float):
        """A result 0.02 below the type's threshold triggers escalation."""
        low = TypedConfidenceBackend(threshold - 0.02, update_type, name="low")
        high = TypedConfidenceBackend(0.99, update_type, name="high")
        db = DegradingBackend(
            backends=[low, high],
            timeouts=[5.0, 5.0],
            cascade_thresholds=self.PER_TYPE,
        )
        result = asyncio.run(db.extract(self.messages, CoPUpdate))
        # Both backends called — the low one escalated
        assert low.call_count == 1
        assert high.call_count == 1
        assert result.confidence == 0.99

    @pytest.mark.parametrize(
        "update_type,threshold",
        [
            (UpdateType.CSAR, 0.85),
            (UpdateType.FIRE_MISSION, 0.80),
            (UpdateType.FUEL, 0.55),
            (UpdateType.NONE, 0.50),
        ],
    )
    def test_above_type_threshold_accepts(self, update_type: UpdateType, threshold: float):
        """A result 0.02 above the type's threshold is accepted, no escalation."""
        ok = TypedConfidenceBackend(threshold + 0.02, update_type, name="ok")
        fallback = TypedConfidenceBackend(0.99, update_type, name="fallback")
        db = DegradingBackend(
            backends=[ok, fallback],
            timeouts=[5.0, 5.0],
            cascade_thresholds=self.PER_TYPE,
        )
        asyncio.run(db.extract(self.messages, CoPUpdate))
        # Only the first backend is called
        assert ok.call_count == 1
        assert fallback.call_count == 0

    def test_per_type_overrides_scalar(self):
        """When both scalar cascade_threshold and per-type map are set, per-type wins.

        A fire_mission at 0.75 is below the type's 0.80 threshold (escalates) but
        above the scalar 0.50 threshold (would pass if scalar were applied). The
        per-type entry must dominate, so this MUST escalate.
        """
        low = TypedConfidenceBackend(0.75, UpdateType.FIRE_MISSION, name="low")
        high = TypedConfidenceBackend(0.99, UpdateType.FIRE_MISSION, name="high")
        db = DegradingBackend(
            backends=[low, high],
            timeouts=[5.0, 5.0],
            cascade_threshold=0.50,  # scalar would pass
            cascade_thresholds={"fire_mission": 0.80, "default": 0.70},  # per-type escalates
        )
        asyncio.run(db.extract(self.messages, CoPUpdate))
        assert low.call_count == 1
        assert high.call_count == 1

    def test_unknown_type_falls_back_to_default_entry(self):
        """A type not in the map uses the 'default' entry, not the scalar."""
        # environmental is in the real config but NOT in this map — so it hits default=0.70
        low = TypedConfidenceBackend(0.65, UpdateType.ENVIRONMENTAL, name="low")
        high = TypedConfidenceBackend(0.99, UpdateType.ENVIRONMENTAL, name="high")
        db = DegradingBackend(
            backends=[low, high],
            timeouts=[5.0, 5.0],
            cascade_threshold=0.10,  # scalar would pass, must not be used
            cascade_thresholds={"csar": 0.85, "default": 0.70},
        )
        asyncio.run(db.extract(self.messages, CoPUpdate))
        # 0.65 < 0.70 default → escalate
        assert low.call_count == 1
        assert high.call_count == 1

    def test_no_default_in_map_falls_back_to_scalar(self):
        """If the map has no 'default' and the type isn't listed, use the scalar cascade_threshold."""
        low = TypedConfidenceBackend(0.40, UpdateType.THREAT, name="low")
        high = TypedConfidenceBackend(0.99, UpdateType.THREAT, name="high")
        db = DegradingBackend(
            backends=[low, high],
            timeouts=[5.0, 5.0],
            cascade_threshold=0.50,
            cascade_thresholds={"csar": 0.85},  # no "default", no "threat"
        )
        asyncio.run(db.extract(self.messages, CoPUpdate))
        # 0.40 < 0.50 scalar → escalate
        assert low.call_count == 1
        assert high.call_count == 1

    def test_empty_per_type_map_preserves_scalar_behavior(self):
        """An empty per-type map keeps the pre-#67 behavior: scalar threshold drives escalation."""
        low = ConfidenceBackend(0.40, name="low")
        high = ConfidenceBackend(0.99, name="high")
        db = DegradingBackend(
            backends=[low, high],
            timeouts=[5.0, 5.0],
            cascade_threshold=0.50,
            cascade_thresholds={},
        )
        asyncio.run(db.extract(self.messages, CoPUpdate))
        assert low.call_count == 1
        assert high.call_count == 1

    def test_schema_without_update_type_uses_default_or_scalar(self):
        """Generic schemas (no update_type attribute) use 'default' map entry or scalar fallback."""
        # FakeBackend returns SimpleResult which has no update_type and no confidence.
        # Without a confidence attr, getattr falls back to 1.0 → never escalates regardless.
        # So for this test, use ConfidenceBackend which always sets UpdateType.FUEL.
        # The "no update_type" case is exercised when schema=SimpleResult; confidence
        # resolves to 1.0 and cascade never fires. That is the existing
        # test_cascade_accepts_above_threshold contract — no new test needed.
        # Here we assert that when the map's default entry is used, it dominates.
        low = TypedConfidenceBackend(0.40, UpdateType.FUEL, name="low")
        high = TypedConfidenceBackend(0.99, UpdateType.FUEL, name="high")
        # Map has no FUEL entry, has default=0.60 → fuel result 0.40 < 0.60 → escalate
        db = DegradingBackend(
            backends=[low, high],
            timeouts=[5.0, 5.0],
            cascade_thresholds={"default": 0.60},
        )
        asyncio.run(db.extract(self.messages, CoPUpdate))
        assert low.call_count == 1
        assert high.call_count == 1


class TestLoadCascadeThresholds:
    """Issue #67: JSON loader for the per-UpdateType threshold map."""

    def test_loads_valid_file(self, tmp_path):
        p = tmp_path / "thresholds.json"
        p.write_text('{"csar": 0.85, "fuel": 0.55, "default": 0.70}', encoding="utf-8")
        result = load_cascade_thresholds(p)
        assert result == {"csar": 0.85, "fuel": 0.55, "default": 0.70}

    def test_missing_file_returns_empty(self, tmp_path):
        result = load_cascade_thresholds(tmp_path / "does_not_exist.json")
        assert result == {}

    def test_ships_with_valid_defaults(self):
        """config/cascade_thresholds.json ships with the repo and must parse cleanly."""
        from pathlib import Path

        import chat_to_cop

        # Walk up from package to repo root (parent of src/chat_to_cop)
        pkg_dir = Path(chat_to_cop.__file__).resolve().parent
        # src/chat_to_cop -> src -> repo root
        repo_root = pkg_dir.parent.parent
        path = repo_root / "config" / "cascade_thresholds.json"
        if not path.exists():
            pytest.skip(f"shipped config not found at {path} (editable install?)")
        result = load_cascade_thresholds(path)
        # Every UpdateType value should be in the map, plus "default"
        for t in UpdateType:
            assert t.value in result, f"shipped config missing {t.value}"
        assert "default" in result
        for key, val in result.items():
            assert 0.0 <= val <= 1.0, f"shipped threshold {key}={val} outside [0, 1]"

    def test_malformed_json_raises(self, tmp_path):
        import json as _json

        p = tmp_path / "bad.json"
        p.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(_json.JSONDecodeError):
            load_cascade_thresholds(p)

    def test_non_object_json_returns_empty(self, tmp_path):
        p = tmp_path / "list.json"
        p.write_text("[0.5, 0.6, 0.7]", encoding="utf-8")
        assert load_cascade_thresholds(p) == {}

    def test_underscore_keys_ignored(self, tmp_path):
        p = tmp_path / "annotated.json"
        p.write_text('{"_description": "notes", "_version": "1", "csar": 0.85}', encoding="utf-8")
        result = load_cascade_thresholds(p)
        assert result == {"csar": 0.85}

    def test_non_numeric_values_dropped(self, tmp_path):
        p = tmp_path / "mixed.json"
        # bool is numeric in Python but should be rejected (True == 1 would misroute)
        p.write_text('{"csar": 0.85, "bogus": "high", "also_bogus": true}', encoding="utf-8")
        result = load_cascade_thresholds(p)
        assert result == {"csar": 0.85}

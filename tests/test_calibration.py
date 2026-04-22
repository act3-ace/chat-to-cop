"""Tests for confidence calibration module."""

from __future__ import annotations

import json

import pytest

from chat_to_cop.calibration import CalibrationBucket, CalibrationModel


class TestCalibrationBucket:
    def test_accuracy_empty(self):
        b = CalibrationBucket(lower=0.0, upper=0.1)
        assert b.accuracy == 0.0

    def test_accuracy_computed(self):
        b = CalibrationBucket(lower=0.8, upper=0.9, total=10, correct=7)
        assert b.accuracy == pytest.approx(0.7)

    def test_midpoint(self):
        b = CalibrationBucket(lower=0.2, upper=0.3)
        assert b.midpoint == pytest.approx(0.25)

    def test_gap(self):
        # Midpoint = 0.85, accuracy = 0.5 -> gap = 0.35
        b = CalibrationBucket(lower=0.8, upper=0.9, total=10, correct=5)
        assert b.gap == pytest.approx(0.35)


class TestCalibrationModelFit:
    def test_fit_basic(self):
        model = CalibrationModel(n_bins=10)
        # All predictions at 0.9 confidence, only half correct
        confs = [0.9] * 10
        correct = [True, False, True, False, True, False, True, False, True, False]
        model.fit(confs, correct)

        # The 0.9-1.0 bucket should have 50% accuracy
        bucket = model.buckets[9]
        assert bucket.total == 10
        assert bucket.correct == 5
        assert bucket.accuracy == pytest.approx(0.5)

    def test_fit_length_mismatch(self):
        model = CalibrationModel()
        with pytest.raises(ValueError, match="Length mismatch"):
            model.fit([0.5, 0.6], [True])

    def test_fit_resets_buckets(self):
        model = CalibrationModel(n_bins=5)
        model.fit([0.9], [True])
        # Fit again with different data — old data should be gone
        model.fit([0.1], [False])
        # The 0.8-1.0 bucket should be empty now
        bucket_high = model.buckets[4]
        assert bucket_high.total == 0

    def test_fit_empty(self):
        model = CalibrationModel()
        model.fit([], [])
        assert all(b.total == 0 for b in model.buckets)

    def test_fit_multiple_buckets(self):
        model = CalibrationModel(n_bins=10)
        confs = [0.15, 0.15, 0.85, 0.85]
        correct = [True, True, False, False]
        model.fit(confs, correct)

        # Bucket 1 (0.1-0.2): 2 total, 2 correct
        assert model.buckets[1].total == 2
        assert model.buckets[1].correct == 2
        # Bucket 8 (0.8-0.9): 2 total, 0 correct
        assert model.buckets[8].total == 2
        assert model.buckets[8].correct == 0


class TestCalibrationModelCalibrate:
    def test_calibrate_unfitted_returns_raw(self):
        model = CalibrationModel()
        assert model.calibrate(0.75) == 0.75

    def test_calibrate_overconfident(self):
        model = CalibrationModel(n_bins=10)
        # Model always says 0.95, but only 30% correct
        confs = [0.95] * 100
        correct = [i < 30 for i in range(100)]
        model.fit(confs, correct)
        calibrated = model.calibrate(0.95)
        assert calibrated == pytest.approx(0.3)

    def test_calibrate_underconfident(self):
        model = CalibrationModel(n_bins=10)
        # Model says 0.25, but 80% are correct
        confs = [0.25] * 100
        correct = [i < 80 for i in range(100)]
        model.fit(confs, correct)
        calibrated = model.calibrate(0.25)
        assert calibrated == pytest.approx(0.8)

    def test_calibrate_empty_bucket_passthrough(self):
        model = CalibrationModel(n_bins=10)
        # Only fit data in the 0.9 bucket
        model.fit([0.95] * 10, [True] * 10)
        # Bucket 0.5-0.6 has no data, should pass through
        assert model.calibrate(0.55) == 0.55

    def test_calibrate_clamps_input(self):
        model = CalibrationModel(n_bins=10)
        model.fit([0.05] * 5, [True] * 5)
        # Values outside [0,1] should be clamped
        result_low = model.calibrate(-0.5)
        assert 0.0 <= result_low <= 1.0
        result_high = model.calibrate(1.5)
        assert 0.0 <= result_high <= 1.0

    def test_calibrate_edge_value_1(self):
        model = CalibrationModel(n_bins=10)
        model.fit([1.0] * 10, [True] * 10)
        # confidence=1.0 should map to the last bucket
        assert model.calibrate(1.0) == pytest.approx(1.0)

    def test_calibrate_edge_value_0(self):
        model = CalibrationModel(n_bins=10)
        model.fit([0.0] * 10, [False] * 10)
        assert model.calibrate(0.0) == pytest.approx(0.0)


class TestIsotonicRegression:
    def test_isotonic_monotonic(self):
        model = CalibrationModel(n_bins=10, method="isotonic")
        # Create data where raw confidence is weakly correlated with correctness
        confs = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
        correct = [False, False, True, False, True, True, True, True, True]
        model.fit(confs, correct)

        # Verify monotonicity: calibrated values should be non-decreasing
        prev = -1.0
        for c in [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]:
            cal = model.calibrate(c)
            assert cal >= prev - 1e-9, f"Non-monotonic at {c}: {cal} < {prev}"
            prev = cal

    def test_isotonic_perfect_calibration(self):
        model = CalibrationModel(n_bins=10, method="isotonic")
        # Perfect calibration: 0.5 confidence -> 50% correct
        confs = [0.5] * 100
        correct = [i % 2 == 0 for i in range(100)]
        model.fit(confs, correct)
        assert model.calibrate(0.5) == pytest.approx(0.5)

    def test_isotonic_unfitted_passthrough(self):
        model = CalibrationModel(method="isotonic")
        assert model.calibrate(0.7) == 0.7

    def test_isotonic_single_point(self):
        model = CalibrationModel(method="isotonic")
        model.fit([0.8], [True])
        assert model.calibrate(0.8) == pytest.approx(1.0)

    def test_isotonic_interpolation(self):
        model = CalibrationModel(method="isotonic")
        # Two distinct groups: low confidence -> 0% correct, high confidence -> 100% correct
        confs = [0.2, 0.2, 0.2, 0.8, 0.8, 0.8]
        correct = [False, False, False, True, True, True]
        model.fit(confs, correct)
        # Midpoint should interpolate between 0.0 and 1.0
        mid = model.calibrate(0.5)
        assert 0.0 < mid < 1.0


class TestECE:
    def test_ece_perfect(self):
        model = CalibrationModel(n_bins=10)
        # Perfect calibration: bucket midpoint == accuracy
        # Put 10 samples in the 0.5-0.6 bucket, 5 correct (accuracy=0.5, midpoint=0.55)
        # ECE won't be exactly 0 but close for well-calibrated data
        model.fit([0.55] * 100, [i < 55 for i in range(100)])
        assert model.ece() < 0.1

    def test_ece_worst_case(self):
        model = CalibrationModel(n_bins=10)
        # All predictions at 0.95 confidence, but 0% correct
        model.fit([0.95] * 100, [False] * 100)
        # ECE should be close to 0.95 (gap = 0.95 - 0.0 = 0.95)
        assert model.ece() == pytest.approx(0.95, abs=0.05)

    def test_ece_empty(self):
        model = CalibrationModel()
        model.fit([], [])
        assert model.ece() == 0.0


class TestReliabilityDiagram:
    def test_diagram_renders(self):
        model = CalibrationModel(n_bins=10)
        model.fit([0.15, 0.55, 0.95, 0.95], [True, False, True, False])
        diagram = model.reliability_diagram_ascii(width=40)
        assert "Reliability Diagram" in diagram
        assert "ECE" in diagram
        # Should have bucket lines
        assert "0.1-0.2" in diagram
        assert "0.5-0.6" in diagram
        assert "0.9-1.0" in diagram

    def test_diagram_empty_buckets(self):
        model = CalibrationModel(n_bins=10)
        model.fit([], [])
        diagram = model.reliability_diagram_ascii()
        assert "(no data)" in diagram

    def test_diagram_width(self):
        model = CalibrationModel(n_bins=10)
        model.fit([0.5] * 10, [True] * 10)
        diagram_wide = model.reliability_diagram_ascii(width=60)
        diagram_narrow = model.reliability_diagram_ascii(width=20)
        # Wider diagram should have longer lines
        assert len(diagram_wide) > len(diagram_narrow)


class TestPersistence:
    def test_save_and_load(self, tmp_path):
        model = CalibrationModel(n_bins=10)
        model.fit([0.1, 0.5, 0.9, 0.9], [True, False, True, False])

        path = tmp_path / "cal.json"
        model.save(path)

        loaded = CalibrationModel()
        loaded.load(path)

        assert loaded.n_bins == model.n_bins
        assert loaded._fitted is True
        assert len(loaded.buckets) == len(model.buckets)

        # Same calibration values
        for conf in [0.1, 0.5, 0.9]:
            assert loaded.calibrate(conf) == pytest.approx(model.calibrate(conf))

    def test_save_creates_parent_dirs(self, tmp_path):
        model = CalibrationModel()
        model.fit([0.5], [True])
        path = tmp_path / "nested" / "deep" / "cal.json"
        model.save(path)
        assert path.exists()

    def test_from_file(self, tmp_path):
        model = CalibrationModel(n_bins=5)
        model.fit([0.3, 0.7, 0.7], [True, True, False])
        path = tmp_path / "cal.json"
        model.save(path)

        loaded = CalibrationModel.from_file(path)
        assert loaded.n_bins == 5
        assert loaded._fitted is True
        assert loaded.calibrate(0.3) == pytest.approx(model.calibrate(0.3))

    def test_save_json_valid(self, tmp_path):
        model = CalibrationModel(n_bins=10)
        model.fit([0.5] * 5, [True, False, True, False, True])
        path = tmp_path / "cal.json"
        model.save(path)

        # Verify it's valid JSON
        data = json.loads(path.read_text())
        assert data["n_bins"] == 10
        assert data["fitted"] is True
        assert len(data["buckets"]) == 10

    def test_isotonic_persistence(self, tmp_path):
        model = CalibrationModel(n_bins=10, method="isotonic")
        model.fit([0.2, 0.2, 0.8, 0.8], [False, False, True, True])

        path = tmp_path / "iso_cal.json"
        model.save(path)

        loaded = CalibrationModel.from_file(path)
        assert loaded.method == "isotonic"
        assert len(loaded.isotonic_points) > 0
        assert loaded.calibrate(0.5) == pytest.approx(model.calibrate(0.5))

    def test_load_missing_file(self, tmp_path):
        model = CalibrationModel()
        with pytest.raises(FileNotFoundError):
            model.load(tmp_path / "nonexistent.json")


class TestChannelAgentCalibration:
    """Test that the channel agent correctly applies calibration post-extraction."""

    @pytest.fixture()
    def calibration_model(self):
        """A calibration model where 0.9 maps to 0.5."""
        model = CalibrationModel(n_bins=10)
        model.fit([0.9] * 100, [i < 50 for i in range(100)])
        return model

    def test_agent_without_calibration(self):
        """Agent without calibration model should not modify confidence."""
        from unittest.mock import AsyncMock

        from chat_to_cop.agent.channel_agent import ChannelAgent

        backend = AsyncMock()
        agent = ChannelAgent(channel="#test", backend=backend, calibration_model=None)
        assert agent.calibration_model is None

    def test_agent_with_calibration(self, calibration_model):
        """Agent with calibration model should have it set."""
        from unittest.mock import AsyncMock

        from chat_to_cop.agent.channel_agent import ChannelAgent

        backend = AsyncMock()
        agent = ChannelAgent(channel="#test", backend=backend, calibration_model=calibration_model)
        assert agent.calibration_model is not None
        # Verify the calibration model works
        assert agent.calibration_model.calibrate(0.9) == pytest.approx(0.5)


class TestCalibrationPlumbing:
    """Verify calibration actually changes process_message output (same !88 bug class).

    The existing TestChannelAgentCalibration tests only check attribute presence.
    These tests assert that calibration changes the *output confidence* of a
    full process_message call, catching the case where the parameter is accepted
    but silently ignored.
    """

    @staticmethod
    def _make_message(content: str = "RR15 F+40"):
        from datetime import datetime, timezone

        from chat_to_cop.models.messages import IRCMessage

        return IRCMessage(
            timestamp=datetime(2025, 9, 23, 14, 10, 0, tzinfo=timezone.utc),
            channel="#c2_coord",
            sender="Hydro_Tank",
            content=content,
        )

    @staticmethod
    def _make_backend(confidence: float = 0.85):
        """Build a FakeBackend matching test_channel_agent's pattern."""
        from datetime import datetime, timezone

        from pydantic import BaseModel

        from chat_to_cop.models.cop_update import CoPUpdate, EntityUpdate, UpdateType

        class _Backend:
            async def extract(self, messages: list[dict], schema: type[BaseModel]) -> CoPUpdate:
                return CoPUpdate(
                    update_type=UpdateType.FUEL,
                    confidence=confidence,
                    extraction_method="llm",
                    entities=[EntityUpdate(callsign="RR15", fuel_state="F+40")],
                    source_channel="",
                    source_speaker="",
                    source_message="",
                    timestamp=datetime(2025, 9, 23, 14, 0, 0, tzinfo=timezone.utc),
                )

        return _Backend()

    @staticmethod
    def _make_calibration_model() -> CalibrationModel:
        """A model where the 0.8-0.9 bucket has 50% accuracy (0.85 -> 0.5)."""
        model = CalibrationModel(n_bins=10)
        model.fit([0.85] * 100, [i < 50 for i in range(100)])
        return model

    def test_calibration_model_applied_in_process_message(self):
        """Confidence WITH calibration must differ from confidence WITHOUT."""
        import asyncio

        from chat_to_cop.agent.channel_agent import ChannelAgent

        cal = self._make_calibration_model()
        msg = self._make_message()

        agent_with = ChannelAgent(channel="#test", backend=self._make_backend(), calibration_model=cal)
        agent_without = ChannelAgent(channel="#test", backend=self._make_backend())

        async def _run():
            result_with = await agent_with.process_message(msg)
            result_without = await agent_without.process_message(msg)
            return result_with, result_without

        result_with, result_without = asyncio.run(_run())

        assert len(result_with) == 1
        assert len(result_without) == 1
        # FakeBackend returns 0.85; calibration maps it to 0.5
        assert result_with[0].confidence == pytest.approx(0.5)
        assert result_without[0].confidence == pytest.approx(0.85)
        assert result_with[0].confidence != result_without[0].confidence

    def test_calibration_flows_through_supervisor(self):
        """Supervisor must pass calibration_model to ChannelAgent."""
        import asyncio

        from chat_to_cop.agent.supervisor import Supervisor

        cal = self._make_calibration_model()
        backend_factory = lambda: self._make_backend()  # noqa: E731
        sup = Supervisor(backend_factory=backend_factory, calibration_model=cal)
        agent = sup.start_agent("#test")

        # Verify attribute was forwarded
        assert agent.calibration_model is not None
        assert agent.calibration_model.calibrate(0.85) == pytest.approx(0.5)

        # Verify the full route_message path applies it
        msg = self._make_message()

        async def _run():
            return await sup.route_message(msg)

        updates = asyncio.run(_run())
        assert len(updates) == 1
        assert updates[0].confidence == pytest.approx(0.5)

    def test_supervisor_without_calibration_leaves_confidence_raw(self):
        """Supervisor with no calibration_model must leave confidence unchanged."""
        import asyncio

        from chat_to_cop.agent.supervisor import Supervisor

        backend_factory = lambda: self._make_backend()  # noqa: E731
        sup = Supervisor(backend_factory=backend_factory, calibration_model=None)
        agent = sup.start_agent("#test")

        assert agent.calibration_model is None

        msg = self._make_message()

        async def _run():
            return await sup.route_message(msg)

        updates = asyncio.run(_run())
        assert len(updates) == 1
        assert updates[0].confidence == pytest.approx(0.85)


class TestConfigCalibrationModel:
    def test_default_empty(self):
        from chat_to_cop.config import PipelineConfig

        cfg = PipelineConfig()
        assert cfg.calibration_model == ""

    def test_env_override(self, monkeypatch):
        from chat_to_cop.config import PipelineConfig

        monkeypatch.setenv("CHAT_TO_COP_CALIBRATION_MODEL", "/path/to/calibration.json")
        cfg = PipelineConfig()
        assert cfg.calibration_model == "/path/to/calibration.json"


class TestNBins:
    def test_custom_bins(self):
        model = CalibrationModel(n_bins=5)
        assert len(model.buckets) == 5
        assert model.buckets[0].lower == pytest.approx(0.0)
        assert model.buckets[0].upper == pytest.approx(0.2)
        assert model.buckets[4].lower == pytest.approx(0.8)
        assert model.buckets[4].upper == pytest.approx(1.0)

    def test_fit_with_custom_bins(self):
        model = CalibrationModel(n_bins=5)
        model.fit([0.1, 0.3, 0.5, 0.7, 0.9], [True, True, False, False, True])
        assert model.buckets[0].total == 1  # 0.0-0.2
        assert model.buckets[1].total == 1  # 0.2-0.4
        assert model.buckets[2].total == 1  # 0.4-0.6
        assert model.buckets[3].total == 1  # 0.6-0.8
        assert model.buckets[4].total == 1  # 0.8-1.0


class TestCalibrationEndToEnd:
    """Integration-style tests that exercise the full fit -> calibrate -> persist cycle."""

    def test_overconfident_model_scenario(self, tmp_path):
        """Simulate a model that always outputs 0.9 but is only 60% accurate."""
        model = CalibrationModel(n_bins=10)

        # 200 predictions, all at 0.9, 60% correct
        n = 200
        confs = [0.9] * n
        correct = [i < 120 for i in range(n)]

        model.fit(confs, correct)

        # Raw 0.9 should calibrate to ~0.6
        assert model.calibrate(0.9) == pytest.approx(0.6)

        # ECE should reflect the 0.3 gap (0.95 midpoint - 0.6 accuracy = 0.35)
        assert model.ece() > 0.3

        # Save and reload
        path = tmp_path / "overconfident.json"
        model.save(path)
        reloaded = CalibrationModel.from_file(path)
        assert reloaded.calibrate(0.9) == pytest.approx(0.6)

    def test_well_calibrated_model_scenario(self):
        """A model whose confidence already matches accuracy should have low ECE."""
        model = CalibrationModel(n_bins=10)
        import random

        random.seed(42)

        confs = []
        correct = []
        for _ in range(1000):
            c = random.random()
            confs.append(c)
            correct.append(random.random() < c)

        model.fit(confs, correct)
        # Well-calibrated model should have ECE < 0.1
        assert model.ece() < 0.1

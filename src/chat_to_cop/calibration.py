"""Confidence calibration: adjust raw model confidence to reflect actual accuracy.

Models tend to output overconfident values (e.g., 0.90 for everything).
This module provides histogram-binning calibration: learn the mapping from
raw confidence buckets to observed accuracy, then apply it to new predictions.

Two approaches implemented:
1. Histogram binning — simple, robust, good for prototyping.
2. Isotonic regression — monotonic, better with more data.

Usage:
    model = CalibrationModel()
    model.fit(confidences=[0.9, 0.9, 0.8], correct=[True, False, True])
    calibrated = model.calibrate(0.9)  # -> 0.5 (only half of 0.9s were correct)
    model.save("calibration.json")
    model.load("calibration.json")
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CalibrationBucket:
    """A single confidence bucket for histogram binning."""

    lower: float
    upper: float
    total: int = 0
    correct: int = 0

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total > 0 else 0.0

    @property
    def midpoint(self) -> float:
        return (self.lower + self.upper) / 2.0

    @property
    def gap(self) -> float:
        """Absolute difference between midpoint confidence and observed accuracy."""
        return abs(self.midpoint - self.accuracy)


@dataclass
class CalibrationModel:
    """Histogram-binning confidence calibration.

    Divides the [0, 1] confidence range into equal-width buckets and maps
    each bucket's midpoint to the observed accuracy within that bucket.

    For buckets with no training data, falls back to the raw confidence
    (identity mapping).
    """

    n_bins: int = 10
    method: str = "histogram"
    buckets: list[CalibrationBucket] = field(default_factory=list)
    # Isotonic regression points (confidence -> calibrated), sorted by confidence
    isotonic_points: list[tuple[float, float]] = field(default_factory=list)
    _fitted: bool = False

    def __post_init__(self):
        if not self.buckets:
            self.buckets = self._make_buckets(self.n_bins)

    @staticmethod
    def _make_buckets(n_bins: int) -> list[CalibrationBucket]:
        width = 1.0 / n_bins
        return [CalibrationBucket(lower=i * width, upper=(i + 1) * width) for i in range(n_bins)]

    def _bucket_for(self, confidence: float) -> CalibrationBucket:
        """Find the bucket containing a given confidence value."""
        idx = int(confidence * self.n_bins)
        # Clamp: confidence=1.0 -> last bucket
        idx = min(idx, self.n_bins - 1)
        idx = max(idx, 0)
        return self.buckets[idx]

    def fit(self, confidences: list[float], correct: list[bool]) -> None:
        """Learn the calibration mapping from paired confidence/outcome data.

        Args:
            confidences: Raw confidence values from the model (0.0 to 1.0).
            correct: Whether each prediction was actually correct.
        """
        if len(confidences) != len(correct):
            raise ValueError(f"Length mismatch: {len(confidences)} confidences vs {len(correct)} outcomes")

        # Reset buckets
        self.buckets = self._make_buckets(self.n_bins)

        for conf, is_correct in zip(confidences, correct):
            bucket = self._bucket_for(conf)
            bucket.total += 1
            if is_correct:
                bucket.correct += 1

        if self.method == "isotonic":
            self._fit_isotonic(confidences, correct)

        self._fitted = True

    def _fit_isotonic(self, confidences: list[float], correct: list[bool]) -> None:
        """Fit isotonic regression (Pool Adjacent Violators Algorithm).

        Produces a monotonically non-decreasing mapping from raw confidence
        to calibrated confidence. Implemented manually — no sklearn needed.
        """
        if not confidences:
            self.isotonic_points = []
            return

        # Sort by confidence
        pairs = sorted(zip(confidences, [float(c) for c in correct]))

        # Pool Adjacent Violators: merge adjacent blocks that violate monotonicity
        blocks: list[tuple[float, float, int]] = []  # (sum_conf, sum_correct, count)
        for conf, outcome in pairs:
            blocks.append((conf, outcome, 1))
            # Merge backward while the last block's average exceeds the new one
            while len(blocks) >= 2:
                s_conf1, s_corr1, n1 = blocks[-2]
                s_conf2, s_corr2, n2 = blocks[-1]
                avg1 = s_corr1 / n1
                avg2 = s_corr2 / n2
                if avg1 > avg2:
                    # Merge: pool the two blocks
                    blocks.pop()
                    blocks.pop()
                    blocks.append((s_conf1 + s_conf2, s_corr1 + s_corr2, n1 + n2))
                else:
                    break

        # Convert blocks to (avg_confidence, sum_correct, count) triples
        raw_points: list[tuple[float, float, int]] = []
        idx = 0
        for s_conf, s_corr, count in blocks:
            avg_conf = 0.0
            for c, _ in pairs[idx : idx + count]:
                avg_conf += c
            avg_conf /= count
            raw_points.append((avg_conf, s_corr, count))
            idx += count

        # Merge consecutive points with the same x-coordinate (degenerate case:
        # all inputs at the same confidence). Weight by sample count.
        merged: list[tuple[float, float, int]] = []
        for x, s_corr, n in raw_points:
            if merged and abs(merged[-1][0] - x) < 1e-9:
                px, ps, pn = merged[-1]
                merged[-1] = (px, ps + s_corr, pn + n)
            else:
                merged.append((x, s_corr, n))

        self.isotonic_points = [(x, s / n) for x, s, n in merged]

    def calibrate(self, raw_confidence: float) -> float:
        """Map a raw confidence value to a calibrated confidence.

        If the model hasn't been fitted, returns the raw value unchanged.
        For histogram method: returns the observed accuracy of the matching bucket.
        For isotonic method: interpolates between isotonic regression points.
        """
        if not self._fitted:
            return raw_confidence

        raw_confidence = max(0.0, min(1.0, raw_confidence))

        if self.method == "isotonic" and self.isotonic_points:
            return self._interpolate_isotonic(raw_confidence)

        # Histogram binning
        bucket = self._bucket_for(raw_confidence)
        if bucket.total == 0:
            return raw_confidence  # No data for this bucket, pass through
        return bucket.accuracy

    def _interpolate_isotonic(self, raw_confidence: float) -> float:
        """Linear interpolation between isotonic regression points."""
        points = self.isotonic_points
        if not points:
            return raw_confidence
        if len(points) == 1:
            return points[0][1]

        # Below minimum or above maximum: clamp to endpoint
        if raw_confidence <= points[0][0]:
            return points[0][1]
        if raw_confidence >= points[-1][0]:
            return points[-1][1]

        # Find bracketing points and interpolate
        for i in range(len(points) - 1):
            x0, y0 = points[i]
            x1, y1 = points[i + 1]
            if x0 <= raw_confidence <= x1:
                if x1 == x0:
                    return y0
                t = (raw_confidence - x0) / (x1 - x0)
                return y0 + t * (y1 - y0)

        return raw_confidence  # Fallback

    def ece(self) -> float:
        """Expected Calibration Error: weighted average of per-bucket gaps.

        Lower is better. 0.0 = perfectly calibrated.
        """
        total_samples = sum(b.total for b in self.buckets)
        if total_samples == 0:
            return 0.0
        return sum(b.total * b.gap for b in self.buckets) / total_samples

    def reliability_diagram_ascii(self, width: int = 50) -> str:
        """Render an ASCII reliability diagram.

        Shows per-bucket: expected confidence (midpoint) vs observed accuracy,
        with a bar chart. The diagonal (perfect calibration) is shown for reference.
        """
        lines = []
        lines.append("Reliability Diagram (confidence vs accuracy)")
        lines.append(f"{'Bucket':>10s} {'Cnt':>5s} {'Conf':>5s} {'Acc':>5s} {'Gap':>5s}  Bar")
        lines.append("-" * (38 + width))

        for bucket in self.buckets:
            label = f"{bucket.lower:.1f}-{bucket.upper:.1f}"
            cnt = str(bucket.total)
            conf = f"{bucket.midpoint:.2f}"

            if bucket.total == 0:
                lines.append(f"{label:>10s} {cnt:>5s} {conf:>5s} {'n/a':>5s} {'n/a':>5s}  (no data)")
                continue

            acc = f"{bucket.accuracy:.2f}"
            gap = f"{bucket.gap:.2f}"

            # Bar: filled portion = accuracy, marker for ideal confidence
            bar_len = int(bucket.accuracy * width)
            ideal_pos = int(bucket.midpoint * width)
            bar_chars = list("." * width)
            for j in range(bar_len):
                bar_chars[j] = "#"
            # Mark ideal position with '|' if it's not already filled
            if 0 <= ideal_pos < width:
                bar_chars[ideal_pos] = "|" if ideal_pos >= bar_len else "#"

            bar = "".join(bar_chars)
            lines.append(f"{label:>10s} {cnt:>5s} {conf:>5s} {acc:>5s} {gap:>5s}  {bar}")

        lines.append("")
        lines.append(f"ECE = {self.ece():.4f}  (# = accuracy, | = ideal confidence)")

        return "\n".join(lines)

    def save(self, path: str | Path) -> None:
        """Persist calibration model to JSON."""
        data = {
            "n_bins": self.n_bins,
            "method": self.method,
            "fitted": self._fitted,
            "buckets": [
                {"lower": b.lower, "upper": b.upper, "total": b.total, "correct": b.correct} for b in self.buckets
            ],
            "isotonic_points": self.isotonic_points,
        }
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2))

    def load(self, path: str | Path) -> None:
        """Load a previously saved calibration model from JSON."""
        path = Path(path)
        data = json.loads(path.read_text())
        self.n_bins = data["n_bins"]
        self.method = data.get("method", "histogram")
        self._fitted = data["fitted"]
        self.buckets = [
            CalibrationBucket(lower=b["lower"], upper=b["upper"], total=b["total"], correct=b["correct"])
            for b in data["buckets"]
        ]
        self.isotonic_points = [tuple(p) for p in data.get("isotonic_points", [])]

    @classmethod
    def from_file(cls, path: str | Path) -> CalibrationModel:
        """Load a CalibrationModel from a JSON file."""
        model = cls()
        model.load(path)
        return model

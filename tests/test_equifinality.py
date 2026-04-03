"""Tests for PathEntropyTracker (equifinality measurement)."""

from __future__ import annotations

import math

import pytest

from chat_to_cop.equifinality import PathEntropyTracker


def test_empty_tracker_report():
    """Empty tracker returns empty report."""
    tracker = PathEntropyTracker()
    assert tracker.report() == {}


def test_empty_tracker_entropy():
    """Entropy for unknown type is 0.0."""
    tracker = PathEntropyTracker()
    assert tracker.entropy("fuel") == 0.0


def test_empty_tracker_fragile_types():
    """Empty tracker returns no fragile types."""
    tracker = PathEntropyTracker()
    assert tracker.fragile_types() == []


def test_single_backend_entropy_zero():
    """Single backend -> entropy 0.0 (completely fragile)."""
    tracker = PathEntropyTracker()
    for _ in range(10):
        tracker.record("fuel", "llm")
    assert tracker.entropy("fuel") == 0.0


def test_two_equal_backends_entropy_one():
    """Two backends with equal counts -> entropy 1.0."""
    tracker = PathEntropyTracker()
    for _ in range(50):
        tracker.record("fuel", "llm")
        tracker.record("fuel", "regex")
    assert tracker.entropy("fuel") == pytest.approx(1.0)


def test_three_equal_backends_entropy():
    """Three equal backends -> entropy log2(3) ~ 1.585."""
    tracker = PathEntropyTracker()
    for _ in range(30):
        tracker.record("threat", "llm")
        tracker.record("threat", "regex")
        tracker.record("threat", "passthrough")
    assert tracker.entropy("threat") == pytest.approx(math.log2(3))


def test_unequal_distribution_entropy():
    """Unequal distribution -> entropy between 0 and max."""
    tracker = PathEntropyTracker()
    # 90% llm, 10% regex
    for _ in range(90):
        tracker.record("location", "llm")
    for _ in range(10):
        tracker.record("location", "regex")

    ent = tracker.entropy("location")
    assert 0.0 < ent < 1.0  # Between 0 (single) and 1.0 (two equal)


def test_fragile_types_below_threshold():
    """fragile_types returns types with entropy below threshold."""
    tracker = PathEntropyTracker()
    # fuel: single backend -> entropy 0.0 (fragile)
    for _ in range(10):
        tracker.record("fuel", "llm")
    # threat: two equal backends -> entropy 1.0 (healthy)
    for _ in range(10):
        tracker.record("threat", "llm")
        tracker.record("threat", "regex")

    fragile = tracker.fragile_types(threshold=0.5)
    assert "fuel" in fragile
    assert "threat" not in fragile


def test_fragile_types_custom_threshold():
    """Custom threshold adjusts fragility detection."""
    tracker = PathEntropyTracker()
    # Two equal backends -> entropy 1.0
    for _ in range(10):
        tracker.record("fuel", "llm")
        tracker.record("fuel", "regex")

    # With high threshold, even 1.0 is fragile
    assert "fuel" in tracker.fragile_types(threshold=1.5)
    # With low threshold, 1.0 is healthy
    assert "fuel" not in tracker.fragile_types(threshold=0.5)


def test_report_multiple_types():
    """Report covers all observed update types."""
    tracker = PathEntropyTracker()
    tracker.record("fuel", "llm")
    tracker.record("threat", "llm")
    tracker.record("threat", "regex")
    tracker.record("location", "llm")

    report = tracker.report()
    assert set(report.keys()) == {"fuel", "location", "threat"}
    assert report["fuel"] == 0.0  # Single backend
    assert report["location"] == 0.0  # Single backend
    assert report["threat"] == pytest.approx(1.0)  # Two equal backends


def test_path_counts():
    """path_counts returns per-type backend breakdown."""
    tracker = PathEntropyTracker()
    tracker.record("fuel", "llm")
    tracker.record("fuel", "llm")
    tracker.record("fuel", "regex")
    tracker.record("threat", "llm")

    counts = tracker.path_counts()
    assert counts["fuel"] == {"llm": 2, "regex": 1}
    assert counts["threat"] == {"llm": 1}


def test_independent_types():
    """Different update types tracked independently."""
    tracker = PathEntropyTracker()
    # fuel: only llm
    for _ in range(100):
        tracker.record("fuel", "llm")
    # threat: llm + regex equally
    for _ in range(50):
        tracker.record("threat", "llm")
        tracker.record("threat", "regex")

    assert tracker.entropy("fuel") == 0.0
    assert tracker.entropy("threat") == pytest.approx(1.0)

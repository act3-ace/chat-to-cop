"""Tests for scripts/analyze_labels.py — label analysis and comparison."""

from __future__ import annotations

# Import the module under test by manipulating sys.path
import json
import sys
from pathlib import Path

# Add scripts/ to path so we can import analyze_labels
_scripts_dir = str(Path(__file__).resolve().parent.parent / "scripts")
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

import analyze_labels  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_label(
    raw_line: str = "[14:03:36] HYDRO_SL: SITREP",
    channel: str = "#c2_coord",
    extracted_type: str = "sitrep",
    confidence: float = 0.85,
    extraction_method: str = "llm",
    **overrides,
) -> dict:
    label = {
        "raw_line": raw_line,
        "channel": channel,
        "sender": "HYDRO_SL",
        "timestamp": "2025-09-23T14:03:36+00:00",
        "extracted_type": extracted_type,
        "extracted_entities": [],
        "confidence": confidence,
        "extraction_method": extraction_method,
        "model": "test-model",
        "source": "llm_judge",
        "labeler": "test",
    }
    label.update(overrides)
    return label


SAMPLE_LABELS = [
    _make_label(raw_line="[14:00:00] A: tasking msg", extracted_type="tasking", confidence=0.9, channel="#c2_coord"),
    _make_label(raw_line="[14:01:00] B: threat msg", extracted_type="threat", confidence=0.8, channel="#c2_coord"),
    _make_label(raw_line="[14:02:00] C: noise", extracted_type="none", confidence=0.0, channel="#fires"),
    _make_label(raw_line="[14:03:00] D: another noise", extracted_type="none", confidence=0.0, channel="#fires"),
    _make_label(raw_line="[14:04:00] E: status", extracted_type="status_change", confidence=0.7, channel="#fires"),
    _make_label(
        raw_line="[14:05:00] F: noise filtered",
        extracted_type="none",
        confidence=0.0,
        extraction_method="noise_filter",
        channel="#c2_coord",
    ),
]


# ---------------------------------------------------------------------------
# get_type
# ---------------------------------------------------------------------------


class TestGetType:
    def test_extracted_type(self):
        assert analyze_labels.get_type({"extracted_type": "tasking"}) == "tasking"

    def test_expected_type_preferred(self):
        assert analyze_labels.get_type({"expected_type": "threat", "extracted_type": "tasking"}) == "threat"

    def test_fallback_to_unknown(self):
        assert analyze_labels.get_type({}) == "unknown"

    def test_none_type_value(self):
        assert analyze_labels.get_type({"extracted_type": "none"}) == "none"


# ---------------------------------------------------------------------------
# type_distribution
# ---------------------------------------------------------------------------


class TestTypeDistribution:
    def test_counts(self):
        dist = analyze_labels.type_distribution(SAMPLE_LABELS)
        assert dist["none"] == 3
        assert dist["tasking"] == 1
        assert dist["threat"] == 1
        assert dist["status_change"] == 1

    def test_empty(self):
        dist = analyze_labels.type_distribution([])
        assert len(dist) == 0


# ---------------------------------------------------------------------------
# confidence_distribution
# ---------------------------------------------------------------------------


class TestConfidenceDistribution:
    def test_bucket_count(self):
        hist = analyze_labels.confidence_distribution(SAMPLE_LABELS, n_bins=10)
        assert len(hist) == 10

    def test_all_labels_accounted(self):
        hist = analyze_labels.confidence_distribution(SAMPLE_LABELS, n_bins=10)
        total = sum(count for _, count in hist)
        assert total == len(SAMPLE_LABELS)

    def test_zero_confidence_in_first_bucket(self):
        labels = [_make_label(confidence=0.0)]
        hist = analyze_labels.confidence_distribution(labels, n_bins=10)
        assert hist[0][1] == 1

    def test_high_confidence_in_last_bucket(self):
        labels = [_make_label(confidence=0.95)]
        hist = analyze_labels.confidence_distribution(labels, n_bins=10)
        assert hist[9][1] == 1

    def test_confidence_1_0_goes_to_last_bucket(self):
        labels = [_make_label(confidence=1.0)]
        hist = analyze_labels.confidence_distribution(labels, n_bins=10)
        assert hist[9][1] == 1

    def test_none_confidence_treated_as_zero(self):
        labels = [_make_label(confidence=None)]
        hist = analyze_labels.confidence_distribution(labels, n_bins=10)
        assert hist[0][1] == 1


# ---------------------------------------------------------------------------
# channel_breakdown
# ---------------------------------------------------------------------------


class TestChannelBreakdown:
    def test_channels_present(self):
        result = analyze_labels.channel_breakdown(SAMPLE_LABELS)
        assert "#c2_coord" in result
        assert "#fires" in result

    def test_type_counts_per_channel(self):
        result = analyze_labels.channel_breakdown(SAMPLE_LABELS)
        assert result["#c2_coord"]["tasking"] == 1
        assert result["#c2_coord"]["threat"] == 1
        assert result["#fires"]["none"] == 2
        assert result["#fires"]["status_change"] == 1


# ---------------------------------------------------------------------------
# method_breakdown
# ---------------------------------------------------------------------------


class TestMethodBreakdown:
    def test_method_counts(self):
        result = analyze_labels.method_breakdown(SAMPLE_LABELS)
        assert result["llm"] == 5
        assert result["noise_filter"] == 1


# ---------------------------------------------------------------------------
# compare_labels
# ---------------------------------------------------------------------------


class TestCompareLabels:
    def test_identical_sets_agree(self):
        result = analyze_labels.compare_labels(SAMPLE_LABELS, SAMPLE_LABELS)
        assert result["matched"] == len(SAMPLE_LABELS)
        assert result["agreed"] == len(SAMPLE_LABELS)
        assert result["disagreed"] == 0
        assert result["agreement_rate"] == 1.0

    def test_no_overlap(self):
        labels_a = [_make_label(raw_line="[14:00:00] A: msg")]
        labels_b = [_make_label(raw_line="[14:00:00] B: different")]
        result = analyze_labels.compare_labels(labels_a, labels_b)
        assert result["matched"] == 0
        assert result["only_a"] == 1
        assert result["only_b"] == 1
        assert result["agreement_rate"] == 0.0

    def test_disagreement_tracked(self):
        labels_a = [_make_label(raw_line="[14:00:00] A: msg", extracted_type="tasking")]
        labels_b = [_make_label(raw_line="[14:00:00] A: msg", extracted_type="threat")]
        result = analyze_labels.compare_labels(labels_a, labels_b)
        assert result["matched"] == 1
        assert result["disagreed"] == 1
        assert result["agreed"] == 0
        assert ("tasking", "threat") in result["confusion"]

    def test_partial_overlap(self):
        labels_a = [
            _make_label(raw_line="[14:00:00] A: shared", extracted_type="tasking"),
            _make_label(raw_line="[14:00:00] B: only_a", extracted_type="threat"),
        ]
        labels_b = [
            _make_label(raw_line="[14:00:00] A: shared", extracted_type="tasking"),
            _make_label(raw_line="[14:00:00] C: only_b", extracted_type="none"),
        ]
        result = analyze_labels.compare_labels(labels_a, labels_b)
        assert result["matched"] == 1
        assert result["agreed"] == 1
        assert result["only_a"] == 1
        assert result["only_b"] == 1

    def test_empty_sets(self):
        result = analyze_labels.compare_labels([], [])
        assert result["matched"] == 0
        assert result["agreement_rate"] == 0.0


# ---------------------------------------------------------------------------
# format_report
# ---------------------------------------------------------------------------


class TestFormatReport:
    def test_contains_key_sections(self):
        report = analyze_labels.format_report(SAMPLE_LABELS, name="Test")
        assert "Test Analysis" in report
        assert "Type Distribution" in report
        assert "Confidence Distribution" in report
        assert "Extraction Method" in report
        assert "Per-Channel Breakdown" in report

    def test_informative_count(self):
        report = analyze_labels.format_report(SAMPLE_LABELS, name="Test")
        # 3 informative out of 6
        assert "3/6" in report

    def test_empty_labels(self):
        report = analyze_labels.format_report([], name="Empty")
        assert "0 labels" in report
        assert "(no labels)" in report


# ---------------------------------------------------------------------------
# format_comparison
# ---------------------------------------------------------------------------


class TestFormatComparison:
    def test_contains_names(self):
        result = analyze_labels.compare_labels(SAMPLE_LABELS, SAMPLE_LABELS)
        text = analyze_labels.format_comparison(result, "A", "B")
        assert "A vs B" in text
        assert "Agreed" in text

    def test_shows_disagreements(self):
        labels_a = [_make_label(raw_line="[14:00:00] A: msg", extracted_type="tasking")]
        labels_b = [_make_label(raw_line="[14:00:00] A: msg", extracted_type="threat")]
        result = analyze_labels.compare_labels(labels_a, labels_b)
        text = analyze_labels.format_comparison(result, "Opus", "Gemini")
        assert "Type Disagreements" in text
        assert "tasking" in text
        assert "threat" in text


# ---------------------------------------------------------------------------
# load_jsonl
# ---------------------------------------------------------------------------


class TestLoadJsonl:
    def test_load_valid_file(self, tmp_path: Path):
        fpath = tmp_path / "test.jsonl"
        labels = [_make_label(raw_line=f"[14:00:0{i}] X: msg{i}") for i in range(3)]
        fpath.write_text("\n".join(json.dumps(lb) for lb in labels), encoding="utf-8")
        loaded = analyze_labels.load_jsonl(fpath)
        assert len(loaded) == 3

    def test_skips_invalid_json(self, tmp_path: Path):
        fpath = tmp_path / "test.jsonl"
        fpath.write_text('{"valid": true}\nnot json\n{"also": "valid"}\n', encoding="utf-8")
        loaded = analyze_labels.load_jsonl(fpath)
        assert len(loaded) == 2

    def test_skips_blank_lines(self, tmp_path: Path):
        fpath = tmp_path / "test.jsonl"
        fpath.write_text('{"a": 1}\n\n{"b": 2}\n', encoding="utf-8")
        loaded = analyze_labels.load_jsonl(fpath)
        assert len(loaded) == 2


class TestLoadFromPath:
    def test_load_directory(self, tmp_path: Path):
        for name in ["a.jsonl", "b.jsonl"]:
            (tmp_path / name).write_text(json.dumps(_make_label()) + "\n", encoding="utf-8")
        loaded = analyze_labels.load_from_path(tmp_path)
        assert len(loaded) == 2

    def test_load_single_file(self, tmp_path: Path):
        fpath = tmp_path / "test.jsonl"
        fpath.write_text(json.dumps(_make_label()) + "\n", encoding="utf-8")
        loaded = analyze_labels.load_from_path(fpath)
        assert len(loaded) == 1

    def test_nonexistent_path(self, tmp_path: Path):
        loaded = analyze_labels.load_from_path(tmp_path / "nonexistent")
        assert loaded == []

"""Tests for the speaker model evaluation metrics (scripts/eval_speaker_models.py)."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from chat_to_cop.models.cop_update import CoPUpdate, EntityUpdate, UpdateType
from scripts.eval_speaker_models import (
    RunMetrics,
    _make_composite_key,
    entity_keys_from_cop_update,
    entity_keys_from_dicts,
    format_report,
    jaccard_similarity,
    load_labels,
    normalize_entity_key,
    pearson_correlation,
    score_run,
    types_match_relaxed,
)

# ---------------------------------------------------------------------------
# type match tests
# ---------------------------------------------------------------------------


class TestTypesMatchRelaxed:
    def test_exact_match(self):
        assert types_match_relaxed("threat", "threat") is True

    def test_relaxed_match_status_sitrep(self):
        assert types_match_relaxed("status_change", "sitrep") is True
        assert types_match_relaxed("sitrep", "status_change") is True

    def test_no_match(self):
        assert types_match_relaxed("threat", "location") is False

    def test_none_match(self):
        assert types_match_relaxed("none", "none") is True

    def test_none_vs_other(self):
        assert types_match_relaxed("none", "threat") is False


# ---------------------------------------------------------------------------
# entity key normalization
# ---------------------------------------------------------------------------


class TestNormalizeEntityKey:
    def test_uppercase(self):
        assert normalize_entity_key("orca01") == "ORCA01"

    def test_strip_spaces_dashes(self):
        assert normalize_entity_key("TM-636") == "TM636"
        assert normalize_entity_key("TM 636") == "TM636"
        assert normalize_entity_key("TM_636") == "TM636"

    def test_whitespace(self):
        assert normalize_entity_key("  ZEUS12  ") == "ZEUS12"


class TestEntityKeysFromDicts:
    def test_extracts_track_and_callsign(self):
        entities = [
            {"track_number": "TM636", "callsign": "ORCA01"},
            {"callsign": "ZEUS12"},
        ]
        keys = entity_keys_from_dicts(entities)
        assert keys == {"TM636", "ORCA01", "ZEUS12"}

    def test_empty_entities(self):
        assert entity_keys_from_dicts([]) == set()

    def test_none_values_skipped(self):
        entities = [{"track_number": None, "callsign": None}]
        assert entity_keys_from_dicts(entities) == set()


class TestEntityKeysFromCoPUpdate:
    def test_extracts_keys(self):
        update = CoPUpdate(
            update_type=UpdateType.THREAT,
            confidence=0.8,
            entities=[
                EntityUpdate(track_number="TM636", callsign="ORCA01"),
                EntityUpdate(callsign="ZEUS12"),
            ],
            timestamp=datetime(2025, 9, 23, tzinfo=timezone.utc),
        )
        keys = entity_keys_from_cop_update(update)
        assert keys == {"TM636", "ORCA01", "ZEUS12"}


# ---------------------------------------------------------------------------
# Jaccard similarity
# ---------------------------------------------------------------------------


class TestJaccardSimilarity:
    def test_identical_sets(self):
        assert jaccard_similarity({"A", "B"}, {"A", "B"}) == 1.0

    def test_disjoint_sets(self):
        assert jaccard_similarity({"A"}, {"B"}) == 0.0

    def test_partial_overlap(self):
        assert jaccard_similarity({"A", "B"}, {"B", "C"}) == pytest.approx(1 / 3)

    def test_both_empty(self):
        assert jaccard_similarity(set(), set()) == 1.0

    def test_one_empty(self):
        assert jaccard_similarity({"A"}, set()) == 0.0


# ---------------------------------------------------------------------------
# Pearson correlation
# ---------------------------------------------------------------------------


class TestPearsonCorrelation:
    def test_perfect_positive(self):
        pairs = [(1.0, 1.0), (2.0, 2.0), (3.0, 3.0)]
        assert pearson_correlation(pairs) == pytest.approx(1.0)

    def test_perfect_negative(self):
        pairs = [(1.0, 3.0), (2.0, 2.0), (3.0, 1.0)]
        assert pearson_correlation(pairs) == pytest.approx(-1.0)

    def test_zero_variance(self):
        pairs = [(1.0, 1.0), (1.0, 1.0), (1.0, 1.0)]
        assert pearson_correlation(pairs) == 0.0

    def test_too_few_pairs(self):
        assert pearson_correlation([(1.0, 2.0)]) == 0.0


# ---------------------------------------------------------------------------
# Label loading
# ---------------------------------------------------------------------------


class TestLoadLabels:
    def test_loads_jsonl(self, tmp_path):
        labels_file = tmp_path / "labels.jsonl"
        labels_file.write_text(
            json.dumps({"raw_line": "line1", "extracted_type": "threat"})
            + "\n"
            + json.dumps({"raw_line": "line2", "extracted_type": "none"})
            + "\n",
            encoding="utf-8",
        )
        labels = load_labels(str(labels_file))
        assert len(labels) == 2
        assert labels[0]["extracted_type"] == "threat"

    def test_skips_empty_lines(self, tmp_path):
        labels_file = tmp_path / "labels.jsonl"
        labels_file.write_text(
            json.dumps({"raw_line": "line1"}) + "\n\n" + json.dumps({"raw_line": "line2"}) + "\n",
            encoding="utf-8",
        )
        labels = load_labels(str(labels_file))
        assert len(labels) == 2

    def test_skips_invalid_json(self, tmp_path):
        labels_file = tmp_path / "labels.jsonl"
        labels_file.write_text(
            json.dumps({"raw_line": "valid"}) + "\n" + "not json\n",
            encoding="utf-8",
        )
        labels = load_labels(str(labels_file))
        assert len(labels) == 1


# ---------------------------------------------------------------------------
# score_run tests
# ---------------------------------------------------------------------------


def _make_cop_update(
    update_type: str = "threat",
    confidence: float = 0.8,
    entities: list[dict] | None = None,
    source_message: str = "",
) -> CoPUpdate:
    """Helper to build a CoPUpdate for testing."""
    ents = []
    for e in entities or []:
        ents.append(EntityUpdate(**e))
    return CoPUpdate(
        update_type=UpdateType(update_type),
        confidence=confidence,
        entities=ents,
        source_message=source_message,
        timestamp=datetime(2025, 9, 23, tzinfo=timezone.utc),
    )


class TestScoreRun:
    """Tests use raw_line format '[HH:MM:SS] #channel sender: content'
    and composite keys (SPEAKER||content) to match the real matching logic."""

    def test_exact_type_match(self):
        labels = [
            {
                "raw_line": "[13:00:00] #vegas_internal alice: threat spotted",
                "extracted_type": "threat",
                "extracted_entities": [],
                "confidence": 0.8,
                "extraction_method": "llm",
                "sender": "alice",
            }
        ]
        index = {_make_composite_key("alice", "threat spotted"): _make_cop_update("threat", 0.8)}
        m = score_run(labels, index, "test")
        assert m.type_exact == 1
        assert m.type_relaxed == 1
        assert m.type_exact_rate == 1.0

    def test_relaxed_match_sitrep_status(self):
        labels = [
            {
                "raw_line": "[13:00:00] #vegas_internal bob: status update",
                "extracted_type": "sitrep",
                "extracted_entities": [],
                "confidence": 0.7,
                "extraction_method": "llm",
                "sender": "bob",
            }
        ]
        index = {_make_composite_key("bob", "status update"): _make_cop_update("status_change", 0.7)}
        m = score_run(labels, index, "test")
        assert m.type_exact == 0
        assert m.type_relaxed == 1
        assert m.type_relaxed_rate == 1.0

    def test_no_match(self):
        labels = [
            {
                "raw_line": "[13:00:00] #vegas_internal alice: wrong type",
                "extracted_type": "threat",
                "extracted_entities": [],
                "confidence": 0.8,
                "extraction_method": "llm",
                "sender": "alice",
            }
        ]
        index = {_make_composite_key("alice", "wrong type"): _make_cop_update("location", 0.5)}
        m = score_run(labels, index, "test")
        assert m.type_exact == 0
        assert m.type_relaxed == 0

    def test_missing_extraction(self):
        labels = [
            {
                "raw_line": "[13:00:00] #vegas_internal alice: no extraction",
                "extracted_type": "threat",
                "extracted_entities": [],
                "confidence": 0.8,
                "extraction_method": "llm",
                "sender": "alice",
            }
        ]
        index: dict[str, CoPUpdate] = {}
        m = score_run(labels, index, "test")
        assert m.matched == 0
        assert m.type_exact == 0

    def test_noise_detection(self):
        labels = [
            {
                "raw_line": "[13:00:00] #vegas_internal alice: just noise",
                "extracted_type": "none",
                "extracted_entities": [],
                "confidence": 0.0,
                "extraction_method": "noise_filter",
                "sender": "alice",
            }
        ]
        # No extraction = correct noise detection
        m = score_run(labels, {}, "test")
        assert m.noise_total == 1
        assert m.noise_correct == 1
        assert m.noise_accuracy == 1.0

    def test_noise_false_positive(self):
        """If the model extracts something for a noise message, it's wrong."""
        labels = [
            {
                "raw_line": "[13:00:00] #vegas_internal alice: just noise",
                "extracted_type": "none",
                "extracted_entities": [],
                "confidence": 0.0,
                "extraction_method": "noise_filter",
                "sender": "alice",
            }
        ]
        index = {_make_composite_key("alice", "just noise"): _make_cop_update("threat", 0.8)}
        m = score_run(labels, index, "test")
        assert m.noise_total == 1
        assert m.noise_correct == 0

    def test_entity_overlap_scoring(self):
        labels = [
            {
                "raw_line": "[13:00:00] #vegas_internal alice: ORCA01 spotted",
                "extracted_type": "threat",
                "extracted_entities": [{"callsign": "ORCA01"}, {"track_number": "TM636"}],
                "confidence": 0.8,
                "extraction_method": "llm",
                "sender": "alice",
            }
        ]
        index = {
            _make_composite_key("alice", "ORCA01 spotted"): _make_cop_update(
                "threat",
                0.8,
                entities=[{"callsign": "ORCA01"}],
            )
        }
        m = score_run(labels, index, "test")
        # Silver: {ORCA01, TM636}, Candidate: {ORCA01}
        # Jaccard = 1/2 = 0.5
        assert m.entity_overlap_avg == pytest.approx(0.5)

    def test_per_speaker_tracking(self):
        labels = [
            {
                "raw_line": f"[13:00:0{i}] #vegas_internal alice: msg{i}",
                "extracted_type": "threat",
                "extracted_entities": [],
                "confidence": 0.8,
                "extraction_method": "llm",
                "sender": "alice",
            }
            for i in range(6)
        ]
        # First 3 correct, last 3 wrong
        index = {}
        for i in range(3):
            index[_make_composite_key("alice", f"msg{i}")] = _make_cop_update("threat", 0.8)
        for i in range(3, 6):
            index[_make_composite_key("alice", f"msg{i}")] = _make_cop_update("location", 0.5)

        m = score_run(labels, index, "test")
        assert len(m.per_speaker_correct["alice"]) == 6
        curves = m.speaker_learning_curves(min_messages=5)
        assert "alice" in curves
        # After 3 messages: 3/3 = 100%, after 6: 3/6 = 50%
        assert curves["alice"][2] == pytest.approx(1.0)
        assert curves["alice"][5] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# RunMetrics properties
# ---------------------------------------------------------------------------


class TestRunMetrics:
    def test_defaults(self):
        m = RunMetrics(name="test")
        assert m.type_exact_rate == 0.0
        assert m.type_relaxed_rate == 0.0
        assert m.entity_overlap_avg == 0.0
        assert m.noise_accuracy == 0.0
        assert m.confidence_corr == 0.0
        assert m.match_rate == 0.0


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------


class TestFormatReport:
    def test_no_runs(self):
        report = format_report(None, None, "labels.jsonl")
        assert "No replay databases provided" in report
        assert "Run the A/B experiment first" in report

    def test_single_run(self):
        m = RunMetrics(name="With Speakers", total=10, matched=8, type_exact=6, type_relaxed=7)
        m.per_type_total["threat"] = 5
        m.per_type_correct["threat"] = 3
        report = format_report(m, None, "labels.jsonl")
        assert "With Speakers" in report
        assert "threat" in report
        assert "Delta" not in report  # no comparison with only one run

    def test_two_runs_delta(self):
        a = RunMetrics(name="With Speakers", total=10, type_exact=7, type_relaxed=8)
        b = RunMetrics(name="Without Speakers", total=10, type_exact=5, type_relaxed=6)
        report = format_report(a, b, "labels.jsonl")
        assert "Delta" in report
        assert "With Speakers" in report
        assert "Without Speakers" in report

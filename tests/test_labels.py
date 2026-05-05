"""Tests for the label manager (src/chat_to_cop/labels.py)."""

from __future__ import annotations

import json
from pathlib import Path

from chat_to_cop.labels import (
    VALID_SOURCES,
    VALID_TYPES,
    label_stats,
    load_labels,
    merge_labels,
    validate_label,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_label(
    raw_line: str = "[14:03:36] HYDRO_SL: SITREP",
    channel: str = "#c2_coord",
    source: str = "human",
    expected_type: str = "sitrep",
    **overrides,
) -> dict:
    """Helper to build a valid label dict."""
    label = {
        "raw_line": raw_line,
        "channel": channel,
        "source": source,
        "expected_type": expected_type,
        "expected_entities": [{"callsign": "ZEUS12", "operational_status": "DESTROYED"}],
        "labeler": "test_user",
        "timestamp": "2026-03-30T03:00:00Z",
    }
    label.update(overrides)
    return label


def _write_jsonl(path: Path, labels: list[dict]) -> None:
    """Write a list of label dicts to a JSONL file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for label in labels:
            f.write(json.dumps(label) + "\n")


# ---------------------------------------------------------------------------
# load_labels
# ---------------------------------------------------------------------------


class TestLoadLabels:
    def test_loads_single_file(self, tmp_path: Path):
        labels_dir = tmp_path / "labels"
        _write_jsonl(
            labels_dir / "gold.jsonl",
            [
                _make_label(raw_line="line1", source="human"),
                _make_label(raw_line="line2", source="human"),
            ],
        )

        result = load_labels(str(labels_dir))
        assert len(result) == 2
        raw_lines = {r["raw_line"] for r in result}
        assert raw_lines == {"line1", "line2"}

    def test_loads_multiple_files(self, tmp_path: Path):
        labels_dir = tmp_path / "labels"
        _write_jsonl(
            labels_dir / "gold.jsonl",
            [
                _make_label(raw_line="line1", source="human"),
            ],
        )
        _write_jsonl(
            labels_dir / "silver.jsonl",
            [
                _make_label(raw_line="line2", source="llm_judge"),
            ],
        )

        result = load_labels(str(labels_dir))
        assert len(result) == 2

    def test_trust_priority_human_wins(self, tmp_path: Path):
        """Same raw_line in gold and silver files: human should win."""
        labels_dir = tmp_path / "labels"
        _write_jsonl(
            labels_dir / "gold.jsonl",
            [
                _make_label(raw_line="same_line", source="human", expected_type="sitrep"),
            ],
        )
        _write_jsonl(
            labels_dir / "silver.jsonl",
            [
                _make_label(raw_line="same_line", source="llm_judge", expected_type="threat"),
            ],
        )

        result = load_labels(str(labels_dir))
        assert len(result) == 1
        assert result[0]["source"] == "human"
        assert result[0]["expected_type"] == "sitrep"

    def test_trust_priority_silver_over_bronze(self, tmp_path: Path):
        """Same raw_line: llm_judge beats synthetic."""
        labels_dir = tmp_path / "labels"
        _write_jsonl(
            labels_dir / "silver.jsonl",
            [
                _make_label(raw_line="same_line", source="llm_judge", expected_type="threat"),
            ],
        )
        _write_jsonl(
            labels_dir / "bronze.jsonl",
            [
                _make_label(raw_line="same_line", source="synthetic", expected_type="none"),
            ],
        )

        result = load_labels(str(labels_dir))
        assert len(result) == 1
        assert result[0]["source"] == "llm_judge"

    def test_trust_priority_three_tiers(self, tmp_path: Path):
        """Same raw_line in all three tiers: human wins."""
        labels_dir = tmp_path / "labels"
        _write_jsonl(
            labels_dir / "a_bronze.jsonl",
            [
                _make_label(raw_line="same_line", source="synthetic", expected_type="none"),
            ],
        )
        _write_jsonl(
            labels_dir / "b_silver.jsonl",
            [
                _make_label(raw_line="same_line", source="llm_judge", expected_type="threat"),
            ],
        )
        _write_jsonl(
            labels_dir / "c_gold.jsonl",
            [
                _make_label(raw_line="same_line", source="human", expected_type="sitrep"),
            ],
        )

        result = load_labels(str(labels_dir))
        assert len(result) == 1
        assert result[0]["source"] == "human"

    def test_empty_directory(self, tmp_path: Path):
        labels_dir = tmp_path / "labels"
        labels_dir.mkdir()
        result = load_labels(str(labels_dir))
        assert result == []

    def test_nonexistent_directory(self, tmp_path: Path):
        result = load_labels(str(tmp_path / "nonexistent"))
        assert result == []

    def test_skips_blank_lines_and_bad_json(self, tmp_path: Path):
        labels_dir = tmp_path / "labels"
        labels_dir.mkdir(parents=True)
        fpath = labels_dir / "mixed.jsonl"
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(json.dumps(_make_label(raw_line="good")) + "\n")
            f.write("\n")
            f.write("NOT JSON\n")
            f.write(json.dumps(_make_label(raw_line="also_good")) + "\n")

        result = load_labels(str(labels_dir))
        assert len(result) == 2

    def test_ignores_non_jsonl_files(self, tmp_path: Path):
        labels_dir = tmp_path / "labels"
        labels_dir.mkdir()
        (labels_dir / "readme.txt").write_text("not a label file")
        (labels_dir / "data.csv").write_text("a,b,c")
        _write_jsonl(labels_dir / "real.jsonl", [_make_label()])

        result = load_labels(str(labels_dir))
        assert len(result) == 1


# ---------------------------------------------------------------------------
# validate_label
# ---------------------------------------------------------------------------


class TestValidateLabel:
    def test_valid_label(self):
        errors = validate_label(_make_label())
        assert errors == []

    def test_missing_raw_line(self):
        label = _make_label()
        del label["raw_line"]
        errors = validate_label(label)
        assert any("raw_line" in e for e in errors)

    def test_missing_channel(self):
        label = _make_label()
        del label["channel"]
        errors = validate_label(label)
        assert any("channel" in e for e in errors)

    def test_missing_source(self):
        label = _make_label()
        del label["source"]
        errors = validate_label(label)
        assert any("source" in e for e in errors)

    def test_invalid_source(self):
        label = _make_label(source="magic_8_ball")
        errors = validate_label(label)
        assert any("invalid source" in e for e in errors)

    def test_invalid_expected_type(self):
        label = _make_label(expected_type="radar_contact")
        errors = validate_label(label)
        assert any("invalid type" in e for e in errors)

    def test_valid_types_cover_enum(self):
        """All UpdateType enum values should be valid."""
        for t in VALID_TYPES:
            label = _make_label(expected_type=t)
            errors = validate_label(label)
            assert errors == [], f"Type '{t}' should be valid but got errors: {errors}"

    def test_all_sources_valid(self):
        for src in VALID_SOURCES:
            label = _make_label(source=src)
            errors = validate_label(label)
            assert errors == [], f"Source '{src}' should be valid"

    def test_extracted_type_validated(self):
        """Labels with extracted_type (silver label format) also validated."""
        label = _make_label()
        del label["expected_type"]
        label["extracted_type"] = "not_a_real_type"
        errors = validate_label(label)
        assert any("invalid type" in e for e in errors)

    def test_label_without_type_is_valid(self):
        """Labels with no expected_type or extracted_type are still valid."""
        label = _make_label()
        del label["expected_type"]
        errors = validate_label(label)
        assert errors == []

    def test_empty_required_field(self):
        """Empty string for a required field should be flagged."""
        label = _make_label(raw_line="")
        errors = validate_label(label)
        assert any("raw_line" in e for e in errors)


# ---------------------------------------------------------------------------
# label_stats
# ---------------------------------------------------------------------------


class TestLabelStats:
    def test_basic_stats(self):
        labels = [
            _make_label(source="human", expected_type="sitrep", channel="#c2"),
            _make_label(source="human", expected_type="threat", channel="#c2"),
            _make_label(source="llm_judge", expected_type="sitrep", channel="#fires"),
        ]
        stats = label_stats(labels)
        assert stats["total"] == 3
        assert stats["by_source"] == {"human": 2, "llm_judge": 1}
        assert stats["by_type"] == {"sitrep": 2, "threat": 1}
        assert stats["by_channel"] == {"#c2": 2, "#fires": 1}

    def test_empty_labels(self):
        stats = label_stats([])
        assert stats["total"] == 0
        assert stats["by_source"] == {}

    def test_falls_back_to_extracted_type(self):
        """When expected_type is missing, use extracted_type."""
        label = {"raw_line": "x", "channel": "#c2", "source": "llm_judge", "extracted_type": "threat"}
        stats = label_stats([label])
        assert stats["by_type"] == {"threat": 1}

    def test_unknown_when_no_type(self):
        """When neither type field is present, count as 'unknown'."""
        label = {"raw_line": "x", "channel": "#c2", "source": "human"}
        stats = label_stats([label])
        assert stats["by_type"] == {"unknown": 1}


# ---------------------------------------------------------------------------
# merge_labels
# ---------------------------------------------------------------------------


class TestMergeLabels:
    def test_merge_into_empty_file(self, tmp_path: Path):
        fpath = tmp_path / "labels.jsonl"
        added = merge_labels(
            fpath,
            [
                _make_label(raw_line="line1"),
                _make_label(raw_line="line2"),
            ],
        )
        assert added == 2
        # Verify written content
        with open(fpath, encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        assert len(lines) == 2

    def test_merge_skips_duplicates(self, tmp_path: Path):
        fpath = tmp_path / "labels.jsonl"
        # Write initial labels
        _write_jsonl(fpath, [_make_label(raw_line="existing")])

        added = merge_labels(
            fpath,
            [
                _make_label(raw_line="existing"),  # duplicate
                _make_label(raw_line="new_one"),
            ],
        )
        assert added == 1

        with open(fpath, encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        assert len(lines) == 2
        raw_lines = {lb["raw_line"] for lb in lines}
        assert raw_lines == {"existing", "new_one"}

    def test_merge_dedup_within_new(self, tmp_path: Path):
        """If new_labels itself contains duplicate raw_lines, only first is written."""
        fpath = tmp_path / "labels.jsonl"
        added = merge_labels(
            fpath,
            [
                _make_label(raw_line="dup"),
                _make_label(raw_line="dup"),
                _make_label(raw_line="unique"),
            ],
        )
        assert added == 2

    def test_merge_creates_parent_dirs(self, tmp_path: Path):
        fpath = tmp_path / "nested" / "dir" / "labels.jsonl"
        added = merge_labels(fpath, [_make_label(raw_line="hello")])
        assert added == 1
        assert fpath.exists()

    def test_merge_returns_zero_for_all_dupes(self, tmp_path: Path):
        fpath = tmp_path / "labels.jsonl"
        _write_jsonl(fpath, [_make_label(raw_line="only")])
        added = merge_labels(fpath, [_make_label(raw_line="only")])
        assert added == 0

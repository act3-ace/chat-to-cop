"""Tests for entity-level scoring in the eval harness."""

import os
import sys
from collections import Counter
from datetime import datetime, timezone

import pytest

from chat_to_cop.models.cop_update import CoPUpdate, EntityUpdate, UpdateType

# Import the scoring functions from scripts/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from eval_models import (
    EvalMetrics,
    _entity_key,
    _field_match,
    _score_entities,
)


def _make_update(
    update_type: UpdateType = UpdateType.FUEL,
    entities: list[EntityUpdate] | None = None,
) -> CoPUpdate:
    return CoPUpdate(
        update_type=update_type,
        confidence=0.9,
        extraction_method="ground_truth",
        entities=entities or [],
        source_channel="#c2_coord",
        source_speaker="test",
        source_message="test",
        timestamp=datetime(2025, 9, 23, 14, 0, 0, tzinfo=timezone.utc),
    )


# --- _entity_key tests ---


class TestEntityKey:
    def test_track_number_preferred(self):
        ent = EntityUpdate(track_number="TM636", callsign="ZEUS11")
        assert _entity_key(ent) == "TM636"

    def test_callsign_fallback(self):
        ent = EntityUpdate(callsign="RR15")
        assert _entity_key(ent) == "RR15"

    def test_normalises_case(self):
        ent = EntityUpdate(callsign="rr15")
        assert _entity_key(ent) == "RR15"

    def test_strips_whitespace(self):
        ent = EntityUpdate(track_number="  TM636  ")
        assert _entity_key(ent) == "TM636"

    def test_none_when_no_identifier(self):
        ent = EntityUpdate(fuel_state="F+40")
        assert _entity_key(ent) is None

    def test_empty_string_treated_as_none(self):
        ent = EntityUpdate(track_number="", callsign="")
        assert _entity_key(ent) is None


# --- _field_match tests ---


class TestFieldMatch:
    def test_string_exact_match(self):
        assert _field_match("RTB", "RTB") is True

    def test_string_case_insensitive(self):
        assert _field_match("RTB", "rtb") is True

    def test_string_whitespace_stripped(self):
        assert _field_match("F+40", " F+40 ") is True

    def test_string_mismatch(self):
        assert _field_match("RTB", "OPERATIONAL") is False

    def test_int_exact_match(self):
        assert _field_match(3, 3) is True

    def test_int_mismatch(self):
        assert _field_match(3, 5) is False

    def test_float_exact_match(self):
        assert _field_match(180.0, 180.0) is True

    def test_float_close_enough(self):
        assert _field_match(180.0, 180.3) is True

    def test_float_too_far(self):
        assert _field_match(180.0, 181.0) is False

    def test_none_expected_always_passes(self):
        assert _field_match(None, "anything") is True

    def test_none_extracted_fails(self):
        assert _field_match("RTB", None) is False

    def test_both_none(self):
        assert _field_match(None, None) is True

    def test_int_vs_float_comparison(self):
        assert _field_match(3, 3.0) is True

    def test_float_vs_int_comparison(self):
        assert _field_match(180.0, 180) is True


# --- _score_entities tests ---


class TestScoreEntities:
    def test_perfect_fuel_match(self):
        """Two tankers with correct callsigns and fuel states."""
        expected = _make_update(
            update_type=UpdateType.FUEL,
            entities=[
                EntityUpdate(callsign="RR15", fuel_state="F+40"),
                EntityUpdate(callsign="RL36", fuel_state="F+20"),
            ],
        )
        got = [
            _make_update(
                update_type=UpdateType.FUEL,
                entities=[
                    EntityUpdate(callsign="RR15", fuel_state="F+40"),
                    EntityUpdate(callsign="RL36", fuel_state="F+20"),
                ],
            )
        ]

        result = _score_entities(expected, got)
        assert result.matched == 2
        assert result.expected_total == 2
        assert result.extracted_total == 2
        assert result.field_correct["fuel_state"] == 2
        assert result.field_total["fuel_state"] == 2

    def test_partial_match_wrong_fuel(self):
        """Correct callsigns but wrong fuel state on one."""
        expected = _make_update(
            update_type=UpdateType.FUEL,
            entities=[
                EntityUpdate(callsign="RR15", fuel_state="F+40"),
                EntityUpdate(callsign="RL36", fuel_state="F+20"),
            ],
        )
        got = [
            _make_update(
                update_type=UpdateType.FUEL,
                entities=[
                    EntityUpdate(callsign="RR15", fuel_state="F+40"),
                    EntityUpdate(callsign="RL36", fuel_state="F+55"),
                ],
            )
        ]

        result = _score_entities(expected, got)
        assert result.matched == 2
        assert result.field_correct["fuel_state"] == 1
        assert result.field_total["fuel_state"] == 2

    def test_missed_entity(self):
        """Model only extracts one of two expected entities."""
        expected = _make_update(
            update_type=UpdateType.FUEL,
            entities=[
                EntityUpdate(callsign="RR15", fuel_state="F+40"),
                EntityUpdate(callsign="RL36", fuel_state="F+20"),
            ],
        )
        got = [
            _make_update(
                update_type=UpdateType.FUEL,
                entities=[
                    EntityUpdate(callsign="RR15", fuel_state="F+40"),
                ],
            )
        ]

        result = _score_entities(expected, got)
        assert result.matched == 1
        assert result.expected_total == 2
        assert result.extracted_total == 1

    def test_extra_entity(self):
        """Model extracts an entity not in ground truth."""
        expected = _make_update(
            update_type=UpdateType.FUEL,
            entities=[
                EntityUpdate(callsign="RR15", fuel_state="F+40"),
            ],
        )
        got = [
            _make_update(
                update_type=UpdateType.FUEL,
                entities=[
                    EntityUpdate(callsign="RR15", fuel_state="F+40"),
                    EntityUpdate(callsign="BOGUS99", fuel_state="F+10"),
                ],
            )
        ]

        result = _score_entities(expected, got)
        assert result.matched == 1
        assert result.expected_total == 1
        assert result.extracted_total == 2

    def test_no_extraction(self):
        """Model produces nothing for a message with expected entities."""
        expected = _make_update(
            update_type=UpdateType.FUEL,
            entities=[
                EntityUpdate(callsign="RR15", fuel_state="F+40"),
            ],
        )
        got = []

        result = _score_entities(expected, got)
        assert result.matched == 0
        assert result.expected_total == 1
        assert result.extracted_total == 0

    def test_noise_message(self):
        """No ground truth (noise) — entities from extraction are just counted."""
        result = _score_entities(None, [])
        assert result.matched == 0
        assert result.expected_total == 0
        assert result.extracted_total == 0

    def test_noise_with_false_extraction(self):
        """Noise message but model extracts something — counted for precision."""
        got = [
            _make_update(
                update_type=UpdateType.FUEL,
                entities=[EntityUpdate(callsign="FAKE01", fuel_state="F+10")],
            )
        ]
        result = _score_entities(None, got)
        assert result.matched == 0
        assert result.expected_total == 0
        assert result.extracted_total == 1

    def test_track_number_matching(self):
        """Entity matched by track_number instead of callsign."""
        expected = _make_update(
            update_type=UpdateType.ENTITY_ID,
            entities=[
                EntityUpdate(track_number="TM636", platform_type="J-10", affiliation="HOSTILE"),
            ],
        )
        got = [
            _make_update(
                update_type=UpdateType.ENTITY_ID,
                entities=[
                    EntityUpdate(track_number="TM636", platform_type="J-10", affiliation="HOSTILE"),
                ],
            )
        ]

        result = _score_entities(expected, got)
        assert result.matched == 1
        assert result.field_correct["platform_type"] == 1
        assert result.field_correct["affiliation"] == 1

    def test_case_insensitive_key_match(self):
        """Keys match even if case differs."""
        expected = _make_update(
            update_type=UpdateType.FUEL,
            entities=[EntityUpdate(callsign="RR15", fuel_state="F+40")],
        )
        got = [
            _make_update(
                update_type=UpdateType.FUEL,
                entities=[EntityUpdate(callsign="rr15", fuel_state="F+40")],
            )
        ]

        result = _score_entities(expected, got)
        assert result.matched == 1

    def test_threat_with_bearing_range(self):
        """Threat entity with bearing and range fields."""
        expected = _make_update(
            update_type=UpdateType.THREAT,
            entities=[
                EntityUpdate(
                    track_number="TM700",
                    affiliation="HOSTILE",
                    bearing=180.0,
                    range_nm=250.0,
                    subsystem_status="DDG active",
                ),
            ],
        )
        got = [
            _make_update(
                update_type=UpdateType.THREAT,
                entities=[
                    EntityUpdate(
                        track_number="TM700",
                        affiliation="HOSTILE",
                        bearing=180.0,
                        range_nm=250.0,
                        subsystem_status="DDG active",
                    ),
                ],
            )
        ]

        result = _score_entities(expected, got)
        assert result.matched == 1
        assert result.field_correct["affiliation"] == 1
        assert result.field_correct["bearing"] == 1
        assert result.field_correct["range_nm"] == 1
        assert result.field_correct["subsystem_status"] == 1

    def test_weapons_fields(self):
        """Weapons entity with weapon_type, qty_launched, qty_remaining."""
        expected = _make_update(
            update_type=UpdateType.WEAPONS,
            entities=[
                EntityUpdate(callsign="ZEUS11", weapon_type="SM6", weapon_qty_launched=3, weapon_qty_remaining=15),
            ],
        )
        got = [
            _make_update(
                update_type=UpdateType.WEAPONS,
                entities=[
                    EntityUpdate(callsign="ZEUS11", weapon_type="SM6", weapon_qty_launched=3, weapon_qty_remaining=15),
                ],
            )
        ]

        result = _score_entities(expected, got)
        assert result.matched == 1
        assert result.field_correct["weapon_type"] == 1
        assert result.field_correct["weapon_qty_launched"] == 1
        assert result.field_correct["weapon_qty_remaining"] == 1

    def test_status_change_entity(self):
        """Status change with operational_status field."""
        expected = _make_update(
            update_type=UpdateType.STATUS_CHANGE,
            entities=[
                EntityUpdate(callsign="ORCA01", operational_status="RTB", subsystem_status="radar inop"),
            ],
        )
        got = [
            _make_update(
                update_type=UpdateType.STATUS_CHANGE,
                entities=[
                    EntityUpdate(callsign="ORCA01", operational_status="RTB", subsystem_status="radar inop"),
                ],
            )
        ]

        result = _score_entities(expected, got)
        assert result.matched == 1
        assert result.field_correct["operational_status"] == 1
        assert result.field_correct["subsystem_status"] == 1

    def test_duplicate_extraction_not_double_counted(self):
        """If model extracts the same entity twice, only count match once."""
        expected = _make_update(
            update_type=UpdateType.FUEL,
            entities=[EntityUpdate(callsign="RR15", fuel_state="F+40")],
        )
        got = [
            _make_update(
                update_type=UpdateType.FUEL,
                entities=[
                    EntityUpdate(callsign="RR15", fuel_state="F+40"),
                    EntityUpdate(callsign="RR15", fuel_state="F+40"),
                ],
            )
        ]

        result = _score_entities(expected, got)
        assert result.matched == 1
        assert result.extracted_total == 2  # Both counted for precision denominator

    def test_only_scored_fields_with_expected_values(self):
        """Fields that are None in ground truth should not be scored."""
        expected = _make_update(
            update_type=UpdateType.FUEL,
            entities=[EntityUpdate(callsign="RR15", fuel_state="F+40")],
        )
        got = [
            _make_update(
                update_type=UpdateType.FUEL,
                entities=[
                    EntityUpdate(callsign="RR15", fuel_state="F+40", operational_status="OPERATIONAL"),
                ],
            )
        ]

        result = _score_entities(expected, got)
        # operational_status is None in expected, so it should not appear in field_total
        assert "operational_status" not in result.field_total
        assert result.field_total["fuel_state"] == 1

    def test_entities_without_keys_ignored(self):
        """Entities without track_number or callsign are not matched."""
        expected = _make_update(
            update_type=UpdateType.FUEL,
            entities=[EntityUpdate(fuel_state="F+40")],  # No key
        )
        got = [
            _make_update(
                update_type=UpdateType.FUEL,
                entities=[EntityUpdate(fuel_state="F+40")],
            )
        ]

        result = _score_entities(expected, got)
        assert result.matched == 0
        assert result.expected_total == 0
        assert result.extracted_total == 0


# --- EvalMetrics entity properties ---


class TestEvalMetricsEntityProperties:
    def test_entity_precision(self):
        m = EvalMetrics(model="test", entity_matched=8, entity_extracted=10, entity_expected=12)
        assert m.entity_precision == pytest.approx(0.8)

    def test_entity_recall(self):
        m = EvalMetrics(model="test", entity_matched=8, entity_extracted=10, entity_expected=12)
        assert m.entity_recall == pytest.approx(8 / 12)

    def test_entity_f1(self):
        m = EvalMetrics(model="test", entity_matched=8, entity_extracted=10, entity_expected=12)
        p = 0.8
        r = 8 / 12
        expected_f1 = 2 * p * r / (p + r)
        assert m.entity_f1 == pytest.approx(expected_f1)

    def test_entity_precision_zero_extracted(self):
        m = EvalMetrics(model="test", entity_matched=0, entity_extracted=0, entity_expected=5)
        assert m.entity_precision == 0.0

    def test_entity_recall_zero_expected(self):
        m = EvalMetrics(model="test", entity_matched=0, entity_extracted=3, entity_expected=0)
        assert m.entity_recall == 0.0

    def test_entity_f1_all_zero(self):
        m = EvalMetrics(model="test")
        assert m.entity_f1 == 0.0

    def test_entity_field_accuracy(self):
        m = EvalMetrics(
            model="test",
            entity_field_correct=Counter({"fuel_state": 8, "affiliation": 5}),
            entity_field_total=Counter({"fuel_state": 10, "affiliation": 10}),
        )
        assert m.entity_field_accuracy == pytest.approx(13 / 20)

    def test_entity_field_accuracy_empty(self):
        m = EvalMetrics(model="test")
        assert m.entity_field_accuracy == 0.0

    def test_summary_includes_entity_section(self):
        m = EvalMetrics(
            model="test-model",
            total=10,
            true_positives=7,
            false_positives=1,
            false_negatives=1,
            true_negatives=1,
            entity_matched=5,
            entity_expected=8,
            entity_extracted=6,
            entity_field_correct=Counter({"fuel_state": 4}),
            entity_field_total=Counter({"fuel_state": 5}),
        )
        summary = m.summary()
        assert "Entity-level scoring:" in summary
        assert "Matched=5" in summary
        assert "Expected=8" in summary
        assert "Extracted=6" in summary
        assert "Field accuracy:" in summary
        assert "fuel_state" in summary

    def test_summary_includes_update_type_section(self):
        m = EvalMetrics(
            model="test-model",
            total=10,
            true_positives=7,
            false_positives=1,
            false_negatives=1,
            true_negatives=1,
        )
        summary = m.summary()
        assert "Update-type scoring:" in summary

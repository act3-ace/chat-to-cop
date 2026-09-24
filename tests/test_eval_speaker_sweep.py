"""Tests for the sweep eval harness (scripts/eval_speaker_sweep.py).

These pin the schema contract the harness depends on. Replay DBs are written by
WorldStateStore, which has no `cop_updates` table and no `entities` column on
`updates` -- entities live inside the serialized CoPUpdate in `data_json`.
The harness queried a non-existent `entities` column for months and died with
`OperationalError: no such column: entities` on any real replay DB.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("numpy", reason="scripts/eval_speaker_sweep.py imports numpy at module scope")

from chat_to_cop.models.cop_update import CoPUpdate, EntityUpdate, UpdateType  # noqa: E402
from chat_to_cop.output.store import WorldStateStore  # noqa: E402
from scripts.eval_speaker_sweep import EvalDBError, eval_db  # noqa: E402

CHANNEL = "#vegas_internal"
TS_A = datetime(2026, 4, 12, 13, 0, tzinfo=timezone.utc)
TS_B = datetime(2026, 4, 12, 13, 1, tzinfo=timezone.utc)


async def _write_update(store: WorldStateStore, speaker: str, ts: datetime, utype: str, callsign: str | None) -> None:
    await store.write_update(
        CoPUpdate(
            update_type=UpdateType(utype),
            confidence=0.85,
            extraction_method="llm",
            entities=[EntityUpdate(callsign=callsign)] if callsign else [],
            source_channel=CHANNEL,
            source_speaker=speaker,
            source_message=f"{speaker}: {callsign or 'no entity'}",
            timestamp=ts,
        )
    )


def _label(speaker: str, ts: datetime, utype: str, callsign: str | None) -> tuple[tuple[str, str, str], dict]:
    key = (ts.isoformat(), CHANNEL, speaker)
    return key, {
        "timestamp": ts.isoformat(),
        "channel": CHANNEL,
        "sender": speaker,
        "extracted_type": utype,
        "extracted_entities": [{"callsign": callsign}] if callsign else [],
    }


class TestSchemaContract:
    """The assumption the old query violated, pinned so it cannot rot again."""

    async def test_updates_has_data_json_and_no_entities_column(self, tmp_path: Path) -> None:
        db_path = tmp_path / "replay.db"
        async with WorldStateStore(db_path):
            pass

        conn = sqlite3.connect(str(db_path))
        cols = [r[1] for r in conn.execute("PRAGMA table_info(updates)").fetchall()]
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        conn.close()

        assert "data_json" in cols
        assert "entities" not in cols, "entities is a TABLE, not a column on updates"
        assert "cop_updates" not in tables, "the table is called `updates`"
        assert "entities" in tables


class TestEvalDB:
    async def test_reads_entities_out_of_data_json(self, tmp_path: Path) -> None:
        db_path = tmp_path / "sweep_7b_on_det_1_1.db"
        async with WorldStateStore(db_path) as store:
            await _write_update(store, "alice", TS_A, "threat", "ORCA01")
            await _write_update(store, "bob", TS_B, "tasking", "TM636")

        # alice: type and entity both right. bob: type right, entity wrong.
        labels = dict(
            [
                _label("alice", TS_A, "threat", "ORCA01"),
                _label("bob", TS_B, "tasking", "ZEUS12"),
            ]
        )

        metrics = eval_db(db_path, labels)

        assert metrics["n_labels"] == 2
        assert metrics["n_matched"] == 2
        assert metrics["match_rate"] == 1.0
        assert metrics["type_exact"] == 1.0
        # 1.0 for alice, 0.0 for bob. A nonzero value is only reachable if the
        # entities were parsed out of data_json.
        assert metrics["entity_overlap"] == pytest.approx(0.5)

    async def test_tolerates_malformed_data_json(self, tmp_path: Path) -> None:
        db_path = tmp_path / "sweep_7b_on_det_1_2.db"
        async with WorldStateStore(db_path) as store:
            await _write_update(store, "alice", TS_A, "threat", "ORCA01")
            await _write_update(store, "bob", TS_B, "tasking", "TM636")

        conn = sqlite3.connect(str(db_path))
        conn.execute("UPDATE updates SET data_json = 'not json' WHERE source_speaker = 'bob'")
        conn.commit()
        conn.close()

        labels = dict(
            [
                _label("alice", TS_A, "threat", "ORCA01"),
                _label("bob", TS_B, "tasking", "ZEUS12"),
            ]
        )

        metrics = eval_db(db_path, labels)

        # Still scored; bob simply contributes no entities rather than crashing.
        assert metrics["n_matched"] == 2
        assert metrics["type_exact"] == 1.0
        assert metrics["entity_overlap"] == pytest.approx(0.5)

    async def test_entity_overlap_counts_a_real_match(self, tmp_path: Path) -> None:
        db_path = tmp_path / "sweep_7b_on_det_1_3.db"
        async with WorldStateStore(db_path) as store:
            await _write_update(store, "alice", TS_A, "threat", "ORCA01")

        labels = dict([_label("alice", TS_A, "threat", "ORCA01")])
        assert eval_db(db_path, labels)["entity_overlap"] == 1.0

    def test_raises_rather_than_scoring_zero_on_a_foreign_schema(self, tmp_path: Path) -> None:
        """A DB we cannot read is not a run that scored zero.

        Returning a zeroed metrics dict here would drag the mean of a sweep
        down silently; docs/GO_NOGO.md uses this script as the N>=50 gate.
        """
        db_path = tmp_path / "sweep_7b_on_det_1_4.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE tracks (id INTEGER PRIMARY KEY, callsign TEXT)")
        conn.commit()
        conn.close()

        with pytest.raises(EvalDBError) as exc_info:
            eval_db(db_path, {})

        message = str(exc_info.value)
        assert "cannot read the 'updates' table" in message
        assert "tracks" in message, "the error should name the tables that are present"

    def test_raises_on_an_empty_file(self, tmp_path: Path) -> None:
        db_path = tmp_path / "sweep_7b_on_det_1_5.db"
        db_path.write_bytes(b"")

        with pytest.raises(EvalDBError):
            eval_db(db_path, {})


class TestLoadLabels:
    def test_round_trips_the_composite_key(self, tmp_path: Path) -> None:
        from scripts.eval_speaker_sweep import load_labels

        labels_path = tmp_path / "labels.jsonl"
        _, rec = _label("alice", TS_A, "threat", "ORCA01")
        labels_path.write_text(json.dumps(rec) + "\n", encoding="utf-8")

        labels = load_labels(labels_path)
        assert (TS_A.isoformat(), CHANNEL, "alice") in labels

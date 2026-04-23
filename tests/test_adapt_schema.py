"""Tests for the adapt-schema CLI (issue #71).

Round-trip tests: ingest a schema, generate a mapping, verify it's valid
for the #70 JsonSchemaAdapter.
"""

import json
import sqlite3

import pytest

from chat_to_cop.cli.adapt_schema import (
    _generate_target_json_schema,
    generate_template_mapping,
    ingest_sqlite,
)


@pytest.fixture
def toy_sqlite(tmp_path):
    """Create a toy SQLite database with a tracks table."""
    db_path = tmp_path / "tracks.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE tracks (
            id INTEGER PRIMARY KEY,
            track_number TEXT NOT NULL,
            callsign TEXT,
            platform_type TEXT,
            affiliation TEXT NOT NULL,
            latitude REAL,
            longitude REAL,
            altitude REAL,
            status TEXT,
            confidence REAL,
            timestamp TEXT,
            source TEXT,
            notes TEXT
        )
    """
    )
    conn.close()
    return db_path


class TestIngestSqlite:
    def test_reads_table_and_columns(self, toy_sqlite):
        tables = ingest_sqlite(toy_sqlite)
        assert len(tables) == 1
        assert tables[0]["table"] == "tracks"
        col_names = [c["name"] for c in tables[0]["columns"]]
        assert "id" in col_names
        assert "track_number" in col_names
        assert "callsign" in col_names
        assert "latitude" in col_names

    def test_column_types_preserved(self, toy_sqlite):
        tables = ingest_sqlite(toy_sqlite)
        cols = {c["name"]: c for c in tables[0]["columns"]}
        assert cols["id"]["type"] == "INTEGER"
        assert cols["track_number"]["type"] == "TEXT"
        assert cols["latitude"]["type"] == "REAL"

    def test_notnull_and_pk_flags(self, toy_sqlite):
        tables = ingest_sqlite(toy_sqlite)
        cols = {c["name"]: c for c in tables[0]["columns"]}
        assert cols["id"]["pk"] is True
        assert cols["track_number"]["notnull"] is True
        assert cols["callsign"]["notnull"] is False

    def test_empty_database(self, tmp_path):
        db_path = tmp_path / "empty.db"
        conn = sqlite3.connect(str(db_path))
        conn.close()
        tables = ingest_sqlite(db_path)
        assert tables == []


class TestIngestExcel:
    def test_csv_ingestion(self, tmp_path):
        from chat_to_cop.cli.adapt_schema import ingest_excel

        csv_path = tmp_path / "tracks.csv"
        csv_path.write_text("id,track_number,callsign,latitude\n1,TM636,ORCA01,22.5\n")
        tables = ingest_excel(csv_path)
        assert len(tables) == 1
        assert tables[0]["table"] == "tracks"
        col_names = [c["name"] for c in tables[0]["columns"]]
        assert col_names == ["id", "track_number", "callsign", "latitude"]


class TestTemplateMapping:
    def test_generates_valid_mapping(self, toy_sqlite):
        tables = ingest_sqlite(toy_sqlite)
        mapping = generate_template_mapping(tables)
        assert "track_number" in mapping
        assert mapping["track_number"] == "track.track_number"
        assert mapping["callsign"] == "track.callsign"
        assert mapping["latitude"] == "track.position.latitude"
        assert mapping["confidence"] == "extraction_confidence"

    def test_unknown_fields_go_to_metadata(self, toy_sqlite):
        tables = ingest_sqlite(toy_sqlite)
        mapping = generate_template_mapping(tables)
        assert mapping["notes"] == "track.metadata.notes"

    def test_timestamp_gets_transform(self, toy_sqlite):
        tables = ingest_sqlite(toy_sqlite)
        mapping = generate_template_mapping(tables)
        assert isinstance(mapping["timestamp"], dict)
        assert mapping["timestamp"]["transform"] == "iso_datetime"


class TestJsonSchemaGeneration:
    def test_schema_has_properties(self, toy_sqlite):
        tables = ingest_sqlite(toy_sqlite)
        schema = _generate_target_json_schema(tables)
        assert "properties" in schema
        assert "track_number" in schema["properties"]
        assert schema["properties"]["latitude"]["type"] == "number"
        assert schema["properties"]["id"]["type"] == "integer"

    def test_required_includes_notnull(self, toy_sqlite):
        tables = ingest_sqlite(toy_sqlite)
        schema = _generate_target_json_schema(tables)
        assert "id" in schema["required"]
        assert "track_number" in schema["required"]
        assert "callsign" not in schema["required"]


class TestRoundTrip:
    """Verify template mapping + generated schema are loadable by JsonSchemaAdapter."""

    def test_adapter_loads_generated_files(self, toy_sqlite, tmp_path):
        from chat_to_cop.output.schema_adapter import JsonSchemaAdapter

        tables = ingest_sqlite(toy_sqlite)
        mapping = generate_template_mapping(tables)
        schema = _generate_target_json_schema(tables)

        schema_path = tmp_path / "schema.json"
        mapping_path = tmp_path / "mapping.json"
        schema_path.write_text(json.dumps(schema))
        mapping_path.write_text(json.dumps(mapping))

        adapter = JsonSchemaAdapter(schema_path, mapping_path)
        assert adapter is not None

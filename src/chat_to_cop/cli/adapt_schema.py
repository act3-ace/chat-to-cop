"""At-event schema ingestion and mapping generation.

Issue #71.  Mia runs this CLI when the contractor hands over the CoP
database schema.  It ingests the schema from SQLite / Excel / live DB,
generates a mapping config via LLM (or falls back to a template), and
writes config/schema_mapping.json for the #70 adapter layer.

Usage:
    python -m chat_to_cop.cli.adapt_schema schema.sqlite
    python -m chat_to_cop.cli.adapt_schema schema.xlsx
    python -m chat_to_cop.cli.adapt_schema --db-url postgresql://host/tracks
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from loguru import logger


def ingest_sqlite(path: Path) -> list[dict[str, Any]]:
    """Read schema from a SQLite file. Returns list of table descriptors."""
    import sqlite3

    conn = sqlite3.connect(str(path))
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    tables = [row[0] for row in cursor.fetchall()]

    result = []
    for table in tables:
        cursor.execute(f"PRAGMA table_info({table})")
        columns = []
        for row in cursor.fetchall():
            columns.append(
                {
                    "name": row[1],
                    "type": row[2] or "TEXT",
                    "notnull": bool(row[3]),
                    "default": row[4],
                    "pk": bool(row[5]),
                }
            )
        result.append({"table": table, "columns": columns})
    conn.close()
    return result


def ingest_excel(path: Path) -> list[dict[str, Any]]:
    """Read schema from Excel/CSV — treat header row as column names."""
    suffix = path.suffix.lower()

    if suffix == ".csv":
        import csv

        with open(path, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None:
                return []
            columns = [
                {"name": col, "type": "TEXT", "notnull": False, "default": None, "pk": False}
                for col in reader.fieldnames
            ]
            return [{"table": path.stem, "columns": columns}]

    try:
        import openpyxl
    except ImportError:
        logger.error("openpyxl not installed. Run: pip install openpyxl")
        sys.exit(1)

    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    result = []
    for sheet in wb.sheetnames:
        ws = wb[sheet]
        rows = list(ws.iter_rows(max_row=2, values_only=True))
        if not rows:
            continue
        headers = [str(cell) if cell else f"col_{i}" for i, cell in enumerate(rows[0])]
        type_hints = ["TEXT"] * len(headers)
        if len(rows) > 1 and rows[1]:
            for i, cell in enumerate(rows[1]):
                if i < len(type_hints):
                    if isinstance(cell, (int, float)):
                        type_hints[i] = "REAL" if isinstance(cell, float) else "INTEGER"
        columns = [
            {"name": h, "type": t, "notnull": False, "default": None, "pk": False} for h, t in zip(headers, type_hints)
        ]
        result.append({"table": sheet, "columns": columns})
    wb.close()
    return result


def ingest_database(db_url: str) -> list[dict[str, Any]]:
    """Read schema from a live database via SQLAlchemy."""
    try:
        from sqlalchemy import create_engine, inspect
    except ImportError:
        logger.error("sqlalchemy not installed. Run: pip install sqlalchemy")
        sys.exit(1)

    engine = create_engine(db_url)
    insp = inspect(engine)
    result = []
    for table in insp.get_table_names():
        columns = []
        for col in insp.get_columns(table):
            columns.append(
                {
                    "name": col["name"],
                    "type": str(col["type"]),
                    "notnull": not col.get("nullable", True),
                    "default": str(col.get("default", "")) if col.get("default") else None,
                    "pk": col["name"]
                    in [pk["name"] for pk in insp.get_pk_constraint(table).get("constrained_columns", [])],
                }
            )
        result.append({"table": table, "columns": columns})
    engine.dispose()
    return result


def _our_fields() -> str:
    """Return a summary of CoPRecord fields for the LLM prompt."""
    from chat_to_cop.output.cop_schema import BattleEffect, CoPRecord, Track

    lines = ["CoPRecord (top-level):"]
    for name, field in CoPRecord.model_fields.items():
        lines.append(f"  {name}: {field.annotation} -- {field.description or ''}")
    lines.append("\nTrack (CoPRecord.track):")
    for name, field in Track.model_fields.items():
        lines.append(f"  track.{name}: {field.annotation} -- {field.description or ''}")
    lines.append("\nBattleEffect (CoPRecord.battle_effect):")
    for name, field in BattleEffect.model_fields.items():
        lines.append(f"  battle_effect.{name}: {field.annotation} -- {field.description or ''}")
    return "\n".join(lines)


def _format_target_schema(tables: list[dict[str, Any]]) -> str:
    """Format ingested schema for the LLM prompt."""
    lines = []
    for tbl in tables:
        lines.append(f"Table: {tbl['table']}")
        for col in tbl["columns"]:
            pk = " [PK]" if col.get("pk") else ""
            nn = " NOT NULL" if col.get("notnull") else ""
            lines.append(f"  {col['name']}: {col['type']}{pk}{nn}")
        lines.append("")
    return "\n".join(lines)


def generate_mapping_via_llm(
    tables: list[dict[str, Any]],
    url: str,
    model: str,
    api_key: str,
) -> dict[str, Any]:
    """Use LLM to generate a mapping from CoPRecord to target schema."""
    import asyncio

    from openai import AsyncOpenAI

    our = _our_fields()
    target = _format_target_schema(tables)

    prompt = f"""You are mapping fields from a source system to a target database schema.

SOURCE FIELDS (our CoPRecord model -- dotted paths like "track.callsign"):
{our}

TARGET SCHEMA (the database tables/columns we need to write to):
{target}

Generate a JSON mapping object where:
- Keys are target column names (from the TARGET SCHEMA)
- Values are either:
  - A string: source field path (e.g., "track.callsign")
  - An object: {{"source": "path", "transform": "name"}}
    Allowed transforms: pass, upper, lower, int, float, iso_datetime

Rules:
- Map every target column that has a reasonable source field
- Skip target columns with no source match (omit them)
- Use "track." prefix for entity fields, "battle_effect." for engagement fields
- For metadata/overflow, use "track.metadata.key_name" paths
- Keys starting with "_" are comments (ignored by the adapter)

Return ONLY the JSON object, no markdown fences, no explanation."""

    client = AsyncOpenAI(base_url=url, api_key=api_key, timeout=60.0)

    async def _call():
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a database schema mapping assistant. Return only valid JSON."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
        )
        return resp.choices[0].message.content

    raw = asyncio.run(_call())
    if not raw:
        raise ValueError("LLM returned empty response")

    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()

    return json.loads(raw)


def generate_template_mapping(tables: list[dict[str, Any]]) -> dict[str, Any]:
    """Generate a template mapping (no LLM) -- all fields to metadata."""
    mapping: dict[str, Any] = {
        "_description": "Template mapping -- review and customize before use",
    }
    common_mappings = {
        "id": "track.track_id",
        "track_id": "track.track_id",
        "track_number": "track.track_number",
        "callsign": "track.callsign",
        "platform": "track.platform_type",
        "platform_type": "track.platform_type",
        "affiliation": "track.affiliation",
        "latitude": "track.position.latitude",
        "longitude": "track.position.longitude",
        "altitude": "track.position.altitude_m",
        "status": "track.operational_status",
        "confidence": "extraction_confidence",
        "source": "source_channel",
        "timestamp": {"source": "timestamp", "transform": "iso_datetime"},
        "message": "source_message",
    }

    for tbl in tables:
        for col in tbl["columns"]:
            name_lower = col["name"].lower()
            if name_lower in common_mappings:
                mapping[col["name"]] = common_mappings[name_lower]
            else:
                mapping[col["name"]] = f"track.metadata.{col['name']}"
    return mapping


def _generate_target_json_schema(tables: list[dict[str, Any]]) -> dict[str, Any]:
    """Generate a minimal JSON Schema from ingested tables."""
    if len(tables) == 1:
        tbl = tables[0]
    else:
        tbl = max(tables, key=lambda t: len(t["columns"]))

    type_map = {
        "INTEGER": "integer",
        "INT": "integer",
        "REAL": "number",
        "FLOAT": "number",
        "DOUBLE": "number",
        "TEXT": "string",
        "VARCHAR": "string",
        "CHAR": "string",
        "BLOB": "string",
        "BOOLEAN": "boolean",
        "DATETIME": "string",
        "TIMESTAMP": "string",
    }

    properties = {}
    required = []
    for col in tbl["columns"]:
        col_type = col["type"].upper().split("(")[0].strip()
        json_type = type_map.get(col_type, "string")
        properties[col["name"]] = {"type": json_type}
        if col.get("notnull") or col.get("pk"):
            required.append(col["name"])

    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": properties,
        "required": required,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Ingest external schema and generate mapping config for the CoP adapter (#71)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("schema_file", nargs="?", help="Path to SQLite, Excel, or CSV schema file")
    parser.add_argument("--db-url", help="SQLAlchemy database URL for live schema reflection")
    parser.add_argument("--url", default="https://api.groq.com/openai/v1", help="LLM API URL")
    parser.add_argument("--model", default="llama-3.3-70b-versatile", help="LLM model for mapping generation")
    parser.add_argument("--api-key", default=None, help="LLM API key (or set GROQ_API_KEY)")
    parser.add_argument("--output-dir", default="config", help="Output directory for mapping files")
    parser.add_argument("--no-llm", action="store_true", help="Skip LLM, generate template mapping only")
    parser.add_argument("--yes", "-y", action="store_true", help="Skip interactive confirmation")
    args = parser.parse_args()

    if not args.schema_file and not args.db_url:
        parser.error("Provide either a schema file or --db-url")

    # Ingest schema
    if args.db_url:
        logger.info(f"Reflecting schema from {args.db_url}")
        tables = ingest_database(args.db_url)
    else:
        path = Path(args.schema_file)
        if not path.exists():
            logger.error(f"File not found: {path}")
            sys.exit(1)

        suffix = path.suffix.lower()
        if suffix in (".db", ".sqlite", ".sqlite3"):
            tables = ingest_sqlite(path)
        elif suffix in (".xlsx", ".xls", ".csv"):
            tables = ingest_excel(path)
        else:
            logger.error(f"Unsupported file type: {suffix}. Use .sqlite/.xlsx/.csv or --db-url")
            sys.exit(1)

    if not tables:
        logger.error("No tables found in schema source")
        sys.exit(1)

    total_cols = sum(len(t["columns"]) for t in tables)
    logger.info(f"Ingested {len(tables)} table(s), {total_cols} columns")
    for tbl in tables:
        col_names = [c["name"] for c in tbl["columns"]]
        logger.info(f"  {tbl['table']}: {', '.join(col_names)}")

    # Generate mapping
    import os

    api_key = args.api_key or os.environ.get("GROQ_API_KEY") or os.environ.get("OPENAI_API_KEY", "")

    if args.no_llm or not api_key:
        if not args.no_llm:
            logger.warning("No API key available, falling back to template mapping")
        mapping = generate_template_mapping(tables)
        method = "template"
    else:
        try:
            logger.info(f"Generating mapping via {args.model}...")
            mapping = generate_mapping_via_llm(tables, args.url, args.model, api_key)
            method = f"llm ({args.model})"
        except Exception as e:
            logger.warning(f"LLM mapping failed ({e}), falling back to template")
            mapping = generate_template_mapping(tables)
            method = "template (LLM fallback)"

    # Display mapping for review
    print(f"\nGenerated mapping ({method}):")
    print("-" * 60)
    for target, source in sorted(mapping.items()):
        if target.startswith("_"):
            continue
        if isinstance(source, dict):
            print(f"  {target:30s} <- {source['source']} [{source.get('transform', 'pass')}]")
        else:
            print(f"  {target:30s} <- {source}")
    print("-" * 60)

    # Confirm
    if not args.yes:
        response = input("\nWrite this mapping? [y/n/edit]: ").strip().lower()
        if response == "edit":
            print("Mapping JSON (edit and paste back):")
            print(json.dumps(mapping, indent=2))
            print("\n(Edit the file manually after it's written)")
        elif response != "y":
            print("Aborted.")
            sys.exit(0)

    # Write files
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    schema_path = out_dir / "cop_target_schema.json"
    mapping_path = out_dir / "schema_mapping.json"

    target_schema = _generate_target_json_schema(tables)
    with open(schema_path, "w") as f:
        json.dump(target_schema, f, indent=2)
    logger.info(f"Wrote target schema: {schema_path}")

    with open(mapping_path, "w") as f:
        json.dump(mapping, f, indent=2)
    logger.info(f"Wrote mapping config: {mapping_path}")

    adapter_spec = f"jsonschema:{schema_path}:{mapping_path}"
    print("\nTo activate, set:")
    print(f"  CHAT_TO_COP_SCHEMA_ADAPTER={adapter_spec}")
    print("\nOr add to your .env / docker-compose.yml.")


if __name__ == "__main__":
    main()

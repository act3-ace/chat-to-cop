"""Mock CoP REST API for end-to-end write path testing.

Accepts the same HTTP requests the CoPWriter/CoPRESTClient sends, validates
incoming payloads, stores them in memory, and provides inspection endpoints.

Usage:
    # Start the mock server
    python scripts/mock_cop_api.py --port 9000

    # Configure chat-to-cop to write to it
    export CHAT_TO_COP_COP_API_URL=http://127.0.0.1:9000
    python -m chat_to_cop.replay data/chat/Dash3-GBC/Data/23Sep/usaf/chat.zip \\
        --url http://127.0.0.1:11434/v1 --model qwen2.5:7b

    # Inspect results
    curl http://127.0.0.1:9000/stats
    curl http://127.0.0.1:9000/records?limit=10
    curl http://127.0.0.1:9000/records/some-id
    curl -X POST http://127.0.0.1:9000/reset
"""

from __future__ import annotations

import argparse
import signal
import sys
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel, ValidationError

from chat_to_cop.output.cop_schema import CoPRecord

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


class RecordStore:
    """In-memory store for received CoP records."""

    def __init__(self) -> None:
        self._records: dict[str, dict[str, Any]] = {}
        self._validation_errors: list[dict[str, Any]] = []
        self._receive_order: list[str] = []

    def add(self, record_id: str, raw_payload: dict[str, Any], validated: CoPRecord | None) -> None:
        entry = {
            "id": record_id,
            "received_at": datetime.now(timezone.utc).isoformat(),
            "payload": raw_payload,
            "valid": validated is not None,
        }
        if validated is not None:
            entry["record_type"] = validated.record_type
            entry["write_authority"] = validated.write_authority.value
            entry["extraction_confidence"] = validated.extraction_confidence
            entry["extraction_method"] = validated.extraction_method
            entry["source_channel"] = validated.source_channel
            entry["source_speaker"] = validated.source_speaker
        self._records[record_id] = entry
        self._receive_order.append(record_id)

    def add_validation_error(self, raw_payload: dict[str, Any], error: str) -> None:
        self._validation_errors.append(
            {
                "received_at": datetime.now(timezone.utc).isoformat(),
                "error": error,
                "payload_keys": list(raw_payload.keys()) if isinstance(raw_payload, dict) else str(type(raw_payload)),
            }
        )

    def get(self, record_id: str) -> dict[str, Any] | None:
        return self._records.get(record_id)

    def list_records(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        ids = self._receive_order[offset : offset + limit]
        return [self._records[rid] for rid in ids if rid in self._records]

    def stats(self) -> dict[str, Any]:
        total = len(self._records)
        valid = sum(1 for r in self._records.values() if r.get("valid"))
        invalid = total - valid

        by_authority: Counter[str] = Counter()
        by_record_type: Counter[str] = Counter()
        by_method: Counter[str] = Counter()
        by_channel: Counter[str] = Counter()
        confidence_buckets = {"high_gte_095": 0, "mid_050_095": 0, "low_lt_050": 0}

        for r in self._records.values():
            if not r.get("valid"):
                continue
            authority = r.get("write_authority", "unknown")
            by_authority[authority] += 1
            by_record_type[r.get("record_type", "unknown")] += 1
            by_method[r.get("extraction_method", "unknown")] += 1
            channel = r.get("source_channel", "unknown")
            if channel:
                by_channel[channel] += 1

            conf = r.get("extraction_confidence", 0.0)
            if conf >= 0.95:
                confidence_buckets["high_gte_095"] += 1
            elif conf >= 0.50:
                confidence_buckets["mid_050_095"] += 1
            else:
                confidence_buckets["low_lt_050"] += 1

        return {
            "total_records": total,
            "valid": valid,
            "invalid": invalid,
            "validation_errors": len(self._validation_errors),
            "by_write_authority": dict(by_authority),
            "by_record_type": dict(by_record_type),
            "by_extraction_method": dict(by_method),
            "by_channel": dict(by_channel),
            "confidence_buckets": confidence_buckets,
        }

    def reset(self) -> dict[str, int]:
        count = len(self._records)
        errors = len(self._validation_errors)
        self._records.clear()
        self._validation_errors.clear()
        self._receive_order.clear()
        return {"cleared_records": count, "cleared_errors": errors}

    @property
    def validation_errors(self) -> list[dict[str, Any]]:
        return list(self._validation_errors)


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

store = RecordStore()
app = FastAPI(title="Mock CoP REST API", version="0.1.0")


def _ingest(payload: dict[str, Any], endpoint: str) -> JSONResponse:
    """Common ingestion logic for all POST endpoints."""
    record_id = str(uuid.uuid4())[:12]

    try:
        validated = CoPRecord.model_validate(payload)
    except ValidationError as exc:
        error_msg = str(exc)
        store.add_validation_error(payload, error_msg)
        store.add(record_id, payload, validated=None)
        logger.warning("[INVALID] {} -> {} validation errors", endpoint, exc.error_count())
        return JSONResponse(
            status_code=400,
            content={"error": "validation_failed", "detail": error_msg, "id": record_id},
        )

    store.add(record_id, payload, validated)

    logger.info(
        "[{}] {} type={} authority={} conf={:.2f} method={} channel={} speaker={}",
        endpoint,
        record_id,
        validated.record_type,
        validated.write_authority.value,
        validated.extraction_confidence,
        validated.extraction_method,
        validated.source_channel or "-",
        validated.source_speaker or "-",
    )

    return JSONResponse(
        status_code=201,
        content={"id": record_id, "status": "accepted"},
    )


@app.post("/api/v1/tracks")
async def post_track(request: Request) -> JSONResponse:
    payload = await request.json()
    return _ingest(payload, "/api/v1/tracks")


@app.post("/api/v1/effects")
async def post_effect(request: Request) -> JSONResponse:
    payload = await request.json()
    return _ingest(payload, "/api/v1/effects")


@app.post("/api/v1/records")
async def post_record(request: Request) -> JSONResponse:
    payload = await request.json()
    return _ingest(payload, "/api/v1/records")


@app.get("/records")
async def list_records(
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    records = store.list_records(limit=limit, offset=offset)
    return {"count": len(records), "offset": offset, "records": records}


@app.get("/records/{record_id}")
async def get_record(record_id: str) -> dict[str, Any]:
    record = store.get(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Record {record_id} not found")
    return record


@app.get("/stats")
async def get_stats() -> dict[str, Any]:
    return store.stats()


@app.get("/errors")
async def get_errors() -> dict[str, Any]:
    errors = store.validation_errors
    return {"count": len(errors), "errors": errors}


@app.post("/reset")
async def reset_store() -> dict[str, Any]:
    result = store.reset()
    logger.info("[RESET] Cleared {} records, {} errors", result["cleared_records"], result["cleared_errors"])
    return result


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Shutdown summary
# ---------------------------------------------------------------------------


class StatsModel(BaseModel):
    """For clean logging on shutdown."""

    total_records: int = 0
    valid: int = 0
    invalid: int = 0
    validation_errors: int = 0
    by_write_authority: dict[str, int] = {}
    by_record_type: dict[str, int] = {}
    by_extraction_method: dict[str, int] = {}
    confidence_buckets: dict[str, int] = {}


def _print_summary() -> None:
    s = store.stats()
    logger.info("--- Mock CoP API Summary ---")
    logger.info("Total records received: {}", s["total_records"])
    logger.info("  Valid: {}, Invalid: {}", s["valid"], s["invalid"])
    logger.info("  Validation errors: {}", s["validation_errors"])
    if s["by_write_authority"]:
        logger.info("  By write authority: {}", s["by_write_authority"])
    if s["by_record_type"]:
        logger.info("  By record type: {}", s["by_record_type"])
    if s["by_extraction_method"]:
        logger.info("  By extraction method: {}", s["by_extraction_method"])
    if s["confidence_buckets"]:
        logger.info("  Confidence buckets: {}", s["confidence_buckets"])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Mock CoP REST API for end-to-end testing")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=9000, help="Port (default: 9000)")
    parser.add_argument("--log-level", default="info", choices=["debug", "info", "warning", "error"])
    args = parser.parse_args()

    # Print summary on shutdown
    def _shutdown_handler(signum, frame):
        _print_summary()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown_handler)
    signal.signal(signal.SIGTERM, _shutdown_handler)

    logger.info("Starting Mock CoP REST API on {}:{}", args.host, args.port)
    logger.info("Configure chat-to-cop: export CHAT_TO_COP_COP_API_URL=http://{}:{}", args.host, args.port)

    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)


if __name__ == "__main__":
    main()

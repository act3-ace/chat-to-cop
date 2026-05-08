"""FastAPI REST API for the world state store.

Exposes extracted CoPUpdates, entity state, and system health via HTTP.
Run with: uvicorn chat_to_cop.api:app --reload

Endpoints:
    GET /                           -- HTML dashboard (redirect)
    GET /dashboard                  -- HTML dashboard for operator visibility
    GET /updates                    -- recent CoPUpdates with filtering
    GET /entities                   -- current entity state
    GET /entities/{id}              -- single entity with details
    GET /health                     -- system health
    GET /stats                      -- extraction statistics
    POST /updates/{id}/correct      -- submit a correction for an update
    GET /corrections                -- list recent corrections
    GET /corrections/stats          -- correction rate by type/channel/speaker
    POST /updates/{id}/override     -- quick operator override ("this is wrong")
    GET /overrides                  -- list recent overrides
    GET /overrides/stats            -- override rate by type/channel/speaker
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, model_validator

from chat_to_cop.config import PipelineConfig
from chat_to_cop.dashboard import DASHBOARD_HTML
from chat_to_cop.metrics import metrics
from chat_to_cop.output.cop_writer import CoPWriter
from chat_to_cop.output.store import WorldStateStore

# Store instance — set by lifespan or injected by tests
_store: WorldStateStore | None = None

# CoPWriter instance — set by lifespan or injected by tests
_cop_writer: CoPWriter | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _store
    config = PipelineConfig()
    _store = WorldStateStore(config.db_path)
    await _store.open()
    yield
    if _store:
        await _store.close()


app = FastAPI(
    title="chat-to-cop",
    description="AI staff officer: world-state extraction from military chat to CoP",
    version="0.1.0",
    lifespan=lifespan,
)


def _get_store() -> WorldStateStore:
    if _store is None:
        raise RuntimeError("Store not initialized")
    return _store


# --- Admin endpoints (pause/resume kill switch) ---


@app.post("/admin/pause")
async def admin_pause():
    """Immediately pause all CoP writes. Incoming updates queue locally."""
    if _cop_writer is None:
        return {"paused": False, "queue_depth": 0, "error": "standalone mode"}
    _cop_writer.pause()
    return {"paused": True, "queue_depth": _cop_writer.pause_queue_size}


@app.post("/admin/resume")
async def admin_resume():
    """Resume CoP writes and flush queued updates."""
    if _cop_writer is None:
        return {"paused": False, "flushed": 0, "queue_depth": 0, "error": "standalone mode"}
    _cop_writer.resume()
    results = await _cop_writer.flush_pause_queue()
    return {
        "paused": False,
        "flushed": len(results),
        "queue_depth": _cop_writer.pause_queue_size,
    }


@app.get("/admin/status")
async def admin_status():
    """Current pause state and queue depth."""
    if _cop_writer is None:
        return {"paused": False, "queue_depth": 0}
    return {"paused": _cop_writer.paused, "queue_depth": _cop_writer.pause_queue_size}


@app.get("/", response_class=RedirectResponse, include_in_schema=False)
async def root():
    """Redirect root to dashboard."""
    return RedirectResponse(url="/dashboard")


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    """Single-page HTML dashboard for operator visibility."""
    return HTMLResponse(content=DASHBOARD_HTML)


@app.get("/updates")
async def get_updates(
    channel: str | None = Query(None, description="Filter by channel"),
    since: str | None = Query(None, description="ISO timestamp filter"),
    limit: int = Query(100, ge=1, le=1000),
):
    """Get recent CoPUpdates with optional filtering. Includes database row IDs."""
    store = _get_store()
    since_dt = datetime.fromisoformat(since) if since else None
    return await store.get_recent_updates_with_ids(channel=channel, since=since_dt, limit=limit)


@app.get("/entities")
async def get_entities():
    """Get all tracked entities."""
    store = _get_store()
    cursor = await store.db.execute(
        "SELECT entity_key, track_number, callsign, platform_type, "
        "affiliation, operational_status, last_known_lat, last_known_lon, "
        "last_updated FROM entities ORDER BY last_updated DESC"
    )
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


@app.get("/entities/{entity_id}")
async def get_entity(entity_id: str):
    """Get a single entity by track number or callsign."""
    store = _get_store()
    entity = await store.get_entity(entity_id)
    if entity is None:
        return JSONResponse(status_code=404, content={"error": f"Entity {entity_id} not found"})
    return entity.model_dump(mode="json")


@app.get("/health")
async def get_health():
    """System health check."""
    store = _get_store()
    msg_total = sum(metrics._counters.get("agent_messages_received_total", {}).values())
    last_msg = await store.last_audit_timestamp()
    return {
        "status": "ok",
        "updates_count": await store.count_updates(),
        "entities_count": await store.count_entities(),
        "messages_received": msg_total,
        "last_message_time": last_msg,
    }


@app.get("/stats")
async def get_stats():
    """Extraction statistics from metrics."""
    snap = metrics.snapshot()
    return {
        "metrics_enabled": metrics.enabled,
        "counters": snap.get("counters", {}),
        "histograms": snap.get("histograms", {}),
    }


# --- Correction endpoints ---


class CorrectionPayload(BaseModel):
    """Operator correction for an extraction."""

    corrected_type: str | None = None
    corrected_entities: list[dict[str, Any]] | None = None
    rejected: bool | None = None
    notes: str | None = None
    corrector: str | None = None

    @model_validator(mode="after")
    def at_least_one_field(self) -> CorrectionPayload:
        if not any([self.corrected_type, self.corrected_entities, self.rejected, self.notes]):
            raise ValueError("At least one of corrected_type, corrected_entities, rejected, or notes must be provided")
        return self


@app.post("/updates/{update_id}/correct")
async def post_correction(update_id: int, payload: CorrectionPayload):
    """Submit an operator correction for an existing extraction."""
    store = _get_store()
    try:
        correction_id = await store.write_correction(update_id, payload.model_dump())
    except ValueError as e:
        return JSONResponse(status_code=404, content={"error": str(e)})
    return {"id": correction_id, "update_id": update_id, "status": "recorded"}


@app.get("/corrections")
async def get_corrections(
    limit: int = Query(50, ge=1, le=500),
):
    """List recent corrections with associated update info."""
    store = _get_store()
    corrections = await store.get_corrections(limit=limit)
    return corrections


@app.get("/corrections/stats")
async def get_correction_stats():
    """Correction rate by type, channel, and speaker."""
    store = _get_store()
    return await store.correction_stats()


# --- Override endpoints (quick "this is wrong" button) ---


class OverridePayload(BaseModel):
    """Operator override: marks an update as wrong. Reason is optional (operator may be busy)."""

    reason: str = ""


@app.post("/updates/{update_id}/override")
async def post_override(update_id: int, payload: OverridePayload | None = None):
    """Record an operator override for an update. One click, optional reason."""
    store = _get_store()
    reason = payload.reason if payload else ""
    try:
        override_id = await store.record_override(update_id, reason=reason)
    except ValueError as e:
        return JSONResponse(status_code=404, content={"error": str(e)})
    return {"id": override_id, "update_id": update_id, "status": "overridden"}


@app.get("/overrides")
async def get_overrides(
    limit: int = Query(100, ge=1, le=1000),
):
    """List recent operator overrides."""
    store = _get_store()
    return await store.get_overrides(limit=limit)


@app.get("/overrides/stats")
async def get_override_stats():
    """Override rate by type, channel, and speaker."""
    store = _get_store()
    return await store.override_stats()

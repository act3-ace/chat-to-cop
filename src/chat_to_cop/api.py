"""FastAPI REST API for the world state store.

Exposes extracted CoPUpdates, entity state, and system health via HTTP.
Run with: uvicorn chat_to_cop.api:app --reload

Endpoints:
    GET /updates          — recent CoPUpdates with filtering
    GET /entities         — current entity state
    GET /entities/{id}    — single entity with details
    GET /health           — system health
    GET /stats            — extraction statistics
"""

from __future__ import annotations

from datetime import datetime

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

from chat_to_cop.metrics import metrics
from chat_to_cop.output.store import WorldStateStore

app = FastAPI(
    title="chat-to-cop",
    description="AI staff officer: world-state extraction from military chat to CoP",
    version="0.1.0",
)

# Store instance — initialized on startup
_store: WorldStateStore | None = None


@app.on_event("startup")
async def startup():
    global _store
    _store = WorldStateStore("data/world_state.db")
    await _store.open()


@app.on_event("shutdown")
async def shutdown():
    if _store:
        await _store.close()


def _get_store() -> WorldStateStore:
    if _store is None:
        raise RuntimeError("Store not initialized")
    return _store


@app.get("/updates")
async def get_updates(
    channel: str | None = Query(None, description="Filter by channel"),
    since: str | None = Query(None, description="ISO timestamp filter"),
    limit: int = Query(100, ge=1, le=1000),
):
    """Get recent CoPUpdates with optional filtering."""
    store = _get_store()
    since_dt = datetime.fromisoformat(since) if since else None
    updates = await store.get_recent_updates(channel=channel, since=since_dt, limit=limit)
    return [u.model_dump(mode="json") for u in updates]


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
    return {
        "status": "ok",
        "updates_count": await store.count_updates(),
        "entities_count": await store.count_entities(),
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

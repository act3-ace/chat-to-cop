"""Tests for the FastAPI REST API."""

import asyncio
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

# Use a shared in-memory store for all tests in this module
import chat_to_cop.api as api_module
from chat_to_cop.api import app
from chat_to_cop.models.cop_update import CoPUpdate, EntityUpdate, UpdateType
from chat_to_cop.output.store import WorldStateStore


@pytest.fixture(autouse=True)
def setup_store():
    """Set up an in-memory store for each test."""

    async def _setup():
        store = WorldStateStore(":memory:")
        await store.open()

        # Seed with test data
        await store.write_update(
            CoPUpdate(
                update_type=UpdateType.FUEL,
                confidence=0.9,
                extraction_method="llm",
                entities=[EntityUpdate(callsign="RR15", fuel_state="F+40")],
                source_channel="#c2_coord",
                source_speaker="Hydro_Tank",
                source_message="RR15 F+40",
                timestamp=datetime(2025, 9, 23, 14, 10, 0, tzinfo=timezone.utc),
            )
        )
        await store.write_update(
            CoPUpdate(
                update_type=UpdateType.THREAT,
                confidence=0.85,
                extraction_method="llm",
                entities=[EntityUpdate(track_number="TM677", affiliation="HOSTILE")],
                source_channel="#isr_reports",
                source_speaker="AOC_SIDO",
                source_message="tacrep TM677",
                timestamp=datetime(2025, 9, 23, 14, 15, 0, tzinfo=timezone.utc),
            )
        )
        return store

    store = asyncio.run(_setup())
    api_module._store = store
    yield store
    asyncio.run(store.close())
    api_module._store = None


@pytest.fixture
def client():
    return TestClient(app)


class TestHealthEndpoint:
    def test_health(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["updates_count"] == 2
        assert data["entities_count"] >= 1


class TestUpdatesEndpoint:
    def test_get_all_updates(self, client):
        r = client.get("/updates")
        assert r.status_code == 200
        updates = r.json()
        assert len(updates) == 2

    def test_filter_by_channel(self, client):
        r = client.get("/updates?channel=%23c2_coord")
        assert r.status_code == 200
        updates = r.json()
        assert len(updates) == 1
        assert updates[0]["source_channel"] == "#c2_coord"

    def test_limit(self, client):
        r = client.get("/updates?limit=1")
        assert r.status_code == 200
        assert len(r.json()) == 1


class TestEntitiesEndpoint:
    def test_get_all_entities(self, client):
        r = client.get("/entities")
        assert r.status_code == 200
        entities = r.json()
        assert len(entities) >= 1

    def test_get_entity_by_callsign(self, client):
        r = client.get("/entities/RR15")
        assert r.status_code == 200
        entity = r.json()
        assert entity["callsign"] == "RR15"

    def test_get_entity_by_track(self, client):
        r = client.get("/entities/TM677")
        assert r.status_code == 200
        entity = r.json()
        assert entity["track_number"] == "TM677"

    def test_entity_not_found(self, client):
        r = client.get("/entities/NONEXISTENT")
        assert r.status_code == 404


class TestDashboardEndpoint:
    def test_dashboard_returns_html(self, client):
        r = client.get("/dashboard")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]

    def test_dashboard_contains_title(self, client):
        r = client.get("/dashboard")
        assert "chat-to-cop Dashboard" in r.text

    def test_dashboard_contains_updates_table(self, client):
        r = client.get("/dashboard")
        assert 'id="updates-body"' in r.text

    def test_dashboard_contains_entities_table(self, client):
        r = client.get("/dashboard")
        assert 'id="entities-body"' in r.text

    def test_dashboard_contains_auto_refresh(self, client):
        r = client.get("/dashboard")
        assert "setInterval(refresh, 5000)" in r.text

    def test_dashboard_contains_confidence_classes(self, client):
        r = client.get("/dashboard")
        assert "conf-high" in r.text
        assert "conf-mid" in r.text
        assert "conf-low" in r.text

    def test_dashboard_contains_fetch_calls(self, client):
        r = client.get("/dashboard")
        assert "fetch('/updates" in r.text
        assert "fetch('/entities')" in r.text
        assert "fetch('/health')" in r.text

    def test_root_redirects_to_dashboard(self, client):
        r = client.get("/", follow_redirects=False)
        assert r.status_code == 307
        assert r.headers["location"] == "/dashboard"


class TestStatsEndpoint:
    def test_stats(self, client):
        r = client.get("/stats")
        assert r.status_code == 200
        data = r.json()
        assert "metrics_enabled" in data
        assert "counters" in data

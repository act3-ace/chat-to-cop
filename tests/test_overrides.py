"""Tests for the operator override path (issue #59).

Tests cover:
- Store: record_override, get_overrides, override_stats, get_overridden_update_ids
- API: POST /updates/{id}/override, GET /overrides, GET /overrides/stats
- Dashboard: override button, override badge, override stats display
- Pattern B: original update is preserved after override
"""

import asyncio
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import chat_to_cop.api as api_module
from chat_to_cop.api import app
from chat_to_cop.models.cop_update import CoPUpdate, EntityUpdate, UpdateType
from chat_to_cop.output.store import WorldStateStore


def _make_update(
    update_type: UpdateType = UpdateType.FUEL,
    confidence: float = 0.85,
    channel: str = "#c2_coord",
    speaker: str = "Hydro_Tank",
    message: str = "RR15 F+40",
    track_number: str | None = "TM636",
    callsign: str | None = "RR15",
) -> CoPUpdate:
    entities = []
    if track_number or callsign:
        entities.append(
            EntityUpdate(
                track_number=track_number,
                callsign=callsign,
                fuel_state="F+40" if update_type == UpdateType.FUEL else None,
            )
        )
    return CoPUpdate(
        update_type=update_type,
        confidence=confidence,
        extraction_method="llm",
        entities=entities,
        source_channel=channel,
        source_speaker=speaker,
        source_message=message,
        timestamp=datetime(2025, 9, 23, 14, 10, 15, tzinfo=timezone.utc),
    )


# --- Store tests ---


class TestRecordOverride:
    def test_record_and_retrieve(self):
        async def run():
            async with WorldStateStore(":memory:") as store:
                update_id = await store.write_update(_make_update())
                override_id = await store.record_override(update_id, reason="wrong callsign")
                assert override_id == 1

                overrides = await store.get_overrides(limit=10)
                assert len(overrides) == 1
                o = overrides[0]
                assert o["update_id"] == update_id
                assert o["operator_id"] == "operator"
                assert o["reason"] == "wrong callsign"
                assert o["source_channel"] == "#c2_coord"
                assert o["source_speaker"] == "Hydro_Tank"
                assert o["update_type"] == "fuel"
                assert o["overridden_at"] is not None

        asyncio.run(run())

    def test_override_preserves_original_data(self):
        """Pattern B: original update data is snapshotted into the override record."""

        async def run():
            async with WorldStateStore(":memory:") as store:
                update = _make_update()
                update_id = await store.write_update(update)
                await store.record_override(update_id, reason="bad data")

                overrides = await store.get_overrides()
                assert len(overrides) == 1
                # original_data_json contains the full CoPUpdate
                original = overrides[0]["original_data_json"]
                assert "RR15" in original
                assert "fuel" in original.lower() or "FUEL" in original

                # The original update still exists in the updates table
                updates = await store.get_recent_updates(limit=10)
                assert len(updates) == 1
                assert updates[0].source_message == "RR15 F+40"

        asyncio.run(run())

    def test_override_missing_update_raises(self):
        async def run():
            async with WorldStateStore(":memory:") as store:
                with pytest.raises(ValueError, match="Update 999 not found"):
                    await store.record_override(999, reason="test")

        asyncio.run(run())

    def test_override_empty_reason(self):
        """Operator can override without providing a reason (they're busy)."""

        async def run():
            async with WorldStateStore(":memory:") as store:
                update_id = await store.write_update(_make_update())
                override_id = await store.record_override(update_id)
                assert override_id is not None

                overrides = await store.get_overrides()
                assert overrides[0]["reason"] == ""

        asyncio.run(run())

    def test_multiple_overrides_same_update(self):
        async def run():
            async with WorldStateStore(":memory:") as store:
                update_id = await store.write_update(_make_update())
                await store.record_override(update_id, reason="first")
                await store.record_override(update_id, reason="second")
                overrides = await store.get_overrides()
                assert len(overrides) == 2

        asyncio.run(run())

    def test_override_with_operator_id(self):
        async def run():
            async with WorldStateStore(":memory:") as store:
                update_id = await store.write_update(_make_update())
                await store.record_override(update_id, operator_id="scott_clouse", reason="nope")
                overrides = await store.get_overrides()
                assert overrides[0]["operator_id"] == "scott_clouse"

        asyncio.run(run())


class TestGetOverrides:
    def test_limit(self):
        async def run():
            async with WorldStateStore(":memory:") as store:
                uid = await store.write_update(_make_update())
                for i in range(5):
                    await store.record_override(uid, reason=f"override {i}")
                overrides = await store.get_overrides(limit=3)
                assert len(overrides) == 3

        asyncio.run(run())

    def test_empty(self):
        async def run():
            async with WorldStateStore(":memory:") as store:
                overrides = await store.get_overrides()
                assert overrides == []

        asyncio.run(run())

    def test_order_newest_first(self):
        async def run():
            async with WorldStateStore(":memory:") as store:
                uid = await store.write_update(_make_update())
                await store.record_override(uid, reason="first")
                await store.record_override(uid, reason="second")
                overrides = await store.get_overrides()
                # Both have same overridden_at (datetime('now')), so order by id desc
                assert overrides[0]["reason"] == "second"
                assert overrides[1]["reason"] == "first"

        asyncio.run(run())


class TestGetOverriddenUpdateIds:
    def test_returns_overridden_ids(self):
        async def run():
            async with WorldStateStore(":memory:") as store:
                uid1 = await store.write_update(_make_update())
                uid2 = await store.write_update(_make_update(channel="#isr_reports"))
                await store.record_override(uid1, reason="bad")
                ids = await store.get_overridden_update_ids()
                assert uid1 in ids
                assert uid2 not in ids

        asyncio.run(run())

    def test_empty_when_no_overrides(self):
        async def run():
            async with WorldStateStore(":memory:") as store:
                ids = await store.get_overridden_update_ids()
                assert ids == set()

        asyncio.run(run())


class TestOverrideStats:
    def test_stats_empty(self):
        async def run():
            async with WorldStateStore(":memory:") as store:
                stats = await store.override_stats()
                assert stats["total"] == 0
                assert stats["by_type"] == {}
                assert stats["by_channel"] == {}
                assert stats["by_speaker"] == {}

        asyncio.run(run())

    def test_stats_counts(self):
        async def run():
            async with WorldStateStore(":memory:") as store:
                uid1 = await store.write_update(_make_update(channel="#c2_coord", speaker="Hydro_Tank"))
                uid2 = await store.write_update(
                    _make_update(update_type=UpdateType.THREAT, channel="#isr_reports", speaker="AOC_SIDO")
                )
                await store.record_override(uid1, reason="fix1")
                await store.record_override(uid1, reason="fix2")
                await store.record_override(uid2, reason="fix3")

                stats = await store.override_stats()
                assert stats["total"] == 3
                assert stats["by_type"]["fuel"] == 2
                assert stats["by_type"]["threat"] == 1
                assert stats["by_channel"]["#c2_coord"] == 2
                assert stats["by_channel"]["#isr_reports"] == 1
                assert stats["by_speaker"]["Hydro_Tank"] == 2
                assert stats["by_speaker"]["AOC_SIDO"] == 1

        asyncio.run(run())


# --- API tests ---


@pytest.fixture(autouse=True)
def setup_api_store():
    """Set up an in-memory store with seeded data for API tests."""

    async def _setup():
        store = WorldStateStore(":memory:")
        await store.open()
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


class TestPostOverrideAPI:
    def test_post_override(self, client):
        r = client.post("/updates/1/override", json={"reason": "wrong callsign"})
        assert r.status_code == 200
        data = r.json()
        assert data["update_id"] == 1
        assert data["status"] == "overridden"
        assert "id" in data

    def test_post_override_empty_reason(self, client):
        """Operator can override with no reason (one-click under stress)."""
        r = client.post("/updates/1/override", json={"reason": ""})
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "overridden"

    def test_post_override_no_body(self, client):
        """Override with no JSON body at all should still work."""
        r = client.post("/updates/1/override")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "overridden"

    def test_post_override_missing_update(self, client):
        r = client.post("/updates/999/override", json={"reason": "nope"})
        assert r.status_code == 404
        assert "not found" in r.json()["error"]

    def test_override_preserves_original_in_updates(self, client):
        """The original update is NOT deleted after override (Pattern B)."""
        client.post("/updates/1/override", json={"reason": "bad"})
        r = client.get("/updates")
        assert r.status_code == 200
        updates = r.json()
        # Both original updates still exist
        assert len(updates) == 2
        ids = [u["id"] for u in updates]
        assert 1 in ids


class TestGetOverridesAPI:
    def test_get_overrides_empty(self, client):
        r = client.get("/overrides")
        assert r.status_code == 200
        assert r.json() == []

    def test_get_overrides_after_post(self, client):
        client.post("/updates/1/override", json={"reason": "test override"})
        r = client.get("/overrides")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1
        assert data[0]["reason"] == "test override"
        assert data[0]["source_channel"] == "#c2_coord"
        assert data[0]["update_type"] == "fuel"

    def test_get_overrides_limit(self, client):
        for i in range(5):
            client.post("/updates/1/override", json={"reason": f"override {i}"})
        r = client.get("/overrides?limit=3")
        assert r.status_code == 200
        assert len(r.json()) == 3


class TestOverrideStatsAPI:
    def test_stats_empty(self, client):
        r = client.get("/overrides/stats")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 0

    def test_stats_after_overrides(self, client):
        client.post("/updates/1/override", json={"reason": "fix1"})
        client.post("/updates/2/override", json={"reason": "fix2"})
        r = client.get("/overrides/stats")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 2
        assert "by_type" in data
        assert "by_channel" in data
        assert "by_speaker" in data


class TestDashboardOverrides:
    def test_dashboard_has_override_button(self, client):
        r = client.get("/dashboard")
        assert r.status_code == 200
        assert "override-btn" in r.text
        assert "WRONG" in r.text

    def test_dashboard_has_override_badge(self, client):
        r = client.get("/dashboard")
        assert "overridden-badge" in r.text
        assert "OVERRIDDEN" in r.text

    def test_dashboard_has_override_stats(self, client):
        r = client.get("/dashboard")
        assert "stat-overrides" in r.text

    def test_dashboard_fetches_overrides(self, client):
        r = client.get("/dashboard")
        assert "fetch('/overrides')" in r.text

    def test_dashboard_has_reason_input(self, client):
        r = client.get("/dashboard")
        assert "override-reason-input" in r.text
        assert "why? (Enter to skip)" in r.text

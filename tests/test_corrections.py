"""Tests for the operator feedback loop (corrections)."""

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


class TestWriteCorrection:
    def test_write_and_retrieve(self):
        async def run():
            async with WorldStateStore(":memory:") as store:
                update_id = await store.write_update(_make_update())
                correction_id = await store.write_correction(
                    update_id,
                    {
                        "corrected_type": "threat",
                        "corrected_entities": [{"callsign": "RR15", "affiliation": "HOSTILE"}],
                        "rejected": False,
                        "notes": "This was a drill",
                        "corrector": "scott_clouse",
                    },
                )
                assert correction_id == 1

                corrections = await store.get_corrections(limit=10)
                assert len(corrections) == 1
                c = corrections[0]
                assert c["update_id"] == update_id
                assert c["corrected_type"] == "threat"
                assert c["corrected_entities"] == [{"callsign": "RR15", "affiliation": "HOSTILE"}]
                assert c["rejected"] is False
                assert c["notes"] == "This was a drill"
                assert c["corrector"] == "scott_clouse"
                assert c["source_channel"] == "#c2_coord"
                assert c["original_type"] == "fuel"

        asyncio.run(run())

    def test_write_correction_missing_update_raises(self):
        async def run():
            async with WorldStateStore(":memory:") as store:
                with pytest.raises(ValueError, match="Update 999 not found"):
                    await store.write_correction(999, {"notes": "test"})

        asyncio.run(run())

    def test_write_correction_rejected(self):
        async def run():
            async with WorldStateStore(":memory:") as store:
                update_id = await store.write_update(_make_update())
                await store.write_correction(update_id, {"rejected": True, "notes": "bad extraction"})
                corrections = await store.get_corrections()
                assert corrections[0]["rejected"] is True

        asyncio.run(run())

    def test_write_correction_minimal(self):
        async def run():
            async with WorldStateStore(":memory:") as store:
                update_id = await store.write_update(_make_update())
                cid = await store.write_correction(update_id, {"notes": "just a note"})
                assert cid is not None
                corrections = await store.get_corrections()
                assert len(corrections) == 1
                assert corrections[0]["corrected_type"] is None
                assert corrections[0]["corrected_entities"] is None

        asyncio.run(run())

    def test_multiple_corrections_same_update(self):
        async def run():
            async with WorldStateStore(":memory:") as store:
                update_id = await store.write_update(_make_update())
                await store.write_correction(update_id, {"notes": "first"})
                await store.write_correction(update_id, {"notes": "second"})
                corrections = await store.get_corrections()
                assert len(corrections) == 2

        asyncio.run(run())


class TestGetCorrections:
    def test_limit(self):
        async def run():
            async with WorldStateStore(":memory:") as store:
                uid = await store.write_update(_make_update())
                for i in range(5):
                    await store.write_correction(uid, {"notes": f"correction {i}"})
                corrections = await store.get_corrections(limit=3)
                assert len(corrections) == 3

        asyncio.run(run())

    def test_empty(self):
        async def run():
            async with WorldStateStore(":memory:") as store:
                corrections = await store.get_corrections()
                assert corrections == []

        asyncio.run(run())

    def test_order_newest_first(self):
        async def run():
            async with WorldStateStore(":memory:") as store:
                uid = await store.write_update(_make_update())
                await store.write_correction(uid, {"notes": "first"})
                await store.write_correction(uid, {"notes": "second"})
                corrections = await store.get_corrections()
                assert corrections[0]["notes"] == "second"
                assert corrections[1]["notes"] == "first"

        asyncio.run(run())


class TestCorrectionStats:
    def test_stats_empty(self):
        async def run():
            async with WorldStateStore(":memory:") as store:
                stats = await store.correction_stats()
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
                    _make_update(
                        update_type=UpdateType.THREAT,
                        channel="#isr_reports",
                        speaker="AOC_SIDO",
                    )
                )
                await store.write_correction(uid1, {"notes": "fix1"})
                await store.write_correction(uid1, {"notes": "fix2"})
                await store.write_correction(uid2, {"notes": "fix3"})

                stats = await store.correction_stats()
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


class TestPostCorrectionAPI:
    def test_post_correction(self, client):
        r = client.post(
            "/updates/1/correct",
            json={"corrected_type": "threat", "notes": "was actually a threat", "corrector": "scott"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["update_id"] == 1
        assert data["status"] == "recorded"
        assert "id" in data

    def test_post_correction_rejected(self, client):
        r = client.post("/updates/1/correct", json={"rejected": True, "notes": "bad extraction"})
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "recorded"

    def test_post_correction_missing_update(self, client):
        r = client.post("/updates/999/correct", json={"notes": "this should 404"})
        assert r.status_code == 404
        assert "not found" in r.json()["error"]

    def test_post_correction_empty_payload(self, client):
        r = client.post("/updates/1/correct", json={})
        assert r.status_code == 422  # Pydantic validation error

    def test_post_correction_only_corrector_rejected(self, client):
        """Providing only corrector (no type/rejected/notes) should fail validation."""
        r = client.post("/updates/1/correct", json={"corrector": "someone"})
        assert r.status_code == 422


class TestGetCorrectionsAPI:
    def test_get_corrections_empty(self, client):
        r = client.get("/corrections")
        assert r.status_code == 200
        assert r.json() == []

    def test_get_corrections_after_post(self, client):
        client.post("/updates/1/correct", json={"notes": "test correction"})
        r = client.get("/corrections")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1
        assert data[0]["notes"] == "test correction"
        assert data[0]["source_channel"] == "#c2_coord"

    def test_get_corrections_limit(self, client):
        for i in range(5):
            client.post("/updates/1/correct", json={"notes": f"correction {i}"})
        r = client.get("/corrections?limit=3")
        assert r.status_code == 200
        assert len(r.json()) == 3


class TestCorrectionStatsAPI:
    def test_stats_empty(self, client):
        r = client.get("/corrections/stats")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 0

    def test_stats_after_corrections(self, client):
        client.post("/updates/1/correct", json={"notes": "fix1"})
        client.post("/updates/2/correct", json={"notes": "fix2"})
        r = client.get("/corrections/stats")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 2
        assert "by_type" in data
        assert "by_channel" in data
        assert "by_speaker" in data


class TestUpdatesIncludeIds:
    def test_updates_have_id_field(self, client):
        r = client.get("/updates")
        assert r.status_code == 200
        updates = r.json()
        assert len(updates) == 2
        for u in updates:
            assert "id" in u
            assert isinstance(u["id"], int)


class TestDashboardCorrections:
    def test_dashboard_has_correct_button(self, client):
        r = client.get("/dashboard")
        assert r.status_code == 200
        assert "correct-btn" in r.text
        assert "openModal" in r.text

    def test_dashboard_has_correction_modal(self, client):
        r = client.get("/dashboard")
        assert "correct-modal" in r.text
        assert "submitCorrection" in r.text

    def test_dashboard_has_correction_stats(self, client):
        r = client.get("/dashboard")
        assert "stat-corrections" in r.text

    def test_dashboard_fetches_correction_stats(self, client):
        r = client.get("/dashboard")
        assert "fetch('/corrections/stats')" in r.text

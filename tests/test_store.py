"""Tests for the SQLite world state store."""

import asyncio
from datetime import datetime, timezone

import pytest

from chat_to_cop.models.cop_update import CoPUpdate, EntityUpdate, UpdateType
from chat_to_cop.models.messages import IRCMessage
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
    """Helper to create a CoPUpdate for testing."""
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


def _make_message(
    channel: str = "#c2_coord",
    sender: str = "Hydro_Tank",
    content: str = "RR15 F+40, RL36 F+50",
) -> IRCMessage:
    return IRCMessage(
        timestamp=datetime(2025, 9, 23, 14, 10, 15, tzinfo=timezone.utc),
        channel=channel,
        sender=sender,
        content=content,
    )


class TestStoreLifecycle:
    """Test open/close and context manager."""

    def test_context_manager(self):
        async def run():
            async with WorldStateStore(":memory:") as store:
                assert store.db is not None
            assert store._db is None

        asyncio.run(run())

    def test_not_opened_raises(self):
        store = WorldStateStore(":memory:")
        with pytest.raises(RuntimeError, match="not opened"):
            _ = store.db


class TestWriteUpdate:
    """Test writing CoPUpdates."""

    def test_write_and_count(self):
        async def run():
            async with WorldStateStore(":memory:") as store:
                row_id = await store.write_update(_make_update())
                assert row_id == 1
                assert await store.count_updates() == 1

        asyncio.run(run())

    def test_write_multiple(self):
        async def run():
            async with WorldStateStore(":memory:") as store:
                await store.write_update(_make_update(track_number="TM636"))
                await store.write_update(_make_update(track_number="TM637"))
                await store.write_update(_make_update(track_number="TM638"))
                assert await store.count_updates() == 3

        asyncio.run(run())

    def test_write_creates_entity(self):
        async def run():
            async with WorldStateStore(":memory:") as store:
                await store.write_update(
                    _make_update(
                        update_type=UpdateType.ENTITY_ID,
                        track_number="44504",
                        callsign="DDG1",
                    )
                )
                assert await store.count_entities() == 1
                entity = await store.get_entity("44504")
                assert entity is not None
                assert entity.callsign == "DDG1"

        asyncio.run(run())

    def test_upsert_entity_updates_fields(self):
        """Second update to same entity should merge, not duplicate."""

        async def run():
            async with WorldStateStore(":memory:") as store:
                # First: identify entity
                await store.write_update(
                    _make_update(
                        update_type=UpdateType.ENTITY_ID,
                        track_number="44504",
                        callsign=None,
                    )
                )
                # Second: add callsign
                update2 = _make_update(
                    update_type=UpdateType.ENTITY_ID,
                    track_number="44504",
                    callsign="DDG1",
                )
                await store.write_update(update2)

                assert await store.count_entities() == 1
                entity = await store.get_entity("44504")
                assert entity is not None
                assert entity.callsign == "DDG1"

        asyncio.run(run())

    def test_write_update_no_entities(self):
        """Updates with no entities (e.g., environmental) still persist."""

        async def run():
            async with WorldStateStore(":memory:") as store:
                update = _make_update(
                    update_type=UpdateType.ENVIRONMENTAL,
                    track_number=None,
                    callsign=None,
                )
                row_id = await store.write_update(update)
                assert row_id == 1
                assert await store.count_updates() == 1
                assert await store.count_entities() == 0

        asyncio.run(run())


class TestWriteAudit:
    """Test audit log writing."""

    def test_write_audit(self):
        async def run():
            async with WorldStateStore(":memory:") as store:
                await store.write_audit(_make_message())
                cursor = await store.db.execute("SELECT COUNT(*) as cnt FROM audit_log")
                row = await cursor.fetchone()
                assert row["cnt"] == 1

        asyncio.run(run())

    def test_audit_preserves_fields(self):
        async def run():
            async with WorldStateStore(":memory:") as store:
                msg = _make_message(channel="#fires", sender="HYDRO_Strike", content="fire!")
                await store.write_audit(msg)
                cursor = await store.db.execute("SELECT * FROM audit_log")
                row = await cursor.fetchone()
                assert row["channel"] == "#fires"
                assert row["sender"] == "HYDRO_Strike"
                assert row["content"] == "fire!"

        asyncio.run(run())


class TestGetEntity:
    """Test entity lookup."""

    def test_get_missing_entity(self):
        async def run():
            async with WorldStateStore(":memory:") as store:
                result = await store.get_entity("NONEXISTENT")
                assert result is None

        asyncio.run(run())

    def test_get_entity_by_callsign(self):
        async def run():
            async with WorldStateStore(":memory:") as store:
                await store.write_update(_make_update(track_number=None, callsign="ORCA01"))
                entity = await store.get_entity("ORCA01")
                assert entity is not None
                assert entity.callsign == "ORCA01"

        asyncio.run(run())


class TestGetRecentUpdates:
    """Test update retrieval with filtering."""

    def test_get_all_recent(self):
        async def run():
            async with WorldStateStore(":memory:") as store:
                for i in range(5):
                    await store.write_update(_make_update(track_number=f"TM{i:03d}"))
                updates = await store.get_recent_updates()
                assert len(updates) == 5

        asyncio.run(run())

    def test_filter_by_channel(self):
        async def run():
            async with WorldStateStore(":memory:") as store:
                await store.write_update(_make_update(channel="#c2_coord"))
                await store.write_update(_make_update(channel="#fires"))
                await store.write_update(_make_update(channel="#c2_coord"))

                c2_updates = await store.get_recent_updates(channel="#c2_coord")
                assert len(c2_updates) == 2

                fires_updates = await store.get_recent_updates(channel="#fires")
                assert len(fires_updates) == 1

        asyncio.run(run())

    def test_limit(self):
        async def run():
            async with WorldStateStore(":memory:") as store:
                for i in range(10):
                    await store.write_update(_make_update(track_number=f"TM{i:03d}"))
                updates = await store.get_recent_updates(limit=3)
                assert len(updates) == 3

        asyncio.run(run())

    def test_roundtrip_preserves_data(self):
        """CoPUpdate serialized to DB and back should be equivalent."""

        async def run():
            async with WorldStateStore(":memory:") as store:
                original = _make_update()
                await store.write_update(original)
                updates = await store.get_recent_updates()
                assert len(updates) == 1
                restored = updates[0]
                assert restored.update_type == original.update_type
                assert restored.confidence == original.confidence
                assert restored.source_channel == original.source_channel
                assert restored.source_speaker == original.source_speaker
                assert restored.source_message == original.source_message
                assert len(restored.entities) == len(original.entities)

        asyncio.run(run())

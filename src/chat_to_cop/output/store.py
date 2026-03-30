"""World state store: SQLite-backed persistence for CoP updates.

SQLite for portability (zero-config, ships with Python, works air-gapped).
Stores CoPUpdates with full provenance, raw message audit log, and an
entity view derived from the latest updates.

Uses aiosqlite for async access.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import aiosqlite

from chat_to_cop.metrics import metrics
from chat_to_cop.models.cop_update import CoPUpdate
from chat_to_cop.models.messages import IRCMessage
from chat_to_cop.models.speaker import SpeakerModel
from chat_to_cop.models.world_state import TrackedEntity

_SCHEMA = """
CREATE TABLE IF NOT EXISTS updates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    source_channel TEXT NOT NULL,
    source_speaker TEXT NOT NULL,
    source_message TEXT NOT NULL,
    update_type TEXT NOT NULL,
    confidence REAL NOT NULL,
    extraction_method TEXT NOT NULL,
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_updates_timestamp ON updates(timestamp);
CREATE INDEX IF NOT EXISTS idx_updates_channel ON updates(source_channel);
CREATE INDEX IF NOT EXISTS idx_updates_type ON updates(update_type);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    channel TEXT NOT NULL,
    sender TEXT NOT NULL,
    content TEXT NOT NULL,
    raw_line TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_channel ON audit_log(channel);

CREATE TABLE IF NOT EXISTS speakers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    channel TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    message_count INTEGER NOT NULL DEFAULT 0,
    data_json TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(username, channel)
);

CREATE INDEX IF NOT EXISTS idx_speakers_channel ON speakers(channel);

CREATE TABLE IF NOT EXISTS corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    update_id INTEGER NOT NULL,
    corrected_type TEXT,
    corrected_entities TEXT,
    rejected INTEGER NOT NULL DEFAULT 0,
    notes TEXT,
    corrector TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (update_id) REFERENCES updates(id)
);

CREATE INDEX IF NOT EXISTS idx_corrections_update_id ON corrections(update_id);
CREATE INDEX IF NOT EXISTS idx_corrections_created_at ON corrections(created_at);

CREATE TABLE IF NOT EXISTS entities (
    entity_key TEXT PRIMARY KEY,
    track_number TEXT,
    callsign TEXT,
    platform_type TEXT,
    affiliation TEXT,
    operational_status TEXT,
    last_known_lat REAL,
    last_known_lon REAL,
    last_updated TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
"""


class WorldStateStore:
    """Async SQLite store for CoP updates, audit log, and entity state."""

    def __init__(self, db_path: str | Path = "data/world_state.db") -> None:
        self.db_path = str(db_path)
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        """Open the database and create schema if needed."""
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(_SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        """Close the database connection."""
        if self._db:
            await self._db.close()
            self._db = None

    async def __aenter__(self) -> WorldStateStore:
        await self.open()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Store not opened. Use 'async with store:' or call store.open().")
        return self._db

    async def write_update(self, update: CoPUpdate) -> int:
        """Persist a CoPUpdate and update entity state. Returns the row ID."""
        async with metrics.async_timer("store_write_update_seconds"):
            cursor = await self.db.execute(
                """INSERT INTO updates
                   (timestamp, source_channel, source_speaker, source_message,
                    update_type, confidence, extraction_method, data_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    update.timestamp.isoformat(),
                    update.source_channel,
                    update.source_speaker,
                    update.source_message,
                    update.update_type.value,
                    update.confidence,
                    update.extraction_method,
                    update.model_dump_json(),
                ),
            )
            await self.db.commit()
            metrics.inc("store_updates_written_total")

            # Update entity table from extracted entities
            for entity in update.entities:
                await self._upsert_entity(entity, update.timestamp)

            return cursor.lastrowid  # type: ignore[return-value]

    async def _upsert_entity(self, entity, timestamp: datetime) -> None:
        """Insert or update an entity from an EntityUpdate."""
        key = entity.track_number or entity.callsign
        if not key:
            return

        await self.db.execute(
            """INSERT INTO entities
               (entity_key, track_number, callsign, platform_type, affiliation,
                operational_status, last_known_lat, last_known_lon, last_updated, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(entity_key) DO UPDATE SET
                track_number = COALESCE(excluded.track_number, entities.track_number),
                callsign = COALESCE(excluded.callsign, entities.callsign),
                platform_type = COALESCE(excluded.platform_type, entities.platform_type),
                affiliation = COALESCE(excluded.affiliation, entities.affiliation),
                operational_status = COALESCE(excluded.operational_status, entities.operational_status),
                last_known_lat = COALESCE(excluded.last_known_lat, entities.last_known_lat),
                last_known_lon = COALESCE(excluded.last_known_lon, entities.last_known_lon),
                last_updated = excluded.last_updated,
                metadata_json = excluded.metadata_json
            """,
            (
                key,
                entity.track_number,
                entity.callsign,
                entity.platform_type,
                entity.affiliation,
                entity.operational_status,
                entity.latitude,
                entity.longitude,
                timestamp.isoformat(),
                str(entity.metadata) if entity.metadata else "{}",
            ),
        )
        await self.db.commit()

    async def write_audit(self, message: IRCMessage) -> None:
        """Log a raw message to the audit trail."""
        await self.db.execute(
            """INSERT INTO audit_log (timestamp, channel, sender, content, raw_line)
               VALUES (?, ?, ?, ?, ?)""",
            (
                message.timestamp.isoformat(),
                message.channel,
                message.sender,
                message.content,
                message.raw_line,
            ),
        )
        await self.db.commit()
        metrics.inc("store_audit_written_total")

    async def get_entity(self, key: str) -> TrackedEntity | None:
        """Look up an entity by track number or callsign."""
        cursor = await self.db.execute("SELECT * FROM entities WHERE entity_key = ?", (key,))
        row = await cursor.fetchone()
        if row is None:
            return None
        return TrackedEntity(
            track_number=row["track_number"],
            callsign=row["callsign"],
            platform_type=row["platform_type"],
            affiliation=row["affiliation"],
            operational_status=row["operational_status"],
            last_known_lat=row["last_known_lat"],
            last_known_lon=row["last_known_lon"],
            last_updated=datetime.fromisoformat(row["last_updated"]) if row["last_updated"] else None,
        )

    async def get_recent_updates(
        self,
        channel: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[CoPUpdate]:
        """Retrieve recent CoPUpdates with optional filtering."""
        query = "SELECT data_json FROM updates WHERE 1=1"
        params: list = []

        if channel:
            query += " AND source_channel = ?"
            params.append(channel)
        if since:
            query += " AND timestamp >= ?"
            params.append(since.isoformat())

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        cursor = await self.db.execute(query, params)
        rows = await cursor.fetchall()
        return [CoPUpdate.model_validate_json(row["data_json"]) for row in rows]

    async def get_recent_updates_with_ids(
        self,
        channel: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Like get_recent_updates but includes the database row ID for each update."""
        query = "SELECT id, data_json FROM updates WHERE 1=1"
        params: list = []

        if channel:
            query += " AND source_channel = ?"
            params.append(channel)
        if since:
            query += " AND timestamp >= ?"
            params.append(since.isoformat())

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        cursor = await self.db.execute(query, params)
        rows = await cursor.fetchall()
        results = []
        for row in rows:
            update = CoPUpdate.model_validate_json(row["data_json"])
            d = update.model_dump(mode="json")
            d["id"] = row["id"]
            results.append(d)
        return results

    async def count_updates(self) -> int:
        """Return total number of updates stored."""
        cursor = await self.db.execute("SELECT COUNT(*) as cnt FROM updates")
        row = await cursor.fetchone()
        return row["cnt"]  # type: ignore[index]

    async def count_entities(self) -> int:
        """Return total number of tracked entities."""
        cursor = await self.db.execute("SELECT COUNT(*) as cnt FROM entities")
        row = await cursor.fetchone()
        return row["cnt"]  # type: ignore[index]

    async def write_speaker(self, model: SpeakerModel, channel: str) -> None:
        """Persist a speaker model (upsert by username + channel)."""
        await self.db.execute(
            """INSERT INTO speakers (username, channel, first_seen, last_seen, message_count, data_json)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(username, channel) DO UPDATE SET
                last_seen = excluded.last_seen,
                message_count = excluded.message_count,
                data_json = excluded.data_json,
                updated_at = datetime('now')
            """,
            (
                model.username,
                channel,
                model.first_seen.isoformat(),
                model.last_seen.isoformat(),
                model.message_count,
                model.model_dump_json(),
            ),
        )
        await self.db.commit()

    async def get_speaker(self, username: str, channel: str) -> SpeakerModel | None:
        """Load a speaker model by username and channel."""
        cursor = await self.db.execute(
            "SELECT data_json FROM speakers WHERE username = ? AND channel = ?",
            (username, channel),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return SpeakerModel.model_validate_json(row["data_json"])

    async def get_speakers(self, channel: str) -> list[SpeakerModel]:
        """Load all speaker models for a channel."""
        cursor = await self.db.execute(
            "SELECT data_json FROM speakers WHERE channel = ? ORDER BY message_count DESC",
            (channel,),
        )
        rows = await cursor.fetchall()
        return [SpeakerModel.model_validate_json(row["data_json"]) for row in rows]

    async def write_correction(self, update_id: int, correction: dict) -> int:
        """Store a correction for an existing update. Returns the correction row ID."""
        # Verify the update exists
        cursor = await self.db.execute("SELECT id FROM updates WHERE id = ?", (update_id,))
        row = await cursor.fetchone()
        if row is None:
            raise ValueError(f"Update {update_id} not found")

        corrected_entities = correction.get("corrected_entities")
        entities_json = json.dumps(corrected_entities) if corrected_entities is not None else None

        cursor = await self.db.execute(
            """INSERT INTO corrections
               (update_id, corrected_type, corrected_entities, rejected, notes, corrector)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                update_id,
                correction.get("corrected_type"),
                entities_json,
                1 if correction.get("rejected") else 0,
                correction.get("notes"),
                correction.get("corrector"),
            ),
        )
        await self.db.commit()
        metrics.inc("store_corrections_written_total")
        return cursor.lastrowid  # type: ignore[return-value]

    async def get_corrections(self, limit: int = 50) -> list[dict]:
        """Return recent corrections with their associated update info."""
        cursor = await self.db.execute(
            """SELECT c.id, c.update_id, c.corrected_type, c.corrected_entities,
                      c.rejected, c.notes, c.corrector, c.created_at,
                      u.source_channel, u.source_speaker, u.update_type
               FROM corrections c
               JOIN updates u ON c.update_id = u.id
               ORDER BY c.created_at DESC
               LIMIT ?""",
            (limit,),
        )
        rows = await cursor.fetchall()
        results = []
        for row in rows:
            entities = json.loads(row["corrected_entities"]) if row["corrected_entities"] else None
            results.append(
                {
                    "id": row["id"],
                    "update_id": row["update_id"],
                    "corrected_type": row["corrected_type"],
                    "corrected_entities": entities,
                    "rejected": bool(row["rejected"]),
                    "notes": row["notes"],
                    "corrector": row["corrector"],
                    "created_at": row["created_at"],
                    "source_channel": row["source_channel"],
                    "source_speaker": row["source_speaker"],
                    "original_type": row["update_type"],
                }
            )
        return results

    async def correction_stats(self) -> dict:
        """Return correction counts grouped by type, channel, and speaker."""
        stats: dict = {"total": 0, "by_type": {}, "by_channel": {}, "by_speaker": {}}

        cursor = await self.db.execute("SELECT COUNT(*) as cnt FROM corrections")
        row = await cursor.fetchone()
        stats["total"] = row["cnt"]  # type: ignore[index]

        cursor = await self.db.execute(
            """SELECT u.update_type, COUNT(*) as cnt
               FROM corrections c JOIN updates u ON c.update_id = u.id
               GROUP BY u.update_type ORDER BY cnt DESC"""
        )
        for row in await cursor.fetchall():
            stats["by_type"][row["update_type"]] = row["cnt"]

        cursor = await self.db.execute(
            """SELECT u.source_channel, COUNT(*) as cnt
               FROM corrections c JOIN updates u ON c.update_id = u.id
               GROUP BY u.source_channel ORDER BY cnt DESC"""
        )
        for row in await cursor.fetchall():
            stats["by_channel"][row["source_channel"]] = row["cnt"]

        cursor = await self.db.execute(
            """SELECT u.source_speaker, COUNT(*) as cnt
               FROM corrections c JOIN updates u ON c.update_id = u.id
               GROUP BY u.source_speaker ORDER BY cnt DESC"""
        )
        for row in await cursor.fetchall():
            stats["by_speaker"][row["source_speaker"]] = row["cnt"]

        return stats

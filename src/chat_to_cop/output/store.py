"""World state store: SQLite-backed persistence for CoP updates.

SQLite for portability (zero-config, ships with Python, works air-gapped).
Can be swapped for PostgreSQL in production without changing application code.

Stores:
- CoP updates with full provenance (agent, backend, confidence, source)
- Speaker models (serialized, queryable by channel agents)
- Audit log (every raw message, whether processed or not)
"""

from __future__ import annotations


# TODO: Implement WorldStateStore
# - __init__(db_path="data/world_state.db")
# - async write_update(update: CoPUpdate)
# - async get_entity(track_number_or_callsign) -> TrackedEntity
# - async get_recent_updates(channel, since) -> list[CoPUpdate]
# - async write_audit(raw_message)
# - Schema creation on first run

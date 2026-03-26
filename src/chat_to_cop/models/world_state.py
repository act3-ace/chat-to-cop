"""World state snapshot: agent's current belief about the battlespace.

Included in the agent's LLM prompt so it can produce updates relative
to known state (e.g., "4 more launched" when current inventory is known).

This is a lightweight in-memory representation, not the full CoP database.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class TrackedEntity(BaseModel):
    """An entity the agent believes exists in the battlespace."""

    track_number: str | None = None
    callsign: str | None = None
    platform_type: str | None = None
    affiliation: str | None = None
    operational_status: str | None = None
    last_known_lat: float | None = None
    last_known_lon: float | None = None
    last_updated: datetime | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


class WorldStateSnapshot(BaseModel):
    """Agent's current belief about the battlespace."""

    entities: dict[str, TrackedEntity] = Field(
        default_factory=dict,
        description="Keyed by track_number or callsign",
    )
    last_updated: datetime | None = None

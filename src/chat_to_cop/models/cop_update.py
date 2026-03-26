"""CoP update models: structured output from agent extraction.

These Pydantic models are passed to the LLM via `instructor` for
structured output. The LLM fills in the fields; Pydantic validates.

Design: map to known CoP fields where possible, overflow to metadata dict.
Never lose data just because we can't categorize it.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class UpdateType(str, Enum):
    """Types of world-state updates extractable from chat."""

    ENTITY_ID = "entity_id"
    STATUS_CHANGE = "status_change"
    WEAPONS = "weapons"
    FUEL = "fuel"
    LOCATION = "location"
    THREAT = "threat"
    TASKING = "tasking"
    HANDOVER = "handover"
    CSAR = "csar"
    FIRE_MISSION = "fire_mission"
    CYBER_EW = "cyber_ew"
    ENVIRONMENTAL = "environmental"
    SITREP = "sitrep"
    NONE = "none"


class EntityUpdate(BaseModel):
    """A single entity state change."""

    track_number: str | None = Field(None, description="Track number (e.g., TM636, 44504)")
    callsign: str | None = Field(None, description="Platform callsign (e.g., ORCA01, HADES31)")
    platform_type: str | None = Field(None, description="Platform type (e.g., E-2D, DDG, F-35)")
    affiliation: str | None = Field(None, description="FRIEND, HOSTILE, NEUTRAL, UNKNOWN")
    operational_status: str | None = Field(None, description="OPERATIONAL, DEGRADED, INOP, RTB, DESTROYED")
    latitude: float | None = Field(None, description="Latitude in decimal degrees")
    longitude: float | None = Field(None, description="Longitude in decimal degrees")
    bearing: float | None = Field(None, description="Bearing from bullseye (degrees)")
    range_nm: float | None = Field(None, description="Range from bullseye (nautical miles)")
    altitude_ft: float | None = Field(None, description="Altitude in feet")
    speed_kts: float | None = Field(None, description="Speed in knots")
    heading: float | None = Field(None, description="Heading in degrees")
    weapon_type: str | None = Field(None, description="Weapon type (e.g., SM6, TLAM, AMRAAM)")
    weapon_qty_launched: int | None = Field(None, description="Weapons launched")
    weapon_qty_remaining: int | None = Field(None, description="Weapons remaining")
    fuel_state: str | None = Field(None, description="Fuel state (e.g., F+40, playtime 15 min)")
    subsystem_status: str | None = Field(None, description="Subsystem detail (e.g., radar inop, CIWS out)")
    metadata: dict[str, str] = Field(default_factory=dict, description="Overflow fields that don't map to schema")


class CoPUpdate(BaseModel):
    """A structured world-state update extracted from chat."""

    update_type: UpdateType
    confidence: float = Field(ge=0.0, le=1.0)
    extraction_method: str = Field(description="'llm', 'regex', or 'passthrough'")
    entities: list[EntityUpdate] = Field(default_factory=list)
    source_channel: str
    source_speaker: str
    source_message: str
    timestamp: datetime
    context_messages: list[str] = Field(
        default_factory=list,
        description="Surrounding conversation for audit trail",
    )
    reasoning: str | None = Field(None, description="LLM's explanation of the extraction")

"""CoP update models: structured output from agent extraction.

These Pydantic models are passed to the LLM via `instructor` for
structured output. The LLM fills in the fields; Pydantic validates.

Design: map to known CoP fields where possible, overflow to metadata dict.
Never lose data just because we can't categorize it.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, Field, model_validator


class CapabilityImpact(str, Enum):
    """SDAC capability mapping for entity updates."""

    SENSE = "sense"
    DECIDE = "decide"
    ACT = "act"
    COLLABORATE = "collaborate"


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


def _coerce_metadata_values(v: object) -> dict[str, str]:
    """Coerce metadata dict values to strings.

    LLMs sometimes return int or None values in metadata. Rather than
    rejecting the entire entity, coerce to strings and drop None values.
    """
    if not isinstance(v, dict):
        return {}
    return {str(k): str(val) for k, val in v.items() if val is not None}


def _coerce_update_type(v: object) -> object:
    """Coerce invalid update_type values to 'none' instead of failing.

    LLMs sometimes return invented types like 'message_data' or
    'communication' that aren't in the UpdateType enum.
    """
    if v is None:
        return "none"
    if isinstance(v, UpdateType):
        return v
    if isinstance(v, str):
        valid = {e.value for e in UpdateType}
        if v.lower() in valid:
            return v.lower()
        return "none"
    return "none"


_WORD_TO_CONFIDENCE = {
    "high": 0.9,
    "very high": 0.95,
    "medium": 0.7,
    "moderate": 0.7,
    "low": 0.3,
    "very low": 0.1,
    "none": 0.0,
    "unknown": 0.5,
}


def _coerce_confidence(v: object) -> float:
    """Coerce confidence values that LLMs commonly hallucinate.

    Handles: percentages (85 -> 0.85), word labels ("high" -> 0.9),
    out-of-range values (clamped to 0.0-1.0), None (-> 0.0).
    """
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        if v > 1.0:
            return min(v / 100.0, 1.0)
        return max(0.0, min(float(v), 1.0))
    if isinstance(v, str):
        low = v.strip().lower().rstrip("%")
        if low in _WORD_TO_CONFIDENCE:
            return _WORD_TO_CONFIDENCE[low]
        try:
            num = float(low)
            if num > 1.0:
                return min(num / 100.0, 1.0)
            return max(0.0, min(num, 1.0))
        except ValueError:
            return 0.0
    return 0.0


def _coerce_capability_impact(v: object) -> object:
    """Coerce invalid capability_impact values to None instead of failing.

    LLMs sometimes return values like "tasking", "extraction", etc.
    that aren't valid SDAC categories. Rather than rejecting the entire
    entity update, we null out the field — the channel agent can fill
    it post-extraction if needed.
    """
    if v is None:
        return v
    if isinstance(v, CapabilityImpact):
        return v
    if isinstance(v, str):
        valid = {e.value for e in CapabilityImpact}
        if v.lower() not in valid:
            return None
    return v


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
    metadata: Annotated[dict[str, str], BeforeValidator(_coerce_metadata_values)] = Field(
        default_factory=dict, description="Overflow fields that don't map to schema"
    )
    capability_impact: Annotated[CapabilityImpact | None, BeforeValidator(_coerce_capability_impact)] = Field(
        None, description="SDAC mapping: sense, decide, act, or collaborate"
    )

    @model_validator(mode="before")
    @classmethod
    def parse_bullseye_bearing(cls, data: object) -> object:
        """Parse bullseye notation (e.g. '240/405') in bearing field.

        LLMs sometimes return bearing as a bullseye string like '240/405'.
        This extracts the bearing (first number) as a float and stores the
        range portion in metadata['bullseye_range'] if present.
        """
        if not isinstance(data, dict):
            return data
        bearing = data.get("bearing")
        if bearing is None or isinstance(bearing, (int, float)):
            return data
        if isinstance(bearing, str):
            bearing = bearing.strip()
            if "/" in bearing:
                parts = bearing.split("/", 1)
                try:
                    data["bearing"] = float(parts[0])
                except ValueError:
                    data["bearing"] = None
                    return data
                try:
                    range_val = float(parts[1])
                    metadata = data.get("metadata") or {}
                    metadata["bullseye_range"] = str(range_val)
                    data["metadata"] = metadata
                    # Also set range_nm if not already provided
                    if data.get("range_nm") is None:
                        data["range_nm"] = range_val
                except (ValueError, IndexError):
                    pass
            else:
                try:
                    data["bearing"] = float(bearing)
                except ValueError:
                    data["bearing"] = None
        return data


class CoPUpdate(BaseModel):
    """A structured world-state update extracted from chat."""

    update_type: Annotated[UpdateType, BeforeValidator(_coerce_update_type)]
    confidence: Annotated[float, BeforeValidator(_coerce_confidence)] = Field(ge=0.0, le=1.0)
    extraction_method: str = Field(default="llm", description="'llm', 'regex', or 'passthrough'")
    entities: list[EntityUpdate] = Field(default_factory=list)
    source_channel: str = Field(default="", description="Filled by channel agent, not the LLM")
    source_speaker: str = Field(default="", description="Filled by channel agent, not the LLM")
    source_message: str = Field(default="", description="Filled by channel agent, not the LLM")
    timestamp: datetime | None = Field(
        default=None,
        description="Filled by channel agent post-extraction, not the LLM",
    )
    context_messages: list[str] = Field(
        default_factory=list,
        description="Surrounding conversation for audit trail",
    )
    reasoning: str | None = Field(None, description="LLM's explanation of the extraction")

    # RAI provenance fields (filled by channel agent post-extraction)
    model_name: str = Field(default="", description="Model that produced this extraction (e.g., qwen2.5:7b)")
    prompt_hash: str = Field(default="", description="SHA-256 hash of the system prompt template")

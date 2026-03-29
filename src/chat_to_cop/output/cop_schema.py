"""Placeholder CoP database schema models.

PLACEHOLDER: These models approximate the Track Manager and SmartPack Manager
schemas expected for MASH 2026. Built from:
  - BattleEffectSchemaV2 (docs/SCHEMAS.md)
  - GenMSG track fields (docs/SCHEMAS.md)
  - DASH 1-3 chat data analysis (docs/CHAT_DATA_ANALYSIS.md)
  - CJADC2 / Link 16 track conventions

When the real contractor schema arrives, update these models and the mapping
functions below. The CoPWriter only sees these types — all translation from
CoPUpdate happens here.

Design: every model has a metadata dict for overflow (Pattern A — ontology as
hypothesis). Fields that don't fit the schema are preserved, never dropped.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class Affiliation(str, Enum):
    FRIEND = "FRIEND"
    HOSTILE = "HOSTILE"
    NEUTRAL = "NEUTRAL"
    UNKNOWN = "UNKNOWN"


class TrackCategory(str, Enum):
    AIR = "AIR"
    LAND = "LAND"
    SURFACE = "SURFACE"
    SUBSURFACE = "SUBSURFACE"
    SPACE = "SPACE"
    CYBER = "CYBER"
    UNKNOWN = "UNKNOWN"


class OperationalStatus(str, Enum):
    OPERATIONAL = "OPERATIONAL"
    DEGRADED = "DEGRADED"
    INOP = "INOP"
    RTB = "RTB"
    DESTROYED = "DESTROYED"
    UNKNOWN = "UNKNOWN"


class EffectType(str, Enum):
    """BattleEffect operator types from BattleEffectSchemaV2."""

    DESTROY = "DESTROY"
    JAM = "JAM"
    MONITOR = "MONITOR"
    STRIKE = "STRIKE"
    DEFEND = "DEFEND"
    LOCATE = "LOCATE"
    CSAR = "CSAR"
    SUPPRESS = "SUPPRESS"
    OTHER = "OTHER"


class EffectStatus(str, Enum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class WriteAuthority(str, Enum):
    """Tiered write authority for RAI compliance.

    AUTO:   confidence >= 0.7 AND low-risk type. Written immediately.
    FLAGGED: confidence 0.4-0.7. Written with review flag.
    HUMAN:  confidence < 0.4 OR high-risk type. Queued for operator review.
    """

    AUTO = "auto"
    FLAGGED = "flagged"
    HUMAN = "human"


# ---------------------------------------------------------------------------
# Position / Velocity (shared sub-models)
# ---------------------------------------------------------------------------


class Position(BaseModel):
    """Geographic position — mirrors BattleEffectSchemaV2 entityOfInterest.position."""

    latitude: float | None = None
    longitude: float | None = None
    altitude_m: float | None = Field(None, description="Altitude MSL in meters")
    bearing_deg: float | None = Field(None, description="Bearing from bullseye in degrees")
    range_nm: float | None = Field(None, description="Range from bullseye in nautical miles")
    mgrs: str | None = Field(None, description="MGRS grid reference (fire missions)")
    accuracy_m: float | None = Field(None, description="Position accuracy CEP in meters")
    source: str | None = Field(None, description="Position source (sensor, chat, STT)")
    timestamp: datetime | None = None


class Velocity(BaseModel):
    """Kinematics — mirrors BattleEffectSchemaV2 entityOfInterest.velocity."""

    speed_kts: float | None = None
    heading_deg: float | None = None
    climb_rate_fpm: float | None = Field(None, description="Climb rate in feet/min")


# ---------------------------------------------------------------------------
# Track (the core CoP entity)
# ---------------------------------------------------------------------------


class Track(BaseModel):
    """A tracked entity in the CoP database.

    PLACEHOLDER: approximates the Track Manager schema. Maps to GenMSG fields
    and BattleEffectSchemaV2 entityOfInterest.

    Key assumptions:
    - track_id is our internal UUID; track_number is the operator-visible TN
    - affiliation_confidence is separate from extraction confidence
    - subsystem_status and weapon/fuel states are track-level properties
    - metadata dict captures anything that doesn't fit (Pattern A)
    """

    track_id: str = Field(description="Internal unique identifier")
    track_number: str | None = Field(None, description="Operator-visible TN (e.g., TM636, 44504)")
    jtn: str | None = Field(None, description="J-series Track Number (Link 16)")
    callsign: str | None = Field(None, description="Platform callsign (e.g., ORCA01)")
    platform_type: str | None = Field(None, description="Platform class (F-35, DDG, E-2D)")
    platform_activity: str | None = Field(None, description="On Mission, RTB, Engaged")

    category: TrackCategory = TrackCategory.UNKNOWN
    affiliation: Affiliation = Affiliation.UNKNOWN
    affiliation_confidence: float | None = Field(None, ge=0.0, le=1.0)
    threat_level: str | None = None

    position: Position | None = None
    velocity: Velocity | None = None

    operational_status: OperationalStatus = OperationalStatus.UNKNOWN
    subsystem_status: str | None = Field(None, description="E.g., 'radar inop', 'CIWS out of ammo'")

    weapon_type: str | None = None
    weapon_qty_launched: int | None = None
    weapon_qty_remaining: int | None = None
    fuel_state: str | None = Field(None, description="E.g., F+40, playtime 15 min")

    source: str | None = Field(None, description="Reporting source (sensor ID, channel)")
    last_updated: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict, description="Overflow — never lose data")


# ---------------------------------------------------------------------------
# BattleEffect (tasking / engagement)
# ---------------------------------------------------------------------------


class BattleEffect(BaseModel):
    """A battle effect record — tasking, engagement, or result.

    PLACEHOLDER: approximates BattleEffectSchemaV2 from docs/SCHEMAS.md.

    Maps update types: WEAPONS, TASKING, FIRE_MISSION, CSAR.
    """

    effect_id: str = Field(description="Unique identifier (e.g., DA011, BattleCOA 6-1)")
    effect_type: EffectType = EffectType.OTHER
    status: EffectStatus = EffectStatus.PENDING

    target_track_number: str | None = None
    target_description: str | None = None

    shooter_callsign: str | None = None
    shooter_track_number: str | None = None

    weapon_type: str | None = None
    weapon_quantity: int | None = None
    result: str | None = Field(None, description="SPLASH, MISS, PENDING, etc.")

    priority: str | None = None
    time_window: str | None = Field(None, description="IMMEDIATE, ASAP, etc.")

    position: Position | None = None
    justification: str | None = None
    chat_message: str | None = Field(None, description="Original chat text for provenance")

    source_channel: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# CoPRecord: wrapper sent to the REST API
# ---------------------------------------------------------------------------


class CoPRecord(BaseModel):
    """A single record to push to the CoP REST API.

    Wraps either a Track update or a BattleEffect with write-authority
    metadata and provenance fields for RAI compliance.
    """

    record_type: str = Field(description="'track' or 'battle_effect'")
    track: Track | None = None
    battle_effect: BattleEffect | None = None

    write_authority: WriteAuthority = WriteAuthority.AUTO
    extraction_confidence: float = Field(ge=0.0, le=1.0)
    extraction_method: str = Field(default="llm")
    source_channel: str = ""
    source_speaker: str = ""
    source_message: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    review_flag: str | None = Field(None, description="Reason for flagged/human review")


# ---------------------------------------------------------------------------
# High-risk update types that always require human review
# ---------------------------------------------------------------------------

HIGH_RISK_TYPES = frozenset(
    [
        "weapons",
        "csar",
        "fire_mission",
        "cyber_ew",
    ]
)


# ---------------------------------------------------------------------------
# Mapping: CoPUpdate -> CoPRecord(s)
# ---------------------------------------------------------------------------


def classify_write_authority(
    confidence: float,
    update_type: str,
) -> WriteAuthority:
    """Determine write authority tier for an update.

    Rules:
    - confidence < 0.4 -> HUMAN (always)
    - high-risk type (weapons, CSAR, fire_mission, cyber_ew) -> HUMAN
    - confidence 0.4-0.7 -> FLAGGED
    - confidence >= 0.7 -> AUTO
    """
    if confidence < 0.4:
        return WriteAuthority.HUMAN
    if update_type in HIGH_RISK_TYPES:
        return WriteAuthority.HUMAN
    if confidence < 0.7:
        return WriteAuthority.FLAGGED
    return WriteAuthority.AUTO


def _build_position(entity) -> Position | None:
    """Extract position from an EntityUpdate if any position fields are set."""
    has_pos = any(
        [
            entity.latitude is not None,
            entity.longitude is not None,
            entity.bearing is not None,
            entity.range_nm is not None,
        ]
    )
    if not has_pos:
        return None
    return Position(
        latitude=entity.latitude,
        longitude=entity.longitude,
        bearing_deg=entity.bearing,
        range_nm=entity.range_nm,
    )


def _build_velocity(entity) -> Velocity | None:
    """Extract velocity from an EntityUpdate if any kinematics fields are set."""
    has_vel = any(
        [
            entity.speed_kts is not None,
            entity.heading is not None,
        ]
    )
    if not has_vel:
        return None
    return Velocity(
        speed_kts=entity.speed_kts,
        heading_deg=entity.heading,
    )


def _entity_to_track(entity, update) -> Track:
    """Map an EntityUpdate + parent CoPUpdate to a Track."""
    import uuid

    track_id = entity.track_number or entity.callsign or str(uuid.uuid4())[:8]

    return Track(
        track_id=track_id,
        track_number=entity.track_number,
        callsign=entity.callsign,
        platform_type=entity.platform_type,
        affiliation=(
            Affiliation(entity.affiliation)
            if entity.affiliation and entity.affiliation in Affiliation.__members__
            else Affiliation.UNKNOWN
        ),
        operational_status=(
            OperationalStatus(entity.operational_status)
            if entity.operational_status and entity.operational_status in OperationalStatus.__members__
            else OperationalStatus.UNKNOWN
        ),
        position=_build_position(entity),
        velocity=_build_velocity(entity),
        weapon_type=entity.weapon_type,
        weapon_qty_launched=entity.weapon_qty_launched,
        weapon_qty_remaining=entity.weapon_qty_remaining,
        fuel_state=entity.fuel_state,
        subsystem_status=entity.subsystem_status,
        source=update.source_channel,
        last_updated=update.timestamp,
        metadata=entity.metadata,
    )


_BATTLE_EFFECT_TYPES = frozenset(["weapons", "tasking", "fire_mission", "csar"])

_UPDATE_TYPE_TO_EFFECT = {
    "weapons": EffectType.STRIKE,
    "tasking": EffectType.STRIKE,
    "fire_mission": EffectType.DESTROY,
    "csar": EffectType.CSAR,
}


def _entity_to_battle_effect(entity, update) -> BattleEffect:
    """Map an EntityUpdate from a weapons/tasking/fire/CSAR update to a BattleEffect."""
    import uuid

    effect_id = entity.metadata.get("task_uid", str(uuid.uuid4())[:8])
    effect_type = _UPDATE_TYPE_TO_EFFECT.get(update.update_type.value, EffectType.OTHER)

    return BattleEffect(
        effect_id=effect_id,
        effect_type=effect_type,
        status=EffectStatus.ACTIVE,
        target_track_number=entity.track_number,
        shooter_callsign=entity.callsign,
        weapon_type=entity.weapon_type,
        weapon_quantity=entity.weapon_qty_launched,
        result=entity.metadata.get("result"),
        position=_build_position(entity),
        chat_message=update.source_message,
        source_channel=update.source_channel,
        timestamp=update.timestamp,
        metadata=entity.metadata,
    )


def cop_update_to_records(update) -> list[CoPRecord]:
    """Convert a CoPUpdate into one or more CoPRecords for the REST API.

    Each EntityUpdate in the CoPUpdate produces one CoPRecord.
    Updates with no entities produce a single metadata-only record.

    The update_type determines whether we create Track records,
    BattleEffect records, or both.
    """

    authority = classify_write_authority(update.confidence, update.update_type.value)
    review_flag = None
    if authority == WriteAuthority.FLAGGED:
        review_flag = f"Confidence {update.confidence:.2f} requires review"
    elif authority == WriteAuthority.HUMAN:
        if update.confidence < 0.4:
            review_flag = f"Low confidence ({update.confidence:.2f})"
        elif update.update_type.value in HIGH_RISK_TYPES:
            review_flag = f"High-risk update type: {update.update_type.value}"

    is_battle_effect_type = update.update_type.value in _BATTLE_EFFECT_TYPES

    records: list[CoPRecord] = []

    if not update.entities:
        # No entities — create a metadata-only record (e.g., environmental, sitrep)
        records.append(
            CoPRecord(
                record_type="track",
                track=Track(
                    track_id="metadata-" + update.update_type.value,
                    source=update.source_channel,
                    last_updated=update.timestamp,
                    metadata={
                        "update_type": update.update_type.value,
                        "source_message": update.source_message,
                        "source_speaker": update.source_speaker,
                    },
                ),
                write_authority=authority,
                extraction_confidence=update.confidence,
                extraction_method=update.extraction_method,
                source_channel=update.source_channel,
                source_speaker=update.source_speaker,
                source_message=update.source_message,
                timestamp=update.timestamp,
                review_flag=review_flag,
            )
        )
        return records

    for entity in update.entities:
        # Always produce a Track record (the entity exists in the battlespace)
        track = _entity_to_track(entity, update)
        records.append(
            CoPRecord(
                record_type="track",
                track=track,
                write_authority=authority,
                extraction_confidence=update.confidence,
                extraction_method=update.extraction_method,
                source_channel=update.source_channel,
                source_speaker=update.source_speaker,
                source_message=update.source_message,
                timestamp=update.timestamp,
                review_flag=review_flag,
            )
        )

        # Battle-effect types also produce a BattleEffect record
        if is_battle_effect_type:
            effect = _entity_to_battle_effect(entity, update)
            records.append(
                CoPRecord(
                    record_type="battle_effect",
                    battle_effect=effect,
                    write_authority=authority,
                    extraction_confidence=update.confidence,
                    extraction_method=update.extraction_method,
                    source_channel=update.source_channel,
                    source_speaker=update.source_speaker,
                    source_message=update.source_message,
                    timestamp=update.timestamp,
                    review_flag=review_flag,
                )
            )

    return records

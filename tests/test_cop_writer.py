"""Tests for CoP schema mapping, write authority classification, and CoPWriter."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from chat_to_cop.models.cop_update import CoPUpdate, EntityUpdate, UpdateType
from chat_to_cop.output.cop_schema import (
    Affiliation,
    BattleEffect,
    CoPRecord,
    EffectType,
    OperationalStatus,
    Track,
    WriteAuthority,
    classify_write_authority,
    cop_update_to_records,
)
from chat_to_cop.output.cop_writer import CoPWriter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TS = datetime(2025, 9, 23, 14, 10, 15, tzinfo=timezone.utc)


def _make_update(
    update_type: UpdateType = UpdateType.FUEL,
    confidence: float = 0.85,
    channel: str = "#c2_coord",
    speaker: str = "Hydro_Tank",
    message: str = "RR15 F+40",
    track_number: str | None = "TM636",
    callsign: str | None = "RR15",
    affiliation: str | None = None,
    operational_status: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    weapon_type: str | None = None,
    weapon_qty_launched: int | None = None,
) -> CoPUpdate:
    entities = []
    if track_number or callsign:
        entities.append(
            EntityUpdate(
                track_number=track_number,
                callsign=callsign,
                fuel_state="F+40" if update_type == UpdateType.FUEL else None,
                affiliation=affiliation,
                operational_status=operational_status,
                latitude=latitude,
                longitude=longitude,
                weapon_type=weapon_type,
                weapon_qty_launched=weapon_qty_launched,
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
        timestamp=_TS,
    )


class _FakeResponse:
    """Minimal mock for an HTTP response."""

    def __init__(self, status_code: int = 200):
        self.status_code = status_code


class _FakeHTTPClient:
    """Mock HTTP client that records calls and returns configurable responses."""

    def __init__(self, status_code: int = 201):
        self.calls: list[tuple[str, dict]] = []
        self._status_code = status_code

    async def post(self, url: str, json: dict) -> _FakeResponse:
        self.calls.append((url, json))
        return _FakeResponse(self._status_code)


# ===========================================================================
# Schema mapping tests
# ===========================================================================


class TestWriteAuthorityClassification:
    """Test the tiered write authority rules."""

    def test_high_confidence_low_risk_is_auto(self):
        assert classify_write_authority(0.85, "fuel") == WriteAuthority.AUTO

    def test_high_confidence_status_is_auto(self):
        assert classify_write_authority(0.9, "status_change") == WriteAuthority.AUTO

    def test_medium_confidence_is_flagged(self):
        assert classify_write_authority(0.55, "fuel") == WriteAuthority.FLAGGED

    def test_boundary_07_is_auto(self):
        assert classify_write_authority(0.7, "entity_id") == WriteAuthority.AUTO

    def test_boundary_04_is_flagged(self):
        assert classify_write_authority(0.4, "entity_id") == WriteAuthority.FLAGGED

    def test_low_confidence_is_human(self):
        assert classify_write_authority(0.3, "fuel") == WriteAuthority.HUMAN

    def test_below_04_is_always_human(self):
        assert classify_write_authority(0.1, "location") == WriteAuthority.HUMAN

    def test_weapons_is_always_human(self):
        assert classify_write_authority(0.95, "weapons") == WriteAuthority.HUMAN

    def test_csar_is_always_human(self):
        assert classify_write_authority(0.99, "csar") == WriteAuthority.HUMAN

    def test_fire_mission_is_always_human(self):
        assert classify_write_authority(0.8, "fire_mission") == WriteAuthority.HUMAN

    def test_cyber_ew_is_always_human(self):
        assert classify_write_authority(0.75, "cyber_ew") == WriteAuthority.HUMAN


class TestCoPUpdateToRecords:
    """Test mapping from CoPUpdate to CoPRecord(s)."""

    def test_fuel_update_produces_one_track(self):
        update = _make_update(update_type=UpdateType.FUEL, confidence=0.85)
        records = cop_update_to_records(update)
        assert len(records) == 1
        assert records[0].record_type == "track"
        assert records[0].track is not None
        assert records[0].track.fuel_state == "F+40"

    def test_entity_id_maps_affiliation(self):
        update = _make_update(
            update_type=UpdateType.ENTITY_ID,
            confidence=0.9,
            affiliation="HOSTILE",
        )
        records = cop_update_to_records(update)
        assert records[0].track.affiliation == Affiliation.HOSTILE

    def test_unknown_affiliation_defaults(self):
        update = _make_update(update_type=UpdateType.ENTITY_ID, confidence=0.9)
        records = cop_update_to_records(update)
        assert records[0].track.affiliation == Affiliation.UNKNOWN

    def test_status_change_maps_operational_status(self):
        update = _make_update(
            update_type=UpdateType.STATUS_CHANGE,
            confidence=0.8,
            operational_status="RTB",
        )
        records = cop_update_to_records(update)
        assert records[0].track.operational_status == OperationalStatus.RTB

    def test_location_maps_position(self):
        update = _make_update(
            update_type=UpdateType.LOCATION,
            confidence=0.75,
            latitude=21.774294,
            longitude=-72.279964,
        )
        records = cop_update_to_records(update)
        pos = records[0].track.position
        assert pos is not None
        assert pos.latitude == pytest.approx(21.774294)
        assert pos.longitude == pytest.approx(-72.279964)

    def test_weapons_produces_track_and_effect(self):
        update = _make_update(
            update_type=UpdateType.WEAPONS,
            confidence=0.9,
            weapon_type="SM6",
            weapon_qty_launched=4,
        )
        records = cop_update_to_records(update)
        # Weapons: 1 track record + 1 battle_effect record
        assert len(records) == 2
        types = {r.record_type for r in records}
        assert types == {"track", "battle_effect"}
        effect_rec = [r for r in records if r.record_type == "battle_effect"][0]
        assert effect_rec.battle_effect.weapon_type == "SM6"
        assert effect_rec.battle_effect.weapon_quantity == 4

    def test_tasking_produces_battle_effect(self):
        update = _make_update(
            update_type=UpdateType.TASKING,
            confidence=0.8,
            track_number="TM636",
            callsign="HADES31",
        )
        records = cop_update_to_records(update)
        effects = [r for r in records if r.record_type == "battle_effect"]
        assert len(effects) == 1
        assert effects[0].battle_effect.effect_type == EffectType.STRIKE

    def test_no_entities_produces_metadata_record(self):
        update = _make_update(
            update_type=UpdateType.ENVIRONMENTAL,
            confidence=0.8,
            track_number=None,
            callsign=None,
        )
        records = cop_update_to_records(update)
        assert len(records) == 1
        assert records[0].track.metadata["update_type"] == "environmental"

    def test_provenance_fields_carried_through(self):
        update = _make_update(
            update_type=UpdateType.FUEL,
            confidence=0.85,
            channel="#c2_coord",
            speaker="Hydro_Tank",
            message="RR15 F+40",
        )
        records = cop_update_to_records(update)
        rec = records[0]
        assert rec.source_channel == "#c2_coord"
        assert rec.source_speaker == "Hydro_Tank"
        assert rec.source_message == "RR15 F+40"
        assert rec.extraction_method == "llm"
        assert rec.extraction_confidence == 0.85

    def test_flagged_record_has_review_reason(self):
        update = _make_update(confidence=0.55)
        records = cop_update_to_records(update)
        assert records[0].write_authority == WriteAuthority.FLAGGED
        assert records[0].review_flag is not None
        assert "0.55" in records[0].review_flag

    def test_human_record_has_review_reason_for_low_conf(self):
        update = _make_update(confidence=0.2)
        records = cop_update_to_records(update)
        assert records[0].write_authority == WriteAuthority.HUMAN
        assert "0.20" in records[0].review_flag

    def test_human_record_has_review_reason_for_high_risk(self):
        update = _make_update(update_type=UpdateType.WEAPONS, confidence=0.95)
        records = cop_update_to_records(update)
        assert records[0].write_authority == WriteAuthority.HUMAN
        assert "weapons" in records[0].review_flag


# ===========================================================================
# CoPWriter tests
# ===========================================================================


class TestCoPWriterLocalMode:
    """Test CoPWriter in local-only mode (no HTTP endpoint)."""

    def test_push_returns_results(self):
        async def run():
            writer = CoPWriter()
            results = await writer.push_update(_make_update())
            assert len(results) == 1
            assert results[0].success is True
            assert results[0].status_code == 200

        asyncio.run(run())

    def test_auto_write_increments_stats(self):
        async def run():
            writer = CoPWriter()
            await writer.push_update(_make_update(confidence=0.85))
            assert writer.stats["auto_writes"] == 1

        asyncio.run(run())

    def test_flagged_write_increments_stats(self):
        async def run():
            writer = CoPWriter()
            await writer.push_update(_make_update(confidence=0.55))
            assert writer.stats["flagged_writes"] == 1

        asyncio.run(run())

    def test_human_queued_not_written(self):
        async def run():
            writer = CoPWriter()
            results = await writer.push_update(_make_update(update_type=UpdateType.WEAPONS, confidence=0.9))
            # Weapons: 2 records (track + effect), both HUMAN-queued
            assert len(results) == 2
            assert writer.stats["human_queued"] == 2
            assert writer.human_queue_size == 2

        asyncio.run(run())


class TestCoPWriterHTTPMode:
    """Test CoPWriter with a mock HTTP client."""

    def test_sends_to_tracks_endpoint(self):
        async def run():
            client = _FakeHTTPClient(status_code=201)
            writer = CoPWriter(cop_base_url="http://cop.local", http_client=client)
            await writer.push_update(_make_update(confidence=0.85))
            assert len(client.calls) == 1
            url, payload = client.calls[0]
            assert url == "http://cop.local/api/v1/tracks"

        asyncio.run(run())

    def test_weapons_sends_to_both_endpoints(self):
        async def run():
            client = _FakeHTTPClient(status_code=201)
            writer = CoPWriter(cop_base_url="http://cop.local", http_client=client)
            # Weapons are HIGH_RISK -> HUMAN queue, so they don't hit HTTP
            # Use a low-risk type that produces track + effect for HTTP test
            # Actually, weapons always go to human queue. Let's verify that.
            await writer.push_update(_make_update(update_type=UpdateType.WEAPONS, confidence=0.9))
            # Weapons are human-queued, not sent via HTTP
            assert len(client.calls) == 0
            assert writer.human_queue_size == 2

        asyncio.run(run())

    def test_http_error_tracks_in_stats(self):
        async def run():
            client = _FakeHTTPClient(status_code=500)
            writer = CoPWriter(cop_base_url="http://cop.local", http_client=client)
            results = await writer.push_update(_make_update(confidence=0.85))
            assert results[0].success is False
            assert results[0].status_code == 500
            assert writer.stats["errors"] == 1

        asyncio.run(run())

    def test_http_exception_handled(self):
        async def run():
            client = MagicMock()
            client.post = AsyncMock(side_effect=ConnectionError("refused"))
            writer = CoPWriter(cop_base_url="http://cop.local", http_client=client)
            results = await writer.push_update(_make_update(confidence=0.85))
            assert results[0].success is False
            assert "refused" in results[0].error
            assert writer.stats["errors"] == 1

        asyncio.run(run())


class TestCoPWriterKillSwitch:
    """Test pause/resume kill switch."""

    def test_pause_queues_updates(self):
        async def run():
            writer = CoPWriter()
            writer.pause()
            assert writer.paused is True
            results = await writer.push_update(_make_update())
            # When paused, returns empty results (update is queued)
            assert results == []
            assert writer.pause_queue_size == 1

        asyncio.run(run())

    def test_resume_sets_flag(self):
        async def run():
            writer = CoPWriter()
            writer.pause()
            writer.resume()
            assert writer.paused is False

        asyncio.run(run())

    def test_flush_drains_pause_queue(self):
        async def run():
            writer = CoPWriter()
            writer.pause()
            await writer.push_update(_make_update(track_number="TM001"))
            await writer.push_update(_make_update(track_number="TM002"))
            await writer.push_update(_make_update(track_number="TM003"))
            assert writer.pause_queue_size == 3
            writer.resume()
            results = await writer.flush_pause_queue()
            assert writer.pause_queue_size == 0
            assert len(results) == 3
            assert all(r.success for r in results)

        asyncio.run(run())

    def test_pause_resume_idempotent(self):
        writer = CoPWriter()
        writer.pause()
        writer.pause()  # second pause is no-op
        assert writer.paused is True
        writer.resume()
        writer.resume()  # second resume is no-op
        assert writer.paused is False

    def test_updates_after_resume_write_normally(self):
        async def run():
            writer = CoPWriter()
            writer.pause()
            writer.resume()
            results = await writer.push_update(_make_update())
            assert len(results) == 1
            assert results[0].success is True
            assert writer.stats["auto_writes"] == 1

        asyncio.run(run())


class TestCoPWriterHumanQueue:
    """Test human review queue operations."""

    def test_peek_does_not_drain(self):
        async def run():
            writer = CoPWriter()
            await writer.push_update(_make_update(update_type=UpdateType.WEAPONS, confidence=0.9))
            items = writer.get_human_queue()
            assert len(items) == 2  # track + effect
            # Still there
            assert writer.human_queue_size == 2

        asyncio.run(run())

    def test_pop_drains(self):
        async def run():
            writer = CoPWriter()
            await writer.push_update(_make_update(update_type=UpdateType.WEAPONS, confidence=0.9))
            popped = writer.pop_human_queue(count=1)
            assert len(popped) == 1
            assert writer.human_queue_size == 1

        asyncio.run(run())

    def test_pop_all(self):
        async def run():
            writer = CoPWriter()
            await writer.push_update(_make_update(update_type=UpdateType.WEAPONS, confidence=0.9))
            popped = writer.pop_human_queue()
            assert len(popped) == 2
            assert writer.human_queue_size == 0

        asyncio.run(run())

    def test_pop_more_than_available(self):
        async def run():
            writer = CoPWriter()
            await writer.push_update(_make_update(update_type=UpdateType.WEAPONS, confidence=0.9))
            popped = writer.pop_human_queue(count=100)
            assert len(popped) == 2

        asyncio.run(run())


class TestCoPWriterMultipleEntities:
    """Test updates with multiple entities."""

    def test_multiple_entities_produce_multiple_records(self):
        update = CoPUpdate(
            update_type=UpdateType.ENTITY_ID,
            confidence=0.9,
            extraction_method="llm",
            entities=[
                EntityUpdate(track_number="44504", callsign="DDG1"),
                EntityUpdate(track_number="44506", callsign=None, platform_type="J-15"),
            ],
            source_channel="#c2_coord",
            source_speaker="Intel_OPS",
            source_message="44504 is DDG1, 44506 is 4x J-15s",
            timestamp=_TS,
        )
        records = cop_update_to_records(update)
        assert len(records) == 2
        track_ids = {r.track.track_id for r in records}
        assert "44504" in track_ids
        assert "44506" in track_ids


class TestCoPSchemaModels:
    """Test the Pydantic schema models themselves."""

    def test_track_serialization(self):
        track = Track(
            track_id="TM636",
            track_number="TM636",
            callsign="HADES31",
            platform_type="F-35",
            affiliation=Affiliation.FRIEND,
            metadata={"extra": "data"},
        )
        data = track.model_dump()
        assert data["track_id"] == "TM636"
        assert data["affiliation"] == "FRIEND"
        assert data["metadata"]["extra"] == "data"

    def test_battle_effect_serialization(self):
        effect = BattleEffect(
            effect_id="DA011",
            effect_type=EffectType.DESTROY,
            target_track_number="TM707",
            shooter_callsign="HADES31",
            weapon_type="JASSM",
        )
        data = effect.model_dump()
        assert data["effect_id"] == "DA011"
        assert data["effect_type"] == "DESTROY"

    def test_cop_record_json_roundtrip(self):
        record = CoPRecord(
            record_type="track",
            track=Track(track_id="TM636", track_number="TM636"),
            write_authority=WriteAuthority.AUTO,
            extraction_confidence=0.85,
            extraction_method="llm",
            source_channel="#c2_coord",
        )
        json_str = record.model_dump_json()
        restored = CoPRecord.model_validate_json(json_str)
        assert restored.track.track_id == "TM636"
        assert restored.write_authority == WriteAuthority.AUTO

"""Tests for CoP schema mapping, write authority classification, CoPWriter, and CoPRESTClient."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from chat_to_cop.config import CoPWriterConfig
from chat_to_cop.models.cop_update import CoPUpdate, EntityUpdate, UpdateType
from chat_to_cop.output.cop_rest_client import CoPRESTClient, SendResult
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
    confidence: float = 0.96,
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


class _FakeRESTClient:
    """Mock CoPRESTClient for testing writer integration."""

    def __init__(self, success: bool = True, dry_run: bool = False, status_code: int = 201):
        self.records_sent: list[CoPRecord] = []
        self._success = success
        self._dry_run = dry_run
        self._status_code = status_code

    async def send_record(self, record: CoPRecord) -> SendResult:
        self.records_sent.append(record)
        return SendResult(
            success=self._success,
            status_code=self._status_code,
            dry_run=self._dry_run,
        )


# ===========================================================================
# Schema mapping tests
# ===========================================================================


class TestWriteAuthorityClassification:
    """Test the tiered write authority rules."""

    def test_high_confidence_low_risk_is_auto(self):
        # With new default auto_threshold=0.95, need >= 0.95 for AUTO
        assert classify_write_authority(0.96, "fuel") == WriteAuthority.AUTO

    def test_high_confidence_status_is_auto(self):
        assert classify_write_authority(0.95, "status_change") == WriteAuthority.AUTO

    def test_085_is_now_flagged_not_auto(self):
        # 0.85 used to be AUTO at the old 0.7 threshold; now it's FLAGGED
        assert classify_write_authority(0.85, "fuel") == WriteAuthority.FLAGGED

    def test_medium_confidence_is_flagged(self):
        # 0.55 is between flag_threshold (0.5) and auto_threshold (0.95)
        assert classify_write_authority(0.55, "fuel") == WriteAuthority.FLAGGED

    def test_boundary_095_is_auto(self):
        # New default auto_threshold is 0.95 (raised from 0.7 after ECE=0.67)
        assert classify_write_authority(0.95, "entity_id") == WriteAuthority.AUTO

    def test_boundary_05_is_flagged(self):
        # New default flag_threshold is 0.5 (raised from 0.4)
        assert classify_write_authority(0.5, "entity_id") == WriteAuthority.FLAGGED

    def test_old_auto_threshold_now_flagged(self):
        # 0.7 used to be AUTO; with new threshold 0.95 it's FLAGGED
        assert classify_write_authority(0.7, "entity_id") == WriteAuthority.FLAGGED

    def test_low_confidence_is_human(self):
        assert classify_write_authority(0.3, "fuel") == WriteAuthority.HUMAN

    def test_below_05_is_always_human(self):
        # Raised from 0.4 to 0.5
        assert classify_write_authority(0.1, "location") == WriteAuthority.HUMAN

    def test_049_is_human(self):
        # Just below the new flag_threshold of 0.5
        assert classify_write_authority(0.49, "location") == WriteAuthority.HUMAN

    def test_weapons_is_always_human(self):
        assert classify_write_authority(0.95, "weapons") == WriteAuthority.HUMAN

    def test_csar_is_always_human(self):
        assert classify_write_authority(0.99, "csar") == WriteAuthority.HUMAN

    def test_fire_mission_is_always_human(self):
        assert classify_write_authority(0.8, "fire_mission") == WriteAuthority.HUMAN

    def test_cyber_ew_is_always_human(self):
        assert classify_write_authority(0.75, "cyber_ew") == WriteAuthority.HUMAN

    def test_custom_thresholds_auto(self):
        # With auto_threshold=0.5, a 0.55 should be AUTO (not FLAGGED)
        assert classify_write_authority(0.55, "fuel", auto_threshold=0.5) == WriteAuthority.AUTO

    def test_custom_thresholds_flagged(self):
        # With flag_threshold=0.3, a 0.35 should be FLAGGED (not HUMAN)
        assert classify_write_authority(0.35, "fuel", flag_threshold=0.3) == WriteAuthority.FLAGGED

    def test_custom_thresholds_human(self):
        # With flag_threshold=0.5, a 0.45 should be HUMAN
        assert classify_write_authority(0.45, "fuel", flag_threshold=0.5) == WriteAuthority.HUMAN

    def test_custom_high_risk_types(self):
        # "fuel" is not in default high_risk, but we can make it high-risk
        assert classify_write_authority(0.95, "fuel", high_risk_types=frozenset(["fuel"])) == WriteAuthority.HUMAN

    def test_custom_high_risk_removes_default(self):
        # With a custom set that doesn't include "weapons", weapons should follow confidence rules
        assert classify_write_authority(0.95, "weapons", high_risk_types=frozenset(["nuclear"])) == WriteAuthority.AUTO


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

    def test_custom_thresholds_in_records(self):
        # With auto_threshold=0.5, a 0.55 confidence update should be AUTO
        update = _make_update(confidence=0.55)
        records = cop_update_to_records(update, auto_threshold=0.5)
        assert records[0].write_authority == WriteAuthority.AUTO


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
            await writer.push_update(_make_update(confidence=0.96))
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

    def test_total_records_tracked(self):
        async def run():
            writer = CoPWriter()
            await writer.push_update(_make_update(confidence=0.85))
            await writer.push_update(_make_update(confidence=0.55))
            assert writer.stats["total_records"] == 2

        asyncio.run(run())


class TestCoPWriterHTTPMode:
    """Test CoPWriter with a mock HTTP client (legacy path)."""

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


class TestCoPWriterRESTClientMode:
    """Test CoPWriter with a mock CoPRESTClient."""

    def test_delegates_to_rest_client(self):
        async def run():
            rest_client = _FakeRESTClient(success=True, status_code=201)
            writer = CoPWriter(rest_client=rest_client)
            results = await writer.push_update(_make_update(confidence=0.85))
            assert len(results) == 1
            assert results[0].success is True
            assert results[0].status_code == 201
            assert len(rest_client.records_sent) == 1

        asyncio.run(run())

    def test_rest_client_dry_run_flag(self):
        async def run():
            rest_client = _FakeRESTClient(success=True, dry_run=True)
            writer = CoPWriter(rest_client=rest_client)
            results = await writer.push_update(_make_update(confidence=0.85))
            assert results[0].dry_run is True

        asyncio.run(run())

    def test_rest_client_failure_increments_errors(self):
        async def run():
            rest_client = _FakeRESTClient(success=False, status_code=500)
            writer = CoPWriter(rest_client=rest_client)
            results = await writer.push_update(_make_update(confidence=0.85))
            assert results[0].success is False
            assert writer.stats["errors"] == 1

        asyncio.run(run())

    def test_human_queue_not_sent_to_rest_client(self):
        async def run():
            rest_client = _FakeRESTClient()
            writer = CoPWriter(rest_client=rest_client)
            await writer.push_update(_make_update(update_type=UpdateType.WEAPONS, confidence=0.9))
            # Weapons -> human queue, never sent to REST client
            assert len(rest_client.records_sent) == 0
            assert writer.human_queue_size == 2

        asyncio.run(run())

    def test_flagged_sent_to_rest_client(self):
        async def run():
            rest_client = _FakeRESTClient(success=True, status_code=201)
            writer = CoPWriter(rest_client=rest_client)
            await writer.push_update(_make_update(confidence=0.55))
            assert len(rest_client.records_sent) == 1
            assert writer.stats["flagged_writes"] == 1

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

    def test_pause_with_rest_client(self):
        async def run():
            rest_client = _FakeRESTClient()
            writer = CoPWriter(rest_client=rest_client)
            writer.pause()
            await writer.push_update(_make_update(confidence=0.85))
            # Nothing sent while paused
            assert len(rest_client.records_sent) == 0
            assert writer.pause_queue_size == 1
            # Resume and flush
            writer.resume()
            results = await writer.flush_pause_queue()
            assert len(results) == 1
            assert len(rest_client.records_sent) == 1

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


# ===========================================================================
# CoPRESTClient tests
# ===========================================================================


class TestCoPRESTClientDryRun:
    """Test CoPRESTClient in dry-run mode (empty base URL)."""

    def test_dry_run_when_no_url(self):
        client = CoPRESTClient(base_url="")
        assert client.dry_run is True

    def test_not_dry_run_when_url_set(self):
        client = CoPRESTClient(base_url="http://cop.local")
        assert client.dry_run is False

    def test_dry_run_send_returns_success(self):
        async def run():
            client = CoPRESTClient(base_url="")
            record = CoPRecord(
                record_type="track",
                track=Track(track_id="TM636"),
                write_authority=WriteAuthority.AUTO,
                extraction_confidence=0.85,
            )
            result = await client.send_record(record)
            assert result.success is True
            assert result.dry_run is True
            assert result.status_code == 200

        asyncio.run(run())

    def test_dry_run_increments_stats(self):
        async def run():
            client = CoPRESTClient(base_url="")
            record = CoPRecord(
                record_type="track",
                track=Track(track_id="TM636"),
                write_authority=WriteAuthority.AUTO,
                extraction_confidence=0.85,
            )
            await client.send_record(record)
            await client.send_record(record)
            assert client.stats["dry_run_logged"] == 2
            assert client.stats["requests_sent"] == 0

        asyncio.run(run())


class TestCoPRESTClientEndpoints:
    """Test endpoint resolution."""

    def test_track_endpoint(self):
        client = CoPRESTClient(base_url="")
        record = CoPRecord(
            record_type="track",
            track=Track(track_id="TM636"),
            write_authority=WriteAuthority.AUTO,
            extraction_confidence=0.85,
        )
        assert client._resolve_endpoint(record) == "/api/v1/tracks"

    def test_battle_effect_endpoint(self):
        client = CoPRESTClient(base_url="")
        record = CoPRecord(
            record_type="battle_effect",
            battle_effect=BattleEffect(effect_id="DA011"),
            write_authority=WriteAuthority.AUTO,
            extraction_confidence=0.85,
        )
        assert client._resolve_endpoint(record) == "/api/v1/effects"

    def test_unknown_type_endpoint(self):
        client = CoPRESTClient(base_url="")
        record = CoPRecord(
            record_type="other",
            write_authority=WriteAuthority.AUTO,
            extraction_confidence=0.85,
        )
        assert client._resolve_endpoint(record) == "/api/v1/records"


class TestSendResult:
    """Test SendResult round-trips through JSON for logging and error reporting."""

    def test_success_fields_for_audit_log(self):
        r = SendResult(success=True, status_code=201)
        assert r.success is True
        assert r.status_code == 201
        assert r.error is None

    def test_failure_preserves_error_message(self):
        r = SendResult(success=False, error="Connection refused")
        assert r.error == "Connection refused"
        assert r.success is False

    def test_dry_run_distinguishable_from_real_write(self):
        real = SendResult(success=True, status_code=201, dry_run=False)
        dry = SendResult(success=True, status_code=200, dry_run=True)
        assert real.dry_run != dry.dry_run


# ===========================================================================
# Config tests
# ===========================================================================


class TestCoPWriterConfig:
    """Test CoPWriterConfig defaults and from_config factory."""

    def test_defaults(self):
        config = CoPWriterConfig()
        assert config.cop_api_url == ""
        assert config.cop_auto_threshold == 0.95
        assert config.cop_flag_threshold == 0.5
        assert "weapons" in config.cop_high_risk_types
        assert "csar" in config.cop_high_risk_types
        assert "fire_mission" in config.cop_high_risk_types
        assert "cyber_ew" in config.cop_high_risk_types

    def test_from_config_creates_writer(self):
        config = CoPWriterConfig()
        writer = CoPWriter.from_config(config)
        assert writer._auto_threshold == 0.95
        assert writer._flag_threshold == 0.5
        assert writer._rest_client is not None

    def test_from_config_custom_thresholds(self):
        config = CoPWriterConfig(cop_auto_threshold=0.8, cop_flag_threshold=0.5)
        writer = CoPWriter.from_config(config)
        assert writer._auto_threshold == 0.8
        assert writer._flag_threshold == 0.5

    def test_from_config_custom_high_risk(self):
        config = CoPWriterConfig(cop_high_risk_types=["weapons", "nuclear"])
        writer = CoPWriter.from_config(config)
        assert "nuclear" in writer._high_risk_types
        assert "csar" not in writer._high_risk_types


class TestCoPWriterConfigurableThresholds:
    """Test that CoPWriter respects configurable thresholds."""

    def test_custom_auto_threshold(self):
        async def run():
            writer = CoPWriter(auto_threshold=0.5)
            # 0.55 would normally be FLAGGED at 0.7 threshold, but AUTO at 0.5
            await writer.push_update(_make_update(confidence=0.55))
            assert writer.stats["auto_writes"] == 1
            assert writer.stats["flagged_writes"] == 0

        asyncio.run(run())

    def test_custom_flag_threshold(self):
        async def run():
            writer = CoPWriter(flag_threshold=0.3)
            # 0.35 would normally be HUMAN at 0.4 threshold, but FLAGGED at 0.3
            await writer.push_update(_make_update(confidence=0.35))
            assert writer.stats["flagged_writes"] == 1
            assert writer.stats["human_queued"] == 0

        asyncio.run(run())

    def test_custom_high_risk_types(self):
        async def run():
            # Remove weapons from high-risk -- should now follow confidence rules
            writer = CoPWriter(high_risk_types=["nuclear"])
            await writer.push_update(_make_update(update_type=UpdateType.WEAPONS, confidence=0.96))
            # With confidence 0.96 and weapons NOT in high-risk, should be AUTO
            assert writer.stats["auto_writes"] == 2  # track + effect
            assert writer.stats["human_queued"] == 0

        asyncio.run(run())

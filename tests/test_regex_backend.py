"""Tests for the regex fallback backend."""

import asyncio
from pathlib import Path

from chat_to_cop.backend.regex_fallback import RegexBackend
from chat_to_cop.models.cop_update import CoPUpdate, UpdateType

FIXTURES = Path(__file__).parent / "fixtures"
CONFIG = Path(__file__).resolve().parent.parent / "config" / "track_patterns.json"


def _extract_sync(backend: RegexBackend, content: str) -> CoPUpdate:
    """Helper: run async extract synchronously with a user message."""
    return asyncio.run(
        backend.extract(
            messages=[{"role": "user", "content": content}],
            schema=CoPUpdate,
        )
    )


class TestPatternLoading:
    """Test pattern loading from config file."""

    def test_loads_track_patterns(self):
        backend = RegexBackend()
        assert len(backend._track_rules) > 0

    def test_loads_noise_patterns(self):
        backend = RegexBackend()
        assert len(backend._noise_rules) > 0

    def test_missing_file_no_crash(self, tmp_path):
        backend = RegexBackend(patterns_path=tmp_path / "nonexistent.json")
        assert len(backend._track_rules) == 0
        assert len(backend._noise_rules) == 0

    def test_repr(self):
        backend = RegexBackend()
        r = repr(backend)
        assert "RegexBackend" in r
        assert "track_rules" in r


class TestNoiseFilter:
    """Test noise filtering: acks, radio checks, dots."""

    def test_single_c(self):
        backend = RegexBackend()
        assert backend.is_noise("c")

    def test_copy(self):
        backend = RegexBackend()
        assert backend.is_noise("copy")

    def test_word(self):
        backend = RegexBackend()
        assert backend.is_noise("word")

    def test_single_dot(self):
        backend = RegexBackend()
        assert backend.is_noise(".")

    def test_double_dot(self):
        backend = RegexBackend()
        assert backend.is_noise("..")

    def test_test_msg(self):
        backend = RegexBackend()
        assert backend.is_noise("test")

    def test_real_message_not_noise(self):
        backend = RegexBackend()
        assert not backend.is_noise("RR15 F+40, RL36 F+50")

    def test_track_message_not_noise(self):
        backend = RegexBackend()
        assert not backend.is_noise("working TM636")


class TestTrackExtractor:
    """Test track number extraction with real DASH patterns."""

    def test_tm_three_digit(self):
        backend = RegexBackend()
        tracks = backend.extract_tracks("TM636 hostile fighter")
        assert "636" in tracks

    def test_da_three_digit(self):
        backend = RegexBackend()
        tracks = backend.extract_tracks("DA502 is friendly tanker")
        assert "502" in tracks

    def test_working_five_digit(self):
        backend = RegexBackend()
        tracks = backend.extract_tracks("working 44504")
        assert "44504" in tracks

    def test_contact_five_digit(self):
        backend = RegexBackend()
        tracks = backend.extract_tracks("contact 55123")
        assert "55123" in tracks

    def test_jtn_reference(self):
        """Track number with TN: prefix from DASH tacrep."""
        backend = RegexBackend()
        tracks = backend.extract_tracks("jtn TM677")
        assert "677" in tracks

    def test_spaced_tm(self):
        """STT sometimes spaces out digits: T M 6 3 6."""
        backend = RegexBackend()
        tracks = backend.extract_tracks("T M 6 3 6")
        assert "636" in tracks

    def test_multiple_tracks(self):
        backend = RegexBackend()
        tracks = backend.extract_tracks("TM636 and TM677 both hostile")
        assert "636" in tracks
        assert "677" in tracks

    def test_no_duplicate_tracks(self):
        backend = RegexBackend()
        tracks = backend.extract_tracks("TM636 TM636 TM636")
        assert tracks.count("636") == 1

    def test_tracks_ref(self):
        backend = RegexBackend()
        tracks = backend.extract_tracks("TN: 44123")
        assert "44123" in tracks

    def test_copy_track(self):
        """'44504 copy' pattern."""
        backend = RegexBackend()
        tracks = backend.extract_tracks("44504 copy")
        assert "44504" in tracks


class TestFuelExtractor:
    """Test fuel state extraction with real DASH messages."""

    def test_fplus(self):
        backend = RegexBackend()
        entities = backend.extract_fuel("RR15 F+40")
        assert len(entities) == 1
        assert entities[0].fuel_state == "F+40"
        assert entities[0].callsign == "RR15"

    def test_multiple_fuel(self):
        """Real DASH message: multiple aircraft fuel states."""
        backend = RegexBackend()
        entities = backend.extract_fuel("RR15 F+40, RL36 F+50")
        assert len(entities) == 2
        assert entities[0].fuel_state == "F+40"
        assert entities[1].fuel_state == "F+50"

    def test_fminus(self):
        backend = RegexBackend()
        entities = backend.extract_fuel("F-10 low fuel")
        assert len(entities) == 1
        assert entities[0].fuel_state == "F-10"

    def test_playtime(self):
        backend = RegexBackend()
        entities = backend.extract_fuel("playtime 15 min")
        assert len(entities) == 1
        assert entities[0].fuel_state == "playtime 15 min"

    def test_playtime_mike(self):
        """Military abbreviation: 'mike' = minutes."""
        backend = RegexBackend()
        entities = backend.extract_fuel("playtime 20 mike")
        assert len(entities) == 1
        assert entities[0].fuel_state == "playtime 20 min"

    def test_no_fuel(self):
        backend = RegexBackend()
        entities = backend.extract_fuel("NSTR")
        assert len(entities) == 0


class TestWeaponsExtractor:
    """Test weapons extraction."""

    def test_fox_three(self):
        backend = RegexBackend()
        entities = backend.extract_weapons("FOX 3")
        assert len(entities) == 1
        assert entities[0].weapon_type == "AMRAAM (active)"
        assert entities[0].weapon_qty_launched == 1

    def test_fox_one(self):
        backend = RegexBackend()
        entities = backend.extract_weapons("fox 1")
        assert len(entities) == 1
        assert "semi-active" in entities[0].weapon_type

    def test_splash(self):
        backend = RegexBackend()
        entities = backend.extract_weapons("splash 2")
        assert len(entities) == 1
        assert entities[0].operational_status == "DESTROYED"
        assert entities[0].weapon_qty_launched == 2

    def test_splash_no_count(self):
        backend = RegexBackend()
        entities = backend.extract_weapons("splash")
        assert len(entities) == 1
        assert entities[0].weapon_qty_launched == 1

    def test_weapon_type_with_count(self):
        backend = RegexBackend()
        entities = backend.extract_weapons("SM6 x4 launched")
        assert any(e.weapon_type == "SM6" for e in entities)
        assert any(e.weapon_qty_launched == 4 for e in entities)

    def test_tlam(self):
        backend = RegexBackend()
        entities = backend.extract_weapons("TLAM strike")
        assert any(e.weapon_type == "TLAM" for e in entities)

    def test_remaining(self):
        backend = RegexBackend()
        entities = backend.extract_weapons("4 remaining")
        assert len(entities) == 1
        assert entities[0].weapon_qty_remaining == 4

    def test_no_weapons(self):
        backend = RegexBackend()
        entities = backend.extract_weapons("NSTR")
        assert len(entities) == 0


class TestStatusExtractor:
    """Test operational status extraction."""

    def test_rtb(self):
        backend = RegexBackend()
        entities = backend.extract_status("ORCA01 RTB bingo fuel")
        assert len(entities) == 1
        assert entities[0].operational_status == "RTB"

    def test_inop(self):
        backend = RegexBackend()
        entities = backend.extract_status("radar inop")
        assert len(entities) == 1
        assert entities[0].operational_status == "INOP"

    def test_gadget_bent(self):
        """DASH jargon: 'gadget bent' = radar failure."""
        backend = RegexBackend()
        entities = backend.extract_status("gadget bent")
        assert len(entities) == 1
        assert entities[0].operational_status == "DEGRADED"
        assert entities[0].subsystem_status == "radar inop"

    def test_on_boom(self):
        """'on boom' = aerial refueling."""
        backend = RegexBackend()
        entities = backend.extract_status("HADES flt on boom")
        assert len(entities) == 1
        assert entities[0].operational_status == "OPERATIONAL"
        assert entities[0].metadata.get("activity") == "aerial refueling"

    def test_winchester(self):
        """'winchester' = out of weapons."""
        backend = RegexBackend()
        entities = backend.extract_status("ORCA01 winchester")
        assert len(entities) == 1
        assert entities[0].operational_status == "DEGRADED"
        assert "winchester" in entities[0].metadata.get("detail", "")

    def test_destroyed(self):
        backend = RegexBackend()
        entities = backend.extract_status("target destroyed")
        assert len(entities) == 1
        assert entities[0].operational_status == "DESTROYED"

    def test_no_status(self):
        backend = RegexBackend()
        entities = backend.extract_status("RR15 F+40")
        assert len(entities) == 0


class TestCoordinateExtractor:
    """Test coordinate extraction."""

    def test_bullseye(self):
        backend = RegexBackend()
        entities = backend.extract_coordinates("cigar 316/398")
        assert len(entities) == 1
        assert entities[0].bearing == 316.0
        assert entities[0].range_nm == 398.0

    def test_bullseye_keyword(self):
        backend = RegexBackend()
        entities = backend.extract_coordinates("bullseye 045/120")
        assert len(entities) == 1
        assert entities[0].bearing == 45.0
        assert entities[0].range_nm == 120.0

    def test_latlon(self):
        backend = RegexBackend()
        entities = backend.extract_coordinates("position 34.5N/118.2W")
        assert len(entities) == 1
        assert entities[0].latitude == 34.5
        assert entities[0].longitude == -118.2

    def test_mgrs(self):
        backend = RegexBackend()
        entities = backend.extract_coordinates("grid 38SMB1234567890")
        assert len(entities) == 1
        assert "38SMB1234567890" in entities[0].metadata.get("mgrs", "")

    def test_no_coords(self):
        backend = RegexBackend()
        entities = backend.extract_coordinates("NSTR")
        assert len(entities) == 0

    def test_dash_tacrep_cigar(self):
        """Real DASH tacrep: 'cigar 316/398'."""
        backend = RegexBackend()
        entities = backend.extract_coordinates("tacrep e10-4, 4th-gen sam active, cigar 316/398, jtn TM677")
        assert len(entities) >= 1
        assert any(e.bearing == 316.0 and e.range_nm == 398.0 for e in entities)


class TestExtractProtocol:
    """Test the async extract() method (LLMBackend protocol compliance)."""

    def test_implements_protocol(self):
        from chat_to_cop.backend.base import LLMBackend

        backend = RegexBackend()
        assert isinstance(backend, LLMBackend)

    def test_noise_returns_none_type(self):
        result = _extract_sync(RegexBackend(), "c")
        assert result.update_type == UpdateType.NONE
        assert result.confidence < 0.5
        assert result.extraction_method == "regex"

    def test_fuel_message(self):
        """Real DASH message: Hydro_Tank fuel report."""
        result = _extract_sync(RegexBackend(), "RR15 F+40, RL36 F+50")
        assert result.update_type == UpdateType.FUEL
        assert result.confidence < 0.5
        assert result.extraction_method == "regex"
        assert len(result.entities) >= 2

    def test_track_message(self):
        result = _extract_sync(RegexBackend(), "working TM636")
        assert result.update_type == UpdateType.ENTITY_ID
        assert result.confidence < 0.5
        assert result.extraction_method == "regex"
        assert any(e.track_number == "636" for e in result.entities)

    def test_combined_message(self):
        """Complex message with tracks, coords, and status."""
        result = _extract_sync(
            RegexBackend(),
            "tacrep e10-4, 4th-gen sam active, cigar 316/398, jtn TM677",
        )
        assert result.confidence < 0.5
        assert result.extraction_method == "regex"
        assert len(result.entities) >= 2  # track + coords at minimum

    def test_status_message(self):
        result = _extract_sync(RegexBackend(), "HADES flt on boom")
        assert result.update_type == UpdateType.STATUS_CHANGE
        assert result.confidence < 0.5
        assert result.extraction_method == "regex"

    def test_weapons_message(self):
        result = _extract_sync(RegexBackend(), "FOX 3 on TM636")
        assert result.update_type in (UpdateType.WEAPONS, UpdateType.ENTITY_ID)
        assert result.confidence < 0.5
        assert result.extraction_method == "regex"

    def test_no_match_returns_none(self):
        """Message with no extractable content."""
        result = _extract_sync(RegexBackend(), "good morning everyone")
        assert result.update_type == UpdateType.NONE
        assert result.confidence < 0.5
        assert result.extraction_method == "regex"

    def test_source_message_preserved(self):
        result = _extract_sync(RegexBackend(), "RR15 F+40")
        assert result.source_message == "RR15 F+40"

    def test_confidence_scales_with_hits(self):
        """More extractor matches = higher confidence (but still < 0.5)."""
        backend = RegexBackend()
        # Single extractor
        r1 = _extract_sync(backend, "RR15 F+40")
        # Multiple extractors (track + coords + status)
        r2 = _extract_sync(backend, "TM636 RTB, cigar 316/398")
        assert r2.confidence > r1.confidence
        assert r2.confidence < 0.5

    def test_missing_patterns_still_works(self, tmp_path):
        """Backend with no patterns file still returns valid CoPUpdate."""
        backend = RegexBackend(patterns_path=tmp_path / "missing.json")
        result = _extract_sync(backend, "TM636 hostile")
        assert result.extraction_method == "regex"
        assert result.confidence < 0.5


class TestDASHRealMessages:
    """Tests using real message patterns from DASH events."""

    def test_startex(self):
        """STARTEX message is not noise but has no extractable state."""
        result = _extract_sync(
            RegexBackend(),
            "***********************STARTEX DASH 3 GBC Run 10************************",
        )
        assert result.update_type == UpdateType.NONE

    def test_sitrep_with_tracks(self):
        """SITREP with shot-down aircraft."""
        result = _extract_sync(
            RegexBackend(),
            "SITREP / AIR: ZEUS 12,13,14 shot down by TTG; YAMA11 flight shot down by TTG",
        )
        # Should at least detect "destroyed" or "shot down" isn't a direct regex match
        # but the overall message is not noise
        assert result.extraction_method == "regex"

    def test_nstr(self):
        """NSTR = nothing significant to report."""
        result = _extract_sync(RegexBackend(), "NSTR")
        assert result.update_type == UpdateType.NONE

    def test_hydro_fuel_report(self):
        """Real Hydro_Tank fuel report from DASH 3."""
        result = _extract_sync(RegexBackend(), "RR15 F+40, RL36 F+50")
        assert result.update_type == UpdateType.FUEL
        assert len(result.entities) == 2
        callsigns = [e.callsign for e in result.entities]
        assert "RR15" in callsigns
        assert "RL36" in callsigns

    def test_tacrep(self):
        """Real AOC_SIDO tacrep from DASH 3."""
        result = _extract_sync(
            RegexBackend(),
            "tacrep e10-4, 4th-gen sam active, cigar 316/398, jtn TM677",
        )
        assert result.extraction_method == "regex"
        # Should get track + coords
        has_track = any(e.track_number for e in result.entities)
        has_coords = any(e.bearing is not None for e in result.entities)
        assert has_track
        assert has_coords

    def test_on_boom(self):
        """Real HYDRO_Strike message."""
        result = _extract_sync(RegexBackend(), "HADES flt on boom")
        assert result.update_type == UpdateType.STATUS_CHANGE
        assert any(e.metadata.get("activity") == "aerial refueling" for e in result.entities)

    def test_copy_shot_down(self):
        """VEGAS_SL reporting shot-down aircraft."""
        result = _extract_sync(
            RegexBackend(),
            "copy harpy12 harpy14 thor13 shark14 all shot down",
        )
        # Should pick up "destroyed" status at minimum
        assert result.extraction_method == "regex"


# ---------------------------------------------------------------------------
# DELTRON-sourced pattern tests (#63, from HLT meeting 2026-04-09, slide 10)
# ---------------------------------------------------------------------------


class TestDeltronCSARPatterns:
    """Test CSAR / emergency patterns sourced from DELTRON."""

    def test_boltout(self):
        result = _extract_sync(RegexBackend(), "SHARK14 bolt out bolt out")
        assert result.update_type == UpdateType.CSAR
        assert any(e.operational_status == "EJECTED" for e in result.entities)

    def test_bail_out(self):
        result = _extract_sync(RegexBackend(), "pilot bailed out near bullseye 270/40")
        assert any(e.operational_status == "EJECTED" for e in result.entities)

    def test_csar_keyword(self):
        result = _extract_sync(RegexBackend(), "initiating CSAR for downed pilot")
        assert result.update_type == UpdateType.CSAR
        assert any(e.metadata.get("event") == "CSAR" for e in result.entities)

    def test_chopsard_stt_mishearing(self):
        """STT commonly mishears 'CSAR' as 'chopsard'."""
        result = _extract_sync(RegexBackend(), "chopsard in progress")
        assert result.update_type == UpdateType.CSAR

    def test_defending(self):
        result = _extract_sync(RegexBackend(), "ORCA01 defending, defensive")
        assert any(e.metadata.get("event") == "defending" for e in result.entities)

    def test_boltout_negative(self):
        """Normal 'bolt' usage should not trigger."""
        result = _extract_sync(RegexBackend(), "lightning bolt near the field")
        assert not any(e.operational_status == "EJECTED" for e in result.entities)


class TestDeltronCrashPatterns:
    """Test crash / emergency patterns sourced from DELTRON."""

    def test_crash(self):
        result = _extract_sync(RegexBackend(), "SHARK14 crashed near the runway")
        assert any(e.operational_status == "DESTROYED" for e in result.entities)
        assert any(e.metadata.get("event") == "crash" for e in result.entities)

    def test_crash_event(self):
        result = _extract_sync(RegexBackend(), "crash event reported at grid XK4423")
        assert any(e.operational_status == "DESTROYED" for e in result.entities)

    def test_smoke_in_cockpit(self):
        result = _extract_sync(RegexBackend(), "smoke in cockpit, ORCA01 RTB")
        assert any(e.operational_status == "DEGRADED" for e in result.entities)
        assert any("smoke" in e.metadata.get("event", "") for e in result.entities)

    def test_emergency_rtb(self):
        result = _extract_sync(RegexBackend(), "TYPHOON11 emergency RTB")
        assert any(e.operational_status == "RTB" for e in result.entities)
        assert any(e.metadata.get("event") == "emergency RTB" for e in result.entities)


class TestDeltronBrevityPatterns:
    """Test catastrophic brevity code patterns sourced from DELTRON."""

    def test_broken_arrow(self):
        result = _extract_sync(RegexBackend(), "BROKEN ARROW BROKEN ARROW")
        assert any(e.metadata.get("brevity_code") == "BROKEN ARROW" for e in result.entities)

    def test_broken_arrow_case_insensitive(self):
        result = _extract_sync(RegexBackend(), "broken arrow reported")
        assert any(e.metadata.get("brevity_code") == "BROKEN ARROW" for e in result.entities)


class TestDeltronEntityDownPatterns:
    """Test entity-down patterns sourced from DELTRON."""

    def test_callsign_is_down(self):
        result = _extract_sync(RegexBackend(), "SHARK14 is down")
        assert any(e.callsign == "SHARK14" and e.operational_status == "DESTROYED" for e in result.entities)

    def test_count_down(self):
        result = _extract_sync(RegexBackend(), "8 down west of the river")
        assert any(e.operational_status == "DESTROYED" for e in result.entities)
        assert any(e.metadata.get("count") == "8" for e in result.entities)

    def test_shot_down(self):
        result = _extract_sync(RegexBackend(), "ZEUS12 shot down by TTG")
        assert any(e.operational_status == "DESTROYED" for e in result.entities)

    def test_went_down(self):
        result = _extract_sync(RegexBackend(), "YAMA11 went down near cigar 270/40")
        assert any(e.callsign == "YAMA11" and e.operational_status == "DESTROYED" for e in result.entities)


class TestDeltronThreatPatterns:
    """Test threat patterns sourced from DELTRON."""

    def test_sa21_active(self):
        result = _extract_sync(RegexBackend(), "SA-21 active near cigar 316/398")
        assert result.update_type == UpdateType.THREAT
        assert any(e.platform_type == "SA-21" and e.affiliation == "HOSTILE" for e in result.entities)

    def test_sam_active(self):
        result = _extract_sync(RegexBackend(), "SAM active, bullseye 180/50")
        assert any(e.affiliation == "HOSTILE" for e in result.entities)

    def test_4th_gen_sam(self):
        result = _extract_sync(RegexBackend(), "tacrep e10-4, 4th-gen sam active, cigar 316/398")
        assert result.update_type == UpdateType.THREAT
        assert any(e.affiliation == "HOSTILE" for e in result.entities)

    def test_tacrep_sam_awake(self):
        result = _extract_sync(RegexBackend(), "tacrep: SAM awake at bullseye 090/120")
        assert any(e.affiliation == "HOSTILE" for e in result.entities)

    def test_sa21_negative(self):
        """Regular number shouldn't trigger SA-## pattern."""
        result = _extract_sync(RegexBackend(), "we have 21 active sorties")
        assert not any(e.affiliation == "HOSTILE" for e in result.entities)

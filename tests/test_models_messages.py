"""Tests for IRCMessage model, routing types, and EntityUpdate validators."""

from datetime import datetime, timezone

from chat_to_cop.models.cop_update import CapabilityImpact, EntityUpdate
from chat_to_cop.models.messages import ChannelPriority, IRCMessage


class TestIRCMessage:
    """Test IRCMessage construction and properties."""

    def test_raw_line_defaults_empty(self):
        """raw_line is optional and defaults to empty string (not None)."""
        msg = IRCMessage(
            timestamp=datetime(2025, 9, 23, 14, 30, 0, tzinfo=timezone.utc),
            channel="#c2_coord",
            sender="Hydro_Tank",
            content="RR15 F+40, RL36 F+50",
        )
        assert msg.raw_line == ""
        assert isinstance(msg.raw_line, str)

    def test_is_stt_typed_chat(self):
        msg = IRCMessage(
            timestamp=datetime.now(tz=timezone.utc),
            channel="#c2_coord",
            sender="Hydro_Tank",
            content="test",
        )
        assert msg.is_stt is False

    def test_is_stt_voice_channel(self):
        msg = IRCMessage(
            timestamp=datetime.now(tz=timezone.utc),
            channel="#stt_hydroBMA",
            sender="google-speech-to-text",
            content="splash two flankers",
        )
        assert msg.is_stt is True

    def test_priority_high_channel(self):
        msg = IRCMessage(
            timestamp=datetime.now(tz=timezone.utc),
            channel="#c2_coord",
            sender="test",
            content="test",
        )
        assert msg.priority == ChannelPriority.HIGH

    def test_priority_medium_channel(self):
        msg = IRCMessage(
            timestamp=datetime.now(tz=timezone.utc),
            channel="#stt_hydroBMA",
            sender="test",
            content="test",
        )
        assert msg.priority == ChannelPriority.MEDIUM

    def test_priority_low_channel(self):
        msg = IRCMessage(
            timestamp=datetime.now(tz=timezone.utc),
            channel="#vegas_internal",
            sender="test",
            content="test",
        )
        assert msg.priority == ChannelPriority.LOW

    def test_priority_unknown_channel_defaults_medium(self):
        msg = IRCMessage(
            timestamp=datetime.now(tz=timezone.utc),
            channel="#new_channel_mid_exercise",
            sender="test",
            content="test",
        )
        assert msg.priority == ChannelPriority.MEDIUM

    def test_empty_content_survives_roundtrip(self):
        """Empty content (ack dots, blank lines) must serialize and deserialize."""
        msg = IRCMessage(
            timestamp=datetime(2025, 9, 23, 14, 0, 0, tzinfo=timezone.utc),
            channel="#c2_coord",
            sender="Hydro_SL",
            content="",
        )
        restored = IRCMessage.model_validate_json(msg.model_dump_json())
        assert restored.content == ""
        assert restored == msg

    def test_unicode_content_survives_roundtrip(self):
        """DMS coordinates with degree symbols must survive JSON roundtrip."""
        content = "Position: N24\u00b003.6134' W074\u00b031.4900'"
        msg = IRCMessage(
            timestamp=datetime(2025, 9, 23, 14, 0, 0, tzinfo=timezone.utc),
            channel="#c2_coord",
            sender="Hydro_SL",
            content=content,
        )
        restored = IRCMessage.model_validate_json(msg.model_dump_json())
        assert restored.content == content
        assert "\u00b0" in restored.content

    def test_long_sitrep_survives_roundtrip(self):
        """Multi-entity SITREPs can be >1000 chars; must not truncate."""
        long_msg = "SITREP / AIR: " + "ZEUS31 shot down by TTG; " * 50
        msg = IRCMessage(
            timestamp=datetime(2025, 9, 23, 14, 0, 0, tzinfo=timezone.utc),
            channel="#c2_coord",
            sender="HYDRO_SL",
            content=long_msg,
        )
        restored = IRCMessage.model_validate_json(msg.model_dump_json())
        assert restored.content == long_msg

    def test_serialization_roundtrip(self):
        msg = IRCMessage(
            timestamp=datetime(2025, 9, 23, 14, 30, 0, tzinfo=timezone.utc),
            channel="#fires",
            sender="3MARDIV_FIRES",
            content="FIRE MISSION! 17QNE9779269855",
            raw_line="[14:30:00] 3MARDIV_FIRES: FIRE MISSION! 17QNE9779269855",
        )
        json_str = msg.model_dump_json()
        restored = IRCMessage.model_validate_json(json_str)
        assert restored == msg

    def test_all_high_priority_channels(self):
        for channel in ["#c2_coord", "#isr_reports", "#fires"]:
            msg = IRCMessage(
                timestamp=datetime.now(tz=timezone.utc),
                channel=channel,
                sender="test",
                content="test",
            )
            assert msg.priority == ChannelPriority.HIGH, f"{channel} should be HIGH"

    def test_all_stt_channels_are_stt(self):
        for channel in ["#stt_C2Coord", "#stt_hydroBMA", "#stt_crusherBMA", "#stt_mesquiteBMA", "#stt_taipanBMA"]:
            msg = IRCMessage(
                timestamp=datetime.now(tz=timezone.utc),
                channel=channel,
                sender="asr_bot",
                content="test",
            )
            assert msg.is_stt is True, f"{channel} should be STT"


class TestChannelPriority:
    """Test ChannelPriority enum and channel-to-priority mapping."""

    def test_enum_has_exactly_three_values(self):
        """Guard against accidental addition/removal of priority levels."""
        assert set(ChannelPriority) == {ChannelPriority.HIGH, ChannelPriority.MEDIUM, ChannelPriority.LOW}

    def test_comparison_for_sorting(self):
        """Priorities should be sortable for load shedding."""
        priorities = [ChannelPriority.LOW, ChannelPriority.HIGH, ChannelPriority.MEDIUM]
        # Sort by custom order for load shedding
        order = {ChannelPriority.HIGH: 0, ChannelPriority.MEDIUM: 1, ChannelPriority.LOW: 2}
        sorted_priorities = sorted(priorities, key=lambda p: order[p])
        assert sorted_priorities == [ChannelPriority.HIGH, ChannelPriority.MEDIUM, ChannelPriority.LOW]


class TestEntityUpdateBullseyeValidator:
    """Test the bearing/bullseye notation validator on EntityUpdate."""

    def test_float_bearing_unchanged(self):
        ent = EntityUpdate(bearing=240.0)
        assert ent.bearing == 240.0

    def test_int_bearing_converted_to_float(self):
        ent = EntityUpdate(bearing=240)
        assert ent.bearing == 240.0

    def test_none_bearing_stays_none(self):
        ent = EntityUpdate(bearing=None)
        assert ent.bearing is None

    def test_bullseye_string_parsed(self):
        """'240/405' should parse bearing=240.0, range_nm=405.0."""
        ent = EntityUpdate.model_validate({"bearing": "240/405"})
        assert ent.bearing == 240.0
        assert ent.range_nm == 405.0
        assert ent.metadata["bullseye_range"] == "405.0"

    def test_bullseye_does_not_overwrite_existing_range(self):
        """If range_nm is already set, bullseye range should not overwrite it."""
        ent = EntityUpdate.model_validate({"bearing": "240/405", "range_nm": 100.0})
        assert ent.bearing == 240.0
        assert ent.range_nm == 100.0
        assert ent.metadata["bullseye_range"] == "405.0"

    def test_bullseye_three_digit_bearing(self):
        ent = EntityUpdate.model_validate({"bearing": "316/398"})
        assert ent.bearing == 316.0
        assert ent.range_nm == 398.0

    def test_string_number_parsed(self):
        """A plain string number like '180' should parse to float."""
        ent = EntityUpdate.model_validate({"bearing": "180"})
        assert ent.bearing == 180.0

    def test_invalid_string_bearing_becomes_none(self):
        ent = EntityUpdate.model_validate({"bearing": "north"})
        assert ent.bearing is None

    def test_invalid_bullseye_bearing_part(self):
        ent = EntityUpdate.model_validate({"bearing": "abc/405"})
        assert ent.bearing is None

    def test_invalid_bullseye_range_part(self):
        """Invalid range part should still parse the bearing."""
        ent = EntityUpdate.model_validate({"bearing": "240/abc"})
        assert ent.bearing == 240.0
        assert ent.range_nm is None

    def test_bullseye_with_whitespace(self):
        ent = EntityUpdate.model_validate({"bearing": " 240/405 "})
        assert ent.bearing == 240.0
        assert ent.range_nm == 405.0

    def test_metadata_preserved_with_bullseye(self):
        """Existing metadata should be preserved when bullseye adds to it."""
        ent = EntityUpdate.model_validate(
            {
                "bearing": "240/405",
                "metadata": {"source": "SIGINT"},
            }
        )
        assert ent.bearing == 240.0
        assert ent.metadata["bullseye_range"] == "405.0"
        assert ent.metadata["source"] == "SIGINT"

    def test_normal_construction_unaffected(self):
        """Normal keyword construction with float bearing should work as before."""
        ent = EntityUpdate(
            track_number="TM677",
            bearing=316.0,
            range_nm=398.0,
            affiliation="HOSTILE",
        )
        assert ent.bearing == 316.0
        assert ent.range_nm == 398.0
        assert ent.track_number == "TM677"


class TestEntityUpdateCapabilityImpactValidator:
    """Test that invalid capability_impact values are coerced to None."""

    def test_valid_sense(self):
        ent = EntityUpdate.model_validate({"capability_impact": "sense"})
        assert ent.capability_impact == CapabilityImpact.SENSE

    def test_valid_decide(self):
        ent = EntityUpdate.model_validate({"capability_impact": "decide"})
        assert ent.capability_impact == CapabilityImpact.DECIDE

    def test_valid_act(self):
        ent = EntityUpdate.model_validate({"capability_impact": "act"})
        assert ent.capability_impact == CapabilityImpact.ACT

    def test_valid_collaborate(self):
        ent = EntityUpdate.model_validate({"capability_impact": "collaborate"})
        assert ent.capability_impact == CapabilityImpact.COLLABORATE

    def test_invalid_tasking_becomes_none(self):
        ent = EntityUpdate.model_validate({"capability_impact": "tasking"})
        assert ent.capability_impact is None

    def test_invalid_radar_contact_becomes_none(self):
        ent = EntityUpdate.model_validate({"capability_impact": "radar_contact"})
        assert ent.capability_impact is None

    def test_invalid_extraction_becomes_none(self):
        ent = EntityUpdate.model_validate({"capability_impact": "extraction"})
        assert ent.capability_impact is None

    def test_none_stays_none(self):
        ent = EntityUpdate.model_validate({"capability_impact": None})
        assert ent.capability_impact is None

    def test_missing_field_defaults_none(self):
        ent = EntityUpdate.model_validate({})
        assert ent.capability_impact is None

    def test_valid_value_with_other_fields(self):
        """Ensure the validator doesn't interfere with other fields."""
        ent = EntityUpdate.model_validate(
            {
                "track_number": "TM636",
                "callsign": "ORCA01",
                "capability_impact": "act",
                "bearing": "240/405",
            }
        )
        assert ent.capability_impact == CapabilityImpact.ACT
        assert ent.track_number == "TM636"
        assert ent.bearing == 240.0


class TestEntityUpdateMetadataCoercion:
    """Test that metadata dict values are coerced to strings."""

    def test_string_values_unchanged(self):
        ent = EntityUpdate.model_validate({"metadata": {"key": "value"}})
        assert ent.metadata == {"key": "value"}

    def test_int_value_coerced_to_string(self):
        ent = EntityUpdate.model_validate({"metadata": {"count": 42}})
        assert ent.metadata == {"count": "42"}

    def test_float_value_coerced_to_string(self):
        ent = EntityUpdate.model_validate({"metadata": {"score": 0.95}})
        assert ent.metadata == {"score": "0.95"}

    def test_none_value_dropped(self):
        ent = EntityUpdate.model_validate({"metadata": {"key": "value", "empty": None}})
        assert ent.metadata == {"key": "value"}
        assert "empty" not in ent.metadata

    def test_all_none_values_produces_empty_dict(self):
        ent = EntityUpdate.model_validate({"metadata": {"a": None, "b": None}})
        assert ent.metadata == {}

    def test_mixed_types(self):
        ent = EntityUpdate.model_validate({"metadata": {"name": "ORCA01", "count": 3, "flag": True, "gone": None}})
        assert ent.metadata == {"name": "ORCA01", "count": "3", "flag": "True"}

    def test_int_key_coerced_to_string(self):
        ent = EntityUpdate.model_validate({"metadata": {1: "one"}})
        assert ent.metadata == {"1": "one"}

    def test_non_dict_becomes_empty(self):
        ent = EntityUpdate.model_validate({"metadata": "not a dict"})
        assert ent.metadata == {}

    def test_empty_dict_unchanged(self):
        ent = EntityUpdate.model_validate({"metadata": {}})
        assert ent.metadata == {}

    def test_default_empty_dict(self):
        ent = EntityUpdate()
        assert ent.metadata == {}

    def test_bullseye_metadata_still_works(self):
        """Bullseye validator writes string values to metadata; coercion should not interfere."""
        ent = EntityUpdate.model_validate({"bearing": "240/405", "metadata": {"source": "SIGINT"}})
        assert ent.metadata["bullseye_range"] == "405.0"
        assert ent.metadata["source"] == "SIGINT"

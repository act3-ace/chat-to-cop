"""Tests for IRCMessage model and routing types."""

from datetime import datetime, timezone

from chat_to_cop.models.messages import ChannelPriority, IRCMessage


class TestIRCMessage:
    """Test IRCMessage construction and properties."""

    def test_basic_construction(self):
        msg = IRCMessage(
            timestamp=datetime(2025, 9, 23, 14, 30, 0, tzinfo=timezone.utc),
            channel="#c2_coord",
            sender="Hydro_Tank",
            content="RR15 F+40, RL36 F+50",
        )
        assert msg.channel == "#c2_coord"
        assert msg.sender == "Hydro_Tank"
        assert msg.content == "RR15 F+40, RL36 F+50"
        assert msg.raw_line == ""

    def test_raw_line_preserved(self):
        raw = "[14:30:00] Hydro_Tank: RR15 F+40, RL36 F+50"
        msg = IRCMessage(
            timestamp=datetime(2025, 9, 23, 14, 30, 0, tzinfo=timezone.utc),
            channel="#c2_coord",
            sender="Hydro_Tank",
            content="RR15 F+40, RL36 F+50",
            raw_line=raw,
        )
        assert msg.raw_line == raw

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

    def test_empty_content(self):
        msg = IRCMessage(
            timestamp=datetime.now(tz=timezone.utc),
            channel="#c2_coord",
            sender="Hydro_SL",
            content="",
        )
        assert msg.content == ""

    def test_unicode_content(self):
        msg = IRCMessage(
            timestamp=datetime.now(tz=timezone.utc),
            channel="#c2_coord",
            sender="Hydro_SL",
            content="Position: N24\u00b003.6134' W074\u00b031.4900'",
        )
        assert "\u00b0" in msg.content

    def test_long_content(self):
        """SITREP messages can be very long."""
        long_msg = "SITREP / AIR: " + "ZEUS31 shot down by TTG; " * 50
        msg = IRCMessage(
            timestamp=datetime.now(tz=timezone.utc),
            channel="#c2_coord",
            sender="HYDRO_SL",
            content=long_msg,
        )
        assert len(msg.content) > 1000

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
    """Test ChannelPriority enum."""

    def test_string_values(self):
        assert ChannelPriority.HIGH == "HIGH"
        assert ChannelPriority.MEDIUM == "MEDIUM"
        assert ChannelPriority.LOW == "LOW"

    def test_comparison_for_sorting(self):
        """Priorities should be sortable for load shedding."""
        priorities = [ChannelPriority.LOW, ChannelPriority.HIGH, ChannelPriority.MEDIUM]
        # Sort by custom order for load shedding
        order = {ChannelPriority.HIGH: 0, ChannelPriority.MEDIUM: 1, ChannelPriority.LOW: 2}
        sorted_priorities = sorted(priorities, key=lambda p: order[p])
        assert sorted_priorities == [ChannelPriority.HIGH, ChannelPriority.MEDIUM, ChannelPriority.LOW]

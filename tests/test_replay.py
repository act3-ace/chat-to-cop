"""Tests for the DASH chat log replay parser."""

import asyncio
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from chat_to_cop.ingestion.replay import (
    _channel_from_filename,
    _extract_date_from_path,
    parse_file,
    parse_line,
    parse_path,
    parse_zip,
    replay_messages,
)

FIXTURES = Path(__file__).parent / "fixtures"


class TestParseLine:
    """Test individual line parsing."""

    def test_dash1_format(self):
        msg = parse_line("[10:39:06] WF_Clark: STARTEX", default_channel="#c2_coord")
        assert msg is not None
        assert msg.sender == "WF_Clark"
        assert msg.content == "STARTEX"
        assert msg.channel == "#c2_coord"
        assert msg.timestamp.hour == 10
        assert msg.timestamp.minute == 39

    def test_combined_format(self):
        msg = parse_line("[14:03:36] #fires HYDRO_Strike: ref DA011: Rank 1 FLOATER11")
        assert msg is not None
        assert msg.channel == "#fires"
        assert msg.sender == "HYDRO_Strike"
        assert msg.content == "ref DA011: Rank 1 FLOATER11"

    def test_stt_combined(self):
        msg = parse_line("[14:05:22] #stt_C2Coord afrl_lavgn: <WF2> Radio check")
        assert msg is not None
        assert msg.channel == "#stt_C2Coord"
        assert msg.is_stt is True
        assert msg.content == "<WF2> Radio check"

    def test_empty_line(self):
        assert parse_line("") is None
        assert parse_line("   ") is None

    def test_unparseable_line(self):
        assert parse_line("just some random text without timestamp") is None

    def test_message_with_colons(self):
        """Messages often contain colons in content."""
        msg = parse_line("[10:39:34] wf_beep: AOC_CCO: @Hydro status Lane Wynn", default_channel="#c2")
        assert msg is not None
        assert msg.sender == "wf_beep"
        assert msg.content == "AOC_CCO: @Hydro status Lane Wynn"

    def test_raw_line_preserved(self):
        raw = "[10:39:06] WF_Clark: STARTEX"
        msg = parse_line(raw, default_channel="#c2")
        assert msg is not None
        assert msg.raw_line == raw

    def test_reference_date(self):
        ref = datetime(2025, 4, 1, tzinfo=timezone.utc)
        msg = parse_line("[10:39:06] WF_Clark: STARTEX", default_channel="#c2", reference_date=ref)
        assert msg is not None
        assert msg.timestamp.month == 4
        assert msg.timestamp.day == 1
        assert msg.timestamp.year == 2025

    def test_dot_ack_message(self):
        msg = parse_line("[14:03:03] VEGAS_ABM2: .", default_channel="#c2_coord")
        assert msg is not None
        assert msg.content == "."


class TestChannelFromFilename:
    """Test channel name extraction from filenames."""

    def test_dash3_channel_log(self):
        assert _channel_from_filename("#c2_coord_2025-09-17_14-58-01.log") == "#c2_coord"

    def test_dash3_stt_channel(self):
        assert _channel_from_filename("#stt_C2Coord_2025-09-17_14-58-05.log") == "#stt_C2Coord"

    def test_dash3_fires(self):
        assert _channel_from_filename("#fires_2025-09-17_20-20-06.log") == "#fires"

    def test_combined_log(self):
        # combined.log doesn't start with #, should get # prepended
        result = _channel_from_filename("combined.log")
        assert result.startswith("#")

    def test_plain_txt(self):
        result = _channel_from_filename("c2.log.txt")
        assert result.startswith("#")


class TestExtractDate:
    """Test date extraction from file paths."""

    def test_dash1_path(self):
        p = Path("Data/1Apr/c2.log.txt")
        dt = _extract_date_from_path(p)
        assert dt is not None
        assert dt.month == 4
        assert dt.day == 1

    def test_dash3_path(self):
        p = Path("Data/23Sep/usaf/chat.zip")
        dt = _extract_date_from_path(p)
        assert dt is not None
        assert dt.month == 9
        assert dt.day == 23

    def test_no_date_in_path(self):
        p = Path("some/random/path/file.txt")
        assert _extract_date_from_path(p) is None

    def test_two_digit_day(self):
        p = Path("Data/16Sep/base1/chat.zip")
        dt = _extract_date_from_path(p)
        assert dt is not None
        assert dt.day == 16


class TestParseFile:
    """Test full file parsing."""

    def test_parse_dash1_sample(self):
        msgs = parse_file(FIXTURES / "sample_dash1.txt", default_channel="#c2_coord")
        assert len(msgs) == 10
        assert msgs[0].sender == "WF_Clark"
        assert msgs[0].content == "STARTEX"
        assert all(m.channel == "#c2_coord" for m in msgs)

    def test_parse_dash3_channel_sample(self):
        msgs = parse_file(FIXTURES / "sample_dash3_channel.txt", default_channel="#c2_coord")
        assert len(msgs) == 10
        assert msgs[0].sender == "wf_beep"
        assert msgs[-1].sender == "AOC_SIDO"
        assert "cigar 316/398" in msgs[-1].content

    def test_parse_dash3_combined_sample(self):
        msgs = parse_file(FIXTURES / "sample_dash3_combined.txt")
        assert len(msgs) == 8
        # Combined format should have different channels
        channels = {m.channel for m in msgs}
        assert "#c2_coord" in channels
        assert "#fires" in channels
        assert "#isr_reports" in channels
        assert "#stt_C2Coord" in channels


class TestParseZip:
    """Test zip archive parsing."""

    def test_parse_zip_with_combined(self, tmp_path):
        """Create a minimal zip with combined.log and verify parsing."""
        zip_path = tmp_path / "23Sep" / "chat.zip"
        zip_path.parent.mkdir(parents=True)
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr(
                "combined.log",
                "[14:00:00] #c2_coord VEGAS_SL: hello world\n[14:00:05] #fires HYDRO_Strike: fire mission\n",
            )
        msgs = parse_zip(zip_path)
        assert len(msgs) == 2
        assert msgs[0].channel == "#c2_coord"
        assert msgs[1].channel == "#fires"
        # Date from path
        assert msgs[0].timestamp.month == 9
        assert msgs[0].timestamp.day == 23

    def test_parse_zip_per_channel_fallback(self, tmp_path):
        """When no combined.log, parse individual channel files."""
        zip_path = tmp_path / "chat.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr(
                "#c2_coord_2025-09-23_14-00-00.log",
                "[14:00:00] VEGAS_SL: hello\n",
            )
            zf.writestr(
                "#fires_2025-09-23_14-00-00.log",
                "[14:00:01] HYDRO_Strike: fire\n",
            )
        msgs = parse_zip(zip_path)
        assert len(msgs) == 2
        assert msgs[0].channel == "#c2_coord"
        assert msgs[1].channel == "#fires"


class TestParsePath:
    """Test auto-detect path parsing."""

    def test_single_file(self):
        msgs = parse_path(FIXTURES / "sample_dash1.txt")
        assert len(msgs) == 10

    def test_directory(self):
        msgs = parse_path(FIXTURES)
        # Should find all three sample files
        assert len(msgs) > 0
        # Should have messages from multiple files
        channels = {m.channel for m in msgs}
        assert len(channels) > 1  # Combined file has multiple channels

    def test_nonexistent_path(self):
        msgs = parse_path(Path("/nonexistent/path"))
        assert msgs == []


class TestReplayMessages:
    """Test the async replay generator."""

    def test_replay_instant(self):
        """speed=0 should yield all messages without delay."""

        async def run():
            msgs = []
            async for msg in replay_messages(FIXTURES / "sample_dash1.txt", speed=0):
                msgs.append(msg)
            return msgs

        msgs = asyncio.run(run())
        assert len(msgs) == 10
        # Verify timestamp ordering
        for i in range(1, len(msgs)):
            assert msgs[i].timestamp >= msgs[i - 1].timestamp

    def test_replay_empty_file(self, tmp_path):
        """Empty file should yield nothing."""
        empty = tmp_path / "empty.txt"
        empty.write_text("")

        async def run():
            msgs = []
            async for msg in replay_messages(empty, speed=0):
                msgs.append(msg)
            return msgs

        msgs = asyncio.run(run())
        assert len(msgs) == 0

    def test_replay_with_real_dash_data(self):
        """Integration test with actual DASH data if available."""
        dash3_zip = Path(
            "c:/Users/hsclouse/GitProjects/act3/equifinality/docs/DASH/downloaded/chat/Dash3-GBC/Data/23Sep/usaf/chat.zip"
        )
        if not dash3_zip.exists():
            pytest.skip("DASH 3 data not available")

        async def run():
            msgs = []
            async for msg in replay_messages(dash3_zip, speed=0):
                msgs.append(msg)
                if len(msgs) >= 50:
                    break
            return msgs

        msgs = asyncio.run(run())
        assert len(msgs) >= 20  # Should have plenty of messages
        # Verify we got messages from multiple channels
        channels = {m.channel for m in msgs}
        assert len(channels) >= 2

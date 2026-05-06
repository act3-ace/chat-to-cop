"""Tests for multi-line message reassembly."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from chat_to_cop.ingestion.multiline import (
    MultiLineBuffer,
    is_multiline_message,
    reassemble_multiline,
)
from chat_to_cop.models.messages import IRCMessage


def _msg(content: str, sender: str = "VEGAS_SL", channel: str = "#c2_coord") -> IRCMessage:
    return IRCMessage(
        timestamp=datetime(2026, 5, 5, 20, 0, 0, tzinfo=timezone.utc),
        channel=channel,
        sender=sender,
        content=content,
    )


class TestIsMultilineMessage:
    def test_line_uppercase(self):
        assert is_multiline_message("LINE 0: BCOA 0502-01A")

    def test_line_titlecase(self):
        assert is_multiline_message("Line 3: System Denial, Power Denial")

    def test_line_lowercase(self):
        assert is_multiline_message("line 5: USSF will report")

    def test_no_line_prefix(self):
        assert not is_multiline_message("Vegas, Floater 02 available for SMACK")

    def test_line_in_middle_of_text(self):
        assert not is_multiline_message("Check LINE 2 for details")


class TestMultiLineBuffer:
    def test_non_line_passthrough(self):
        buf = MultiLineBuffer()
        msg = _msg("Vegas, Floater 02 available")
        result = buf.add(msg)
        assert result is msg
        assert not buf.pending

    def test_single_line_buffers(self):
        buf = MultiLineBuffer()
        result = buf.add(_msg("LINE 0: BCOA 0502-01A"))
        assert result is None
        assert buf.pending

    def test_consecutive_lines_merge(self):
        buf = MultiLineBuffer()
        buf.add(_msg("LINE 0: BCOA 0502-01A"))
        buf.add(_msg("LINE 1: OCO Prime, to deny cyber"))
        buf.add(_msg("LINE 2: Cigar 252 / 331 NM"))

        merged = buf.flush()
        assert merged is not None
        assert "BCOA 0502-01A" in merged.content
        assert "OCO Prime, to deny cyber" in merged.content
        assert "Cigar 252 / 331 NM" in merged.content
        lines = merged.content.split("\n")
        assert len(lines) == 3

    def test_lines_sorted_by_number(self):
        """Lines within a block are sorted by number even if received slightly out of order."""
        buf = MultiLineBuffer()
        buf.add(_msg("LINE 1: middle"))
        buf.add(_msg("LINE 3: last"))
        buf.add(_msg("LINE 2: second"))

        merged = buf.flush()
        assert merged is not None
        lines = merged.content.split("\n")
        assert lines[0] == "middle"
        assert lines[1] == "second"
        assert lines[2] == "last"

    def test_different_sender_flushes(self):
        buf = MultiLineBuffer()
        buf.add(_msg("LINE 0: BCOA 0502-01A", sender="VEGAS_SL"))
        buf.add(_msg("LINE 1: deny cyber", sender="VEGAS_SL"))

        result = buf.add(_msg("copy that", sender="ORCA_MC"))
        assert result is not None
        assert "BCOA 0502-01A" in result.content
        assert "deny cyber" in result.content

    def test_different_channel_flushes(self):
        buf = MultiLineBuffer()
        buf.add(_msg("LINE 0: BCOA 0502-01A", channel="#c2_coord"))

        result = buf.add(_msg("LINE 1: something", channel="#fires"))
        assert result is not None
        assert "BCOA 0502-01A" in result.content

    def test_non_line_message_flushes_buffer(self):
        buf = MultiLineBuffer()
        buf.add(_msg("LINE 0: BCOA 0502-01A"))
        buf.add(_msg("LINE 1: deny cyber"))

        result = buf.add(_msg("Vegas, Floater 02 available"))
        assert result is not None
        assert "BCOA 0502-01A" in result.content

    def test_new_line_zero_flushes_previous(self):
        buf = MultiLineBuffer()
        buf.add(_msg("LINE 0: first message"))
        buf.add(_msg("LINE 1: first continued"))

        result = buf.add(_msg("LINE 0: second message"))
        assert result is not None
        assert "first message" in result.content
        assert buf.pending

    def test_preserves_first_timestamp(self):
        buf = MultiLineBuffer()
        msg0 = _msg("LINE 0: BCOA 0502-01A")
        buf.add(msg0)
        buf.add(_msg("LINE 1: deny cyber"))
        merged = buf.flush()
        assert merged is not None
        assert merged.timestamp == msg0.timestamp

    def test_preserves_channel_and_sender(self):
        buf = MultiLineBuffer()
        buf.add(_msg("LINE 0: BCOA 0502-01A", sender="VEGAS_SL", channel="#c2_coord"))
        buf.add(_msg("LINE 1: deny cyber", sender="VEGAS_SL", channel="#c2_coord"))
        merged = buf.flush()
        assert merged is not None
        assert merged.sender == "VEGAS_SL"
        assert merged.channel == "#c2_coord"

    def test_single_buffered_line_returns_original(self):
        buf = MultiLineBuffer()
        original = _msg("LINE 0: BCOA 0502-01A")
        buf.add(original)
        result = buf.flush()
        assert result is original

    def test_six_line_cas_request(self):
        buf = MultiLineBuffer()
        lines = [
            "LINE 0: BCOA 0502-01A",
            "LINE 1: ASTRO, ROO, or VADER provide optical",
            "LINE 2: N 22 34.8394 W 080 38.5534",
            "LINE 3: ASTRO, ROO, and VADER are available for space based optical",
            "LINE 4: Asset provides positive ID on mobile C2",
            "LINE 5: Report back ID",
        ]
        for line in lines:
            buf.add(_msg(line))

        merged = buf.flush()
        assert merged is not None
        result_lines = merged.content.split("\n")
        assert len(result_lines) == 6
        assert result_lines[0] == "BCOA 0502-01A"
        assert "N 22 34.8394 W 080 38.5534" in result_lines[2]
        assert result_lines[5] == "Report back ID"


class TestReassembleMultiline:
    @pytest.mark.asyncio
    async def test_passthrough_normal_messages(self):
        async def source():
            yield _msg("Vegas, Floater 02 available")
            yield _msg("copy that")

        results = [msg async for msg in reassemble_multiline(source())]
        assert len(results) == 2
        assert results[0].content == "Vegas, Floater 02 available"
        assert results[1].content == "copy that"

    @pytest.mark.asyncio
    async def test_merges_multiline_block(self):
        async def source():
            yield _msg("LINE 0: BCOA 0502-01A")
            yield _msg("LINE 1: deny cyber on mobile C2")
            yield _msg("LINE 2: Cigar 252 / 331 NM")
            yield _msg("roger that", sender="ORCA_MC")

        results = [msg async for msg in reassemble_multiline(source())]
        assert len(results) == 2
        assert "BCOA 0502-01A" in results[0].content
        assert "deny cyber" in results[0].content
        assert "Cigar 252" in results[0].content
        assert results[1].content == "roger that"

    @pytest.mark.asyncio
    async def test_flushes_at_end_of_stream(self):
        async def source():
            yield _msg("LINE 0: BCOA 0502-01A")
            yield _msg("LINE 1: deny cyber")

        results = [msg async for msg in reassemble_multiline(source())]
        assert len(results) == 1
        assert "BCOA 0502-01A" in results[0].content

    @pytest.mark.asyncio
    async def test_two_consecutive_blocks(self):
        async def source():
            yield _msg("LINE 0: first block")
            yield _msg("LINE 1: first continued")
            yield _msg("LINE 0: second block")
            yield _msg("LINE 1: second continued")

        results = [msg async for msg in reassemble_multiline(source())]
        assert len(results) == 2
        assert "first block" in results[0].content
        assert "second block" in results[1].content

    @pytest.mark.asyncio
    async def test_mixed_messages_and_blocks(self):
        async def source():
            yield _msg("Vegas requests CAS")
            yield _msg("LINE 0: BCOA 0502-01A")
            yield _msg("LINE 1: deny cyber")
            yield _msg("LINE 2: Cigar 252")
            yield _msg("copy all", sender="ORCA_MC")
            yield _msg("next tasking incoming")

        results = [msg async for msg in reassemble_multiline(source())]
        assert len(results) == 4
        assert results[0].content == "Vegas requests CAS"
        assert "BCOA 0502-01A" in results[1].content
        assert results[2].content == "copy all"
        assert results[3].content == "next tasking incoming"

    @pytest.mark.asyncio
    async def test_timeout_flushes_buffer(self):
        """Buffer flushes after timeout with no new messages."""

        async def slow_source():
            yield _msg("LINE 0: BCOA 0502-01A")
            yield _msg("LINE 1: deny cyber")
            await asyncio.sleep(5)  # Exceeds flush_timeout
            yield _msg("roger that", sender="ORCA_MC")

        results = [msg async for msg in reassemble_multiline(slow_source(), flush_timeout=0.5)]
        assert len(results) == 2
        assert "BCOA 0502-01A" in results[0].content
        assert "deny cyber" in results[0].content
        assert results[1].content == "roger that"

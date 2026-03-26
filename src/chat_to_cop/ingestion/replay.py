"""Replay: feed DASH chat log files through the agent pipeline.

Supports three formats:
- DASH 1: plain text files with `[HH:MM:SS] sender: message`
- DASH 3 per-channel: same format, filename encodes channel name
- DASH 3 combined: `[HH:MM:SS] #channel sender: message`
- DASH 3 chat.zip: zip containing per-channel logs + combined.log

All formats yield IRCMessage objects through the same async interface
as the live IRC client.
"""

from __future__ import annotations

import asyncio
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

from chat_to_cop.models.messages import IRCMessage

# Matches: [HH:MM:SS] #channel sender: message  (combined format)
_COMBINED_RE = re.compile(
    r"^\[(\d{2}:\d{2}:\d{2})\]\s+"  # [HH:MM:SS]
    r"(#\S+)\s+"  # #channel_name
    r"(\S+?):\s*"  # sender:
    r"(.*)$"  # message content
)

# Matches: [HH:MM:SS] sender: message  (per-channel / DASH 1 format)
_CHANNEL_RE = re.compile(
    r"^\[(\d{2}:\d{2}:\d{2})\]\s+"  # [HH:MM:SS]
    r"(\S+?):\s*"  # sender:
    r"(.*)$"  # message content
)


def _channel_from_filename(filename: str) -> str:
    """Extract channel name from DASH 3 per-channel log filename.

    Filenames look like: #c2_coord_2025-09-17_14-58-01.log
    We want: #c2_coord
    """
    # Strip the date/time suffix: everything after the last _YYYY pattern
    match = re.match(r"(#[^_]+(?:_[^_]+)*?)_\d{4}-\d{2}-\d{2}", filename)
    if match:
        return match.group(1)
    # Fallback: strip extension and date-like suffixes
    name = Path(filename).stem
    if name.startswith("#"):
        return name
    return f"#{name}"


def _parse_time(time_str: str, reference_date: datetime | None = None) -> datetime:
    """Parse HH:MM:SS into a datetime using a reference date.

    DASH logs only have time-of-day, not full dates. We use the reference
    date (from filename or caller) to construct a full datetime.
    """
    h, m, s = time_str.split(":")
    base = reference_date or datetime(2025, 9, 23, tzinfo=timezone.utc)
    return base.replace(hour=int(h), minute=int(m), second=int(s), microsecond=0)


def _extract_date_from_path(path: Path) -> datetime | None:
    """Try to extract a date from the directory path.

    DASH paths contain date info like: Data/23Sep/usaf/ or Data/1Apr/
    """
    month_map = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    }
    for part in path.parts:
        match = re.match(r"(\d{1,2})([A-Za-z]{3})$", part)
        if match:
            day = int(match.group(1))
            month_str = match.group(2).lower()
            if month_str in month_map:
                month = month_map[month_str]
                year = 2025  # DASH events were in 2025
                return datetime(year, month, day, tzinfo=timezone.utc)
    return None


def parse_line(line: str, default_channel: str = "#unknown", reference_date: datetime | None = None) -> IRCMessage | None:
    """Parse a single log line into an IRCMessage, or None if unparseable."""
    line = line.rstrip()
    if not line:
        return None

    # Try combined format first: [HH:MM:SS] #channel sender: message
    m = _COMBINED_RE.match(line)
    if m:
        return IRCMessage(
            timestamp=_parse_time(m.group(1), reference_date),
            channel=m.group(2),
            sender=m.group(3),
            content=m.group(4),
            raw_line=line,
        )

    # Try per-channel format: [HH:MM:SS] sender: message
    m = _CHANNEL_RE.match(line)
    if m:
        return IRCMessage(
            timestamp=_parse_time(m.group(1), reference_date),
            channel=default_channel,
            sender=m.group(2),
            content=m.group(3),
            raw_line=line,
        )

    return None


def parse_file(path: Path, default_channel: str = "#unknown") -> list[IRCMessage]:
    """Parse all messages from a single log file."""
    reference_date = _extract_date_from_path(path)
    messages = []
    text = path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        msg = parse_line(line, default_channel=default_channel, reference_date=reference_date)
        if msg is not None:
            messages.append(msg)
    return messages


def parse_zip(zip_path: Path) -> list[IRCMessage]:
    """Parse all messages from a DASH 3 chat.zip archive.

    Prefers combined.log if present (has channel info). Otherwise
    parses individual per-channel logs with channel from filename.
    """
    reference_date = _extract_date_from_path(zip_path)
    messages = []

    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()

        if "combined.log" in names:
            with zf.open("combined.log") as f:
                for raw_line in f:
                    line = raw_line.decode("utf-8", errors="replace")
                    msg = parse_line(line, reference_date=reference_date)
                    if msg is not None:
                        messages.append(msg)
        else:
            # Parse individual channel logs
            for name in sorted(names):
                if not name.endswith(".log"):
                    continue
                channel = _channel_from_filename(name)
                with zf.open(name) as f:
                    for raw_line in f:
                        line = raw_line.decode("utf-8", errors="replace")
                        msg = parse_line(line, default_channel=channel, reference_date=reference_date)
                        if msg is not None:
                            messages.append(msg)

    # Sort by timestamp for interleaved playback
    messages.sort(key=lambda m: m.timestamp)
    return messages


def parse_path(path: Path) -> list[IRCMessage]:
    """Auto-detect format and parse messages from a file, zip, or directory."""
    path = Path(path)

    if path.is_file():
        if path.suffix == ".zip":
            return parse_zip(path)
        return parse_file(path, default_channel=_channel_from_filename(path.name))

    if path.is_dir():
        messages = []
        # Look for zip files first
        zips = list(path.rglob("chat.zip"))
        if zips:
            for z in zips:
                messages.extend(parse_zip(z))
        else:
            # Fall back to plain text files
            for f in sorted(path.rglob("*.txt")) + sorted(path.rglob("*.log")):
                channel = _channel_from_filename(f.name)
                messages.extend(parse_file(f, default_channel=channel))
        messages.sort(key=lambda m: m.timestamp)
        return messages

    return []


async def replay_messages(
    path: Path | str,
    speed: float = 0.0,
) -> AsyncIterator[IRCMessage]:
    """Async generator that yields messages with optional timing.

    Args:
        path: File, zip, or directory to replay.
        speed: Playback speed multiplier.
            0.0 = as fast as possible (no delays)
            1.0 = real-time (original inter-message gaps)
            2.0 = 2x speed, etc.

    Yields:
        IRCMessage objects in timestamp order.
    """
    messages = parse_path(Path(path))

    if not messages:
        return

    last_ts = messages[0].timestamp
    for msg in messages:
        if speed > 0:
            gap = (msg.timestamp - last_ts).total_seconds()
            if gap > 0:
                await asyncio.sleep(gap / speed)
        last_ts = msg.timestamp
        yield msg

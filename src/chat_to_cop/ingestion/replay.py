"""Replay: feed DASH chat log files through the agent pipeline.

Parses DASH chat log formats (plain text, IRC channel exports, chat.zip)
and yields messages with original timestamps through the same interface
as the live IRC client.

Supports:
- DASH 1 format: "timestamp sender: message"
- DASH 2 format: single room logs
- DASH 3 format: per-channel IRC logs in zip files
- Speed control: real-time, accelerated, or as-fast-as-possible
"""

from __future__ import annotations


# TODO: Implement ReplaySource
# - __init__(chat_path, speed_multiplier=1.0)
# - async iter_messages() -> AsyncIterator[IRCMessage]
# - Parse all three DASH formats
# - Respect original inter-message timing (scaled by multiplier)
# - Support directory of files or single file

"""Mock IRC WebSocket server for end-to-end live pipeline testing.

Simulates the MASH exercise IRC server so `python -m chat_to_cop.replay --irc-url ws://...`
can be tested without real exercise infrastructure. Speaks the same
WebSocket protocol the IRCClient expects: raw IRC lines over WebSocket,
NICK/USER registration, JOIN/PART, PING/PONG, and PRIVMSG delivery.

Two modes:
  - Canned (default): serves ~20 hardcoded realistic messages across
    multiple channels. No external data needed.
  - Replay: reads a DASH 3 chat.zip and serves messages at realistic
    pacing with a configurable speed multiplier.

Usage:
    # Canned mode (no external data)
    python scripts/mock_irc_server.py --canned --port 8097

    # Replay mode from DASH 3 data
    python scripts/mock_irc_server.py --replay data/chat/Dash3-GBC/Data/23Sep/usaf/chat.zip --speed 10

    # Then in another terminal:
    python -m chat_to_cop.replay --irc-url ws://127.0.0.1:8097
"""

from __future__ import annotations

import argparse
import asyncio
import re
import signal
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import websockets
import websockets.asyncio.server
import websockets.exceptions

# ---------------------------------------------------------------------------
# IRC PRIVMSG wire format
# ---------------------------------------------------------------------------

_COMBINED_RE = re.compile(
    r"^\[(\d{2}:\d{2}:\d{2})\]\s+"  # [HH:MM:SS]
    r"(#\S+)\s+"  # #channel
    r"(\S+?):\s*"  # sender:
    r"(.*)$",  # content
)


def _to_privmsg(channel: str, sender: str, content: str) -> str:
    """Format a message as an IRC PRIVMSG line (what IRCClient.parse_irc_message expects)."""
    return f":{sender}!user@host PRIVMSG {channel} :{content}\r\n"


# ---------------------------------------------------------------------------
# Canned messages -- realistic DASH/MASH exercise traffic
# ---------------------------------------------------------------------------

CANNED_MESSAGES: list[dict[str, str]] = [
    # CSAR (high-stakes)
    {
        "channel": "#jprc",
        "sender": "JPRC_OPS",
        "content": "ZEUS14 pilot ejected vic N24 03.6 W074 31.5, initiating CSAR",
    },
    {
        "channel": "#jprc",
        "sender": "JPRC_OPS",
        "content": "SANDY01 on station for CSAR, contact ZEUS14 beacon freq 243.0",
    },
    # Fuel reports
    {"channel": "#c2_coord", "sender": "Hydro_Tank", "content": "RR15 F+40, RL36 F+50"},
    {"channel": "#c2_coord", "sender": "Crusher_Tank", "content": "RR22 F+35, RL44 F+45 on SEAHAWKS track"},
    # Weapons status
    {"channel": "#fires", "sender": "WF_BMA_03", "content": "HADES31 winchester on AMRAAMs, RTB for rearm"},
    {"channel": "#fires", "sender": "WF_BMA_03", "content": "VIPER22 expended 2x JDAM on TGT AQ1234, BDA pending"},
    # Tasking
    {"channel": "#c2_coord", "sender": "Hydro_SL", "content": "SHARK71 retask to MANDALAY BMA, hold at FL350"},
    {"channel": "#c2_coord", "sender": "Hydro_SL", "content": "ORCA01 RTB fuel state, ORCA02 assume CAP station"},
    # Entity ID / track correlation
    {
        "channel": "#isr_reports",
        "sender": "ISR_COORD",
        "content": "TN 44504 correlates DDG1 HOSTILE, heading 270 speed 18kt",
    },
    {
        "channel": "#isr_reports",
        "sender": "ISR_COORD",
        "content": "New contact TN 55012 bearing 045 range 120 from bullseye, evaluating",
    },
    # Fire mission
    {
        "channel": "#fires",
        "sender": "FIRES_COORD",
        "content": "fire mission TGT AQ1234, 2x JDAM, TOT 1430Z, grid 17QNE9779269855",
    },
    # Status updates
    {
        "channel": "#c2_coord",
        "sender": "Taipan_BMA",
        "content": "EAGLE11 on station CAP north, checking in with 4x AIM120 2x AIM9",
    },
    {"channel": "#c2_coord", "sender": "Hydro_Tank", "content": "ORCA01 RTB fuel state bingo"},
    # Acknowledgments / noise
    {"channel": "#c2_coord", "sender": "Crusher_SL", "content": "Copy"},
    {"channel": "#c2_coord", "sender": "Hydro_SL", "content": "Roger"},
    {"channel": "#c2_coord", "sender": "Vegas_WD", "content": "Copy all"},
    # STT (noisy voice-to-text)
    {
        "channel": "#stt_hydroBMA",
        "sender": "google-speech-to-text",
        "content": "uh... two more down, west of the river",
    },
    {
        "channel": "#stt_crusherBMA",
        "sender": "google-speech-to-text",
        "content": "splash 2 flankers at bullseye 270 40",
    },
    {"channel": "#stt_hydroBMA", "sender": "google-speech-to-text", "content": "tanker is uh bingo minus ten"},
    # Correction
    {
        "channel": "#isr_reports",
        "sender": "ISR_COORD",
        "content": "CORRECTION: disregard last on TN 44504, NOT DDG1, reassessing",
    },
    # Exercise control
    {"channel": "#c2_coord", "sender": "Vegas_WD", "content": "All players, STARTEX STARTEX STARTEX, time is 1400Z"},
]


# ---------------------------------------------------------------------------
# Zip replay loader
# ---------------------------------------------------------------------------


def _load_messages_from_zip(zip_path: Path) -> list[dict[str, str]]:
    """Load messages from a DASH 3 chat.zip (combined.log or per-channel logs)."""
    messages: list[dict[str, str]] = []

    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()

        if "combined.log" in names:
            with zf.open("combined.log") as f:
                for raw_line in f:
                    line = raw_line.decode("utf-8", errors="replace").rstrip()
                    m = _COMBINED_RE.match(line)
                    if m:
                        messages.append(
                            {
                                "time": m.group(1),
                                "channel": m.group(2),
                                "sender": m.group(3),
                                "content": m.group(4),
                            }
                        )
        else:
            # Parse individual per-channel logs
            for name in sorted(names):
                if not name.endswith(".log"):
                    continue
                channel_match = re.match(r"(#[^_]+(?:_[^_]+)*?)_\d{4}-\d{2}-\d{2}", name)
                channel = channel_match.group(1) if channel_match else f"#{Path(name).stem}"
                with zf.open(name) as f:
                    for raw_line in f:
                        line = raw_line.decode("utf-8", errors="replace").rstrip()
                        # Per-channel format: [HH:MM:SS] sender: message
                        pm = re.match(r"^\[(\d{2}:\d{2}:\d{2})\]\s+(\S+?):\s*(.*)$", line)
                        if pm:
                            messages.append(
                                {
                                    "time": pm.group(1),
                                    "channel": channel,
                                    "sender": pm.group(2),
                                    "content": pm.group(3),
                                }
                            )

    # Sort by time for interleaved playback
    messages.sort(key=lambda m: m.get("time", ""))
    return messages


def _time_to_seconds(t: str) -> float:
    """Convert HH:MM:SS to seconds since midnight."""
    h, m, s = t.split(":")
    return int(h) * 3600 + int(m) * 60 + int(s)


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------


class MockIRCServer:
    """WebSocket server that emulates a MASH IRC server.

    Handles IRC registration (NICK/USER), JOIN/PART, PING/PONG, and
    streams PRIVMSG lines from either canned or replay data.
    """

    def __init__(
        self,
        messages: list[dict[str, str]],
        speed: float = 1.0,
        loop_messages: bool = True,
    ) -> None:
        self.messages = messages
        self.speed = speed
        self.loop_messages = loop_messages
        self._clients: set[websockets.asyncio.server.ServerConnection] = set()
        self._server = None
        self._stop_event = asyncio.Event()
        self._broadcast_task: asyncio.Task | None = None
        self._msg_count = 0

    async def _handle_client(self, ws: websockets.asyncio.server.ServerConnection) -> None:
        """Handle a single client: respond to IRC registration and commands."""
        self._clients.add(ws)
        nick = "unknown"
        try:
            async for raw in ws:
                for line in raw.split("\r\n"):
                    line = line.strip()
                    if not line:
                        continue

                    if line.startswith("NICK "):
                        nick = line.split(" ", 1)[1]
                        await ws.send(f":server 001 {nick} :Welcome to mock IRC server\r\n")
                        _log(f"Client registered: {nick}")

                    elif line.startswith("USER "):
                        pass  # Registration ack sent with NICK

                    elif line.startswith("JOIN "):
                        channel = line.split(" ", 1)[1].strip()
                        await ws.send(f":server 332 {nick} {channel} :Mock channel\r\n")
                        _log(f"Client {nick} joined {channel}")

                    elif line.startswith("PART "):
                        channel = line.split(" ", 1)[1].strip()
                        _log(f"Client {nick} parted {channel}")

                    elif line.startswith("PING"):
                        payload = line[4:].strip()
                        await ws.send(f"PONG {payload}\r\n")

                    elif line.startswith("PONG"):
                        pass  # Client keepalive response

                    elif line.startswith("QUIT"):
                        _log(f"Client {nick} quit")
                        break

        except websockets.exceptions.ConnectionClosed:
            _log(f"Client {nick} disconnected")
        finally:
            self._clients.discard(ws)

    async def _broadcast(self, irc_line: str, channel: str, sender: str, content: str) -> None:
        """Send a PRIVMSG to all connected clients and log it."""
        self._msg_count += 1
        disconnected: set[websockets.asyncio.server.ServerConnection] = set()
        for ws in self._clients:
            try:
                await ws.send(irc_line)
            except websockets.exceptions.ConnectionClosed:
                disconnected.add(ws)
        self._clients -= disconnected
        _log(f"[{self._msg_count:>4}] {channel:<20} {sender:<25} {content[:80]}")

    async def _broadcast_canned(self) -> None:
        """Broadcast canned messages at a fixed 3-second interval, looping."""
        interval = 3.0
        while not self._stop_event.is_set():
            for msg in self.messages:
                if self._stop_event.is_set():
                    return
                # Wait for at least one client
                while not self._clients and not self._stop_event.is_set():
                    try:
                        await asyncio.wait_for(self._stop_event.wait(), timeout=1.0)
                        return
                    except asyncio.TimeoutError:
                        pass

                irc_line = _to_privmsg(msg["channel"], msg["sender"], msg["content"])
                await self._broadcast(irc_line, msg["channel"], msg["sender"], msg["content"])

                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
                    return
                except asyncio.TimeoutError:
                    pass

            if not self.loop_messages:
                _log("All canned messages sent.")
                return

    async def _broadcast_replay(self) -> None:
        """Broadcast replay messages respecting inter-message timing with speed multiplier."""
        while not self._stop_event.is_set():
            prev_time: float | None = None
            for msg in self.messages:
                if self._stop_event.is_set():
                    return
                # Wait for at least one client
                while not self._clients and not self._stop_event.is_set():
                    try:
                        await asyncio.wait_for(self._stop_event.wait(), timeout=1.0)
                        return
                    except asyncio.TimeoutError:
                        pass

                # Apply inter-message delay based on timestamps
                if "time" in msg and self.speed > 0:
                    cur_time = _time_to_seconds(msg["time"])
                    if prev_time is not None:
                        gap = cur_time - prev_time
                        if gap > 0:
                            delay = gap / self.speed
                            # Cap individual delay at 30s to avoid long silences
                            delay = min(delay, 30.0)
                            try:
                                await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
                                return
                            except asyncio.TimeoutError:
                                pass
                    prev_time = cur_time

                irc_line = _to_privmsg(msg["channel"], msg["sender"], msg["content"])
                await self._broadcast(irc_line, msg["channel"], msg["sender"], msg["content"])

            if not self.loop_messages:
                _log("All replay messages sent.")
                return

    async def start(self, host: str = "127.0.0.1", port: int = 8097) -> None:
        """Start the server and begin broadcasting messages."""
        self._stop_event.clear()
        self._server = await websockets.serve(self._handle_client, host, port)
        _log(f"Mock IRC server listening on ws://{host}:{port}")
        is_replay = any("time" in m for m in self.messages)
        mode_str = "replay" if is_replay else "canned"
        _log(f"Loaded {len(self.messages)} messages, mode={mode_str}")
        if is_replay:
            _log(f"Speed: {self.speed}x (original inter-message timing)")
            self._broadcast_task = asyncio.create_task(self._broadcast_replay())
        else:
            _log("Interval: 3s between messages")
            self._broadcast_task = asyncio.create_task(self._broadcast_canned())

    async def stop(self) -> None:
        """Stop the server and clean up."""
        self._stop_event.set()
        if self._broadcast_task is not None:
            self._broadcast_task.cancel()
            try:
                await self._broadcast_task
            except asyncio.CancelledError:
                pass
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        for ws in list(self._clients):
            try:
                await ws.close()
            except Exception:
                pass
        self._clients.clear()
        _log(f"Server stopped. {self._msg_count} messages sent total.")

    async def run_forever(self, host: str = "127.0.0.1", port: int = 8097) -> None:
        """Start and run until interrupted."""
        await self.start(host, port)
        try:
            await self._stop_event.wait()
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()


def _log(msg: str) -> None:
    """Print a timestamped log line."""
    ts = datetime.now(tz=timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Mock IRC WebSocket server for testing the chat-to-cop live pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  %(prog)s --canned
  %(prog)s --replay data/chat/Dash3-GBC/Data/23Sep/usaf/chat.zip --speed 10
  %(prog)s --canned --port 9000 --no-loop
""",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--canned",
        action="store_true",
        help="Serve hardcoded realistic messages (no external data needed)",
    )
    mode.add_argument(
        "--replay",
        type=Path,
        metavar="ZIP",
        help="Replay messages from a DASH 3 chat.zip file",
    )
    parser.add_argument("--port", type=int, default=8097, help="WebSocket port (default: 8097)")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    parser.add_argument(
        "--speed",
        type=float,
        default=10.0,
        help="Replay speed multiplier: 1=realtime, 10=10x faster (default: 10). Only used in replay mode.",
    )
    parser.add_argument(
        "--no-loop",
        action="store_true",
        help="Send messages once then stop (default: loop forever)",
    )
    args = parser.parse_args()

    if args.replay:
        if not args.replay.exists():
            print(f"Error: {args.replay} not found", file=sys.stderr)
            sys.exit(1)
        messages = _load_messages_from_zip(args.replay)
        if not messages:
            print("Error: no messages loaded from zip", file=sys.stderr)
            sys.exit(1)
    else:
        messages = CANNED_MESSAGES

    server = MockIRCServer(
        messages=messages,
        speed=args.speed,
        loop_messages=not args.no_loop,
    )

    loop = asyncio.new_event_loop()

    def shutdown(sig, frame):
        _log(f"Received {signal.Signals(sig).name}, shutting down...")
        server._stop_event.set()

    signal.signal(signal.SIGINT, shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, shutdown)

    try:
        loop.run_until_complete(server.run_forever(args.host, args.port))
    finally:
        loop.close()


if __name__ == "__main__":
    main()

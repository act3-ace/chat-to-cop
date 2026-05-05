"""WebSocket test server that replays DASH 3 chat logs over IRC protocol.

Reads messages from a DASH 3 chat zip (combined.log format), converts them
to IRC PRIVMSG format, and serves them over WebSocket. Used for integration
testing the IRCClient without a real IRC server.

Usage:
    python scripts/ws_test_server.py                           # defaults
    python scripts/ws_test_server.py --port 9000 --rate 20     # custom
    python scripts/ws_test_server.py --path data/dash3/23Sep_usaf_chat.zip
"""

from __future__ import annotations

import argparse
import asyncio
import re
import signal
import sys
import zipfile
from pathlib import Path

import websockets
import websockets.asyncio.server

# Default DASH 3 zip path relative to project root
_DEFAULT_ZIP = Path(__file__).resolve().parent.parent / "data" / "dash3" / "23Sep_usaf_chat.zip"

# Combined log line: [HH:MM:SS] #channel sender: message
_COMBINED_RE = re.compile(
    r"^\[(\d{2}:\d{2}:\d{2})\]\s+"
    r"(#\S+)\s+"
    r"(\S+?):\s*"
    r"(.*)$"
)


def load_messages_from_zip(zip_path: Path) -> list[dict]:
    """Load messages from a DASH 3 chat zip, returning dicts with channel/sender/content."""
    messages = []
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        if "combined.log" not in names:
            print(f"No combined.log found in {zip_path}. Available: {names}", file=sys.stderr)
            sys.exit(1)

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
    return messages


def to_irc_privmsg(msg: dict) -> str:
    """Convert a message dict to IRC PRIVMSG wire format."""
    return f":{msg['sender']}!user@host PRIVMSG {msg['channel']} :{msg['content']}\r\n"


class IRCTestServer:
    """WebSocket server that emulates an IRC server sending PRIVMSG lines.

    Handles IRC registration (NICK/USER), JOIN commands, PING/PONG,
    and streams messages from the loaded DASH data at a configurable rate.
    """

    def __init__(self, messages: list[dict], rate: float = 5.0) -> None:
        self.messages = messages
        self.rate = rate  # messages per minute
        self._clients: set[websockets.asyncio.server.ServerConnection] = set()
        self._server = None
        self._stop_event = asyncio.Event()
        self._broadcast_task: asyncio.Task | None = None

    async def _handle_client(self, ws: websockets.asyncio.server.ServerConnection) -> None:
        """Handle a single client connection: respond to IRC commands."""
        self._clients.add(ws)
        joined_channels: set[str] = set()
        try:
            async for raw in ws:
                for line in raw.split("\r\n"):
                    line = line.strip()
                    if not line:
                        continue

                    # Respond to NICK
                    if line.startswith("NICK "):
                        nick = line.split(" ", 1)[1]
                        await ws.send(f":server 001 {nick} :Welcome to the test IRC server\r\n")

                    # Respond to USER (ignored beyond registration)
                    elif line.startswith("USER "):
                        pass

                    # Respond to JOIN
                    elif line.startswith("JOIN "):
                        channel = line.split(" ", 1)[1].strip()
                        joined_channels.add(channel)
                        await ws.send(f":server 332 chat-to-cop {channel} :Test channel\r\n")

                    # Respond to PART
                    elif line.startswith("PART "):
                        channel = line.split(" ", 1)[1].strip()
                        joined_channels.discard(channel)

                    # Respond to PING with PONG
                    elif line.startswith("PING"):
                        payload = line[4:].strip()
                        await ws.send(f"PONG {payload}\r\n")

                    # Respond to PONG (client keepalive response, ignore)
                    elif line.startswith("PONG"):
                        pass

                    # Respond to QUIT
                    elif line.startswith("QUIT"):
                        break

        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self._clients.discard(ws)

    async def _broadcast_messages(self) -> None:
        """Broadcast loaded messages to all connected clients at the configured rate."""
        if self.rate <= 0:
            return

        interval = 60.0 / self.rate  # seconds between messages
        for msg in self.messages:
            if self._stop_event.is_set():
                break

            irc_line = to_irc_privmsg(msg)

            # Send to all connected clients
            disconnected = set()
            for ws in self._clients:
                try:
                    await ws.send(irc_line)
                except websockets.exceptions.ConnectionClosed:
                    disconnected.add(ws)
            self._clients -= disconnected

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
                break  # stop event was set
            except asyncio.TimeoutError:
                pass  # interval elapsed, continue

    async def start(self, host: str = "127.0.0.1", port: int = 8097) -> None:
        """Start the WebSocket server and message broadcast."""
        self._stop_event.clear()
        self._server = await websockets.serve(self._handle_client, host, port)
        print(f"IRC test server listening on ws://{host}:{port}")
        print(f"Loaded {len(self.messages)} messages, rate={self.rate} msg/min")
        self._broadcast_task = asyncio.create_task(self._broadcast_messages())

    async def stop(self) -> None:
        """Stop the server and cancel broadcast."""
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
        # Close any remaining client connections
        for ws in list(self._clients):
            try:
                await ws.close()
            except Exception:
                pass
        self._clients.clear()

    async def run_forever(self, host: str = "127.0.0.1", port: int = 8097) -> None:
        """Start and run until interrupted."""
        await self.start(host, port)
        try:
            await self._stop_event.wait()
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()


async def _send_all_fast(ws: websockets.asyncio.server.ServerConnection, messages: list[dict]) -> None:
    """Send all messages as fast as possible (for testing)."""
    for msg in messages:
        try:
            await ws.send(to_irc_privmsg(msg))
        except websockets.exceptions.ConnectionClosed:
            break


def main() -> None:
    parser = argparse.ArgumentParser(description="WebSocket IRC test server for DASH 3 chat replay")
    parser.add_argument("--path", type=Path, default=_DEFAULT_ZIP, help="Path to DASH 3 chat zip")
    parser.add_argument("--port", type=int, default=8097, help="WebSocket port (default: 8097)")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    parser.add_argument("--rate", type=float, default=5.0, help="Messages per minute (default: 5)")
    args = parser.parse_args()

    if not args.path.exists():
        print(f"Error: {args.path} not found", file=sys.stderr)
        sys.exit(1)

    messages = load_messages_from_zip(args.path)
    if not messages:
        print("Error: No messages loaded from zip", file=sys.stderr)
        sys.exit(1)

    server = IRCTestServer(messages, rate=args.rate)

    loop = asyncio.new_event_loop()

    def shutdown(sig, frame):
        print(f"\nReceived {signal.Signals(sig).name}, shutting down...")
        server._stop_event.set()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        loop.run_until_complete(server.run_forever(args.host, args.port))
    finally:
        loop.close()


if __name__ == "__main__":
    main()

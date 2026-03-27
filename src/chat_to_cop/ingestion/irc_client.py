"""IRC WebSocket client: connects to the MASH/DASH IRC server.

Protocol is WebSocket (not raw IRC) on port 8097. Connects, joins
configured channels, and yields parsed messages to the message router.

Handles:
- Connection and reconnection with backoff
- Channel join/part
- Message parsing (timestamp, sender, channel, content)
- Heartbeat/keepalive
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from datetime import datetime, timezone

import websockets
import websockets.exceptions
from loguru import logger
from tenacity import AsyncRetrying, retry_if_exception_type, stop_never, wait_exponential

from chat_to_cop.metrics import metrics
from chat_to_cop.models.messages import IRCMessage

# IRC message patterns from the WebSocket stream
# PRIVMSG format: :sender!user@host PRIVMSG #channel :message content
_PRIVMSG_RE = re.compile(r"^:(\S+?)(?:!\S+)?\s+PRIVMSG\s+(#\S+)\s+:(.*)$")

# Default backoff parameters for reconnection
_INITIAL_BACKOFF_S = 1.0
_MAX_BACKOFF_S = 60.0

# Heartbeat interval — send PING if no data received in this window
_HEARTBEAT_INTERVAL_S = 30.0

# Read timeout — how long to wait for a message before checking heartbeat
_READ_TIMEOUT_S = 5.0


def parse_irc_message(raw_line: str) -> IRCMessage | None:
    """Parse a raw IRC PRIVMSG line into an IRCMessage.

    Returns None for non-PRIVMSG lines (PING, JOIN, PART, server numerics, etc.).
    """
    raw_line = raw_line.strip()
    if not raw_line:
        return None

    m = _PRIVMSG_RE.match(raw_line)
    if not m:
        return None

    sender = m.group(1)
    channel = m.group(2)
    content = m.group(3)

    return IRCMessage(
        timestamp=datetime.now(tz=timezone.utc),
        channel=channel,
        sender=sender,
        content=content,
        raw_line=raw_line,
    )


class IRCClient:
    """WebSocket-based IRC client for live message ingestion.

    Connects to the MASH/DASH IRC server via WebSocket, joins the specified
    channels, and yields IRCMessage objects through the same async iterator
    interface as replay_messages().

    Usage:
        client = IRCClient("ws://irc-server:8097", ["#c2_coord", "#fires"])
        async for msg in client.iter_messages():
            await agent.process_message(msg)
    """

    def __init__(
        self,
        server_url: str,
        channels: list[str],
        nick: str = "chat-to-cop",
        initial_backoff: float = _INITIAL_BACKOFF_S,
        max_backoff: float = _MAX_BACKOFF_S,
        heartbeat_interval: float = _HEARTBEAT_INTERVAL_S,
    ) -> None:
        self.server_url = server_url
        self.channels = list(channels)
        self.nick = nick
        self.initial_backoff = initial_backoff
        self.max_backoff = max_backoff
        self.heartbeat_interval = heartbeat_interval

        self._ws = None
        self._connected = False
        self._stop_event = asyncio.Event()
        self._message_count = 0
        self._reconnect_count = 0

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def message_count(self) -> int:
        return self._message_count

    @property
    def reconnect_count(self) -> int:
        return self._reconnect_count

    async def _send(self, data: str) -> None:
        """Send a raw IRC command over the WebSocket."""
        if self._ws is not None:
            await self._ws.send(data)
            logger.debug("IRC TX: {}", data.strip())

    async def _register(self) -> None:
        """Send NICK and USER commands to register with the server."""
        await self._send(f"NICK {self.nick}\r\n")
        await self._send(f"USER {self.nick} 0 * :{self.nick}\r\n")

    async def _join_channels(self) -> None:
        """Join all configured channels."""
        for channel in self.channels:
            await self._send(f"JOIN {channel}\r\n")
            logger.info("Joining channel: {}", channel)

    async def _handle_ping(self, line: str) -> bool:
        """Respond to PING with PONG. Returns True if line was a PING."""
        if line.startswith("PING"):
            payload = line[4:].strip()
            await self._send(f"PONG {payload}\r\n")
            return True
        return False

    async def _send_heartbeat(self) -> None:
        """Send a PING to keep the connection alive."""
        await self._send(f"PING :{self.nick}\r\n")

    async def connect(self) -> None:
        """Establish WebSocket connection and register with IRC server.

        Raises websockets exceptions on failure — callers should handle
        reconnection via iter_messages() which has built-in auto-reconnect.
        """
        logger.info("Connecting to IRC server: {}", self.server_url)
        self._ws = await websockets.connect(self.server_url)
        self._connected = True
        logger.info("Connected to {}", self.server_url)

        await self._register()
        await self._join_channels()

        metrics.inc("irc_connections_total")

    async def disconnect(self) -> None:
        """Gracefully disconnect from the IRC server."""
        self._stop_event.set()
        if self._ws is not None:
            try:
                for channel in self.channels:
                    await self._send(f"PART {channel}\r\n")
                await self._send("QUIT :chat-to-cop shutting down\r\n")
            except Exception:
                pass  # Best-effort cleanup
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        self._connected = False
        logger.info("Disconnected from IRC server")

    def _log_retry(self, retry_state) -> None:
        """Tenacity before_sleep callback: log and count reconnection attempts."""
        exc = retry_state.outcome.exception()
        wait = retry_state.next_action.sleep
        self._reconnect_count += 1
        logger.warning("IRC connect failed: {} — retrying in {:.1f}s (attempt #{})", exc, wait, self._reconnect_count)
        metrics.inc("irc_connection_errors_total")

    async def _connect_with_backoff(self) -> None:
        """Connect to IRC with tenacity-managed exponential backoff."""
        async for attempt in AsyncRetrying(
            wait=wait_exponential(multiplier=1, min=self.initial_backoff, max=self.max_backoff),
            stop=stop_never,
            retry=retry_if_exception_type((OSError, websockets.exceptions.WebSocketException)),
            before_sleep=self._log_retry,
        ):
            with attempt:
                if self._stop_event.is_set():
                    return
                await self.connect()

    async def join(self, channel: str) -> None:
        """Join an additional channel at runtime."""
        if channel not in self.channels:
            self.channels.append(channel)
        if self._connected:
            await self._send(f"JOIN {channel}\r\n")
            logger.info("Joined channel: {}", channel)

    async def part(self, channel: str) -> None:
        """Leave a channel at runtime."""
        if channel in self.channels:
            self.channels.remove(channel)
        if self._connected:
            await self._send(f"PART {channel}\r\n")
            logger.info("Parted channel: {}", channel)

    async def iter_messages(self) -> AsyncIterator[IRCMessage]:
        """Async generator that yields parsed IRCMessage objects.

        Automatically reconnects with exponential backoff on disconnection
        (via tenacity). Sends heartbeat PINGs to keep the connection alive.
        Stops when disconnect() is called or the stop event is set.

        Yields:
            IRCMessage objects for each PRIVMSG received.
        """
        while not self._stop_event.is_set():
            try:
                if not self._connected:
                    await self._connect_with_backoff()
                    if self._stop_event.is_set():
                        break

                loop = asyncio.get_running_loop()
                last_data_time = loop.time()

                while not self._stop_event.is_set():
                    try:
                        raw = await asyncio.wait_for(
                            self._ws.recv(),
                            timeout=_READ_TIMEOUT_S,
                        )
                    except asyncio.TimeoutError:
                        # No data received — check if heartbeat is needed
                        now = loop.time()
                        if now - last_data_time > self.heartbeat_interval:
                            await self._send_heartbeat()
                            last_data_time = now
                        continue

                    last_data_time = loop.time()

                    # IRC messages can be batched; split on CRLF
                    for line in raw.split("\r\n"):
                        line = line.strip()
                        if not line:
                            continue

                        logger.debug("IRC RX: {}", line)

                        # Handle PING/PONG keepalive
                        if await self._handle_ping(line):
                            continue

                        # Parse PRIVMSG into IRCMessage
                        msg = parse_irc_message(line)
                        if msg is not None:
                            self._message_count += 1
                            metrics.inc("irc_messages_received_total", labels={"channel": msg.channel})
                            yield msg

            except websockets.exceptions.ConnectionClosed as e:
                self._connected = False
                self._ws = None
                self._reconnect_count += 1
                logger.warning("IRC connection closed: {}", e)
                metrics.inc("irc_disconnections_total")

            except (OSError, websockets.exceptions.WebSocketException) as e:
                self._connected = False
                self._ws = None
                self._reconnect_count += 1
                logger.warning("IRC connection error: {}", e)
                metrics.inc("irc_connection_errors_total")

            except Exception as e:
                self._connected = False
                self._ws = None
                self._reconnect_count += 1
                logger.error("Unexpected error in IRC client: {}", e)
                metrics.inc("irc_connection_errors_total")

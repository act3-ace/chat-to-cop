"""Tests for the IRC WebSocket client.

All tests use mocked WebSockets — no running IRC server required.
"""

from __future__ import annotations

import asyncio
from datetime import timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chat_to_cop.ingestion.irc_client import IRCClient, parse_irc_message
from chat_to_cop.models.messages import IRCMessage

# ---------------------------------------------------------------------------
# parse_irc_message unit tests
# ---------------------------------------------------------------------------


class TestParseIrcMessage:
    """Test raw IRC line parsing."""

    def test_privmsg_basic(self):
        line = ":Hydro_Tank!user@host PRIVMSG #c2_coord :RR15 F+40, RL36 F+50"
        msg = parse_irc_message(line)
        assert msg is not None
        assert msg.sender == "Hydro_Tank"
        assert msg.channel == "#c2_coord"
        assert msg.content == "RR15 F+40, RL36 F+50"
        assert msg.raw_line == line

    def test_privmsg_no_host(self):
        """Some servers omit the !user@host part."""
        line = ":WF_Clark PRIVMSG #fires :STARTEX"
        msg = parse_irc_message(line)
        assert msg is not None
        assert msg.sender == "WF_Clark"
        assert msg.channel == "#fires"
        assert msg.content == "STARTEX"

    def test_privmsg_with_colons_in_content(self):
        line = ":wf_beep!u@h PRIVMSG #c2_coord :AOC_CCO: @Hydro status Lane Wynn"
        msg = parse_irc_message(line)
        assert msg is not None
        assert msg.content == "AOC_CCO: @Hydro status Lane Wynn"

    def test_privmsg_stt_channel(self):
        line = ":google-stt!u@h PRIVMSG #stt_C2Coord :<WF2> Radio check"
        msg = parse_irc_message(line)
        assert msg is not None
        assert msg.channel == "#stt_C2Coord"
        assert msg.is_stt is True
        assert msg.content == "<WF2> Radio check"

    def test_privmsg_timestamp_is_utc(self):
        line = ":user!u@h PRIVMSG #test :hello"
        msg = parse_irc_message(line)
        assert msg is not None
        assert msg.timestamp.tzinfo == timezone.utc

    def test_ping_returns_none(self):
        assert parse_irc_message("PING :server.example.com") is None

    def test_join_returns_none(self):
        assert parse_irc_message(":user!u@h JOIN #c2_coord") is None

    def test_part_returns_none(self):
        assert parse_irc_message(":user!u@h PART #c2_coord") is None

    def test_numeric_returns_none(self):
        assert parse_irc_message(":server 001 chat-to-cop :Welcome") is None

    def test_empty_line_returns_none(self):
        assert parse_irc_message("") is None
        assert parse_irc_message("   ") is None

    def test_dot_ack_message(self):
        line = ":VEGAS_ABM2!u@h PRIVMSG #c2_coord :."
        msg = parse_irc_message(line)
        assert msg is not None
        assert msg.content == "."

    def test_raw_line_preserved(self):
        line = ":user!u@h PRIVMSG #test :some content"
        msg = parse_irc_message(line)
        assert msg is not None
        assert msg.raw_line == line


# ---------------------------------------------------------------------------
# Fake WebSocket for testing
# ---------------------------------------------------------------------------


class FakeWebSocket:
    """Mock WebSocket that yields predefined messages then closes."""

    def __init__(self, messages: list[str], close_after: int | None = None):
        self._messages = list(messages)
        self._index = 0
        self._close_after = close_after
        self._sent: list[str] = []
        self._closed = False

    async def send(self, data: str) -> None:
        self._sent.append(data)

    async def recv(self) -> str:
        if self._closed:
            import websockets

            raise websockets.exceptions.ConnectionClosed(None, None)

        if self._index >= len(self._messages):
            # Block indefinitely (test should timeout or stop the client)
            await asyncio.sleep(100)
            return ""

        msg = self._messages[self._index]
        self._index += 1

        if self._close_after is not None and self._index >= self._close_after:
            self._closed = True

        return msg

    async def close(self) -> None:
        self._closed = True


# ---------------------------------------------------------------------------
# IRCClient unit tests
# ---------------------------------------------------------------------------


class TestIRCClientConstruction:
    """Test client construction and configuration."""

    def test_default_construction(self):
        client = IRCClient("ws://localhost:8097", ["#c2_coord", "#fires"])
        assert client.server_url == "ws://localhost:8097"
        assert client.channels == ["#c2_coord", "#fires"]
        assert client.nick == "chat-to-cop"
        assert client.connected is False
        assert client.message_count == 0
        assert client.reconnect_count == 0

    def test_custom_nick(self):
        client = IRCClient("ws://localhost:8097", ["#test"], nick="my-bot")
        assert client.nick == "my-bot"

    def test_custom_backoff(self):
        client = IRCClient(
            "ws://localhost:8097",
            ["#test"],
            initial_backoff=0.5,
            max_backoff=30.0,
        )
        assert client.initial_backoff == 0.5
        assert client.max_backoff == 30.0


class TestIRCClientConnect:
    """Test connection and registration."""

    @pytest.mark.asyncio
    async def test_connect_sends_nick_and_user(self):
        fake_ws = FakeWebSocket([])

        with patch("chat_to_cop.ingestion.irc_client.websockets") as mock_ws:
            mock_ws.connect = AsyncMock(return_value=fake_ws)
            client = IRCClient("ws://localhost:8097", ["#c2_coord", "#fires"])
            await client.connect()

        assert client.connected is True
        sent = fake_ws._sent
        assert any("NICK chat-to-cop" in s for s in sent)
        assert any("USER chat-to-cop" in s for s in sent)

    @pytest.mark.asyncio
    async def test_connect_joins_channels(self):
        fake_ws = FakeWebSocket([])

        with patch("chat_to_cop.ingestion.irc_client.websockets") as mock_ws:
            mock_ws.connect = AsyncMock(return_value=fake_ws)
            client = IRCClient("ws://localhost:8097", ["#c2_coord", "#fires"])
            await client.connect()

        sent = fake_ws._sent
        assert any("JOIN #c2_coord" in s for s in sent)
        assert any("JOIN #fires" in s for s in sent)


class TestIRCClientDisconnect:
    """Test graceful disconnection."""

    @pytest.mark.asyncio
    async def test_disconnect_sends_quit(self):
        fake_ws = FakeWebSocket([])

        with patch("chat_to_cop.ingestion.irc_client.websockets") as mock_ws:
            mock_ws.connect = AsyncMock(return_value=fake_ws)
            client = IRCClient("ws://localhost:8097", ["#c2_coord"])
            await client.connect()
            await client.disconnect()

        assert client.connected is False
        sent = fake_ws._sent
        assert any("PART #c2_coord" in s for s in sent)
        assert any("QUIT" in s for s in sent)


class TestIRCClientChannelManagement:
    """Test join/part at runtime."""

    @pytest.mark.asyncio
    async def test_join_new_channel(self):
        fake_ws = FakeWebSocket([])

        with patch("chat_to_cop.ingestion.irc_client.websockets") as mock_ws:
            mock_ws.connect = AsyncMock(return_value=fake_ws)
            client = IRCClient("ws://localhost:8097", ["#c2_coord"])
            await client.connect()
            await client.join("#isr_reports")

        assert "#isr_reports" in client.channels
        assert any("JOIN #isr_reports" in s for s in fake_ws._sent)

    @pytest.mark.asyncio
    async def test_join_existing_channel_no_duplicate(self):
        fake_ws = FakeWebSocket([])

        with patch("chat_to_cop.ingestion.irc_client.websockets") as mock_ws:
            mock_ws.connect = AsyncMock(return_value=fake_ws)
            client = IRCClient("ws://localhost:8097", ["#c2_coord"])
            await client.connect()
            await client.join("#c2_coord")

        assert client.channels.count("#c2_coord") == 1

    @pytest.mark.asyncio
    async def test_part_channel(self):
        fake_ws = FakeWebSocket([])

        with patch("chat_to_cop.ingestion.irc_client.websockets") as mock_ws:
            mock_ws.connect = AsyncMock(return_value=fake_ws)
            client = IRCClient("ws://localhost:8097", ["#c2_coord", "#fires"])
            await client.connect()
            await client.part("#fires")

        assert "#fires" not in client.channels
        assert any("PART #fires" in s for s in fake_ws._sent)


class TestIRCClientIterMessages:
    """Test the async message iterator."""

    @pytest.mark.asyncio
    async def test_yields_privmsg_as_ircmessage(self):
        """PRIVMSG lines should be yielded as IRCMessage objects."""
        fake_ws = FakeWebSocket(
            [
                ":Hydro_Tank!u@h PRIVMSG #c2_coord :RR15 F+40\r\n",
                ":WF_Clark!u@h PRIVMSG #fires :fire mission complete\r\n",
            ]
        )

        with patch("chat_to_cop.ingestion.irc_client.websockets") as mock_ws:
            mock_ws.connect = AsyncMock(return_value=fake_ws)
            # Avoid real reconnect delays
            mock_ws.exceptions = MagicMock()
            mock_ws.exceptions.ConnectionClosed = type("ConnectionClosed", (Exception,), {})
            mock_ws.exceptions.WebSocketException = type("WebSocketException", (Exception,), {})

            client = IRCClient("ws://localhost:8097", ["#c2_coord", "#fires"])
            messages = []

            async for msg in client.iter_messages():
                messages.append(msg)
                if len(messages) >= 2:
                    client._stop_event.set()
                    break

        assert len(messages) == 2
        assert isinstance(messages[0], IRCMessage)
        assert messages[0].sender == "Hydro_Tank"
        assert messages[0].channel == "#c2_coord"
        assert messages[0].content == "RR15 F+40"
        assert messages[1].sender == "WF_Clark"
        assert messages[1].channel == "#fires"

    @pytest.mark.asyncio
    async def test_handles_ping_pong(self):
        """PING messages should get PONG responses, not yield as messages."""
        fake_ws = FakeWebSocket(
            [
                "PING :server.example.com\r\n",
                ":user!u@h PRIVMSG #test :hello after ping\r\n",
            ]
        )

        with patch("chat_to_cop.ingestion.irc_client.websockets") as mock_ws:
            mock_ws.connect = AsyncMock(return_value=fake_ws)
            mock_ws.exceptions = MagicMock()
            mock_ws.exceptions.ConnectionClosed = type("ConnectionClosed", (Exception,), {})
            mock_ws.exceptions.WebSocketException = type("WebSocketException", (Exception,), {})

            client = IRCClient("ws://localhost:8097", ["#test"])
            messages = []

            async for msg in client.iter_messages():
                messages.append(msg)
                if len(messages) >= 1:
                    client._stop_event.set()
                    break

        # Should have responded to PING
        pong_sent = [s for s in fake_ws._sent if "PONG" in s]
        assert len(pong_sent) == 1
        assert "server.example.com" in pong_sent[0]

        # Only the PRIVMSG should be yielded
        assert len(messages) == 1
        assert messages[0].content == "hello after ping"

    @pytest.mark.asyncio
    async def test_skips_non_privmsg_lines(self):
        """JOIN, PART, numerics, etc. should be silently skipped."""
        fake_ws = FakeWebSocket(
            [
                ":server 001 chat-to-cop :Welcome\r\n"
                ":user!u@h JOIN #test\r\n"
                ":user!u@h PRIVMSG #test :the real message\r\n",
            ]
        )

        with patch("chat_to_cop.ingestion.irc_client.websockets") as mock_ws:
            mock_ws.connect = AsyncMock(return_value=fake_ws)
            mock_ws.exceptions = MagicMock()
            mock_ws.exceptions.ConnectionClosed = type("ConnectionClosed", (Exception,), {})
            mock_ws.exceptions.WebSocketException = type("WebSocketException", (Exception,), {})

            client = IRCClient("ws://localhost:8097", ["#test"])
            messages = []

            async for msg in client.iter_messages():
                messages.append(msg)
                if len(messages) >= 1:
                    client._stop_event.set()
                    break

        assert len(messages) == 1
        assert messages[0].content == "the real message"

    @pytest.mark.asyncio
    async def test_batched_messages_split_on_crlf(self):
        """Multiple IRC messages in a single WebSocket frame should all be parsed."""
        fake_ws = FakeWebSocket(
            [
                ":a!u@h PRIVMSG #test :msg1\r\n:b!u@h PRIVMSG #test :msg2\r\n",
            ]
        )

        with patch("chat_to_cop.ingestion.irc_client.websockets") as mock_ws:
            mock_ws.connect = AsyncMock(return_value=fake_ws)
            mock_ws.exceptions = MagicMock()
            mock_ws.exceptions.ConnectionClosed = type("ConnectionClosed", (Exception,), {})
            mock_ws.exceptions.WebSocketException = type("WebSocketException", (Exception,), {})

            client = IRCClient("ws://localhost:8097", ["#test"])
            messages = []

            async for msg in client.iter_messages():
                messages.append(msg)
                if len(messages) >= 2:
                    client._stop_event.set()
                    break

        assert len(messages) == 2
        assert messages[0].sender == "a"
        assert messages[0].content == "msg1"
        assert messages[1].sender == "b"
        assert messages[1].content == "msg2"

    @pytest.mark.asyncio
    async def test_message_count_increments(self):
        fake_ws = FakeWebSocket(
            [
                ":a!u@h PRIVMSG #test :one\r\n",
                ":b!u@h PRIVMSG #test :two\r\n",
                ":c!u@h PRIVMSG #test :three\r\n",
            ]
        )

        with patch("chat_to_cop.ingestion.irc_client.websockets") as mock_ws:
            mock_ws.connect = AsyncMock(return_value=fake_ws)
            mock_ws.exceptions = MagicMock()
            mock_ws.exceptions.ConnectionClosed = type("ConnectionClosed", (Exception,), {})
            mock_ws.exceptions.WebSocketException = type("WebSocketException", (Exception,), {})

            client = IRCClient("ws://localhost:8097", ["#test"])
            assert client.message_count == 0

            count = 0
            async for _ in client.iter_messages():
                count += 1
                if count >= 3:
                    client._stop_event.set()
                    break

        assert client.message_count == 3


class TestIRCClientReconnect:
    """Test auto-reconnect with exponential backoff."""

    @pytest.mark.asyncio
    async def test_reconnects_on_connection_closed(self):
        """Client should reconnect after a ConnectionClosed exception."""
        call_count = 0
        fake_ws_1 = FakeWebSocket(
            [":a!u@h PRIVMSG #test :before disconnect\r\n"],
            close_after=1,
        )
        fake_ws_2 = FakeWebSocket(
            [":b!u@h PRIVMSG #test :after reconnect\r\n"],
        )

        import websockets.exceptions

        async def fake_connect(url):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return fake_ws_1
            return fake_ws_2

        with patch("chat_to_cop.ingestion.irc_client.websockets") as mock_ws:
            mock_ws.connect = AsyncMock(side_effect=fake_connect)
            mock_ws.exceptions.ConnectionClosed = websockets.exceptions.ConnectionClosed
            mock_ws.exceptions.WebSocketException = websockets.exceptions.WebSocketException

            client = IRCClient(
                "ws://localhost:8097",
                ["#test"],
                initial_backoff=0.01,  # Fast backoff for testing
                max_backoff=0.05,
            )
            messages = []

            async for msg in client.iter_messages():
                messages.append(msg)
                if len(messages) >= 2:
                    client._stop_event.set()
                    break

        assert len(messages) == 2
        assert messages[0].content == "before disconnect"
        assert messages[1].content == "after reconnect"
        assert client.reconnect_count >= 1
        assert call_count >= 2

    @pytest.mark.asyncio
    async def test_tenacity_backoff_config(self):
        """Verify tenacity is configured with correct exponential backoff parameters."""
        fail_count = 0
        fake_ws = FakeWebSocket([":a!u@h PRIVMSG #test :finally\r\n"])

        import websockets.exceptions

        async def fail_then_succeed(url):
            nonlocal fail_count
            fail_count += 1
            if fail_count <= 3:
                raise OSError(f"fail #{fail_count}")
            return fake_ws

        with patch("chat_to_cop.ingestion.irc_client.websockets") as mock_ws:
            mock_ws.connect = AsyncMock(side_effect=fail_then_succeed)
            mock_ws.exceptions.ConnectionClosed = websockets.exceptions.ConnectionClosed
            mock_ws.exceptions.WebSocketException = websockets.exceptions.WebSocketException

            client = IRCClient(
                "ws://localhost:8097",
                ["#test"],
                initial_backoff=0.01,
                max_backoff=0.05,
            )
            messages = []

            async for msg in client.iter_messages():
                messages.append(msg)
                client._stop_event.set()
                break

        # Tenacity retried connect() until it succeeded
        assert fail_count == 4  # 3 failures + 1 success
        assert len(messages) == 1
        assert messages[0].content == "finally"
        # reconnect_count tracks each failed attempt via _log_retry
        assert client.reconnect_count == 3

    @pytest.mark.asyncio
    async def test_reconnects_on_oserror(self):
        """OSError (network issues) should trigger reconnect."""
        call_count = 0

        fake_ws = FakeWebSocket(
            [":a!u@h PRIVMSG #test :recovered\r\n"],
        )

        async def fake_connect(url):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OSError("Network unreachable")
            return fake_ws

        with patch("chat_to_cop.ingestion.irc_client.websockets") as mock_ws:
            mock_ws.connect = AsyncMock(side_effect=fake_connect)
            mock_ws.exceptions = MagicMock()
            mock_ws.exceptions.ConnectionClosed = type("ConnectionClosed", (Exception,), {})
            mock_ws.exceptions.WebSocketException = type("WebSocketException", (Exception,), {})

            client = IRCClient(
                "ws://localhost:8097",
                ["#test"],
                initial_backoff=0.01,
                max_backoff=0.05,
            )
            messages = []

            async for msg in client.iter_messages():
                messages.append(msg)
                if len(messages) >= 1:
                    client._stop_event.set()
                    break

        assert len(messages) == 1
        assert messages[0].content == "recovered"
        assert client.reconnect_count >= 1


class TestIRCClientStopEvent:
    """Test graceful shutdown via stop event."""

    @pytest.mark.asyncio
    async def test_stop_event_breaks_loop(self):
        """Setting the stop event should terminate iter_messages."""
        fake_ws = FakeWebSocket(
            [
                ":a!u@h PRIVMSG #test :msg1\r\n",
            ]
        )

        with patch("chat_to_cop.ingestion.irc_client.websockets") as mock_ws:
            mock_ws.connect = AsyncMock(return_value=fake_ws)
            mock_ws.exceptions = MagicMock()
            mock_ws.exceptions.ConnectionClosed = type("ConnectionClosed", (Exception,), {})
            mock_ws.exceptions.WebSocketException = type("WebSocketException", (Exception,), {})

            client = IRCClient("ws://localhost:8097", ["#test"])

            messages = []
            async for msg in client.iter_messages():
                messages.append(msg)
                # Stop after first message
                client._stop_event.set()
                break

        assert len(messages) == 1

    @pytest.mark.asyncio
    async def test_disconnect_sets_stop_event(self):
        fake_ws = FakeWebSocket([])

        with patch("chat_to_cop.ingestion.irc_client.websockets") as mock_ws:
            mock_ws.connect = AsyncMock(return_value=fake_ws)

            client = IRCClient("ws://localhost:8097", ["#test"])
            await client.connect()
            await client.disconnect()

        assert client._stop_event.is_set()
        assert client.connected is False


class TestIRCMessageInterface:
    """Verify that live messages match the IRCMessage interface from replay."""

    def test_message_has_all_required_fields(self):
        line = ":Hydro_Tank!u@h PRIVMSG #c2_coord :RR15 F+40"
        msg = parse_irc_message(line)
        assert msg is not None

        # All fields that replay_messages produces
        assert hasattr(msg, "timestamp")
        assert hasattr(msg, "channel")
        assert hasattr(msg, "sender")
        assert hasattr(msg, "content")
        assert hasattr(msg, "raw_line")
        assert hasattr(msg, "is_stt")
        assert hasattr(msg, "priority")

    def test_stt_channel_detection(self):
        line = ":stt-bot!u@h PRIVMSG #stt_hydroBMA :hostile contacts north"
        msg = parse_irc_message(line)
        assert msg is not None
        assert msg.is_stt is True

    def test_regular_channel_not_stt(self):
        line = ":user!u@h PRIVMSG #c2_coord :hello"
        msg = parse_irc_message(line)
        assert msg is not None
        assert msg.is_stt is False


class TestChannelDiscovery:
    """Test IRC LIST-based channel discovery."""

    def test_discovery_defaults(self):
        client = IRCClient("ws://localhost:8097", ["#c2_coord"])
        assert client.discover_channels is True
        assert client.max_channels == 50

    def test_discovery_disabled(self):
        client = IRCClient("ws://localhost:8097", ["#c2_coord"], discover_channels=False)
        assert client.discover_channels is False

    def test_handle_list_line_when_not_collecting(self):
        client = IRCClient("ws://localhost:8097", ["#c2_coord"])
        assert client._pending_list is None
        assert client._handle_list_line(":server 322 bot #fires 5 :topic") is False

    def test_handle_list_line_collects_channels(self):
        client = IRCClient("ws://localhost:8097", ["#c2_coord"])
        client._pending_list = []
        assert client._handle_list_line(":server 322 bot #fires 5 :Fire control") is True
        assert client._handle_list_line(":server 322 bot #new_net 3 :New net") is True
        assert len(client._pending_list) == 2
        assert client._pending_list[0] == ("#fires", 5)
        assert client._pending_list[1] == ("#new_net", 3)

    def test_handle_list_end_triggers_finish(self):
        client = IRCClient("ws://localhost:8097", ["#c2_coord"])
        client._pending_list = [("#fires", 5)]
        assert client._handle_list_line(":server 323 bot :End of /LIST") is True
        assert client._pending_list is None
        assert "#fires" in client.channels

    def test_finish_discovery_skips_existing_channels(self):
        client = IRCClient("ws://localhost:8097", ["#c2_coord", "#fires"])
        client._finish_discovery([("#c2_coord", 10), ("#fires", 5)])
        assert len(client.channels) == 2

    def test_finish_discovery_skips_empty_channels(self):
        client = IRCClient("ws://localhost:8097", ["#c2_coord"])
        client._finish_discovery([("#empty", 0)])
        assert "#empty" not in client.channels

    def test_finish_discovery_joins_new_channels(self):
        client = IRCClient("ws://localhost:8097", ["#c2_coord"])
        client._finish_discovery([("#fires", 5), ("#isr", 3)])
        assert "#fires" in client.channels
        assert "#isr" in client.channels

    def test_finish_discovery_respects_max_channels(self):
        client = IRCClient("ws://localhost:8097", ["#c2_coord"], max_channels=2)
        client._finish_discovery([("#fires", 5), ("#isr", 3)])
        assert len(client.channels) == 2
        assert "#fires" in client.channels

    def test_finish_discovery_prefers_popular_channels(self):
        client = IRCClient("ws://localhost:8097", ["#c2_coord"], max_channels=2)
        client._finish_discovery([("#quiet", 1), ("#busy", 20), ("#medium", 5)])
        assert "#busy" in client.channels
        assert "#quiet" not in client.channels

    def test_config_max_channels(self):
        client = IRCClient("ws://localhost:8097", ["#c2_coord"], max_channels=10)
        assert client.max_channels == 10

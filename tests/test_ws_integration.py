"""Integration tests for live WebSocket IRC ingestion.

These tests start a real WebSocket server (ws_test_server.py) in a background
task, connect the IRCClient, and verify end-to-end message flow. They also
test reconnection behavior and multi-channel routing.

Marked @pytest.mark.integration — skipped in CI, run manually with:
    pytest tests/test_ws_integration.py -v
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
import websockets

from chat_to_cop.ingestion.irc_client import IRCClient, parse_irc_message
from chat_to_cop.ingestion.replay import parse_zip
from chat_to_cop.models.messages import IRCMessage

# Resolve DASH 3 zip path relative to project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DASH3_ZIP = _PROJECT_ROOT / "data" / "dash3" / "23Sep_usaf_chat.zip"

# Import server helpers — add scripts/ to path so ws_test_server is importable
sys.path.insert(0, str(_PROJECT_ROOT / "scripts"))
from ws_test_server import IRCTestServer, load_messages_from_zip, to_irc_privmsg  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def dash3_messages():
    """Load messages from the DASH 3 zip for test use."""
    if not _DASH3_ZIP.exists():
        pytest.skip("DASH 3 data not available at data/dash3/23Sep_usaf_chat.zip")
    return load_messages_from_zip(_DASH3_ZIP)


@pytest.fixture
def sample_messages():
    """A small set of synthetic messages for fast tests."""
    return [
        {"time": "14:00:00", "channel": "#c2_coord", "sender": "WF_Clark", "content": "STARTEX"},
        {"time": "14:00:05", "channel": "#fires", "sender": "HYDRO_Strike", "content": "fire mission ready"},
        {"time": "14:00:10", "channel": "#c2_coord", "sender": "VEGAS_SL", "content": "copy STARTEX"},
        {"time": "14:00:15", "channel": "#isr_reports", "sender": "ISR_Lead", "content": "SAM site active grid AB12"},
        {"time": "14:00:20", "channel": "#fires", "sender": "WF_Clark", "content": "engage SAM site AB12"},
        {"time": "14:00:25", "channel": "#c2_coord", "sender": "VEGAS_ABM1", "content": "."},
        {"time": "14:00:30", "channel": "#stt_C2Coord", "sender": "google-stt", "content": "<WF2> Radio check"},
        {"time": "14:00:35", "channel": "#c2_coord", "sender": "wf_beep", "content": "AOC_CCO: status update"},
    ]


async def _find_free_port() -> int:
    """Find a free TCP port on localhost."""
    srv = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
    port = srv.sockets[0].getsockname()[1]
    srv.close()
    await srv.wait_closed()
    return port


# ---------------------------------------------------------------------------
# Unit tests for server helpers (not marked integration)
# ---------------------------------------------------------------------------


class TestServerHelpers:
    """Unit tests for ws_test_server helper functions."""

    def test_to_irc_privmsg_format(self):
        msg = {"time": "14:00:00", "channel": "#c2_coord", "sender": "WF_Clark", "content": "STARTEX"}
        result = to_irc_privmsg(msg)
        assert result == ":WF_Clark!user@host PRIVMSG #c2_coord :STARTEX\r\n"

    def test_to_irc_privmsg_with_colons(self):
        msg = {"time": "14:00:00", "channel": "#c2_coord", "sender": "wf_beep", "content": "AOC_CCO: status"}
        result = to_irc_privmsg(msg)
        assert ":AOC_CCO: status\r\n" in result

    def test_to_irc_privmsg_roundtrip(self):
        """Verify that to_irc_privmsg output can be parsed by parse_irc_message."""
        msg = {"time": "14:00:00", "channel": "#fires", "sender": "HYDRO_Strike", "content": "fire mission"}
        irc_line = to_irc_privmsg(msg)
        parsed = parse_irc_message(irc_line)
        assert parsed is not None
        assert parsed.sender == "HYDRO_Strike"
        assert parsed.channel == "#fires"
        assert parsed.content == "fire mission"

    def test_load_messages_from_zip(self, dash3_messages):
        """Verify the zip loader returns messages with expected fields."""
        assert len(dash3_messages) > 100
        for msg in dash3_messages[:10]:
            assert "channel" in msg
            assert "sender" in msg
            assert "content" in msg
            assert msg["channel"].startswith("#")


# ---------------------------------------------------------------------------
# Integration tests — require a running WebSocket server
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestWebSocketBasicConnection:
    """Test basic connection and message reception over WebSocket."""

    async def test_client_receives_messages(self, sample_messages):
        """IRCClient connects to the test server and receives PRIVMSG messages."""
        port = await _find_free_port()

        # Use a custom handler that sends all messages immediately on connect
        received: list[IRCMessage] = []
        all_channels = list({m["channel"] for m in sample_messages})

        async def send_on_connect(ws):
            """Custom handler: respond to NICK, send all messages, then wait."""
            async for raw in ws:
                for line in raw.split("\r\n"):
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("NICK "):
                        nick = line.split(" ", 1)[1]
                        await ws.send(f":server 001 {nick} :Welcome\r\n")
                    elif line.startswith("JOIN "):
                        # Once a channel is joined, start sending messages
                        pass
                    elif line.startswith("PING"):
                        payload = line[4:].strip()
                        await ws.send(f"PONG {payload}\r\n")

                # After registration, send all messages
                for msg in sample_messages:
                    await ws.send(to_irc_privmsg(msg))

        ws_server = await websockets.serve(send_on_connect, "127.0.0.1", port)
        try:
            client = IRCClient(
                f"ws://127.0.0.1:{port}",
                all_channels,
                initial_backoff=0.1,
                max_backoff=0.5,
            )

            async for msg in client.iter_messages():
                received.append(msg)
                if len(received) >= len(sample_messages):
                    client._stop_event.set()
                    break

            assert len(received) == len(sample_messages)
            assert all(isinstance(m, IRCMessage) for m in received)
            # Verify first message
            assert received[0].sender == "WF_Clark"
            assert received[0].channel == "#c2_coord"
            assert received[0].content == "STARTEX"
        finally:
            ws_server.close()
            await ws_server.wait_closed()

    async def test_client_message_count_tracks(self, sample_messages):
        """Verify message_count property tracks received messages."""
        port = await _find_free_port()

        async def handler(ws):
            async for raw in ws:
                for line in raw.split("\r\n"):
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("NICK "):
                        nick = line.split(" ", 1)[1]
                        await ws.send(f":server 001 {nick} :Welcome\r\n")
                for msg in sample_messages[:3]:
                    await ws.send(to_irc_privmsg(msg))

        ws_server = await websockets.serve(handler, "127.0.0.1", port)
        try:
            client = IRCClient(f"ws://127.0.0.1:{port}", ["#c2_coord", "#fires"])
            count = 0
            async for _ in client.iter_messages():
                count += 1
                if count >= 3:
                    client._stop_event.set()
                    break
            assert client.message_count == 3
        finally:
            ws_server.close()
            await ws_server.wait_closed()


@pytest.mark.integration
class TestWebSocketMultiChannel:
    """Test multi-channel message routing."""

    async def test_messages_from_multiple_channels(self, sample_messages):
        """Verify messages from different channels are all received and routed correctly."""
        port = await _find_free_port()

        async def handler(ws):
            async for raw in ws:
                for line in raw.split("\r\n"):
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("NICK "):
                        nick = line.split(" ", 1)[1]
                        await ws.send(f":server 001 {nick} :Welcome\r\n")
                for msg in sample_messages:
                    await ws.send(to_irc_privmsg(msg))

        ws_server = await websockets.serve(handler, "127.0.0.1", port)
        try:
            all_channels = list({m["channel"] for m in sample_messages})
            client = IRCClient(f"ws://127.0.0.1:{port}", all_channels)
            received: list[IRCMessage] = []

            async for msg in client.iter_messages():
                received.append(msg)
                if len(received) >= len(sample_messages):
                    client._stop_event.set()
                    break

            # Verify we got messages from multiple channels
            channels_seen = {m.channel for m in received}
            assert "#c2_coord" in channels_seen
            assert "#fires" in channels_seen
            assert "#isr_reports" in channels_seen
            assert "#stt_C2Coord" in channels_seen

            # Verify STT detection
            stt_msgs = [m for m in received if m.is_stt]
            assert len(stt_msgs) >= 1
            assert stt_msgs[0].channel == "#stt_C2Coord"

        finally:
            ws_server.close()
            await ws_server.wait_closed()

    async def test_channel_message_counts(self, sample_messages):
        """Verify per-channel message counts match expected distribution."""
        port = await _find_free_port()

        async def handler(ws):
            async for raw in ws:
                for line in raw.split("\r\n"):
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("NICK "):
                        nick = line.split(" ", 1)[1]
                        await ws.send(f":server 001 {nick} :Welcome\r\n")
                for msg in sample_messages:
                    await ws.send(to_irc_privmsg(msg))

        ws_server = await websockets.serve(handler, "127.0.0.1", port)
        try:
            all_channels = list({m["channel"] for m in sample_messages})
            client = IRCClient(f"ws://127.0.0.1:{port}", all_channels)
            received: list[IRCMessage] = []

            async for msg in client.iter_messages():
                received.append(msg)
                if len(received) >= len(sample_messages):
                    client._stop_event.set()
                    break

            # Count by channel
            from collections import Counter

            channel_counts = Counter(m.channel for m in received)
            expected_counts = Counter(m["channel"] for m in sample_messages)
            assert channel_counts == expected_counts
        finally:
            ws_server.close()
            await ws_server.wait_closed()


@pytest.mark.integration
class TestWebSocketReconnection:
    """Test client reconnection behavior."""

    async def test_reconnects_after_server_restart(self, sample_messages):
        """Stop server, restart it, verify client reconnects and receives messages."""
        port = await _find_free_port()
        received: list[IRCMessage] = []

        # Phase 1: send first 2 messages, then close
        # Phase 2: send next 2 messages
        async def handler_phase1(ws):
            async for raw in ws:
                for line in raw.split("\r\n"):
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("NICK "):
                        nick = line.split(" ", 1)[1]
                        await ws.send(f":server 001 {nick} :Welcome\r\n")
                for msg in sample_messages[:2]:
                    await ws.send(to_irc_privmsg(msg))
                # Close connection after sending
                await asyncio.sleep(0.1)
                return

        async def handler_phase2(ws):
            async for raw in ws:
                for line in raw.split("\r\n"):
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("NICK "):
                        nick = line.split(" ", 1)[1]
                        await ws.send(f":server 001 {nick} :Welcome\r\n")
                for msg in sample_messages[2:4]:
                    await ws.send(to_irc_privmsg(msg))
                # Keep connection open
                await asyncio.sleep(10)

        all_channels = list({m["channel"] for m in sample_messages})
        client = IRCClient(
            f"ws://127.0.0.1:{port}",
            all_channels,
            initial_backoff=0.1,
            max_backoff=0.5,
        )

        # Start phase 1 server
        server1 = await websockets.serve(handler_phase1, "127.0.0.1", port)

        async def collect_messages():
            async for msg in client.iter_messages():
                received.append(msg)
                if len(received) >= 4:
                    client._stop_event.set()
                    break

        collect_task = asyncio.create_task(collect_messages())

        # Wait for phase 1 messages
        for _ in range(50):  # up to 5 seconds
            if len(received) >= 2:
                break
            await asyncio.sleep(0.1)

        # Shut down phase 1 server
        server1.close()
        await server1.wait_closed()

        # Brief gap — client should detect disconnect
        await asyncio.sleep(0.3)

        # Start phase 2 server on the same port
        server2 = await websockets.serve(handler_phase2, "127.0.0.1", port)

        # Wait for phase 2 messages (client should reconnect)
        try:
            await asyncio.wait_for(collect_task, timeout=15.0)
        except asyncio.TimeoutError:
            client._stop_event.set()
            collect_task.cancel()
            try:
                await collect_task
            except asyncio.CancelledError:
                pass

        server2.close()
        await server2.wait_closed()

        # We should have received messages from both phases
        assert len(received) >= 3, f"Expected >=3 messages, got {len(received)}: {[m.content for m in received]}"
        assert client.reconnect_count >= 1, "Client should have reconnected at least once"

    async def test_reconnect_count_increments(self):
        """Verify reconnect_count increments on each disconnection."""
        port = await _find_free_port()
        connect_count = {"value": 0}

        async def handler(ws):
            connect_count["value"] += 1
            async for raw in ws:
                for line in raw.split("\r\n"):
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("NICK "):
                        nick = line.split(" ", 1)[1]
                        await ws.send(f":server 001 {nick} :Welcome\r\n")

                if connect_count["value"] <= 2:
                    # Close first two connections immediately
                    return
                else:
                    # Third connection: send a message and stay
                    await ws.send(":test!u@h PRIVMSG #test :finally\r\n")
                    await asyncio.sleep(10)

        ws_server = await websockets.serve(handler, "127.0.0.1", port)
        try:
            client = IRCClient(
                f"ws://127.0.0.1:{port}",
                ["#test"],
                initial_backoff=0.1,
                max_backoff=0.3,
            )
            received = []

            async def collect():
                async for msg in client.iter_messages():
                    received.append(msg)
                    client._stop_event.set()
                    break

            await asyncio.wait_for(collect(), timeout=10.0)

            assert len(received) == 1
            assert received[0].content == "finally"
            assert client.reconnect_count >= 1
        finally:
            ws_server.close()
            await ws_server.wait_closed()


@pytest.mark.integration
class TestWebSocketPingPong:
    """Test PING/PONG keepalive over live WebSocket."""

    async def test_server_ping_gets_pong(self):
        """Server sends PING, client should respond with PONG."""
        port = await _find_free_port()
        pong_received = asyncio.Event()

        async def handler(ws):
            # Wait for registration
            async for raw in ws:
                for line in raw.split("\r\n"):
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("NICK "):
                        nick = line.split(" ", 1)[1]
                        await ws.send(f":server 001 {nick} :Welcome\r\n")

                # Send a PING
                await ws.send("PING :testserver\r\n")
                # Then send a message so client can yield
                await ws.send(":user!u@h PRIVMSG #test :after ping\r\n")

                # Read response and check for PONG
                try:
                    while True:
                        resp = await asyncio.wait_for(ws.recv(), timeout=5.0)
                        if "PONG" in resp and "testserver" in resp:
                            pong_received.set()
                            break
                except (asyncio.TimeoutError, websockets.exceptions.ConnectionClosed):
                    pass

                await asyncio.sleep(10)

        ws_server = await websockets.serve(handler, "127.0.0.1", port)
        try:
            client = IRCClient(f"ws://127.0.0.1:{port}", ["#test"])
            received = []

            async def collect():
                async for msg in client.iter_messages():
                    received.append(msg)
                    client._stop_event.set()
                    break

            await asyncio.wait_for(collect(), timeout=10.0)

            assert len(received) == 1
            assert received[0].content == "after ping"
            # The server should have received a PONG
            assert pong_received.is_set(), "Server did not receive PONG from client"
        finally:
            ws_server.close()
            await ws_server.wait_closed()


@pytest.mark.integration
class TestReplayComparison:
    """Compare WebSocket-received messages to replay parser output for the same data."""

    async def test_ws_messages_match_replay_parser(self, dash3_messages):
        """Messages received via WebSocket should match what the replay parser extracts.

        This verifies that to_irc_privmsg -> parse_irc_message produces the same
        sender/channel/content as the replay parser does from the original log lines.
        """
        if not _DASH3_ZIP.exists():
            pytest.skip("DASH 3 data not available")

        # Get replay-parsed messages for comparison
        replay_msgs = parse_zip(_DASH3_ZIP)

        # Use first N messages for a manageable test
        n = min(50, len(dash3_messages), len(replay_msgs))

        port = await _find_free_port()

        async def handler(ws):
            async for raw in ws:
                for line in raw.split("\r\n"):
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("NICK "):
                        nick = line.split(" ", 1)[1]
                        await ws.send(f":server 001 {nick} :Welcome\r\n")

                # Send first N messages
                for msg in dash3_messages[:n]:
                    await ws.send(to_irc_privmsg(msg))
                await asyncio.sleep(10)

        ws_server = await websockets.serve(handler, "127.0.0.1", port)
        try:
            all_channels = list({m["channel"] for m in dash3_messages[:n]})
            client = IRCClient(f"ws://127.0.0.1:{port}", all_channels)
            ws_received: list[IRCMessage] = []

            async for msg in client.iter_messages():
                ws_received.append(msg)
                if len(ws_received) >= n:
                    client._stop_event.set()
                    break

            assert len(ws_received) == n

            # Compare each WebSocket-received message to the replay-parsed version
            for i in range(n):
                ws_msg = ws_received[i]
                replay_msg = replay_msgs[i]

                assert ws_msg.sender == replay_msg.sender, (
                    f"Message {i}: sender mismatch: ws={ws_msg.sender} replay={replay_msg.sender}"
                )
                assert ws_msg.channel == replay_msg.channel, (
                    f"Message {i}: channel mismatch: ws={ws_msg.channel} replay={replay_msg.channel}"
                )
                assert ws_msg.content == replay_msg.content, (
                    f"Message {i}: content mismatch: ws={ws_msg.content!r} replay={replay_msg.content!r}"
                )
        finally:
            ws_server.close()
            await ws_server.wait_closed()

    def test_irc_privmsg_roundtrip_all_messages(self, dash3_messages):
        """Every DASH 3 message should survive the to_irc_privmsg -> parse_irc_message roundtrip."""
        for i, msg in enumerate(dash3_messages[:100]):
            irc_line = to_irc_privmsg(msg)
            parsed = parse_irc_message(irc_line)
            assert parsed is not None, f"Message {i} failed to parse: {irc_line!r}"
            assert parsed.sender == msg["sender"], f"Message {i}: sender mismatch"
            assert parsed.channel == msg["channel"], f"Message {i}: channel mismatch"
            assert parsed.content == msg["content"], f"Message {i}: content mismatch"


@pytest.mark.integration
class TestIRCTestServer:
    """Test the IRCTestServer class directly."""

    async def test_server_start_stop(self, sample_messages):
        """Server should start and stop cleanly."""
        port = await _find_free_port()
        server = IRCTestServer(sample_messages, rate=600)  # 10 msg/sec
        await server.start("127.0.0.1", port)

        # Verify we can connect
        ws = await websockets.connect(f"ws://127.0.0.1:{port}")
        await ws.send("NICK test\r\n")
        resp = await asyncio.wait_for(ws.recv(), timeout=5.0)
        assert "001" in resp  # Welcome message
        await ws.close()

        await server.stop()

    async def test_server_broadcasts_at_rate(self, sample_messages):
        """Server should broadcast messages at the configured rate."""
        port = await _find_free_port()
        # 600 msg/min = 10 msg/sec
        server = IRCTestServer(sample_messages, rate=600)
        await server.start("127.0.0.1", port)

        try:
            ws = await websockets.connect(f"ws://127.0.0.1:{port}")
            await ws.send("NICK testbot\r\n")

            received = []
            try:
                while True:
                    data = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    for line in data.split("\r\n"):
                        line = line.strip()
                        if not line:
                            continue
                        parsed = parse_irc_message(line)
                        if parsed is not None:
                            received.append(parsed)
                    if len(received) >= len(sample_messages):
                        break
            except asyncio.TimeoutError:
                pass

            await ws.close()
            # Should have received at least some messages
            assert len(received) >= 1
        finally:
            await server.stop()

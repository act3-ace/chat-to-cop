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


# TODO: Implement IRCClient
# - __init__(server_url, channels)
# - async connect()
# - async iter_messages() -> AsyncIterator[IRCMessage]
# - Reconnection with exponential backoff
# - Channel management

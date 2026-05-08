"""Multi-line message reassembly for structured military messages.

Military chat often uses numbered-line formats for structured requests
(9-line CAS, cyber taskings, ISR requests, etc.). These arrive as
separate IRC messages but form a single coherent tasking:

    LINE 0: BCOA 0502-01A
    LINE 1: OCO Prime, to deny cyber on mobile C2 JTN: TL113
    LINE 2: Cigar 252 / 331 NM
    LINE 3: System Denial, Power Denial, Geolocation, DDOS
    LINE 4: for specified duration
    LINE 5: USSF will report if denial is successful

This module buffers consecutive LINE N messages from the same sender
on the same channel and merges them into a single IRCMessage before
the extraction pipeline sees them.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator

from chat_to_cop.models.messages import IRCMessage

_LINE_RE = re.compile(r"^(?:LINE|Line|line)\s*(\d+)\s*:", re.IGNORECASE)

_FLUSH_TIMEOUT_S = 3.0


class MultiLineBuffer:
    """Buffers consecutive LINE N: messages from the same sender/channel.

    Flush triggers:
    - Different sender or channel speaks
    - Non-LINE message from the same sender
    - Timeout (no new LINE message in _FLUSH_TIMEOUT_S seconds)
    - LINE 0 arrives when buffer is non-empty (new structured message starting)
    """

    def __init__(self, flush_timeout: float = _FLUSH_TIMEOUT_S):
        self.flush_timeout = flush_timeout
        self._buffer: list[IRCMessage] = []
        self._channel: str = ""
        self._sender: str = ""

    @property
    def pending(self) -> bool:
        return len(self._buffer) > 0

    def _merge(self) -> IRCMessage:
        """Merge buffered LINE messages into a single IRCMessage."""
        lines_by_num: dict[int, str] = {}
        for msg in self._buffer:
            m = _LINE_RE.match(msg.content)
            if m:
                line_num = int(m.group(1))
                line_content = msg.content[m.end() :].strip()
                lines_by_num[line_num] = line_content
            else:
                lines_by_num[len(lines_by_num)] = msg.content

        sorted_lines = [lines_by_num[k] for k in sorted(lines_by_num)]
        merged_content = "\n".join(sorted_lines)

        raw_lines = "\n".join(msg.raw_line or msg.content for msg in self._buffer)

        merged = IRCMessage(
            timestamp=self._buffer[0].timestamp,
            channel=self._channel,
            sender=self._sender,
            content=merged_content,
            raw_line=raw_lines,
        )
        return merged

    def flush(self) -> IRCMessage | None:
        """Flush the buffer, returning a merged message or None."""
        if not self._buffer:
            return None
        if len(self._buffer) == 1:
            msg = self._buffer[0]
            self._buffer.clear()
            return msg
        merged = self._merge()
        self._buffer.clear()
        return merged

    def add(self, msg: IRCMessage) -> IRCMessage | None:
        """Add a message. Returns a flushed message if the buffer was displaced."""
        is_line = _LINE_RE.match(msg.content) is not None
        same_context = msg.channel == self._channel and msg.sender == self._sender

        if is_line and same_context and self._buffer:
            line_match = _LINE_RE.match(msg.content)
            if line_match and int(line_match.group(1)) == 0 and self._buffer:
                flushed = self.flush()
                self._channel = msg.channel
                self._sender = msg.sender
                self._buffer = [msg]
                return flushed
            self._buffer.append(msg)
            return None

        if is_line and not self._buffer:
            self._channel = msg.channel
            self._sender = msg.sender
            self._buffer = [msg]
            return None

        if self._buffer:
            flushed = self.flush()
            if is_line:
                self._channel = msg.channel
                self._sender = msg.sender
                self._buffer = [msg]
                return flushed
            self._channel = ""
            self._sender = ""
            return flushed
            # caller must also handle msg separately -- see reassemble_multiline

        return msg


async def reassemble_multiline(
    source: AsyncIterator[IRCMessage],
    flush_timeout: float = _FLUSH_TIMEOUT_S,
) -> AsyncIterator[IRCMessage]:
    """Wrap a message source to reassemble multi-line structured messages.

    Buffers consecutive LINE N: messages from the same sender/channel,
    merging them into a single message. Non-LINE messages pass through
    immediately (after flushing any pending buffer).

    When the buffer has pending lines and no new message arrives within
    *flush_timeout* seconds, the buffer is flushed automatically so
    messages are not held indefinitely during exercise breaks.
    """
    buf = MultiLineBuffer(flush_timeout=flush_timeout)
    ait = source.__aiter__()

    # We can't use asyncio.wait_for on __anext__() directly because
    # cancellation corrupts async generator state. Instead, run the
    # fetch in a shielded task and race it against a sleep.
    pending_next: asyncio.Task[IRCMessage] | None = None

    while True:
        try:
            if pending_next is None:
                pending_next = asyncio.ensure_future(ait.__anext__())

            if buf.pending:
                done, _ = await asyncio.wait({pending_next}, timeout=flush_timeout)
                if not done:
                    flushed = buf.flush()
                    if flushed:
                        yield flushed
                    continue
            else:
                await asyncio.shield(pending_next)

            msg = pending_next.result()
            pending_next = None
        except StopAsyncIteration:
            pending_next = None
            break

        is_line = _LINE_RE.match(msg.content) is not None
        result = buf.add(msg)
        if result:
            yield result
        if not is_line and result is not None and result is not msg:
            yield msg
        elif not is_line and result is None and not buf.pending:
            yield msg

    final = buf.flush()
    if final:
        yield final


def is_multiline_message(content: str) -> bool:
    """Check if a message is part of a multi-line structured format."""
    return _LINE_RE.match(content) is not None

"""Channel Agent: stateful per-channel world-state extractor.

Each channel agent maintains:
- Conversation window (sliding buffer of recent messages)
- An LLM backend for extraction (swappable, degradable)

The agent receives IRCMessages and emits CoPUpdate objects.
Speaker models and world state context come in Sprint 2.
"""

from __future__ import annotations

from collections import deque
from datetime import timedelta

from chat_to_cop.backend.base import LLMBackend
from chat_to_cop.backend.openai_compat import DEFAULT_GLOSSARY, build_system_prompt
from chat_to_cop.metrics import metrics
from chat_to_cop.models.cop_update import CoPUpdate, UpdateType
from chat_to_cop.models.messages import IRCMessage


class ChannelAgent:
    """Stateful agent for a single IRC channel.

    Maintains a sliding conversation window and calls the LLM backend
    to extract structured CoPUpdates from each message in context.
    """

    def __init__(
        self,
        channel: str,
        backend: LLMBackend,
        window_size: int = 50,
        window_minutes: float = 15.0,
        glossary: str = DEFAULT_GLOSSARY,
    ) -> None:
        self.channel = channel
        self.backend = backend
        self.window_size = window_size
        self.window_minutes = window_minutes
        self.glossary = glossary
        self._window: deque[IRCMessage] = deque(maxlen=window_size)
        self._message_count = 0

    @property
    def message_count(self) -> int:
        return self._message_count

    def _trim_window_by_time(self) -> None:
        """Remove messages older than window_minutes from the front.

        Uses the latest message timestamp as reference (not wall clock)
        so replay with historical data works correctly.
        """
        if len(self._window) < 2:
            return
        latest = self._window[-1].timestamp
        cutoff = latest - timedelta(minutes=self.window_minutes)
        while self._window and self._window[0].timestamp < cutoff:
            self._window.popleft()

    def _build_messages(self, current: IRCMessage) -> list[dict[str, str]]:
        """Build the OpenAI message list from system prompt + conversation context."""
        system_prompt = build_system_prompt(glossary=self.glossary)

        # Build conversation context from window
        context_lines = []
        for msg in self._window:
            context_lines.append(f"[{msg.timestamp.strftime('%H:%M:%S')}] {msg.sender}: {msg.content}")

        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]

        if context_lines:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Recent conversation context:\n"
                        + "\n".join(context_lines)
                        + "\n\nExtract world-state updates from the LATEST message only. "
                        "Use the context to resolve references."
                    ),
                }
            )
        else:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"[{current.timestamp.strftime('%H:%M:%S')}] "
                        f"{current.sender}: {current.content}\n\n"
                        "Extract world-state updates from this message."
                    ),
                }
            )

        return messages

    async def process_message(self, message: IRCMessage) -> list[CoPUpdate]:
        """Process a single message and return extracted CoPUpdates.

        The message is added to the conversation window, then the full
        window is sent to the LLM for extraction. Returns an empty list
        if the message has no actionable content.
        """
        self._message_count += 1
        labels = {"channel": self.channel}
        metrics.inc("agent_messages_received_total", labels=labels)

        # Add to window
        self._window.append(message)
        self._trim_window_by_time()

        # Build prompt and extract
        llm_messages = self._build_messages(message)

        try:
            async with metrics.async_timer("agent_extraction_latency_seconds", labels=labels):
                result = await self.backend.extract(llm_messages, CoPUpdate)
        except Exception as e:
            metrics.inc("agent_extraction_errors_total", labels=labels)
            # Return a passthrough update so flow never stops
            return [
                CoPUpdate(
                    update_type=UpdateType.NONE,
                    confidence=0.0,
                    extraction_method="error",
                    entities=[],
                    source_channel=self.channel,
                    source_speaker=message.sender,
                    source_message=message.content,
                    timestamp=message.timestamp,
                    reasoning=f"Extraction error: {e}",
                )
            ]

        # Fill in source fields the LLM doesn't know
        result.source_channel = self.channel
        result.source_speaker = message.sender
        result.source_message = message.content
        result.timestamp = message.timestamp
        result.context_messages = [f"{m.sender}: {m.content}" for m in list(self._window)[-5:]]

        # Filter out NONE updates (acks, noise)
        if result.update_type == UpdateType.NONE:
            metrics.inc("agent_messages_filtered_total", labels=labels)
            return []

        metrics.inc("agent_updates_emitted_total", labels=labels)
        metrics.observe(
            "agent_confidence",
            result.confidence,
            labels=labels,
        )
        return [result]

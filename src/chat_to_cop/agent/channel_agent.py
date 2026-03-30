"""Channel Agent: stateful per-channel world-state extractor.

Each channel agent maintains:
- Conversation window (sliding buffer of recent messages)
- Speaker models (learned per-user profiles, CTA/SDAC framework)
- An LLM backend for extraction (swappable, degradable)

The agent receives IRCMessages and emits CoPUpdate objects.
"""

from __future__ import annotations

import re
from collections import deque
from datetime import timedelta

from loguru import logger

from chat_to_cop.backend.base import LLMBackend
from chat_to_cop.backend.openai_compat import DEFAULT_GLOSSARY, build_system_prompt
from chat_to_cop.calibration import CalibrationModel
from chat_to_cop.metrics import metrics
from chat_to_cop.models.cop_update import CoPUpdate, UpdateType
from chat_to_cop.models.messages import IRCMessage
from chat_to_cop.models.speaker import SpeakerInference, SpeakerRegistry

# Noise patterns — skip LLM call for these
_NOISE_RE = re.compile(
    r"^[\s.]+$"  # dots only
    r"|^\s*c\s*$"  # "c"
    r"|^\s*copy\s*$"  # standalone "copy" only
    r"|^\s*word\s*$"  # "word"
    r"|^\s*test\s*$"  # "test"
    r"|^\s*NSTR\s*$"  # "nothing significant to report"
    r"|^\s*roger\s*$"  # roger
    r"|^\s*affirm\s*$"  # affirm
    r"|^\s*wilco\s*$",  # will comply
    re.IGNORECASE,
)

# URL/link patterns — these are admin/debrief, not world-state
_URL_RE = re.compile(r"https?://\S+")

# Radio check patterns — not operational
_RADIO_CHECK_RE = re.compile(r"radio\s+check", re.IGNORECASE)

# Banter / radio-protocol noise — only match when the phrase dominates the message.
# These use fullmatch on stripped text (with optional trailing punctuation).
# Do NOT match if the phrase is embedded in a longer operational message.
#
# Specific banter phrases that are unambiguously non-operational even in longer messages:
_BANTER_SPECIFIC_RE = re.compile(
    r"(?:"
    r".*didn.t\s+say\s+over.*"  # banter about saying "over"
    r"|.*(?:not\s+out|i.m\s+over).*"  # "not out, I'm over" radio banter
    r"|.*what\s+you\s+want\s+\w*\??"  # "what you want girl?"
    r"|.*talk\s+a\s+little\s+louder.*"  # "talk a little louder"
    r"|.*can.t\s+go\s+over\s+and\s+out.*"  # "you can't go over and out"
    r"|.*let.s\s+do\s+some\s+radio.*"  # "let's do some radio tracks"
    r"|.*buh-?bye.*"  # "buh-bye"
    r"|.*see\s+ya.*"  # "see ya"
    r"|.*good\s+to\s+meet.*"  # "good to meet you"
    r")\s*[.!?,]*\s*",
    re.IGNORECASE,
)

# Radio-protocol phrases that CAN appear in operational messages.
# Only match when they are the dominant content (short message, no trailing ops data).
# "how me?" at end, "how do you hear me?", "loud and clear", "lima charlie",
# "say again", "over and out", "copy all" — these must be the whole message.
_BANTER_SHORT_RE = re.compile(
    r"(?:"
    r".*how\s+me\??"  # "how me?" at end of message (radio check)
    r"|.*how\s+do\s+you\s+hear(?:\s+me)?\??"  # "how do you hear me?"
    r"|.*loud\s+and\s+clear\s*"  # "loud and clear" without trailing content
    r"|.*lima\s+charlie\s*"  # "lima charlie" without trailing content
    r"|say\s+again\s*"  # "say again" alone
    r"|.*over\s+and\s+out\s*"  # "over and out"
    r"|copy\s+all"  # "copy all" as the entire message
    r")\s*[.!?,]*\s*",
    re.IGNORECASE,
)

# STARTEX/ENDEX patterns — exercise control, not world-state
_EXERCISE_CONTROL_RE = re.compile(
    r"\*{3,}.*(?:STARTEX|ENDEX|START\s+EX|END\s+EX).*\*{3,}"
    r"|^\s*(?:STARTEX|ENDEX)\b",
    re.IGNORECASE,
)


def _is_noise(content: str) -> bool:
    """Check if a message is noise that shouldn't be sent to the LLM.

    Catches: acks, dots, URLs/links, STARTEX/ENDEX, and short banter.
    """
    stripped = content.strip()

    # Regex noise patterns
    if _NOISE_RE.match(stripped):
        return True

    # URLs (debrief links, surveys)
    if _URL_RE.match(stripped):
        return True

    # Radio checks
    if _RADIO_CHECK_RE.search(stripped):
        return True

    # Banter — unambiguous phrases (safe to match even in longer messages)
    if _BANTER_SPECIFIC_RE.fullmatch(stripped):
        return True

    # Radio-protocol phrases — only noise when they ARE the whole message (short)
    if _BANTER_SHORT_RE.fullmatch(stripped):
        return True

    # Exercise control
    if _EXERCISE_CONTROL_RE.search(stripped):
        return True

    return False


# How often (in messages) to run LLM inference on a speaker's profile.
# First inference at message 3, then every 10 messages.
_SPEAKER_FIRST_INFERENCE_AT = 3
_SPEAKER_INFERENCE_INTERVAL = 10


class ChannelAgent:
    """Stateful agent for a single IRC channel.

    Maintains a sliding conversation window, per-speaker models, and
    calls the LLM backend to extract structured CoPUpdates from each
    message in context.
    """

    def __init__(
        self,
        channel: str,
        backend: LLMBackend,
        window_size: int = 50,
        window_minutes: float = 15.0,
        glossary: str = DEFAULT_GLOSSARY,
        use_speaker_models: bool = True,
        calibration_model: CalibrationModel | None = None,
    ) -> None:
        self.channel = channel
        self.backend = backend
        self.window_size = window_size
        self.window_minutes = window_minutes
        self.glossary = glossary
        self.use_speaker_models = use_speaker_models
        self.calibration_model = calibration_model
        self._window: deque[IRCMessage] = deque(maxlen=window_size)
        self._message_count = 0
        self._speakers = SpeakerRegistry()

    @property
    def message_count(self) -> int:
        return self._message_count

    @property
    def speakers(self) -> SpeakerRegistry:
        return self._speakers

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
        speaker_context = ""
        if self.use_speaker_models:
            speaker_context = self._speakers.format_all_for_prompt()

        system_prompt = build_system_prompt(
            glossary=self.glossary,
            speaker_context=speaker_context,
        )

        # Build conversation context from window
        context_lines = []
        for msg in self._window:
            context_lines.append(f"[{msg.timestamp.strftime('%H:%M:%S')}] {msg.sender}: {msg.content}")

        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]

        if context_lines:
            # Clearly separate context from the target message
            context_block = "\n".join(context_lines[:-1]) if len(context_lines) > 1 else ""
            latest_line = context_lines[-1]

            content_parts = []
            if context_block:
                content_parts.append(f"PRIOR CONTEXT (for reference only, do NOT extract from these):\n{context_block}")
            content_parts.append(f"\nEXTRACT FROM THIS MESSAGE ONLY:\n{latest_line}")

            messages.append(
                {
                    "role": "user",
                    "content": "\n".join(content_parts),
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

    def _should_infer_speaker(self, speaker_model) -> bool:
        """Determine whether to run LLM inference on a speaker's profile."""
        count = speaker_model.message_count
        if count == _SPEAKER_FIRST_INFERENCE_AT:
            return True
        if (
            count > _SPEAKER_FIRST_INFERENCE_AT
            and (count - _SPEAKER_FIRST_INFERENCE_AT) % _SPEAKER_INFERENCE_INTERVAL == 0
        ):
            return True
        return False

    async def _infer_speaker(self, speaker_model) -> None:
        """Run LLM inference to update a speaker's learned attributes."""
        if not speaker_model.recent_messages:
            return

        prompt = (
            f"Analyze these recent messages from '{speaker_model.username}' in channel {self.channel}. "
            f"Infer their role, area of interest, jargon patterns, goals, and SDAC category.\n\n"
            + "\n".join(f"- {m}" for m in speaker_model.recent_messages)
        )

        messages = [
            {"role": "system", "content": "You are analyzing a military chat speaker's behavior patterns."},
            {"role": "user", "content": prompt},
        ]

        try:
            raw = await self.backend.extract(messages, SpeakerInference)
            result: SpeakerInference = raw  # type: ignore[assignment]
            speaker_model.update_from_inference(
                role=result.role,
                area_of_interest=result.area_of_interest,
                jargon_notes=result.jargon_notes if result.jargon_notes else None,
                topics=result.topics if result.topics else None,
                goals=result.goals if result.goals else None,
                cues=result.cues if result.cues else None,
                expectancies=result.expectancies if result.expectancies else None,
                typical_actions=result.typical_actions if result.typical_actions else None,
                sdac_category=result.sdac_category,
            )
            logger.debug(
                "Updated speaker model for {}: role={}, area={}",
                speaker_model.username,
                speaker_model.role,
                speaker_model.area_of_interest,
            )
        except Exception as e:
            # Speaker inference is best-effort; failures don't block extraction
            logger.warning("Speaker inference failed for {}: {}", speaker_model.username, e)

    async def process_message(self, message: IRCMessage) -> list[CoPUpdate]:
        """Process a single message and return extracted CoPUpdates.

        The message is added to the conversation window, the speaker model
        is updated, then the full window is sent to the LLM for extraction.
        Returns an empty list if the message has no actionable content.
        """
        self._message_count += 1
        labels = {"channel": self.channel}
        metrics.inc("agent_messages_received_total", labels=labels)

        # Update speaker model
        if self.use_speaker_models:
            speaker = self._speakers.get_or_create(message.sender, message.timestamp)
            speaker.update_from_message(message)

            # Periodically run LLM inference on speaker profile
            if self._should_infer_speaker(speaker):
                await self._infer_speaker(speaker)

        # Add to window
        self._window.append(message)
        self._trim_window_by_time()

        # Pre-filter noise — don't waste an LLM call on acks, dots, URLs, STARTEX
        if _is_noise(message.content):
            metrics.inc("agent_messages_filtered_total", labels=labels)
            return []

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

        # Fill in source fields the LLM doesn't know / shouldn't control
        result.source_channel = self.channel
        result.source_speaker = message.sender
        result.source_message = message.content
        result.timestamp = message.timestamp
        result.extraction_method = "llm"  # Override — LLM should not set this
        result.context_messages = [f"{m.sender}: {m.content}" for m in list(self._window)[-5:]]

        # RAI provenance: attach model name and prompt hash if backend exposes them
        result.model_name = getattr(self.backend, "model", "")
        result.prompt_hash = getattr(self.backend, "prompt_hash", "")

        # Apply confidence calibration if a model is loaded
        if self.calibration_model is not None:
            raw = result.confidence
            result.confidence = self.calibration_model.calibrate(raw)
            logger.debug("Calibrated confidence: {:.2f} -> {:.2f}", raw, result.confidence)

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

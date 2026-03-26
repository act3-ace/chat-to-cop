"""OpenAI-compatible backend: works with Ollama, vLLM, LiteLLM, cloud APIs.

This single class covers every model we'd want to use. The only
configuration difference is the base_url and model name.

Uses `instructor` for structured output with Pydantic validation.
"""

from __future__ import annotations

import asyncio

import instructor
from openai import AsyncOpenAI
from pydantic import BaseModel

from chat_to_cop.metrics import metrics


class RetryableError(Exception):
    """Backend error that may succeed on retry (timeout, 429, 503)."""


class PermanentError(Exception):
    """Backend error that will not succeed on retry (400, schema)."""


class OpenAICompatibleBackend:
    """LLM backend using any OpenAI-compatible API endpoint.

    Works with Ollama, vLLM, LiteLLM, Ask Sage, cloud APIs — anything
    that serves the /v1/chat/completions endpoint.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:11434/v1",
        model: str = "qwen3:30b-a3b",
        api_key: str = "not-needed",
        timeout: float = 10.0,
        max_retries: int = 2,
    ) -> None:
        self.base_url = base_url
        self.model = model
        self.timeout = timeout

        self._client = instructor.from_openai(
            AsyncOpenAI(
                base_url=base_url,
                api_key=api_key,
                timeout=timeout,
            ),
            mode=instructor.Mode.JSON,
        )
        self._max_retries = max_retries

    async def extract(
        self,
        messages: list[dict[str, str]],
        schema: type[BaseModel],
    ) -> BaseModel:
        """Extract structured data from messages according to schema.

        Args:
            messages: OpenAI-format message list (role + content dicts).
            schema: Pydantic model class defining the expected output.

        Returns:
            An instance of `schema` populated with extracted data.

        Raises:
            RetryableError: On timeout, rate limit, or server error.
            PermanentError: On bad request or schema validation failure.
        """
        labels = {"backend": "openai_compat", "model": self.model}
        metrics.inc("backend_calls_total", labels=labels)

        try:
            async with metrics.async_timer("extraction_latency_seconds", labels=labels):
                result = await self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    response_model=schema,
                    max_retries=self._max_retries,
                )
            metrics.inc("backend_successes_total", labels=labels)
            return result

        except asyncio.TimeoutError as e:
            metrics.inc("backend_failures_total", labels={**labels, "reason": "timeout"})
            raise RetryableError(f"Timeout after {self.timeout}s") from e
        except Exception as e:
            err_str = str(e).lower()
            if any(code in err_str for code in ("429", "rate limit", "503", "502", "timeout")):
                metrics.inc("backend_failures_total", labels={**labels, "reason": "retryable"})
                raise RetryableError(str(e)) from e
            metrics.inc("backend_failures_total", labels={**labels, "reason": "permanent"})
            raise PermanentError(str(e)) from e

    def __repr__(self) -> str:
        return f"OpenAICompatibleBackend(url={self.base_url!r}, model={self.model!r})"


def build_system_prompt(
    glossary: str = "",
    world_state_summary: str = "",
    speaker_context: str = "",
) -> str:
    """Build the system prompt for world-state extraction.

    Args:
        glossary: Military abbreviations and exercise-specific terms.
        world_state_summary: Current believed battlespace state.
        speaker_context: Known speaker profiles for this channel.

    Returns:
        Complete system prompt string.
    """
    parts = [
        "You are a military chat message interpreter for a Common Operating Picture (CoP) database.",
        "Extract world-state changes from the messages and return structured JSON.",
        "If the message contains no actionable world-state information "
        "(acks like 'c' or 'copy', radio checks, chatter), set update_type to 'none'.",
        "Be concise in your reasoning. Focus on what changed in the battlespace.",
    ]

    if glossary:
        parts.append(f"\nGLOSSARY:\n{glossary}")

    if world_state_summary:
        parts.append(f"\nCURRENT WORLD STATE:\n{world_state_summary}")

    if speaker_context:
        parts.append(f"\nKNOWN SPEAKERS:\n{speaker_context}")

    return "\n\n".join(parts)


# Default glossary from DASH event analysis
DEFAULT_GLOSSARY = """- "gadget bent" = radar failure
- "buzzer on" = EW jamming active
- "splash" = target destroyed
- "FOX 1/2/3" = missile launch (semi-active/IR/active radar)
- "RTB" = return to base
- "angels XX" = altitude in thousands of feet
- "bullseye/cigar XXX/YYY" = bearing/range from reference point
- "F+XX" = fuel above frag in thousands of lbs
- "playtime XX Mike" = XX minutes of fuel remaining
- "c" or "copy" = acknowledgment (no state change)
- "NSTR" = nothing significant to report
- "TTG" = terminal threat group (enemy air defense)
- "TLAM" = Tomahawk land attack missile
- "SM6" = Standard Missile 6
- "BDA" = battle damage assessment
- "SITREP" = situation report
- "TACREP" = tactical report
- "CSAR" = combat search and rescue
- "BMA" = battle management area
- "AR" = aerial refueling
- "on boom" = currently refueling"""

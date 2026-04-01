"""Direct Anthropic API backend for Claude models.

Uses the Anthropic SDK directly (not Bedrock). For Claude Haiku and other
models available on the commercial API but not on GovCloud Bedrock.

Requires: pip install anthropic
Authentication: ANTHROPIC_API_KEY environment variable.
"""

from __future__ import annotations

import asyncio
import hashlib

import instructor
from pydantic import BaseModel

from chat_to_cop.backend.openai_compat import (
    DEFAULT_GLOSSARY,
    PermanentError,
    RetryableError,
    build_system_prompt,
)
from chat_to_cop.metrics import metrics

try:
    from anthropic import Anthropic

    HAS_ANTHROPIC = True
except ImportError:
    Anthropic = None  # type: ignore[assignment, misc]
    HAS_ANTHROPIC = False


class AnthropicDirectBackend:
    """LLM backend using Claude via the direct Anthropic API.

    Same interface as BedrockBackend but uses commercial API endpoint.
    """

    def __init__(
        self,
        model: str = "claude-haiku-4-5-20251001",
        timeout: float = 30.0,
        max_retries: int = 2,
        max_tokens: int = 4096,
    ) -> None:
        if not HAS_ANTHROPIC:
            raise ImportError("anthropic package required. Install with: pip install anthropic")

        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens
        self._prompt_hash = hashlib.sha256(build_system_prompt(glossary=DEFAULT_GLOSSARY).encode()).hexdigest()

        self._raw_client = Anthropic()
        # Use JSON mode instead of TOOLS mode to avoid tool_use/tool_result
        # retry corruption (Anthropic requires tool_result after every tool_use,
        # but instructor's retry mechanism doesn't include it).
        self._client = instructor.from_anthropic(self._raw_client, mode=instructor.Mode.ANTHROPIC_JSON)
        self._max_retries = max_retries

    @property
    def prompt_hash(self) -> str:
        return self._prompt_hash

    async def extract(
        self,
        messages: list[dict[str, str]],
        schema: type[BaseModel],
    ) -> BaseModel:
        """Extract structured data from messages according to schema."""
        labels = {"backend": "anthropic", "model": self.model}
        metrics.inc("backend_calls_total", labels=labels)

        system_prompt = ""
        chat_messages = []
        for msg in messages:
            if msg.get("role") == "system":
                system_prompt = msg.get("content", "")
            else:
                chat_messages.append(msg)

        try:
            async with metrics.async_timer("extraction_latency_seconds", labels=labels):
                result = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: self._client.messages.create(
                        model=self.model,
                        max_tokens=self.max_tokens,
                        system=system_prompt,
                        messages=chat_messages,
                        response_model=schema,
                        max_retries=self._max_retries,
                    ),
                )
            metrics.inc("backend_successes_total", labels=labels)
            return result

        except asyncio.TimeoutError as e:
            metrics.inc("backend_failures_total", labels={**labels, "reason": "timeout"})
            raise RetryableError(f"Timeout after {self.timeout}s") from e
        except Exception as e:
            err_str = str(e).lower()
            if any(code in err_str for code in ("429", "rate limit", "503", "502", "timeout", "throttl")):
                metrics.inc("backend_failures_total", labels={**labels, "reason": "retryable"})
                raise RetryableError(str(e)) from e
            metrics.inc("backend_failures_total", labels={**labels, "reason": "permanent"})
            raise PermanentError(str(e)) from e

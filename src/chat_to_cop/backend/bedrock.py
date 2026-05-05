"""AWS Bedrock backend for Claude models on GovCloud.

Uses the Anthropic SDK with Bedrock transport. Works on Analytics Gateway
where Claude Sonnet 4.5 is available via AWS Bedrock in us-gov-west-1.

Requires: pip install anthropic
Authentication: Uses AWS credentials from environment (IAM role, credentials file, or env vars).
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
    from anthropic import AnthropicBedrock

    HAS_ANTHROPIC = True
except ImportError:
    AnthropicBedrock = None  # type: ignore[assignment, misc]
    HAS_ANTHROPIC = False


class BedrockBackend:
    """LLM backend using Claude via AWS Bedrock (GovCloud).

    Uses the Anthropic SDK's Bedrock transport with instructor for
    structured output. Implements the same LLMBackend protocol as
    OpenAICompatibleBackend.

    The Anthropic Messages API uses a different format than OpenAI:
    - System message is a separate parameter, not in the messages list
    - Messages are role/content pairs (same as OpenAI)

    Instructor handles the structured output extraction identically.
    """

    def __init__(
        self,
        model: str = "us-gov.anthropic.claude-sonnet-4-5-20250929-v1:0",
        aws_region: str = "us-gov-west-1",
        timeout: float = 120.0,
        max_retries: int = 2,
        max_tokens: int = 4096,
        hard_timeout: float | None = None,
    ) -> None:
        if not HAS_ANTHROPIC:
            raise ImportError("anthropic package required for Bedrock backend. Install with: pip install anthropic")

        self.model = model
        self.aws_region = aws_region
        self.timeout = timeout
        self.max_tokens = max_tokens
        # Issue #75 — hard wall-clock cap on the full instructor retry chain.
        self._hard_timeout = hard_timeout
        self._prompt_hash = hashlib.sha256(build_system_prompt(glossary=DEFAULT_GLOSSARY).encode()).hexdigest()

        self._raw_client = AnthropicBedrock(aws_region=aws_region)
        # Use JSON mode to avoid tool_use/tool_result retry corruption
        self._client = instructor.from_anthropic(self._raw_client, mode=instructor.Mode.BEDROCK_JSON)
        self._max_retries = max_retries

    @property
    def prompt_hash(self) -> str:
        return self._prompt_hash

    async def extract(
        self,
        messages: list[dict[str, str]],
        schema: type[BaseModel],
    ) -> BaseModel:
        """Extract structured data from messages according to schema.

        Converts OpenAI-format messages to Anthropic format:
        - Extracts system message from the list (Anthropic takes it as separate param)
        - Passes remaining messages as-is
        """
        labels = {"backend": "bedrock", "model": self.model}
        metrics.inc("backend_calls_total", labels=labels)

        # Anthropic API takes system as a separate parameter
        system_prompt = ""
        chat_messages = []
        for msg in messages:
            if msg.get("role") == "system":
                system_prompt = msg.get("content", "")
            else:
                chat_messages.append(msg)

        try:
            async with metrics.async_timer("extraction_latency_seconds", labels=labels):
                # instructor.from_anthropic returns a sync client, so we run in executor
                future = asyncio.get_event_loop().run_in_executor(
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
                # Issue #75 — hard cap around the full instructor retry chain.
                if self._hard_timeout is not None:
                    result = await asyncio.wait_for(future, timeout=self._hard_timeout)
                else:
                    result = await future
            metrics.inc("backend_successes_total", labels=labels)
            return result

        except asyncio.TimeoutError as e:
            metrics.inc("backend_failures_total", labels={**labels, "reason": "timeout"})
            budget = self._hard_timeout if self._hard_timeout is not None else self.timeout
            raise RetryableError(f"Hard timeout after {budget}s (issue #75)") from e
        except Exception as e:
            err_str = str(e).lower()
            if any(code in err_str for code in ("429", "rate limit", "503", "502", "timeout", "throttl")):
                metrics.inc("backend_failures_total", labels={**labels, "reason": "retryable"})
                raise RetryableError(str(e)) from e
            metrics.inc("backend_failures_total", labels={**labels, "reason": "permanent"})
            raise PermanentError(str(e)) from e

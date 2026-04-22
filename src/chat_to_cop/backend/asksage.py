"""Ask Sage backend for Claude/GPT models on NIPRNet (IL5).

Uses the asksageclient Python package to access LLMs through the
Ask Sage API gateway at api.genai.army.mil. Available on NIPRNet
with CAC + VPN.

Requires: pip install asksageclient pip_system_certs requests
Authentication: API key + email from Ask Sage portal (CDAO-OSD link).

Ask Sage uses a custom API (not OpenAI-compatible), so this backend
wraps the asksageclient.query() method to produce Pydantic models
via manual JSON parsing (not instructor — Ask Sage doesn't support
the OpenAI tool/function calling that instructor relies on).

Usage:
    python -m chat_to_cop.replay data/dash3/23Sep_usaf_chat.zip \\
        --asksage --asksage-email your.email@mil \\
        --asksage-key YOUR_KEY --model claude-opus-4-6
"""

from __future__ import annotations

import asyncio
import hashlib
import json

from pydantic import BaseModel

from chat_to_cop.backend.openai_compat import (
    DEFAULT_GLOSSARY,
    PermanentError,
    RetryableError,
    build_system_prompt,
)
from chat_to_cop.metrics import metrics

try:
    from asksageclient import AskSageClient

    HAS_ASKSAGE = True
except ImportError:
    AskSageClient = None  # type: ignore[assignment, misc]
    HAS_ASKSAGE = False


class AskSageBackend:
    """LLM backend using Ask Sage API on NIPRNet.

    Ask Sage uses a custom query() API, not OpenAI chat completions.
    This backend converts our OpenAI-format messages to a single
    Ask Sage query with a system prompt, then parses the JSON response
    back into a Pydantic model.
    """

    def __init__(
        self,
        email: str,
        api_key: str,
        model: str = "claude-opus-4-6",
        user_base_url: str = "https://api.genai.army.mil/user/",
        server_base_url: str = "https://api.genai.army.mil/server/",
        temperature: float = 0.0,
        max_retries: int = 2,
        hard_timeout: float | None = None,
    ) -> None:
        if not HAS_ASKSAGE:
            raise ImportError(
                "asksageclient package required for Ask Sage backend. "
                "Install with: pip install asksageclient pip_system_certs requests"
            )

        self.model = model
        self.temperature = temperature
        self._max_retries = max_retries
        # Issue #75 — hard wall-clock cap on the full retry loop below.
        self._hard_timeout = hard_timeout
        self._prompt_hash = hashlib.sha256(build_system_prompt(glossary=DEFAULT_GLOSSARY).encode()).hexdigest()

        self._client = AskSageClient(
            email=email,
            api_key=api_key,
            user_base_url=user_base_url,
            server_base_url=server_base_url,
        )

    @property
    def prompt_hash(self) -> str:
        return self._prompt_hash

    async def extract(
        self,
        messages: list[dict[str, str]],
        schema: type[BaseModel],
    ) -> BaseModel:
        """Extract structured data via Ask Sage query().

        Converts OpenAI-format messages to Ask Sage's single-message
        query format with system_prompt parameter.
        """
        labels = {"backend": "asksage", "model": self.model}
        metrics.inc("backend_calls_total", labels=labels)

        # Extract system prompt and user message from OpenAI format
        system_prompt = ""
        user_message = ""
        for msg in messages:
            if msg.get("role") == "system":
                system_prompt = msg.get("content", "")
            elif msg.get("role") == "user":
                user_message = msg.get("content", "")

        # Add JSON schema instruction to the user message
        schema_json = schema.model_json_schema()
        user_message += (
            "\n\nRespond with ONLY valid JSON matching this schema. "
            "No markdown, no explanation, just the JSON object:\n"
            f"{json.dumps(schema_json, indent=2)}"
        )

        async def _run_retry_loop() -> BaseModel:
            last_error: Exception | None = None
            for attempt in range(self._max_retries + 1):
                try:
                    async with metrics.async_timer("extraction_latency_seconds", labels=labels):
                        # Ask Sage client is synchronous — run in executor
                        response = await asyncio.get_event_loop().run_in_executor(
                            None,
                            lambda: self._client.query(
                                message=user_message,
                                persona="default",
                                dataset="none",
                                limit_references=0,
                                temperature=self.temperature,
                                live=0,
                                model=self.model,
                                system_prompt=system_prompt,
                            ),
                        )

                    response_text = response.get("message", "")
                    if not response_text:
                        raise PermanentError(f"Empty response from Ask Sage: {response}")

                    text = response_text.strip()
                    if text.startswith("```json"):
                        text = text[7:]
                    if text.startswith("```"):
                        text = text[3:]
                    if text.endswith("```"):
                        text = text[:-3]
                    text = text.strip()

                    result = schema.model_validate_json(text)
                    metrics.inc("backend_successes_total", labels=labels)
                    return result

                except Exception as e:
                    last_error = e
                    err_str = str(e).lower()
                    if any(code in err_str for code in ("429", "rate limit", "throttl", "timeout")):
                        if attempt < self._max_retries:
                            continue
                        metrics.inc("backend_failures_total", labels={**labels, "reason": "retryable"})
                        raise RetryableError(str(e)) from e

                    if attempt < self._max_retries:
                        continue

            metrics.inc("backend_failures_total", labels={**labels, "reason": "permanent"})
            raise PermanentError(str(last_error)) from last_error

        # Issue #75 — hard wall-clock cap around the full retry loop.
        try:
            if self._hard_timeout is not None:
                return await asyncio.wait_for(_run_retry_loop(), timeout=self._hard_timeout)
            return await _run_retry_loop()
        except asyncio.TimeoutError as e:
            metrics.inc("backend_failures_total", labels={**labels, "reason": "timeout"})
            raise RetryableError(f"Hard timeout after {self._hard_timeout}s (issue #75)") from e

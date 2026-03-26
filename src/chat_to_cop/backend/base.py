"""Base protocol for LLM backends.

All backends implement the same interface. Agent code never knows
which model or method is producing the extraction — it just calls
extract() and gets a Pydantic model back.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel


@runtime_checkable
class LLMBackend(Protocol):
    """Protocol that all extraction backends must implement."""

    async def extract(
        self,
        messages: list[dict],
        schema: type[BaseModel],
    ) -> BaseModel:
        """Extract structured data from messages according to schema.

        Args:
            messages: OpenAI-format message list (role + content dicts).
            schema: Pydantic model class defining the expected output.

        Returns:
            An instance of `schema` populated with extracted data.
        """
        ...

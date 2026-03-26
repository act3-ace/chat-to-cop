"""OpenAI-compatible backend: works with Ollama, vLLM, LiteLLM, cloud APIs.

This single class covers every model we'd want to use. The only
configuration difference is the base_url and model name.

Uses `instructor` for structured output with Pydantic validation.
"""

from __future__ import annotations


# TODO: Implement OpenAICompatibleBackend
# - __init__(base_url, model, api_key=None, timeout=5.0)
# - async extract(messages, schema) -> BaseModel
# - Uses instructor.from_openai() for structured output
# - Timeout handling
# - Error classification (retryable vs permanent)

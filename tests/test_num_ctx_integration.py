"""Integration tests for Ollama num_ctx configuration.

Requires a running Ollama instance. Run with:
    pytest tests/test_num_ctx_integration.py -m integration -v

These tests verify that the num_ctx parameter is actually honored by Ollama,
both via the extra_body passthrough and via Modelfile configuration.
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

from chat_to_cop.backend.openai_compat import (
    DEFAULT_GLOSSARY,
    OpenAICompatibleBackend,
    build_system_prompt,
)

# Base model to use for tests -- must be already pulled in Ollama
BASE_MODEL = "qwen2.5:3b"
CUSTOM_MODEL_NAME = "test-num-ctx-8k"
OLLAMA_URL = "http://127.0.0.1:11434/v1"


class SimpleExtraction(BaseModel):
    """Minimal extraction model for testing."""

    update_type: str = "none"
    confidence: float = 0.0
    summary: str = ""


def ollama_available() -> bool:
    """Check if Ollama is running and the base model is available."""
    try:
        import httpx

        r = httpx.get("http://127.0.0.1:11434/api/tags", timeout=5)
        if r.status_code != 200:
            return False
        models = [m["name"] for m in r.json().get("models", [])]
        # Accept both "qwen2.5:3b" and "qwen2.5:3b-instruct-..." variants
        return any(BASE_MODEL in m for m in models)
    except Exception:
        return False


def create_custom_model(model_name: str, base: str, num_ctx: int) -> bool:
    """Create an Ollama model with a custom Modelfile setting num_ctx."""
    try:
        import httpx

        modelfile = f"FROM {base}\nPARAMETER num_ctx {num_ctx}\n"
        r = httpx.post(
            "http://127.0.0.1:11434/api/create",
            json={"name": model_name, "modelfile": modelfile},
            timeout=120,
        )
        return r.status_code == 200
    except Exception:
        return False


def delete_custom_model(model_name: str) -> None:
    """Delete a custom Ollama model (best effort)."""
    try:
        import httpx

        httpx.delete(
            "http://127.0.0.1:11434/api/delete",
            json={"name": model_name},
            timeout=30,
        )
    except Exception:
        pass


@pytest.mark.integration
class TestNumCtxExtraBody:
    """Verify that extra_body num_ctx is accepted by Ollama without error.

    This tests the instructor -> OpenAI SDK -> Ollama path. We cannot directly
    observe what context window Ollama is using, but we can verify that:
    1. The request does not error out (Ollama accepts the options.num_ctx field)
    2. A long-context prompt completes successfully
    """

    def test_extra_body_num_ctx_accepted(self):
        """Ollama should accept extra_body options.num_ctx without error."""
        if not ollama_available():
            pytest.skip("Ollama not available or base model not pulled")

        backend = OpenAICompatibleBackend(
            base_url=OLLAMA_URL,
            model=BASE_MODEL,
            timeout=60.0,
            num_ctx=8192,
        )

        messages = [
            {"role": "system", "content": build_system_prompt(glossary=DEFAULT_GLOSSARY)},
            {"role": "user", "content": "Hydro_Tank: RR15 F+40"},
        ]

        result = asyncio.run(backend.extract(messages=messages, schema=SimpleExtraction))
        # If we get here without error, extra_body was accepted
        assert result is not None
        assert isinstance(result, SimpleExtraction)

    def test_long_context_prompt_completes(self):
        """A prompt exceeding default 2048 tokens should complete with num_ctx=8192.

        We build a prompt with ~3000 tokens of context. If num_ctx were stuck at
        a low default, the model would truncate input and likely fail or produce
        garbage. With 8192, it should handle this fine.
        """
        if not ollama_available():
            pytest.skip("Ollama not available or base model not pulled")

        # Build a long context: system prompt (~2000 tokens) + many messages
        system_prompt = build_system_prompt(glossary=DEFAULT_GLOSSARY)
        # Add extra context to push token count higher
        filler_messages = []
        for i in range(20):
            filler_messages.append(
                {
                    "role": "user",
                    "content": (
                        f"HYDRO_SL: SITREP / AIR: ZEUS{i:02d} engaged TTG at cigar "
                        f"{300 + i}/{400 + i}, RTB with gadget bent, fuel state F+{10 + i}"
                    ),
                }
            )
            filler_messages.append(
                {
                    "role": "assistant",
                    "content": '{"update_type": "status_change", "confidence": 0.8, "summary": "acknowledged"}',
                }
            )

        messages = [
            {"role": "system", "content": system_prompt},
            *filler_messages,
            {"role": "user", "content": "HYDRO_SL: ZEUS99 splash, confirmed kill by AMRAAM"},
        ]

        backend = OpenAICompatibleBackend(
            base_url=OLLAMA_URL,
            model=BASE_MODEL,
            timeout=120.0,
            num_ctx=8192,
        )

        result = asyncio.run(backend.extract(messages=messages, schema=SimpleExtraction))
        assert result is not None
        assert isinstance(result, SimpleExtraction)
        # The model should recognize this as a status change or kill
        # (we don't enforce exact output -- just that it completed)


@pytest.mark.integration
class TestNumCtxModelfile:
    """Verify the Modelfile approach for baking num_ctx into the model.

    This is the recommended approach for production Ollama deployments:
    create a derived model with PARAMETER num_ctx baked in, so every
    request automatically uses the right context window.
    """

    @pytest.fixture(autouse=True)
    def setup_and_teardown(self):
        """Create custom model before tests, delete after."""
        if not ollama_available():
            pytest.skip("Ollama not available or base model not pulled")

        created = create_custom_model(CUSTOM_MODEL_NAME, BASE_MODEL, num_ctx=8192)
        if not created:
            pytest.skip(f"Could not create custom model {CUSTOM_MODEL_NAME}")

        yield

        delete_custom_model(CUSTOM_MODEL_NAME)

    def test_modelfile_model_responds(self):
        """A model created with PARAMETER num_ctx should respond normally."""
        backend = OpenAICompatibleBackend(
            base_url=OLLAMA_URL,
            model=CUSTOM_MODEL_NAME,
            timeout=60.0,
            num_ctx=0,  # Don't use extra_body -- rely on Modelfile
        )

        messages = [
            {"role": "system", "content": build_system_prompt(glossary=DEFAULT_GLOSSARY)},
            {"role": "user", "content": "AOC_SIDO: tacrep, 4th-gen sam active, cigar 316/398, jtn TM677"},
        ]

        result = asyncio.run(backend.extract(messages=messages, schema=SimpleExtraction))
        assert result is not None
        assert isinstance(result, SimpleExtraction)

    def test_modelfile_long_context(self):
        """Modelfile model should handle long context without truncation."""
        system_prompt = build_system_prompt(glossary=DEFAULT_GLOSSARY)
        filler_messages = []
        for i in range(20):
            filler_messages.append(
                {
                    "role": "user",
                    "content": (
                        f"WF_COORD: TN{40000 + i} classified as DDG, bearing {i * 10:03d}, "
                        f"range {100 + i}, AEGIS active, SCL: SM6x32 TLAM x12"
                    ),
                }
            )
            filler_messages.append(
                {
                    "role": "assistant",
                    "content": '{"update_type": "entity_id", "confidence": 0.85, "summary": "acknowledged"}',
                }
            )

        messages = [
            {"role": "system", "content": system_prompt},
            *filler_messages,
            {"role": "user", "content": "WF_COORD: TN44599 re-classified, NOT DDG, reassessing as CG"},
        ]

        backend = OpenAICompatibleBackend(
            base_url=OLLAMA_URL,
            model=CUSTOM_MODEL_NAME,
            timeout=120.0,
            num_ctx=0,  # Rely on Modelfile
        )

        result = asyncio.run(backend.extract(messages=messages, schema=SimpleExtraction))
        assert result is not None
        assert isinstance(result, SimpleExtraction)

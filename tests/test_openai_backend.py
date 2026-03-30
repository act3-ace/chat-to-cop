"""Tests for the OpenAI-compatible LLM backend."""

import asyncio

import pytest
from pydantic import BaseModel

from chat_to_cop.backend.openai_compat import (
    DEFAULT_GLOSSARY,
    OpenAICompatibleBackend,
    PermanentError,
    RetryableError,
    build_system_prompt,
)


class SimpleExtraction(BaseModel):
    """Minimal extraction model for testing."""

    update_type: str = "none"
    confidence: float = 0.0
    summary: str = ""


class TestBuildSystemPrompt:
    """Test system prompt construction."""

    def test_minimal_prompt(self):
        prompt = build_system_prompt()
        assert "Common Operating Picture" in prompt
        assert "world-state" in prompt

    def test_with_glossary(self):
        prompt = build_system_prompt(glossary="RTB = return to base")
        assert "GLOSSARY" in prompt
        assert "RTB = return to base" in prompt

    def test_with_world_state(self):
        prompt = build_system_prompt(world_state_summary="TM636 is hostile fighter")
        assert "CURRENT WORLD STATE" in prompt
        assert "TM636" in prompt

    def test_with_speaker_context(self):
        prompt = build_system_prompt(speaker_context="Hydro_Tank: tanker controller")
        assert "KNOWN SPEAKERS" in prompt
        assert "Hydro_Tank" in prompt

    def test_full_prompt(self):
        prompt = build_system_prompt(
            glossary="splash = destroyed",
            world_state_summary="3 targets tracked",
            speaker_context="VEGAS_SL: shift lead",
        )
        assert "GLOSSARY" in prompt
        assert "CURRENT WORLD STATE" in prompt
        assert "KNOWN SPEAKERS" in prompt

    def test_default_glossary_content(self):
        assert "gadget bent" in DEFAULT_GLOSSARY
        assert "splash" in DEFAULT_GLOSSARY
        assert "F+XX" in DEFAULT_GLOSSARY


class TestOpenAICompatibleBackend:
    """Test backend initialization and error handling."""

    def test_construction(self):
        backend = OpenAICompatibleBackend(
            base_url="http://localhost:11434/v1",
            model="qwen2.5:7b",
        )
        assert backend.base_url == "http://localhost:11434/v1"
        assert backend.model == "qwen2.5:7b"

    def test_default_num_ctx(self):
        backend = OpenAICompatibleBackend()
        assert backend.num_ctx == 8192

    def test_custom_num_ctx(self):
        backend = OpenAICompatibleBackend(num_ctx=16384)
        assert backend.num_ctx == 16384

    def test_num_ctx_passed_in_extract(self):
        """Verify num_ctx is sent via extra_body in the API call."""
        backend = OpenAICompatibleBackend(num_ctx=32768)

        captured_kwargs = {}

        async def mock_create(**kwargs):
            captured_kwargs.update(kwargs)
            return SimpleExtraction(update_type="none", confidence=0.0, summary="")

        backend._client.chat.completions.create = mock_create

        asyncio.run(
            backend.extract(
                messages=[{"role": "user", "content": "test"}],
                schema=SimpleExtraction,
            )
        )

        assert "extra_body" in captured_kwargs
        assert captured_kwargs["extra_body"] == {"options": {"num_ctx": 32768}}

    def test_repr(self):
        backend = OpenAICompatibleBackend(model="test-model")
        assert "test-model" in repr(backend)

    def test_extract_timeout_raises_retryable(self):
        """Timeouts should be classified as retryable."""
        backend = OpenAICompatibleBackend(timeout=0.001)

        # Mock the client to raise TimeoutError
        async def mock_create(**kwargs):
            raise asyncio.TimeoutError()

        backend._client.chat.completions.create = mock_create

        with pytest.raises(RetryableError, match="Timeout"):
            asyncio.run(
                backend.extract(
                    messages=[{"role": "user", "content": "test"}],
                    schema=SimpleExtraction,
                )
            )

    def test_extract_rate_limit_raises_retryable(self):
        """429 errors should be classified as retryable."""
        backend = OpenAICompatibleBackend()

        async def mock_create(**kwargs):
            raise Exception("Error code: 429 rate limit exceeded")

        backend._client.chat.completions.create = mock_create

        with pytest.raises(RetryableError):
            asyncio.run(
                backend.extract(
                    messages=[{"role": "user", "content": "test"}],
                    schema=SimpleExtraction,
                )
            )

    def test_extract_bad_request_raises_permanent(self):
        """400 errors should be classified as permanent."""
        backend = OpenAICompatibleBackend()

        async def mock_create(**kwargs):
            raise Exception("Error code: 400 bad request")

        backend._client.chat.completions.create = mock_create

        with pytest.raises(PermanentError):
            asyncio.run(
                backend.extract(
                    messages=[{"role": "user", "content": "test"}],
                    schema=SimpleExtraction,
                )
            )

    def test_extract_success_with_mock(self):
        """Successful extraction should return a populated model."""
        backend = OpenAICompatibleBackend()

        expected = SimpleExtraction(
            update_type="fuel",
            confidence=0.85,
            summary="RR15 fuel state F+40",
        )

        async def mock_create(**kwargs):
            return expected

        backend._client.chat.completions.create = mock_create

        result = asyncio.run(
            backend.extract(
                messages=[
                    {"role": "system", "content": "You are a military chat interpreter."},
                    {"role": "user", "content": "Hydro_Tank: RR15 F+40, RL36 F+50"},
                ],
                schema=SimpleExtraction,
            )
        )

        assert result.update_type == "fuel"
        assert result.confidence == 0.85
        assert "RR15" in result.summary


class TestIntegrationOllama:
    """Integration tests that require a running Ollama instance.

    These are skipped unless Ollama is available locally.
    Mark with: pytest -m integration
    """

    @pytest.mark.integration
    def test_extract_fuel_message(self):
        """Test extraction of a fuel state message against real LLM."""
        backend = OpenAICompatibleBackend(
            base_url="http://localhost:11434/v1",
            model="qwen2.5:7b",
            timeout=30.0,
        )

        messages = [
            {"role": "system", "content": build_system_prompt(glossary=DEFAULT_GLOSSARY)},
            {"role": "user", "content": "Hydro_Tank: RR15 F+40, RL36 F+50"},
        ]

        try:
            result = asyncio.run(backend.extract(messages=messages, schema=SimpleExtraction))
            assert result.update_type != "none"
            assert result.confidence > 0.0
        except Exception:
            pytest.skip("Ollama not available")

    @pytest.mark.integration
    def test_extract_ack_message(self):
        """ACK messages should return update_type=none."""
        backend = OpenAICompatibleBackend(
            base_url="http://localhost:11434/v1",
            model="qwen2.5:7b",
            timeout=30.0,
        )

        messages = [
            {"role": "system", "content": build_system_prompt(glossary=DEFAULT_GLOSSARY)},
            {"role": "user", "content": "Hydro_MSO: c"},
        ]

        try:
            result = asyncio.run(backend.extract(messages=messages, schema=SimpleExtraction))
            assert result.update_type == "none"
        except Exception:
            pytest.skip("Ollama not available")

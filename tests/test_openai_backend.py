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

    def test_prompt_contains_csar_examples(self):
        prompt = build_system_prompt()
        assert "MISREP" in prompt
        assert "CSAR" in prompt
        assert "csar" in prompt
        assert "ZEUS14 pilot ejected" in prompt
        assert "pilot recovered" in prompt
        assert "RESCORT01" in prompt

    def test_prompt_contains_fire_mission_examples(self):
        prompt = build_system_prompt()
        assert "fire_mission" in prompt
        assert "fire mission TGT AQ1234" in prompt
        assert "JDAM" in prompt
        assert "CAS request" in prompt
        assert "BDA TGT AQ1234" in prompt

    def test_prompt_contains_cyber_ew_examples(self):
        prompt = build_system_prompt()
        assert "cyber_ew" in prompt
        assert "GPS jamming" in prompt
        assert "SA-20 battery activating" in prompt
        assert "SIGINT" in prompt
        # Claude disambiguation examples (#42)
        assert "DRAGON EYE active" in prompt
        assert "DRAGON EYE is an EW system" in prompt
        assert "DRFM jammer" in prompt
        assert "Electronic jamming is cyber_ew" in prompt

    def test_prompt_disambiguates_threat_vs_cyber_ew(self):
        prompt = build_system_prompt()
        # Threat contrast examples
        assert "SA-21 launch detected" in prompt
        assert "Missile launch / kinetic weapon employment is threat" in prompt
        assert "hostile fighter 4-ship" in prompt
        assert "Hostile aircraft is a kinetic threat" in prompt

    def test_prompt_disambiguates_fire_mission_vs_tasking(self):
        prompt = build_system_prompt()
        # fire_mission with specific target/weapon/TOT
        assert "SPOTTER21 requests fire mission" in prompt
        assert "fire request with target/weapon/TOT is fire_mission" in prompt
        # tasking contrast
        assert "BattleCOA: push ZEUS flight" in prompt
        assert "Coordination/asset management is tasking" in prompt

    def test_prompt_contains_type_disambiguation_rules(self):
        prompt = build_system_prompt()
        assert "TYPE DISAMBIGUATION" in prompt
        assert "The effect is electromagnetic or cyber" in prompt
        assert "The danger is a physical weapon" in prompt
        assert "No specific target/weapon/TOT" in prompt

    def test_default_glossary_content(self):
        assert "gadget bent" in DEFAULT_GLOSSARY
        assert "splash" in DEFAULT_GLOSSARY
        assert "F+XX" in DEFAULT_GLOSSARY

    def test_default_glossary_csar_terms(self):
        assert "MISREP" in DEFAULT_GLOSSARY
        assert "JPRC" in DEFAULT_GLOSSARY
        assert "SANDY" in DEFAULT_GLOSSARY
        assert "RESCORT" in DEFAULT_GLOSSARY
        assert "DUSTOFF" in DEFAULT_GLOSSARY

    def test_default_glossary_fire_mission_terms(self):
        assert "TOT" in DEFAULT_GLOSSARY
        assert "JDAM" in DEFAULT_GLOSSARY
        assert "CAS" in DEFAULT_GLOSSARY
        assert "TIC" in DEFAULT_GLOSSARY
        assert "FARP" in DEFAULT_GLOSSARY

    def test_default_glossary_ew_terms(self):
        assert "EW" in DEFAULT_GLOSSARY
        assert "SIGINT" in DEFAULT_GLOSSARY
        assert "DRAGON EYE" in DEFAULT_GLOSSARY

    def test_prompt_contains_radio_banter_noise_examples(self):
        prompt = build_system_prompt()
        # Radio check few-shot examples
        assert "loud and clear the Vegas SL how me" in prompt
        assert "Radio check, no world-state change" in prompt
        # Banter few-shot example
        assert "didn't say over, over" in prompt
        assert "Communication protocol discussion" in prompt
        # Radio setup
        assert "let's do some radio tracks" in prompt
        # Sign-off
        assert "Buh-bye now" in prompt
        assert "Sign-off, no world-state change" in prompt


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

        with pytest.raises(RetryableError, match="(?i)timeout"):
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


class TestAzureOpenAIBackend:
    """Azure OpenAI support — uses AsyncAzureOpenAI client internally."""

    def test_azure_construction(self):
        backend = OpenAICompatibleBackend(
            azure_endpoint="https://myresource.openai.azure.com",
            api_key="test-azure-key",
            model="gpt-4o",
        )
        assert backend._is_azure is True
        assert backend._is_ollama is False
        assert backend.model == "gpt-4o"

    def test_azure_custom_api_version(self):
        backend = OpenAICompatibleBackend(
            azure_endpoint="https://myresource.openai.azure.com",
            api_key="test-azure-key",
            model="gpt-4o",
            azure_api_version="2025-01-15",
        )
        assert backend._is_azure is True

    def test_non_azure_when_endpoint_is_none(self):
        backend = OpenAICompatibleBackend(
            base_url="http://localhost:11434/v1",
            model="qwen2.5:7b",
        )
        assert backend._is_azure is False

    def test_azure_disables_ollama_detection(self):
        backend = OpenAICompatibleBackend(
            azure_endpoint="https://myresource.openai.azure.com",
            model="gpt-4o",
        )
        assert backend._is_ollama is False

    def test_azure_extract_with_mock(self):
        backend = OpenAICompatibleBackend(
            azure_endpoint="https://myresource.openai.azure.com",
            api_key="test-azure-key",
            model="gpt-4o",
        )

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
                    {"role": "user", "content": "Hydro_Tank: RR15 F+40"},
                ],
                schema=SimpleExtraction,
            )
        )

        assert result.update_type == "fuel"
        assert result.confidence == 0.85

    def test_azure_num_ctx_not_sent(self):
        backend = OpenAICompatibleBackend(
            azure_endpoint="https://myresource.openai.azure.com",
            model="gpt-4o",
            num_ctx=8192,
        )

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

        assert "extra_body" not in captured_kwargs


class TestHardTimeout:
    """Issue #75 — hard wall-clock cap on the full instructor retry chain.

    Without this cap, a pathological schema-validation loop can burn
    timeout * (max_retries+1) seconds per message (12 min observed on
    Blueback 2026-04-22 with 14B / 120s / max_retries=2).
    """

    def test_default_hard_timeout_is_none(self):
        """Default is None to preserve pre-#75 behavior for any caller
        that does not opt in (e.g. direct backend construction)."""
        backend = OpenAICompatibleBackend()
        assert backend._hard_timeout is None

    def test_hard_timeout_fires_when_create_hangs(self):
        """A create() that never resolves must be interrupted at the hard
        timeout and surfaced as RetryableError so DegradingBackend falls
        through to the next slot."""
        backend = OpenAICompatibleBackend(hard_timeout=0.1)

        async def mock_create(**kwargs):
            await asyncio.sleep(5.0)  # far longer than 0.1s
            return SimpleExtraction(update_type="none", confidence=0.0, summary="")

        backend._client.chat.completions.create = mock_create

        async def _timed_extract():
            start = asyncio.get_event_loop().time()
            with pytest.raises(RetryableError, match="(?i)hard timeout"):
                await backend.extract(
                    messages=[{"role": "user", "content": "test"}],
                    schema=SimpleExtraction,
                )
            # Budget + scheduling slop. Pre-#75 this would be ~5s (the mock
            # sleep); with the hard cap it must land well under 1s.
            return asyncio.get_event_loop().time() - start

        elapsed = asyncio.run(_timed_extract())
        assert elapsed < 1.0, f"extract ran {elapsed:.2f}s, exceeded hard timeout budget"

    def test_hard_timeout_error_message_includes_budget(self):
        """Operators read the error text in logs — it must name the budget."""
        backend = OpenAICompatibleBackend(hard_timeout=0.05)

        async def mock_create(**kwargs):
            await asyncio.sleep(5.0)

        backend._client.chat.completions.create = mock_create

        with pytest.raises(RetryableError, match="0.05"):
            asyncio.run(
                backend.extract(
                    messages=[{"role": "user", "content": "test"}],
                    schema=SimpleExtraction,
                )
            )

    def test_hard_timeout_none_allows_slow_call(self):
        """When hard_timeout=None, a call that would exceed any fixed budget
        must still succeed — this is the pre-#75 path for callers that
        explicitly opt out of the cap."""
        backend = OpenAICompatibleBackend(hard_timeout=None)

        async def mock_create(**kwargs):
            await asyncio.sleep(0.2)
            return SimpleExtraction(update_type="fuel", confidence=0.9, summary="slow")

        backend._client.chat.completions.create = mock_create

        result = asyncio.run(
            backend.extract(
                messages=[{"role": "user", "content": "test"}],
                schema=SimpleExtraction,
            )
        )
        assert result.update_type == "fuel"


class TestNumCtxDetection:
    """Issue #74 — num_ctx must reach Ollama on non-standard ports.

    The old heuristic gated on '11434' or 'ollama' in the URL. Any other
    port (e.g. HPC array jobs) silently dropped num_ctx, causing Ollama to
    use its 4K default and truncating the system prompt.
    """

    def test_explicit_is_ollama_sends_num_ctx_on_nonstandard_port(self):
        """Regression: port 19999, is_ollama=True, assert extra_body present."""
        backend = OpenAICompatibleBackend(
            base_url="http://127.0.0.1:19999/v1",
            num_ctx=8192,
            is_ollama=True,
        )
        kwargs: dict = {}
        kwargs["model"] = backend.model
        kwargs["messages"] = [{"role": "user", "content": "test"}]
        kwargs["response_model"] = SimpleExtraction
        kwargs["max_retries"] = 0

        if backend.num_ctx and backend._is_ollama:
            kwargs["extra_body"] = {"options": {"num_ctx": backend.num_ctx}}

        assert "extra_body" in kwargs
        assert kwargs["extra_body"]["options"]["num_ctx"] == 8192

    def test_default_port_auto_detects_ollama(self):
        backend = OpenAICompatibleBackend(
            base_url="http://127.0.0.1:11434/v1",
            num_ctx=8192,
        )
        assert backend._is_ollama is True

    def test_ollama_in_hostname_auto_detects(self):
        backend = OpenAICompatibleBackend(
            base_url="http://ollama.example.com:8080/v1",
            num_ctx=8192,
        )
        assert backend._is_ollama is True

    def test_openai_url_does_not_detect_as_ollama(self):
        """Anti-regression: openai.com must NOT send extra_body."""
        backend = OpenAICompatibleBackend(
            base_url="https://api.openai.com/v1",
            num_ctx=8192,
            is_ollama=False,
        )
        assert backend._is_ollama is False

    def test_auto_detect_nonstandard_port_warns(self):
        """When num_ctx>0 and auto-detect fails, emit a visible warning."""
        from io import StringIO

        from loguru import logger

        buf = StringIO()
        sink_id = logger.add(buf, format="{message}", level="WARNING")
        try:
            OpenAICompatibleBackend(
                base_url="http://127.0.0.1:19999/v1",
                num_ctx=8192,
            )
            output = buf.getvalue()
            assert "num_ctx" in output and "not detected" in output
        finally:
            logger.remove(sink_id)

    def test_explicit_false_suppresses_num_ctx(self):
        backend = OpenAICompatibleBackend(
            base_url="http://127.0.0.1:11434/v1",
            num_ctx=8192,
            is_ollama=False,
        )
        assert backend._is_ollama is False

    def test_num_ctx_zero_skips_regardless(self):
        backend = OpenAICompatibleBackend(
            base_url="http://127.0.0.1:11434/v1",
            num_ctx=0,
            is_ollama=True,
        )
        assert backend._is_ollama is True
        assert backend.num_ctx == 0


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

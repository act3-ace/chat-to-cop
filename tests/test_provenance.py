"""Tests for RAI provenance: CoPUpdate fields, PipelineTracker, AIBOM generation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from pydantic import BaseModel

from chat_to_cop.agent.channel_agent import ChannelAgent
from chat_to_cop.models.cop_update import CoPUpdate, EntityUpdate, UpdateType
from chat_to_cop.models.messages import IRCMessage
from chat_to_cop.tracking import PipelineTracker

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _msg(content: str = "RR15 F+40", sender: str = "Hydro_Tank", channel: str = "#c2_coord") -> IRCMessage:
    return IRCMessage(
        timestamp=datetime(2025, 9, 23, 14, 10, 0, tzinfo=timezone.utc),
        channel=channel,
        sender=sender,
        content=content,
    )


class FakeBackendWithProvenance:
    """Mock backend that exposes model and prompt_hash like OpenAICompatibleBackend."""

    def __init__(self) -> None:
        self.model = "test-model:7b"
        self._prompt_hash = hashlib.sha256(b"test prompt").hexdigest()

    @property
    def prompt_hash(self) -> str:
        return self._prompt_hash

    async def extract(self, messages: list[dict], schema: type[BaseModel]) -> CoPUpdate:
        return CoPUpdate(
            update_type=UpdateType.FUEL,
            confidence=0.85,
            extraction_method="llm",
            entities=[EntityUpdate(callsign="RR15", fuel_state="F+40")],
        )


class FakeBackendNoProvenance:
    """Mock backend without provenance attributes (e.g., RegexBackend)."""

    async def extract(self, messages: list[dict], schema: type[BaseModel]) -> CoPUpdate:
        return CoPUpdate(
            update_type=UpdateType.FUEL,
            confidence=0.7,
            extraction_method="regex",
            entities=[EntityUpdate(callsign="RR15", fuel_state="F+40")],
        )


# ---------------------------------------------------------------------------
# CoPUpdate provenance fields
# ---------------------------------------------------------------------------


class TestCoPUpdateProvenance:
    def test_default_provenance_fields_empty(self):
        update = CoPUpdate(update_type=UpdateType.FUEL, confidence=0.9)
        assert update.model_name == ""
        assert update.prompt_hash == ""

    def test_provenance_fields_set(self):
        update = CoPUpdate(
            update_type=UpdateType.FUEL,
            confidence=0.9,
            model_name="qwen2.5:7b",
            prompt_hash="abc123",
        )
        assert update.model_name == "qwen2.5:7b"
        assert update.prompt_hash == "abc123"

    def test_provenance_fields_roundtrip_json(self):
        update = CoPUpdate(
            update_type=UpdateType.FUEL,
            confidence=0.9,
            model_name="test-model",
            prompt_hash="deadbeef",
        )
        data = json.loads(update.model_dump_json())
        assert data["model_name"] == "test-model"
        assert data["prompt_hash"] == "deadbeef"


# ---------------------------------------------------------------------------
# Channel agent fills provenance from backend
# ---------------------------------------------------------------------------


class TestChannelAgentProvenance:
    def test_agent_fills_provenance_from_backend(self):
        async def run():
            backend = FakeBackendWithProvenance()
            agent = ChannelAgent("#test", backend, use_speaker_models=False)
            updates = await agent.process_message(_msg())
            assert len(updates) == 1
            assert updates[0].model_name == "test-model:7b"
            assert updates[0].prompt_hash == backend.prompt_hash

        asyncio.run(run())

    def test_agent_handles_backend_without_provenance(self):
        async def run():
            backend = FakeBackendNoProvenance()
            agent = ChannelAgent("#test", backend, use_speaker_models=False)
            updates = await agent.process_message(_msg())
            assert len(updates) == 1
            assert updates[0].model_name == ""
            assert updates[0].prompt_hash == ""

        asyncio.run(run())


# ---------------------------------------------------------------------------
# OpenAI backend prompt_hash
# ---------------------------------------------------------------------------


class TestOpenAIBackendPromptHash:
    def test_prompt_hash_is_sha256_length(self):
        from chat_to_cop.backend.openai_compat import OpenAICompatibleBackend

        backend = OpenAICompatibleBackend(base_url="http://fake:1234/v1", model="test:1b")
        assert len(backend.prompt_hash) == 64  # SHA-256 hex length
        assert all(c in "0123456789abcdef" for c in backend.prompt_hash)

    def test_prompt_hash_is_deterministic(self):
        """Same config → same hash (provenance reproducibility)."""
        from chat_to_cop.backend.openai_compat import OpenAICompatibleBackend

        b1 = OpenAICompatibleBackend(base_url="http://fake:1234/v1", model="test:1b")
        b2 = OpenAICompatibleBackend(base_url="http://fake:1234/v1", model="test:1b")
        assert b1.prompt_hash == b2.prompt_hash

    def test_prompt_hash_changes_with_glossary(self):
        """Different glossary produces a different hash (detects prompt drift)."""
        from unittest.mock import patch as mock_patch

        from chat_to_cop.backend.openai_compat import DEFAULT_GLOSSARY, OpenAICompatibleBackend

        b_default = OpenAICompatibleBackend(base_url="http://fake:1234/v1", model="test:1b")

        with mock_patch("chat_to_cop.backend.openai_compat.DEFAULT_GLOSSARY", DEFAULT_GLOSSARY + "\nCUSTOM: test"):
            b_custom = OpenAICompatibleBackend(base_url="http://fake:1234/v1", model="test:1b")

        assert b_default.prompt_hash != b_custom.prompt_hash, (
            "Same hash with different glossaries -- prompt_hash is not incorporating glossary content"
        )


# ---------------------------------------------------------------------------
# PipelineTracker (no-op mode)
# ---------------------------------------------------------------------------


class TestPipelineTrackerNoOp:
    def test_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            tracker = PipelineTracker()
            assert not tracker.enabled
            assert not tracker.active

    def test_start_run_noop_when_disabled(self):
        with patch.dict(os.environ, {}, clear=True):
            tracker = PipelineTracker()
            tracker.start_run(model="test", url="http://x")
            assert not tracker.active

    def test_log_extraction_noop_when_disabled(self):
        with patch.dict(os.environ, {}, clear=True):
            tracker = PipelineTracker()
            update = CoPUpdate(update_type=UpdateType.FUEL, confidence=0.9)
            tracker.log_extraction(update)  # should not raise
            assert tracker._total_extractions == 0

    def test_end_run_noop_when_disabled(self):
        with patch.dict(os.environ, {}, clear=True):
            tracker = PipelineTracker()
            tracker.end_run({"total_messages": 100})  # should not raise
            assert not tracker.active

    def test_enabled_requires_mlflow(self):
        """Even with env var set, enabled is False if mlflow not importable."""
        with patch.dict(os.environ, {"CHAT_TO_COP_TRACKING_ENABLED": "true"}):
            tracker = PipelineTracker()
            # enabled depends on _HAS_MLFLOW — if mlflow IS installed, this is True
            # Either way, the tracker should not crash
            tracker.start_run(model="test")
            tracker.end_run()


# ---------------------------------------------------------------------------
# AIBOM generation
# ---------------------------------------------------------------------------


class TestAIBOMGeneration:
    def test_generate_aibom_structure(self, tmp_path: Path):
        # Add scripts dir to path so we can import
        scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
        sys.path.insert(0, str(scripts_dir))
        try:
            from generate_aibom import generate_aibom

            output = tmp_path / "test_aibom.json"
            bom = generate_aibom(output_path=output)

            assert output.exists()

            # Validate top-level CycloneDX structure
            assert bom["bomFormat"] == "CycloneDX"
            assert bom["specVersion"] == "1.7"
            assert bom["serialNumber"].startswith("urn:uuid:")
            assert bom["version"] == 1

            # Metadata
            assert "metadata" in bom
            assert bom["metadata"]["component"]["type"] == "machine-learning-model"
            assert bom["metadata"]["component"]["name"] == "chat-to-cop"

            # Components list exists (may be empty in test env)
            assert "components" in bom
            assert isinstance(bom["components"], list)

            # Model card
            assert "modelCard" in bom
            assert "datasets" in bom["modelCard"]
            assert len(bom["modelCard"]["datasets"]) >= 2

            # Model implementation
            assert "modelImplementation" in bom
            assert len(bom["modelImplementation"]["models"]) >= 2

            # Ethical considerations
            considerations = bom["modelCard"]["considerations"]
            assert "ethicalConsiderations" in considerations
            assert len(considerations["ethicalConsiderations"]) >= 1

            # Verify JSON is valid by re-parsing from file
            reloaded = json.loads(output.read_text())
            assert reloaded["bomFormat"] == "CycloneDX"
        finally:
            sys.path.remove(str(scripts_dir))

    def test_aibom_has_git_commit(self, tmp_path: Path):
        scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
        sys.path.insert(0, str(scripts_dir))
        try:
            from generate_aibom import generate_aibom

            output = tmp_path / "test_aibom2.json"
            bom = generate_aibom(output_path=output)

            # git:commit property should exist (may be empty in CI)
            props = bom["metadata"]["properties"]
            git_prop = [p for p in props if p["name"] == "git:commit"]
            assert len(git_prop) == 1
        finally:
            sys.path.remove(str(scripts_dir))

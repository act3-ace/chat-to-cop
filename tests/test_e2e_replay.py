"""End-to-end tests for the replay pipeline."""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from pydantic import BaseModel

from chat_to_cop.models.cop_update import CoPUpdate, EntityUpdate, UpdateType
from chat_to_cop.output.store import WorldStateStore
from chat_to_cop.replay import run_replay

FIXTURES = Path(__file__).parent / "fixtures"


class FakeBackendForE2E:
    """Backend that returns realistic updates based on message content."""

    async def extract(self, messages: list[dict], schema: type[BaseModel]) -> CoPUpdate:
        # Look at the last user message to decide what to return
        content = messages[-1]["content"] if messages else ""

        if any(noise in content.lower() for noise in ["startex", "nstr", ": .", ": .."]):
            return CoPUpdate(
                update_type=UpdateType.NONE,
                confidence=0.0,
                extraction_method="llm",
                entities=[],
                source_channel="",
                source_speaker="",
                source_message="",
                timestamp=datetime(2025, 9, 23, 14, 0, 0, tzinfo=timezone.utc),
            )

        if "f+" in content.lower():
            return CoPUpdate(
                update_type=UpdateType.FUEL,
                confidence=0.85,
                extraction_method="llm",
                entities=[EntityUpdate(callsign="RR15", fuel_state="F+40")],
                source_channel="",
                source_speaker="",
                source_message="",
                timestamp=datetime(2025, 9, 23, 14, 0, 0, tzinfo=timezone.utc),
            )

        return CoPUpdate(
            update_type=UpdateType.STATUS_CHANGE,
            confidence=0.7,
            extraction_method="llm",
            entities=[],
            source_channel="",
            source_speaker="",
            source_message="",
            timestamp=datetime(2025, 9, 23, 14, 0, 0, tzinfo=timezone.utc),
        )


class TestE2EReplayWithMockBackend:
    """Test the full pipeline with a mock backend."""

    def test_replay_sample_file(self, tmp_path):
        """Replay a sample DASH file end-to-end with mock backend."""
        db_path = str(tmp_path / "test.db")

        async def run():
            # Patch the backend constructor to return our fake
            with patch(
                "chat_to_cop.replay.OpenAICompatibleBackend",
                return_value=FakeBackendForE2E(),
            ):
                await run_replay(
                    path=FIXTURES / "sample_dash3_channel.txt",
                    llm_url="http://fake:11434/v1",
                    model="fake-model",
                    speed=0,
                    db_path=db_path,
                )

            # Verify results in the database
            async with WorldStateStore(db_path) as store:
                update_count = await store.count_updates()
                assert update_count > 0, "Should have extracted some updates"

                updates = await store.get_recent_updates(limit=100)
                # At least some should be fuel updates (the sample has F+40)
                fuel_updates = [u for u in updates if u.update_type == UpdateType.FUEL]
                assert len(fuel_updates) > 0, "Should have found fuel updates in sample"

        asyncio.run(run())

    def test_replay_combined_file(self, tmp_path):
        """Replay combined format — should create agents for multiple channels."""
        db_path = str(tmp_path / "test.db")

        async def run():
            with patch(
                "chat_to_cop.replay.OpenAICompatibleBackend",
                return_value=FakeBackendForE2E(),
            ):
                await run_replay(
                    path=FIXTURES / "sample_dash3_combined.txt",
                    llm_url="http://fake:11434/v1",
                    model="fake-model",
                    speed=0,
                    db_path=db_path,
                )

            async with WorldStateStore(db_path) as store:
                update_count = await store.count_updates()
                assert update_count > 0

        asyncio.run(run())

    def test_replay_writes_audit_log(self, tmp_path):
        """Every message should be in the audit log regardless of extraction."""
        db_path = str(tmp_path / "test.db")

        async def run():
            with patch(
                "chat_to_cop.replay.OpenAICompatibleBackend",
                return_value=FakeBackendForE2E(),
            ):
                await run_replay(
                    path=FIXTURES / "sample_dash1.txt",
                    llm_url="http://fake:11434/v1",
                    model="fake-model",
                    speed=0,
                    db_path=db_path,
                )

            async with WorldStateStore(db_path) as store:
                cursor = await store.db.execute("SELECT COUNT(*) as cnt FROM audit_log")
                row = await cursor.fetchone()
                # sample_dash1.txt has 10 messages
                assert row["cnt"] == 10

        asyncio.run(run())

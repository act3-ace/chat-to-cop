"""End-to-end tests for the replay pipeline."""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from pydantic import BaseModel

from chat_to_cop.config import PipelineConfig
from chat_to_cop.models.cop_update import CoPUpdate, EntityUpdate, UpdateType
from chat_to_cop.output.store import WorldStateStore
from chat_to_cop.replay import run_replay

FIXTURES = Path(__file__).parent / "fixtures"


class FakeBackendForE2E:
    """Backend that returns realistic updates based on message content."""

    async def extract(self, messages: list[dict], schema: type[BaseModel]) -> CoPUpdate:
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


def _fake_config(db_path: str) -> PipelineConfig:
    """Create a PipelineConfig for testing."""
    config = PipelineConfig()
    config.db_path = db_path
    return config


class TestE2EReplayWithMockBackend:
    """Test the full pipeline (supervisor + fusion) with a mock backend."""

    def test_replay_sample_file(self, tmp_path):
        """Replay a sample DASH file end-to-end with mock backend."""
        db_path = str(tmp_path / "test.db")

        async def run():
            with patch(
                "chat_to_cop.replay._make_degrading_backend",
                return_value=FakeBackendForE2E(),
            ):
                await run_replay(
                    path=FIXTURES / "sample_dash3_channel.txt",
                    config=_fake_config(db_path),
                    speed=0,
                )

            async with WorldStateStore(db_path) as store:
                update_count = await store.count_updates()
                # 10 messages: 4 noise (STARTEX, dot, 2x NSTR) + 6 non-none.
                # Fusion drops entities-less STATUS_CHANGE; only FUEL (with callsign) survives.
                assert update_count == 1, f"Expected 1 update (fuel), got {update_count}"

                updates = await store.get_recent_updates(limit=100)
                fuel_updates = [u for u in updates if u.update_type == UpdateType.FUEL]
                assert len(fuel_updates) == 1, f"Expected 1 fuel update, got {len(fuel_updates)}"

        asyncio.run(run())

    def test_replay_combined_file(self, tmp_path):
        """Replay combined format — supervisor spawns agents for multiple channels."""
        db_path = str(tmp_path / "test.db")

        async def run():
            with patch(
                "chat_to_cop.replay._make_degrading_backend",
                return_value=FakeBackendForE2E(),
            ):
                await run_replay(
                    path=FIXTURES / "sample_dash3_combined.txt",
                    config=_fake_config(db_path),
                    speed=0,
                )

            async with WorldStateStore(db_path) as store:
                update_count = await store.count_updates()
                # 8 messages across 2 channels; FakeBackend produces fuel + status_change.
                # Fusion keeps updates with real entities.
                assert update_count == 4, f"Expected 4 updates from combined fixture, got {update_count}"

        asyncio.run(run())

    def test_replay_writes_audit_log(self, tmp_path):
        """Every message should be in the audit log regardless of extraction."""
        db_path = str(tmp_path / "test.db")

        async def run():
            with patch(
                "chat_to_cop.replay._make_degrading_backend",
                return_value=FakeBackendForE2E(),
            ):
                await run_replay(
                    path=FIXTURES / "sample_dash1.txt",
                    config=_fake_config(db_path),
                    speed=0,
                )

            async with WorldStateStore(db_path) as store:
                cursor = await store.db.execute("SELECT COUNT(*) as cnt FROM audit_log")
                row = await cursor.fetchone()
                assert row["cnt"] == 10

        asyncio.run(run())

"""End-to-end replay pipeline: DASH chat logs -> channel agent -> SQLite store.

Usage:
    python -m chat_to_cop.replay data/chat/Dash3-GBC/Data/23Sep/usaf/chat.zip
    python -m chat_to_cop.replay path/to/logs/ --speed 0 --url http://localhost:11434/v1
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import defaultdict
from pathlib import Path

from loguru import logger

from chat_to_cop.agent.channel_agent import ChannelAgent
from chat_to_cop.backend.openai_compat import OpenAICompatibleBackend
from chat_to_cop.ingestion.replay import replay_messages
from chat_to_cop.metrics import metrics
from chat_to_cop.output.store import WorldStateStore


async def run_replay(
    path: Path,
    llm_url: str,
    model: str,
    speed: float,
    db_path: str,
) -> None:
    """Run the full replay pipeline."""
    backend = OpenAICompatibleBackend(base_url=llm_url, model=model, timeout=30.0)
    agents: dict[str, ChannelAgent] = {}
    type_counts: dict[str, int] = defaultdict(int)
    total_messages = 0
    total_updates = 0

    async with WorldStateStore(db_path) as store:
        logger.info(f"Replaying {path}")
        logger.info(f"LLM: {llm_url} / {model}")
        logger.info(f"Store: {db_path}")
        logger.info(f"Speed: {'instant' if speed == 0 else f'{speed}x'}")
        logger.info("---")

        async for message in replay_messages(path, speed=speed):
            total_messages += 1

            # Audit log every message
            await store.write_audit(message)

            # Get or create agent for this channel
            if message.channel not in agents:
                agents[message.channel] = ChannelAgent(
                    channel=message.channel,
                    backend=backend,
                )
                logger.info(f"Spawned agent for {message.channel}")

            agent = agents[message.channel]

            # Extract
            updates = await agent.process_message(message)

            # Store updates
            for update in updates:
                await store.write_update(update)
                total_updates += 1
                type_counts[update.update_type.value] += 1

                logger.info(
                    f"[{update.source_channel}] {update.update_type.value} "
                    f"(conf={update.confidence:.2f}): "
                    f"{update.source_speaker}: {update.source_message[:80]}"
                )

            # Progress every 25 messages
            if total_messages % 25 == 0:
                logger.info(f"Progress: {total_messages} messages, {total_updates} updates, {len(agents)} channels")

        # Summary
        logger.info("---")
        logger.info(f"Replay complete: {total_messages} messages processed")
        logger.info(f"Updates extracted: {total_updates}")
        logger.info(f"Channels: {', '.join(sorted(agents.keys()))}")
        logger.info(f"Entities tracked: {await store.count_entities()}")
        if type_counts:
            logger.info("Update types:")
            for utype, count in sorted(type_counts.items(), key=lambda x: -x[1]):
                logger.info(f"  {utype}: {count}")

        metrics.log_summary()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Replay DASH chat logs through the chat-to-cop agent pipeline",
    )
    parser.add_argument(
        "path",
        type=Path,
        help="Path to chat log file, zip, or directory",
    )
    parser.add_argument(
        "--url",
        default="http://localhost:11434/v1",
        help="LLM endpoint URL (default: Ollama local)",
    )
    parser.add_argument(
        "--model",
        default="qwen2.5:7b",
        help="Model name (default: qwen2.5:7b)",
    )
    parser.add_argument(
        "--speed",
        type=float,
        default=0.0,
        help="Playback speed: 0=instant, 1.0=realtime, 2.0=2x (default: 0)",
    )
    parser.add_argument(
        "--db",
        default="data/world_state.db",
        help="SQLite database path (default: data/world_state.db)",
    )

    args = parser.parse_args()

    if not args.path.exists():
        logger.error(f"Path not found: {args.path}")
        sys.exit(1)

    asyncio.run(run_replay(args.path, args.url, args.model, args.speed, args.db))


if __name__ == "__main__":
    main()

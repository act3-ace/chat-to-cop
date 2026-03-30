"""End-to-end replay pipeline: DASH chat logs -> supervisor -> fusion -> store.

Uses the full pipeline: DegradingBackend (LLM + regex + passthrough),
Supervisor (agent lifecycle), FusionAgent (deconfliction), WorldStateStore.

Usage:
    python -m chat_to_cop.replay data/chat/Dash3-GBC/Data/23Sep/usaf/chat.zip
    python -m chat_to_cop.replay path/to/logs/ --speed 0 --url http://127.0.0.1:11434/v1
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import defaultdict
from pathlib import Path

from loguru import logger

from chat_to_cop.agent.fusion_agent import FusionAgent
from chat_to_cop.agent.supervisor import Supervisor
from chat_to_cop.backend.degrading import DegradingBackend
from chat_to_cop.backend.openai_compat import OpenAICompatibleBackend
from chat_to_cop.backend.regex_fallback import RegexBackend
from chat_to_cop.config import PipelineConfig
from chat_to_cop.ingestion.replay import replay_messages
from chat_to_cop.metrics import metrics
from chat_to_cop.output.cop_writer import CoPWriter
from chat_to_cop.output.store import WorldStateStore
from chat_to_cop.tracking import PipelineTracker


def _make_degrading_backend(config: PipelineConfig) -> DegradingBackend:
    """Build the degrading backend from config: LLM -> fallback -> regex."""
    primary = OpenAICompatibleBackend(
        base_url=config.llm.llm_url,
        model=config.llm.llm_model,
        api_key=config.llm.llm_api_key,
        timeout=config.llm.llm_timeout,
        max_retries=config.llm.llm_max_retries,
        num_ctx=config.llm.llm_num_ctx,
    )
    # Only add a separate fallback if it's a different model
    backends = [primary]
    timeouts = [config.llm.llm_timeout]

    if config.fallback.fallback_model != config.llm.llm_model:
        fallback = OpenAICompatibleBackend(
            base_url=config.fallback.fallback_url,
            model=config.fallback.fallback_model,
            timeout=config.fallback.fallback_timeout,
        )
        backends.append(fallback)
        timeouts.append(config.fallback.fallback_timeout)

    backends.append(RegexBackend())
    timeouts.append(1.0)  # Regex is near-instant

    return DegradingBackend(
        backends=backends,
        timeouts=timeouts,
        fail_max=config.degrading.circuit_fail_max,
        cooldown=config.degrading.circuit_cooldown,
    )


async def run_replay(
    path: Path,
    config: PipelineConfig,
    speed: float,
) -> None:
    """Run the full replay pipeline with supervisor + fusion."""

    # Build the degrading backend factory for the supervisor
    def backend_factory():
        return _make_degrading_backend(config)

    supervisor = Supervisor(backend_factory=backend_factory)
    fusion = FusionAgent()
    cop_writer = CoPWriter.from_config(config.cop_writer)
    tracker = PipelineTracker()
    tracker.start_run(
        model=config.llm.llm_model,
        url=config.llm.llm_url,
        timeout=config.llm.llm_timeout,
        num_ctx=config.llm.llm_num_ctx,
        window_size=config.agent.window_size,
        run_name=f"replay-{path.stem}",
    )
    type_counts: dict[str, int] = defaultdict(int)
    total_messages = 0
    total_updates = 0

    async with WorldStateStore(config.db_path) as store:
        logger.info(f"Replaying {path}")
        logger.info(f"LLM: {config.llm.llm_url} / {config.llm.llm_model}")
        if config.fallback.fallback_model != config.llm.llm_model:
            logger.info(f"Fallback: {config.fallback.fallback_model}")
        logger.info("Degradation: LLM -> regex -> passthrough")
        logger.info(f"Store: {config.db_path}")
        cop_mode = "dry-run" if not config.cop_writer.cop_api_url else config.cop_writer.cop_api_url
        logger.info(f"CoP writer: {cop_mode}")
        logger.info(f"Speed: {'instant' if speed == 0 else f'{speed}x'}")
        logger.info("---")

        # Collect updates in batches for fusion
        pending_updates = []

        async for message in replay_messages(path, speed=speed):
            total_messages += 1

            # Audit log every message
            await store.write_audit(message)

            # Route through supervisor (handles agent lifecycle + load shedding)
            updates = await supervisor.route_message(message)

            # Collect for fusion batching
            pending_updates.extend(updates)

            # Run fusion every 5 messages or when we have enough updates
            if total_messages % 5 == 0 and pending_updates:
                fused = fusion.process_updates(pending_updates)
                for update in fused:
                    if update.update_type.value != "none":
                        await store.write_update(update)
                        await cop_writer.push_update(update)
                        tracker.log_extraction(update)
                        total_updates += 1
                        type_counts[update.update_type.value] += 1
                        logger.info(
                            f"[{update.source_channel}] {update.update_type.value} "
                            f"(conf={update.confidence:.2f}, "
                            f"method={update.extraction_method}): "
                            f"{update.source_speaker}: "
                            f"{update.source_message[:60]}"
                        )
                pending_updates.clear()

            # Progress every 25 messages
            if total_messages % 25 == 0:
                logger.info(
                    f"Progress: {total_messages} msgs, "
                    f"{total_updates} updates, "
                    f"{len(supervisor.active_channels)} channels, "
                    f"{len(supervisor.shed_channels)} shed"
                )

        # Flush remaining updates through fusion
        if pending_updates:
            fused = fusion.process_updates(pending_updates)
            for update in fused:
                if update.update_type.value != "none":
                    await store.write_update(update)
                    await cop_writer.push_update(update)
                    tracker.log_extraction(update)
                    total_updates += 1
                    type_counts[update.update_type.value] += 1

        # Summary
        logger.info("---")
        logger.info(f"Replay complete: {total_messages} messages processed")
        logger.info(f"Updates extracted: {total_updates}")
        logger.info(f"Channels: {', '.join(sorted(supervisor.active_channels))}")
        logger.info(f"Entities tracked: {await store.count_entities()}")
        if type_counts:
            logger.info("Update types:")
            for utype, count in sorted(type_counts.items(), key=lambda x: -x[1]):
                logger.info(f"  {utype}: {count}")

        # CoP writer stats
        writer_stats = cop_writer.stats
        logger.info("CoP writer stats:")
        logger.info(f"  auto_writes: {writer_stats['auto_writes']}")
        logger.info(f"  flagged_writes: {writer_stats['flagged_writes']}")
        logger.info(f"  human_queued: {writer_stats['human_queued']}")
        logger.info(f"  errors: {writer_stats['errors']}")
        logger.info(f"  human_review_pending: {cop_writer.human_queue_size}")

        # Fusion stats
        logger.info("Fusion stats:")
        for ch in sorted(supervisor.active_channels):
            noise = fusion.channel_noise_rate(ch)
            logger.info(f"  {ch}: noise_rate={noise:.2f}")

        metrics.log_summary()

        # End MLflow tracking run with summary metrics
        tracker.end_run(
            {
                "total_messages": total_messages,
                "total_updates": total_updates,
                "channels": len(supervisor.active_channels),
                "entities_tracked": await store.count_entities(),
            }
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Replay DASH chat logs through the full chat-to-cop pipeline",
    )
    parser.add_argument(
        "path",
        type=Path,
        help="Path to chat log file, zip, or directory",
    )
    parser.add_argument(
        "--url",
        default=None,
        help="LLM endpoint URL (overrides CHAT_TO_COP_LLM_URL)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model name (overrides CHAT_TO_COP_LLM_MODEL)",
    )
    parser.add_argument(
        "--speed",
        type=float,
        default=0.0,
        help="Playback speed: 0=instant, 1.0=realtime, 2.0=2x (default: 0)",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="SQLite database path (overrides CHAT_TO_COP_DB_PATH)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="LLM timeout in seconds (default: 120, increase for cold starts)",
    )
    parser.add_argument(
        "--num-ctx",
        type=int,
        default=None,
        help="Context window size for Ollama models (default: 8192)",
    )

    args = parser.parse_args()

    if not args.path.exists():
        logger.error(f"Path not found: {args.path}")
        sys.exit(1)

    # Load config from env vars, then apply CLI overrides
    config = PipelineConfig()
    if args.url:
        config.llm.llm_url = args.url
    if args.model:
        config.llm.llm_model = args.model
    if args.db:
        config.db_path = args.db
    if args.timeout:
        config.llm.llm_timeout = args.timeout
    if args.num_ctx:
        config.llm.llm_num_ctx = args.num_ctx

    asyncio.run(run_replay(args.path, config, args.speed))


if __name__ == "__main__":
    main()

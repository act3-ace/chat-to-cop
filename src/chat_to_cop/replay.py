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
import os
import sys
from collections import defaultdict
from pathlib import Path

from loguru import logger

from chat_to_cop.agent.fusion_agent import FusionAgent
from chat_to_cop.agent.supervisor import Supervisor
from chat_to_cop.backend.degrading import DegradingBackend, load_cascade_thresholds
from chat_to_cop.backend.openai_compat import OpenAICompatibleBackend
from chat_to_cop.backend.regex_fallback import RegexBackend
from chat_to_cop.calibration import CalibrationModel
from chat_to_cop.config import PipelineConfig
from chat_to_cop.equifinality import PathEntropyTracker
from chat_to_cop.ingestion.irc_client import IRCClient
from chat_to_cop.ingestion.replay import replay_messages
from chat_to_cop.metrics import metrics
from chat_to_cop.output.cop_writer import CoPWriter
from chat_to_cop.output.store import WorldStateStore
from chat_to_cop.tracking import PipelineTracker


def _make_degrading_backend(
    config: PipelineConfig,
    bedrock_config: dict | None = None,
    anthropic_config: dict | None = None,
    asksage_config: dict | None = None,
) -> DegradingBackend:
    """Build the degrading backend from config: LLM -> fallback -> regex."""
    if asksage_config:
        from chat_to_cop.backend.asksage import AskSageBackend

        primary = AskSageBackend(
            email=asksage_config["email"],
            api_key=asksage_config["api_key"],
            model=asksage_config["model"],
            hard_timeout=config.llm.llm_extract_hard_timeout,
        )
    elif bedrock_config:
        from chat_to_cop.backend.bedrock import BedrockBackend

        primary = BedrockBackend(
            model=bedrock_config["model"],
            aws_region=bedrock_config["region"],
            timeout=bedrock_config["timeout"],
            max_retries=config.llm.llm_max_retries,
            hard_timeout=config.llm.llm_extract_hard_timeout,
        )
    elif anthropic_config:
        from chat_to_cop.backend.anthropic_direct import AnthropicDirectBackend

        primary = AnthropicDirectBackend(
            model=anthropic_config["model"],
            timeout=anthropic_config["timeout"],
            max_retries=config.llm.llm_max_retries,
            hard_timeout=config.llm.llm_extract_hard_timeout,
        )
    else:
        primary = OpenAICompatibleBackend(
            base_url=config.llm.llm_url,
            model=config.llm.llm_model,
            api_key=config.llm.llm_api_key,
            timeout=config.llm.llm_timeout,
            max_retries=config.llm.llm_max_retries,
            num_ctx=config.llm.llm_num_ctx,
            hard_timeout=config.llm.llm_extract_hard_timeout,
            is_ollama=config.llm.llm_is_ollama,
        )
    # Only add a separate fallback if it's a different model
    backends = [primary]
    timeouts = [config.llm.llm_timeout]

    if config.fallback.fallback_model != config.llm.llm_model:
        fallback = OpenAICompatibleBackend(
            base_url=config.fallback.fallback_url,
            model=config.fallback.fallback_model,
            timeout=config.fallback.fallback_timeout,
            hard_timeout=config.fallback.fallback_extract_hard_timeout,
            is_ollama=config.fallback.fallback_is_ollama,
        )
        backends.append(fallback)
        timeouts.append(config.fallback.fallback_timeout)

    backends.append(RegexBackend())
    timeouts.append(1.0)  # Regex is near-instant

    # Issue #67: load per-UpdateType cascade thresholds if the configured
    # JSON file exists. Missing file degrades to the scalar cascade_threshold
    # (which defaults to 0, i.e. cascade disabled — same as pre-#67 behavior).
    cascade_thresholds = load_cascade_thresholds(config.degrading.cascade_thresholds_path)

    return DegradingBackend(
        backends=backends,
        timeouts=timeouts,
        fail_max=config.degrading.circuit_fail_max,
        cooldown=config.degrading.circuit_cooldown,
        cascade_thresholds=cascade_thresholds,
    )


async def run_replay(
    path: Path | None,
    config: PipelineConfig,
    speed: float,
    bedrock_config: dict | None = None,
    anthropic_config: dict | None = None,
    asksage_config: dict | None = None,
    irc_url: str | None = None,
    irc_channels: list[str] | None = None,
) -> None:
    """Run the full pipeline with supervisor + fusion.

    Message source is either file replay (path) or live IRC (irc_url).
    """

    # Build the degrading backend factory for the supervisor
    def backend_factory():
        return _make_degrading_backend(
            config,
            bedrock_config=bedrock_config,
            anthropic_config=anthropic_config,
            asksage_config=asksage_config,
        )

    # Load calibration model if configured. Without this, ChannelAgent reports
    # raw (uncalibrated) confidence and the AUTO/FLAGGED tier thresholds gate on
    # noise. Was previously dead config — see issue #52 audit.
    calibration_model: CalibrationModel | None = None
    if config.calibration_model:
        calibration_model = CalibrationModel.from_file(config.calibration_model)
        logger.info(f"Loaded calibration model from {config.calibration_model}")

    supervisor = Supervisor(
        backend_factory=backend_factory,
        agent_config=config.agent,
        calibration_model=calibration_model,
        latency_threshold=config.supervisor.latency_threshold,
        error_rate_threshold=config.supervisor.error_rate_threshold,
        health_check_interval=config.supervisor.health_check_interval,
        max_restart_attempts=config.supervisor.max_restart_attempts,
        health_alarm_interval=config.supervisor.health_alarm_interval,
        health_alarm_window_minutes=config.supervisor.health_alarm_window_minutes,
        passthrough_alarm_threshold=config.supervisor.passthrough_alarm_threshold,
    )
    fusion = FusionAgent()
    cop_writer = CoPWriter.from_config(config.cop_writer)
    tracker = PipelineTracker()
    run_name = f"live-{irc_url}" if irc_url else f"replay-{path.stem}" if path else "unknown"
    tracker.start_run(
        model=config.llm.llm_model,
        url=config.llm.llm_url,
        timeout=config.llm.llm_timeout,
        num_ctx=config.llm.llm_num_ctx,
        window_size=config.agent.window_size,
        run_name=run_name,
    )
    entropy_tracker = PathEntropyTracker()
    type_counts: dict[str, int] = defaultdict(int)
    total_messages = 0
    total_updates = 0

    async with WorldStateStore(config.db_path) as store:
        if irc_url:
            _default_channels = [
                "#c2_coord",
                "#fires",
                "#isr_reports",
                "#jprc",
                "#stt_C2Coord",
                "#stt_hydroBMA",
                "#stt_crusherBMA",
                "#stt_mesquiteBMA",
                "#stt_taipanBMA",
                "#vegas_internal",
            ]
            channels = irc_channels or _default_channels
            irc_client = IRCClient(irc_url, channels)
            message_source = irc_client.iter_messages()
            logger.info(f"Live IRC: {irc_url}")
            logger.info(f"Channels: {', '.join(channels)}")
        else:
            assert path is not None
            message_source = replay_messages(path, speed=speed)
            logger.info(f"Replaying {path}")

        logger.info(f"LLM: {config.llm.llm_url} / {config.llm.llm_model}")
        if config.fallback.fallback_model != config.llm.llm_model:
            logger.info(f"Fallback: {config.fallback.fallback_model}")
        logger.info("Degradation: LLM -> regex -> passthrough")
        logger.info(f"Store: {config.db_path}")
        cop_mode = "dry-run" if not config.cop_writer.cop_api_url else config.cop_writer.cop_api_url
        logger.info(f"CoP writer: {cop_mode}")
        if not irc_url:
            logger.info(f"Speed: {'instant' if speed == 0 else f'{speed}x'}")
        logger.info("---")

        # Collect updates in batches for fusion
        pending_updates = []
        import time as _time

        _replay_start = _time.monotonic()
        _msg_latencies: list[float] = []

        async for message in message_source:
            total_messages += 1
            _msg_start = _time.monotonic()

            # Audit log every message
            await store.write_audit(message)

            # Route through supervisor (handles agent lifecycle + load shedding)
            updates = await supervisor.route_message(message)

            _msg_elapsed = _time.monotonic() - _msg_start
            _msg_latencies.append(_msg_elapsed)

            # Collect for fusion batching
            pending_updates.extend(updates)

            # Run fusion every 5 messages or when we have enough updates
            if total_messages % 5 == 0 and pending_updates:
                fused = fusion.process_updates(pending_updates)

                # Feedback loop: route fusion signals back to channel agents
                feedback = fusion.generate_feedback(pending_updates)
                if feedback:
                    supervisor.route_feedback(feedback)

                for update in fused:
                    if update.update_type.value != "none":
                        await store.write_update(update)
                        await cop_writer.push_update(update)
                        tracker.log_extraction(update)
                        entropy_tracker.record(update.update_type.value, update.extraction_method)
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

            # Progress every 25 messages — with latency stats
            if total_messages % 25 == 0:
                _elapsed_total = _time.monotonic() - _replay_start
                _recent = _msg_latencies[-25:]
                _avg_recent = sum(_recent) / len(_recent) if _recent else 0
                _avg_overall = _elapsed_total / total_messages
                logger.info(
                    f"Progress: {total_messages} msgs, "
                    f"{total_updates} updates, "
                    f"{len(supervisor.active_channels)} channels, "
                    f"{len(supervisor.shed_channels)} shed | "
                    f"last25_avg={_avg_recent:.1f}s, "
                    f"overall_avg={_avg_overall:.1f}s, "
                    f"elapsed={_elapsed_total:.0f}s"
                )

        # Flush remaining updates through fusion
        if pending_updates:
            fused = fusion.process_updates(pending_updates)
            feedback = fusion.generate_feedback(pending_updates)
            if feedback:
                supervisor.route_feedback(feedback)
            for update in fused:
                if update.update_type.value != "none":
                    await store.write_update(update)
                    await cop_writer.push_update(update)
                    tracker.log_extraction(update)
                    entropy_tracker.record(update.update_type.value, update.extraction_method)
                    total_updates += 1
                    type_counts[update.update_type.value] += 1

        # Summary with latency diagnostics
        _total_elapsed = _time.monotonic() - _replay_start
        logger.info("---")
        logger.info(f"Replay complete: {total_messages} messages processed")
        logger.info(f"Updates extracted: {total_updates}")
        logger.info(f"Channels: {', '.join(sorted(supervisor.active_channels))}")
        logger.info(f"Entities tracked: {await store.count_entities()}")
        logger.info(f"Wall clock: {_total_elapsed:.0f}s ({_total_elapsed / 60:.1f} min)")
        if _msg_latencies:
            _sorted = sorted(_msg_latencies)
            _p50 = _sorted[len(_sorted) // 2]
            _p95 = _sorted[int(len(_sorted) * 0.95)]
            _p99 = _sorted[int(len(_sorted) * 0.99)]
            _mean = sum(_msg_latencies) / len(_msg_latencies)
            logger.info(
                f"Per-message latency: mean={_mean:.1f}s, "
                f"p50={_p50:.1f}s, p95={_p95:.1f}s, p99={_p99:.1f}s, "
                f"min={_sorted[0]:.1f}s, max={_sorted[-1]:.1f}s"
            )
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

        # Path entropy (equifinality health)
        entropy_report = entropy_tracker.report()
        if entropy_report:
            logger.info("Path entropy (equifinality):")
            for utype, ent in entropy_report.items():
                logger.info(f"  {utype}: {ent:.2f}")
            fragile = entropy_tracker.fragile_types()
            if fragile:
                logger.warning(f"Fragile extraction paths (entropy < 0.5): {fragile}")

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
        nargs="?",
        default=None,
        help="Path to chat log file, zip, or directory (omit when using --irc-url)",
    )
    parser.add_argument(
        "--irc-url",
        default=None,
        help="Live IRC WebSocket URL (e.g. ws://127.0.0.1:8097). Replaces file replay.",
    )
    parser.add_argument(
        "--irc-channels",
        default=None,
        help="Comma-separated IRC channels to join (default: DASH 3 channel list)",
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
    parser.add_argument(
        "--bedrock",
        action="store_true",
        help="Use AWS Bedrock (Claude) instead of OpenAI-compatible endpoint",
    )
    parser.add_argument(
        "--bedrock-region",
        default="us-gov-west-1",
        help="AWS region for Bedrock (default: us-gov-west-1)",
    )
    parser.add_argument(
        "--anthropic",
        action="store_true",
        help="Use Anthropic API directly (requires ANTHROPIC_API_KEY)",
    )
    parser.add_argument(
        "--asksage",
        action="store_true",
        help="Use Ask Sage API on NIPRNet (requires asksageclient)",
    )
    parser.add_argument(
        "--asksage-email",
        default=None,
        help="Ask Sage account email",
    )
    parser.add_argument(
        "--asksage-key",
        default=None,
        help="Ask Sage API key",
    )

    args = parser.parse_args()

    # Load config from env vars, then apply CLI overrides
    config = PipelineConfig()

    # IRC settings: CLI overrides env var (config.irc_url / config.irc_channels)
    effective_irc_url = args.irc_url or config.irc_url
    effective_irc_channels_str = args.irc_channels  # raw CLI string, may be None

    if not effective_irc_url and not args.path:
        parser.error("either path or --irc-url (or CHAT_TO_COP_IRC_URL env var) is required")
    if args.path and not effective_irc_url and not args.path.exists():
        logger.error(f"Path not found: {args.path}")
        sys.exit(1)

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

    # Bedrock mode: override the backend factory
    bedrock_config = None
    anthropic_config = None
    if args.bedrock:
        bedrock_config = {
            "model": args.model or "us-gov.anthropic.claude-sonnet-4-5-20250929-v1:0",
            "region": args.bedrock_region,
            "timeout": args.timeout or config.llm.llm_timeout,
        }
        config.llm.llm_model = bedrock_config["model"]
    elif args.anthropic:
        anthropic_config = {
            "model": args.model or "claude-haiku-4-5-20251001",
            "timeout": args.timeout or 30.0,
        }
        config.llm.llm_model = anthropic_config["model"]

    # Ask Sage mode
    asksage_config = None
    if args.asksage:
        asksage_config = {
            "email": args.asksage_email or os.environ.get("ASKSAGE_EMAIL", ""),
            "api_key": args.asksage_key or os.environ.get("ASKSAGE_API_KEY", ""),
            "model": args.model or "claude-opus-4-6",
        }
        if not asksage_config["email"] or not asksage_config["api_key"]:
            logger.error("--asksage requires --asksage-email and --asksage-key (or env vars)")
            sys.exit(1)
        config.llm.llm_model = asksage_config["model"]

    # Parse IRC channels: CLI string > env var string > None (use defaults in run_replay)
    if effective_irc_channels_str:
        irc_channels = effective_irc_channels_str.split(",")
    elif config.irc_channels:
        irc_channels = config.irc_channels.split(",")
    else:
        irc_channels = None

    asyncio.run(
        run_replay(
            args.path,
            config,
            args.speed,
            bedrock_config=bedrock_config,
            anthropic_config=anthropic_config,
            asksage_config=asksage_config,
            irc_url=effective_irc_url,
            irc_channels=irc_channels,
        )
    )


if __name__ == "__main__":
    main()

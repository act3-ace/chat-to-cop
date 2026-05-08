"""Generate silver labels: send DASH 3 messages to a high-quality LLM and save extractions.

Sends real DASH 3 messages through the OpenAICompatibleBackend (per-message, no
conversation window effects) and saves the structured output as JSONL. These
labels become the reference for evaluating smaller/faster models.

Default target: Qwen3-32B on Groq (F1=0.96 on synthetic data).

Usage:
    # Generate labels with Groq (default)
    python scripts/generate_silver_labels.py --api-key $GROQ_API_KEY --count 100

    # Use local Ollama
    python scripts/generate_silver_labels.py --url http://127.0.0.1:11434/v1 --model qwen3:30b-a3b --rate-delay 0

    # Filter to specific channels
    python scripts/generate_silver_labels.py --channels "#c2_coord,#isr_reports" --count 200

    # Resume a partial run (skips already-labeled lines)
    python scripts/generate_silver_labels.py --count 500

Environment:
    GROQ_API_KEY -- API key for Groq (or use --api-key)
    OPENAI_API_KEY -- Fallback API key source
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

from chat_to_cop.backend.openai_compat import (
    DEFAULT_GLOSSARY,
    OpenAICompatibleBackend,
    RetryableError,
    build_system_prompt,
)
from chat_to_cop.ingestion.replay import parse_path
from chat_to_cop.models.cop_update import CoPUpdate
from chat_to_cop.models.messages import IRCMessage

_NOISE_RE = re.compile(
    r"^[\s.]+$"
    r"|^\s*c\s*$"
    r"|^\s*copy\s*$"
    r"|^\s*word\s*$"
    r"|^\s*test\s*$"
    r"|^\s*NSTR\s*$"
    r"|^\s*roger\s*$"
    r"|^\s*affirm\s*$"
    r"|^\s*wilco\s*$",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"https?://\S+")
_RADIO_CHECK_RE = re.compile(r"radio\s+check", re.IGNORECASE)
_EXERCISE_CONTROL_RE = re.compile(
    r"\*{3,}.*(?:STARTEX|ENDEX|START\s+EX|END\s+EX).*\*{3,}"
    r"|^\s*(?:STARTEX|ENDEX)\b",
    re.IGNORECASE,
)


def _is_noise(content: str) -> bool:
    """Check if a message is noise that shouldn't be sent to the LLM."""
    stripped = content.strip()
    if not stripped or len(stripped) <= 1:
        return True
    if _NOISE_RE.match(stripped):
        return True
    if _URL_RE.match(stripped):
        return True
    if _RADIO_CHECK_RE.search(stripped):
        return True
    if _EXERCISE_CONTROL_RE.search(stripped):
        return True
    return False


def _build_single_message_prompt(msg: IRCMessage) -> list[dict[str, str]]:
    """Build a prompt for a single message (no conversation window)."""
    system_prompt = build_system_prompt(glossary=DEFAULT_GLOSSARY)
    user_content = (
        f"EXTRACT FROM THIS MESSAGE ONLY:\n[{msg.timestamp.strftime('%H:%M:%S')}] {msg.sender}: {msg.content}"
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]


def _label_to_dict(msg: IRCMessage, result: CoPUpdate, model: str) -> dict:
    """Convert a message + extraction result into a label dict for JSONL."""
    entities = []
    for ent in result.entities:
        ent_dict = ent.model_dump(exclude_none=True, exclude_defaults=True)
        # Remove empty metadata
        if "metadata" in ent_dict and not ent_dict["metadata"]:
            del ent_dict["metadata"]
        entities.append(ent_dict)

    return {
        "raw_line": msg.raw_line,
        "channel": msg.channel,
        "sender": msg.sender,
        "timestamp": msg.timestamp.isoformat(),
        "extracted_type": result.update_type.value,
        "extracted_entities": entities,
        "confidence": result.confidence,
        "extraction_method": result.extraction_method,
        "model": model,
        "reasoning": result.reasoning,
        "source": "llm_judge",
        "labeler": model,
    }


def _noise_label(msg: IRCMessage, model: str) -> dict:
    """Create a label for a noise-filtered message (no LLM call needed)."""
    return {
        "raw_line": msg.raw_line,
        "channel": msg.channel,
        "sender": msg.sender,
        "timestamp": msg.timestamp.isoformat(),
        "extracted_type": "none",
        "extracted_entities": [],
        "confidence": 0.0,
        "extraction_method": "noise_filter",
        "model": model,
        "reasoning": "Pre-filtered as noise (ack, dot, URL, exercise control, or too short)",
        "source": "llm_judge",
        "labeler": model,
    }


def _load_existing_labels(output_path: Path) -> set[str]:
    """Load raw_line values from an existing JSONL file for dedup."""
    seen = set()
    if not output_path.exists():
        return seen
    with open(output_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                seen.add(obj.get("raw_line", ""))
            except json.JSONDecodeError:
                continue
    return seen


async def generate_labels(
    data_path: str,
    url: str,
    model: str,
    api_key: str,
    output: str,
    count: int | None,
    rate_delay: float,
    channels: list[str] | None,
) -> None:
    """Main label generation loop."""
    # Parse messages
    messages = parse_path(Path(data_path))
    if not messages:
        print(f"ERROR: No messages found in {data_path}")
        sys.exit(1)

    print(f"Parsed {len(messages)} messages from {data_path}")

    # Channel filter
    if channels:
        channel_set = set(channels)
        messages = [m for m in messages if m.channel in channel_set]
        print(f"Filtered to {len(messages)} messages in channels: {channels}")

    # Count limit
    if count is not None:
        messages = messages[:count]
        print(f"Limited to first {len(messages)} messages")

    # Load existing labels for resume support
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    existing = _load_existing_labels(output_path)
    if existing:
        print(f"Found {len(existing)} existing labels in {output} (will skip duplicates)")

    # Set up backend
    backend = OpenAICompatibleBackend(
        base_url=url,
        model=model,
        api_key=api_key,
        timeout=120.0,
        max_retries=3,
    )

    # Stats
    total = len(messages)
    processed = 0
    skipped_existing = 0
    skipped_noise = 0
    extracted = 0
    errors = 0
    last_llm_call = 0.0

    print(f"\nGenerating silver labels: {total} messages -> {output}")
    print(f"Model: {model} @ {url}")
    if rate_delay > 0:
        non_noise = sum(1 for m in messages if not _is_noise(m.content) and m.raw_line not in existing)
        est_minutes = non_noise * rate_delay / 60
        print(f"Rate delay: {rate_delay:.1f}s between LLM calls (~{est_minutes:.0f} min for {non_noise} LLM calls)")
    print()

    with open(output_path, "a", encoding="utf-8") as f:
        for i, msg in enumerate(messages):
            processed += 1

            # Skip already-labeled messages
            if msg.raw_line in existing:
                skipped_existing += 1
                _print_progress(i + 1, total, extracted, skipped_noise, skipped_existing, errors)
                continue

            # Save noise labels without LLM call
            if _is_noise(msg.content):
                label = _noise_label(msg, model)
                f.write(json.dumps(label, ensure_ascii=False) + "\n")
                f.flush()
                skipped_noise += 1
                _print_progress(i + 1, total, extracted, skipped_noise, skipped_existing, errors)
                continue

            # Rate limiting
            if rate_delay > 0:
                elapsed = time.perf_counter() - last_llm_call
                if elapsed < rate_delay and last_llm_call > 0:
                    wait = rate_delay - elapsed
                    _print_progress(i + 1, total, extracted, skipped_noise, skipped_existing, errors, wait=wait)
                    await asyncio.sleep(wait)

            # Extract via LLM
            prompt = _build_single_message_prompt(msg)
            retries = 0
            max_retries = 5

            while True:
                try:
                    result = await backend.extract(prompt, CoPUpdate)
                    last_llm_call = time.perf_counter()

                    # Fill source fields
                    result.extraction_method = "llm"
                    label = _label_to_dict(msg, result, model)
                    f.write(json.dumps(label, ensure_ascii=False) + "\n")
                    f.flush()
                    extracted += 1
                    break

                except RetryableError as e:
                    retries += 1
                    if retries > max_retries:
                        # Save error label so we can see what failed
                        error_label = {
                            "raw_line": msg.raw_line,
                            "channel": msg.channel,
                            "sender": msg.sender,
                            "timestamp": msg.timestamp.isoformat(),
                            "extracted_type": "none",
                            "extracted_entities": [],
                            "confidence": 0.0,
                            "extraction_method": "error",
                            "model": model,
                            "reasoning": f"Failed after {max_retries} retries: {e}",
                            "source": "llm_judge",
                            "labeler": model,
                        }
                        f.write(json.dumps(error_label, ensure_ascii=False) + "\n")
                        f.flush()
                        errors += 1
                        break

                    # Exponential backoff: 5s, 10s, 20s, 40s, 80s
                    backoff = 5 * (2 ** (retries - 1))
                    print(f"\n  Rate limited (attempt {retries}/{max_retries}), backing off {backoff}s...")
                    await asyncio.sleep(backoff)

                except Exception as e:
                    error_label = {
                        "raw_line": msg.raw_line,
                        "channel": msg.channel,
                        "sender": msg.sender,
                        "timestamp": msg.timestamp.isoformat(),
                        "extracted_type": "none",
                        "extracted_entities": [],
                        "confidence": 0.0,
                        "extraction_method": "error",
                        "model": model,
                        "reasoning": f"Permanent error: {e}",
                        "source": "llm_judge",
                        "labeler": model,
                    }
                    f.write(json.dumps(error_label, ensure_ascii=False) + "\n")
                    f.flush()
                    errors += 1
                    break

            _print_progress(i + 1, total, extracted, skipped_noise, skipped_existing, errors)

    print(f"\n\nDone! Results saved to {output}")
    print(f"  Total messages: {total}")
    print(f"  LLM extractions: {extracted}")
    print(f"  Noise filtered: {skipped_noise}")
    print(f"  Skipped (existing): {skipped_existing}")
    print(f"  Errors: {errors}")


def _print_progress(
    current: int,
    total: int,
    extracted: int,
    noise: int,
    skipped: int,
    errors: int,
    wait: float = 0.0,
) -> None:
    """Print a single-line progress update."""
    pct = current / total * 100 if total > 0 else 0
    wait_str = f" (waiting {wait:.0f}s)" if wait > 0 else ""
    print(
        f"\r  [{current:>5d}/{total}] {pct:5.1f}%  "
        f"extracted={extracted} noise={noise} skipped={skipped} errors={errors}{wait_str}    ",
        end="",
        flush=True,
    )


async def generate_labels_asksage(
    data_path: str,
    email: str,
    api_key: str,
    model: str,
    output: str,
    count: int | None,
    rate_delay: float,
    channels: list[str] | None,
) -> None:
    """Generate labels using Ask Sage backend."""
    from chat_to_cop.backend.asksage import AskSageBackend

    # Parse messages
    messages = parse_path(Path(data_path))
    if not messages:
        print(f"ERROR: No messages found in {data_path}")
        sys.exit(1)
    print(f"Parsed {len(messages)} messages from {data_path}")

    if channels:
        channel_set = set(channels)
        messages = [m for m in messages if m.channel in channel_set]
        print(f"Filtered to {len(messages)} messages in channels: {channels}")
    if count is not None:
        messages = messages[:count]
        print(f"Limited to first {len(messages)} messages")

    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    existing = _load_existing_labels(output_path)
    if existing:
        print(f"Found {len(existing)} existing labels in {output} (will skip duplicates)")

    backend = AskSageBackend(email=email, api_key=api_key, model=model)

    total = len(messages)
    extracted = 0
    skipped_noise = 0
    skipped_existing = 0
    errors = 0
    last_llm_call = 0.0

    print(f"\nGenerating silver labels (Ask Sage): {total} messages -> {output}")
    print(f"Model: {model} via Ask Sage")
    if rate_delay > 0:
        non_noise = sum(1 for m in messages if not _is_noise(m.content) and m.raw_line not in existing)
        est_minutes = non_noise * rate_delay / 60
        print(f"Rate delay: {rate_delay:.1f}s between calls (~{est_minutes:.0f} min for {non_noise} LLM calls)")
    print()

    with open(output_path, "a", encoding="utf-8") as f:
        for i, msg in enumerate(messages):
            if msg.raw_line in existing:
                skipped_existing += 1
                _print_progress(i + 1, total, extracted, skipped_noise, skipped_existing, errors)
                continue

            if _is_noise(msg.content):
                label = _noise_label(msg, model)
                f.write(json.dumps(label, ensure_ascii=False) + "\n")
                f.flush()
                skipped_noise += 1
                _print_progress(i + 1, total, extracted, skipped_noise, skipped_existing, errors)
                continue

            if rate_delay > 0:
                elapsed = time.perf_counter() - last_llm_call
                if elapsed < rate_delay and last_llm_call > 0:
                    wait = rate_delay - elapsed
                    _print_progress(i + 1, total, extracted, skipped_noise, skipped_existing, errors, wait=wait)
                    await asyncio.sleep(wait)

            prompt = _build_single_message_prompt(msg)
            try:
                result = await backend.extract(prompt, CoPUpdate)
                last_llm_call = time.perf_counter()
                result.extraction_method = "llm"
                label = _label_to_dict(msg, result, model)
                f.write(json.dumps(label, ensure_ascii=False) + "\n")
                f.flush()
                extracted += 1
            except Exception as e:
                error_label = {
                    "raw_line": msg.raw_line,
                    "channel": msg.channel,
                    "sender": msg.sender,
                    "timestamp": msg.timestamp.isoformat(),
                    "extracted_type": "none",
                    "extracted_entities": [],
                    "confidence": 0.0,
                    "extraction_method": "error",
                    "model": model,
                    "reasoning": f"Ask Sage error: {e}",
                    "source": "llm_judge",
                    "labeler": model,
                }
                f.write(json.dumps(error_label, ensure_ascii=False) + "\n")
                f.flush()
                errors += 1

            _print_progress(i + 1, total, extracted, skipped_noise, skipped_existing, errors)

    print(f"\n\nDone! Results saved to {output}")
    print(f"  Total messages: {total}")
    print(f"  LLM extractions: {extracted}")
    print(f"  Noise filtered: {skipped_noise}")
    print(f"  Skipped (existing): {skipped_existing}")
    print(f"  Errors: {errors}")


def main():
    parser = argparse.ArgumentParser(
        description="Generate silver labels from DASH 3 data using a high-quality LLM",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Generate labels with Groq
    python scripts/generate_silver_labels.py --api-key $GROQ_API_KEY --count 100

    # Use local Ollama (no rate limiting needed)
    python scripts/generate_silver_labels.py --url http://127.0.0.1:11434/v1 --model qwen3:30b-a3b --rate-delay 0

    # Filter channels and resume a partial run
    python scripts/generate_silver_labels.py --channels "#c2_coord,#fires" --count 500
        """,
    )
    parser.add_argument(
        "--data",
        default="data/dash3/23Sep_usaf_chat.zip",
        help="Path to DASH 3 data (zip, file, or directory). Default: data/dash3/23Sep_usaf_chat.zip",
    )
    parser.add_argument(
        "--url",
        default="https://api.groq.com/openai/v1",
        help="OpenAI-compatible API base URL. Default: Groq",
    )
    parser.add_argument(
        "--model",
        default="qwen/qwen3-32b",
        help="Model name. Default: qwen/qwen3-32b",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="API key (or set GROQ_API_KEY / OPENAI_API_KEY env var)",
    )
    parser.add_argument(
        "--output",
        default="data/labels/dash3_silver_labels.jsonl",
        help="Output JSONL path. Default: data/labels/dash3_silver_labels.jsonl",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=None,
        help="Limit to first N messages. Default: all",
    )
    parser.add_argument(
        "--rate-delay",
        type=float,
        default=2.0,
        help="Seconds between LLM calls for rate-limited APIs. Default: 2.0 (Groq free tier)",
    )
    parser.add_argument(
        "--channels",
        default=None,
        help='Comma-separated channel filter, e.g. "#c2_coord,#isr_reports"',
    )
    parser.add_argument(
        "--asksage",
        action="store_true",
        help="Use Ask Sage API instead of OpenAI-compatible endpoint",
    )
    parser.add_argument(
        "--asksage-email",
        default=None,
        help="Ask Sage account email (or set ASKSAGE_EMAIL env var)",
    )
    args = parser.parse_args()

    # Resolve API key
    api_key = args.api_key or os.environ.get("GROQ_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not args.asksage and not api_key:
        print("ERROR: No API key provided. Use --api-key or set GROQ_API_KEY / OPENAI_API_KEY env var.")
        sys.exit(1)

    # Ask Sage mode: resolve email + key
    if args.asksage:
        api_key = args.api_key or os.environ.get("ASKSAGE_API_KEY", "")
        asksage_email = args.asksage_email or os.environ.get("ASKSAGE_EMAIL", "")
        if not api_key or not asksage_email:
            print("ERROR: --asksage requires --api-key and --asksage-email (or env vars)")
            sys.exit(1)

    # Parse channels
    channels = None
    if args.channels:
        channels = [c.strip() for c in args.channels.split(",")]

    if args.asksage:
        asyncio.run(
            generate_labels_asksage(
                data_path=args.data,
                email=asksage_email,
                api_key=api_key,
                model=args.model or "claude-opus-4-6",
                output=args.output,
                count=args.count,
                rate_delay=args.rate_delay,
                channels=channels,
            )
        )
    else:
        asyncio.run(
            generate_labels(
                data_path=args.data,
                url=args.url,
                model=args.model,
                api_key=api_key,
                output=args.output,
                count=args.count,
                rate_delay=args.rate_delay,
                channels=channels,
            )
        )


if __name__ == "__main__":
    main()

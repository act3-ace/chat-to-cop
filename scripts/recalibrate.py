"""One-command recalibration: evaluate a model against labels and fit a CalibrationModel.

Loads all labels from data/labels/ via load_labels(), sends each labeled
message through the specified model, compares output to labels, fits a
CalibrationModel on the results, and saves it to data/calibration/<model>.json.

Usage:
    python scripts/recalibrate.py --url http://127.0.0.1:11434/v1 --model qwen2.5:7b

    # Use specific labels directory
    python scripts/recalibrate.py --labels-dir data/labels/ --model qwen2.5:7b

    # With API key for cloud endpoints
    python scripts/recalibrate.py --url https://api.groq.com/openai/v1 \\
        --model llama-3.3-70b-versatile --api-key $GROQ_API_KEY --rate-delay 2
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from chat_to_cop.backend.openai_compat import (
    DEFAULT_GLOSSARY,
    OpenAICompatibleBackend,
    RetryableError,
    build_system_prompt,
)
from chat_to_cop.calibration import CalibrationModel
from chat_to_cop.labels import load_labels
from chat_to_cop.models.cop_update import CoPUpdate
from chat_to_cop.models.messages import IRCMessage


def _extract_content_from_raw(raw_line: str) -> str:
    """Extract the message content from a raw log line."""
    m = re.match(r"^\[[\d:]+\]\s+#\S+\s+\S+?:\s*(.*)", raw_line)
    if m:
        return m.group(1)
    m = re.match(r"^\[[\d:]+\]\s+\S+?:\s*(.*)", raw_line)
    if m:
        return m.group(1)
    return raw_line


def _label_to_irc_message(label: dict) -> IRCMessage:
    """Reconstruct an IRCMessage from a label dict."""
    ts = label.get("timestamp", "2025-09-23T00:00:00+00:00")
    if isinstance(ts, str):
        try:
            timestamp = datetime.fromisoformat(ts)
        except ValueError:
            timestamp = datetime(2025, 9, 23, tzinfo=timezone.utc)
    else:
        timestamp = datetime(2025, 9, 23, tzinfo=timezone.utc)

    return IRCMessage(
        timestamp=timestamp,
        channel=label.get("channel", "#unknown"),
        sender=label.get("sender", "unknown"),
        content=_extract_content_from_raw(label.get("raw_line", "")),
        raw_line=label.get("raw_line", ""),
    )


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


def _get_expected_type(label: dict) -> str:
    """Get the expected update type from a label, trying both field names."""
    return label.get("expected_type") or label.get("extracted_type") or "none"


async def recalibrate(
    labels: list[dict],
    url: str,
    model: str,
    api_key: str,
    rate_delay: float,
    output_dir: str,
    n_bins: int,
) -> None:
    """Run recalibration: evaluate labels, fit calibration model, save."""
    # Filter out error/noise labels for calibration
    scorable = [lb for lb in labels if lb.get("extraction_method") not in ("error", "noise_filter")]
    if not scorable:
        print("ERROR: No scorable labels found (all are error or noise-filtered)")
        sys.exit(1)

    print(f"Recalibrating {model} against {len(scorable)} scorable labels (from {len(labels)} total)")
    if rate_delay > 0:
        est_min = len(scorable) * rate_delay / 60
        print(f"Rate delay: {rate_delay:.1f}s (~{est_min:.0f} min)")

    backend = OpenAICompatibleBackend(
        base_url=url,
        model=model,
        api_key=api_key,
        timeout=120.0,
        max_retries=3,
    )

    confidences: list[float] = []
    correct: list[bool] = []
    errors = 0
    last_llm_call = 0.0

    for i, label in enumerate(scorable):
        expected_type = _get_expected_type(label)
        msg = _label_to_irc_message(label)

        # Rate limiting
        if rate_delay > 0 and last_llm_call > 0:
            elapsed = time.perf_counter() - last_llm_call
            if elapsed < rate_delay:
                await asyncio.sleep(rate_delay - elapsed)

        prompt = _build_single_message_prompt(msg)
        retries = 0
        max_retries = 5
        result = None

        while True:
            try:
                result = await backend.extract(prompt, CoPUpdate)
                last_llm_call = time.perf_counter()
                break
            except RetryableError:
                retries += 1
                if retries > max_retries:
                    errors += 1
                    break
                backoff = 5 * (2 ** (retries - 1))
                print(f"\n  Rate limited (attempt {retries}/{max_retries}), backing off {backoff}s...")
                await asyncio.sleep(backoff)
            except Exception:
                errors += 1
                break

        if result is None:
            pct = (i + 1) / len(scorable) * 100
            print(f"\r  [{i + 1:>5d}/{len(scorable)}] {pct:5.1f}%  errors={errors}    ", end="", flush=True)
            continue

        is_correct = result.update_type.value == expected_type
        confidences.append(result.confidence)
        correct.append(is_correct)

        pct = (i + 1) / len(scorable) * 100
        correct_count = sum(correct)
        accuracy = correct_count / len(correct) * 100 if correct else 0
        print(
            f"\r  [{i + 1:>5d}/{len(scorable)}] {pct:5.1f}%  accuracy={accuracy:.1f}%  errors={errors}    ",
            end="",
            flush=True,
        )

    print()

    if not confidences:
        print("ERROR: No successful extractions to calibrate on")
        sys.exit(1)

    # Fit calibration model
    print(f"\nFitting calibration model ({n_bins} bins)...")

    # Before calibration: compute ECE with uncalibrated data
    pre_model = CalibrationModel(n_bins=n_bins)
    pre_model.fit(confidences, correct)
    pre_ece = pre_model.ece()

    print("\n--- BEFORE calibration ---")
    print(pre_model.reliability_diagram_ascii())

    # The calibration model IS the result of fitting — pre and post are the same
    # histogram binning. The value is that future predictions get remapped.
    cal_model = pre_model

    # Save
    safe_model_name = model.replace("/", "_").replace(":", "_")
    output_path = Path(output_dir) / f"{safe_model_name}.json"
    cal_model.save(output_path)
    print(f"\nCalibration model saved to {output_path}")

    # Summary
    accuracy = sum(correct) / len(correct) * 100
    print("\n=== Recalibration Summary ===")
    print(f"  Model:       {model}")
    print(f"  Labels:      {len(scorable)} scorable, {errors} errors")
    print(f"  Accuracy:    {accuracy:.1f}% ({sum(correct)}/{len(correct)})")
    print(f"  ECE:         {pre_ece:.4f}")
    print(f"  Saved to:    {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Recalibrate a model's confidence using ground truth labels",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python scripts/recalibrate.py --url http://127.0.0.1:11434/v1 --model qwen2.5:7b
    python scripts/recalibrate.py --url https://api.groq.com/openai/v1 \\
        --model llama-3.3-70b-versatile --api-key $GROQ_API_KEY --rate-delay 2
        """,
    )
    parser.add_argument(
        "--labels-dir",
        default="data/labels/",
        help="Directory of .jsonl label files. Default: data/labels/",
    )
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:11434/v1",
        help="OpenAI-compatible API base URL. Default: local Ollama",
    )
    parser.add_argument(
        "--model",
        default="qwen2.5:7b",
        help="Model to recalibrate. Default: qwen2.5:7b",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="API key (or set GROQ_API_KEY / OPENAI_API_KEY env var)",
    )
    parser.add_argument(
        "--rate-delay",
        type=float,
        default=0.0,
        help="Seconds between LLM calls. Default: 0 (local).",
    )
    parser.add_argument(
        "--output-dir",
        default="data/calibration/",
        help="Directory to save calibration model. Default: data/calibration/",
    )
    parser.add_argument(
        "--n-bins",
        type=int,
        default=10,
        help="Number of calibration bins. Default: 10",
    )
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("GROQ_API_KEY") or os.environ.get("OPENAI_API_KEY", "not-needed")

    labels = load_labels(args.labels_dir)
    if not labels:
        print(f"ERROR: No labels found in {args.labels_dir}")
        print("Run generate_silver_labels.py first or place JSONL files in the labels directory.")
        sys.exit(1)
    print(f"Loaded {len(labels)} labels from {args.labels_dir}")

    asyncio.run(
        recalibrate(
            labels=labels,
            url=args.url,
            model=args.model,
            api_key=api_key,
            rate_delay=args.rate_delay,
            output_dir=args.output_dir,
            n_bins=args.n_bins,
        )
    )


if __name__ == "__main__":
    main()

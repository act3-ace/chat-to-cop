"""Run a controlled replay test: N messages from DASH 3 typed chat channels.

Measures: extraction quality, latency, update type distribution, failure rate.

Usage:
    python scripts/replay_test.py --count 30
    python scripts/replay_test.py --count 50 --model qwen2.5:7b
"""

import argparse
import asyncio
import time
from collections import Counter
from pathlib import Path

from chat_to_cop.agent.channel_agent import ChannelAgent
from chat_to_cop.backend.openai_compat import OpenAICompatibleBackend
from chat_to_cop.ingestion.replay import parse_path

# Only typed chat channels — skip STT and vegas_internal for this test
TYPED_CHANNELS = {"#c2_coord", "#isr_reports", "#fires", "#jprc"}

DASH3_PATH = Path("data/dash3/23Sep_usaf_chat.zip")


async def run_replay_test(count: int, url: str, model: str):
    print(f"\n{'=' * 70}")
    print(f"REPLAY TEST: {model} @ {url}")
    print(f"Channels: {', '.join(sorted(TYPED_CHANNELS))}")
    print(f"Message limit: {count}")
    print(f"{'=' * 70}\n")

    backend = OpenAICompatibleBackend(base_url=url, model=model, timeout=120.0)
    agents: dict[str, ChannelAgent] = {}

    all_messages = parse_path(DASH3_PATH)
    typed_messages = [m for m in all_messages if m.channel in TYPED_CHANNELS]
    test_messages = typed_messages[:count]

    print(f"Total messages in dataset: {len(all_messages)}")
    print(f"Typed chat messages: {len(typed_messages)}")
    print(f"Testing with: {len(test_messages)}\n")

    # Stats
    type_counts: Counter = Counter()
    latencies: list[float] = []
    errors = 0
    filtered = 0
    extracted = 0

    for i, msg in enumerate(test_messages):
        if msg.channel not in agents:
            agents[msg.channel] = ChannelAgent(
                channel=msg.channel,
                backend=backend,
                use_speaker_models=False,  # Disable for speed in this test
            )

        start = time.perf_counter()
        updates = await agents[msg.channel].process_message(msg)
        elapsed = time.perf_counter() - start
        latencies.append(elapsed)

        if not updates:
            filtered += 1
            status = "FILTERED"
        elif updates[0].extraction_method == "error":
            errors += 1
            status = "ERROR"
        else:
            extracted += 1
            for u in updates:
                type_counts[u.update_type.value] += 1
            status = f"{updates[0].update_type.value} (conf={updates[0].confidence:.2f})"

        # Progress line
        print(
            f"[{i + 1:3d}/{len(test_messages)}] "
            f"{elapsed:5.1f}s | {msg.channel:15s} | {msg.sender:15s} | {status:30s} | {msg.content[:60]}"
        )

    # Summary
    print(f"\n{'=' * 70}")
    print("RESULTS")
    print(f"{'=' * 70}")
    print(f"Messages processed: {len(test_messages)}")
    print(f"  Extracted:  {extracted} ({extracted / len(test_messages) * 100:.0f}%)")
    print(f"  Filtered:   {filtered} ({filtered / len(test_messages) * 100:.0f}%)")
    print(f"  Errors:     {errors} ({errors / len(test_messages) * 100:.0f}%)")
    print()
    print("Update type distribution:")
    for utype, cnt in type_counts.most_common():
        print(f"  {utype:20s}: {cnt}")
    print()
    if latencies:
        latencies.sort()
        p50 = latencies[len(latencies) // 2]
        p95 = latencies[int(len(latencies) * 0.95)]
        print("Latency (seconds):")
        print(f"  p50: {p50:.1f}s")
        print(f"  p95: {p95:.1f}s")
        print(f"  max: {max(latencies):.1f}s")
        print(f"  avg: {sum(latencies) / len(latencies):.1f}s")
        print(f"  total: {sum(latencies):.0f}s ({sum(latencies) / 60:.1f} min)")
    print(f"{'=' * 70}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=30, help="Number of messages to process")
    parser.add_argument("--url", default="http://127.0.0.1:11434/v1")
    parser.add_argument("--model", default="qwen2.5:3b")
    args = parser.parse_args()

    if not DASH3_PATH.exists():
        print(f"DASH data not found: {DASH3_PATH}")
        return

    asyncio.run(run_replay_test(args.count, args.url, args.model))


if __name__ == "__main__":
    main()

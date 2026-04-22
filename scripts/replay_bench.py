"""Pre-merge replay benchmark: stratified sample against silver labels.

Issue #73 Part A.  Runs a stratified subset of silver labels through the
current extraction pipeline and writes a summary JSON that MR reviewers
can diff pre-vs-post for any prompt-touching change.

Usage:
    python scripts/replay_bench.py --model qwen2.5:7b --n 100
    python scripts/replay_bench.py --model qwen3:30b-a3b --n 50 \
        --silver-labels data/labels/dash3_silver_labels.jsonl

Output:
    results/replay_bench_<model>_<timestamp>.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from chat_to_cop.backend.openai_compat import (
    DEFAULT_GLOSSARY,
    OpenAICompatibleBackend,
    RetryableError,
    build_system_prompt,
)
from chat_to_cop.models.cop_update import CoPUpdate

sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_against_labels import (
    _entity_keys,
    _is_noise,
    _types_match_relaxed,
    label_to_irc_message,
    load_labels,
)


def stratified_sample(labels: list[dict], n: int, seed: int = 42) -> list[dict]:
    """Sample N labels proportionally by extracted_type."""
    rng = random.Random(seed)
    by_type: dict[str, list[dict]] = defaultdict(list)
    for lb in labels:
        by_type[lb.get("extracted_type", "none")].append(lb)

    total = len(labels)
    sampled: list[dict] = []
    remainder: list[tuple[str, list[dict]]] = []

    for utype, group in sorted(by_type.items()):
        quota = max(1, round(n * len(group) / total))
        quota = min(quota, len(group))
        picked = rng.sample(group, quota)
        sampled.extend(picked)
        leftover = [lb for lb in group if lb not in picked]
        remainder.append((utype, leftover))

    # Fill remaining slots if rounding left us short
    while len(sampled) < n:
        for utype, leftover in remainder:
            if leftover and len(sampled) < n:
                sampled.append(leftover.pop(rng.randrange(len(leftover))))
        if all(not lf for _, lf in remainder):
            break

    # Trim if rounding overshot
    sampled = sampled[:n]
    rng.shuffle(sampled)
    return sampled


def _build_single_message_prompt(msg) -> list[dict[str, str]]:
    system_prompt = build_system_prompt(glossary=DEFAULT_GLOSSARY)
    user_content = (
        f"EXTRACT FROM THIS MESSAGE ONLY:\n[{msg.timestamp.strftime('%H:%M:%S')}] {msg.sender}: {msg.content}"
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]


async def run_bench(
    labels: list[dict],
    url: str,
    model: str,
    api_key: str,
    num_ctx: int,
    timeout: float,
) -> dict:
    """Run the benchmark and return metrics dict."""
    backend = OpenAICompatibleBackend(
        base_url=url,
        model=model,
        api_key=api_key,
        timeout=timeout,
        max_retries=2,
        num_ctx=num_ctx,
    )

    total = len(labels)
    type_exact = 0
    entity_overlap_sum = 0.0
    entity_overlap_count = 0
    none_fp = 0  # predicted none when silver != none
    none_tp = 0  # predicted none when silver == none
    none_fn = 0  # predicted != none when silver == none
    latencies: list[float] = []
    prompt_chars: list[int] = []
    per_type_correct: Counter[str] = Counter()
    per_type_total: Counter[str] = Counter()
    errors = 0
    noise_skipped = 0

    print(f"\nBenchmark: {model} @ {url}, N={total}")
    print("-" * 60)

    for i, label in enumerate(labels):
        silver_type = label.get("extracted_type", "none")
        silver_entities = label.get("extracted_entities", [])
        msg = label_to_irc_message(label)
        per_type_total[silver_type] += 1

        if label.get("extraction_method") == "noise_filter" or _is_noise(msg.content):
            noise_skipped += 1
            if silver_type == "none":
                type_exact += 1
                per_type_correct["none"] += 1
                none_tp += 1
            else:
                none_fp += 1
            pct = (i + 1) / total * 100
            print(f"\r  [{i + 1:>4d}/{total}] {pct:5.1f}%  errors={errors}", end="", flush=True)
            continue

        prompt = _build_single_message_prompt(msg)
        prompt_chars.append(sum(len(m["content"]) for m in prompt))

        try:
            start = time.perf_counter()
            result = await backend.extract(prompt, CoPUpdate)
            elapsed = time.perf_counter() - start
            latencies.append(elapsed)
        except (RetryableError, Exception):
            errors += 1
            pct = (i + 1) / total * 100
            print(f"\r  [{i + 1:>4d}/{total}] {pct:5.1f}%  errors={errors}", end="", flush=True)
            continue

        cand_type = result.update_type.value
        cand_entities = [e.model_dump(exclude_none=True, exclude_defaults=True) for e in result.entities]

        if cand_type == silver_type:
            type_exact += 1
            per_type_correct[silver_type] += 1
        elif _types_match_relaxed(cand_type, silver_type):
            per_type_correct[silver_type] += 1

        # none FP/FN tracking
        if cand_type == "none" and silver_type != "none":
            none_fp += 1
        if cand_type != "none" and silver_type == "none":
            none_fn += 1
        if cand_type == "none" and silver_type == "none":
            none_tp += 1

        silver_keys = _entity_keys(silver_entities)
        cand_keys = _entity_keys(cand_entities)
        if silver_keys or cand_keys:
            union = silver_keys | cand_keys
            intersection = silver_keys & cand_keys
            entity_overlap_sum += len(intersection) / len(union) if union else 1.0
            entity_overlap_count += 1

        pct = (i + 1) / total * 100
        print(f"\r  [{i + 1:>4d}/{total}] {pct:5.1f}%  errors={errors}", end="", flush=True)

    print()

    # Compute metrics
    type_accuracy = type_exact / total if total else 0
    entity_jaccard = entity_overlap_sum / entity_overlap_count if entity_overlap_count else 0
    none_predicted = none_tp + none_fp
    none_fpr = none_fp / none_predicted if none_predicted else 0
    mean_latency_ms = (sum(latencies) / len(latencies) * 1000) if latencies else 0
    mean_prompt_chars = (sum(prompt_chars) / len(prompt_chars)) if prompt_chars else 0

    metrics = {
        "model": model,
        "url": url,
        "n": total,
        "num_ctx": num_ctx,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "prompt_char_count": int(mean_prompt_chars),
        "prompt_approx_tokens": int(mean_prompt_chars / 4),
        "type_accuracy": round(type_accuracy, 4),
        "entity_overlap_jaccard": round(entity_jaccard, 4),
        "none_false_positive_rate": round(none_fpr, 4),
        "mean_latency_ms": round(mean_latency_ms, 1),
        "errors": errors,
        "noise_skipped": noise_skipped,
        "per_type": {
            utype: {
                "correct": per_type_correct[utype],
                "total": per_type_total[utype],
                "rate": round(per_type_correct[utype] / per_type_total[utype], 4) if per_type_total[utype] else 0,
            }
            for utype in sorted(per_type_total)
        },
    }
    if latencies:
        s = sorted(latencies)
        metrics["p50_latency_ms"] = round(s[len(s) // 2] * 1000, 1)
        metrics["p95_latency_ms"] = round(s[int(len(s) * 0.95)] * 1000, 1)

    return metrics


def main():
    parser = argparse.ArgumentParser(description="Pre-merge replay benchmark (issue #73)")
    parser.add_argument("--model", default="qwen2.5:7b")
    parser.add_argument("--url", default="http://127.0.0.1:11434/v1")
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--silver-labels", default="data/labels/dash3_silver_labels.jsonl")
    parser.add_argument("--n", type=int, default=100, help="Sample size (stratified by type)")
    parser.add_argument("--num-ctx", type=int, default=8192)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output-dir", default="results")
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("GROQ_API_KEY") or os.environ.get("OPENAI_API_KEY", "not-needed")

    labels_path = Path(args.silver_labels)
    if not labels_path.exists():
        print(f"ERROR: Silver labels not found: {labels_path}")
        sys.exit(1)

    all_labels = load_labels(str(labels_path))
    print(f"Loaded {len(all_labels)} labels from {labels_path}")

    sample = stratified_sample(all_labels, args.n, seed=args.seed)
    dist = Counter(lb.get("extracted_type", "none") for lb in sample)
    print(f"Stratified sample: {len(sample)} labels across {len(dist)} types")
    for utype, count in sorted(dist.items()):
        print(f"  {utype:20s} {count}")

    metrics = asyncio.run(
        run_bench(
            labels=sample,
            url=args.url,
            model=args.model,
            api_key=api_key,
            num_ctx=args.num_ctx,
            timeout=args.timeout,
        )
    )

    # Write results
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_model = args.model.replace("/", "_").replace(":", "_")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"replay_bench_{safe_model}_{ts}.json"
    with open(out_path, "w") as f:
        json.dump(metrics, f, indent=2)

    print(f"\nResults written to {out_path}")
    print(f"\n  type_accuracy:           {metrics['type_accuracy']:.1%}")
    print(f"  entity_overlap_jaccard:  {metrics['entity_overlap_jaccard']:.1%}")
    print(f"  none_false_positive_rate:{metrics['none_false_positive_rate']:.1%}")
    print(f"  mean_latency_ms:         {metrics['mean_latency_ms']:.0f}")
    print(f"  prompt_approx_tokens:    {metrics['prompt_approx_tokens']}")


if __name__ == "__main__":
    main()

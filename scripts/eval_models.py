"""Model evaluation harness: systematic comparison across LLM endpoints.

Runs labeled synthetic data (or real data with manual labels) through the
extraction pipeline and computes precision, recall, and F1 per update type.

Works with any OpenAI-compatible endpoint: Ollama, Groq, OpenAI, Gemini, etc.

Usage:
    # Single model
    python scripts/eval_models.py --url https://api.groq.com/openai/v1 --model qwen/qwen3-32b --count 50

    # Compare multiple models (runs sequentially)
    python scripts/eval_models.py --compare --count 50

    # Use local Ollama
    python scripts/eval_models.py --url http://localhost:11434/v1 --model qwen2.5:3b --count 30

Environment:
    OPENAI_API_KEY — API key for the endpoint (set for Groq, OpenAI, etc.)
    GROQ_API_KEY — alternative key source for Groq specifically
"""

from __future__ import annotations

import argparse
import asyncio
import os
import time
from collections import Counter
from dataclasses import dataclass, field

from chat_to_cop.agent.channel_agent import ChannelAgent
from chat_to_cop.backend.openai_compat import OpenAICompatibleBackend
from chat_to_cop.models.cop_update import CoPUpdate, UpdateType
from chat_to_cop.models.messages import IRCMessage
from chat_to_cop.testing.generator import generate_messages

# Models to compare on Groq's free tier
GROQ_MODELS = [
    "qwen/qwen3-32b",
    "llama-3.3-70b-versatile",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "llama-3.1-8b-instant",
]


@dataclass
class ExtractionResult:
    """Result of extracting from a single message."""

    message: IRCMessage
    expected: CoPUpdate | None  # Ground truth (None = noise)
    got: list[CoPUpdate]  # What the pipeline produced
    latency: float  # Seconds
    error: bool = False


@dataclass
class EvalMetrics:
    """Aggregated metrics for one model run."""

    model: str
    total: int = 0
    true_positives: int = 0  # Correct extraction (right type)
    false_positives: int = 0  # Extracted something when shouldn't have (noise -> update)
    false_negatives: int = 0  # Missed extraction (update -> filtered/error)
    true_negatives: int = 0  # Correctly filtered noise
    errors: int = 0  # Schema validation failures
    type_correct: Counter = field(default_factory=Counter)  # Per-type correct count
    type_total: Counter = field(default_factory=Counter)  # Per-type total count
    latencies: list[float] = field(default_factory=list)

    @property
    def precision(self) -> float:
        denom = self.true_positives + self.false_positives
        return self.true_positives / denom if denom > 0 else 0.0

    @property
    def recall(self) -> float:
        denom = self.true_positives + self.false_negatives
        return self.true_positives / denom if denom > 0 else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) > 0 else 0.0

    @property
    def error_rate(self) -> float:
        return self.errors / self.total if self.total > 0 else 0.0

    @property
    def p50_latency(self) -> float:
        if not self.latencies:
            return 0.0
        s = sorted(self.latencies)
        return s[len(s) // 2]

    @property
    def p95_latency(self) -> float:
        if not self.latencies:
            return 0.0
        s = sorted(self.latencies)
        return s[int(len(s) * 0.95)]

    def summary(self) -> str:
        lines = [
            f"Model: {self.model}",
            f"  Messages: {self.total}",
            f"  Precision: {self.precision:.2f}  Recall: {self.recall:.2f}  F1: {self.f1:.2f}",
            f"  TP={self.true_positives} FP={self.false_positives} "
            f"FN={self.false_negatives} TN={self.true_negatives} Err={self.errors}",
            f"  Error rate: {self.error_rate:.1%}",
            f"  Latency: p50={self.p50_latency:.2f}s p95={self.p95_latency:.2f}s",
        ]
        if self.type_total:
            lines.append("  Per-type recall:")
            for utype in sorted(self.type_total.keys()):
                correct = self.type_correct[utype]
                total = self.type_total[utype]
                pct = correct / total if total > 0 else 0
                lines.append(f"    {utype:20s}: {correct}/{total} ({pct:.0%})")
        return "\n".join(lines)


def _score(expected: CoPUpdate | None, got: list[CoPUpdate]) -> str:
    """Score an extraction result. Returns: TP, FP, FN, TN, or ERROR."""
    if not got:
        # Nothing extracted
        if expected is None:
            return "TN"  # Correctly filtered noise
        return "FN"  # Missed a real update

    update = got[0]

    if update.extraction_method == "error":
        return "ERROR"

    if expected is None:
        # We extracted something but it was noise
        if update.update_type == UpdateType.NONE:
            return "TN"  # Model correctly said "none"
        return "FP"  # False positive

    # We have both expected and extracted — check type match
    if update.update_type == expected.update_type:
        return "TP"
    # Close enough? status_change and sitrep are related
    if {update.update_type, expected.update_type} <= {UpdateType.STATUS_CHANGE, UpdateType.SITREP}:
        return "TP"  # Give credit for sitrep/status_change confusion
    return "FP"  # Wrong type


async def eval_model(
    url: str,
    model: str,
    api_key: str,
    messages: list[IRCMessage],
    ground_truth: list[CoPUpdate | None],
    verbose: bool = False,
) -> EvalMetrics:
    """Evaluate a single model against labeled data."""
    backend = OpenAICompatibleBackend(base_url=url, model=model, api_key=api_key, timeout=60.0, max_retries=3)
    agents: dict[str, ChannelAgent] = {}
    metrics = EvalMetrics(model=model)

    for i, (msg, expected) in enumerate(zip(messages, ground_truth)):
        if msg.channel not in agents:
            agents[msg.channel] = ChannelAgent(channel=msg.channel, backend=backend, use_speaker_models=False)

        start = time.perf_counter()
        updates = await agents[msg.channel].process_message(msg)
        elapsed = time.perf_counter() - start

        metrics.total += 1
        metrics.latencies.append(elapsed)

        # Score
        score = _score(expected, updates)
        if score == "TP":
            metrics.true_positives += 1
        elif score == "FP":
            metrics.false_positives += 1
        elif score == "FN":
            metrics.false_negatives += 1
        elif score == "TN":
            metrics.true_negatives += 1
        elif score == "ERROR":
            metrics.errors += 1

        # Per-type tracking
        if expected is not None:
            utype = expected.update_type.value
            metrics.type_total[utype] += 1
            if score == "TP":
                metrics.type_correct[utype] += 1

        if verbose:
            got_str = "FILTERED" if not updates else updates[0].update_type.value
            exp_str = "noise" if expected is None else expected.update_type.value
            marker = "OK" if score in ("TP", "TN") else "XX" if score in ("FP", "FN") else "!!"
            print(
                f"  [{i + 1:3d}] {marker} {elapsed:5.1f}s expected={exp_str:15s} got={got_str:15s} | {msg.content[:50]}"
            )

    return metrics


async def run_eval(
    url: str,
    model: str,
    api_key: str,
    count: int,
    verbose: bool,
):
    """Run evaluation for a single model."""
    print(f"\n{'=' * 70}")
    print(f"EVALUATING: {model} @ {url}")
    print(f"Messages: {count} synthetic (30% noise)")
    print(f"{'=' * 70}\n")

    messages, ground_truth = generate_messages(count=count, noise_ratio=0.3)
    metrics = await eval_model(url, model, api_key, messages, ground_truth, verbose=verbose)
    print(f"\n{metrics.summary()}")
    return metrics


async def run_comparison(url: str, api_key: str, models: list[str], count: int, verbose: bool):
    """Compare multiple models on the same test data."""
    # Generate data once, share across models
    messages, ground_truth = generate_messages(count=count, noise_ratio=0.3)

    all_metrics: list[EvalMetrics] = []

    for model in models:
        print(f"\n{'=' * 70}")
        print(f"EVALUATING: {model}")
        print(f"{'=' * 70}")

        m = await eval_model(url, model, api_key, messages, ground_truth, verbose=verbose)
        all_metrics.append(m)
        print(f"\n{m.summary()}")

    # Comparison table
    print(f"\n{'=' * 70}")
    print("COMPARISON SUMMARY")
    print(f"{'=' * 70}")
    print(f"{'Model':45s} {'Prec':>6s} {'Rec':>6s} {'F1':>6s} {'Err%':>6s} {'p50':>6s} {'p95':>6s}")
    print("-" * 81)
    for m in sorted(all_metrics, key=lambda x: x.f1, reverse=True):
        print(
            f"{m.model:45s} {m.precision:6.2f} {m.recall:6.2f} {m.f1:6.2f} "
            f"{m.error_rate:5.1%} {m.p50_latency:5.2f}s {m.p95_latency:5.2f}s"
        )

    return all_metrics


def main():
    parser = argparse.ArgumentParser(description="Evaluate LLM extraction quality")
    parser.add_argument("--url", default="https://api.groq.com/openai/v1")
    parser.add_argument("--model", default="qwen/qwen3-32b")
    parser.add_argument("--count", type=int, default=50, help="Number of test messages")
    parser.add_argument("--compare", action="store_true", help="Compare all Groq models")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show per-message results")
    args = parser.parse_args()

    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("GROQ_API_KEY", "not-needed")

    if args.compare:
        asyncio.run(run_comparison(args.url, api_key, GROQ_MODELS, args.count, args.verbose))
    else:
        asyncio.run(run_eval(args.url, args.model, api_key, args.count, args.verbose))


if __name__ == "__main__":
    main()

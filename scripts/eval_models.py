"""Model evaluation harness: systematic comparison across LLM endpoints.

Runs labeled synthetic data (or real data with manual labels) through the
extraction pipeline and computes precision, recall, and F1 per update type
and per entity field.

Works with any OpenAI-compatible endpoint: Ollama, Groq, OpenAI, Gemini, etc.

Usage:
    # Single model
    python scripts/eval_models.py --url https://api.groq.com/openai/v1 --model qwen/qwen3-32b --count 50

    # Compare multiple models (runs sequentially)
    python scripts/eval_models.py --compare --count 50

    # Use local Ollama
    python scripts/eval_models.py --url http://127.0.0.1:11434/v1 --model qwen2.5:3b --count 30

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
from chat_to_cop.calibration import CalibrationModel
from chat_to_cop.models.cop_update import CoPUpdate, EntityUpdate, UpdateType
from chat_to_cop.models.messages import IRCMessage
from chat_to_cop.testing.generator import generate_messages

# Models to compare on Groq's free tier
GROQ_MODELS = [
    "qwen/qwen3-32b",
    "llama-3.3-70b-versatile",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "llama-3.1-8b-instant",
]


# Fields to compare for entity-level scoring. Only fields with non-None
# values in the ground truth are checked.
ENTITY_SCORED_FIELDS = (
    "operational_status",
    "fuel_state",
    "weapon_type",
    "affiliation",
    "bearing",
    "range_nm",
    "platform_type",
    "weapon_qty_launched",
    "weapon_qty_remaining",
    "subsystem_status",
)


def _entity_key(entity: EntityUpdate) -> str | None:
    """Return a normalised matching key for an entity (track_number or callsign).

    Returns None if the entity has neither identifier.
    """
    if entity.track_number:
        return entity.track_number.strip().upper()
    if entity.callsign:
        return entity.callsign.strip().upper()
    return None


@dataclass
class EntityScoreResult:
    """Result of matching extracted entities against ground truth for one message."""

    matched: int = 0  # Entities matched by key (hit in both expected and got)
    expected_total: int = 0  # Total ground truth entities with a key
    extracted_total: int = 0  # Total extracted entities with a key
    field_correct: Counter = field(default_factory=Counter)  # Per-field correct counts
    field_total: Counter = field(default_factory=Counter)  # Per-field comparison counts


def _score_entities(expected: CoPUpdate | None, got: list[CoPUpdate]) -> EntityScoreResult:
    """Compare extracted entities against ground truth entities field-by-field.

    Matching strategy:
    - Build a lookup of expected entities keyed by normalised track_number or callsign.
    - For each extracted entity, find the matching expected entity by key.
    - For matched pairs, compare each scored field that has a non-None expected value.
    """
    result = EntityScoreResult()

    if expected is None or not expected.entities:
        # No ground truth entities to compare against.
        # Count any extracted entities as unmatched extractions (for precision).
        if got:
            for update in got:
                for ent in update.entities:
                    if _entity_key(ent) is not None:
                        result.extracted_total += 1
        return result

    # Build expected lookup: key -> EntityUpdate
    expected_by_key: dict[str, EntityUpdate] = {}
    for ent in expected.entities:
        key = _entity_key(ent)
        if key is not None:
            expected_by_key[key] = ent
            result.expected_total += 1

    if not got:
        return result

    # Collect all extracted entities
    extracted_entities: list[EntityUpdate] = []
    for update in got:
        extracted_entities.extend(update.entities)

    seen_keys: set[str] = set()
    for ext_ent in extracted_entities:
        ext_key = _entity_key(ext_ent)
        if ext_key is None:
            continue
        result.extracted_total += 1

        # Match against expected
        exp_ent = expected_by_key.get(ext_key)
        if exp_ent is None:
            continue

        # Avoid double-counting if the model extracts the same entity twice
        if ext_key in seen_keys:
            continue
        seen_keys.add(ext_key)

        result.matched += 1

        # Compare scored fields
        for fname in ENTITY_SCORED_FIELDS:
            exp_val = getattr(exp_ent, fname, None)
            if exp_val is None:
                continue  # Don't score fields that aren't set in ground truth
            result.field_total[fname] += 1
            ext_val = getattr(ext_ent, fname, None)
            if _field_match(exp_val, ext_val):
                result.field_correct[fname] += 1

    return result


def _field_match(expected: object, extracted: object) -> bool:
    """Check if an extracted field value matches the expected value.

    String comparison is case-insensitive with whitespace stripped.
    Numeric comparison allows a small tolerance for floats.
    """
    if expected is None:
        return True  # Nothing expected, always passes
    if extracted is None:
        return False  # Expected something, got nothing

    if isinstance(expected, str) and isinstance(extracted, str):
        return expected.strip().upper() == extracted.strip().upper()

    if isinstance(expected, (int, float)) and isinstance(extracted, (int, float)):
        # Exact match for ints, tolerance for floats
        if isinstance(expected, int) and isinstance(extracted, int):
            return expected == extracted
        return abs(float(expected) - float(extracted)) < 0.5

    return expected == extracted


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
    # Entity-level metrics
    entity_matched: int = 0  # Entities correctly matched by key
    entity_expected: int = 0  # Total ground truth entities
    entity_extracted: int = 0  # Total extracted entities
    entity_field_correct: Counter = field(default_factory=Counter)  # Per-field correct
    entity_field_total: Counter = field(default_factory=Counter)  # Per-field total
    # Calibration tracking: (confidence, correct) pairs for every scored extraction
    calibration_pairs: list[tuple[float, bool]] = field(default_factory=list)

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
    def entity_precision(self) -> float:
        return self.entity_matched / self.entity_extracted if self.entity_extracted > 0 else 0.0

    @property
    def entity_recall(self) -> float:
        return self.entity_matched / self.entity_expected if self.entity_expected > 0 else 0.0

    @property
    def entity_f1(self) -> float:
        p, r = self.entity_precision, self.entity_recall
        return 2 * p * r / (p + r) if (p + r) > 0 else 0.0

    @property
    def entity_field_accuracy(self) -> float:
        total = sum(self.entity_field_total.values())
        correct = sum(self.entity_field_correct.values())
        return correct / total if total > 0 else 0.0

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
            "  Update-type scoring:",
            f"    Precision: {self.precision:.2f}  Recall: {self.recall:.2f}  F1: {self.f1:.2f}",
            f"    TP={self.true_positives} FP={self.false_positives} "
            f"FN={self.false_negatives} TN={self.true_negatives} Err={self.errors}",
            "  Entity-level scoring:",
            f"    Precision: {self.entity_precision:.2f}  Recall: {self.entity_recall:.2f}  F1: {self.entity_f1:.2f}",
            f"    Matched={self.entity_matched} Expected={self.entity_expected} Extracted={self.entity_extracted}",
            f"    Field accuracy: {self.entity_field_accuracy:.0%}",
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
        if self.entity_field_total:
            lines.append("  Per-field accuracy:")
            for fname in sorted(self.entity_field_total.keys()):
                correct = self.entity_field_correct[fname]
                total = self.entity_field_total[fname]
                pct = correct / total if total > 0 else 0
                lines.append(f"    {fname:25s}: {correct}/{total} ({pct:.0%})")
        # Calibration summary
        if self.calibration_pairs:
            cal = CalibrationModel(n_bins=10)
            confs = [c for c, _ in self.calibration_pairs]
            outcomes = [o for _, o in self.calibration_pairs]
            cal.fit(confs, outcomes)
            lines.append("")
            lines.append("  Confidence calibration:")
            lines.append(f"    ECE = {cal.ece():.4f}")
            lines.append("")
            # Indent the reliability diagram
            for diagram_line in cal.reliability_diagram_ascii(width=40).split("\n"):
                lines.append(f"    {diagram_line}")
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
    rate_delay: float = 0.0,
) -> EvalMetrics:
    """Evaluate a single model against labeled data.

    Args:
        rate_delay: Seconds to wait between LLM calls (for rate-limited APIs).
            Groq free tier: use 18.0 (6000 tokens/min / ~1700 tokens per call = ~3.5/min).
            Paid APIs or local: use 0.0.
    """
    backend = OpenAICompatibleBackend(base_url=url, model=model, api_key=api_key, timeout=120.0, max_retries=3)
    agents: dict[str, ChannelAgent] = {}
    metrics = EvalMetrics(model=model)
    last_llm_call = 0.0

    for i, (msg, expected) in enumerate(zip(messages, ground_truth)):
        if msg.channel not in agents:
            agents[msg.channel] = ChannelAgent(channel=msg.channel, backend=backend, use_speaker_models=False)

        # Rate limiting — wait between LLM calls (noise is pre-filtered, no delay needed)
        if rate_delay > 0:
            elapsed_since_last = time.perf_counter() - last_llm_call
            if elapsed_since_last < rate_delay:
                await asyncio.sleep(rate_delay - elapsed_since_last)

        start = time.perf_counter()
        updates = await agents[msg.channel].process_message(msg)
        elapsed = time.perf_counter() - start

        # Track when we last made an LLM call (filtered messages don't count)
        if updates and updates[0].extraction_method != "error":
            last_llm_call = time.perf_counter()

        metrics.total += 1
        metrics.latencies.append(elapsed)

        # Score — update-type level
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

        # Score — entity level (only when we have ground truth entities)
        ent_result = _score_entities(expected, updates)
        metrics.entity_matched += ent_result.matched
        metrics.entity_expected += ent_result.expected_total
        metrics.entity_extracted += ent_result.extracted_total
        metrics.entity_field_correct += ent_result.field_correct
        metrics.entity_field_total += ent_result.field_total

        # Calibration tracking — record (confidence, correct) for every non-error extraction
        if updates and updates[0].extraction_method != "error":
            conf = updates[0].confidence
            is_correct = score in ("TP", "TN")
            metrics.calibration_pairs.append((conf, is_correct))

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
    rate_delay: float = 0.0,
    save_calibration: str | None = None,
):
    """Run evaluation for a single model."""
    print(f"\n{'=' * 70}")
    print(f"EVALUATING: {model} @ {url}")
    print(f"Messages: {count} synthetic (30% noise)")
    if rate_delay > 0:
        est_minutes = count * rate_delay / 60 * 0.7  # ~70% hit LLM, rest filtered
        print(f"Rate delay: {rate_delay:.0f}s between LLM calls (~{est_minutes:.0f} min estimated)")
    print(f"{'=' * 70}\n")

    messages, ground_truth = generate_messages(count=count, noise_ratio=0.3)
    metrics = await eval_model(url, model, api_key, messages, ground_truth, verbose=verbose, rate_delay=rate_delay)
    print(f"\n{metrics.summary()}")

    # Save calibration model if requested
    if save_calibration and metrics.calibration_pairs:
        cal = CalibrationModel(n_bins=10)
        confs = [c for c, _ in metrics.calibration_pairs]
        outcomes = [o for _, o in metrics.calibration_pairs]
        cal.fit(confs, outcomes)
        cal.save(save_calibration)
        print(f"\nCalibration model saved to {save_calibration}")

    return metrics


async def run_comparison(url: str, api_key: str, models: list[str], count: int, verbose: bool, rate_delay: float = 0.0):
    """Compare multiple models on the same test data."""
    messages, ground_truth = generate_messages(count=count, noise_ratio=0.3)

    all_metrics: list[EvalMetrics] = []

    for model in models:
        print(f"\n{'=' * 70}")
        print(f"EVALUATING: {model}")
        if rate_delay > 0:
            est_minutes = count * rate_delay / 60 * 0.7
            print(f"Rate delay: {rate_delay:.0f}s (~{est_minutes:.0f} min)")
        print(f"{'=' * 70}")

        m = await eval_model(url, model, api_key, messages, ground_truth, verbose=verbose, rate_delay=rate_delay)
        all_metrics.append(m)
        print(f"\n{m.summary()}")

    # Comparison table
    print(f"\n{'=' * 70}")
    print("COMPARISON SUMMARY")
    print(f"{'=' * 70}")
    header = (
        f"{'Model':40s} {'Prec':>5s} {'Rec':>5s} {'F1':>5s}"
        f" {'EntP':>5s} {'EntR':>5s} {'EntF1':>5s} {'FldA':>5s}"
        f" {'Err%':>5s} {'p50':>6s} {'p95':>6s}"
    )
    print(header)
    print("-" * len(header))
    for m in sorted(all_metrics, key=lambda x: x.f1, reverse=True):
        print(
            f"{m.model:40s} {m.precision:5.2f} {m.recall:5.2f} {m.f1:5.2f}"
            f" {m.entity_precision:5.2f} {m.entity_recall:5.2f} {m.entity_f1:5.2f}"
            f" {m.entity_field_accuracy:5.2f}"
            f" {m.error_rate:4.1%} {m.p50_latency:5.2f}s {m.p95_latency:5.2f}s"
        )

    return all_metrics


def main():
    parser = argparse.ArgumentParser(description="Evaluate LLM extraction quality")
    parser.add_argument("--url", default="https://api.groq.com/openai/v1")
    parser.add_argument("--model", default="qwen/qwen3-32b")
    parser.add_argument("--count", type=int, default=50, help="Number of test messages")
    parser.add_argument("--compare", action="store_true", help="Compare all Groq models")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show per-message results")
    parser.add_argument(
        "--rate-delay",
        type=float,
        default=0.0,
        help="Seconds between LLM calls for rate-limited APIs. Groq free: use 18. Local/paid: use 0.",
    )
    parser.add_argument(
        "--save-calibration",
        type=str,
        default=None,
        help="Path to save calibration model JSON after evaluation (e.g., calibration.json)",
    )
    args = parser.parse_args()

    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("GROQ_API_KEY", "not-needed")

    if args.compare:
        asyncio.run(run_comparison(args.url, api_key, GROQ_MODELS, args.count, args.verbose, args.rate_delay))
    else:
        asyncio.run(
            run_eval(args.url, args.model, api_key, args.count, args.verbose, args.rate_delay, args.save_calibration)
        )


if __name__ == "__main__":
    main()

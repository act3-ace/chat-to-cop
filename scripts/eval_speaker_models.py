"""Evaluate speaker model impact on extraction quality (RQ1).

Compares extraction quality WITH vs WITHOUT speaker models against Opus
silver labels. This is the core RQ1 experiment: does online speaker model
learning improve world-state extraction from military chat?

The A/B experiment:
    # Run A: with speaker models (default)
    python -m chat_to_cop.replay data/dash3/23Sep_usaf_chat.zip \
        --url http://127.0.0.1:11434/v1 --model qwen2.5:7b-8k \
        --db /tmp/replay_with_speakers.db

    # Run B: without speaker models
    CHAT_TO_COP_USE_SPEAKER_MODELS=false python -m chat_to_cop.replay data/dash3/23Sep_usaf_chat.zip \
        --url http://127.0.0.1:11434/v1 --model qwen2.5:7b-8k \
        --db /tmp/replay_without_speakers.db

    # Compare
    python scripts/eval_speaker_models.py \
        --labels data/labels/dash3_silver_labels_opus.jsonl \
        --with-speakers /tmp/replay_with_speakers.db \
        --without-speakers /tmp/replay_without_speakers.db

Metrics:
    - Type match rate (exact + relaxed) against Opus labels
    - Entity overlap (Jaccard similarity on callsigns/track numbers)
    - Per-speaker accuracy over time (learning curve)
    - Noise detection accuracy
    - Per-type breakdown
    - Confidence correlation with Opus labels
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import aiosqlite

from chat_to_cop.models.cop_update import CoPUpdate

# ---------------------------------------------------------------------------
# Shared metric helpers (reusable outside this script)
# ---------------------------------------------------------------------------

# Update types considered equivalent for relaxed matching
_RELAXED_EQUIVALENTS = [
    {"status_change", "sitrep"},
]


def types_match_relaxed(a: str, b: str) -> bool:
    """Check if two update types match, allowing related types."""
    if a == b:
        return True
    for group in _RELAXED_EQUIVALENTS:
        if a in group and b in group:
            return True
    return False


def normalize_entity_key(s: str) -> str:
    """Normalize a callsign or track number for matching."""
    return s.strip().upper().replace(" ", "").replace("-", "").replace("_", "")


def entity_keys_from_dicts(entities: list[dict]) -> set[str]:
    """Extract normalized matching keys from entity dicts."""
    keys: set[str] = set()
    for ent in entities:
        tn = ent.get("track_number")
        cs = ent.get("callsign")
        if tn:
            keys.add(normalize_entity_key(tn))
        if cs:
            keys.add(normalize_entity_key(cs))
    return keys


def entity_keys_from_cop_update(update: CoPUpdate) -> set[str]:
    """Extract normalized matching keys from a CoPUpdate's entities."""
    keys: set[str] = set()
    for ent in update.entities:
        if ent.track_number:
            keys.add(normalize_entity_key(ent.track_number))
        if ent.callsign:
            keys.add(normalize_entity_key(ent.callsign))
    return keys


def jaccard_similarity(set_a: set[str], set_b: set[str]) -> float:
    """Compute Jaccard similarity between two sets. Returns 1.0 if both empty."""
    if not set_a and not set_b:
        return 1.0
    union = set_a | set_b
    if not union:
        return 1.0
    return len(set_a & set_b) / len(union)


def pearson_correlation(pairs: list[tuple[float, float]]) -> float:
    """Compute Pearson correlation between two series."""
    n = len(pairs)
    if n < 3:
        return 0.0
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    denom = (var_x * var_y) ** 0.5
    if denom == 0:
        return 0.0
    return cov / denom


# ---------------------------------------------------------------------------
# Label loading
# ---------------------------------------------------------------------------


def load_labels(path: str) -> list[dict]:
    """Load silver labels from JSONL file."""
    labels = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                labels.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return labels


# ---------------------------------------------------------------------------
# Replay database loading
# ---------------------------------------------------------------------------


async def load_replay_updates(db_path: str) -> list[CoPUpdate]:
    """Load all CoPUpdates from a replay SQLite database."""
    db = await aiosqlite.connect(db_path)
    db.row_factory = aiosqlite.Row
    try:
        cursor = await db.execute("SELECT data_json FROM updates ORDER BY timestamp ASC")
        rows = await cursor.fetchall()
        updates = []
        for row in rows:
            try:
                updates.append(CoPUpdate.model_validate_json(row["data_json"]))
            except Exception:
                continue
        return updates
    finally:
        await db.close()


def index_updates_by_message(updates: list[CoPUpdate]) -> dict[str, CoPUpdate]:
    """Index CoPUpdates by their source_message for matching against labels.

    Uses the raw_line from the label (which is stored as source_message in the
    replay DB) as the key. If multiple updates share the same source_message,
    the last one wins (later extractions may be more refined).
    """
    index: dict[str, CoPUpdate] = {}
    for update in updates:
        if update.source_message:
            index[update.source_message.strip()] = update
    return index


# ---------------------------------------------------------------------------
# Per-run scoring against labels
# ---------------------------------------------------------------------------


@dataclass
class RunMetrics:
    """Metrics for a single replay run compared against Opus labels."""

    name: str
    total: int = 0
    matched: int = 0  # labels that had a corresponding extraction
    type_exact: int = 0
    type_relaxed: int = 0
    entity_overlap_sum: float = 0.0
    entity_overlap_count: int = 0
    noise_correct: int = 0
    noise_total: int = 0
    confidence_pairs: list[tuple[float, float]] = field(default_factory=list)
    per_type_correct: Counter = field(default_factory=Counter)
    per_type_total: Counter = field(default_factory=Counter)
    # Per-speaker tracking for learning curve
    per_speaker_correct: dict[str, list[bool]] = field(default_factory=lambda: defaultdict(list))

    @property
    def type_exact_rate(self) -> float:
        return self.type_exact / self.total if self.total > 0 else 0.0

    @property
    def type_relaxed_rate(self) -> float:
        return self.type_relaxed / self.total if self.total > 0 else 0.0

    @property
    def entity_overlap_avg(self) -> float:
        return self.entity_overlap_sum / self.entity_overlap_count if self.entity_overlap_count > 0 else 0.0

    @property
    def noise_accuracy(self) -> float:
        return self.noise_correct / self.noise_total if self.noise_total > 0 else 0.0

    @property
    def confidence_corr(self) -> float:
        return pearson_correlation(self.confidence_pairs) if len(self.confidence_pairs) > 2 else 0.0

    @property
    def match_rate(self) -> float:
        return self.matched / self.total if self.total > 0 else 0.0

    def speaker_learning_curves(self, min_messages: int = 5) -> dict[str, list[float]]:
        """Compute rolling accuracy for each speaker with enough messages.

        Returns {speaker: [rolling_accuracy_at_msg_1, ..., rolling_accuracy_at_msg_N]}.
        """
        curves = {}
        for speaker, results in self.per_speaker_correct.items():
            if len(results) < min_messages:
                continue
            cumulative = []
            correct_so_far = 0
            for i, is_correct in enumerate(results):
                correct_so_far += int(is_correct)
                cumulative.append(correct_so_far / (i + 1))
            curves[speaker] = cumulative
        return curves


def score_run(
    labels: list[dict],
    updates_index: dict[str, CoPUpdate],
    name: str,
) -> RunMetrics:
    """Score a replay run's extractions against Opus labels."""
    m = RunMetrics(name=name)

    for label in labels:
        m.total += 1
        raw_line = label.get("raw_line", "").strip()
        silver_type = label.get("extracted_type", "none")
        silver_entities = label.get("extracted_entities", [])
        silver_confidence = label.get("confidence", 0.0)
        silver_method = label.get("extraction_method", "")
        speaker = label.get("sender", "unknown")

        # Look up the corresponding extraction
        update = updates_index.get(raw_line)

        # Track noise detection
        if silver_type == "none" and silver_method == "noise_filter":
            m.noise_total += 1
            if update is None or update.update_type.value == "none":
                m.noise_correct += 1
            m.per_type_total["none"] += 1
            if update is None or update.update_type.value == "none":
                m.per_type_correct["none"] += 1
                m.type_exact += 1
                m.type_relaxed += 1
                m.per_speaker_correct[speaker].append(True)
            else:
                m.per_speaker_correct[speaker].append(False)
            continue

        if update is None:
            # No extraction for this label -- count as miss
            m.per_type_total[silver_type] += 1
            m.per_speaker_correct[speaker].append(False)
            continue

        m.matched += 1
        candidate_type = update.update_type.value

        # Type matching
        m.per_type_total[silver_type] += 1
        if candidate_type == silver_type:
            m.type_exact += 1
            m.type_relaxed += 1
            m.per_type_correct[silver_type] += 1
            m.per_speaker_correct[speaker].append(True)
        elif types_match_relaxed(candidate_type, silver_type):
            m.type_relaxed += 1
            m.per_type_correct[silver_type] += 1
            m.per_speaker_correct[speaker].append(True)
        else:
            m.per_speaker_correct[speaker].append(False)

        # Entity overlap
        silver_keys = entity_keys_from_dicts(silver_entities)
        candidate_keys = entity_keys_from_cop_update(update)
        if silver_keys or candidate_keys:
            overlap = jaccard_similarity(silver_keys, candidate_keys)
            m.entity_overlap_sum += overlap
            m.entity_overlap_count += 1

        # Confidence
        m.confidence_pairs.append((silver_confidence, update.confidence))

    return m


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


def format_report(
    with_metrics: RunMetrics | None,
    without_metrics: RunMetrics | None,
    labels_path: str,
) -> str:
    """Format a comparison report in Markdown."""
    lines = []
    lines.append("# Speaker Model Evaluation Report (RQ1)")
    lines.append("")
    lines.append(f"**Labels**: {labels_path}")

    runs = []
    if with_metrics:
        runs.append(with_metrics)
    if without_metrics:
        runs.append(without_metrics)

    if not runs:
        lines.append("")
        lines.append("No replay databases provided. Run the A/B experiment first.")
        lines.append("")
        lines.append("```bash")
        lines.append("# Run A: with speaker models (default)")
        lines.append("python -m chat_to_cop.replay data/dash3/23Sep_usaf_chat.zip \\")
        lines.append("    --url http://127.0.0.1:11434/v1 --model qwen2.5:7b-8k \\")
        lines.append("    --db /tmp/replay_with_speakers.db")
        lines.append("")
        lines.append("# Run B: without speaker models")
        lines.append(
            "CHAT_TO_COP_USE_SPEAKER_MODELS=false python -m chat_to_cop.replay data/dash3/23Sep_usaf_chat.zip \\"
        )
        lines.append("    --url http://127.0.0.1:11434/v1 --model qwen2.5:7b-8k \\")
        lines.append("    --db /tmp/replay_without_speakers.db")
        lines.append("```")
        return "\n".join(lines)

    # Summary table
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    header = "| Metric |"
    sep = "| --- |"
    for r in runs:
        header += f" {r.name} |"
        sep += " --- |"
    lines.append(header)
    lines.append(sep)

    def _row(label: str, fmt: str, getter):
        row = f"| {label} |"
        for r in runs:
            row += f" {fmt.format(getter(r))} |"
        lines.append(row)

    _row("Labels", "{}", lambda r: r.total)
    _row("Matched extractions", "{}", lambda r: r.matched)
    _row("Match rate", "{:.1%}", lambda r: r.match_rate)
    _row("Type exact match", "{:.1%}", lambda r: r.type_exact_rate)
    _row("Type relaxed match", "{:.1%}", lambda r: r.type_relaxed_rate)
    _row("Entity overlap (avg Jaccard)", "{:.1%}", lambda r: r.entity_overlap_avg)
    _row("Noise detection accuracy", "{:.1%}", lambda r: r.noise_accuracy)
    _row("Confidence correlation", "{:.3f}", lambda r: r.confidence_corr)

    # Delta
    if len(runs) == 2:
        a, b = runs
        lines.append("")
        lines.append("## Delta (with - without speaker models)")
        lines.append("")
        delta_exact = a.type_exact_rate - b.type_exact_rate
        delta_relaxed = a.type_relaxed_rate - b.type_relaxed_rate
        delta_entity = a.entity_overlap_avg - b.entity_overlap_avg
        delta_noise = a.noise_accuracy - b.noise_accuracy
        lines.append(f"- Type exact match: **{delta_exact:+.1%}**")
        lines.append(f"- Type relaxed match: **{delta_relaxed:+.1%}**")
        lines.append(f"- Entity overlap: **{delta_entity:+.1%}**")
        lines.append(f"- Noise accuracy: **{delta_noise:+.1%}**")

    # Per-type breakdown for each run
    for r in runs:
        lines.append("")
        lines.append(f"## Per-Type Breakdown: {r.name}")
        lines.append("")
        lines.append(f"| {'Type':25s} | {'Correct':>8s} | {'Total':>8s} | {'Rate':>8s} |")
        lines.append(f"| {'-' * 25} | {'-' * 8} | {'-' * 8} | {'-' * 8} |")
        for utype in sorted(r.per_type_total.keys()):
            correct = r.per_type_correct[utype]
            total = r.per_type_total[utype]
            rate = correct / total if total > 0 else 0
            lines.append(f"| {utype:25s} | {correct:>8d} | {total:>8d} | {rate:>7.0%} |")

    # Learning curves
    for r in runs:
        curves = r.speaker_learning_curves(min_messages=5)
        if curves:
            lines.append("")
            lines.append(f"## Learning Curves: {r.name}")
            lines.append("")
            lines.append("Rolling accuracy by message count for speakers with 5+ messages:")
            lines.append("")
            for speaker in sorted(curves, key=lambda s: len(curves[s]), reverse=True)[:10]:
                curve = curves[speaker]
                # Show accuracy at message 5, 10, 20, final
                checkpoints = []
                for cp in [5, 10, 20, len(curve)]:
                    if cp <= len(curve):
                        checkpoints.append(f"msg{cp}={curve[cp - 1]:.0%}")
                lines.append(f"- **{speaker}** ({len(curve)} msgs): {', '.join(checkpoints)}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def async_main(args: argparse.Namespace) -> None:
    # Load labels
    labels_path = Path(args.labels)
    if not labels_path.exists():
        print(f"ERROR: Labels file not found: {labels_path}")
        sys.exit(1)

    labels = load_labels(str(labels_path))
    if not labels:
        print(f"ERROR: No labels found in {labels_path}")
        sys.exit(1)

    # Filter out error labels
    labels = [lb for lb in labels if lb.get("extraction_method") != "error"]
    print(f"Loaded {len(labels)} labels from {labels_path}")

    with_metrics = None
    without_metrics = None

    # Load replay databases if provided
    if args.with_speakers:
        db_path = Path(args.with_speakers)
        if not db_path.exists():
            print(f"ERROR: Database not found: {db_path}")
            sys.exit(1)
        updates = await load_replay_updates(str(db_path))
        print(f"Loaded {len(updates)} extractions from {db_path} (with speakers)")
        index = index_updates_by_message(updates)
        with_metrics = score_run(labels, index, "With Speakers")

    if args.without_speakers:
        db_path = Path(args.without_speakers)
        if not db_path.exists():
            print(f"ERROR: Database not found: {db_path}")
            sys.exit(1)
        updates = await load_replay_updates(str(db_path))
        print(f"Loaded {len(updates)} extractions from {db_path} (without speakers)")
        index = index_updates_by_message(updates)
        without_metrics = score_run(labels, index, "Without Speakers")

    # Generate report
    report = format_report(with_metrics, without_metrics, str(labels_path))
    print()
    print(report)

    # Optionally write to file
    if args.output:
        Path(args.output).write_text(report, encoding="utf-8")
        print(f"\nReport written to {args.output}")


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate speaker model impact on extraction quality (RQ1)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--labels",
        default="data/labels/dash3_silver_labels_opus.jsonl",
        help="Path to Opus silver labels JSONL. Default: data/labels/dash3_silver_labels_opus.jsonl",
    )
    parser.add_argument(
        "--with-speakers",
        default=None,
        help="Path to replay database WITH speaker models enabled.",
    )
    parser.add_argument(
        "--without-speakers",
        default=None,
        help="Path to replay database WITHOUT speaker models enabled.",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="Write report to this file (Markdown).",
    )
    args = parser.parse_args()
    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()

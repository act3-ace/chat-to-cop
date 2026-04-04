"""Standalone label analysis: statistics and comparison without an LLM endpoint.

Loads silver labels and computes:
- Type distribution (with percentages)
- Confidence distribution (histogram buckets)
- Per-channel breakdown (types per channel)
- Extraction method breakdown
- Optional: comparison between two label files (agreement rate, type confusion matrix)

Usage:
    python scripts/analyze_labels.py data/labels/dash3_silver_labels_opus.jsonl
    python scripts/analyze_labels.py data/labels/dash3_silver_labels_opus.jsonl \
        --compare data/labels/dash3_silver_labels_gemini_pro.jsonl
    python scripts/analyze_labels.py data/labels/  # analyze all labels in directory
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


def load_jsonl(path: Path) -> list[dict]:
    """Load all labels from a single JSONL file."""
    labels: list[dict] = []
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


def load_from_path(path: Path) -> list[dict]:
    """Load labels from a file or all .jsonl files in a directory."""
    if path.is_dir():
        labels: list[dict] = []
        for jsonl_file in sorted(path.glob("*.jsonl")):
            labels.extend(load_jsonl(jsonl_file))
        return labels
    elif path.is_file():
        return load_jsonl(path)
    return []


def get_type(label: dict) -> str:
    """Get the update type from a label, trying both field names."""
    return label.get("expected_type") or label.get("extracted_type") or "unknown"


def type_distribution(labels: list[dict]) -> Counter:
    """Count of each update type."""
    return Counter(get_type(lb) for lb in labels)


def confidence_distribution(labels: list[dict], n_bins: int = 10) -> list[tuple[str, int]]:
    """Bucket confidence values into histogram bins.

    Returns list of (bucket_label, count) tuples.
    """
    bins: list[int] = [0] * n_bins
    for lb in labels:
        conf = lb.get("confidence", 0.0)
        if conf is None:
            conf = 0.0
        idx = min(int(conf * n_bins), n_bins - 1)
        idx = max(idx, 0)
        bins[idx] += 1

    width = 1.0 / n_bins
    result = []
    for i, count in enumerate(bins):
        lo = i * width
        hi = (i + 1) * width
        result.append((f"{lo:.1f}-{hi:.1f}", count))
    return result


def channel_breakdown(labels: list[dict]) -> dict[str, Counter]:
    """Per-channel type distribution.

    Returns {channel: Counter({type: count})}.
    """
    result: dict[str, Counter] = {}
    for lb in labels:
        ch = lb.get("channel", "unknown")
        if ch not in result:
            result[ch] = Counter()
        result[ch][get_type(lb)] += 1
    return result


def method_breakdown(labels: list[dict]) -> Counter:
    """Count by extraction_method."""
    return Counter(lb.get("extraction_method", "unknown") for lb in labels)


def compare_labels(labels_a: list[dict], labels_b: list[dict]) -> dict:
    """Compare two label sets by raw_line matching.

    Returns a dict with:
        matched: number of labels matched by raw_line
        agreed: number where type matches
        disagreed: number where type differs
        agreement_rate: agreed / matched
        only_a: count of labels only in A
        only_b: count of labels only in B
        confusion: dict of (type_a, type_b) -> count for disagreements
    """
    index_a = {lb.get("raw_line", ""): lb for lb in labels_a}
    index_b = {lb.get("raw_line", ""): lb for lb in labels_b}

    keys_a = set(index_a.keys())
    keys_b = set(index_b.keys())
    shared = keys_a & keys_b

    agreed = 0
    disagreed = 0
    confusion: Counter = Counter()

    for key in shared:
        type_a = get_type(index_a[key])
        type_b = get_type(index_b[key])
        if type_a == type_b:
            agreed += 1
        else:
            disagreed += 1
            confusion[(type_a, type_b)] += 1

    matched = len(shared)
    return {
        "matched": matched,
        "agreed": agreed,
        "disagreed": disagreed,
        "agreement_rate": agreed / matched if matched > 0 else 0.0,
        "only_a": len(keys_a - keys_b),
        "only_b": len(keys_b - keys_a),
        "confusion": dict(confusion),
    }


def format_report(labels: list[dict], name: str = "Labels") -> str:
    """Format a full analysis report as a string."""
    lines: list[str] = []
    total = len(labels)
    lines.append(f"=== {name} Analysis ({total} labels) ===")
    lines.append("")

    if total == 0:
        lines.append("  (no labels)")
        return "\n".join(lines)

    # Type distribution
    types = type_distribution(labels)
    lines.append("--- Type Distribution ---")
    informative = total - types.get("none", 0)
    lines.append(f"  Informative: {informative}/{total} ({informative / total * 100:.1f}%)")
    lines.append(f"  Noise/none:  {types.get('none', 0)}/{total} ({types.get('none', 0) / total * 100:.1f}%)")
    lines.append("")
    for utype, count in types.most_common():
        pct = count / total * 100
        bar = "#" * int(pct / 2)
        lines.append(f"  {utype:<16s} {count:>5d} ({pct:5.1f}%) {bar}")
    lines.append("")

    # Confidence distribution
    conf_hist = confidence_distribution(labels)
    lines.append("--- Confidence Distribution ---")
    for bucket_label, count in conf_hist:
        pct = count / total * 100
        bar = "#" * int(pct / 2)
        lines.append(f"  {bucket_label:<7s} {count:>5d} ({pct:5.1f}%) {bar}")
    lines.append("")

    # Extraction method
    methods = method_breakdown(labels)
    lines.append("--- Extraction Method ---")
    for method, count in methods.most_common():
        lines.append(f"  {method:<16s} {count:>5d}")
    lines.append("")

    # Channel breakdown
    channels = channel_breakdown(labels)
    lines.append("--- Per-Channel Breakdown ---")
    for ch in sorted(channels.keys()):
        ch_total = sum(channels[ch].values())
        ch_informative = ch_total - channels[ch].get("none", 0)
        info_pct = ch_informative / ch_total * 100 if ch_total > 0 else 0.0
        lines.append(f"  {ch:<25s} {ch_total:>4d} msgs, {ch_informative:>4d} informative ({info_pct:.0f}%)")
        # Show top types for this channel (skip 'none')
        top_types = [(t, c) for t, c in channels[ch].most_common() if t != "none"]
        if top_types:
            type_strs = [f"{t}={c}" for t, c in top_types[:5]]
            lines.append(f"    {' '.join(type_strs)}")
    lines.append("")

    return "\n".join(lines)


def format_comparison(result: dict, name_a: str, name_b: str) -> str:
    """Format a comparison report as a string."""
    lines: list[str] = []
    lines.append(f"=== Comparison: {name_a} vs {name_b} ===")
    lines.append(f"  Matched (shared raw_line): {result['matched']}")
    lines.append(f"  Agreed:  {result['agreed']} ({result['agreement_rate'] * 100:.1f}%)")
    lines.append(f"  Disagreed: {result['disagreed']}")
    lines.append(f"  Only in {name_a}: {result['only_a']}")
    lines.append(f"  Only in {name_b}: {result['only_b']}")

    if result["confusion"]:
        lines.append("")
        lines.append("  --- Type Disagreements ---")
        sorted_conf = sorted(result["confusion"].items(), key=lambda x: -x[1])
        for (type_a, type_b), count in sorted_conf:
            lines.append(f"    {name_a}={type_a:<16s} {name_b}={type_b:<16s} x{count}")

    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze silver label statistics and optionally compare two label sets",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python scripts/analyze_labels.py data/labels/dash3_silver_labels_opus.jsonl
    python scripts/analyze_labels.py data/labels/ --compare data/labels/other.jsonl
        """,
    )
    parser.add_argument(
        "path",
        help="Path to a .jsonl label file or a directory of .jsonl files",
    )
    parser.add_argument(
        "--compare",
        default=None,
        help="Second label file or directory to compare against",
    )
    args = parser.parse_args()

    target = Path(args.path)
    labels = load_from_path(target)
    if not labels:
        print(f"ERROR: No labels found at {target}")
        sys.exit(1)

    name = target.stem if target.is_file() else str(target)
    report = format_report(labels, name=name)
    print(report)

    if args.compare:
        compare_path = Path(args.compare)
        labels_b = load_from_path(compare_path)
        if not labels_b:
            print(f"ERROR: No labels found at {compare_path}")
            sys.exit(1)

        name_b = compare_path.stem if compare_path.is_file() else str(compare_path)
        result = compare_labels(labels, labels_b)
        print(format_comparison(result, name, name_b))


if __name__ == "__main__":
    main()

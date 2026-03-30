"""CLI tool to validate label JSONL files.

Usage:
    python scripts/validate_labels.py data/labels/my_labels.jsonl
    python scripts/validate_labels.py data/labels/   # validate all files in directory
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from chat_to_cop.labels import label_stats, validate_label


def validate_file(path: Path) -> tuple[int, int, list[tuple[int, list[str]]]]:
    """Validate a single JSONL file.

    Returns (total, valid_count, list of (line_number, errors) for invalid labels).
    """
    total = 0
    valid = 0
    error_lines: list[tuple[int, list[str]]] = []

    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                label = json.loads(line)
            except json.JSONDecodeError as e:
                error_lines.append((lineno, [f"JSON parse error: {e}"]))
                total += 1
                continue

            total += 1
            errors = validate_label(label)
            if errors:
                error_lines.append((lineno, errors))
            else:
                valid += 1

    return total, valid, error_lines


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate label JSONL files for the chat-to-cop pipeline",
    )
    parser.add_argument(
        "path",
        help="Path to a .jsonl file or a directory of .jsonl files",
    )
    args = parser.parse_args()

    target = Path(args.path)
    if target.is_dir():
        files = sorted(target.glob("*.jsonl"))
        if not files:
            print(f"No .jsonl files found in {target}")
            sys.exit(1)
    elif target.is_file():
        files = [target]
    else:
        print(f"ERROR: Path not found: {target}")
        sys.exit(1)

    all_labels: list[dict] = []
    total_errors = 0

    for fpath in files:
        print(f"\n--- {fpath} ---")
        total, valid, error_lines = validate_file(fpath)
        print(f"  {total} labels loaded")
        print(f"  {valid} valid, {len(error_lines)} errors")

        for lineno, errors in error_lines:
            for err in errors:
                print(f"  Error line {lineno}: {err}")

        total_errors += len(error_lines)

        # Load valid labels for stats
        with open(fpath, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    all_labels.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    # Summary stats
    if all_labels:
        stats = label_stats(all_labels)
        print(f"\n=== Summary ({len(files)} file(s)) ===")
        print(f"  Total labels: {stats['total']}")

        print("  Types:", end="")
        for utype, count in sorted(stats["by_type"].items()):
            print(f" {utype}={count}", end="")
        print()

        print("  Sources:", end="")
        for source, count in sorted(stats["by_source"].items()):
            print(f" {source}={count}", end="")
        print()

        print("  Channels:", end="")
        for channel, count in sorted(stats["by_channel"].items()):
            print(f" {channel}={count}", end="")
        print()

    sys.exit(1 if total_errors > 0 else 0)


if __name__ == "__main__":
    main()

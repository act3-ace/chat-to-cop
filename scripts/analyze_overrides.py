#!/usr/bin/env python3
"""Post-event analysis of operator overrides.

Reads the overrides table from a replay database and reports:
- Total overrides
- Per-speaker override rate (which speakers produce overridden extractions)
- Per-update-type override rate (which extraction types get overridden most)
- Per-channel override rate (which channels produce overridden extractions)

This is the debrief tool: operator corrections become system improvement signals.

Usage:
    python scripts/analyze_overrides.py data/world_state.db
    python scripts/analyze_overrides.py data/world_state.db --json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


def analyze(db_path: str) -> dict:
    """Read overrides from the given database and return analysis."""
    if not Path(db_path).exists():
        print(f"Error: database not found: {db_path}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Total updates and overrides
    total_updates = conn.execute("SELECT COUNT(*) as cnt FROM updates").fetchone()["cnt"]
    total_overrides = conn.execute("SELECT COUNT(*) as cnt FROM overrides").fetchone()["cnt"]

    if total_overrides == 0:
        conn.close()
        return {
            "total_updates": total_updates,
            "total_overrides": 0,
            "override_rate": 0.0,
            "by_speaker": {},
            "by_type": {},
            "by_channel": {},
            "overrides": [],
        }

    # Per-speaker override rate
    speaker_rows = conn.execute(
        """SELECT u.source_speaker,
                  COUNT(DISTINCT o.update_id) as overridden,
                  (SELECT COUNT(*) FROM updates u2 WHERE u2.source_speaker = u.source_speaker) as total
           FROM overrides o
           JOIN updates u ON o.update_id = u.id
           GROUP BY u.source_speaker
           ORDER BY overridden DESC"""
    ).fetchall()
    by_speaker = {}
    for row in speaker_rows:
        total = row["total"]
        overridden = row["overridden"]
        by_speaker[row["source_speaker"]] = {
            "overridden": overridden,
            "total": total,
            "rate": round(overridden / total, 4) if total > 0 else 0.0,
        }

    # Per-type override rate
    type_rows = conn.execute(
        """SELECT u.update_type,
                  COUNT(DISTINCT o.update_id) as overridden,
                  (SELECT COUNT(*) FROM updates u2 WHERE u2.update_type = u.update_type) as total
           FROM overrides o
           JOIN updates u ON o.update_id = u.id
           GROUP BY u.update_type
           ORDER BY overridden DESC"""
    ).fetchall()
    by_type = {}
    for row in type_rows:
        total = row["total"]
        overridden = row["overridden"]
        by_type[row["update_type"]] = {
            "overridden": overridden,
            "total": total,
            "rate": round(overridden / total, 4) if total > 0 else 0.0,
        }

    # Per-channel override rate
    channel_rows = conn.execute(
        """SELECT u.source_channel,
                  COUNT(DISTINCT o.update_id) as overridden,
                  (SELECT COUNT(*) FROM updates u2 WHERE u2.source_channel = u.source_channel) as total
           FROM overrides o
           JOIN updates u ON o.update_id = u.id
           GROUP BY u.source_channel
           ORDER BY overridden DESC"""
    ).fetchall()
    by_channel = {}
    for row in channel_rows:
        total = row["total"]
        overridden = row["overridden"]
        by_channel[row["source_channel"]] = {
            "overridden": overridden,
            "total": total,
            "rate": round(overridden / total, 4) if total > 0 else 0.0,
        }

    # Recent overrides with reasons
    override_rows = conn.execute(
        """SELECT o.id, o.update_id, o.operator_id, o.reason, o.overridden_at,
                  u.source_channel, u.source_speaker, u.update_type, u.source_message
           FROM overrides o
           JOIN updates u ON o.update_id = u.id
           ORDER BY o.overridden_at DESC
           LIMIT 50"""
    ).fetchall()
    overrides = [dict(row) for row in override_rows]

    conn.close()

    return {
        "total_updates": total_updates,
        "total_overrides": total_overrides,
        "override_rate": round(total_overrides / total_updates, 4) if total_updates > 0 else 0.0,
        "by_speaker": by_speaker,
        "by_type": by_type,
        "by_channel": by_channel,
        "overrides": overrides,
    }


def print_report(result: dict) -> None:
    """Print a human-readable override analysis report."""
    print("Override Analysis Report")
    print(f"{'=' * 60}")
    print(f"Total updates:   {result['total_updates']}")
    print(f"Total overrides: {result['total_overrides']}")
    print(f"Override rate:   {result['override_rate']:.1%}")
    print()

    if result["total_overrides"] == 0:
        print("No overrides recorded.")
        return

    print("Per-speaker override rate:")
    print(f"  {'Speaker':<25} {'Overridden':>10} {'Total':>8} {'Rate':>8}")
    print(f"  {'-' * 53}")
    for speaker, stats in result["by_speaker"].items():
        print(f"  {speaker:<25} {stats['overridden']:>10} {stats['total']:>8} {stats['rate']:>7.1%}")
    print()

    print("Per-type override rate:")
    print(f"  {'Type':<25} {'Overridden':>10} {'Total':>8} {'Rate':>8}")
    print(f"  {'-' * 53}")
    for utype, stats in result["by_type"].items():
        print(f"  {utype:<25} {stats['overridden']:>10} {stats['total']:>8} {stats['rate']:>7.1%}")
    print()

    print("Per-channel override rate:")
    print(f"  {'Channel':<25} {'Overridden':>10} {'Total':>8} {'Rate':>8}")
    print(f"  {'-' * 53}")
    for channel, stats in result["by_channel"].items():
        print(f"  {channel:<25} {stats['overridden']:>10} {stats['total']:>8} {stats['rate']:>7.1%}")
    print()

    if result["overrides"]:
        print("Recent overrides:")
        for o in result["overrides"][:10]:
            reason_str = f" -- {o['reason']}" if o["reason"] else ""
            print(
                f"  [{o['overridden_at']}] {o['source_channel']} / {o['source_speaker']}"
                f' / {o["update_type"]}: "{o["source_message"][:60]}"{reason_str}'
            )


def main():
    parser = argparse.ArgumentParser(description="Analyze operator overrides from a replay database")
    parser.add_argument("db_path", help="Path to the SQLite world_state database")
    parser.add_argument("--json", action="store_true", help="Output as JSON instead of human-readable text")
    args = parser.parse_args()

    result = analyze(args.db_path)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print_report(result)


if __name__ == "__main__":
    main()

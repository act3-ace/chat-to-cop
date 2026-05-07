"""Validate chat-to-cop extraction against DELTRON silver labels.

Compares our WorldStateStore output against DELTRON's classified message
baseline data dump. Does NOT require a running LLM -- reads DELTRON's
exported messages.json and (optionally) our data/world_state.db.

Usage:
    python scripts/validate_against_deltron.py path/to/deltron_0602Baseline_20260506_203633

    # With explicit DB path:
    python scripts/validate_against_deltron.py path/to/deltron_baseline --db data/world_state.db

The DELTRON directory must contain a messages.json (or messages_all.json)
file with the standard DELTRON message export format.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from collections import Counter


def load_deltron_messages(data_dir: str) -> list[dict]:
    """Load messages from a DELTRON baseline directory."""
    for name in ("messages.json", "messages_all.json"):
        path = os.path.join(data_dir, name)
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return data.get("messages", [])
    print(f"ERROR: No messages.json or messages_all.json found in {data_dir}")
    sys.exit(1)


def extract_resolver_entities(messages: list[dict]) -> dict[str, dict]:
    """Build a catalog of resolver-confirmed entities from bin assignments.

    Returns {entity_name: {method, count, channels, reasons}} for bins
    whose method is 'resolver' (confirmed callsigns vs STT noise).
    """
    catalog: dict[str, dict] = {}
    for msg in messages:
        for b in msg.get("bins", []):
            key = b.get("bin_key", "")
            method = b.get("method", "")
            if not key.startswith("entity:"):
                continue
            name = key.split(":", 1)[1]
            if name not in catalog:
                catalog[name] = {
                    "method": method,
                    "count": 0,
                    "channels": set(),
                    "reasons": set(),
                }
            catalog[name]["count"] += 1
            catalog[name]["channels"].add(msg.get("channel", "?"))
            if b.get("reason"):
                catalog[name]["reasons"].add(b["reason"])
            # Promote to resolver if any bin says so
            if method == "resolver":
                catalog[name]["method"] = "resolver"
    return catalog


def collect_track_ids(messages: list[dict]) -> Counter:
    """Collect all track numbers referenced across messages."""
    counts: Counter = Counter()
    for msg in messages:
        for tn in msg.get("entities_referenced", {}).get("track_numbers", []):
            counts[tn] += 1
    return counts


def collect_bcoa_ids(messages: list[dict]) -> Counter:
    """Find BCOA references in raw message content."""
    counts: Counter = Counter()
    pattern = re.compile(r"BCOA[\s\-]*(\d[\w\-]*)", re.IGNORECASE)
    for msg in messages:
        text = msg.get("raw_content", "") or msg.get("processed_content", "")
        for m in pattern.finditer(text):
            counts[f"BCOA {m.group(1)}"] += 1
    return counts


def print_deltron_summary(messages: list[dict]) -> dict:
    """Print summary stats and return structured info for cross-reference."""
    total = len(messages)
    print(f"Total messages: {total}")
    print()

    # By channel
    chan_counts: Counter = Counter()
    for msg in messages:
        chan_counts[msg.get("channel", "(none)")] += 1
    print("Messages by channel:")
    for ch, cnt in chan_counts.most_common():
        print(f"  {ch:<30s} {cnt:>5d}")
    print()

    # By importance tier
    tier_counts: Counter = Counter()
    for msg in messages:
        tier_counts[msg.get("importance_tier", "UNKNOWN")] += 1
    print("Messages by importance tier:")
    for tier in ["CRITICAL", "HIGH_VALUE", "COORDINATION", "LOW_VALUE", "NOISE"]:
        cnt = tier_counts.get(tier, 0)
        pct = cnt / total * 100 if total else 0
        print(f"  {tier:<16s} {cnt:>5d}  ({pct:5.1f}%)")
    for tier, cnt in tier_counts.items():
        if tier not in {"CRITICAL", "HIGH_VALUE", "COORDINATION", "LOW_VALUE", "NOISE"}:
            pct = cnt / total * 100 if total else 0
            print(f"  {tier:<16s} {cnt:>5d}  ({pct:5.1f}%)")
    print()

    # Entity catalog
    catalog = extract_resolver_entities(messages)
    resolver_entities = {k: v for k, v in catalog.items() if v["method"] == "resolver"}
    other_entities = {k: v for k, v in catalog.items() if v["method"] != "resolver"}

    print(f"Entity catalog: {len(catalog)} total entities in bins")
    print(f"  Resolver-confirmed: {len(resolver_entities)}")
    print(f"  Other (ad_hoc/llm_enrich/speaker): {len(other_entities)}")
    print()

    if resolver_entities:
        print("Resolver-confirmed callsigns:")
        for name in sorted(resolver_entities):
            info = resolver_entities[name]
            chans = ", ".join(sorted(info["channels"]))
            print(f"  {name:<24s} refs={info['count']:<4d} channels=[{chans}]")
        print()

    # Track IDs
    track_counts = collect_track_ids(messages)
    if track_counts:
        print(f"Track IDs found: {len(track_counts)}")
        for tn, cnt in track_counts.most_common(20):
            print(f"  {tn:<20s} {cnt:>4d} refs")
        if len(track_counts) > 20:
            print(f"  ... and {len(track_counts) - 20} more")
        print()

    # BCOA IDs
    bcoa_counts = collect_bcoa_ids(messages)
    if bcoa_counts:
        print(f"BCOA IDs found: {len(bcoa_counts)}")
        for bid, cnt in bcoa_counts.most_common():
            print(f"  {bid:<20s} {cnt:>4d} refs")
        print()

    # Threat entities (bins with "threat" prefix or entities with threat-like keys)
    threat_entities: list[str] = []
    for msg in messages:
        for b in msg.get("bins", []):
            key = b.get("bin_key", "")
            if "threat" in key.lower() or "hostile" in key.lower():
                threat_entities.append(key)
    threat_counts = Counter(threat_entities)
    if threat_counts:
        print(f"Threat-related bins: {len(threat_counts)}")
        for tk, cnt in threat_counts.most_common():
            print(f"  {tk:<30s} {cnt:>4d} refs")
        print()

    return {
        "channels": chan_counts,
        "tiers": tier_counts,
        "catalog": catalog,
        "resolver_entities": resolver_entities,
        "track_counts": track_counts,
    }


def cross_reference_db(db_path: str, messages: list[dict], info: dict) -> None:
    """Compare our world_state.db against DELTRON's labeled messages."""
    print("=" * 60)
    print(f"Cross-reference: {db_path}")
    print("=" * 60)
    print()

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Pull our audit log
    try:
        our_msgs = conn.execute("SELECT channel, sender, content, timestamp FROM audit_log").fetchall()
    except (sqlite3.OperationalError, sqlite3.DatabaseError) as e:
        print(f"  Could not read audit_log: {e}. Skipping cross-reference.")
        conn.close()
        return

    print(f"Our audit_log: {len(our_msgs)} messages")

    # Build lookup from content -> our record (normalize whitespace)
    def normalize(text: str) -> str:
        return " ".join(text.strip().split()).lower()

    our_content_set = {normalize(r["content"]) for r in our_msgs}

    # Match DELTRON messages to ours
    matched = []
    unmatched = []
    for msg in messages:
        raw = msg.get("raw_content", "") or msg.get("processed_content", "")
        if normalize(raw) in our_content_set:
            matched.append(msg)
        else:
            unmatched.append(msg)

    print(f"DELTRON messages matched to our audit_log: {len(matched)} / {len(messages)}")
    print(f"DELTRON messages we did not process: {len(unmatched)}")
    print()

    # Pull our extracted entities from the updates table
    try:
        our_updates = conn.execute("SELECT source_message, source_channel, data_json FROM updates").fetchall()
    except sqlite3.OperationalError:
        print("  updates table not found. Skipping entity comparison.")
        conn.close()
        return

    # Build our callsign extractions per normalized message
    our_callsigns_by_msg: dict[str, set[str]] = {}
    our_tracks_by_msg: dict[str, set[str]] = {}
    for row in our_updates:
        key = normalize(row["source_message"])
        try:
            data = json.loads(row["data_json"])
        except (json.JSONDecodeError, TypeError):
            continue
        cs = data.get("callsign") or data.get("entity_key", "")
        if cs:
            our_callsigns_by_msg.setdefault(key, set()).add(cs.upper())
        tn = data.get("track_number", "")
        if tn:
            our_tracks_by_msg.setdefault(key, set()).add(str(tn))

    # Compute precision/recall on callsigns for matched messages
    tp = 0  # We extracted a callsign DELTRON also found (resolver-confirmed)
    fp = 0  # We extracted a callsign DELTRON did not confirm
    fn = 0  # DELTRON found a resolver callsign we missed
    compared = 0

    resolver_names = {k.upper() for k in info["resolver_entities"]}

    for msg in matched:
        raw = msg.get("raw_content", "") or msg.get("processed_content", "")
        key = normalize(raw)

        deltron_cs = set()
        for b in msg.get("bins", []):
            bk = b.get("bin_key", "")
            if bk.startswith("entity:") and b.get("method") == "resolver":
                deltron_cs.add(bk.split(":", 1)[1].upper())
        # Also include entities_referenced callsigns that are in resolver set
        for cs in msg.get("entities_referenced", {}).get("callsigns", []):
            if cs.upper() in resolver_names:
                deltron_cs.add(cs.upper())

        our_cs = our_callsigns_by_msg.get(key, set())

        if not deltron_cs and not our_cs:
            continue
        compared += 1
        tp += len(our_cs & deltron_cs)
        fp += len(our_cs - deltron_cs)
        fn += len(deltron_cs - our_cs)

    print(f"Entity extraction comparison ({compared} messages with entities):")
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    print(f"  True positives:  {tp}")
    print(f"  False positives: {fp}")
    print(f"  False negatives: {fn}")
    print(f"  Precision: {precision:.3f}")
    print(f"  Recall:    {recall:.3f}")
    print(f"  F1:        {f1:.3f}")
    print()

    # Track number comparison
    track_tp = 0
    track_fp = 0
    track_fn = 0
    track_compared = 0
    for msg in matched:
        raw = msg.get("raw_content", "") or msg.get("processed_content", "")
        key = normalize(raw)
        deltron_tn = set(msg.get("entities_referenced", {}).get("track_numbers", []))
        our_tn = our_tracks_by_msg.get(key, set())
        if not deltron_tn and not our_tn:
            continue
        track_compared += 1
        track_tp += len(our_tn & deltron_tn)
        track_fp += len(our_tn - deltron_tn)
        track_fn += len(deltron_tn - our_tn)

    if track_compared:
        t_prec = track_tp / (track_tp + track_fp) if (track_tp + track_fp) else 0.0
        t_rec = track_tp / (track_tp + track_fn) if (track_tp + track_fn) else 0.0
        t_f1 = 2 * t_prec * t_rec / (t_prec + t_rec) if (t_prec + t_rec) else 0.0
        print(f"Track number comparison ({track_compared} messages with tracks):")
        print(f"  True positives:  {track_tp}")
        print(f"  False positives: {track_fp}")
        print(f"  False negatives: {track_fn}")
        print(f"  Precision: {t_prec:.3f}")
        print(f"  Recall:    {t_rec:.3f}")
        print(f"  F1:        {t_f1:.3f}")
        print()

    conn.close()


def print_glossary_supplement(catalog: dict[str, dict]) -> None:
    """Print which callsigns are real vs noise based on DELTRON's resolver."""
    print("=" * 60)
    print("Glossary supplement: callsign validation from DELTRON")
    print("=" * 60)
    print()

    confirmed = sorted(k for k, v in catalog.items() if v["method"] == "resolver")
    noise = sorted(k for k, v in catalog.items() if v["method"] != "resolver" and "unknown" in k.lower())
    unconfirmed = sorted(k for k, v in catalog.items() if v["method"] != "resolver" and "unknown" not in k.lower())

    print(f"Confirmed entities (method=resolver): {len(confirmed)}")
    for name in confirmed:
        info = catalog[name]
        print(f"  {name:<24s} ({info['count']} refs)")

    print()
    print(f"Unconfirmed entities (ad_hoc/speaker/llm_enrich): {len(unconfirmed)}")
    for name in unconfirmed:
        info = catalog[name]
        print(f"  {name:<24s} ({info['count']} refs, method={info['method']})")

    print()
    print(f"Likely STT noise (entity:unknown:*): {len(noise)}")
    for name in noise[:30]:
        info = catalog[name]
        print(f"  {name:<24s} ({info['count']} refs)")
    if len(noise) > 30:
        print(f"  ... and {len(noise) - 30} more")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate chat-to-cop extraction against DELTRON silver labels.")
    parser.add_argument(
        "data_dir",
        help="Path to DELTRON baseline directory containing messages.json",
    )
    parser.add_argument(
        "--db",
        default=os.path.join("data", "world_state.db"),
        help="Path to our world_state.db (default: data/world_state.db)",
    )
    args = parser.parse_args()

    if not os.path.isdir(args.data_dir):
        print(f"ERROR: {args.data_dir} is not a directory")
        sys.exit(1)

    print("DELTRON baseline validation")
    print(f"  Source: {args.data_dir}")
    print(f"  DB:     {args.db}")
    print()

    messages = load_deltron_messages(args.data_dir)
    print("=" * 60)
    print("DELTRON summary")
    print("=" * 60)
    print()
    info = print_deltron_summary(messages)

    # Glossary supplement (always printed)
    print_glossary_supplement(info["catalog"])

    # Cross-reference if DB exists
    if os.path.isfile(args.db):
        cross_reference_db(args.db, messages, info)
    else:
        print(f"(No world_state.db at {args.db} -- skipping cross-reference)")
        print()


if __name__ == "__main__":
    main()

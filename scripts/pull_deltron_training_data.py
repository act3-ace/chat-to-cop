"""Pull DELTRON's classified message history as labeled training data.

Fetches all classified messages from DELTRON's API, including:
- Importance scores (T0-T4) for every message
- Extracted entities (callsigns, track numbers, coordinates)
- Bin assignments (which entity each message references)
- Cross-channel correlation data
- Entity table (callsign -> platform type lookup)
- Threat table (if populated)

This gives us a silver-labeled dataset we can use to validate and
train our own importance triage and entity extraction.

=== INSTRUCTIONS FOR MIA ===

Run this from the chat-to-cop directory:

    python scripts/pull_deltron_training_data.py

It will create data/deltron_training/ with:
    - messages_all.json      (every classified message, full detail)
    - messages_summary.csv   (one row per message: id, channel, sender, tier, entities)
    - entity_table.json      (189 callsign -> platform records)
    - threat_table.json      (threat catalog if populated)
    - bins_snapshot.json      (all bins with message counts)
    - stats.json             (pull statistics)

Takes 1-3 minutes depending on how many messages DELTRON has processed.
No dependencies beyond Python stdlib. Run it periodically during the
exercise to capture more labeled data.

If DELTRON is down or unreachable, the script will tell you and exit cleanly.
"""

from __future__ import annotations

import csv
import io
import json
import os
from datetime import datetime, timezone
from urllib.error import URLError
from urllib.request import Request, urlopen

DELTRON_URL = "http://10.0.0.1:3060"
OUTPUT_DIR = os.path.join("data", "deltron_training")
PAGE_SIZE = 500


def fetch(url: str, timeout: float = 10.0) -> str | None:
    try:
        req = Request(url)
        req.add_header("Accept", "application/json")
        resp = urlopen(req, timeout=timeout)
        return resp.read().decode("utf-8", errors="replace")
    except (URLError, OSError, TimeoutError):
        return None


def fetch_json(url: str, timeout: float = 10.0) -> dict | None:
    body = fetch(url, timeout=timeout)
    if not body:
        return None
    try:
        parsed = json.loads(body)
        if isinstance(parsed, dict):
            return parsed
        return None
    except (json.JSONDecodeError, ValueError):
        return None


def pull_all_messages() -> list[dict]:
    """Pull all classified messages, paginating through the entire history."""
    all_messages: list[dict] = []
    page = 1

    print("  Pulling messages (page_size=500)...", flush=True)
    while True:
        url = f"{DELTRON_URL}/messages?page_size={PAGE_SIZE}&page={page}&order=asc"
        data = fetch_json(url)
        if data is None:
            print(f"    Page {page}: request failed, stopping.", flush=True)
            break

        messages = data.get("messages", [])
        total = data.get("total", 0)
        all_messages.extend(messages)

        print(f"    Page {page}: got {len(messages)} messages (total so far: {len(all_messages)}/{total})", flush=True)

        if len(messages) < PAGE_SIZE or len(all_messages) >= total:
            break
        page += 1

    return all_messages


def pull_bins() -> dict:
    """Pull all bin categories and their contents."""
    bins_data: dict = {"categories": {}}

    categories = fetch_json(f"{DELTRON_URL}/category")
    if not categories:
        print("  Bins: could not fetch categories", flush=True)
        return bins_data

    cat_list = categories.get("categories", [])
    print(f"  Bins: {len(cat_list)} categories: {cat_list}", flush=True)

    for cat in cat_list:
        url = f"{DELTRON_URL}/{cat}"
        cat_data = fetch_json(url)
        if cat_data:
            bins = cat_data.get("bins", [])
            bins_data["categories"][cat] = {
                "total_bins": cat_data.get("total", 0),
                "bins": bins,
            }
            print(f"    {cat}: {len(bins)} bins", flush=True)

    return bins_data


def messages_to_csv(messages: list[dict]) -> str:
    """Convert messages to a flat CSV for easy analysis."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "message_id",
            "timestamp",
            "channel",
            "sender",
            "importance_score",
            "importance_tier",
            "confidence",
            "classification_path",
            "callsigns",
            "track_numbers",
            "coordinates",
            "mission_numbers",
            "bma_names",
            "bins",
            "is_cross_channel",
            "processing_time_ms",
            "llm_enriched",
            "raw_content",
        ]
    )

    for msg in messages:
        entities = msg.get("entities_referenced", {})
        bins = msg.get("bins", [])
        cross = msg.get("cross_channel", {})

        writer.writerow(
            [
                msg.get("message_id", ""),
                msg.get("timestamp", ""),
                msg.get("channel", ""),
                msg.get("sender", ""),
                msg.get("importance_score", ""),
                msg.get("importance_tier", ""),
                msg.get("confidence", ""),
                msg.get("classification_path", ""),
                "|".join(entities.get("callsigns", [])),
                "|".join(entities.get("track_numbers", [])),
                "|".join(entities.get("coordinates", [])),
                "|".join(entities.get("mission_numbers", [])),
                "|".join(entities.get("bma_names", [])),
                "|".join(b.get("bin_key", "") for b in bins),
                cross.get("is_cross_channel_correlated", False),
                msg.get("processing_time_ms", ""),
                bool(msg.get("llm_invocations")),
                msg.get("raw_content", ""),
            ]
        )

    return output.getvalue()


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    print(f"DELTRON training data pull -- {timestamp}")
    print(f"Target: {DELTRON_URL}")
    print(f"Output: {OUTPUT_DIR}/")
    print()

    # --- Health check ---
    health = fetch_json(f"{DELTRON_URL}/health")
    if health is None:
        print("ERROR: Cannot reach DELTRON at {DELTRON_URL}")
        print("Make sure you're on the MASH network (shoc.lan Wi-Fi).")
        return
    print(f"  DELTRON health: {health}")
    print()

    # --- Entity table ---
    print("  Pulling entity table...", flush=True)
    entity_table = fetch_json(f"{DELTRON_URL}/entity-table")
    if entity_table:
        count = entity_table.get("count", 0)
        print(f"    {count} entities loaded", flush=True)
        with open(os.path.join(OUTPUT_DIR, "entity_table.json"), "w", encoding="utf-8") as f:
            json.dump(entity_table, f, indent=2)
    else:
        print("    Failed to fetch entity table", flush=True)

    # --- Threat table ---
    print("  Pulling threat table...", flush=True)
    threat_table = fetch_json(f"{DELTRON_URL}/threat-table")
    if threat_table:
        count = threat_table.get("count", 0)
        loaded = threat_table.get("loaded", False)
        print(f"    loaded={loaded}, {count} records", flush=True)
        with open(os.path.join(OUTPUT_DIR, "threat_table.json"), "w", encoding="utf-8") as f:
            json.dump(threat_table, f, indent=2)
    else:
        print("    Failed to fetch threat table", flush=True)

    # --- All messages ---
    print()
    messages = pull_all_messages()
    print(f"  Total messages pulled: {len(messages)}")

    if messages:
        # Full JSON (all fields)
        with open(os.path.join(OUTPUT_DIR, "messages_all.json"), "w", encoding="utf-8") as f:
            json.dump({"messages": messages, "count": len(messages), "pulled_at": timestamp}, f, indent=2)

        # Summary CSV (flat, for quick analysis)
        csv_content = messages_to_csv(messages)
        with open(os.path.join(OUTPUT_DIR, "messages_summary.csv"), "w", encoding="utf-8", newline="") as f:
            f.write(csv_content)

    # --- Bins snapshot ---
    print()
    bins_data = pull_bins()
    with open(os.path.join(OUTPUT_DIR, "bins_snapshot.json"), "w", encoding="utf-8") as f:
        json.dump(bins_data, f, indent=2)

    # --- Stats ---
    tier_counts = {}
    channel_counts = {}
    entity_msg_count = 0
    llm_enriched_count = 0
    for msg in messages:
        tier = msg.get("importance_tier", "UNKNOWN")
        tier_counts[tier] = tier_counts.get(tier, 0) + 1

        ch = msg.get("channel", "unknown")
        channel_counts[ch] = channel_counts.get(ch, 0) + 1

        entities = msg.get("entities_referenced", {})
        if any(entities.get(k) for k in ["callsigns", "track_numbers", "coordinates"]):
            entity_msg_count += 1

        if msg.get("llm_invocations"):
            llm_enriched_count += 1

    stats = {
        "pulled_at": timestamp,
        "total_messages": len(messages),
        "tier_distribution": tier_counts,
        "channel_distribution": channel_counts,
        "messages_with_entities": entity_msg_count,
        "llm_enriched_messages": llm_enriched_count,
        "entity_table_count": entity_table.get("count", 0) if entity_table else 0,
        "threat_table_loaded": threat_table.get("loaded", False) if threat_table else False,
    }
    with open(os.path.join(OUTPUT_DIR, "stats.json"), "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2)

    # --- Summary ---
    print()
    print("=" * 60)
    print(f"Pull complete: {len(messages)} messages")
    print(f"  Tier distribution:")
    for tier in ["NOISE", "LOW_VALUE", "COORDINATION", "HIGH_VALUE", "CRITICAL"]:
        count = tier_counts.get(tier, 0)
        pct = (count / len(messages) * 100) if messages else 0
        print(
            f"    T{['NOISE', 'LOW_VALUE', 'COORDINATION', 'HIGH_VALUE', 'CRITICAL'].index(tier)} {tier:<14} {count:>5} ({pct:.1f}%)"
        )
    print(f"  Messages with entities: {entity_msg_count}")
    print(f"  LLM-enriched: {llm_enriched_count}")
    print(f"  Channels: {len(channel_counts)}")
    print()
    print(f"Files saved to {OUTPUT_DIR}/:")
    print(f"  messages_all.json      -- full classified messages ({len(messages)} records)")
    print(f"  messages_summary.csv   -- flat CSV for analysis")
    print(f"  entity_table.json      -- callsign/platform lookup")
    print(f"  threat_table.json      -- threat catalog")
    print(f"  bins_snapshot.json     -- entity bin groupings")
    print(f"  stats.json             -- pull statistics")
    print()
    print("Run this again later to capture more messages as the exercise progresses.")


if __name__ == "__main__":
    main()

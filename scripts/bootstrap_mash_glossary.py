"""Bootstrap a supplemental glossary from live MASH services.

Pulls entity catalog data from DELTRON and (optionally) current tracks
from Track Manager, then writes data/mash_glossary.txt in a format the
chat-to-cop extraction pipeline can append to its DEFAULT_GLOSSARY.

=== INSTRUCTIONS FOR MIA ===

Run from the chat-to-cop directory while on the MASH network:

    python scripts/bootstrap_mash_glossary.py

This creates data/mash_glossary.txt.  To use it, set the env var:

    set CHAT_TO_COP_GLOSSARY_FILE=data/mash_glossary.txt

Then start the pipeline normally.  The supplemental glossary is appended
to the built-in DEFAULT_GLOSSARY so the LLM sees both.

If DELTRON is unreachable (laptop off-network), you can build the
glossary from a previously-saved messages JSON file instead:

    python scripts/bootstrap_mash_glossary.py --from-file data/deltron_training/messages_all.json

Re-run periodically during the exercise to pick up new entities.
No dependencies beyond Python stdlib.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from urllib.error import URLError
from urllib.request import Request, urlopen

DELTRON_URL = "http://10.5.185.30:3060"
TRACK_MANAGER_URL = "http://10.5.185.29:3021"
OUTPUT_PATH = os.path.join("data", "mash_glossary.txt")
_CALLSIGN_RE = re.compile(r"^([A-Z]+)(\d+)$", re.IGNORECASE)


def _fetch_json(url: str, timeout: float = 10.0):
    """GET a JSON endpoint. Returns parsed object or None on failure."""
    try:
        req = Request(url)
        req.add_header("Accept", "application/json")
        return json.loads(urlopen(req, timeout=timeout).read().decode("utf-8", errors="replace"))
    except (URLError, OSError, TimeoutError, json.JSONDecodeError, ValueError):
        return None


def _group_callsigns(names: list[str]) -> tuple[dict[str, list[str]], list[str]]:
    """Group callsigns by family prefix. GISMO21,GISMO22 -> GISMO: [...]."""
    families: dict[str, list[str]] = defaultdict(list)
    ungrouped: list[str] = []
    for name in sorted(set(names)):
        m = _CALLSIGN_RE.match(name)
        if m:
            families[m.group(1).upper()].append(name.upper())
        else:
            ungrouped.append(name)
    return dict(families), ungrouped


def _pull_deltron_entities() -> tuple[dict[str, list[str]], list[str], list[str]]:
    """Pull resolver-confirmed entities from DELTRON bin categories."""
    categories = _fetch_json(f"{DELTRON_URL}/category")
    if not categories:
        return {}, [], []
    callsigns: list[str] = []
    threats: list[str] = []
    for cat in categories.get("categories", []):
        cat_data = _fetch_json(f"{DELTRON_URL}/{cat}")
        if not cat_data:
            continue
        for b in cat_data.get("bins", []):
            if b.get("method") != "resolver":
                continue
            key = b.get("bin_key", "") or b.get("name", "")
            if not key:
                continue
            if "threat" in cat.lower() or "red" in cat.lower():
                threats.append(key)
            else:
                callsigns.append(key)
    families, ungrouped = _group_callsigns(callsigns)
    return families, ungrouped, sorted(set(threats))


def _entities_from_file(path: str) -> tuple[dict[str, list[str]], list[str], list[str]]:
    """Fallback: extract callsigns from a saved messages JSON file."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    messages = data if isinstance(data, list) else data.get("messages", [])
    names: set[str] = set()
    for msg in messages:
        for cs in msg.get("entities_referenced", {}).get("callsigns", []):
            names.add(cs.strip())
    families, ungrouped = _group_callsigns(list(names))
    return families, ungrouped, []


def _pull_entity_table() -> list[str]:
    """Pull structured entity records from DELTRON's /entity-table endpoint."""
    data = _fetch_json(f"{DELTRON_URL}/entity-table")
    if not data:
        return []
    records = data if isinstance(data, list) else data.get("records", data.get("entities", []))
    if isinstance(data, dict) and not records:
        for key in data:
            if isinstance(data[key], list):
                records = data[key]
                break
    lines: list[str] = []
    for r in records:
        name = r.get("name", r.get("entity", r.get("callsign", "")))
        etype = r.get("type", r.get("entity_type", r.get("category", "")))
        affil = r.get("affiliation", r.get("identity", r.get("side", "")))
        if name:
            parts = [name]
            if etype:
                parts.append(f"type={etype}")
            if affil:
                parts.append(f"affiliation={affil}")
            lines.append(" | ".join(parts))
    return sorted(set(lines))


def _pull_threat_table() -> list[str]:
    """Pull threat records from DELTRON's /threat-table endpoint."""
    data = _fetch_json(f"{DELTRON_URL}/threat-table")
    if not data:
        return []
    records = data if isinstance(data, list) else data.get("records", data.get("threats", []))
    if isinstance(data, dict) and not records:
        for key in data:
            if isinstance(data[key], list):
                records = data[key]
                break
    threats: list[str] = []
    for r in records:
        name = r.get("name", r.get("threat", r.get("designation", "")))
        ttype = r.get("type", r.get("threat_type", r.get("category", "")))
        if name:
            label = f"{name} ({ttype})" if ttype else name
            threats.append(label)
    return sorted(set(threats))


def _pull_tracks() -> list[str]:
    """Pull tracks from Track Manager, return formatted glossary lines."""
    data = _fetch_json(f"{TRACK_MANAGER_URL}/tracks")
    if data is None:
        return []
    tracks = data if isinstance(data, list) else data.get("tracks", data.get("items", []))
    lines: list[str] = []
    for t in tracks:
        tn = t.get("trackNumber", t.get("track_number", t.get("id", "")))
        label = t.get("platformType", t.get("platform_type", "")) or t.get("affiliation", t.get("identity", "unknown"))
        if tn:
            lines.append(f"- {tn} = {label}")
    return sorted(set(lines))


def _build_glossary(families, ungrouped, threats, track_lines, entity_table_lines=None, threat_table_lines=None) -> str:
    """Assemble the glossary text file."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    p: list[str] = [
        "## MASH Exercise Roster (auto-generated from DELTRON + Track Manager)",
        f"## Last updated: {now}",
        "## Source: DELTRON entity resolver + entity-table + threat-table + Track Manager",
        "",
        "### Known Callsigns (DELTRON-confirmed)",
    ]
    for prefix in sorted(families):
        p.append(f'- "{prefix}" family = {", ".join(families[prefix])}')
    for name in sorted(ungrouped):
        p.append(f"- {name}")
    if not families and not ungrouped:
        p.append("- (none found)")
    if entity_table_lines:
        p += ["", "### Entity Catalog (DELTRON /entity-table)"]
        p.extend(f"- {line}" for line in entity_table_lines)
    p += ["", "### Known Track Numbers"]
    p.extend(track_lines or ["- (Track Manager unreachable or empty)"])
    p += ["", "### Known Threats"]
    has_threats = False
    if threat_table_lines:
        p.extend(f"- {t}" for t in threat_table_lines)
        has_threats = True
    if threats:
        p.extend(f"- {t}" for t in threats if t not in (threat_table_lines or []))
        has_threats = True
    if not has_threats:
        p += ["- SA-21, SA-22 (SAM systems)", "- destroyer, submarine (naval threats)"]
    p.append("")
    return "\n".join(p) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap a supplemental glossary from MASH services.")
    parser.add_argument("--from-file", default="", help="Path to saved messages JSON (offline fallback).")
    parser.add_argument("--output", default=OUTPUT_PATH, help=f"Output path (default: {OUTPUT_PATH}).")
    args = parser.parse_args()

    print(f"MASH glossary bootstrap  ->  {args.output}\n")
    families: dict[str, list[str]] = {}
    ungrouped: list[str] = []
    threats: list[str] = []

    if args.from_file:
        print(f"  Reading entities from file: {args.from_file}")
        families, ungrouped, threats = _entities_from_file(args.from_file)
    else:
        print(f"  Pulling entities from DELTRON ({DELTRON_URL})...")
        if _fetch_json(f"{DELTRON_URL}/health") is None:
            print("    DELTRON unreachable.")
            if os.path.isfile(args.output):
                print(f"    Keeping existing {args.output}")
            else:
                print("    No cached glossary. Use --from-file for offline mode.")
            raise SystemExit(1)
        families, ungrouped, threats = _pull_deltron_entities()
    total = sum(len(v) for v in families.values()) + len(ungrouped)
    print(f"    {len(families)} families, {total} callsigns, {len(threats)} threats")

    entity_table_lines: list[str] = []
    threat_table_lines: list[str] = []
    if not args.from_file:
        print(f"  Pulling entity table from DELTRON ({DELTRON_URL}/entity-table)...")
        entity_table_lines = _pull_entity_table()
        print(
            f"    {len(entity_table_lines)} entity records"
            if entity_table_lines
            else "    entity-table empty or unavailable"
        )

        print(f"  Pulling threat table from DELTRON ({DELTRON_URL}/threat-table)...")
        threat_table_lines = _pull_threat_table()
        print(
            f"    {len(threat_table_lines)} threat records"
            if threat_table_lines
            else "    threat-table empty or unavailable"
        )

    print(f"  Pulling tracks from Track Manager ({TRACK_MANAGER_URL})...")
    track_lines = _pull_tracks()
    print(f"    {len(track_lines)} tracks" if track_lines else "    Track Manager unreachable or empty, skipping")

    glossary = _build_glossary(families, ungrouped, threats, track_lines, entity_table_lines, threat_table_lines)
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8", newline="\n") as f:
        f.write(glossary)

    print(f"\n  Wrote {args.output} ({len(glossary)} bytes)")
    print(f"\nTo activate, set:\n  CHAT_TO_COP_GLOSSARY_FILE={args.output}")


if __name__ == "__main__":
    main()

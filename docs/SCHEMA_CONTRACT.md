# Schema Contract: What chat-to-cop Writes to the CoP

**For: Sarah Bowman (711 HPW/RHWTE), Elizabeth Frost, Jeremy Gwinnup**
**From: Scott Clouse (ACT3), Mia Kollia (ACT3)**
**Date: 2026-04-10**
**Classification: UNCLASSIFIED / IL2**

This document describes exactly what the chat-to-cop system produces so that the CoP database schema can accommodate it. This is our half of the integration contract. We can adapt field names and formats to match your schema — the Pydantic model is the source of truth on our side, and changing it is a one-line edit per field.

---

## Overview

For each operationally relevant IRC/STT message, chat-to-cop produces a **CoPUpdate** object containing:

1. **What happened** — the update type (13 categories + none)
2. **To which entities** — zero or more EntityUpdate objects with track numbers, callsigns, status, position, weapons, fuel
3. **How confident we are** — a calibrated confidence score
4. **Where it came from** — full provenance chain (model, prompt, source message, channel, speaker, timestamp)
5. **What the LLM thinks** — a reasoning explanation

The CoPUpdate is the atomic unit of output. One IRC message may produce one CoPUpdate with multiple EntityUpdates (e.g., a SITREP mentioning four destroyed aircraft produces one CoPUpdate with four entities).

---

## The CoPUpdate object

| Field | Type | Description | Example |
|---|---|---|---|
| `update_type` | enum (see below) | What kind of world-state change this represents | `threat` |
| `confidence` | float 0.0-1.0 | Calibrated extraction confidence (see note below) | `0.72` |
| `extraction_method` | string | How this was extracted: `llm`, `regex`, or `passthrough` | `llm` |
| `entities` | list of EntityUpdate | The entities affected (see next section) | [EntityUpdate, ...] |
| `source_channel` | string | IRC channel the message came from | `#c2_coord` |
| `source_speaker` | string | IRC username of the sender | `HYDRO_SL` |
| `source_message` | string | The raw message text | `SITREP / AIR: ZEUS 12,13,14 shot down by TTG` |
| `timestamp` | ISO 8601 datetime | When the message was sent (UTC) | `2025-09-23T14:03:36Z` |
| `context_messages` | list of strings | Surrounding conversation for audit trail (typically last 3-5 messages) | [...] |
| `reasoning` | string or null | The LLM's explanation of what it extracted and why | `"The message is a SITREP explicitly stating..."` |
| `model_name` | string | Which model produced this extraction | `qwen2.5:7b` |
| `prompt_hash` | string | SHA-256 hash of the system prompt template used | `a3f2b1...` |

### Update types

| Value | Meaning | Frequency in DASH-3 (Opus labels) |
|---|---|---|
| `entity_id` | New entity identification or classification | 4% |
| `status_change` | Operational status change (RTB, destroyed, degraded) | 12% |
| `weapons` | Weapons employment, inventory, or expenditure | 1% |
| `fuel` | Fuel state report | 4% |
| `location` | Position update (coordinates, BMA, checkpoint) | 3% |
| `threat` | Threat assessment, SAM activation, hostile activity | 22% |
| `tasking` | Mission assignment, BattleCOA coordination | 43% |
| `handover` | C2 handover between pits or shifts | <1% |
| `csar` | Combat search and rescue event | <1% |
| `fire_mission` | Fire mission request or execution | 1% |
| `cyber_ew` | Cyber or electronic warfare event | 6% |
| `environmental` | Weather, terrain, or environmental condition | <1% |
| `sitrep` | General situation report (often multi-entity) | 3% |
| `none` | No operationally relevant content (noise) | 69% of all messages |

### Confidence semantics

The confidence field has been **calibrated** against Opus silver labels (935 messages, Claude Opus 4.6, ECE=0.67 on Qwen2.5-7B). Key findings:

- The model is **bimodally overconfident**: at raw 0.95, it's correct only 65% of the time
- The calibration model is in `data/calibration/qwen2.5_7b-8k.json`
- When a calibration model is loaded, the confidence reported in the CoPUpdate is the *calibrated* value
- Without calibration, it's the raw model output

**Write authority tiers** (how we recommend the CoP use confidence):

| Tier | Confidence range | Recommended action |
|---|---|---|
| AUTO | >= 0.95 | Write directly to the CoP |
| FLAGGED | 0.50 - 0.95 | Write with a "AI-extracted, unverified" flag |
| HUMAN | < 0.50 | Queue for operator review before writing |
| HUMAN (always) | Any (weapons, csar, fire_mission, cyber_ew) | High-risk types always require human review |

---

## The EntityUpdate object

Each CoPUpdate contains zero or more EntityUpdates. Each entity represents one platform, unit, or system mentioned in the message.

| Field | Type | Description | Example |
|---|---|---|---|
| `track_number` | string or null | Track number (TM###, DA###, 5-digit TNs) | `TM636` |
| `callsign` | string or null | Platform callsign | `ORCA01` |
| `platform_type` | string or null | Platform type / class | `F/A-18E`, `DDG`, `SA-21` |
| `affiliation` | string or null | FRIEND, HOSTILE, NEUTRAL, UNKNOWN | `HOSTILE` |
| `operational_status` | string or null | OPERATIONAL, DEGRADED, INOP, RTB, DESTROYED, EJECTED | `DESTROYED` |
| `latitude` | float or null | Latitude in decimal degrees (WGS84) | `33.456` |
| `longitude` | float or null | Longitude in decimal degrees (WGS84) | `-117.234` |
| `bearing` | float or null | Bearing from bullseye/reference point (degrees) | `270.0` |
| `range_nm` | float or null | Range from bullseye/reference point (nautical miles) | `40.0` |
| `altitude_ft` | float or null | Altitude in feet MSL | `25000.0` |
| `speed_kts` | float or null | Speed in knots | `450.0` |
| `heading` | float or null | Heading in degrees | `090.0` |
| `weapon_type` | string or null | Weapon type used or carried | `AMRAAM`, `SM6`, `TLAM` |
| `weapon_qty_launched` | int or null | Number of weapons launched/expended | `2` |
| `weapon_qty_remaining` | int or null | Number of weapons remaining | `38` |
| `fuel_state` | string or null | Fuel state as reported | `F+40`, `playtime 15 min` |
| `subsystem_status` | string or null | Subsystem detail | `radar inop`, `CIWS out` |
| `capability_impact` | enum or null | SDAC mapping: `sense`, `decide`, `act`, `collaborate` | `act` |
| `metadata` | dict (string keys, string values) | Overflow fields that don't map to any named field | `{"bullseye_range": "405.0"}` |

### Entity identification

Entities are identified by **track_number** or **callsign** (or both). These are the primary keys for matching against the CoP database. At least one should be present for any entity that maps to a known track in the CoP.

Some updates have entities with neither — for example, a threat report mentioning "4th-gen SAM active" has a platform_type and affiliation but no callsign or track number. These are still useful for the CoP as new-entity candidates.

### The metadata dict (Pattern B)

The `metadata` field is a free-form string-string dictionary for any extracted information that doesn't fit the named fields. Examples:

- `{"bullseye_range": "405.0"}` — range from bullseye when the bearing was extracted from `"240/405"` notation
- `{"event": "CSAR"}` — event type when the LLM couldn't categorize further
- `{"brevity_code": "BROKEN ARROW"}` — critical brevity code
- `{"count": "8"}` — entity count from "8 down" pattern
- `{"activity": "aerial refueling"}` — platform activity from "on boom" report

This is **intentionally open-ended** (Pattern B from our anti-ontology framework: out-of-ontology data is retained, never dropped). The metadata dict is where novel information lands when it doesn't fit the schema. It should be preserved, not discarded, even if the CoP doesn't have a field for it today.

---

## What we don't yet produce but you may need

These are fields we know the CoP likely needs that we don't currently extract. If you need them, tell us — some are straightforward to add, others are research problems.

| Field | Status | Difficulty |
|---|---|---|
| Track correlation ID (unique cross-system entity key) | Not produced | Needs the CoP's track numbering scheme |
| Kill chain phase | Not produced | Could derive from update_type + context |
| ROE compliance flag | Not produced | Would need ROE rules as input |
| Time validity window (how long this update is "fresh") | Not produced | Research item (#47 temporal decay) |
| Geospatial polygon (BMA boundaries, tanker tracks) | Available in equifinality repo | Integration item (#37) |
| Battle COA reference (which COA this update relates to) | Not produced | Would need BattleCOA catalog as input |
| Coalition releasability (REL TO) | Not produced | All data is currently IL2 |

---

## What we produce that you may not need

These fields exist for our internal pipeline and RAI compliance. They may not belong in the CoP but should be preserved in the audit trail.

| Field | Purpose |
|---|---|
| `extraction_method` | Pipeline debugging (llm vs regex vs passthrough) |
| `reasoning` | LLM explanation — useful for trust but possibly verbose for the CoP |
| `context_messages` | Surrounding conversation — useful for audit but large |
| `model_name` | RAI provenance (DoD AI Ethics Principle 3: Traceable) |
| `prompt_hash` | RAI provenance — pins the exact prompt version |

**Recommendation:** Store provenance fields in a linked audit table, not inline with the entity data. The CoP's primary consumers (vendor viz tools, operator displays) don't need to see `prompt_hash`, but post-event auditors do.

---

## How we'd consume your schema

Our CoPUpdate → CoP database mapping lives in one function:

```
src/chat_to_cop/output/cop_schema.py :: cop_update_to_records()
```

This function converts a CoPUpdate into one or more `CoPRecord` objects that map to the CoP REST API. Right now it uses a nominal schema because we don't have yours yet. When you share your schema:

1. We update the `CoPRecord` model to match your field names and types
2. We update the mapping in `cop_update_to_records()` — one line per field
3. We update the `CoPRESTClient` to hit your API endpoints
4. We run the DASH-3 replay and verify the records land correctly

This is a few hours of work once we have the schema. We've done 7 backend integrations (local GPU, HPC, 4 cloud APIs, Bedrock) with zero code changes between them — adding a new output schema is the same pattern.

---

## Wire format

The CoPUpdate is serialized as JSON via Pydantic's `.model_dump_json()`. Here's a real example from the DASH-3 replay:

```json
{
  "update_type": "sitrep",
  "confidence": 0.95,
  "extraction_method": "llm",
  "entities": [
    {"callsign": "ZEUS12", "operational_status": "DESTROYED"},
    {"callsign": "ZEUS13", "operational_status": "DESTROYED"},
    {"callsign": "ZEUS14", "operational_status": "DESTROYED"},
    {"callsign": "YAMA11", "operational_status": "DESTROYED"}
  ],
  "source_channel": "#c2_coord",
  "source_speaker": "HYDRO_SL",
  "source_message": "SITREP / AIR: ZEUS 12,13,14 shot down by TTG; YAMA11 flight shot down by TTG",
  "timestamp": "2025-09-23T14:03:36+00:00",
  "context_messages": [],
  "reasoning": "The message is a SITREP explicitly stating multiple aircraft were shot down",
  "model_name": "gemini-2.5-flash",
  "prompt_hash": "a3f2b184..."
}
```

---

## Questions for Sarah

1. **What entity ID format does the CoP use?** We extract callsigns and track numbers as free-text strings. If the CoP uses a specific integer ID scheme or a namespace (e.g., `TM-` prefix always present), we can normalize to match.

2. **What position format does the CoP prefer?** We extract both decimal degrees (lat/lon) and bullseye (bearing/range). If the CoP stores one format, we can convert.

3. **How does the CoP handle multi-entity updates?** We produce one CoPUpdate with N EntityUpdates. Should each entity be written as a separate API call, or is there a batch endpoint?

4. **Is there a "flagged / unverified" status in the CoP schema?** Our FLAGGED write authority tier wants to write with a review marker. If the schema supports it, we'll use it. If not, we need a side channel.

5. **What's the CoP's update frequency expectation?** We produce updates in 3-8 seconds per message. Is the CoP designed for that throughput, or should we batch?

6. **Can you share even a draft of the entity table schema?** Even field names and types would let us wire `cop_update_to_records()` now and iterate as the schema stabilizes.

---

## Contact

- **Scott Clouse** — hamilton.clouse.1@us.af.mil, ACT3 Mattermost: `hsclouse`
- **Mia Kollia** — mia.kollia.1@us.af.mil, ACT3 Mattermost: `mkollia`
- **GitLab repo** — https://gitlab.dle.afrl.af.mil/c2es1/mash/chat-to-cop (Internal visibility, your team should now have access)
- **Confluence docs (Sarah's)** — https://confluence.dle.afrl.af.mil/spaces/JADPACT/pages/548864328/Mash+Documentation

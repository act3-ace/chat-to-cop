# Session Notes - 2026-05-05

## Context

Live MASH wargame event at H2O Las Vegas. Mia Kollia deploying chat-to-cop on-site.
Hamilton supporting remotely.

## Completed This Session

### Dashboard and API fixes (pushed, confirmed working)
- Fixed JS syntax error in dashboard.py (Python `\'` escaping in triple-quoted strings)
- Fixed admin endpoint 500 errors when CoPWriter not initialized (standalone serve.bat mode)
- Commits: f758dac, 599a5f9

### CoP Schema Discovery (ISSUE #17 UNBLOCKED)
- Wrote aggressive one-shot schema puller: `scripts/pull_mash_schemas.py`
- Mia ran it on MASH network at 20:01Z -- 10/12 specs found, 74 files saved
- Commit: b0d74bc

### Results: All MASH Service Schemas Now In Hand

| Service | Port | Endpoints | Schemas | Key Write Path |
|---------|------|-----------|---------|----------------|
| TrackManager | :3021 | 14 | 8 | `POST /tracks`, `PUT /tracks/{id}` |
| SmartPackManager | :3028 | 12 | 33 | `POST /api/AirOperationsDirective` |
| EffectsManager | :3024 | 30 | 23 | `/damageeffect`, `/effectorconfiguration/{callsign}` |
| EventManager | :3016 | 16 | 18 | `POST /paeoutputs`, `/paeoutputs/bulk` |
| MatchEffectorManager | :3027 | 10 | 3 | MEF output |
| MissionManager | :3022 | 28 | 29 | `/missions/coa`, `/missions/intent` |
| AreaOfInterestManager | :3015 | 8 | 10 | AOI CRUD |
| PointOfInterestManager | :3017 | 6 | 2 | POI CRUD |
| DELTRON | :3060 | 13 | 18 | `/classify`, `/classify/batch` |
| IRC Multiplexer | :3080 | 8 | 3 | `/channels`, `/messages` |
| MASH UI | :3011 | - | - | Frontend only (PARTIAL) |
| Dev Chat | :9000 | - | - | Dev service (PARTIAL) |

### Extra Ports Discovered
- 10.5.185.29:3000 (40KB HTML -- likely Grafana or admin UI)
- 10.5.185.29:3012 (44KB HTML -- unknown)
- 10.5.185.29:3050 (5KB HTML -- unknown)

### Key Schema Findings

**Track object (TrackManager):**
- id, sourceId, aliases (dict), label, entityType, cotType
- location: GeoJSON Point [lon, lat, alt]
- rollDeg, pitchDeg, headingDeg, speed
- missionNumber, capabilities[], comms[], weapons[], targets[], missions[]
- affiliation (int enum), dimension (int enum), manned (bool)
- lastUpdateTime, detectionTime (ISO 8601)
- additionalInfo (freeform string), genMsgJtn

**Key insight: missions[] is a string array of "Key:Value" pairs**
- "Callsign:SWIFT01", "Fuel:2284", "TrackNumber:14306"
- "AirEntityType:22", "AirEntityTypeDesc:CIVIL, AIRLINER"
- This is the freeform metadata dictionary mentioned in the planning meeting

**Affiliation enum (integer):** Values observed: 0, 1, 2, 3 (likely Unknown, Friend, Neutral, Hostile)

**Write path confirmed:**
- `POST /tracks` creates a new track (assigns ID if not provided)
- `PUT /tracks/{id}` upserts by ID
- `/tracks/identifier/{identifier}` resolves by id, label, sourceId, JTN, or alias
- `/tracks-sse` for real-time streaming subscription

**EventManager (PAE output):**
- `POST /paeoutputs` and `/paeoutputs/bulk` for writing
- PaeOutput has SSE stream at `/paeoutputs-sse`
- Schemas: PaeInput, PaeOutput, Event, EventAction, EventTarget

**MissionManager (GBC output):**
- COA CRUD: `/missions/coa`, `/missions/coa-request`
- Commander's Intent: `/missions/intent`
- Schemas: Coa, CoaRequest, CommandersIntent, GbcOutput, BattleCoaHyperedge, etc.

**EffectsManager:**
- Weapons database: DamageEffect, CyberEffect, ElectronicAttackEffect, SensingEffect
- Platform configs: `/effectorconfiguration/{callsign}`
- Bulk write: `/damageeffects/bulk`, `/cybereffects/bulk`
- Live data: AIM-9L/M Sidewinder with full Pk tables by target type

**SmartPackManager:**
- Air Operations Directives, Joint Task Order Targets, Bullseyes, Airfields
- Area of Responsibility, Air Operations Centers
- Schemas for: CDE (Collateral Damage Estimate), Loadout, TargetDetails

## Known Issues

- Wi-Fi client isolation on shoc.lan prevents device-to-device access
  (other machines cannot reach Mia's API at 10.5.184.1:8000)
- Ethernet hostname (rdwwtws-41lgz2) not responding -- may need wired connection
- mash_ui (:3011) and dev_chat (:9000) returned HTML only, no API specs

## Next Steps (for agents)

1. **CoPWriter adapter** -- Map CoPUpdate schema to Track Manager `POST /tracks`
   - Build the HTTP client targeting http://10.5.185.29:3021/tracks
   - Map entity fields to Track schema (label, location, affiliation, missions[])
   - Handle the "Key:Value" string array pattern for missions/capabilities/comms

2. **EventManager integration** -- Write PaeOutput for processed events
   - Map our structured events to PaeInput/PaeOutput schemas
   - Use bulk endpoint for batch writes

3. **DELTRON coordination** -- We and Jeremy's system are complementary
   - DELTRON does importance scoring (T0-T4), entity extraction, cross-channel
   - We do world-state extraction and CoP database writes
   - Could consume DELTRON's `/messages-sse` as an input source

4. **Real-time track subscription** -- Subscribe to `/tracks-sse`
   - Use as ground truth for entity resolution
   - Cross-reference chat mentions against live track data

5. **Schema adapter config** -- Generate mapping config from the OpenAPI specs
   - `python -m chat_to_cop.cli.adapt_schema` can read these specs
   - Map CoPRecord fields to Track, PaeOutput, etc.

### Multi-line Message Reassembly (Mia's idea)
- Implemented `src/chat_to_cop/ingestion/multiline.py` -- MultiLineBuffer
  merges LINE 0 through LINE 5 ("5 line" plans) into a single IRCMessage
- Wired into replay.py at both live IRC and replay message sources
- 22 tests in `tests/test_multiline.py`, 1173 total suite passes
- Commit: 98ffe6a

### DELTRON Training Data Script
- `scripts/pull_deltron_training_data.py` -- pulls all classified messages,
  entity table, threat table, bin snapshots from DELTRON REST API
- Output: `data/deltron_training/` (messages_all.json, messages_summary.csv, etc.)
- Commit: 08f0e81

### GitHub Mirror and Vendor Access
- Sanitized orphan snapshot pushed to act3-ace/chat-to-cop (commit 5f12c4c)
- Branch protection enabled on GitHub main (requires 1 approving review)
- Natan Vidra (nv78) invited as collaborator for MASH shell scripts
- Cleaned 18 accidentally tracked PNGs from repo, updated .gitignore
- Commit: 9661673

## Files Modified This Session

- `src/chat_to_cop/dashboard.py` (JS escaping fix)
- `src/chat_to_cop/api.py` (admin endpoint null-safety)
- `scripts/pull_mash_schemas.py` (complete rewrite, aggressive discovery)
- `data/mash_schemas/` (74 files from MASH network pull)
- `src/chat_to_cop/ingestion/multiline.py` (new -- multi-line reassembly)
- `src/chat_to_cop/replay.py` (wrap message sources with reassemble_multiline)
- `tests/test_multiline.py` (new -- 22 tests)
- `scripts/pull_deltron_training_data.py` (new -- DELTRON data puller)
- `.gitignore` (block *.png, *.jpg, Mia_*.bat, env)

# Labeling Guide: DASH Ground Truth for Evaluation

How to manually label DASH chat messages with expected CoPUpdates so we can measure extraction quality.

## Goal

Label **100 typed chat messages** from DASH 3 with their expected world-state updates. These become ground truth for the eval harness (`scripts/eval_models.py`), letting us compute precision, recall, and F1 per model and per update type.

## Label Format

Each labeled message is a JSON object with three parts:

1. **The raw message** (what appeared in IRC)
2. **The expected update type** (one of 13 categories, or `none`)
3. **The expected entities** (structured fields the pipeline should extract)

```json
{
  "raw_line": "[14:03:36] HYDRO_SL: SITREP / AIR: ZEUS 12,13,14 shot down by TTG",
  "channel": "#c2_coord",
  "sender": "HYDRO_SL",
  "content": "SITREP / AIR: ZEUS 12,13,14 shot down by TTG",
  "expected_update_type": "sitrep",
  "expected_entities": [
    {"callsign": "ZEUS12", "operational_status": "DESTROYED"},
    {"callsign": "ZEUS13", "operational_status": "DESTROYED"},
    {"callsign": "ZEUS14", "operational_status": "DESTROYED"}
  ],
  "notes": "Multi-entity SITREP. Could also be labeled status_change -- either is acceptable."
}
```

A noise message (no world-state change):

```json
{
  "raw_line": "[14:03:03] VEGAS_ABM2: .",
  "channel": "#c2_coord",
  "sender": "VEGAS_ABM2",
  "content": ".",
  "expected_update_type": "none",
  "expected_entities": [],
  "notes": "Acknowledgment dot -- no information content."
}
```

## File Structure

Labels go in `data/labels/dash3_typed_chat.jsonl` -- one JSON object per line (JSON Lines format).

```
data/
  labels/
    dash3_typed_chat.jsonl    <-- 100 labeled messages
    README.md                 <-- brief description of labeling criteria
```

## The 13 Update Types

Every message gets exactly one `expected_update_type`. Here is one example for each type, showing exactly what the label should look like.

### 1. entity_id -- Entity Identification

A track number is resolved to a specific platform type.

```json
{
  "raw_line": "[14:15:22] Intel_OPS: @ Hydro_MSO TN 44504 is DDG1",
  "channel": "#c2_coord",
  "sender": "Intel_OPS",
  "content": "@ Hydro_MSO TN 44504 is DDG1",
  "expected_update_type": "entity_id",
  "expected_entities": [
    {"track_number": "44504", "platform_type": "DDG1", "affiliation": "HOSTILE"}
  ],
  "notes": ""
}
```

### 2. status_change -- Platform Status Changes

Equipment failures, damage, RTB, destroyed.

```json
{
  "raw_line": "[14:22:10] WF_Clark: ORCA01 (E-2D HAWKEYE): We are gadget bent and have to RTB.",
  "channel": "#c2_coord",
  "sender": "WF_Clark",
  "content": "ORCA01 (E-2D HAWKEYE): We are gadget bent and have to RTB.",
  "expected_update_type": "status_change",
  "expected_entities": [
    {
      "callsign": "ORCA01",
      "platform_type": "E-2D",
      "operational_status": "RTB",
      "subsystem_status": "radar inop"
    }
  ],
  "notes": "'gadget bent' = radar failure in brevity code."
}
```

### 3. weapons -- Weapons Employment & Engagement

Weapons launches, kill results, ammunition expenditure.

```json
{
  "raw_line": "[15:01:44] Floater11_OPS: 24 TLAMS launched, 38 TLAMS remaining",
  "channel": "#fires",
  "sender": "Floater11_OPS",
  "content": "24 TLAMS launched, 38 TLAMS remaining",
  "expected_update_type": "weapons",
  "expected_entities": [
    {
      "callsign": "Floater11_OPS",
      "weapon_type": "TLAM",
      "weapon_qty_launched": 24,
      "weapon_qty_remaining": 38
    }
  ],
  "notes": "Floater11_OPS is the USS Zumwalt DDG."
}
```

### 4. location -- Location & Position Reports

Position updates using bullseye/cigar, lat/lon, or MGRS.

```json
{
  "raw_line": "[14:30:05] JSTARS_01: TBM Track # L2079 inbound, IVO N21.774294, W72.279964",
  "channel": "#isr_reports",
  "sender": "JSTARS_01",
  "content": "TBM Track # L2079 inbound, IVO N21.774294, W72.279964",
  "expected_update_type": "location",
  "expected_entities": [
    {
      "track_number": "L2079",
      "platform_type": "TBM",
      "latitude": 21.774294,
      "longitude": -72.279964
    }
  ],
  "notes": "IVO = in the vicinity of. W prefix means negative longitude."
}
```

### 5. threat -- Threat Assessments & Intelligence

Hostile force characterization and TACREPs.

```json
{
  "raw_line": "[14:45:00] AOC_SIDO: tacrep e10-4, 4th-gen sam active, cigar 316/398, jtn TM677",
  "channel": "#isr_reports",
  "sender": "AOC_SIDO",
  "content": "tacrep e10-4, 4th-gen sam active, cigar 316/398, jtn TM677",
  "expected_update_type": "threat",
  "expected_entities": [
    {
      "track_number": "TM677",
      "affiliation": "HOSTILE",
      "bearing": 316.0,
      "range_nm": 398.0,
      "subsystem_status": "4th-gen SAM active"
    }
  ],
  "notes": "Cigar = bearing/range from reference point."
}
```

### 6. tasking -- Mission Tasking & Re-tasking

Orders, BattleCOA directives, effector matching.

```json
{
  "raw_line": "[15:10:30] VEGAS_SL: @vegas_abm1 Generate BattleCOA 6-1 for: SUNNY21 STRIKE TN TM636",
  "channel": "#c2_coord",
  "sender": "VEGAS_SL",
  "content": "@vegas_abm1 Generate BattleCOA 6-1 for: SUNNY21 STRIKE TN TM636",
  "expected_update_type": "tasking",
  "expected_entities": [
    {"callsign": "SUNNY21", "track_number": "TM636"}
  ],
  "notes": "BattleCOA = Battle Course of Action. SUNNY21 is the assigned asset."
}
```

### 7. fuel -- Fuel & Logistics

Tanker states, aerial refueling.

```json
{
  "raw_line": "[14:55:00] Hydro_Tank: RR15 F+40, RL36 F+50",
  "channel": "#c2_coord",
  "sender": "Hydro_Tank",
  "content": "RR15 F+40, RL36 F+50",
  "expected_update_type": "fuel",
  "expected_entities": [
    {"callsign": "RR15", "fuel_state": "F+40"},
    {"callsign": "RL36", "fuel_state": "F+50"}
  ],
  "notes": "F+40 = 40,000 lbs above frag (planned fuel load)."
}
```

### 8. handover -- C2 Handover & Airspace

Control transfers, BMA management, airbase status.

```json
{
  "raw_line": "[15:30:00] AOC_Ops: @TOC01_OPS You now have SADC duties for Lanes 2 and 3",
  "channel": "#c2_coord",
  "sender": "AOC_Ops",
  "content": "@TOC01_OPS You now have SADC duties for Lanes 2 and 3",
  "expected_update_type": "handover",
  "expected_entities": [
    {
      "callsign": "TOC01_OPS",
      "metadata": {"duty": "SADC", "lanes": "2, 3"}
    }
  ],
  "notes": "SADC = Senior Area Defense Commander."
}
```

### 9. csar -- Personnel Recovery / CSAR

Bailout reports, survivor locations.

```json
{
  "raw_line": "[15:45:00] Hydro_SL: @JPRC, FG31 bailout, 2 good chutes, 272/43",
  "channel": "#jprc",
  "sender": "Hydro_SL",
  "content": "@JPRC, FG31 bailout, 2 good chutes, 272/43",
  "expected_update_type": "csar",
  "expected_entities": [
    {
      "callsign": "FG31",
      "operational_status": "DESTROYED",
      "bearing": 272.0,
      "range_nm": 43.0,
      "metadata": {"chute_count": "2"}
    }
  ],
  "notes": "272/43 = bearing 272 degrees, range 43 NM from bullseye."
}
```

### 10. fire_mission -- Fire Missions

Structured artillery/missile fire requests.

```json
{
  "raw_line": "[16:00:00] 3MARDIV_FIRES: FIRE MISSION! Spotter: Labradoodle 69, MLRS, 17QNE9779269855, DPICM, Immediate",
  "channel": "#fires",
  "sender": "3MARDIV_FIRES",
  "content": "FIRE MISSION! Spotter: Labradoodle 69, MLRS, 17QNE9779269855, DPICM, Immediate",
  "expected_update_type": "fire_mission",
  "expected_entities": [
    {
      "metadata": {
        "spotter": "Labradoodle 69",
        "weapon_system": "MLRS",
        "target_mgrs": "17QNE9779269855",
        "munition_type": "DPICM",
        "timing": "Immediate"
      }
    }
  ],
  "notes": "MGRS grid reference. Fire missions have a numbered template format."
}
```

### 11. cyber_ew -- Cyber & EW Events

Electronic warfare and cyber reports.

```json
{
  "raw_line": "[15:20:00] CyberCOM_OPS: Ref network A, event: Lateral movement of compromised account to AOC file server",
  "channel": "#c2_coord",
  "sender": "CyberCOM_OPS",
  "content": "Ref network A, event: Lateral movement of compromised account to AOC file server",
  "expected_update_type": "cyber_ew",
  "expected_entities": [
    {
      "metadata": {
        "network_id": "A",
        "event_type": "lateral movement",
        "affected_system": "AOC file server"
      }
    }
  ],
  "notes": ""
}
```

### 12. sitrep -- SITREP / Handover Reports

Structured status reports, often at shift change. Information-dense.

```json
{
  "raw_line": "[14:03:36] HYDRO_SL: SITREP / AIR: ZEUS 12,13,14 shot down by TTG; YAMA11 shot down / LRSAMs: / NAVAL SAMs: / OTHER: //",
  "channel": "#c2_coord",
  "sender": "HYDRO_SL",
  "content": "SITREP / AIR: ZEUS 12,13,14 shot down by TTG; YAMA11 shot down / LRSAMs: / NAVAL SAMs: / OTHER: //",
  "expected_update_type": "sitrep",
  "expected_entities": [
    {"callsign": "ZEUS12", "operational_status": "DESTROYED"},
    {"callsign": "ZEUS13", "operational_status": "DESTROYED"},
    {"callsign": "ZEUS14", "operational_status": "DESTROYED"},
    {"callsign": "YAMA11", "operational_status": "DESTROYED"}
  ],
  "notes": "Multi-entity report. 5-10 state changes per message is typical for SITREPs."
}
```

### 13. environmental -- Environmental & Admin

Weather, base conditions.

```json
{
  "raw_line": "[16:15:00] WOC_OPS: Lightning within 5, all aircraft on a ground stop.",
  "channel": "#c2_coord",
  "sender": "WOC_OPS",
  "content": "Lightning within 5, all aircraft on a ground stop.",
  "expected_update_type": "environmental",
  "expected_entities": [
    {
      "metadata": {"condition": "lightning within 5 NM", "effect": "ground stop all aircraft"}
    }
  ],
  "notes": "'Within 5' = within 5 nautical miles."
}
```

## Handling Ambiguous Cases

Some messages could reasonably be labeled with more than one type. Here is how to handle common ambiguities.

### "copy harpy12 harpy14 all shot down" -- status_change or none?

This is a **status_change**. Even though the speaker is acknowledging someone else's report, the message itself communicates that platforms were destroyed. Label with the information content, not the speech act.

```json
{
  "content": "@Hydro_SL: copy harpy12 harpy14 thor13 shark14 all shot down",
  "expected_update_type": "status_change",
  "expected_entities": [
    {"callsign": "HARPY12", "operational_status": "DESTROYED"},
    {"callsign": "HARPY14", "operational_status": "DESTROYED"},
    {"callsign": "THOR13", "operational_status": "DESTROYED"},
    {"callsign": "SHARK14", "operational_status": "DESTROYED"}
  ],
  "notes": "Acknowledgment WITH information. Label based on content, not intent."
}
```

### SITREP vs status_change

If the message uses SITREP format (structured sections with slashes), label it **sitrep**. If it is a single platform status report, label it **status_change**. The eval harness gives credit for either when both are reasonable.

### Tasking that mentions a threat

If the primary action is tasking an asset (e.g., "SINATRA DIRECTS HADES31 STRIKE ENEMY CARRIER TN TM707"), label it **tasking**. The threat information is context, not the primary update.

### Messages with multiple update types

Pick the **primary** type. If a message contains both a weapon launch and a status change ("4xSM6 launched, now winchester"), label it **weapons** (the launch is the new information; winchester is a consequence). Use the `notes` field to flag the secondary type.

### Pure acknowledgments

These are always **none**: `.`, `..`, `c`, `copy`, `roger`, `affirm`, `wilco`, `NSTR`, `word`.

Exception: if the acknowledgment repeats or adds information (see "copy ... all shot down" above), it is NOT none.

### Exercise control messages

STARTEX, ENDEX, "this is an exercise" -- label as **none**. These are not world-state changes.

### Radio checks

"Radio check", "loud and clear", "how copy" -- label as **none**.

### Requests for information

"What's the status of ORCA01?" -- label as **none** unless the question contains world-state information (e.g., "ORCA01 is still winchester, any tanker available?").

## Labeling Workflow

### 1. Pick messages to label

Start with the typed chat channels from DASH 3 (23 Sep):
- `#c2_coord` -- highest value, most information-dense
- `#isr_reports` -- threat assessments and intelligence
- `#fires` -- weapons and fire missions
- `#jprc` -- CSAR events

Skip STT channels (`#stt_*`) for this round -- they need denoising first.

### 2. Use the replay output for reference

Run a small replay and look at what the pipeline currently extracts. This helps calibrate your labels:

```bash
python scripts/replay_test.py --count 50
```

### 3. Label in a text editor

Open `data/labels/dash3_typed_chat.jsonl` and add one JSON object per line. Use the examples above as templates.

### 4. Validate your labels

Each JSON object must have these fields:
- `raw_line` (string) -- the full IRC line with timestamp
- `channel` (string) -- IRC channel name
- `sender` (string) -- who sent the message
- `content` (string) -- message content without timestamp/sender prefix
- `expected_update_type` (string) -- one of the 13 types or `none`
- `expected_entities` (array) -- list of entity objects (empty array for `none`)
- `notes` (string) -- any labeling notes or ambiguity flags

Validate with:

```bash
python -c "
import json, sys
with open('data/labels/dash3_typed_chat.jsonl') as f:
    for i, line in enumerate(f, 1):
        try:
            obj = json.loads(line)
            assert 'expected_update_type' in obj, 'missing expected_update_type'
            assert 'expected_entities' in obj, 'missing expected_entities'
        except Exception as e:
            print(f'Line {i}: {e}')
            sys.exit(1)
print(f'Validated {i} labels -- all OK')
"
```

## Target Distribution

Aim for roughly this distribution in the 100 labeled messages (matching real DASH frequency):

| Type | Target count | Notes |
|------|-------------|-------|
| none (noise) | 25-30 | Acks, banter, radio checks |
| entity_id | 8-10 | "TN XXXXX is ..." |
| status_change | 10-12 | RTB, shot down, equipment failure |
| weapons | 5-8 | Launches, splash, winchester |
| location | 8-10 | Lat/lon, cigar, bullseye |
| threat | 8-10 | TACREPs, intel reports |
| tasking | 10-12 | BattleCOA, commit, directives |
| fuel | 5-7 | F+ states, tanker plans |
| handover | 3-5 | SADC, BMA transfers |
| csar | 2-3 | Bailout reports |
| fire_mission | 1-2 | MLRS, artillery |
| cyber_ew | 2-3 | Cyber and EW events |
| sitrep | 2-3 | Structured handover reports |
| environmental | 1-2 | Weather, base conditions |

## Entity Field Reference

These are the fields available on each entity object. Only include fields that are explicitly stated or clearly implied in the message. Leave everything else out.

| Field | Type | When to use |
|-------|------|-------------|
| `track_number` | string | "TN 44504", "TM677", "L2079" |
| `callsign` | string | "ORCA01", "ZEUS12", "RR15" |
| `platform_type` | string | "E-2D", "DDG", "J-15", "PATRIOT" |
| `affiliation` | string | FRIEND, HOSTILE, NEUTRAL, UNKNOWN |
| `operational_status` | string | OPERATIONAL, DEGRADED, INOP, RTB, DESTROYED |
| `latitude` | float | Decimal degrees (negative for South) |
| `longitude` | float | Decimal degrees (negative for West) |
| `bearing` | float | Degrees from reference point |
| `range_nm` | float | Nautical miles from reference point |
| `altitude_ft` | float | Feet |
| `speed_kts` | float | Knots |
| `heading` | float | Degrees |
| `weapon_type` | string | "SM6", "TLAM", "AMRAAM" |
| `weapon_qty_launched` | int | Number fired |
| `weapon_qty_remaining` | int | Number remaining |
| `fuel_state` | string | "F+40", "playtime 15 min" |
| `subsystem_status` | string | "radar inop", "CIWS out of ammo" |
| `metadata` | object | Overflow for anything else (key-value pairs) |

## Tips

- **When in doubt, label it.** A false positive is less harmful than a false negative for our use case. We want the pipeline to extract aggressively.
- **Normalize callsigns.** Remove spaces: "ZEUS 12" becomes `"ZEUS12"`. Remove punctuation: "HARPY-12" becomes `"HARPY12"`.
- **Use the notes field.** If you are unsure about a label, write down why. This helps resolve disagreements later.
- **Label what is said, not what you infer.** If the message says "gadget bent", label subsystem_status as "radar inop" because that is what "gadget bent" means. But do not infer the platform is "DEGRADED" if the message says "RTB" -- label operational_status as "RTB".
- **One type per message.** Even if a message contains multiple types of information, pick the primary one.

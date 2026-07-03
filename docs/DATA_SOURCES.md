# Data Sources

How to access DASH event data on Pydio and what we've already downloaded.

## Pydio Access

**URL:** `https://pydio.example.mil`
**WebDAV base:** `/dav/dash-mef/`
**Auth:** Personal Access Token (PAT) — file a DLE support ticket if you don't have one. This is a separate token from your DLE/GitLab PAT.

### Download Tool

```bash
# Explore the directory tree
python scripts/explore_and_download_chat.py explore /dav/dash-mef/ --max-depth 2

# Search for specific file types
python scripts/explore_and_download_chat.py explore /dav/dash-mef/ --pattern "chat|irc|speech"

# Download all chat-related files
python scripts/explore_and_download_chat.py download --output data/chat
```

Configure your credentials in the script: `DLE_USER` and `PAT_FILE` at the top of `scripts/explore_and_download_chat.py`.

## Pydio Directory Structure

```text
/dav/dash-mef/
  Dash1-PAE/          # April 2025 — Perceive Actionable Entity
    Academics/
    Briefings/
    Data/              # Per-day, per-team folders
    Sim/
  Dash2-MEF/          # July 2025 — Match Effectors
    Academics/         # Contains BattleEffectSchemaV2.json, GenMSG field docs
    Briefings/
    Data/              # Per-day folders + _vendor_misc/ with chat rules
    Misc/Scripts/      # GenMSG TCP/UDP listener scripts
    Sim/MACE/
  Dash3-GBC/          # September 2025 — Battle COA Generation
    .http/irc/         # IRC server config (config.json)
    .http/docs/        # GenMSG field documentation
    Academics/
    Briefings/
    Data/              # Per-day, per-team folders with chat.zip + GenMSG
    Sim/MACE/
```

## Chat Data Inventory

### DASH 1 PAE (April 2025) — 26 files

| Type | Files | Format | Content |
| ---- | ----- | ------ | ------- |
| c2.log.txt | 10 | `timestamp sender: message` | C2 coordination typed chat |
| stt.log.txt | 12 | `timestamp google-speech-to-text: text` | Voice-to-text transcripts |
| omnichat.log | 1 | Same as c2.log | Combined chat log |
| Other logs | 3 | Various | testing.log, raft.log |

### DASH 2 MEF (July 2025) — 46 files

| Type | Files | Format | Content |
| ---- | ----- | ------ | ------- |
| Chat logs | 3 | Plain text | Full exercise chat + MIRC channel export |
| Chat rules JSON | 7 | JSON regex rules | Track number extraction patterns (versions 2-4) |
| Chat rule exports | 2 | JSON timeline | Pre-parsed and annotated chat (494KB each) |
| Weapons log | 1 | Plain text | Weapons loadout tracking (9.5MB) |
| Link 16 log | 1 | J13.2 format | Link 16 message log (854KB) |
| Other | ~32 | Various | Dashboard icons, misc (false positives from search) |

### DASH 3 GBC (September 2025) — 68 files

| Type | Files | Format | Content |
| ---- | ----- | ------ | ------- |
| chat.zip | 12 | IRC channel logs | 8-21 per-channel files per zip (Combined, ISR, Fires, etc.) |
| Parsed chat zips | 6 | Per-COA text files | Pre-parsed chat broken down by BattleCOA |
| IRC chat zips | 2 | IRC logs | Sep 18 & 19 morning sessions |
| Per-COA stats | 31 | Text files | Individual COA chat + stats + curated version |
| AircraftPlatformStatus | varies | Link 16 J-series | Track data from MACE simulation |

## Schemas and Config Files

Downloaded to `docs/DASH/downloaded/` in the equifinality repo (copy relevant ones here):

| File | Location | Description |
| ---- | -------- | ----------- |
| BattleEffectSchemaV2.json | `schemas/` | Entity model: track, position, velocity, platform, tags |
| Parsed Info Fields GenMSG v2.txt | `schemas/` | GenMSG track fields documentation |
| PAE-API-README.md | `schemas/` | SSE streaming API for battle effects |
| GenMsg225 sample JSON | `schemas/` | 20MB sample of actual GenMSG data |
| irc config.json | `config/` | IRC WebSocket server URL + channel list |
| GenMSG TCP Listener | `config/` | Python TCP socket listener for track feed |
| GenMSG UDP Listener | `config/` | Python UDP multicast listener for track feed |
| Chat rules JSON (v2-v4) | `config/` | Regex patterns for track number extraction |

## Related Data in Equifinality Repo

The `equifinality` repo (`../equifinality/`) has processed entity catalogs useful for this pipeline:

| File | Content |
| ---- | ------- |
| `data/processed/forces.csv` | 266 Blue+Red forces with callsigns, platform types |
| `data/processed/force_locations.csv` | 1,775 force location records with coordinates |
| `data/processed/schema_assets.csv` | 122 Blue assets with platform type, supercategory |
| `data/processed/schema_effects.csv` | 3,465 capability-target effect rules |
| `config/dash_target_taxonomy.csv` | Target class hierarchy with aliases |
| `data/processed/theater_geometry_polygons.geojson` | GeoJSON polygon boundaries for BMAs, holding areas, tanker tracks (from ACO) |
| `scripts/geocode_dash_locations.py` | ACO parser — resolves named areas to coordinates |
| `docs/GLOSSARY.md` | 50+ weapons, 100+ acronyms |
| `docs/DASH/SCENARIO_REFERENCE.md` | 122 callsigns, 77 targets, geographic locations |

**Note on theater geometry:** The GeoJSON polygons and named area boundaries are from the DASH 3 GBC scenario. The MASH event will likely use a different theater with different BMAs, holding areas, and tanker tracks. However, the *structure* and *types* of named areas will be similar. When the MASH scenario data becomes available, the same ACO parsing tooling (`scripts/geocode_dash_locations.py`, `scripts/export_aco_polygons.py`) can generate updated geometry. In the meantime, the DASH 3 data is useful for development and testing — e.g., resolving "MANDALAY BMA" or "SEAHAWKS track" to polygon boundaries.

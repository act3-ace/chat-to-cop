# Session Notes - 2026-05-20

## Context

MASH live event ongoing. Mia Kollia running chat-to-cop on-site with GenAI.mil
and AG Bedrock Sonnet backends. Hamilton supporting remotely. Multi-party
collaboration forming around MASH data replay capability.

## Completed This Session

### Structured output failure detection (committed, pushed)
- Auto-detects models that return text instead of JSON (input_type=str/nonetype
  in pydantic errors) and disables instructor retries to avoid wasting LLM calls.
- Mia hit this with GenAI.mil's Gemini backend returning conversational text.
- `_effective_retries()` returns 0 after any structured output failure; resets on
  success. PermanentError raised immediately instead of 2 wasted retries.
- 6 new tests in TestStructuredOutputFailureDetection.
- Commits: e6e4496, b1bd27e

### run.bat --live + --cloud support (committed, pushed)
- run.bat now accepts `--live ws://SERVER --cloud http://REMOTE/v1` together.
- Eliminates need for shell-specific env var syntax (CMD `set` vs PS `$env:`).
- Tested all 4 arg patterns in both CMD and PowerShell on Windows 11.
- Commit: 4872d3d

### AG Bedrock Sonnet instance for Mia
- Job 1430 on AG (172.33.69.153, port 4000, 6hr walltime).
- Bedrock Sonnet extracts ~70% more threats/taskings and runs 2x faster than
  T4 7B. Command for Mia:
  `run.bat --live ws://IRC_SERVER:8097 --cloud http://172.33.69.153:4000/v1`

### MASH data replay research
Comprehensive audit of replay/re-streaming capabilities across all MASH services,
chat-to-cop, and Colin Leong's equifinality repos.

Key findings:
- Track Manager has built-in `POST /StreamGenMsgFile` with configurable delay
- IRC Multiplexer archives to disk + provides SSE stream
- DELTRON has paginated message history + SSE stream
- All services have SSE endpoints for real-time subscription
- Colin's equifinality repos (3 on DLE GitLab) are batch ETL only -- no replay
- chat-to-cop has proven chat replay but no track/entity data replay yet
- Leidos/CRONUS aggregator on DLE is essentially the same pattern as StreamGenMsgFile

### Multi-party replay collaboration (email thread)
- Kathleen Dipple (AFRL/RYZA CTR) interested in replay; connected to Leidos
  vendor (Andrew Molnar) who used CRONUS aggregator from UDRI at MASH
- Colin Leong has initial-state parser for DASH 3 data (coord cards, effector
  plays, ACO, MACE files) with typo correction and conflict reconciliation
- Juan Vasquez cc'd on thread
- Drafted summary email mapping what exists vs what needs building

## Data Replay Architecture (from research)

Three tiers of replayable data:

**Tier 1 -- IRC chat (solved):** replay.py handles DASH 1/2/3 chat.zip archives.
IRC Multiplexer archives produce the same format after each event.

**Tier 2 -- Track data (infrastructure exists):** Track Manager's StreamGenMsgFile
replays GenMSG files natively. GenMSG data available from DASH 2/3 on Pydio.
TCP (port 5001) and UDP multicast (224.9.8.63:5003) listeners documented.

**Tier 3 -- Mission/Effects/Events (snapshottable):** MissionManager has timestamped
COA assignments, EffectsManager has weapon Pk tables (static reference),
DELTRON has scored messages with entity bins. All queryable via REST.

**Gap:** No one is currently recording SSE streams during the live event. Need
explicit data capture before MASH network teardown. The orchestrator that
coordinates multi-stream replay with timestamp alignment doesn't exist yet.

## MASH Service SSE Endpoints (discovered)

| Service | Endpoint | Content |
|---------|----------|---------|
| Track Manager :3021 | GET /tracks-sse | Track create/update events |
| Track Manager :3021 | POST /StreamGenMsgFile | Replay GenMSG file with delay |
| DELTRON :3060 | GET /messages-sse | Scored/classified messages |
| IRC Multiplexer :3080 | GET /api/archive/stream | Live IRC message stream |
| Event Manager :3016 | GET /paeoutputs-sse | PAE output events |

## Commits This Session

- e6e4496: auto-disable instructor retries when model can't produce JSON
- 4872d3d: support --live and --cloud together in run.bat
- b1bd27e: remove unused _max_retries_configured field

All pushed to DLE GitLab. GitHub mirror is behind (at d8551fb).
1170 tests passing.

## Next Steps

1. Capture MASH data before teardown: DELTRON message history, Track Manager
   snapshot, IRC archive
2. Coordinate with Colin on aligning his SQLite schema with Track Manager's
   OpenAPI spec for shared ground truth
3. Build multi-stream replay orchestrator (coordination layer, not rebuild)
4. Sync GitHub mirror

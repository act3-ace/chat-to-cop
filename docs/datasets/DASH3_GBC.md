# Dataset Card: DASH 3 Global Battlestaff Challenge Chat Logs

**Version:** 1.0
**Date:** 2026-03-29
**Classification:** IL2 (Unclassified)
**Maintainer:** ACT3 / C2ES, Air Force Research Laboratory

---

## Dataset Description

IRC chat and voice speech-to-text (STT) logs collected during the DASH 3 Global Battlestaff Challenge (GBC) wargame exercise, conducted in September 2025 by ACT3/C2ES at AFRL. The dataset captures real-time tactical communications between exercise participants (battle managers, intelligence analysts, fires coordinators, tanker controllers, and exercise control staff) operating in a synthetic Pacific theater scenario.

This dataset is the primary test and validation corpus for the chat-to-cop pipeline.

## Source

- **Collection Event:** DASH 3 GBC (Global Battlestaff Challenge), September 2025
- **Collection Method:** IRC server with WebSocket interface (port 8097); voice channels captured via automatic speech recognition (ASR) and piped into IRC as `#stt_*` channels
- **Storage:** Pydio file server at `https://pydio.dle.afrl.af.mil` under `/dav/dash-mef/Dash3-GBC/`
- **Local Path:** `data/dash3/23Sep_usaf_chat.zip` (committed to repository)

## Composition

| Attribute | Value |
|-----------|-------|
| **Total messages** | 935 |
| **Channels** | 11 (typed chat + voice STT) |
| **Date** | 23 September 2025 |
| **Team** | USAF exercise cell |
| **Format** | ZIP archive containing per-channel `.log` files and `combined.log` |
| **File size** | ~120 KB (compressed) |

### Channel Breakdown

| Channel | Type | Description | Messages (approx) |
|---------|------|-------------|-------------------|
| `#c2_coord` | Typed chat | Command and control coordination -- highest information density | High |
| `#isr_reports` | Typed chat | Intelligence, surveillance, reconnaissance reports | Medium |
| `#vegas_internal` | Typed chat | Internal coordination (most active channel by volume) | High |
| `#jprc` | Typed chat | Joint Personnel Recovery Center -- CSAR coordination | Low |
| `#fires` | Typed chat | Fires coordination and weapons employment | Low |
| `#stt_C2Coord` | Voice STT | ASR transcription of C2 coordination voice net | Medium |
| `#stt_crusherBMA` | Voice STT | ASR transcription of Crusher BMA voice net | Medium |
| `#stt_hydroBMA` | Voice STT | ASR transcription of Hydro BMA voice net | Medium |
| `#stt_taipanBMA` | Voice STT | ASR transcription of Taipan BMA voice net | Low |
| `#stt_mesquiteBMA` | Voice STT | ASR transcription of Mesquite BMA voice net | Low |
| `#stt` | Voice STT | General/unassigned STT channel | Low |

### Message Format

Each log line follows one of two formats:

```
# Per-channel log file:
[HH:MM:SS] sender: message content

# Combined log file:
[HH:MM:SS] #channel_name sender: message content
```

Parsed by `src/chat_to_cop/ingestion/replay.py` into `IRCMessage` objects with fields: `timestamp`, `channel`, `sender`, `content`, `raw_line`.

### Content Types

Messages contain military tactical communications including:
- **Entity reports:** Track numbers (e.g., TM636, TN44504), callsigns (ORCA01, HADES31), platform types (E-2D, DDG, F-35)
- **Status updates:** Operational status, weapons states (winchester, RTB), fuel states (F+40, playtime 15 min)
- **Tasking:** Kill chain assignments, weapons release authority, handovers between BMAs
- **Location data:** Bullseye bearings (270/40), grid references, named areas (MANDALAY BMA)
- **Threat assessments:** Target identification, threat axis descriptions
- **Coordination:** Acknowledgments ("copy"), corrections ("disregard last"), radio checks
- **Exercise control:** White cell (WF_*) administrative messages

## Annotation Status

**Not annotated.** This dataset does not have human-labeled ground truth for extraction evaluation. The chat-to-cop evaluation harness (`scripts/eval_models.py`) uses separately generated synthetic labeled data for quantitative metrics.

A labeling guide exists at [docs/LABELING_GUIDE.md](../LABELING_GUIDE.md) for future annotation efforts.

## Known Issues

### STT Channel Quality

Voice STT channels (`#stt_*`) contain significant ASR noise:
- **Radio checks and protocol phrases:** "Radio check 1-2-3-4-5, 5-4-3-2-1" provides no operational information
- **Crosstalk:** Multiple speakers captured simultaneously produce garbled text
- **Mangled numbers:** ASR frequently misrecognizes track numbers, bearings, and fuel quantities (e.g., "forty-four five oh four" may render as "4454" instead of "44504")
- **Filler words and false starts:** "uh... yeah... so the uh... two more down west of the river"
- **Estimated noise ratio:** ~60% of STT messages contain no actionable information

The fusion agent cross-references STT reports against typed chat to validate or discard noisy data.

### Timestamp Resolution

Log files contain only time-of-day (`HH:MM:SS`), not full dates. The replay parser infers the date from the directory path (e.g., `23Sep` -> 2025-09-23). Sub-second timing is not available.

### Single Exercise Day

This dataset covers a single exercise day (23 September 2025). It does not capture learning effects across multiple days, scenario variations, or different force compositions. Other DASH 3 exercise days exist on Pydio but have not been processed for this pipeline.

### Scenario-Specific Terminology

The dataset uses DASH 3 GBC scenario-specific terminology (BMA names, callsigns, geographic references) that may not generalize to other exercises. The MASH event (May 2026) will use a different scenario.

## Ethical Considerations

- **No PII.** Speaker usernames are exercise callsigns (e.g., `WF_BMA_03`, `Hydro_Tank`), not real names. No personal information is present.
- **Synthetic scenario.** All tactical data (targets, forces, locations, events) is from a simulated wargame exercise, not real-world operations.
- **IL2 classification.** The data has been reviewed and determined to be unclassified. No classified information, methods, or capabilities are present.
- **Government data.** Collected by US government personnel during a government-organized exercise. No third-party data rights apply.

## Usage in This Project

| Use | Details |
|-----|---------|
| **Development testing** | Full pipeline replay: `python -m chat_to_cop.replay data/dash3/23Sep_usaf_chat.zip` |
| **Benchmark baseline** | 935-message replay provides throughput, latency, and degradation behavior metrics |
| **Channel agent validation** | Multi-channel fan-out, speaker model learning, conversation window management |
| **Fusion agent validation** | Cross-channel deconfliction between typed chat and STT channels |
| **Regex backend validation** | Track number, fuel state, and weapons pattern extraction from real military jargon |

### Reproduction

```bash
# Full replay with local Ollama
python -m chat_to_cop.replay data/dash3/23Sep_usaf_chat.zip \
    --url http://127.0.0.1:11434/v1 \
    --model qwen2.5:7b \
    --db /tmp/dash3_replay.db

# Quick smoke test (first 5 messages)
python scripts/quick_test.py --url http://127.0.0.1:11434/v1 --model qwen2.5:7b
```

## Related Datasets

| Dataset | Status | Notes |
|---------|--------|-------|
| DASH 1 PAE (April 2025) | Downloaded, not processed | Earlier exercise, different format |
| DASH 2 MEF (July 2025) | Downloaded, not processed | Includes chat rules JSON with track extraction patterns |
| DASH 3 other days | On Pydio, not downloaded | Additional exercise days with different COA variants |
| Synthetic labeled data | Generated | Used by eval harness for quantitative metrics |

## Revision History

| Date | Version | Changes |
|------|---------|---------|
| 2026-03-29 | 1.0 | Initial dataset card |

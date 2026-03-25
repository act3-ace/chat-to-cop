# chat-to-cop

Real-time extraction of world-state updates from military IRC chat and voice-to-text streams, pushed to a Common Operating Picture (CoP) database for the MASH event (May 2026).

## Problem

During DASH/MASH wargame events, battle managers communicate critical operational information through mIRC chat channels and voice radio nets. Vendor AI tools rely on a structured database (the CoP) to function, but this database is **not automatically updated** from chat or voice. In previous events, there was no shared database at all — vendors wasted 3-5 days per two-week sprint just parsing chat themselves.

For the upcoming MASH event, a centralized CoP database will exist for the first time. **Our job is to keep it current from chat and voice in real-time** — faster than the white cell humans can manually enter the information.

## What This Is NOT

This is not NER (Named Entity Recognition) in the traditional sense. We're not just tagging entities — we're interpreting **any world-state change** communicated in chat or voice and pushing it as a structured update to the CoP. Examples:

- "TN 44504 is DDG1" → update track classification
- "ORCA01 gadget bent, RTB" → mark platform radar-inop, status RTB
- "wings fell off" → operational_status: NON_OPERATIONAL
- "24 TLAMS launched, 38 remaining" → decrement weapons inventory
- "Lightning within 5, all aircraft ground stop" → set base status WEATHER_HOLD

## Architecture

```
IRC WebSocket (all channels: chat + voice STT)
        |
        v
[Tier 1: Regex/Rules] ~0ms
  Filter acks ("c", "copy"), extract track numbers,
  callsigns, coordinates. Fast-path known patterns.
        |
        v  (needs interpretation)
[Tier 2: Local LLM] ~0.3-0.6s
  Qwen 3 30B MoE on Ollama with JSON schema output
  System prompt with exercise glossary + few-shot examples
        |
        v  (ambiguous/complex — rare)
[Tier 3: Cloud API fallback] ~0.5-1.5s
  Gemini 2.5 Flash / Claude 4.5 Haiku
        |
        v
[CoP REST API] → Track Manager / SmartPack Manager
```

Target: **<2 seconds** from message to database update. At 10-30 messages/minute, even a single desktop GPU is massively over-provisioned.

## Event Infrastructure (from DASH 3 data)

| System | Protocol | Address | What It Carries |
|--------|----------|---------|-----------------|
| IRC/Chat | WebSocket | `ws://10.5.185.72:8097` | All chat + voice STT channels |
| GenMSG | TCP | `10.5.185.9:5001` | Track updates (Link 16) |
| GenMSG | UDP multicast | `224.9.8.63:5003` | Track updates (multicast) |
| Battle Effects API | HTTP SSE | `http://<IP>/api/events/battle-effects/v2` | Vendor output stream |

Voice is already transcribed and piped into IRC as `#stt_*` channels — we only need one connection point.

### IRC Channels

| Channel | Content | Priority |
|---------|---------|----------|
| `#c2_coord` | C2 coordination — primary ops channel | HIGH |
| `#isr_reports` | Intelligence/surveillance reports | HIGH |
| `#fires` | Fires coordination | HIGH |
| `#jprc` | Personnel recovery (CSAR) | MEDIUM |
| `#stt_C2Coord` | Voice-to-text from C2 coord | MEDIUM |
| `#stt_hydroBMA` | Voice-to-text from Hydro BMA | MEDIUM |
| `#stt_crusherBMA` | Voice-to-text from Crusher BMA | MEDIUM |
| `#stt_mesquiteBMA` | Voice-to-text from Mesquite BMA | MEDIUM |
| `#stt_taipanBMA` | Voice-to-text from Taipan BMA | MEDIUM |
| `#vegas_internal` | Exercise control (useful for context) | LOW |

## Key Documents

| Document | Description |
|----------|-------------|
| [docs/CHAT_DATA_ANALYSIS.md](docs/CHAT_DATA_ANALYSIS.md) | Taxonomy of 13 world-state update types with real examples from DASH 1-3 |
| [docs/LLM_BENCHMARKS.md](docs/LLM_BENCHMARKS.md) | March 2026 LLM speed/capability research for cloud and local deployment |
| [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md) | What's on Pydio, what we downloaded, how to access DASH data |
| [docs/SCHEMAS.md](docs/SCHEMAS.md) | BattleEffectSchemaV2, GenMSG fields, CoP database model |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Detailed pipeline design and deployment options |

## Quick Start

```bash
# 1. Download chat data from Pydio (requires PAT)
python scripts/explore_and_download_chat.py download --output data/chat

# 2. (Coming soon) Run the pipeline against downloaded chat logs
# python -m chat_to_cop.replay data/chat/Dash3-GBC/Data/23Sep/usaf/chat/

# 3. (Coming soon) Connect to live IRC for real-time processing
# python -m chat_to_cop.live --server ws://10.5.185.72:8097
```

## Team

| Person | Role |
|--------|------|
| **Scott Clouse** | ACT3 CAIO — project lead, hardware/access |
| **Colin Leong** | FACS-NCA contractor — DASH data expertise, equifinality repo |
| **Mia Kollia** | Developer — pipeline implementation |

## Related Projects

- **[equifinality](https://gitlab.dle.afrl.af.mil/COLIN.LEONG/equifinality)** — Colin's DASH data analysis repo (kill webs, BattleCOAs, capability matrices). Contains parsed entity catalogs, target taxonomies, and Pydio download tooling we can reuse.
- **FACS-NCA** — MCP-based F2T2EA kill-web pipeline; related architecture patterns.

## Classification

DASH event data is **IL2** through the MASH event. Becomes CUI after.

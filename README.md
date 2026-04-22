# chat-to-cop

A team of AI agents that watch military IRC chat and voice-to-text streams, maintain situational awareness, learn operator patterns, and push structured world-state updates to a Common Operating Picture (CoP) database. Built for the MASH event (May 2026).

## Why This Exists

During DASH/MASH wargame events, battle managers communicate critical operational information through mIRC chat channels and voice radio nets. The CoP database is the experiment's oracle — vendors poll it via REST API — but it's not automatically updated from chat or voice. In previous events, vendors wasted 3-5 days per sprint just parsing chat themselves. The real-time streaming contractor was cut.

**Our job is to keep the CoP current from chat and voice in real-time** — faster than the white cell humans can manually enter the information.

## What This Is NOT

This is not NER. This is not an ETL pipeline. We're building **AI staff officers** — agents that interpret any world-state change communicated in chat or voice and push it as a structured update to the CoP:

- "TN 44504 is DDG1" -> update track classification
- "ORCA01 gadget bent, RTB" -> mark platform radar-inop, status RTB
- "wings fell off" -> operational_status: NON_OPERATIONAL
- "24 TLAMS launched, 38 remaining" -> decrement weapons inventory
- "Lightning within 5, all aircraft ground stop" -> set base status WEATHER_HOLD

## Architecture

```
IRC/STT WebSocket
       |
  Message Router (async Python)
       |
  +----+------------------------+
  |   Agent per channel          |
  |   +------------------------+ |
  |   | Conversation window    | |  <- sliding context of raw chat
  |   | Speaker models         | |  <- learned per-user: role, AOI, jargon
  |   | World state snapshot   | |  <- current believed battlespace state
  |   | Degrading LLM backend  | |  <- best model -> smaller -> regex -> passthrough
  |   +------------------------+ |
  |          |                   |
  |     Tool calls:              |
  |     - emit_cop_update()      |
  |     - flag_uncertainty()     |
  |     - update_speaker()       |
  +-------------+----------------+
                |
         Fusion Agent (semantic deconfliction)
                |
         World State Store (SQLite -> CoP REST API)
```

Each channel agent is **stateful** — it maintains a running conversation window, learns who's talking and what they care about, and outputs structured CoP updates. The fusion agent reconciles overlapping reports from different channels using semantic similarity, not field matching.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full design.

## Design Philosophy

This system embodies three ideas from ACT3's research portfolio:

- **Equifinality** — Multiple paths to the same CoP update (large model, small model, regex, passthrough, cross-channel fusion). Kill one path, the others still converge.
- **Antifragility** — Stress produces information that improves future performance. Backend failures teach the system which backends are reliable for which message types.
- **FACS** — The agent pool is a family of autonomous systems: heterogeneous, loosely coordinated, and configurable at runtime.

See [docs/DESIGN_PHILOSOPHY.md](docs/DESIGN_PHILOSOPHY.md) for how this connects to the broader research program.

## Model Agnosticism

The system is deliberately model-agnostic. Every LLM call goes through the OpenAI-compatible chat completions API. Structured output uses `instructor` + Pydantic. No vendor SDK, no framework, no ecosystem lock-in.

| Backend | When |
|---------|------|
| Ollama (local) | Air-gapped, development, MASH default |
| vLLM (local) | GPU cluster, production |
| Ask Sage API | IL5, `api.genai.army.mil` |
| AWS Bedrock | GovCloud |
| Any OpenAI-compatible endpoint | Future-proof |

## Graceful Degradation

Every agent degrades independently. Every message gets *something* written to the CoP.

| Level | What Happens | Latency |
|-------|-------------|---------|
| **Normal** | Best available model, full speaker modeling | ~0.5-1s |
| **Degraded** | Smaller/faster model, speaker models frozen | ~0.2-0.5s |
| **Minimal** | Regex/keyword extraction only | ~0ms |
| **Passthrough** | Raw message forwarded with "unprocessed" flag | ~0ms |

The supervisor monitors agent health and manages transitions. The system never silently stops updating.

## Research Questions

Two fundamental problems are embedded in this architecture:

1. **Online user modeling** — How does a software agent personalize/learn a model of the user *during* operations? Each agent builds speaker models that grow over the session.
2. **Ontology-free team adaptation** — How can a heterogeneous team of teams adapt without predefined ontology or comms protocol? The LLM serves as a universal translator between team vocabularies.

## Event Infrastructure (from DASH 3)

| System | Protocol | Address | Content |
|--------|----------|---------|---------|
| IRC/Chat | WebSocket | `ws://10.5.185.72:8097` | All chat + voice STT channels |
| GenMSG | TCP | `10.5.185.9:5001` | Track updates (Link 16) |
| GenMSG | UDP multicast | `224.9.8.63:5003` | Track updates (multicast) |

Voice STT is already piped into IRC as `#stt_*` channels — one connection point for everything.

## Quick Start

```bash
# 1. Install dependencies
pip install -e ".[dev]"

# 2. Download chat data from Pydio (requires PAT)
python scripts/explore_and_download_chat.py download --output data/chat

# 3. Replay DASH chat logs through the agent pipeline
python -m chat_to_cop.replay data/chat/Dash3-GBC/Data/23Sep/usaf/chat/

# 4. Connect to live IRC for real-time processing
python -m chat_to_cop.live --server ws://10.5.185.72:8097
```

## Key Documents

| Document | Description |
|----------|-------------|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Agent architecture, degradation strategy, component design |
| [docs/DESIGN_PHILOSOPHY.md](docs/DESIGN_PHILOSOPHY.md) | Equifinality, antifragility, FACS connection |
| [docs/CHAT_DATA_ANALYSIS.md](docs/CHAT_DATA_ANALYSIS.md) | Taxonomy of 13 world-state update types with real DASH examples |
| [docs/LLM_BENCHMARKS.md](docs/LLM_BENCHMARKS.md) | March 2026 LLM speed/capability research |
| [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md) | Pydio access, DASH data inventory |
| [docs/SCHEMAS.md](docs/SCHEMAS.md) | BattleEffectSchemaV2, GenMSG fields, CoP database model |
| [docs/PRIOR_ART.md](docs/PRIOR_ART.md) | Vendor chat-parsing lessons from DASH events |
| [docs/MASH_GO_NO_GO.md](docs/MASH_GO_NO_GO.md) | **MASH event deployment decision tree** — pre-event checklist, decision matrix, recovery procedures, hard rules |
| [docs/MASH_GO_NOGO.md](docs/MASH_GO_NOGO.md) | **MASH production config and flip criteria** — default settings, speaker model flip gate, sign-off chain |
| [docs/DEMO_RUNBOOK.md](docs/DEMO_RUNBOOK.md) | External-presentation demo runbook (5-message smoke test) |
| [docs/COMPARISON_DELTRON_2026-04-09.md](docs/COMPARISON_DELTRON_2026-04-09.md) | DELTRON (HLT) vs chat-to-cop comparison + Cadre of Critics review |

## Team

| Person | Role |
|--------|------|
| **Scott Clouse** | ACT3 CAIO — project originator, architecture, integration |
| **Mia Kollia** | Primary developer — LLM evaluation, pipeline implementation |
| **Colin Leong** | DASH domain expert — equifinality repo, entity catalogs |
| **Jennifer Carlet** | Pipeline engineering — LLM infrastructure, chat ingestion |
| **Jared Culbertson** | Strategic sponsor — FACS-NCA lead, schema requirements |

## Related Projects

- **[equifinality](https://gitlab.dle.afrl.af.mil/COLIN.LEONG/equifinality)** — DASH data analysis (kill webs, BattleCOAs, entity catalogs, alias resolution). Direct dependency.
- **FACS-NCA** — Family of Autonomous Combat Systems. This pipeline validates FACS principles at chat-parsing scale.

## Classification

DASH event data is **IL2** through the MASH event. Becomes CUI after.

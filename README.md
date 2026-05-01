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

## Quick Start

```bash
# Clone and install
git clone https://github.com/act3-ace/chat-to-cop.git
cd chat-to-cop
pip install -e ".[dev]"

# Run smoke test (no data download needed)
python scripts/quick_test.py

# Replay bundled DASH 3 sample through the agent pipeline
python -m chat_to_cop.replay data/dash3/23Sep_usaf_chat.zip
```

On Windows, double-click `run.bat` for guided setup.

See [docs/QUICKSTART.md](docs/QUICKSTART.md) for full instructions including
Ollama setup, eval harness, Docker, and troubleshooting.

## Key Documents

| Document | Description |
|----------|-------------|
| [docs/QUICKSTART.md](docs/QUICKSTART.md) | Setup guide (all platforms) |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Agent architecture, degradation strategy, component design |
| [docs/DESIGN_PHILOSOPHY.md](docs/DESIGN_PHILOSOPHY.md) | Equifinality, antifragility, FACS connection |
| [docs/SCHEMAS.md](docs/SCHEMAS.md) | BattleEffectSchemaV2, GenMSG fields, CoP database model |
| [docs/MASH_DEPLOYMENT.md](docs/MASH_DEPLOYMENT.md) | Full deployment guide for MASH (7 backend options) |
| [docs/GO_NOGO.md](docs/GO_NOGO.md) | MASH decision tree, config defaults, recovery procedures |
| [docs/RUNBOOK.md](docs/RUNBOOK.md) | Demo and development procedures |
| [docs/SYSTEM_CARD.md](docs/SYSTEM_CARD.md) | RAI system card |
| [docs/research/](docs/research/) | Benchmarks, speaker model results, adversarial robustness |

## Team

| Person | Role |
|--------|------|
| **Scott Clouse** | ACT3 CAIO — project originator, architecture, integration |
| **Mia Kollia** | Primary developer — LLM evaluation, pipeline implementation |
| **Colin Leong** | DASH domain expert — equifinality repo, entity catalogs |
| **Jennifer Carlet** | Pipeline engineering — LLM infrastructure, chat ingestion |
| **Jared Culbertson** | Strategic sponsor — FACS-NCA lead, schema requirements |

## Related Projects

- **equifinality** — DASH data analysis (kill webs, BattleCOAs, entity catalogs, alias resolution). Direct dependency.
- **FACS-NCA** — Family of Autonomous Combat Systems. This pipeline validates FACS principles at chat-parsing scale.

## Classification

DASH event data is **IL2** through the MASH event. Becomes CUI after.

# CLAUDE.md

## Project Overview

AI staff officer system: a team of stateful agents that watch military IRC chat and voice STT, maintain situational awareness, learn operator patterns, and push structured world-state updates to a Common Operating Picture (CoP) database. Target: MASH event, May 2026.

## Key Context

- This is an ACT3 government project for DASH/MASH wargame events
- Data is IL2 (unclassified) through the MASH event
- The CoP database schema is pending from the contractor team — design around the BattleEffectSchemaV2 and GenMSG field specs in docs/SCHEMAS.md for now
- Chat data from DASH 1-3 is available on Pydio (see docs/DATA_SOURCES.md)
- The equifinality repo (`../equifinality/`) has entity catalogs, target taxonomies, and alias resolution code that can be reused
- This system embodies equifinality, antifragility, and FACS principles — see docs/DESIGN_PHILOSOPHY.md

## Architecture

```
IRC WebSocket -> Message Router -> Channel Agents (1 per channel)
                                       |
                                  Each agent: conversation window + speaker models
                                  + world state + degrading LLM backend
                                       |
                                  Fusion Agent (semantic deconfliction)
                                       |
                                  World State Store -> CoP REST API
```

See docs/ARCHITECTURE.md for details.

## Design Principles

- **Model-agnostic**: OpenAI-compatible API everywhere. `instructor` + Pydantic for structured output. No vendor lock-in.
- **Equifinality**: Multiple paths to the same CoP update (large model, small model, regex, passthrough, cross-channel fusion).
- **Antifragile**: Component failures produce information that improves future routing.
- **Flow never stops**: Every message gets something written to CoP. Quality degrades; output never halts.
- **Stateful, not stateless**: Agents maintain conversation context, speaker models, and world state across messages.

## Common Commands

```bash
# Download DASH chat data from Pydio
python scripts/explore_and_download_chat.py explore /dav/dash-mef/ --pattern chat
python scripts/explore_and_download_chat.py download --output data/chat

# Replay DASH chat through agent pipeline
python -m chat_to_cop.replay data/chat/Dash3-GBC/Data/23Sep/usaf/chat/

# Run live against IRC server
python -m chat_to_cop.live --server ws://10.5.185.72:8097

# Run tests
pytest tests/
```

## Code Style

- Python 3.10+
- Use ruff for formatting and linting
- Type hints for function signatures
- Use loguru for logging
- Pydantic for all data models
- asyncio for all I/O

## Important Notes

- The goal is world-state extraction, NOT just NER. Any information that represents a change to the operational picture needs to be captured.
- Latency is the #1 priority. Must be faster than humans.
- "70% is great" — even basic extraction adds value. Don't over-engineer accuracy at the cost of speed.
- The IRC server uses WebSocket protocol on port 8097. Voice STT is already piped into IRC as #stt_* channels.
- White cell (WF_*) messages are the most information-dense. Battle managers communicate in abbreviated military jargon.
- Each channel agent is stateful — it maintains context, learns speakers, and tracks world state across messages.
- Every LLM call must go through the OpenAI-compatible interface. Never import a vendor-specific SDK.
- The system must degrade gracefully: LLM -> smaller LLM -> regex -> passthrough. Never silently stop.

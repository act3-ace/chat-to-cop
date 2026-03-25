# CLAUDE.md

## Project Overview

Real-time pipeline to extract world-state updates from military IRC chat and voice STT, then push structured updates to a Common Operating Picture (CoP) database. Target: MASH event, May 2026.

## Key Context

- This is an ACT3 government project for DASH/MASH wargame events
- Data is IL2 (unclassified) through the MASH event
- The CoP database schema is pending from the contractor team — design around the BattleEffectSchemaV2 and GenMSG field specs in docs/SCHEMAS.md for now
- Chat data from DASH 1-3 is available on Pydio (see docs/DATA_SOURCES.md)
- The equifinality repo (`../equifinality/`) has entity catalogs, target taxonomies, and alias resolution code that can be reused

## Architecture

IRC WebSocket → Tier 1 (regex/rules) → Tier 2 (local LLM) → Tier 3 (cloud fallback) → CoP REST API

See docs/ARCHITECTURE.md for details.

## Common Commands

```bash
# Download DASH chat data from Pydio
python scripts/explore_and_download_chat.py explore /dav/dash-mef/ --pattern chat
python scripts/explore_and_download_chat.py download --output data/chat

# (Future) Run pipeline
# python -m chat_to_cop.replay data/chat/...
# python -m chat_to_cop.live --server ws://...
```

## Code Style

- Python 3.10+
- Use ruff for formatting and linting
- Type hints for function signatures
- Use loguru for logging

## Important Notes

- The goal is world-state extraction, NOT just NER. Any information that represents a change to the operational picture needs to be captured.
- Latency is the #1 priority. Must be faster than humans.
- "70% is great" — even basic extraction adds value. Don't over-engineer accuracy at the cost of speed.
- The IRC server uses WebSocket protocol on port 8097. Voice STT is already piped into IRC as #stt_* channels.
- White cell (WF_*) messages are the most information-dense. Battle managers communicate in abbreviated military jargon.

# Architecture

Detailed design for the chat-to-CoP real-time pipeline.

## Design Principles

1. **Latency over accuracy** — "70% is great." Even basic extraction adds value. Don't over-engineer accuracy at the cost of speed.
2. **Don't lose data** — If something can't be mapped to a strict schema field, put it in metadata. Always preserve the raw message.
3. **Single connection point** — Voice STT is already piped into IRC. We only need to connect to the IRC WebSocket.
4. **Structure where possible, metadata for the rest** — Map to known schema fields when confident, use the freeform metadata dictionary otherwise.
5. **We write, they read** — Only our pipeline writes to the CoP database. Vendors are read-only consumers.

## Pipeline Overview

```text
                    IRC WebSocket (ws://server:8097)
                    ┌──────────────────────────────┐
                    │  #c2_coord    #isr_reports    │
                    │  #fires       #jprc           │
                    │  #stt_C2Coord #stt_hydroBMA   │
                    │  #stt_crusherBMA  ...         │
                    └──────────────┬───────────────┘
                                  │
                    ┌─────────────▼─────────────┐
                    │     Message Ingestion      │
                    │  WebSocket client          │
                    │  Channel tagging           │
                    │  Timestamp normalization   │
                    └─────────────┬─────────────┘
                                  │
                    ┌─────────────▼─────────────┐
                    │    Tier 1: Fast Filter     │  ~0ms
                    │  - Drop acks ("c", ".")    │
                    │  - Regex track numbers     │
                    │  - Coordinate extraction   │
                    │  - Callsign dictionary     │
                    │  - Exercise control filter │
                    └────┬────────────┬─────────┘
                         │            │
                    (fast-path)  (needs interpretation)
                         │            │
                         │  ┌─────────▼─────────────┐
                         │  │  Tier 2: Local LLM     │  ~0.3-0.6s
                         │  │  Structured JSON output │
                         │  │  System prompt with:    │
                         │  │  - Exercise glossary    │
                         │  │  - Few-shot examples    │
                         │  │  - Output schema        │
                         │  └────┬──────────┬────────┘
                         │       │          │
                         │  (confident) (low confidence)
                         │       │          │
                         │       │  ┌───────▼───────────┐
                         │       │  │ Tier 3: Cloud API  │  ~0.5-1.5s
                         │       │  │ Complex reasoning  │
                         │       │  │ Disambiguation     │
                         │       │  └───────┬───────────┘
                         │       │          │
                    ┌────▼───────▼──────────▼────┐
                    │   Schema Mapper / Validator │
                    │  - Map to CoP fields       │
                    │  - Validate against known  │
                    │    entity lists             │
                    │  - Overflow → metadata dict │
                    └─────────────┬──────────────┘
                                  │
                    ┌─────────────▼─────────────┐
                    │    CoP REST API Writer     │
                    │  - Track Manager updates   │
                    │  - SmartPack Manager       │
                    │  - Audit log               │
                    └───────────────────────────┘
```

## Tier 1: Fast Filter (~0ms)

Rule-based filtering and extraction. No ML/LLM involved.

### What it does

1. **Drop noise** — Single-character acks ("c", "."), radio checks ("test"), exercise control (STARTEX/ENDEX)
2. **Extract track numbers** — Regex patterns from prior event tooling (see config/track_patterns.json)
3. **Extract coordinates** — Lat/lon regex, MGRS grid parser
4. **Callsign lookup** — Match against known callsign dictionary from scenario data
5. **Channel-based routing** — #fires messages get fire mission parser, #jprc gets CSAR parser, etc.

### What it produces

For high-confidence extractions (track ID with known callsign, coordinates in standard format), emit a structured update directly to the CoP without LLM processing.

For everything else, pass to Tier 2 with any extracted fields pre-populated.

## Tier 2: Local LLM (~0.3-0.6s)

The workhorse. Handles 80-90% of messages that get past Tier 1.

### Model Selection

**Primary:** Qwen 3 30B-A3B MoE at Q4_K_M quantization
- 196 tokens/sec on RTX 4090
- Only 3B parameters active per token (MoE) = fast
- 18GB VRAM = fits with headroom on 24GB card
- Qwen family excels at structured JSON output

**Fallback:** Qwen 2.5 14B or Phi-4 14B if GPU is smaller

### Inference Engine

**Ollama** for prototyping (easiest setup, JSON schema support since v0.5)
**llama.cpp server** for production (GBNF grammar = 100% valid JSON guaranteed)

### System Prompt Design

```text
You are a military chat message interpreter for a Common Operating Picture database.
Extract world-state changes from the message and return structured JSON.

GLOSSARY:
- "gadget bent" = radar failure
- "buzzer on" = EW jamming active
- "splash" = target destroyed
- "FOX 3" = active radar missile launched
- "RTB" = return to base
- "angels XX" = altitude in thousands of feet
- "bullseye/cigar XXX/YYY" = bearing/range from reference point
- "F+XX" = fuel above frag in thousands of lbs
- "c" or "copy" = acknowledgment (ignore)
[... exercise-specific glossary ...]

KNOWN CALLSIGNS: [from scenario data]
KNOWN TRACK NUMBERS: [from current CoP state]

OUTPUT SCHEMA:
{
  "update_type": "entity_id | status_change | weapons | location | threat | tasking | fuel | handover | csar | fire_mission | cyber | sitrep | environmental | none",
  "confidence": 0.0-1.0,
  "entities": [...],
  "raw_message": "original text"
}
```

### Few-Shot Examples

Include 3-5 representative messages in the prompt with expected output. These should cover the common update types. Source examples from the DASH 3 chat data.

## Tier 3: Cloud API Fallback (~0.5-1.5s)

For messages where the local model reports low confidence. This should be <10% of traffic.

**Options (see docs/LLM_BENCHMARKS.md for full comparison):**
- Gemini 2.5 Flash — best speed/cost ratio
- Claude 4.5 Haiku — most consistent latency
- Mercury 2 — fastest raw speed

**When to escalate:**
- Local model confidence < 0.5
- Message contains multiple entities or compound state changes
- SITREP/handover messages (information-dense, multiple updates per message)
- Ambiguous abbreviations the local model hasn't seen

## Schema Mapper

Takes structured output from any tier and maps to CoP database fields.

1. **Known fields** → direct mapping (trackNumber, position, status, etc.)
2. **Unknown fields** → metadata dictionary (JSON key-value pairs on the entity)
3. **Validation** → check track numbers against known entities, callsigns against scenario data
4. **Conflict resolution** → most recent update wins (as decided in planning meeting)

## CoP Writer

REST API client that pushes updates to Track Manager and SmartPack Manager.

- **Optimistic writes** — push immediately, don't wait for validation
- **Audit trail** — log every update with source message, confidence score, and tier that processed it
- **Idempotency** — handle duplicate messages gracefully (same track update from chat + STT)

## Deployment Options

### Option A: On-Site Desktop (Preferred)

Desktop workstation with RTX 4090/5090 at H2O Las Vegas.

- Pros: Lowest latency, no network dependency, works in air-gapped scenarios
- Cons: Need to ship/set up hardware, limited to one GPU

### Option B: Cloud API Only

Process everything through cloud APIs from the event facility.

- Pros: No hardware logistics, effectively unlimited compute
- Cons: Network dependency, latency variability, classification constraints (IL2 data)

### Option C: Hybrid

Local GPU for Tier 2 (primary processing), cloud for Tier 3 (fallback).

- Pros: Best of both worlds
- Cons: Most complex setup

**Recommendation:** Start with Option A (local desktop), have Option B ready as fallback. The message volume (10-30/min) is trivially handled by a single GPU.

## Strategic Context

This pipeline sits within the broader C2ES (Command & Control Effects at Scale) project, which is the ACT3/AFRL contribution to CJADC2. Key context from project planning:

- C2ES is grounded in the CJADC2 "sense, make sense, and act" loop. Our pipeline is the **"make sense"** layer for unstructured chat/voice data.
- The DASH events demonstrated that AI-enabled tools generate **30x more COAs** than human-only teams — but only when they have current, accurate data to work with. That's what we provide.
- As of Mar 2026, the real-time streaming contractor was cut. The **critical need is for a structured API endpoint to augment CoP data** — this validates our approach of LLM-structured JSON output pushed to the CoP REST API.
- The HLT (Human Language Translation) team is ramping up to cover some of the gap. Coordinate with them on overlapping scope.

## Open Questions

1. **CoP database schema** — Pending from contractor team. Need this to build the Schema Mapper.
2. **IRC server details for MASH** — Same server config as DASH 3, or new setup?
3. **Channel list for MASH** — Same channels, or different?
4. **Bullseye reference point** — Need the scenario's bullseye coordinates to convert cigar bearings to lat/lon.
5. **Fine-tuning data** — Should we annotate a subset of the DASH 3 chat for fine-tuning the local model?
6. **GPU availability** — Confirm desktop GPU specs at H2O.
7. **HLT team coordination** — What is the HLT team covering? Avoid duplication of effort.
8. **JADPACT integration** — C2ES uses JADPACT as architectural blueprint. Does our CoP output need to conform to JADPACT data formats?

# Architecture

## Core Concept: AI Staff Officer, Not ETL Pipeline

Traditional chat parsing treats each message independently: regex it, classify it, extract fields, write to database. That's an ETL pipeline. It works, but it's a dead end for the two research questions we care about (online user modeling, ontology-free team adaptation) and it can't maintain context across messages.

Instead, we deploy **stateful agents** — one per IRC channel — that maintain a running world model, learn operator patterns, and output structured CoP updates. Each agent is an AI staff officer watching a radio net.

## System Overview

```
                    IRC WebSocket (ws://server:8097)
                    +------------------------------+
                    |  #c2_coord    #isr_reports    |
                    |  #fires       #jprc           |
                    |  #stt_C2Coord #stt_hydroBMA   |
                    |  #stt_crusherBMA  ...         |
                    +--------------+---------------+
                                   |
                    +--------------v---------------+
                    |       Message Router          |
                    |  async Python, channel fan-out|
                    +--------------+---------------+
                                   |
              +--------------------+--------------------+
              |                    |                     |
    +---------v--------+ +--------v---------+ +---------v--------+
    | Channel Agent    | | Channel Agent    | | Channel Agent    |
    | #c2_coord        | | #fires           | | #stt_hydroBMA    |
    |                  | |                  | |                  |
    | - Conv. window   | | - Conv. window   | | - Conv. window   |
    | - Speaker models | | - Speaker models | | - Speaker models |
    | - World state    | | - World state    | | - World state    |
    | - LLM backend    | | - LLM backend    | | - LLM backend    |
    +--------+---------+ +--------+---------+ +--------+---------+
             |                    |                     |
             +--------------------+---------------------+
                                  |
                    +-------------v--------------+
                    |       Fusion Agent          |
                    |  Semantic deconfliction     |
                    |  Cross-channel correlation  |
                    |  Duplicate suppression      |
                    +-------------+--------------+
                                  |
                    +-------------v--------------+
                    |     World State Store       |
                    |  SQLite (portable)          |
                    |  -> CoP REST API            |
                    |  -> Audit log               |
                    +----------------------------+
```

## Design Principles

1. **Model-agnostic** — Every LLM call goes through the OpenAI-compatible chat completions API. `instructor` + Pydantic for structured output. Backend is a configuration choice, not an architecture decision.
2. **Equifinality** — Multiple paths to the same CoP update. No single point of failure in the extraction logic.
3. **Antifragile** — Component failures produce information that improves future routing decisions.
4. **Flow never stops** — Every message gets something written to the CoP. Quality degrades gracefully; output never halts.
5. **Latency over accuracy** — "70% is great." Even basic extraction adds value. Must beat white cell humans.
6. **Don't lose data** — If it can't be structured, forward the raw message with an "unprocessed" flag.
7. **Stateful extraction** — Context from previous messages informs current interpretation. "Copy" after a tasking message is an acknowledgment. "Copy" in isolation is noise.

## Components

### Message Router

Async Python WebSocket client that connects to the IRC server, joins all configured channels, and fans messages out to the appropriate channel agent.

- **Protocol:** WebSocket (not raw IRC) on port 8097
- **Channels:** Configured in `config/irc_channels.json`
- **Responsibilities:** Connection management, reconnection, channel subscription, message framing
- **Does NOT:** Filter, classify, or interpret messages — that's the agent's job

For development/testing, the router also supports replay mode: read DASH chat log files and feed them through the same agent interface with original timestamps.

### Multi-line Reassembly

Between the message router and channel agents, a reassembly layer (`ingestion/multiline.py`) merges structured multi-line messages. Military chat uses numbered "5 line" formats for CAS requests, cyber taskings, and ISR requests:

    LINE 0: BCOA 0502-01A
    LINE 1: OCO Prime, to deny cyber on mobile C2
    LINE 2: Cigar 252 / 331 NM
    LINE 3: System Denial, Power Denial, Geolocation
    LINE 4: for specified duration
    LINE 5: USSF will report if denial is successful

These arrive as 6 separate IRC messages but represent a single tasking. The `MultiLineBuffer` buffers consecutive LINE N: messages from the same sender/channel and emits a single merged `IRCMessage` when the block ends (different speaker, non-LINE message, or a new LINE 0). The channel agent sees one coherent message instead of six fragments.

### Channel Agent

The core component. One instance per IRC channel, each maintaining:

**Conversation Window** — Sliding buffer of recent messages (raw text + metadata). Included in the LLM prompt so the agent has conversational context. Size is configurable; default is the last 50 messages or 15 minutes, whichever is smaller.

**Speaker Models** — Learned per-speaker profiles built during the session:
- Role (battle manager, intel analyst, fires coordinator, tanker controller)
- Area of interest (which BMA, which lane, which threat axis)
- Jargon preferences (how this specific person abbreviates things)
- Reliability (do their reports tend to be confirmed or corrected?)

Speaker models are updated incrementally. The agent doesn't need to be told who "Hydro_SL" is — it learns from context that this speaker issues tasking, references the Hydro BMA, and uses specific abbreviation patterns.

**World State Snapshot** — The agent's current belief about the battlespace, derived from messages it has seen. This is included in the prompt so the agent can produce updates relative to current state (e.g., "4 more launched" when current inventory is known).

**Degrading LLM Backend** — Each agent has a backend stack that degrades gracefully:

| Level | Backend | Trigger | What's Lost |
|-------|---------|---------|-------------|
| Normal | Best available model (70B class) | Default | Nothing |
| Degraded | Smaller model (8B class) | Primary times out or errors 3x | Speaker model updates |
| Minimal | Regex/keyword patterns | All LLM backends down | Semantic interpretation |
| Passthrough | Raw forward | Everything down | All extraction |

Transitions are managed by a circuit breaker: 3 consecutive failures or p95 latency >5s triggers a downgrade. Recovery is automatic when the backend starts responding normally.

### Fusion Agent

Operates on channel agent outputs, not raw messages. Responsibilities:

1. **Semantic deconfliction** — Two agents reporting the same event from different channels (e.g., typed chat says "splash 2 flankers" and STT picks up "two more down"). The fusion agent recognizes these as the same event using semantic similarity + temporal proximity, not field matching.

2. **Cross-channel correlation** — A tasking in #c2_coord and a status update in #fires about the same track number. The fusion agent links these into a coherent narrative.

3. **Confidence aggregation** — When multiple channels report the same event, confidence increases. When they contradict, flag for human review.

4. **STT denoising** — STT channels are ~60% noise. The fusion agent can cross-reference STT reports against typed chat to validate or discard.

**Degradation:** If the fusion agent fails, channel agents write directly to the world state store. Updates may contain duplicates, but no data is lost.

### LLM Backend

The `backend` package abstracts all LLM interaction behind a single protocol:

```python
class LLMBackend(Protocol):
    async def extract(
        self,
        messages: list[ChatMessage],
        schema: type[BaseModel],
        context: AgentContext,
    ) -> BaseModel: ...
```

Implementations:

**OpenAICompatibleBackend** — Wraps any OpenAI-compatible endpoint (Ollama, vLLM, LiteLLM, cloud APIs). Uses `instructor` for structured output. This one class covers every model we'd want to use.

**RegexBackend** — Pattern matching against `config/track_patterns.json` and domain-specific extractors (fuel state, weapons count, operational status). No LLM involved. Fast, reliable, limited.

**DegradingBackend** — Wraps a list of backends, tries in order with configurable timeouts and circuit breakers. This is what channel agents actually use.

**PassthroughBackend** — Returns the raw message wrapped in the output schema with `confidence: 0.0` and `extraction_method: "passthrough"`. The last resort.

### World State Store

SQLite database (zero-config, portable, ships with Python) that holds:

- **CoP updates** — Every structured update extracted by any agent, with provenance (which agent, which backend, confidence score, source message)
- **Speaker models** — Serialized per-speaker profiles, queryable by channel agents
- **Audit log** — Every raw message received, whether processed or not

The store exposes a FastAPI REST API for:
- Querying current world state
- Streaming updates (SSE)
- Forwarding updates to the actual CoP database when available

For deployment, SQLite can be swapped for PostgreSQL without changing the application code (SQLAlchemy or similar).

### Supervisor

Lightweight Python process (not an LLM) that manages the agent pool:

- **Health monitoring** — Tracks each agent's latency, error rate, backend status
- **Agent lifecycle** — Start, stop, restart agents; reassign channels
- **Priority management** — When all agents are slow, shed load by prioritizing HIGH channels (typed chat) over MEDIUM (STT) over LOW (exercise control)
- **New channel detection** — If a new channel appears mid-exercise, spawn a new agent and seed it with context from the most similar existing agent
- **Metrics** — Expose Prometheus-compatible metrics for monitoring during the event

## Structured Output

All CoP updates use Pydantic models enforced by `instructor`. The core update model:

```python
class CoPUpdate(BaseModel):
    update_type: UpdateType           # entity_id, status_change, weapons, fuel, etc.
    confidence: float                 # 0.0-1.0
    extraction_method: str            # "llm", "regex", "passthrough"
    entities: list[EntityUpdate]      # What changed
    source_channel: str               # Which IRC channel
    source_speaker: str               # Who said it
    source_message: str               # Raw text
    timestamp: datetime               # When it was said
    context_messages: list[str]       # Surrounding conversation for audit
```

The `EntityUpdate` model maps to CoP database fields where possible, with overflow to a metadata dict. See [SCHEMAS.md](SCHEMAS.md) for the target schema.

## Deployment

### MASH Event (May 2026)

Desktop workstation with RTX 4090/5090 at H2O Las Vegas.

```
Docker Compose:
  - ollama (GPU, serves local models)
  - chat-to-cop (Python, the agent pipeline)
  - sqlite (embedded, no separate container)
  - fastapi (CoP REST API, same container as pipeline)
```

Single `docker compose up`. Total setup time: <10 minutes.

### Development

Same Docker Compose stack, but the message router runs in replay mode against DASH chat logs instead of connecting to a live IRC server.

### Future (Analytics Gateway)

The same container images deploy to AG (AWS GovCloud). Ollama is replaced by a vLLM deployment or Bedrock endpoint. The application code doesn't change — only the backend configuration.

## Message Flow Example

1. IRC message arrives: `Hydro_Tank: RR15 F+40, RL36 F+50`
2. Message router sends it to the `#c2_coord` channel agent
3. Agent includes message in conversation window, checks speaker model for "Hydro_Tank" (known: tanker controller for Hydro BMA)
4. Agent calls LLM backend with conversation context + world state + output schema
5. LLM returns structured output: two fuel updates (RR15 at +40k lbs, RL36 at +50k lbs), confidence 0.9
6. Agent emits two `CoPUpdate` objects to the world state store
7. Agent updates speaker model: "Hydro_Tank reports fuel states, tracks tanker assets RR15 and RL36"
8. Fusion agent sees the updates, checks for conflicts with other channels, passes them through
9. World state store writes to SQLite and pushes to CoP REST API
10. Total time: ~0.5-1s from IRC message to CoP update

## Open Questions

1. **CoP database schema** — Pending from contractor team. Need this to finalize the CoPUpdate -> database field mapping.
2. **IRC server details for MASH** — Same config as DASH 3, or new setup?
3. **Bullseye reference point** — Need the scenario's bullseye coordinates to convert cigar bearings.
4. **GPU specs at H2O** — Confirm desktop GPU model for local inference sizing.
5. **HLT team scope** — What is the Human Language Translation team covering? Coordinate to avoid duplication.

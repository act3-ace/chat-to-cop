# Chat-to-CoP Briefing Slides

Slide content and speaker notes for the chat-to-cop technical briefing. Each section is one slide. Adapt talking points based on audience (battle managers, engineers, researchers, leadership).

---

## Slide 1: Title

**Chat-to-CoP: AI Staff Officer for Real-Time World-State Extraction**

ACT3 / C2ES -- Air Force Research Laboratory

MASH Wargame Exercise, May 2026

Scott Clouse, Chief AI Officer

**Speaker notes:** This is a technical overview of the chat-to-cop system. Depending on the audience, emphasize different parts: for battle managers, focus on slides 2-4 and 9-10. For engineers, focus on 3-6. For researchers and leadership, focus on 7-8 and 11-12. The system is pre-deployment, IL2 unclassified, targeting the MASH event at H2O Las Vegas.

---

## Slide 2: The Problem

**Chat carries information that never makes it to the CoP.**

During DASH/MASH exercises:

- 10+ IRC channels running simultaneously
- 2-7 messages/minute, spiking to 12/min during engagements
- Battle managers communicate fuel states, kill results, threat assessments, CSAR events in chat
- Voice radio is captured by STT and piped into IRC as `#stt_*` channels
- **Current process:** Someone reads chat and types data into the CoP manually
- **Result:** Minutes per update. CoP falls behind. Decisions made on stale data.

| What's in chat but NOT in the CoP | Example |
|-----------------------------------|---------|
| Fuel states | `RR15 F+40, RL36 F+50` |
| Kill results | `splash 2 flankers at bullseye 270/40` |
| Platform failures | `ORCA01 gadget bent, RTB` |
| Weapons inventory | `24 TLAMS launched, 38 remaining` |
| Threat assessments | `probable J-15s IVO Cigar 272/330` |

**Speaker notes:** The key insight is that chat is where the real-time battlespace knowledge lives. The CoP database is always behind because the manual transcription bottleneck cannot keep up with message rates during high-tempo operations. Battle managers know this -- they check chat, not the CoP, during engagements. We are automating the transcription step. For battle managers: this means the CoP screen in your pit will have information that currently only lives in chat windows. For engineers: this is a real-time streaming extraction problem with 10+ concurrent channels.

---

## Slide 3: The Solution

**Architecture: AI staff officers watching every radio net**

```
IRC Server (WebSocket, port 8097)
        |
   Message Router
        |
   +----+----+----+----+----+
   |    |    |    |    |    |
 Agent Agent Agent Agent Agent  ...  (1 per channel)
   |    |    |    |    |    |
   +----+----+----+----+----+
        |
   Fusion Agent (deconfliction)
        |
   Write Authority (auto / flagged / human)
        |
   CoP Database
```

Each agent maintains:
- Conversation context (last 50 messages)
- Learned speaker profiles
- Current world-state belief
- Graceful fallback chain (LLM -> regex -> passthrough)

Single desktop workstation with GPU. Docker Compose. `docker compose up`.

**Speaker notes:** One agent per IRC channel, like having a dedicated staff officer listening to each radio net. Agents are stateful -- they remember what was said 10 minutes ago, they learn who each speaker is, they track what they believe about the battlespace. The fusion agent sits on top and catches duplicates when the same event is reported on multiple channels (typed chat and voice STT often echo each other). The whole thing runs on a single workstation with a GPU, deployed via Docker. For Jennifer/pipeline engineers: this is async Python, no framework, just instructor + Pydantic + pybreaker. For Juan/711 HPW: the LLM interface is OpenAI-compatible everywhere -- swap models with one config change.

---

## Slide 4: How It Works

**Real message, end to end**

**Input (DASH 3, #c2_coord):**
```
HYDRO_SL: SITREP / AIR: ZEUS 12,13,14 shot down by TTG;
YAMA11 flight shot down by TTG
```

**Output (6-7 seconds later):**

| Entity | Status | Confidence | Method |
|--------|--------|------------|--------|
| ZEUS12 | DESTROYED | 0.95 | LLM + fusion corroboration |
| ZEUS13 | DESTROYED | 0.95 | LLM + fusion corroboration |
| ZEUS14 | DESTROYED | 0.95 | LLM + fusion corroboration |
| YAMA11 | DESTROYED | 0.95 | LLM + fusion corroboration |

**What happened:**
1. IRC parse -> channel agent adds to conversation window
2. Speaker model lookup: HYDRO_SL = Section Lead, Hydro BMA, high reliability
3. LLM extracts 4 entity status changes with structured JSON
4. Fusion agent: matches against STT echo, suppresses duplicate, boosts confidence
5. Write authority: confidence 0.95, not high-risk type -> auto-write to CoP

**Speaker notes:** Walk through each step. The key points for different audiences: For battle managers -- this is a SITREP that would have taken someone 2-3 minutes to manually enter into the CoP. The system does it in 6 seconds. For engineers -- the LLM output is a Pydantic model enforced by instructor, so it always conforms to schema. The fusion agent caught the STT duplicate automatically. For Jared/researchers -- notice the speaker model already knows HYDRO_SL is a Section Lead with high reliability. That was learned from prior messages in the session, not pre-configured.

---

## Slide 5: Key Innovation -- LLM as Ontology

**No predefined schema for the world. The model understands jargon.**

Three channels, same event, different language:

| Source | Message |
|--------|---------|
| Typed chat (BMA) | `splash 2 flankers at bullseye 270/40` |
| Typed chat (Intel) | `confirmed 2x SU-27 destroyed, grid XK4423` |
| Voice STT | `uh... two more down, west of the river` |

Traditional approach: Define a mapping between these representations before the exercise. Update the mapping when terminology changes. Fail when someone uses unexpected language.

Our approach: The LLM maps all three to `{entity_type: HOSTILE_AIR, count: 2, status: DESTROYED}` because it understands language. No mapping table. No pre-coordination. No ontology.

**From "Against the Ontological Mandate" (C2ES):**
- Schemas are hypotheses, not truth (Pattern A)
- Data that doesn't fit the schema is retained, not dropped (Pattern B)
- Validate the output shape, not the internal representation (Pattern C)

**Speaker notes:** This is the intellectual foundation of the system. For Jared and the research community: this directly addresses RQ2 -- how a heterogeneous team adapts without defining an ontology beforehand. The LLM is the universal translator. For the 711 HPW team: this is where our approach differs fundamentally from traditional NER pipelines. We do not define entity types and patterns up front. We let the model interpret the language and validate the output shape. For battle managers: what this means practically is that the system handles your jargon without being programmed for it. When you abbreviate differently than Crusher pit, the system still understands.

---

## Slide 6: Graceful Degradation

**Flow never stops. Quality degrades; output never halts.**

```
Level 1: Primary LLM (7B+)         confidence ~0.9     NORMAL
    | circuit breaker (3 fails)
Level 2: Fallback LLM (3B)         confidence ~0.7     DEGRADED
    | circuit breaker (3 fails)
Level 3: Regex patterns             confidence ~0.5     MINIMAL
    | circuit breaker (3 fails)
Level 4: Passthrough (raw text)     confidence  0.0     SAFE STATE
    (always succeeds)
```

**Real DASH 3 results (935 messages):**
- LLM extractions: 100 (laptop CPU -- circuit breaker tripped constantly)
- Regex fallback: 876
- Passthrough: 0
- **Messages dropped: 0**

Recovery is automatic. Circuit breaker cooldown (30s) -> retry -> if the backend is back, promote.

Run-Time Assurance pattern: **Simplex Architecture** with guaranteed safe state.

**Speaker notes:** This is the safety story. For battle managers: the system never goes dark. Even if the GPU dies, you still get data -- it just comes from pattern matching instead of AI interpretation. For engineers: this uses pybreaker for circuit breakers, tenacity for retries. The passthrough backend wraps the raw message in the output schema with confidence 0.0 -- the data is preserved for human review. For Jared: this maps directly to the Simplex Architecture from the Lyons et al. RTA framework. The passthrough is the guaranteed safe state. We measured Time-to-Adapt (KPP-A) at <60s on GPU, 44 minutes on CPU -- which is why GPU is required for deployment.

---

## Slide 7: Results -- Quality

**Extraction quality (synthetic data, entity-level scoring)**

| Model | F1 (type) | F1 (entity) | Schema Errors |
|-------|-----------|-------------|---------------|
| **Qwen3-32B** (cloud) | **0.96** | **0.95** | **0%** |
| Qwen2.5:7b (local T4) | 0.92 | 0.91 | 1% |
| Llama-3.3-70B | 0.88 | 0.85 | 12% |
| Llama-3.1-8B | 0.72 | 0.68 | 22% |

**Per-type recall (Qwen2.5:7b on T4):**

| Type | Recall | | Type | Recall |
|------|--------|-|------|--------|
| entity_id | 100% | | threat | 92% |
| fuel | 100% | | status_change | 71% |
| tasking | 100% | | csar | 0% |
| weapons | 100% | | | |

**Key findings:**
- Qwen >> Llama for structured JSON (0% vs 12-22% schema errors)
- Prompt engineering > model size (3B with good examples > 70B with bare prompt)
- CSAR needs few-shot prompt work; status_change needs brevity code coverage

**Speaker notes:** For engineers: the schema error rate is the dominant criterion. If the model can't produce valid JSON, instructor has to retry, which costs latency. Qwen produces clean JSON almost every time. For Jared: these are synthetic eval numbers. We do not have human-labeled ground truth for real DASH 3 data yet -- that's a Sprint 3 item. The per-type recall numbers tell us where to focus prompt improvement: CSAR is a gap, status_change needs brevity code examples. For 711 HPW: the eval harness is in scripts/eval_models.py and works with any OpenAI-compatible endpoint -- you can run it against your own models.

---

## Slide 8: Results -- Performance

**GPU is required for real-time operation.**

| Hardware | Model | Tokens/s | Per-extraction (p50) | Real-time? |
|----------|-------|----------|---------------------|------------|
| **T4 16GB** (AG) | qwen2.5:7b | 41 | **6.5s** | Yes |
| CPU (AG m7i) | qwen2.5:7b | 8.3 | 4-6 min | No |
| CPU (laptop) | qwen2.5:3b | ~3 | 33s | No |

**Full pipeline (935 messages, 10 channels):**
- 477 updates extracted by agents
- 376 updates after fusion (56 deduplicates, 1 contradiction, 4 corroborations)
- Zero messages dropped
- 10+ simultaneous channels with single Ollama instance

**Target for MASH:**
- Desktop workstation with RTX 4090/5090 (24GB VRAM)
- Qwen3-32B (F1=0.96) or Qwen3-30B-A3B MoE (3B active parameters)
- Single `docker compose up`, <10 minute setup

**Speaker notes:** The performance story is straightforward: GPU is mandatory. 6.5 seconds per extraction on T4 means we can keep up with typical message rates (2-7/min). During spikes (12/min), the supervisor sheds load by prioritizing typed chat over STT. For Jennifer/pipeline engineers: this is a single Ollama instance serving all 10 agents sequentially. With vLLM and batching, throughput would be significantly higher. For leadership: the hardware ask is one desktop workstation with a 24GB GPU. That's it. No cloud, no cluster, no special infrastructure.

---

## Slide 9: Operational Context

**Where it sits in the MASH pit**

```
+------------------+     +-------------------+
|  Battle Mgmt Pit |     | Chat-to-CoP       |
|  (Vegas, Hydro,  |     | Workstation       |
|   Crusher, etc.) |     |                   |
|                  |     | GPU + Ollama      |
|  Operators type  |     | Docker Compose    |
|  and speak       |     |                   |
|       |          |     |  Monitors:        |
|       v          |     |  - 10+ IRC chans  |
|  IRC Server -----+---->|  - #stt_* voice   |
|  (port 8097)     |     |  - processes in   |
|                  |     |    real time       |
|  CoP Database <--+-----| <-- writes here   |
|       |          |     |                   |
|       v          |     +-------------------+
|  Viz screens     |
+------------------+
```

**What operators see:** The CoP visualization screens update faster. Information that previously only lived in chat windows now appears in the shared picture within seconds.

**What operators do NOT see:** The pipeline itself. No new UI, no new workflow. The system is invisible to the end user -- it just makes the CoP better.

**Kill switch:** REST endpoint to pause all writes instantly. Individual agents can be stopped/restarted without affecting others.

**Speaker notes:** For battle managers: nothing changes about how you work. You keep typing in chat, you keep talking on radio. The system watches passively and pushes data to the CoP that your viz tools already display. The only visible difference is that the CoP has more data, faster. For engineers: the kill switch is CoPWriter.pause() -- it immediately halts all writes and queues incoming updates. resume() + flush_pause_queue() drains the queue. For leadership: this is a zero-integration-cost addition to the exercise. One workstation, one Docker command, plugs into the existing IRC server.

---

## Slide 10: Safety and Trust

**Wrong CoP data is worse than no CoP data.**

**Tiered Write Authority (bounded authority / SDAC):**

| Tier | Condition | Action |
|------|-----------|--------|
| **AUTO** | confidence >= 0.7, low-risk type | Write immediately |
| **FLAGGED** | confidence 0.4-0.7 | Write with review flag |
| **HUMAN** | confidence < 0.4 OR high-risk type | Queue for operator review |

**High-risk types always require human review:** weapons, CSAR, fire_mission, cyber_ew -- even at high confidence, because the consequence of error outweighs the latency cost.

**Every update carries:**
- Confidence score (0.0-1.0)
- Extraction method (LLM / regex / passthrough)
- Source channel, speaker, raw message, timestamp
- Reasoning explanation from the LLM

**Adversarial protection:**
- Prompt injection detection (regex patterns)
- Speaker reliability scoring (built during session)
- Cross-channel corroboration requirement for single-source reports
- STT confidence penalty (-0.15 default)

**Speaker notes:** For battle managers: if the system is not sure, it flags the update for your review rather than writing wrong data to the CoP. High-consequence items like weapons employment always require a human to approve, no matter how confident the system is. For Jared: this implements Software-Defined Authority Controls (SDAC) from the RTA framework -- bounded authority where the system's ability to affect the real world is tiered by confidence and risk. The adversarial detection is regex-based and only catches known pattern families -- sophisticated attacks would require LLM-based detection, which is a future item. For the RAI discussion: every update has a full provenance chain. You can trace any CoP entry back to the raw chat message, the model that produced it, and the confidence score.

---

## Slide 11: Research Value

**This system IS a miniature FACS research platform.**

| FACS Property | Implementation |
|---------------|----------------|
| **Heterogeneous** | Different agents, different models, different degradation states |
| **Loosely coordinated** | Supervisor + fusion, not central control |
| **Configurable** | Add/remove channels, swap models at runtime |
| **Trustworthy** | Confidence + provenance on every output |

**Two research questions for MASH:**

**RQ1: Online user modeling** -- Speaker models learn operator profiles during the session. How fast? How accurate? Does learning Hydro_Tank help with Crusher_Tank?

**RQ2: Ontology-free adaptation** -- The LLM maps heterogeneous terminology to shared world state. What happens when a new team joins mid-exercise with unfamiliar jargon?

**Key Performance Parameters (anti-ontology framework):**

| KPP | Measured | Target |
|-----|----------|--------|
| Time-to-Adapt (TTA) | <60s (GPU) | <30s |
| Unknown Retention Rate (URR) | 100% | 100% |
| Adversarial Robustness (ASR) | Regex detection | + LLM detection |
| Mean Time to Recover (MTTR) | ~35s (GPU) | <30s |

**Speaker notes:** For Jared: this is the publication story. Chat-to-cop is not a side project -- it is a FACS research platform at the individual-operator scale. The same properties we want to study in heterogeneous autonomous combat systems (flexibility, adaptability, configurability, trustworthiness) are testable here with faster iteration cycles. The two RQs map to FACS-core problems: trust calibration (RQ1) and interoperability without shared standards (RQ2). The KPPs come from Pattern F of the anti-ontology framework -- we measure time-to-adapt, not compliance. For 711 HPW: these RQs are where our approaches could be compared. Your NER pipeline and our agent architecture represent different paths to the same goal -- equifinality at the program level.

---

## Slide 12: Status and Path to MASH

**Sprint status (as of March 2026):**

| Sprint | Scope | Status | Tests |
|--------|-------|--------|-------|
| Sprint 1 | Vertical slice (single channel, single model) | COMPLETE | -- |
| Sprint 2 | Multi-channel + resilience (degradation, fusion, supervisor) | COMPLETE | -- |
| Sprint 3 | Deploy + harden (Docker, eval, CoP writer, RAI) | IN PROGRESS | -- |
| **Total** | | | **549 tests passing** |

**What's done:**
- Full extraction pipeline: channel agents, fusion, supervisor, store
- Degrading backend with circuit breakers (tenacity + pybreaker)
- Regex fallback with 50+ military patterns
- Speaker model learning
- Full DASH 3 replay (935 messages, 10 channels)
- Eval harness + benchmark results across 5 models
- Tiered write authority + kill switch
- Docker containerization
- System card, dataset cards, labeling guide

**What remains:**
- Full GPU replay validation (#16, in progress)
- CoP database writer (#17, blocked on contractor schema)
- RAI provenance / MLflow integration (#18)
- CSAR and status_change prompt improvement
- Ground truth labeling for real data
- MASH-specific configuration (bullseye reference, channel list)

**Risks:**
1. **GPU hardware at H2O** -- need confirmation of desktop GPU availability (RTX 4090/5090)
2. **CoP database schema** -- blocked on contractor delivery; current output goes to local SQLite
3. **No ground truth labels** -- real-data accuracy is unknown without labeled test set

**Speaker notes:** For leadership: Sprints 1 and 2 are complete. Sprint 3 is in progress with the major engineering work done. The remaining items are deployment configuration and blocked dependencies (CoP schema from contractors, GPU hardware confirmation at H2O). The 549 test count means CI is enforced on every push -- nothing merges that breaks tests. For engineers: the open issues are on GitLab, parallelizable, and have acceptance criteria. For battle managers: we are targeting MASH in May 2026 with a working system. The question is not whether it works -- the DASH 3 replay proves the pipeline operates end-to-end. The question is how good the extraction quality will be with the target model and hardware.

---

## Slide 13: Demo / Q&A

**Live demo options:**

1. **Smoke test** -- 5 real DASH messages through extraction (30 seconds)
2. **DASH 3 replay** -- real-time playback of 23 Sep exercise data
3. **Eval harness** -- run extraction quality benchmarks against Groq cloud
4. **API exploration** -- Swagger UI at `localhost:8000/docs`

**Links:**

| Resource | URL |
|----------|-----|
| GitLab (DLE) | `gitlab.dle.afrl.af.mil/c2es1/mash/chat-to-cop` |
| Documentation | `docs/` directory (ARCHITECTURE, BENCHMARK_RESULTS, WALKTHROUGH) |
| Quickstart | `docs/QUICKSTART.md` (15-minute setup) |

**Questions to discuss:**
- What CoP fields matter most to your team?
- What channels should we prioritize for MASH?
- What brevity codes does your pit use that we should add to the prompt?
- How would you want to see flagged/low-confidence updates?

**Speaker notes:** Tailor the demo to the audience. For battle managers: run the smoke test, show real DASH messages going in and structured output coming out. For engineers: show the eval harness and API. For researchers: show the full replay with fusion statistics. The questions at the bottom are genuine -- we need operational input from the BM community to configure for MASH. End by collecting feedback: what would make this useful for your team? What would make you not trust it?

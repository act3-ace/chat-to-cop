# Chat-to-CoP Briefing Slides

Slide content and speaker notes for the chat-to-cop technical briefing. Each section is one slide. Adapt talking points based on audience (battle managers, engineers, researchers, leadership).

For additional context, meeting notes, and DASH event data, see the [C2ES Google Drive](https://drive.google.com/drive/u/0/folders/1po6MGtfA5GF8QRub3X5LVpA_spJ9E7zz).

---

## Slide 1: Title

**Chat-to-CoP: AI Staff Officer for MASH 2026**

*A starting point to spark discussion -- not a finished product*

ACT3 / C2ES -- Air Force Research Laboratory

MASH Wargame Exercise, May 2026

Scott Clouse, Chief AI Officer

**Speaker notes:** This is a starting point designed to spark discussion with operators and developers. We need their input on what matters most. Depending on the audience, emphasize different parts: for battle managers, focus on slides 2-5 and 9-10. For engineers, focus on 3-7. For researchers and leadership, focus on 8, 11-12. The system is pre-deployment, IL2 unclassified, targeting the MASH event at H2O Las Vegas.

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

## Slide 3: The Pit Structure

**Each pit = Pit Boss + 2 Battle Managers, working as a coordinated unit**

| Pit | Example Roles |
| --- | ------------- |
| Vegas | Vegas_PB (pit boss), Vegas_Strike, Vegas_Tank |
| Hydro | HYDRO_SL (pit boss), HYDRO_Strike, HYDRO_Tank |
| Crusher | Crusher_PB, Crusher_Strike, Crusher_Surv |
| Taipan | Taipan_PB, Taipan_Strike, Taipan_Tank |
| Mesquite | Mesquite_PB, Mesquite_Strike, Mesquite_Tank |

Plus: Intel/Fires coordinators, White Cell (WF_* -- scenario injection, most information-dense)

Operators communicate via IRC (typed) and voice radio (STT into `#stt_*` channels).

**Speaker notes:** Understanding the pit structure is essential. The pit boss synthesizes information for higher echelons. The two BMs handle specific functions -- strike planning, tanker coordination, surveillance. The agent must learn the rhythm of each pit: who reports to whom, which BM tracks which assets, how the pit boss rolls up information. White cell messages are the most information-dense because they inject scenario events. Voice radio is transcribed by STT and piped into IRC as separate channels -- these are noisy but carry real-time information.

---

## Slide 4: The Solution -- Background Agent

**An AI agent that works in the BACKGROUND on behalf of operators.**

- Operators never interact with the agent directly -- **not a chatbot**
- Agent sits on the IRC server, reads every message silently
- Highlights relevant information, pushes structured data to the CoP
- Keeps the human in the loop via confidence scores and flagged updates
- Learns by observing: models each operator's rhythm and jargon
- Works for both expert and novice operators

```text
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
   CoP Database  -->  Vendor Viz Tools
```

Each agent maintains: conversation context (last 50 messages), learned speaker profiles, current world-state belief, graceful fallback chain (LLM -> regex -> passthrough).

Single box, single GPU. Docker Compose. `docker compose up`.

**Speaker notes:** The key design decision from the March 2026 all-day meeting: agents work in the background on behalf of human operators. They are like a digital assistant that learns what the operator does and helps them do it faster. Nothing changes about operator workflow -- they keep typing in chat, keep talking on radio. The system watches passively and pushes structured data to the CoP. The whole thing runs on a single workstation with a GPU. Internet is expected at H2O for commercial model access as well. For Jennifer/pipeline engineers: this is async Python, no framework, just instructor + Pydantic + pybreaker. For Juan/711 HPW: the LLM interface is OpenAI-compatible everywhere -- swap models with one config change.

---

## Slide 5: How It Works

**Real message, end to end**

**Input (DASH 3, #c2_coord):**

```text
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
2. Speaker model lookup: HYDRO_SL = Pit Boss, Hydro BMA, high reliability
3. LLM extracts 4 entity status changes with structured JSON
4. Fusion agent: matches against STT echo, suppresses duplicate, boosts confidence
5. Write authority: confidence 0.95, not high-risk type -> auto-write to CoP

**Speaker notes:** Walk through each step. The key points for different audiences: For battle managers -- this is a SITREP that would have taken someone 2-3 minutes to manually enter into the CoP. The system does it in 6 seconds. For engineers -- the LLM output is a Pydantic model enforced by instructor, so it always conforms to schema. The fusion agent caught the STT duplicate automatically. For Jared/researchers -- notice the speaker model already knows HYDRO_SL is the Hydro Pit Boss with high reliability. That was learned from prior messages in the session, not pre-configured.

---

## Slide 6: Key Innovation -- LLM as Ontology

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

## Slide 7: Graceful Degradation

**Flow never stops. Quality degrades; output never halts.**

```text
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

## Slide 8: Results -- Quality

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

## Slide 9: Results -- Performance

**7 backends validated, zero code changes between them.**

| Backend | Model | Mean Latency | Duration (935 msgs) | Real-time? |
|---------|-------|-------------|---------------------|------------|
| **DSRC V100** | qwen2.5:14b | **2.2s** | **34 min** | Yes |
| **Cloud API** | Gemini 2.5 Flash | 3.5s | 55 min | Yes |
| **Cloud API** | GPT-4.1 nano | 4.4s | 68 min | Yes |
| **Bedrock** | Claude Sonnet 4.5 | 4.8s | 75 min | Yes |
| **AG GPU** | qwen2.5:7b (T4) | 7.6s | 150 min | Yes |
| **DSRC V100** | qwen2.5:32b | ~7s | ~110 min (est) | Yes |
| CPU | qwen2.5:7b | 4-6 min | Not viable | No |

**Semantic extraction quality (935 messages, 11 channels):**

| Metric | Gemini Flash | GPT-4.1 nano | Claude Haiku | Bedrock Sonnet | V100 14B | T4 7B |
|--------|-------------|--------------|-------------|----------------|----------|-------|
| Updates | 324 | 318 | 273 | 256 | 362 | 255 |
| Entities | **254** | 229 | 193 | 206 | 58 | 196 |
| Threats | **66** | 45 | 52 | 54 | 18 | 32 |
| Taskings | **86** | 49 | 76 | 69 | 6 | 51 |
| Confidence | **0.80** | 0.78 | 0.71 | — | 0.25 | — |
| Cost/run | $0.15 | $0.11 | $0.93 | $3.50 | $0 (HPC) | $0 (AG) |

**Key insight:** Cloud APIs extract richer semantics (3-20x more threats and taskings). Larger local models (32B) close the quality gap but are slower.

**Narwhal V100 Model Comparison (2026-04-04) -- 5 models vs Opus silver labels:**

| Model | Updates | Avg Conf | Type Match vs Opus | Tasking | Threat | Location |
|-------|---------|----------|--------------------|---------|--------|----------|
| V100 7B | 259 | 0.82 | 40% | 59 | 28 | 40 |
| V100 14B | 255 | 0.82 | **49%** | 70 | 53 | 51 |
| V100 32B (Qwen2.5) | 255 | 0.79 | 47% | 50 | 51 | 35 |
| V100 Qwen3-32B | 251 | 0.79 | 45% | 58 | 50 | 53 |
| **Opus (ground truth)** | **290** | -- | 100% | 125 | 63 | 8 |

14B has best type match (49%). All models produce consistent update counts (251-259). Local models under-extract tasking and over-classify as location -- categorization differences, not missed information. Qwen3-32B on V100 validates MASH target hardware.

**Target for MASH:**

- Option A: Desktop GPU (RTX 4090/5090) with Qwen3-32B -- best local quality
- Option B: Cloud API (Gemini Flash at $0.15/run) -- best semantics, needs internet
- Option C: DSRC HPC (V100) -- offline-capable, CI-built containers, validated with 4 model sizes
- Option D: Hybrid -- cloud primary, local fallback. Config change, not code change.

**Speaker notes:** We now have 7 validated backends across local GPU, DSRC HPC, and cloud APIs — all on the same 935-message DASH 3 dataset. The model-agnostic architecture is proven: zero code changes between any of them. Cloud APIs find dramatically more threats and taskings than local models, but the DSRC V100 with 14B is the fastest at 34 minutes. The 32B on V100 matches cloud-level confidence (0.79) but is slower. For MASH: we have options at every price point — from free (HPC hours) to $0.15 (Gemini Flash) to $3.50 (Bedrock Sonnet). The pipeline adapts to whatever hardware and connectivity is available at H2O.

---

## Slide 9b: Can It Keep Up? Message Rate vs Extraction Latency

**DASH 3 real message rates (935 messages, 4 hours, 11 channels):**

| Rate Metric | Value | What it means |
|-------------|-------|---------------|
| Overall average | 3.9 msgs/min | Across all channels combined |
| Per-minute p50 | 9 msgs/min | Typical active period |
| Per-minute p90 | 20 msgs/min | Heavy engagement |
| Per-minute p95 | 25 msgs/min | Peak combat |
| Peak 1-minute burst | **42 msgs/min** | Maximum observed |
| Busiest single channel | 2.0 msgs/min | STT Taipan BMA (sustained) |
| Peak 5-min window | 26.8 msgs/min | Sustained peak across all channels |

**The critical insight: each channel agent processes independently.**

The busiest single channel sees 1 message every **30 seconds**. Any backend under 30s/message keeps up per-channel with zero queuing. All our backends are well under this:

| Backend | Latency/msg | Msgs/min/agent | Can keep up? | CoP staleness |
|---------|-------------|----------------|-------------|---------------|
| V100 14B | 2.2s | 27 | **YES** (14x headroom) | ~2s |
| Gemini Flash | 3.5s | 17 | **YES** (9x headroom) | ~4s |
| Bedrock Sonnet | 4.8s | 12 | **YES** (6x headroom) | ~5s |
| T4 7B | 7.6s | 8 | **YES** (4x headroom) | ~8s |
| V100 32B | 12.0s | 5 | **YES** (2.5x headroom) | ~12s |
| CPU 7B | 4-6 min | 0.2 | **NO** | minutes |

**System-wide at peak (42 msgs/min across 11 channels):**

With 11 agents processing in parallel, even the slowest GPU backend (V100 32B at 12s/msg) delivers 55 msgs/min system capacity — still ahead of the 42 msgs/min peak burst.

**What "staleness" means for the battle manager:**

A fuel state change at 14:20:00 with 5s extraction reaches the CoP at 14:20:05. A human checking the CoP at 14:20:10 sees data that is **10 seconds old**. With 7.6s extraction, it's **17.6 seconds old**. Both are operationally acceptable — today, humans take **minutes** to manually type the same data into the CoP. Even our slowest GPU backend is 10-50x faster than the current manual process.

**The speed vs quality tradeoff:**

Faster backends (V100 14B, 2.2s) are real-time but extract fewer threats/taskings. Slower backends (cloud APIs, 3.5-8.3s) extract richer semantics with higher confidence. The optimal choice depends on what matters more: update freshness or update quality. For MASH, the recommendation is cloud primary (best semantics) with local fallback (guaranteed availability).

**Speaker notes:** This is the answer to "is it fast enough?" — yes, by a wide margin. The busiest single channel in DASH 3 sees 1 message every 30 seconds. Our fastest backend processes in 2.2 seconds. Even our slowest (12s) has 2.5x headroom. The real question isn't speed — it's quality. A 2-second extraction that misses the threat is worse than a 5-second extraction that catches it. The CoP staleness numbers show that even at 8 seconds, the data arrives 10-50x faster than manual entry. CPU-only is the only configuration that can't keep up — and we already knew that from the AG benchmark.

---

## Slide 10: Operational Context

**Where it sits in the MASH pit**

```text
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

## Slide 11: Safety and Trust

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

## Slide 12: Research Value

**This system IS a miniature FACS research platform.**

| FACS Property | Implementation |
|---------------|----------------|
| **Heterogeneous** | Different agents, different models, different degradation states |
| **Loosely coordinated** | Supervisor + fusion, not central control |
| **Configurable** | Add/remove channels, swap models at runtime |
| **Trustworthy** | Confidence + provenance on every output |

**Two research questions for MASH:**

**RQ1: Online user modeling** -- Speaker models learn operator profiles during the session. How fast? How accurate? Does learning Hydro_Tank help with Crusher_Tank?

*Initial result (2026-04-06):* On Qwen2.5-7B, speaker models show no significant benefit (-0.6% type match, -2.8% entity overlap vs without). **Hypothesis: model capacity bottleneck** -- 7B lacks sufficient capacity to leverage speaker context. Testing on 14B/32B/cloud APIs to confirm (#52). If larger models benefit, finding is: "speaker personalization requires ≥14B parameters."

**RQ2: Ontology-free adaptation** -- The LLM maps heterogeneous terminology to shared world state. What happens when a new team joins mid-exercise with unfamiliar jargon?

**Confidence calibration (2026-04-04):** Qwen2.5-7B is bimodally overconfident -- says 0.95, right 65%. ECE=0.67. The model has no nuanced uncertainty; it's either confident or not. Write authority thresholds need recalibration.

**Key Performance Parameters (anti-ontology framework):**

| KPP | Measured | Target |
|-----|----------|--------|
| Time-to-Adapt (TTA) | <60s (GPU) | <30s |
| Unknown Retention Rate (URR) | 100% | 100% |
| Adversarial Robustness (ASR) | Regex detection | + LLM detection |
| Mean Time to Recover (MTTR) | ~35s (GPU) | <30s |

**Speaker notes:** For Jared: this is the publication story. Chat-to-cop is not a side project -- it is a FACS research platform at the individual-operator scale. The same properties we want to study in heterogeneous autonomous combat systems (flexibility, adaptability, configurability, trustworthiness) are testable here with faster iteration cycles. The two RQs map to FACS-core problems: trust calibration (RQ1) and interoperability without shared standards (RQ2). The KPPs come from Pattern F of the anti-ontology framework -- we measure time-to-adapt, not compliance. RQ1 initial results are in: 7B shows no speaker model benefit, but this may be a capacity issue. Multi-model testing (#52) will disambiguate. The calibration finding (ECE=0.67, bimodal overconfidence) is directly actionable for write authority threshold design. For 711 HPW: these RQs are where our approaches could be compared. Your NER pipeline and our agent architecture represent different paths to the same goal -- equifinality at the program level.

---

## Slide 12b: Ground Truth -- Opus Silver Labels

**935 messages labeled by Claude Opus 4.6 via Ask Sage (NIPRNet, free, IL5)**

875 LLM extractions, 60 noise-filtered, 0 errors.

**Message composition:**
- **31% informative** (290/935), **69% noise/none** (645/935)
- Confidence is **bimodal**: noise at 0.0, extractions at 0.85-0.95

**What the data tells us (tailor to audience):**

| Audience | Key Insight |
|----------|-------------|
| **Battle Manager** | 31% of your chat is operationally informative. #isr_reports is your densest channel (71%). |
| **AI Expert** | Bimodal confidence = clean separation for write authority thresholds. Auto-write at 0.7 validated. |
| **FACS Researcher** | Tasking dominates at 43% of informative messages -- BattleCOA coordination is the primary information flow. |

**Type distribution (informative only):**

| Type | Count | % |
|------|-------|---|
| tasking | 125 | 43% |
| threat | 63 | 22% |
| status_change | 34 | 12% |
| cyber_ew | 17 | 6% |
| fuel | 13 | 4% |
| entity_id | 12 | 4% |
| sitrep | 9 | 3% |
| location | 8 | 3% |
| fire_mission | 3 | 1% |
| weapons | 3 | 1% |
| handover/csar/environmental | 1 each | <1% |

**Per-channel informative rates:** #isr_reports 71%, #fires 50%, #stt_taipanBMA 41%, #c2_coord 38%, #vegas_internal 34%, #stt_mesquiteBMA 32%, #stt_C2Coord 23%, #stt_hydroBMA 22%, #stt_crusherBMA 19%, #jprc 11%.

**Speaker notes:** This is the first complete ground-truth labeling of all 935 DASH 3 messages. Claude Opus 4.6 via Ask Sage on NIPRNet -- free, IL5, no data leaving the enclave. The bimodal confidence distribution means our write authority thresholds work: noise is cleanly separated from signal. Tasking dominance (43%) tells us the primary information flow in DASH chat is BattleCOA coordination, not entity tracking. For battle managers: #isr_reports is 71% informative -- nearly every message matters. For AI experts: the 0.85-0.95 confidence cluster means silver labels are high-quality anchors for confidence calibration (#30). For researchers: the label analysis script (`scripts/analyze_labels.py`) runs standalone on any labeled DB.

---

## Slide 13: Status and Path to MASH

**Sprint status (as of April 2026):**

| Sprint | Scope | Status | Tests |
|--------|-------|--------|-------|
| Sprint 1 | Vertical slice (single channel, single model) | COMPLETE | -- |
| Sprint 2 | Multi-channel + resilience (degradation, fusion, supervisor) | COMPLETE | -- |
| Sprint 3 | Deploy + harden (Docker, eval, CoP writer, RAI) | IN PROGRESS | -- |
| **Total** | | | **843 tests passing** |

**What's done:**
- Full extraction pipeline: channel agents, fusion, supervisor, store
- Degrading backend with circuit breakers (tenacity + pybreaker)
- Regex fallback with 50+ military patterns
- Speaker model learning
- Full DASH 3 replay (935 messages, 11 channels, **100% LLM success** on Qwen, **99.6%** on Claude Sonnet 4.5)
- **AWS Bedrock validated** -- Claude Sonnet 4.5, 4.8s mean latency, no GPU needed, zero code changes
- Fusion feedback loop (cross-channel speaker corroboration)
- Eval harness + benchmark results across 5+ models (local + cloud)
- Tiered write authority + kill switch
- 35 MRs merged, 843 tests
- Docker containerization
- System card, dataset cards, labeling guide
- **Opus silver labels complete** -- 935 messages labeled via Claude Opus 4.6 / Ask Sage (NIPRNet, IL5)
- **Label analysis and speaker model eval frameworks** -- `scripts/analyze_labels.py`, `scripts/eval_speaker_models.py`
- **Narwhal V100 model comparison complete** -- 5 replay DBs (7B/14B/32B Qwen2.5/Qwen3-32B) compared vs Opus labels; 14B best type match (49%), pipeline stable across all model sizes (251-259 updates)
- **Confidence calibration complete** -- 79.7% accuracy, ECE=0.67, bimodal overconfidence identified
- **RQ1 speaker model A/B complete (7B)** -- no significant benefit on 7B; capacity bottleneck hypothesis, multi-backend testing planned (#52)

**What remains:**

- Commercial model integration (Mia's priority upon return, with Colin/Jennifer assisting)
- CoP database writer (#17, blocked on contractor schema)
- RAI provenance / MLflow integration (#18)
- CSAR and status_change prompt improvement
- Multi-backend speaker model A/B testing (#52) -- confirm capacity bottleneck hypothesis
- vLLM on AG as Ollama alternative (#51)
- MASH-specific configuration (bullseye reference, channel list)

**Risks:**

1. **CoP database schema** -- blocked on contractor delivery; current output goes to local SQLite
2. ~~No ground truth labels~~ -- **RESOLVED**: Opus silver labels complete for all 935 messages
3. ~~Confidence calibration~~ -- **DONE**: 79.7% accuracy, ECE=0.67, bimodal overconfidence
4. ~~Speaker model evaluation~~ -- **DONE (7B)**: no benefit on 7B, testing larger models (#52)
5. **Data separation** -- data from different DASH events must remain separate to avoid misrepresenting results

**Speaker notes:** For leadership: Sprints 1 and 2 are complete. Sprint 3 is mostly done -- the major engineering work is finished and the research findings are landing. Seven backends are validated on real DASH 3 data: Qwen 7B on T4 GPU (100% success, 7.6s), Qwen 14B/32B and Qwen3-32B on Narwhal V100, Claude Sonnet 4.5 on AWS Bedrock (99.6% success, 4.8s, no GPU), and three cloud APIs (Gemini Flash, GPT-4.1 nano, Claude Haiku). The model-agnostic architecture works -- zero code changes between any of them. Internet at H2O is expected, making Bedrock or Gemini Flash viable primary or hybrid options. The ground truth labeling risk is now resolved -- Opus silver labels cover all 935 messages with bimodal confidence that validates our write authority design. Two key research findings landed in early April: (1) confidence calibration on 7B shows ECE=0.67 with bimodal overconfidence -- the model says 0.95 and is right only 65% of the time, so write authority thresholds need recalibration before deployment; (2) RQ1 speaker model A/B on 7B shows no significant benefit (-0.6% type match, -2.8% entity overlap), with the hypothesis being a model capacity bottleneck -- multi-backend testing on 14B/32B/cloud APIs is the next step (#52) to confirm. The Narwhal V100 model comparison (5 models, 935 msgs each) shows the pipeline is stable across all model sizes (251-259 updates) and that 14B hits the sweet spot for prompt compliance (49% type match vs Opus). Qwen3-32B on V100 validates the MASH target hardware path. The 843 test count and 35 merged MRs mean CI is enforced on every push -- nothing merges that breaks tests. The remaining work is the multi-backend RQ1 extension (#52), per-model calibration, vLLM as an Ollama alternative (#51), and blocked dependencies (CoP schema from contractors). For engineers: the open issues are on GitLab, parallelizable, and have acceptance criteria. For battle managers: we are targeting MASH in May 2026 with a working system. The DASH 3 replay proves the pipeline operates end-to-end on both local and cloud backends.

---

## Slide 14: May Strategy -- Virtuous Cycle Infrastructure

**MASH is not a product demo. It is infrastructure for a virtuous cycle.**

```text
Deploy rudimentary agent
        |
        v
Process live MASH data -----> Operators see output
        |                            |
        v                            v
Capture interaction data <---- Operator feedback
        |
        v
Labeled training data for next version
```

- Deploy a **handwired, rudimentary agent** that works well enough to be useful
- Establish infrastructure to capture feedback from operators and developers
- Operator interaction generates labeled training data
- Present a **starting point** to spark discussion -- not a finished product
- LLMs are inevitable as a utility; the question is how to use them well

**Speaker notes:** This framing comes directly from the March 2026 C2ES All-Day Meeting. The goal in May is not to impress with accuracy -- it is to establish the infrastructure so that every exercise makes the next one better. The agent processes live data, operators interact with the output, and that interaction generates the labeled training data needed to improve the model. A customized or few-shot model is necessary due to military jargon density. The feedback we need from operators: what information matters most, how should confidence be displayed, where does the agent help versus get in the way.

---

## Slide 15: What Vendors Need -- Structured API Output

**The critical need (from C2ES All-Day Meeting, March 2026):**

> "A highly structured API endpoint to augment Common Operating Picture data, rather than continuing to process unstructured data."

**What chat-to-cop provides:**

- Schema-conformant JSON via REST API
- Every record: confidence score, extraction method, source channel, speaker, timestamp
- Full provenance chain: raw message -> extraction -> fusion -> CoP write
- Tiered write authority: auto / flagged / human review
- Pydantic-validated output -- schema errors caught before they reach the CoP

**Context:** The virtual real-time streaming vendor was cut. The HLT team and our pipeline must fill that gap. TMDA (Transformational Model for Decision Advantage) is the broader framework. Budget: ~$2M/year baseline covering key personnel.

**Speaker notes:** This is what makes the system useful to the broader CoP ecosystem. Vendor visualization tools can poll our structured API endpoint. The tiered write authority prevents low-confidence updates from automatically modifying the CoP. The fact that the streaming vendor was cut increases the urgency -- someone needs to produce structured data from unstructured comms, and that is exactly what this pipeline does. The CoP database writer (#17) is blocked on the contractor delivering the schema, but the tiered write authority design is complete and tested.

---

## Slide 16: Demo / Q&A

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

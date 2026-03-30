# RTA and FACS Framing for Chat-to-CoP

This document maps the chat-to-cop pipeline components to the Run Time Assurance (RTA) framework described in Lyons, Hobbs, Rogers, & Clouse (2023) and connects them to FACS (Family of Autonomous Combat Systems) principles. The goal is to show that this system is not just an extraction pipeline -- it is a miniature FACS research platform with formal safety assurance properties.

## Run Time Assurance (RTA) Mechanisms

RTA provides a framework for bounding autonomous system behavior at runtime without constraining internal representations. Three RTA patterns appear in the chat-to-cop architecture.

### Degrading Backend = Simplex-Architecture RTA

**Component:** `src/chat_to_cop/backend/degrading.py` (DegradingBackend)

The degrading backend implements a **Simplex Architecture** -- the foundational RTA pattern where a complex (high-performance, potentially unreliable) controller is monitored and backed by progressively simpler (lower-performance, higher-reliability) controllers.

```
+-----------------+     +-----------------+     +------------------+     +------------------+
| Complex Ctrl    |     | Reduced Ctrl    |     | Safety Net       |     | Guaranteed Safe  |
| Large LLM (70B) | --> | Small LLM (8B)  | --> | Regex Patterns   | --> | Passthrough      |
| conf ~0.9       |     | conf ~0.7       |     | conf ~0.5        |     | conf = 0.0       |
+-----------------+     +-----------------+     +------------------+     +------------------+
        |                       |                       |                       |
   [circuit breaker]       [circuit breaker]       [circuit breaker]       [always available]
   fail_max=3              fail_max=3              fail_max=3
   cooldown=30s            cooldown=30s            cooldown=30s
```

**How it maps to Lyons et al.:**

| Simplex RTA Concept | Chat-to-CoP Implementation |
|---------------------|---------------------------|
| Complex controller | Primary LLM (70B class) -- full semantic extraction, speaker-aware, context-dependent |
| Reduced controller | Fallback LLM (8B class) -- truncated context, no speaker model updates |
| Safety controller | Regex extraction -- pattern matching against known military formats |
| Guaranteed safe state | Passthrough -- raw message forwarded with `confidence: 0.0`, no data lost |
| Decision module | `pybreaker` circuit breakers -- 3 consecutive failures or timeout triggers transition |
| Recovery mechanism | Automatic cooldown (30s default) -- breaker transitions open -> half-open -> closed |

**What triggers degradation:**
- Backend raises an exception (model error, network failure, malformed output)
- Backend exceeds its per-slot timeout (configurable, default 10s primary / 5s fallback)
- Circuit breaker opens after `fail_max` consecutive failures

**What never happens:**
- Data is never dropped. The passthrough backend is the guaranteed safe state -- it always succeeds, returning the raw message with zero confidence. This is Pattern B (Out-of-Ontology Lane) from the anti-ontology framework.

**Measured performance (DASH 3 replay, 935 messages):**
- LLM extractions (laptop CPU, 3B model): 100/935 (circuit breaker tripped frequently)
- Regex fallback extractions: 876/935
- Passthrough: 0 (regex caught everything the LLM missed)
- Circuit breaker mean recovery: 44 minutes (CPU-bound -- GPU reduces this to seconds)
- Zero messages dropped

### Fusion Agent = Output Monitoring RTA

**Component:** `src/chat_to_cop/agent/fusion_agent.py` (FusionAgent)

The fusion agent implements an **Output Monitoring RTA** -- it observes the outputs of channel agents and modifies propagation when safety or trust properties are violated. It does not control channel agents; it monitors their outputs and gates what reaches the CoP database.

```
Channel Agent #c2_coord ----+
Channel Agent #fires -------+----> [Fusion Agent / Output Monitor] ----> World State Store
Channel Agent #stt_hydro ---+            |
                                   [Safety checks]
                                   - Adversarial input detection
                                   - Contradiction flagging
                                   - STT denoising
                                   - Speaker trust scoring
                                   - Temporal deconfliction
```

**How it maps to Lyons et al.:**

| Output Monitoring RTA Concept | Chat-to-CoP Implementation |
|-------------------------------|---------------------------|
| Plant output observation | Fusion agent receives CoPUpdates from all channel agents |
| Safety property check | Adversarial input detection (prompt injection patterns) |
| Trust property check | Speaker reliability scoring, STT confidence penalty |
| Output modification | Confidence adjustment: boost corroborated, penalize contradictions |
| Flagging for human review | Contradictory reports flagged with combined provenance |
| Pass-through on monitor failure | If fusion itself raises, raw updates pass through unchanged |

**Safety checks performed:**

1. **Adversarial input detection** -- Regex patterns scan for prompt injection attempts ("ignore previous instructions", role-tag injection, override patterns). Detected inputs are confidence-penalized by 0.3 and flagged, never dropped.

2. **Cross-channel contradiction detection** -- When two channels report conflicting states for the same entity (e.g., one says OPERATIONAL, another says DESTROYED), the fusion agent keeps the highest-confidence version and flags the contradiction for human review.

3. **STT denoising** -- Updates from `#stt_*` channels receive a confidence penalty (default -0.15) because STT channels are approximately 60% noise. Cross-referencing STT against typed chat validates or discards.

4. **Speaker trust scoring** -- Per-speaker reliability is tracked over the session. Speakers whose reports are frequently contradicted receive a confidence penalty on future updates. Speakers whose reports are corroborated receive a boost.

5. **Temporal deconfliction** -- Reports about the same entity within a configurable time window (default 30s) are grouped and merged. Same-channel duplicates are suppressed. Cross-channel corroborations boost confidence.

**Measured performance (DASH 3 replay):**
- Updates in: 477 (from channel agents)
- Updates out: 376 (after fusion)
- Deduplicates: 56
- Contradictions flagged: 1
- Corroborations: 4

### Tiered Write Authority = Bounded Authority (SDAC)

**Component:** `src/chat_to_cop/output/cop_writer.py` (CoPWriter) + `src/chat_to_cop/output/cop_schema.py` (WriteAuthority, classify_write_authority)

The CoP writer implements **Software-Defined Authority Controls (SDAC)** -- a bounded authority pattern where the system's ability to affect the real world (the CoP database) is tiered by confidence and risk.

```
                     CoPUpdate from Fusion Agent
                              |
                    [classify_write_authority]
                     /         |         \
                    /          |          \
              AUTO           FLAGGED        HUMAN
          conf >= 0.7     conf 0.4-0.7    conf < 0.4
          low-risk type                   OR high-risk type
              |               |               |
         Write immediately  Write with     Queue for
         to CoP database    review flag    operator review
                              |               |
                         [operator can       [operator must
                          override]           approve/reject]
```

**How it maps to Lyons et al.:**

| Bounded Authority Concept | Chat-to-CoP Implementation |
|--------------------------|---------------------------|
| Authority boundary | Confidence thresholds: 0.7 (auto), 0.4 (flagged), <0.4 (human) |
| Risk classification | High-risk types always require human review: weapons, CSAR, fire_mission, cyber_ew |
| Kill switch | `CoPWriter.pause()` -- immediately halts ALL writes, queues incoming updates |
| Recovery | `CoPWriter.resume()` + `flush_pause_queue()` -- drains queued updates |
| Audit trail | Every CoPRecord includes extraction_confidence, extraction_method, source_message, source_speaker |
| Bounded queue | Max 1000 queued updates (configurable) -- prevents unbounded memory growth |

**Why high-risk types always require human review:**
A weapons engagement or CSAR report that is wrong does not just degrade the CoP -- it could misdirect real resources. Even if the extraction confidence is 0.95, a weapons report is queued for human review because the consequence of error outweighs the latency cost.

## FACS Connection

Chat-to-cop is not analogous to a FACS -- it IS a miniature FACS. The agent pool exhibits the four FACS properties:

| FACS Property | Chat-to-CoP Implementation | Scale Difference |
|---------------|---------------------------|-----------------|
| **Flexible** | Add new channels mid-exercise, reconfigure extraction prompts at runtime | New sensor vs. new channel |
| **Adaptable** | Circuit breaker degradation, speaker model learning, confidence re-weighting | Platform failure vs. model failure |
| **Configurable** | Swap models, adjust timeouts, add/remove agents without restart (Supervisor) | Force recomposition vs. agent pool recomposition |
| **Trustworthy** | Every output has confidence, source, method, provenance chain | Sensor fusion trust vs. extraction trust |

### Equifinality (Pattern E: Parallel Analytical Paths)

Every CoP update has multiple viable extraction paths:

```
Path 1: Large LLM (70B) with full context + speaker model   -> CoPUpdate (conf ~0.9)
Path 2: Small LLM (8B) with truncated context                -> CoPUpdate (conf ~0.7)
Path 3: Regex patterns (track numbers, fuel states, weapons)  -> CoPUpdate (conf ~0.5)
Path 4: Cross-channel fusion (another agent saw the same event) -> CoPUpdate (boosted)
Path 5: Passthrough (raw message, human reads it)             -> CoPUpdate (conf  0.0)
```

This is not redundancy (N copies of the same thing). It is equifinality: N different approaches to the same goal, each with different failure modes. Kill any single path and the system still produces useful output. The degrading backend makes this operational by trying paths in priority order and falling through on failure.

**What equifinality buys us:** Path entropy -- the number of viable extraction paths for a message type -- is a measurable proxy for system robustness. Single-path message types (e.g., CSAR, which only the LLM can extract) are fragile. Multi-path types (e.g., track numbers, which regex and LLM both handle) are robust.

### Antifragility

The system gets better under stress, not just resilient:

- **Backend failure** produces routing information. The circuit breaker records which backends fail for which conditions. Over time, the system learns failure patterns. Recovery time (KPP-D) measures how fast it adapts.

- **Noisy channels** produce weighting information. The fusion agent tracks per-channel noise rates. STT channels (~60% noise) are automatically down-weighted. A channel that starts clean and degrades mid-exercise is detected and adjusted.

- **Contradictory reports** are the most valuable data. When two agents disagree about the same entity, the fusion agent flags it. These disagreements reveal where understanding is weakest and where human attention should focus.

- **Speaker corrections** produce reliability information. When an operator corrects a previous report ("Disregard last, TN 44504 is NOT DDG1"), the speaker model and trust score are updated. Future reports from ambiguous tracks get higher scrutiny.

### Two Research Questions Map to FACS-Core Problems

**RQ1: Online User Modeling (Speaker Models)**

How can a software agent personalize and learn a model of the user during operations?

This is the FACS trust-calibration problem at the individual operator level. Each channel agent maintains per-speaker models that grow during the session -- learning role, jargon, area of interest, and reliability from context, not pre-configuration. The fusion agent's speaker trust scoring is the trust-calibration mechanism.

FACS connection: In a full FACS, platforms must calibrate trust in other platforms' sensor outputs. In chat-to-cop, agents calibrate trust in individual speakers' reports. Same problem, different scale.

**RQ2: Ontology-Free Team Adaptation (LLM as Universal Translator)**

How can a heterogeneous team adapt to new challenges without defining an ontology or communications protocol beforehand?

This is the FACS interoperability problem without the traditional solution (shared data standards). Different teams use different terminology for the same concepts:

```
WF_BMA says:      "splash 2 flankers at bullseye 270/40"
WF_ISR says:      "confirmed 2x SU-27 destroyed, grid XK4423"
STT picks up:     "uh... two more down, west of the river"
```

The LLM maps all three to the same CoPUpdate because it understands language. The fusion agent performs semantic deconfliction -- recognizing that these reports describe the same event -- without field matching. No ontology, no comms protocol, no pre-coordination.

FACS connection: In a full FACS, heterogeneous platforms must share situational awareness without a universal data standard. In chat-to-cop, heterogeneous channels must be fused without a universal schema. The LLM is the universal translator.

## Anti-Ontology KPPs Mapped to Metrics

These Key Performance Parameters come from Pattern F (OODA-centric metrics) of the anti-ontology framework. Each is instrumented in the codebase.

### KPP-A: Time-to-Adapt (TTA)

**Definition:** How quickly does the system recover and adapt after a component failure?

**Measurement:** Tracked in `DegradingBackend` -- when a circuit breaker opens, the timestamp is recorded (`slot.last_opened`). When the backend recovers (next successful call after cooldown), the elapsed time is emitted as `time_to_adapt_seconds`.

**Instrument:** `metrics.observe("time_to_adapt_seconds", tta, labels={"backend": slot.name})`

**Benchmark results:**
- Laptop CPU (3B model): mean TTA = 44 minutes (CPU-bound, circuit breaker cooldown dominated by slow inference)
- AG GPU T4 (7B model): TTA < 60 seconds (pending full measurement)
- Target for MASH: TTA < 30 seconds

**Why it matters:** TTA measures adaptability, not uptime. A system that recovers in 30 seconds after a model crash is more operationally useful than a system that never crashes but takes 10 minutes to restart.

### KPP-B: Unknown Retention Rate (URR)

**Definition:** What fraction of inputs that cannot be classified by the primary extraction path are retained rather than dropped?

**Measurement:** The passthrough backend guarantees URR = 100%. Every message that exhausts all backends is wrapped as a CoPUpdate with `confidence: 0.0`, `extraction_method: "passthrough"`, and the full raw message text. The fusion agent also retains flagged adversarial inputs rather than dropping them.

**Instrument:** `metrics.inc("passthrough_total")` counts passthrough events. URR = 1.0 - (dropped / total) = 1.0 by design.

**Benchmark results:**
- DASH 3 replay: 0 messages dropped out of 935
- URR = 100% (structural guarantee, not empirical)

**Why it matters:** This is Pattern B (Out-of-Ontology Lane). In a traditional NER pipeline, messages that don't match any pattern are silently dropped. In chat-to-cop, they are first-class signals of novelty -- the system retains what it doesn't understand, which is precisely the data that reveals ontological gaps.

### KPP-C: Adversarial Semantic Robustness (ASR)

**Definition:** How does the system respond to inputs designed to manipulate its outputs (prompt injection, semantic adversarial attack)?

**Measurement:** The fusion agent scans every source message against known prompt injection patterns. Detected adversarial inputs are confidence-penalized (default -0.3) and annotated with "FLAGGED: Possible adversarial input detected" but are never dropped.

**Instrument:** `metrics.inc("fusion_adversarial_detected_total")` counts detected adversarial inputs.

**Current detection patterns:**
- "ignore (all) previous instructions"
- "you are now a/an ..."
- "system:" role injection
- `<|system|>`, `<|assistant|>`, `<|user|>` tag injection
- "forget everything/all/your ..."
- "override your/the instructions/rules/prompt"

**Limitations:** Current detection is regex-based and only catches known pattern families. Sophisticated adversarial inputs (semantic manipulation without syntactic markers) require LLM-based detection, which is a Sprint 4 research item.

**Why it matters:** In a military C2 environment, chat channels could be compromised. A system that blindly trusts all inputs is a liability. ASR measures the system's ability to detect and contain malicious inputs without halting operations.

### KPP-D: Mapping Volatility / Mean Time to Recover (MTTR)

**Definition:** How quickly does the system recover to full extraction capability after a backend failure?

**Measurement:** Related to but distinct from KPP-A. MTTR is the circuit breaker cooldown period -- the time a failed backend remains disabled before being retried. Configured as `cooldown` parameter (default 30s) in the DegradingBackend.

**Instrument:** `metrics.inc("degradation_events_total", labels={"backend": slot.name})` counts circuit breaker open events. MTTR = cooldown period + first-successful-retry latency.

**Benchmark results:**
- Circuit breaker cooldown: 30s (configurable)
- Backend recovery after cooldown (GPU): typically < 5s (one inference cycle)
- Backend recovery after cooldown (CPU): 30-60s (slow inference)
- Total MTTR (GPU): ~35s; Total MTTR (CPU): ~60-90s

**Why it matters:** MTTR bounds the worst-case quality degradation window. During MTTR, the system operates on lower-tier backends (smaller model or regex). The shorter the MTTR, the less time is spent in degraded mode.

## Architecture Diagram with RTA Annotations

```
                        IRC WebSocket (ws://server:8097)
                        +-------------------------------+
                        |  #c2_coord    #isr_reports     |
                        |  #fires       #jprc            |
                        |  #stt_C2Coord #stt_hydroBMA    |
                        +---------------+---------------+
                                        |
                        +---------------v---------------+
                        |        Message Router          |
                        |   (fan-out to channel agents)  |
                        +---------------+---------------+
                                        |
               +------------------------+------------------------+
               |                        |                        |
   +-----------v-----------+ +---------v-----------+ +-----------v-----------+
   | Channel Agent         | | Channel Agent       | | Channel Agent         |
   | #c2_coord             | | #fires              | | #stt_hydroBMA         |
   |                       | |                     | |                       |
   | +-------------------+ | | +-----------------+ | | +-------------------+ |
   | | SIMPLEX RTA       | | | | SIMPLEX RTA     | | | | SIMPLEX RTA       | |
   | | (DegradingBackend)| | | | (DegradingBknd) | | | | (DegradingBackend)| |
   | |                   | | | |                 | | | |                   | |
   | | 70B LLM           | | | | 70B LLM         | | | | 70B LLM           | |
   | |   | [breaker]     | | | |   | [breaker]   | | | |   | [breaker]     | |
   | | 8B LLM            | | | | 8B LLM          | | | | 8B LLM            | |
   | |   | [breaker]     | | | |   | [breaker]   | | | |   | [breaker]     | |
   | | Regex             | | | | Regex            | | | | Regex             | |
   | |   | [breaker]     | | | |   | [breaker]   | | | |   | [breaker]     | |
   | | Passthrough       | | | | Passthrough      | | | | Passthrough       | |
   | |   (safe state)    | | | |   (safe state)  | | | |   (safe state)    | |
   | +-------------------+ | | +-----------------+ | | +-------------------+ |
   |                       | |                     | |                       |
   | + Speaker models      | | + Speaker models    | | + Speaker models      |
   | + Conv. window        | | + Conv. window      | | + Conv. window        |
   | + World state         | | + World state       | | + World state         |
   +-----------+-----------+ +---------+-----------+ +-----------+-----------+
               |                       |                         |
               +-----------------------+-------------------------+
                                       |
                       +---------------v-----------------+
                       |     OUTPUT MONITORING RTA        |
                       |       (Fusion Agent)             |
                       |                                  |
                       | 1. Adversarial input detection   |
                       | 2. Temporal deconfliction        |
                       | 3. Cross-channel correlation     |
                       | 4. Contradiction flagging        |
                       | 5. Speaker trust scoring         |
                       | 6. STT denoising                 |
                       | 7. Confidence aggregation        |
                       +---------------+-----------------+
                                       |
                       +---------------v-----------------+
                       |    BOUNDED AUTHORITY (SDAC)      |
                       |       (CoPWriter)                |
                       |                                  |
                       |  AUTO (>=0.7, low-risk)          |
                       |    -> write immediately          |
                       |  FLAGGED (0.4-0.7)               |
                       |    -> write with review flag     |
                       |  HUMAN (<0.4 or high-risk)       |
                       |    -> queue for operator         |
                       |                                  |
                       |  [KILL SWITCH: pause()/resume()] |
                       +---------------+-----------------+
                                       |
                       +---------------v-----------------+
                       |       World State Store          |
                       |     (SQLite + CoP REST API)      |
                       |                                  |
                       |  + Full audit trail              |
                       |  + Provenance chain              |
                       |  + Extraction method tracking    |
                       +---------------------------------+
```

## Summary: RTA Coverage Matrix

| Pipeline Stage | RTA Type | Failure Mode | Safe State | KPP |
|---------------|----------|-------------|------------|-----|
| LLM extraction | Simplex Architecture | Model crash, timeout, malformed output | Fall to next backend, ultimately passthrough | KPP-A (TTA), KPP-D (MTTR) |
| Cross-channel fusion | Output Monitoring | Contradiction, adversarial input, noise | Flag for human review, confidence penalty | KPP-C (ASR) |
| CoP database write | Bounded Authority (SDAC) | Low confidence, high-risk type | Queue for human review, kill switch | KPP-B (URR) |
| End-to-end | All three in series | Total system failure | Passthrough + kill switch = safe stop | All KPPs |

Every message that enters the system exits the system. The RTA mechanisms control *how* it exits (structured update, flagged update, raw passthrough, or human queue) but never *whether* it exits. This is the fundamental safety guarantee.

## References

- Lyons, J. B., Hobbs, B., Rogers, W. A., & Clouse, S. (2023). *Run Time Assurance for Autonomous Systems.* AFRL technical framework.
- Anti-ontology patterns A, B, C, E, F from "Against the Ontological Mandate" (C2ES Addendum, ACT3 internal).
- FACS properties (Flexible, Adaptable, Configurable, Trustworthy) from FACS-NCA program documentation.

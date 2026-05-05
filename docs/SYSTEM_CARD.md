# System Card: Chat-to-CoP AI Staff Officer

**Version:** 0.1.0 (pre-deployment)
**Date:** 2026-03-29
**Organization:** ACT3 / C2ES, Air Force Research Laboratory
**Classification:** IL2 (Unclassified)
**Contact:** Hamilton Clouse, ACT3 Chief AI Officer

---

## System Description

Chat-to-CoP is an AI staff officer system that performs real-time world-state extraction from military IRC chat and voice speech-to-text (STT) channels during wargame exercises. It deploys a team of stateful agents -- one per IRC channel -- that maintain conversational context, learn operator communication patterns, and push structured updates to a Common Operating Picture (CoP) database.

The system is designed as a miniature FACS (Family of Autonomous Combat Systems) research platform: a heterogeneous team of AI agents that maintains shared situational awareness without a predefined ontology, adapts to component failures, and learns operator models online.

### What It Does

- Monitors 10+ simultaneous IRC channels (typed chat and voice STT)
- Extracts structured world-state updates: entity identification, status changes, weapons employment, fuel states, location reports, threat assessments, tasking, handovers, CSAR events, fire missions, cyber/EW actions, environmental conditions, and situation reports
- Performs cross-channel semantic deconfliction (recognizing when multiple channels describe the same event)
- Learns per-speaker communication profiles during the session without pre-configuration
- Writes provenance-tracked updates to a CoP database via REST API

### What It Does NOT Do

- It does not make operational decisions. It provides information to human decision-makers.
- It does not generate orders, taskings, or recommendations.
- It does not control any weapons, platforms, or autonomous systems.
- It does not filter, suppress, or editorialize information. If it cannot structure a message, it forwards the raw text.

## Intended Use

**Primary:** Real-time situational awareness support during the MASH (Multi-domain Analytic Synthetic Hybrid) wargame event, May 2026, at H2O Las Vegas.

**Scope:** IL2 (unclassified) wargame exercise data only. All chat and voice data is synthetic or exercise-generated.

**Users:** White cell battle managers, exercise controllers, and analysts who monitor the CoP during the exercise. The system augments human staff -- it does not replace them.

**Operating Environment:** Desktop workstation with GPU (RTX 4090/5090) for local inference via Ollama, or CPU-only instance with AWS Bedrock for cloud inference. Docker Compose deployment. Single-machine, connected to IRC server network. Internet connectivity enables Bedrock as a validated alternative to local GPU inference.

## Out-of-Scope Use

The following uses are explicitly **not supported** and were not considered during design:

- **Classified data processing.** The system has no security controls for data above IL2.
- **Autonomous weapons decisions.** The system produces information, not directives.
- **Personnel actions.** The system does not evaluate, rate, or make decisions about people.
- **Real-world operational deployment.** The system is designed for wargame exercises with synthetic scenarios.
- **Adversarial environments.** The system has no hardening against deliberate manipulation of input chat channels (see Known Limitations).
- **Medical, legal, or safety-critical decisions.** CoP updates are informational and may be incorrect.

## Models Used

The system is model-agnostic by design. All LLM calls go through an OpenAI-compatible chat completions API with `instructor` for structured output. No vendor-specific SDKs are used.

### Current Models

| Model | Role | Parameters | Quantization | Transport | Source |
|-------|------|-----------|--------------|-----------|--------|
| [Qwen 2.5 7B](https://huggingface.co/Qwen/Qwen2.5-7B-Instruct) | Primary local inference | 7B | Q4_K_M (Ollama) | Local (Ollama) | Alibaba Cloud / Qwen Team |
| [Qwen 2.5 3B](https://huggingface.co/Qwen/Qwen2.5-3B-Instruct) | Fallback (degraded mode) | 3B | Q4_K_M (Ollama) | Local (Ollama) | Alibaba Cloud / Qwen Team |
| [Claude Sonnet 4.5](https://docs.anthropic.com/en/docs/about-claude/models) | Cloud inference (validated) | N/A (proprietary) | N/A | AWS Bedrock (`us-gov`) | Anthropic |

### Target MASH Model

| Model | Role | Parameters | Quantization | Source |
|-------|------|-----------|--------------|--------|
| [Qwen 3 30B-A3B MoE](https://huggingface.co/Qwen/Qwen3-30B-A3B) | Primary (if 24GB VRAM available) | 30B (3B active) | TBD | Alibaba Cloud / Qwen Team |

### Why Qwen

Qwen models produce 0% schema errors on structured JSON extraction tasks compared to 12-22% for Llama-family models at equivalent sizes. For a system that requires every LLM output to conform to a Pydantic schema, this is the dominant selection criterion. See [BENCHMARK_RESULTS.md](research/BENCHMARK_RESULTS.md) for detailed comparisons.

### Model Licensing

Qwen 2.5 models are released under the Apache 2.0 license. Qwen 3 models are released under the Apache 2.0 license. Both permit commercial and government use without restriction.

### Modelfile Variant Naming Convention

When deploying with Ollama, we create custom model variants using Ollama Modelfiles to override default parameters -- most commonly the context window size. These variants follow a `-<param>` suffix convention:

- **`qwen2.5:7b-8k`** is created from `qwen2.5:7b` with `PARAMETER num_ctx 8192` (8K context window instead of the default 4096).
- **`qwen2.5:14b-8k`** follows the same pattern for the 14B model.

**Important for provenance:** The `-8k` suffix indicates a deployment configuration override, NOT a different model. The underlying model weights are identical to the base model (`qwen2.5:7b`). The Modelfile only changes Ollama runtime parameters (context window, temperature, etc.). The provenance `model_name` field will report the Ollama model name including the suffix (e.g., `qwen2.5:7b-8k`) because that is the name Ollama returns in API responses. When interpreting provenance records, treat `qwen2.5:7b-8k` and `qwen2.5:7b` as the same model weights with different context configurations.

See [internal/ANALYTICS_GATEWAY_DEPLOYMENT.md](internal/ANALYTICS_GATEWAY_DEPLOYMENT.md) for the Modelfile creation commands.

## Architecture

```
IRC WebSocket (live) or DASH log replay (development)
        |
   Message Router (async Python, channel fan-out)
        |
   Channel Agents (1 per IRC channel, stateful)
   - Conversation window (sliding buffer, last 50 msgs or 15 min)
   - Speaker models (learned per-user profiles, CTA/SDAC framework)
   - World state snapshot (agent's current belief about the battlespace)
   - Degrading LLM backend (primary -> fallback -> regex -> passthrough)
        |
   Fusion Agent (semantic deconfliction, cross-channel correlation)
        |
   World State Store (SQLite) -> CoP REST API
```

### Component Roles

| Component | Function | Failure Mode |
|-----------|----------|-------------|
| **Message Router** | WebSocket connection, channel subscription, message framing | Reconnects automatically |
| **Channel Agent** | Stateful extraction with context, speaker models, world state | Restartable by supervisor |
| **Degrading Backend** | Ordered fallback: LLM -> smaller LLM -> regex -> passthrough | Always produces output |
| **Fusion Agent** | Cross-channel deconfliction, duplicate suppression, confidence aggregation | Agents write directly to store (duplicates possible, no data loss) |
| **Supervisor** | Health monitoring, agent lifecycle, load shedding, metrics | Agents run independently without it |
| **World State Store** | SQLite persistence, audit log, REST API | Writes queue; no data loss |

## Run-Time Assurance (RTA) Mechanisms

### 1. Simplex Switching (Degrading Backend)

The degrading backend implements simplex architecture: a monitored primary system with automatic fallback to simpler, more predictable systems.

| Level | Backend | Trigger | What Is Lost |
|-------|---------|---------|-------------|
| Normal | Primary LLM (7B+) | Default | Nothing |
| Degraded | Fallback LLM (3B) | Primary times out or errors 3x | Speaker model updates, some semantic depth |
| Minimal | Regex pattern matching | All LLM backends fail | Semantic interpretation (only known patterns extracted) |
| Passthrough | Raw message forwarding | Everything fails | All extraction (raw text preserved with confidence 0.0) |

The circuit breaker (`pybreaker`) opens after 3 consecutive failures and automatically retries after a configurable cooldown (default 30s). Recovery time is tracked as the Time-to-Adapt (TTA) KPP.

### 2. Output Monitoring (Fusion Agent)

The fusion agent operates on channel agent outputs, not raw messages. It performs:
- **Contradiction detection:** Flags conflicting reports from different channels for human review instead of silently choosing one.
- **Confidence aggregation:** Multiple corroborating reports increase confidence; single-source reports are labeled as such.
- **Duplicate suppression:** Prevents the same event from appearing multiple times in the CoP from different channels.

### 3. Bounded Authority (Tiered Write Authority)

Write authority to the CoP database is gated by extraction method and confidence (design pending implementation in issue #17):

| Tier | Write Authority | Condition |
|------|----------------|-----------|
| Auto-write | Entity updates written directly | confidence >= 0.7 AND extraction_method = "llm" AND corroborated by fusion |
| Review queue | Flagged for human review | confidence < 0.7 OR single-source OR contradiction detected |
| Passthrough | Raw text visible, not written as structured data | extraction_method = "passthrough" |

### 4. Kill Switch

The supervisor exposes a REST endpoint to immediately halt all agent processing. Agents can also be individually stopped, restarted, or have their channels reassigned without system restart.

### 5. Schema Validation

Every LLM output is validated against Pydantic models via `instructor`. Outputs that fail schema validation are retried (up to 2 times) and then fall through to the next backend in the degradation chain. Invalid data never reaches the CoP database.

## Evaluation Results

Detailed results are in [BENCHMARK_RESULTS.md](research/BENCHMARK_RESULTS.md). Summary:

### Extraction Quality (Synthetic Data)

| Model | F1 (type) | F1 (entity) | Field Accuracy | Schema Errors |
|-------|-----------|-------------|----------------|---------------|
| Qwen3-32B (Groq cloud) | 0.96 | 0.95 | 82% | 0% |
| Qwen2.5:7b (local T4) | 0.92 | 0.91 | 73% | 1% |

### Per-Type Recall (Qwen2.5:7b on T4)

| Update Type | Recall |
|-------------|--------|
| entity_id | 100% |
| fuel | 100% |
| tasking | 100% |
| weapons | 100% |
| threat | 92% |
| status_change | 71% |
| csar | 0% |

### Full Pipeline (935 real DASH 3 messages)

Seven backends validated across local GPU, DSRC HPC, and cloud APIs (2026-03-29 through 2026-03-31):

| Metric | V100 14B | V100 32B* | T4 7B | Gemini Flash | GPT-4.1 nano | Claude Haiku | Bedrock Sonnet |
|--------|----------|-----------|-------|-------------|--------------|-------------|----------------|
| Duration | **34 min** | ~110 min | 150 min | 55 min | 68 min | 129 min | 75 min |
| Updates | 362 | 168* | 255 | 324 | 318 | 273 | 256 |
| Entities | 58 | 150* | 196 | **254** | 229 | 193 | 206 |
| Threats | 18 | 35* | 32 | **66** | 45 | 52 | 54 |
| Taskings | 6 | 31* | 51 | **86** | 49 | 76 | 69 |
| Confidence | 0.25 | 0.79 | — | **0.80** | 0.78 | 0.71 | — |
| LLM success | 100% | 100% | 100% | 100% | 100% | 100% | 99.6% |
| Degradation | 0 | 0 | 0 | 0 | 0 | 0 | 4 |

*V100 32B partial run (625/935 messages — Slurm reservation expired).

Zero code changes between backends — model-agnostic design validated across all seven.

### Latency

| Hardware | Model | Mean Latency | Max Latency | Meets Real-Time? |
|----------|-------|------------|-------------|-----------------|
| **DSRC V100** | qwen2.5:14b | **2.2s** | — | **Yes** |
| Cloud API | Gemini 2.5 Flash | 3.5s | — | **Yes** |
| Cloud API | GPT-4.1 nano | 4.4s | — | **Yes** |
| **AWS Bedrock** | Claude Sonnet 4.5 | 4.8s | **10.3s** | **Yes** |
| DSRC V100 | qwen2.5:32b | ~7s | — | Yes |
| T4 16GB (AG) | qwen2.5:7b-8k | 7.6s | 60.4s | Yes |
| CPU (AG m7i) | qwen2.5:7b | 4-6 min | -- | No |
| CPU (laptop) | qwen2.5:3b | 33.2s | -- | No |

Both local GPU and AWS Bedrock meet real-time requirements. Bedrock has lower mean and dramatically lower tail latency. CPU-only local deployment is not viable for MASH.

## Known Limitations

### Extraction Gaps

- **CSAR (Combat Search and Rescue): 0% recall.** The model does not reliably extract CSAR-related events. Requires few-shot prompt examples with CSAR scenarios.
- **Status change: 71% recall.** Implicit status changes (e.g., "ORCA01 winchester" meaning weapons depleted) are sometimes missed.
- **Bullseye notation:** The system does not convert bullseye bearing/range ("270/40") to geographic coordinates. Requires the scenario bullseye reference point, which varies per exercise.

### Context and Input

- **4096 token context window** (Qwen2.5 default). Long conversation histories are truncated. Configurable via `num_ctx` in Ollama.
- **STT noise:** Voice-to-text channels contain ~60% noise (radio checks, crosstalk, ASR artifacts). The fusion agent cross-references STT against typed chat, but noisy STT reports can still produce low-confidence CoP updates.
- **Context bleed:** Addressed in prompt engineering but residual risk: the LLM may incorporate information from prior messages in the conversation window rather than the target message. Mitigated by explicit prompt separation of context and target.

### Evaluation

- **No real-data ground truth.** Benchmark numbers are based on synthetic labeled data. Real DASH 3 data has been replayed through the full pipeline but without human-labeled ground truth for systematic accuracy measurement.
- **Single exercise scenario.** All testing uses DASH 3 GBC scenario data. Performance on different scenarios, force structures, or communication patterns is untested.

### Deployment

- **Single GPU.** The system is designed for a single workstation. Scaling to multiple machines or cloud deployment (Analytics Gateway) requires configuration changes but no code changes.
- **CoP database schema pending.** The CoP database writer (issue #17) is blocked on the contractor schema. Current output goes to local SQLite.

## Ethical Considerations

### Information Integrity

Wrong CoP data is worse than no CoP data. An incorrect entity position or status in the CoP can lead to bad decisions downstream. The system addresses this through:
- **Confidence scores** on every update (0.0-1.0)
- **Extraction method labeling** (LLM, regex, passthrough) so users know how the data was produced
- **Tiered write authority** preventing low-confidence or unverified updates from auto-writing to the CoP
- **Source provenance** (channel, speaker, raw message, timestamp) on every update for auditability

### Adversarial Risks

The system processes IRC chat without authentication or message integrity verification. A malicious actor with access to the IRC channel could inject false reports. Mitigations:
- Speaker models develop reliability scores, so new or unreliable speakers' reports receive lower confidence
- Cross-channel corroboration raises confidence; single-source reports are flagged
- Tiered write authority prevents uncorroborated reports from auto-writing
- This is a known gap: the MASH exercise environment is assumed cooperative

### Human Oversight

The "70% is great" design principle explicitly acknowledges that the system will be wrong a meaningful fraction of the time. This is by design:
- **Overtrust prevention:** Confidence scores and extraction methods are always visible. Passthrough messages (confidence 0.0) are clearly marked as unprocessed.
- **Human-in-the-loop:** The tiered write authority system ensures that ambiguous, contradictory, or low-confidence updates require human review before entering the CoP.
- **No autonomous action:** The system produces structured information. It does not generate orders, recommendations, or actions.

### Bias and Representation

- The system processes military jargon and abbreviations from US Air Force wargame exercises. It has not been evaluated on other service branches, coalition partner communication styles, or non-English languages.
- Speaker model learning is based on in-session context only. It does not use pre-existing profiles, demographic data, or historical performance records.
- The "LLM as ontology" approach means the model's training data determines what concepts it can map between. Terminology outside the model's training distribution will receive lower confidence scores but will not be dropped (Pattern B: out-of-ontology lane).

## Data Sources

| Dataset | Classification | Description | Document |
|---------|---------------|-------------|----------|
| DASH 3 GBC Chat Logs | IL2 (Unclassified) | 935 messages, 11 channels, September 2025 exercise | [DASH3_GBC.md](datasets/DASH3_GBC.md) |
| DASH 1 PAE Chat Logs | IL2 (Unclassified) | 26 files, April 2025 exercise (not yet used for evaluation) | -- |
| DASH 2 MEF Chat Logs | IL2 (Unclassified) | 46 files, July 2025 exercise (not yet used for evaluation) | -- |
| Synthetic Labeled Data | N/A | Generated for eval harness; entity-level ground truth | -- |

All data is sourced from DASH wargame exercises conducted by ACT3/C2ES. No real-world operational data is used. See [DATA_SOURCES.md](DATA_SOURCES.md) for access details.

## Provenance Tracking

Every CoP update carries the following provenance fields:

| Field | Description |
|-------|-------------|
| `source_channel` | IRC channel the message was received on |
| `source_speaker` | Username of the speaker |
| `source_message` | Raw text of the original message |
| `timestamp` | When the message was sent (from IRC server or log file) |
| `confidence` | Extraction confidence (0.0-1.0) |
| `extraction_method` | How the update was produced: "llm", "regex", or "passthrough" |
| `context_messages` | Surrounding conversation window for audit trail |
| `reasoning` | LLM's explanation of the extraction (when available) |

Additional provenance captured at the system level (implementation in progress, issue #18):
- Model name, version, and quantization for every LLM call
- Prompt template hash and version
- Circuit breaker state at time of extraction
- Full audit trail: raw message -> CoPUpdate -> CoP database write

## Related Documents

- [ARCHITECTURE.md](ARCHITECTURE.md) -- Full system architecture
- [DESIGN_PHILOSOPHY.md](DESIGN_PHILOSOPHY.md) -- Equifinality, antifragility, FACS principles
- [BENCHMARK_RESULTS.md](research/BENCHMARK_RESULTS.md) -- Detailed evaluation results
- [DATA_SOURCES.md](DATA_SOURCES.md) -- Data access and inventory
- [SCHEMAS.md](SCHEMAS.md) -- CoP database schema reference
- [datasets/DASH3_GBC.md](datasets/DASH3_GBC.md) -- Primary dataset card

## Revision History

| Date | Version | Changes |
|------|---------|---------|
| 2026-03-30 | 0.1.2 | Added Claude Sonnet 4.5 via AWS Bedrock as validated backend (99.6% success, 4.8s latency, no GPU) |
| 2026-03-30 | 0.1.1 | Updated evaluation results with fusion feedback run (100% LLM success, 255 updates, 7.6s latency) |
| 2026-03-29 | 0.1.0 | Initial system card (pre-deployment) |

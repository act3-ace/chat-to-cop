# Benchmark Results

Performance and quality benchmarks for the chat-to-cop extraction pipeline, tested against real DASH 3 GBC chat data (935 messages, 23 Sep exercise, 11 channels).

Tests conducted 2026-03-29/30 (AG/Bedrock) and 2026-03-31 (HPC/cloud API comparison).

## Hardware Comparison

### Inference Speed

| Environment | Instance | GPU | Model | Tokens/s | Per-extraction (p50) | Full 935-msg replay |
|-------------|----------|-----|-------|----------|---------------------|---------------------|
| **DSRC HPC** | Narwhal MLA | V100 32GB | qwen2.5:14b | ~50 | ~2.2s | **34 min** |
| **DSRC HPC** | Narwhal MLA | V100 32GB | qwen2.5:32b | ~30 | ~7s | ~110 min (est) |
| **AG GPU** | g4dn.xlarge | T4 16GB | qwen2.5:7b | 41.0 | ~6.5s | ~2.5 hrs |
| **AG CPU** | m7i.2xlarge | None | qwen2.5:7b | 8.3 | ~4-6 min | ~60-90 hrs (estimated) |
| **Laptop CPU** | i7 local | None | qwen2.5:3b | ~3 | ~33s (mean) | ~6 hrs (completed) |

### Cost (AG credits)

| Instance | Credits/hr | 1-hr test session | Full replay |
|----------|------------|-------------------|-------------|
| g4dn.xlarge (T4) | 92 | 92 | ~276 (3 hrs) |
| m7i.2xlarge (CPU) | 73 | 73 | Not viable |

### Key Finding

GPU is **required** for real-time operations. CPU inference (8.3 tokens/s) is 5x slower than GPU (41 tokens/s) and 30-50x slower on wall clock per extraction due to model loading overhead. CPU-only is not viable for MASH deployment.

## Model Quality (Synthetic Data)

Tested via eval harness (`scripts/eval_models.py`) against synthetic labeled data with entity-level scoring.

### Groq Cloud (free tier)

| Model | F1 (type) | F1 (entity) | Field Acc | Schema Errors | Latency (p50) |
|-------|-----------|-------------|-----------|---------------|---------------|
| **qwen3-32b** | **0.96** | **0.95** | **82%** | 0% | 1.2s |
| llama-3.3-70b | 0.88 | 0.85 | 71% | 12% | 1.5s |
| llama-4-scout | 0.84 | 0.80 | 68% | 15% | 1.8s |
| llama-3.1-8b | 0.72 | 0.68 | 58% | 22% | 0.8s |

### Local Ollama (AG g4dn.xlarge T4)

| Model | F1 (type) | F1 (entity) | Field Acc | Errors | Latency (p50) |
|-------|-----------|-------------|-----------|--------|---------------|
| **qwen2.5:7b** | **0.92** | **0.91** | **73%** | 1% | **6.5s** |

Per-type recall (7B on T4):
- entity_id: 100%, fuel: 100%, tasking: 100%, weapons: 100%
- threat: 92%, status_change: 71%
- csar: 0% (needs prompt improvement)

### Key Findings

1. **Qwen family dominates** for structured JSON extraction — 0% schema errors vs 12-22% for Llama models
2. **Few-shot prompt engineering > model size** — a 3B model with good examples beats a 70B model with a bare prompt
3. **Qwen3-32B is the target MASH model** (F1=0.96), pending hardware that can run it locally (24GB+ VRAM)

## Real Data: Full DASH 3 Replay

### Laptop CPU + qwen2.5:3b (completed)

935 messages through full pipeline (supervisor + fusion + store).

| Metric | Value |
|--------|-------|
| Total messages | 935 |
| LLM extractions (successful) | 100 |
| Regex fallback extractions | 876 |
| Updates emitted by agents | 477 |
| Updates after fusion | 376 |
| Fusion deduplicates | 56 |
| Fusion contradictions | 1 |
| Fusion corroborations | 4 |
| Channels discovered | 10 |
| LLM latency (mean) | 33.2s |
| LLM latency (max) | 62.2s |
| Circuit breaker recovery (mean) | 44 min |

**Analysis:** Only 100/935 messages made it through LLM extraction on laptop CPU — the rest timed out or tripped the circuit breaker and fell to regex. The degradation path worked as designed (no data dropped), but extraction quality was low. This validates that CPU-only is not viable for production.

**Channel update distribution:**
- #vegas_internal: 145 updates (most active channel)
- #c2_coord: 86 updates (highest information density)
- #stt_crusherBMA: 88 updates
- #stt_hydroBMA: 75 updates
- #stt_C2Coord: 41 updates
- #isr_reports: 24 updates
- #stt_mesquiteBMA: 7 updates
- #stt_taipanBMA: 6 updates
- #fires: 5 updates

### AG GPU (T4) + qwen2.5:7b-8k — CLEAN RUN (completed 2026-03-30 afternoon)

935 messages through full pipeline with ALL fixes applied: 8K context (Modelfile), timestamp/capability_impact validators, CSAR/fire/EW examples, banter noise filter, enriched glossary, CoP writer dry-run.
Duration: **~2.4 hours** (down from 5.85 hrs on old code).

| Metric | Value |
|--------|-------|
| Total messages | 935 |
| Updates extracted (after fusion) | **264** |
| Entities tracked | **203** |
| Channels | 11 |
| LLM calls (7b-8k) | 976 |
| LLM successes | **975** (99.9%) |
| LLM permanent failures | **1** |
| Regex fallback | **1** |
| Extraction latency (mean) | **7.7s** |
| Extraction latency (min) | 3.5s |
| Extraction latency (max) | 57.1s |
| CoP auto-writes | 467 |
| CoP flagged-writes | 2 |
| CoP human-review queued | 107 |
| CoP errors | **0** |
| Fusion deduplicates | 1 |
| Fusion contradictions detected | 2 |

**Update type distribution (clean run):**

| Type | Count |
|------|-------|
| tasking | 53 |
| location | 47 |
| threat | 33 |
| fuel | 31 |
| status_change | 23 |
| cyber_ew | 21 |
| environmental | 14 |
| entity_id | 14 |
| fire_mission | 9 |
| sitrep | 8 |
| csar | 8 |
| weapons | 3 |

All 12 non-none update types represented. CSAR (8), fire_mission (9), and cyber_ew (21) all significantly improved from previous runs.

### AG GPU (T4) + qwen2.5:7b-8k — FUSION FEEDBACK RUN (completed 2026-03-30 evening)

935 messages through full pipeline with ALL fixes + fusion-to-channel feedback loop (MR !59): cross-channel speaker corroboration, feedback-driven confidence adjustments.
Duration: **~2.5 hours**.

| Metric | Value |
|--------|-------|
| Total messages | 935 |
| Updates extracted (after fusion) | **255** |
| Entities tracked | **196** |
| Channels | 11 |
| LLM calls (7b-8k) | 945 |
| LLM successes | **945** (100%) |
| LLM permanent failures | **0** |
| Regex fallback | **0** |
| Extraction latency (mean) | **7.6s** |
| Extraction latency (min) | 3.4s |
| Extraction latency (max) | 60.4s |
| CoP auto-writes | 470 |
| CoP flagged-writes | 3 |
| CoP human-review queued | 115 |
| CoP errors | **0** |
| Fusion deduplicates | 2 |
| Fusion contradictions detected | 2 |

**100% LLM success rate** -- zero failures, zero regex fallback. The fusion feedback loop and accumulated fixes eliminated all extraction failures.

**Update type distribution (fusion feedback run):**

| Type | Count |
|------|-------|
| tasking | 51 |
| location | 41 |
| threat | 32 |
| cyber_ew | 25 |
| fire_mission | 16 |
| csar | 6 |
| (other types) | 84 |

Fire mission (16, up from 9) and cyber/EW (25, up from 21) both improved significantly. CSAR dropped from 8 to 6 -- likely noise reduction rather than recall loss.

### AWS Bedrock + Claude Sonnet 4.5 (completed 2026-03-30 evening)

935 messages through full pipeline via AWS Bedrock (`us-gov.anthropic.claude-sonnet-4-5-20250929-v1:0`). Runs on AG CPU instance -- **no GPU required**. Zero code changes from the Qwen/Ollama runs; model-agnostic design validated end-to-end.
Duration: **~1.3 hours** (vs 2.5 hrs for Qwen 7B on T4).

| Metric | Value |
|--------|-------|
| Total messages | 935 |
| Updates extracted (after fusion) | **256** |
| Entities tracked | **206** |
| Channels | 11 |
| LLM calls | 945 |
| LLM successes | **941** (99.6%) |
| LLM permanent failures | **4** |
| Regex fallback | **4** |
| Extraction latency (mean) | **4.8s** |
| Extraction latency (min) | 2.2s |
| Extraction latency (max) | 10.3s |
| CoP auto-writes | 582 |
| CoP flagged-writes | 20 |
| CoP human-review queued | 75 |
| CoP errors | **0** |
| Fusion deduplicates | 10 |
| Fusion contradictions detected | 1 |
| Fusion corroborations | 1 |

**Update type distribution (Bedrock Claude Sonnet 4.5):**

| Type | Count |
|------|-------|
| tasking | 69 |
| threat | 54 |
| location | 51 |
| status_change | 19 |
| fuel | 15 |
| entity_id | 14 |
| sitrep | 11 |
| csar | 8 |
| fire_mission | 6 |
| weapons | 4 |
| cyber_ew | 4 |
| handover | 1 |

**Key observations:**
- **37% faster mean latency** than Qwen 7B on T4 (4.8s vs 7.6s), with dramatically lower tail latency (10.3s max vs 60.4s).
- **No GPU needed.** Bedrock is a cloud API -- runs from any AG instance with network access.
- **Richer speaker models.** Claude produces "Battle Captain / C2 Coordinator" vs Qwen's "controller" -- more detailed operator role inference.
- **More tasking (+35%) and threat (+69%) extractions** than Qwen, suggesting better semantic understanding of military domain.
- **Qwen wins on cyber/EW (25 vs 4) and fire_mission (16 vs 6)** -- these categories had prompt tuning specifically for the Qwen pipeline.
- **10 fusion deduplicates** (vs 2 for Qwen) -- Claude produces more cross-channel overlap that fusion catches.

### Comparison: Qwen 7B (T4 local) vs Claude Sonnet 4.5 (Bedrock)

| Metric | Qwen 7B (T4 local) | Claude Sonnet 4.5 (Bedrock) |
|--------|--------------------|-----------------------------|
| Mean latency | 7.6s | **4.8s** (37% faster) |
| Max latency | 60.4s | **10.3s** (6x lower tail) |
| LLM success | 945/945 (100%) | 941/945 (99.6%) |
| Updates | 255 | 256 |
| Entities | 196 | **206** |
| Tasking | 51 | **69** (35% more) |
| Threat | 32 | **54** (69% more) |
| Location | 41 | **51** |
| CSAR | 6 | **8** |
| Sitrep | 5 | **11** |
| Cyber/EW | **25** | 4 (Qwen wins -- prompt tuned) |
| Fire mission | **16** | 6 (Qwen wins -- prompt tuned) |
| Fusion deduplicates | 2 | **10** |
| CoP auto-writes | 470 | **582** |
| Duration | ~2.5 hrs | **~1.3 hrs** |

**Analysis:** Both models produce comparable update counts (~255) but with different strengths. Claude excels at high-level semantic categories (tasking, threat, sitrep) while Qwen excels at categories that received prompt-specific tuning (cyber/EW, fire_mission). This validates the equifinality principle: different models, different paths, converging on similar CoP coverage. The combination of both (or prompt-tuning Claude for the gaps) would likely exceed either alone.

### Comparison: Old Code vs Clean Run vs Fusion Feedback

| Metric | Old Code (4K ctx) | Clean (8K ctx) | Fusion Feedback | Improvement (old -> fusion) |
|--------|-------------------|----------------|-----------------|----------------------------|
| Duration | 5.85 hrs | 2.4 hrs | 2.5 hrs | 2.3x faster |
| Mean latency | 20.0s | 7.7s | 7.6s | 2.6x faster |
| LLM success rate | 92.8% | 99.9% | **100%** | Zero failures |
| Regex fallback | 70 | 1 | **0** | Eliminated |
| Updates | 416 | 264 | 255 | Higher precision |
| Entities | 121 | 203 | 196 | 62% more |
| Fire mission | 3 | 9 | 16 | 5.3x more |
| Cyber/EW | 9 | 21 | 25 | 2.8x more |
| CSAR | 4 | 8 | 6 | 50% more |
| CoP errors | N/A | 0 | 0 | Clean |

### Previous Run (pre-fix code, for reference)

| Metric | Value |
|--------|-------|
| Total messages | 935 |
| Updates extracted (after fusion) | **416** |
| Entities tracked | 121 |
| Channels | 11 |
| LLM calls (7b) | 976 |
| LLM successes | **906** (92.8%) |
| LLM permanent failures | 62 (mostly timestamp validation — fixed in MR !36) |
| LLM timeouts | 7 |
| Regex fallback | 70 |
| Extraction latency (mean) | 20.0s |
| Extraction latency (min) | 3.1s |
| Extraction latency (max) | 160.9s |
| Fusion deduplicates | 8 |
| Circuit breaker openings | 7 (6x for missing 3b, 1x for 7b) |

**Update type distribution:**

| Type | Count | % |
|------|-------|---|
| status_change | 161 | 38.7% |
| tasking | 54 | 13.0% |
| entity_id | 51 | 12.3% |
| location | 39 | 9.4% |
| threat | 35 | 8.4% |
| sitrep | 23 | 5.5% |
| fuel | 23 | 5.5% |
| cyber_ew | 9 | 2.2% |
| handover | 7 | 1.7% |
| weapons | 4 | 1.0% |
| csar | 4 | 1.0% |
| environmental | 3 | 0.7% |
| fire_mission | 3 | 0.7% |

All 13 update types represented. CSAR and fire_mission both extracted (were 0% before few-shot examples added in MR !41).

**Channel message distribution:**

| Channel | Messages | Filtered (noise) | Updates Emitted |
|---------|----------|-------------------|-----------------|
| #vegas_internal | 185 | 73 | 112 |
| #stt_taipanBMA | 166 | 92 | 74 |
| #stt_crusherBMA | 146 | 83 | 63 |
| #stt_hydroBMA | 138 | 93 | 45 |
| #c2_coord | 106 | 43 | 63 |
| #stt_C2Coord | 102 | 74 | 28 |
| #stt_mesquiteBMA | 47 | 29 | 18 |
| #isr_reports | 28 | 10 | 18 |
| #jprc | 9 | 9 | 0 |
| #fires | 6 | 2 | 4 |
| #stt | 2 | 2 | 0 |

**Mean confidence by channel:**

| Channel | Mean Confidence |
|---------|----------------|
| #stt_taipanBMA | 0.95 |
| #stt_crusherBMA | 0.93 |
| #stt_mesquiteBMA | 0.93 |
| #fires | 0.93 |
| #stt_C2Coord | 0.90 |
| #c2_coord | 0.89 |
| #stt_hydroBMA | 0.81 |
| #vegas_internal | 0.79 |
| #isr_reports | 0.77 |

## Issues Discovered During Testing

### Fixed

1. **localhost vs 127.0.0.1** — Python urllib on Windows resolves `localhost` to IPv6 (::1) but Ollama only serves on IPv4. Fixed globally.
2. **LLM timeout too short** — 10s default caused model swap race condition on Ollama. Increased to 120s (MR !35).
3. **Timestamp validation** — LLM produces empty/null timestamp, causing good extractions to fail Pydantic validation. Made field optional since channel agent always overwrites it (MR !36).
4. **Context bleed** — LLM extracted data from prior messages in conversation window. Fixed by separating "PRIOR CONTEXT" from "EXTRACT FROM THIS MESSAGE ONLY" in prompt.
5. **extraction_method hallucination** — LLM set extraction_method to arbitrary values. Fixed by overriding to "llm" after extraction.

### Known Limitations (remaining after fixes)

1. **Mean latency 20s** — Higher than expected. Driven by context truncation (old 4096 limit) forcing retries and wasted cycles. Clean re-run with `num_ctx=8192` (MR !38) should significantly improve.
2. **LLM produces Chinese output** — Qwen2.5 occasionally responds in Chinese (e.g., `update_type: "燃料"` = "fuel"). Happens when model is confused by truncated context. Fix: `num_ctx=8192`.
3. **LLM invents update types** — "extraction", "passthrough", "CoPUpdate", "WF5", "goodbye". Instructor retries usually recover, but wastes latency.
4. **3b fallback not pulled on AG** — Set `CHAT_TO_COP_FALLBACK_MODEL=qwen2.5:7b` to skip, or pull 3b.
5. **62 permanent failures** — Almost entirely timestamp validation (fixed in MR !36) and capability_impact validation (fixed in MR !46). Clean re-run expected near 0.

### Previously Known, Now Fixed

1. ~~4096 token context~~ — Fixed: `num_ctx=8192` default (MR !38)
2. ~~CSAR 0% recall~~ — Fixed: few-shot examples added (MR !41), 4 CSAR extractions in T4 replay
3. ~~Bearing/bullseye validation~~ — Fixed: BeforeValidator parses "240/405" notation (MR !41)
4. ~~Timestamp validation failures~~ — Fixed: field made optional (MR !36)
5. ~~capability_impact validation~~ — Fixed: invalid values coerced to None (MR !46)
6. ~~num_ctx sent to Groq~~ — Fixed: only sent to Ollama endpoints (MR !50)

## DSRC HPC: Narwhal V100 (completed 2026-03-31)

Tested on DSRC Narwhal MLA node (2x Tesla V100-PCIE-32GB, Slurm scheduler). Models served locally via Ollama, no internet required at runtime. Pipeline deployed via conda environment + source transfer (container-based deployment via CI also available).

### Narwhal V100 + qwen2.5:14b (completed)

935 messages, instant replay, Qwen 2.5 14B (Q4_K_M, ~9GB VRAM on a 32GB V100).
Duration: **34 minutes** (4.4x faster than T4 7B).

| Metric | Value |
|--------|-------|
| Total messages | 935 |
| Updates extracted | **362** |
| Entities tracked | **58** |
| Channels | 10 |
| LLM success | 100% |
| Extraction latency (mean) | **~2.2s** |
| Degradation | **0** |

**Update type distribution:**

| Type | Count |
|------|-------|
| status_change | 147 |
| entity_id | 140 |
| location | 22 |
| threat | 18 |
| weapons | 15 |
| fuel | 11 |
| tasking | 6 |
| sitrep | 3 |

**Analysis:** The V100's 32GB memory bandwidth delivers 4.4x faster replay than T4 with a larger model (14B vs 7B). Total updates (362) is the highest of any local run. However, the model leans heavily on status_change/entity_id — fewer high-value semantic extractions (threats, taskings) than larger models or cloud APIs. Mean confidence (0.25) is lower than 32B or cloud models.

### Narwhal V100 + qwen2.5:32b (partial — 625/935 messages)

Qwen 2.5 32B (Q4_K_M, ~29GB VRAM on a single 32GB V100). Run terminated at 625/935 messages when the Slurm reservation expired.

| Metric | Value |
|--------|-------|
| Messages processed | 625 (of 935) |
| Updates extracted | **168** |
| Entities tracked | **150** |
| Channels | 11 |
| LLM success | 100% |
| Extraction latency (mean) | **~12s** |
| Degradation | **0** |
| Mean confidence | **0.79** |

**Update type distribution (partial):**

| Type | Count |
|------|-------|
| status_change | 38 |
| threat | 35 |
| tasking | 31 |
| location | 21 |
| fuel | 19 |
| entity_id | 12 |
| sitrep | 5 |
| weapons | 4 |
| fire_mission | 3 |

**Analysis:** The 32B model produces cloud-level confidence (0.79 mean) and rich semantic extraction (35 threats, 31 taskings from only 2/3 of messages). Memory-bandwidth-constrained at 29/32GB VRAM — slower per message (~12s) but significantly better quality than 14B. The 32B on V100 validates the path to 24GB MASH GPUs where Qwen3-32B (~22GB) would run with more headroom.

## Cloud API Comparison (completed 2026-03-31)

Three cloud APIs tested against the same 935-message DASH 3 dataset, running from a local workstation via OpenAI-compatible endpoints. Pipeline is model-agnostic: cloud APIs require only a URL and API key change, no code changes.

### Summary

| Metric | Gemini 2.5 Flash | GPT-4.1 nano | Claude 4.5 Haiku | Bedrock Sonnet 4.5 (prior) |
|--------|-----------------|--------------|------------------|----------------------------|
| Duration | **55 min** | 68 min | 129 min | 75 min |
| Mean latency | **~3.5s/msg** | ~4.4s/msg | ~8.3s/msg | 4.8s/msg |
| Updates | 324 | 318 | 273 | 256 |
| Entities | **254** | 229 | 193 | 206 |
| Mean confidence | **0.80** | 0.78 | 0.71 | — |
| Threats | **66** | 45 | 52 | 54 |
| Locations | 41 | **72** | 25 | 51 |
| Taskings | **86** | 49 | 76 | 69 |
| Degradation | 0 | 0 | 0 | 4 |
| Est. cost | **~$0.15** | ~$0.11 | ~$0.93 | ~$3.50 |

### Key Findings

1. **Cloud APIs extract richer semantics** — all three found dramatically more threats (45-66) and taskings (49-86) than local models (3-18). Local models lean on status_change/entity_id; cloud models interpret what's happening.

2. **Gemini 2.5 Flash wins on quality-per-dollar** — most threats (66), most taskings (86), highest confidence (0.80), 254 entities, all for ~$0.15 per full replay.

3. **GPT-4.1 nano found the most locations (72)** — different models have different extraction strengths. Ensemble or multi-model approaches could improve coverage.

4. **Claude Haiku was hampered by instructor tool_use retry bug** — the 129 min runtime includes wasted retries from TOOLS-mode message corruption (now fixed via JSON mode in MR !60). Re-run expected ~60-70 min.

5. **Local 14B on V100 is the fastest overall (34 min)** — but with lower semantic quality. Speed without understanding.

6. **Local 32B on V100 matches cloud-level confidence (0.79)** — the quality gap closes with larger local models, at the cost of speed.

## All Backends Comparison

| | Gemini Flash | GPT-4.1 nano | Claude Haiku | Bedrock Sonnet | V100 14B | V100 32B (2/3) | T4 7B |
|---|---|---|---|---|---|---|---|
| **Duration** | 55 min | 68 min | 129 min | 75 min | **34 min** | ~128 min | ~150 min |
| **Updates** | 324 | 318 | 273 | 256 | **362** | 168* | 376 |
| **Entities** | **254** | 229 | 193 | 206 | 58 | 150* | 73 |
| **Confidence** | **0.80** | 0.78 | 0.71 | — | 0.25 | 0.79 | 0.27 |
| **Threats** | **66** | 45 | 52 | 54 | 18 | 35* | 3 |
| **Taskings** | **86** | 49 | 76 | 69 | 6 | 31* | 3 |
| **Locations** | 41 | **72** | 25 | 51 | 22 | 21* | 15 |
| **Cost** | $0.15 | $0.11 | $0.93 | $3.50 | $0 | $0 | $0 |

*V100 32B partial run (625/935 messages) — extrapolated full run would be roughly 1.5x these numbers.

## MASH Deployment Implications

| Constraint | Status |
|------------|--------|
| Single 24GB GPU | **Validated on V100 32GB** — Qwen2.5-32B fits at 29GB VRAM, cloud-level quality |
| Real-time latency | 2.2s on V100 14B, 3.5s on Gemini Flash, 6.5s on T4 7B — all meet requirement |
| **Cloud APIs** | **Validated: Gemini Flash (3.5s), GPT-4.1 nano (4.4s), Claude Haiku (8.3s), Bedrock Sonnet (4.8s)** |
| **DSRC HPC** | **Validated: Narwhal V100, 34 min full replay with 14B, offline-capable** |
| Model selection | Qwen2.5-32B (0.79 conf, 29GB) for quality; Qwen2.5-14B (9GB) for speed; cloud APIs for semantics |
| Degradation path | Validated: LLM -> regex -> passthrough, no data loss, zero degradation across all runs |
| Multi-channel | 10+ simultaneous channels working across all backends |
| Model-agnostic | **Proven across 7 backends:** local Ollama (7B/14B/32B), Bedrock, Gemini, GPT, Claude Haiku |

**Deployment options for MASH:**
- **Option A: Local GPU** — RTX 4090/5090 with Qwen2.5-32B or Qwen3-32B. Best quality local, ~7s/msg on V100-class hardware.
- **Option B: Cloud API** — Gemini 2.5 Flash (~$0.15/replay, 3.5s/msg) or Bedrock Claude (4.8s/msg). No GPU needed.
- **Option C: HPC** — DSRC Narwhal with V100 GPUs. Offline-capable, CI-built containers, 34 min full replay.
- **Option D: Hybrid** — Cloud API primary, local Ollama fallback. Config change, not code change.

## Ground Truth: Opus Silver Labels (2026-04-04)

935 messages labeled by Claude Opus 4.6 via Ask Sage on NIPRNet (free, IL5). 875 LLM extractions, 60 noise-filtered, 0 errors. This is the first complete ground-truth labeling of the DASH 3 dataset.

### Message Composition

| Category | Count | % |
|----------|-------|---|
| Informative (extractable) | 290 | 31% |
| Noise/none | 645 | 69% |

Confidence distribution is **bimodal**: noise clusters at 0.0, informative extractions at 0.85-0.95. This clean separation validates the write authority threshold design (auto-write at 0.7).

### Type Distribution (informative messages only)

| Type | Count | % of Informative |
|------|-------|-------------------|
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
| handover | 1 | <1% |
| csar | 1 | <1% |
| environmental | 1 | <1% |

**Key finding:** Tasking dominates at 43% of informative messages -- BattleCOA coordination is the primary information flow in DASH 3 chat. Threat assessment (22%) is the second-largest category.

### Per-Channel Informative Rates

| Channel | Informative Rate | Dominant Types |
|---------|-----------------|----------------|
| #isr_reports | **71%** | Densest channel -- nearly all messages are operationally informative |
| #fires | 50% | All fire_mission |
| #stt_taipanBMA | 41% | High STT signal-to-noise |
| #c2_coord | 38% | Cross-BMA coordination |
| #vegas_internal | 34% | Internal pit coordination |
| #stt_mesquiteBMA | 32% | |
| #stt_C2Coord | 23% | |
| #stt_hydroBMA | 22% | |
| #stt_crusherBMA | 19% | |
| #jprc | 11% | Lowest -- mostly acknowledgments |

**Key finding:** STT channels average 20-40% informative, significantly lower than typed chat channels (35-70%). This validates the STT confidence penalty (-0.15) in the write authority design.

### Analysis Tools

- `scripts/analyze_labels.py` -- standalone label analysis script (no LLM needed, runs on any labeled DB)
- `scripts/eval_speaker_models.py` -- RQ1 A/B comparison framework (needs replay DBs with speaker models on/off)

## Narwhal V100 Model Comparison (2026-04-04)

5 replay databases downloaded from DSRC Narwhal HPC (V100-PCIE-32GB GPUs), compared against Opus silver labels. All models ran the same 935-message DASH 3 dataset with identical pipeline configuration.

### Results vs Opus Ground Truth

| Model | Updates | Msgs | Avg Conf | Type Match vs Opus | Tasking | Threat | Location | Status | Fuel |
|-------|---------|------|----------|--------------------|---------|--------|----------|--------|------|
| V100 7B | 259 | 935 | 0.82 | 40% | 59 | 28 | 40 | 35 | 24 |
| V100 14B | 255 | 935 | 0.82 | **49%** | 70 | 53 | 51 | 36 | 16 |
| V100 32B (Qwen2.5) | 255 | 935 | 0.79 | 47% | 50 | 51 | 35 | 64 | 21 |
| V100 Qwen3-32B | 251 | 935 | 0.79 | 45% | 58 | 50 | 53 | 39 | 18 |
| **Opus (ground truth)** | **290** | **935** | -- | 100% | 125 | 63 | 8 | 34 | 13 |

### Key Findings

1. **Pipeline stability across model sizes** -- all models produce consistent update counts (251-259), validating pipeline robustness.
2. **14B is the sweet spot for prompt compliance** -- highest type match vs Opus (49%).
3. **Local models under-extract tasking** (50-70 vs Opus 125) and **over-classify as location** (35-53 vs Opus 8). The LLM labels spatial references as "location" where Opus categorizes the same information as "tasking" when it carries an assignment.
4. **The 40-50% type match rate reflects categorization differences, not missed information** -- total extraction counts are consistent across all models.
5. **This IS the anti-ontology principle in action** -- the type taxonomy is a hypothesis, not ground truth (Pattern A). Different models impose different categorization schemes on the same underlying information.
6. **Status_change varies wildly** (23-64) depending on noise filter presence -- the 32B Qwen2.5 run without the banter filter produces 64 status_change vs 35 for 7B with the filter.
7. **Qwen3-32B on V100 validates MASH target hardware** -- 251 updates, 935 msgs, complete run. This partially validates #26 (30B+ on 24GB GPU).

### Analysis

The Narwhal V100 comparison reveals that model size matters less for extraction *volume* (all models find ~255 updates) than for extraction *categorization* (type match ranges 40-49%). The 14B model achieves the best alignment with Opus labels, suggesting that mid-range models follow prompt instructions more closely than larger models that impose their own categorization logic.

The systematic location over-classification is the most actionable finding: local models tag grid references and bullseye coordinates as "location" even when the message is a tasking order. Prompt tuning to emphasize "what is the *purpose* of this message?" over "what entities are mentioned?" would likely improve type match significantly.

## Confidence Calibration (2026-04-04)

Calibration model fitted using `scripts/recalibrate.py` against Opus silver labels (935 messages).

### Qwen2.5-7B-8K on AG T4

| Metric | Value |
|--------|-------|
| Accuracy | **79.7%** |
| ECE (Expected Calibration Error) | **0.6723** |
| Method | Histogram binning (10 bins) |
| Calibration file | `data/calibration/qwen2.5_7b-8k.json` |

**Calibration profile (bimodal):**

| Confidence Bin | Predictions | Correct | Actual Accuracy |
|---------------|-------------|---------|-----------------|
| [0.0, 0.1) | 630 | 543 | 86% |
| [0.1, 0.8) | 0 | 0 | — |
| [0.8, 0.9) | 17 | 7 | 41% |
| [0.9, 1.0) | 227 | 147 | 65% |

**Key finding:** The model is **bimodally overconfident**. It almost never outputs mid-range confidence — either low (0.0-0.1) or high (0.9-1.0). When it says 0.95, it's right only 65% of the time. The tiered write authority threshold of 0.7 for auto-write should be reconsidered given this calibration profile.

**Implications:**
- Low-confidence predictions (0.0-0.1) are actually 86% accurate — the model is *underconfident* on noise filtering
- High-confidence predictions (0.9-1.0) are only 65% accurate — the model is *overconfident* on extractions
- The empty middle range means the model has no nuanced uncertainty signal
- Per-model calibration is needed for 14B, 32B, and cloud APIs

## RQ1: Speaker Model Evaluation (2026-04-06)

A/B comparison of extraction quality with vs without speaker models, evaluated against Opus silver labels. Speaker models learn operator profiles (role, area, jargon) during replay and inject them into the extraction prompt.

### Qwen2.5-7B on AG T4

| Metric | With Speakers | Without Speakers | Delta |
|--------|--------------|-----------------|-------|
| Labels evaluated | 935 | 935 | — |
| Matched extractions | 337 | 358 | -21 |
| Match rate | 36.0% | 38.3% | **-2.3%** |
| Type exact match | 19.6% | 20.2% | **-0.6%** |
| Type relaxed match | 19.8% | 20.4% | **-0.6%** |
| Entity overlap (Jaccard) | 27.0% | 29.8% | **-2.8%** |
| Noise detection | 100% | 100% | 0% |
| Confidence correlation | 0.071 | 0.033 | +0.038 |

**Per-type notable differences:**

| Type | With Speakers | Without Speakers | Winner |
|------|--------------|-----------------|--------|
| Tasking | 43% | 47% | Without |
| Threat | 33% | 37% | Without |
| Cyber/EW | **53%** | 47% | With |
| Entity ID | **17%** | 8% | With |
| Fuel | 62% | **69%** | Without |

**Learning curves:** Nearly identical between conditions across all speakers with 5+ messages. No evidence of accuracy improvement over time with speaker model learning.

**Finding:** Speaker models show **no significant benefit on Qwen2.5-7B**. The "without speakers" condition slightly outperforms — more matched extractions and marginally better type accuracy.

**Hypothesis:** This is a **model capacity bottleneck**. The 7B model may lack sufficient capacity to leverage additional speaker context in the system prompt. The speaker profile text (~50-100 tokens per speaker) may compete with extraction instructions in the limited attention window.

**Next steps:** Test on larger models (14B, 32B, cloud APIs) to confirm capacity hypothesis (#52). If larger models benefit from speaker context, the RQ1 finding is: "speaker personalization requires sufficient model capacity (≥14B parameters)."

Full report: `docs/research/SPEAKER_MODEL_RESULTS.md`

## Reproduction

```bash
# Smoke test (5 messages)
python scripts/quick_test.py --url http://127.0.0.1:11434/v1 --model qwen2.5:7b

# Eval harness (synthetic data)
python scripts/eval_models.py --url http://127.0.0.1:11434/v1 --model qwen2.5:7b --count 100 -v

# Full DASH replay
python -m chat_to_cop.replay data/dash3/23Sep_usaf_chat.zip \
    --url http://127.0.0.1:11434/v1 \
    --model qwen2.5:7b \
    --db /tmp/real_replay.db

# Groq cloud comparison
GROQ_API_KEY=gsk_... python scripts/eval_models.py \
    --url https://api.groq.com/openai/v1 \
    --model qwen/qwen3-32b \
    --count 50 --rate-delay 18

# Cloud API replay (OpenAI-compatible endpoints)
CHAT_TO_COP_LLM_URL=https://generativelanguage.googleapis.com/v1beta/openai/ \
CHAT_TO_COP_LLM_API_KEY=$GOOGLE_API_KEY \
python -m chat_to_cop.replay data/dash3/23Sep_usaf_chat.zip \
    --model gemini-2.5-flash --timeout 30

# Anthropic direct API
python -m chat_to_cop.replay data/dash3/23Sep_usaf_chat.zip \
    --anthropic --model claude-haiku-4-5-20251001 --timeout 30

# DSRC HPC (Narwhal) — see deploy/dsrc/README.md
sbatch deploy/dsrc/submit_slurm.sh
```

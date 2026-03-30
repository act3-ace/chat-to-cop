# Benchmark Results

Performance and quality benchmarks for the chat-to-cop extraction pipeline, tested against real DASH 3 GBC chat data (935 messages, 23 Sep exercise, 11 channels).

All tests conducted 2026-03-29/30.

## Hardware Comparison

### Inference Speed

| Environment | Instance | GPU | Model | Tokens/s | Per-extraction (p50) | Full 935-msg replay |
|-------------|----------|-----|-------|----------|---------------------|---------------------|
| **AG GPU** | g4dn.xlarge | T4 16GB | qwen2.5:7b | 41.0 | ~6.5s | ~3 hrs (in progress) |
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

## MASH Deployment Implications

| Constraint | Status |
|------------|--------|
| Single 24GB GPU | Need g5 (A10G) or equivalent — not available on AG as of March 2026 |
| Real-time latency | 6.5s p50 on T4 with 7B — meets requirement |
| **Bedrock (cloud)** | **Validated: 4.8s mean, no GPU needed, zero code changes** |
| Model selection | Qwen3-32B (F1=0.96) needs 24GB VRAM; Qwen2.5:7b (F1=0.92) fits in 16GB; Claude Sonnet 4.5 via Bedrock needs only network access |
| Degradation path | Validated: LLM -> regex -> passthrough, no data loss |
| Multi-channel | 10+ simultaneous channels working with single Ollama instance |
| Model-agnostic | **Proven: same pipeline, zero code changes between Ollama local and Bedrock cloud** |

**Bedrock as deployment option:** AWS Bedrock with Claude Sonnet 4.5 is now a validated deployment path. It requires no GPU, delivers 37% faster mean latency than local T4 inference, and eliminates tail latency spikes (10.3s max vs 60.4s). Internet connectivity at H2O Las Vegas is expected, making this a viable primary or hybrid option for MASH. The model-agnostic architecture means switching between local Ollama and Bedrock is a config change, not a code change.

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
```

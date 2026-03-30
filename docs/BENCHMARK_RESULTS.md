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

### AG GPU (T4) + qwen2.5:7b (in progress)

_Results pending — replay running on g4dn.xlarge, estimated completion ~3 hrs from start._

Partial results at 275 messages:
- 52 updates extracted
- 10 channels active, 0 shed
- No timeout failures (120s timeout working)
- Timestamp validation errors causing some good extractions to fall through (fixed in MR !36)
- Prompt truncation warnings on long conversation windows (4096 token context limit)

## Issues Discovered During Testing

### Fixed

1. **localhost vs 127.0.0.1** — Python urllib on Windows resolves `localhost` to IPv6 (::1) but Ollama only serves on IPv4. Fixed globally.
2. **LLM timeout too short** — 10s default caused model swap race condition on Ollama. Increased to 120s (MR !35).
3. **Timestamp validation** — LLM produces empty/null timestamp, causing good extractions to fail Pydantic validation. Made field optional since channel agent always overwrites it (MR !36).
4. **Context bleed** — LLM extracted data from prior messages in conversation window. Fixed by separating "PRIOR CONTEXT" from "EXTRACT FROM THIS MESSAGE ONLY" in prompt.
5. **extraction_method hallucination** — LLM set extraction_method to arbitrary values. Fixed by overriding to "llm" after extraction.

### Known Limitations

1. **4096 token context** — qwen2.5:7b default context window truncates long conversation histories. Can increase with `num_ctx` in Ollama config or reduce `window_size`.
2. **3b fallback not pulled on AG** — The degrading backend tries qwen2.5:3b as fallback, but it wasn't pulled on the GPU instance. Either pull it or set `CHAT_TO_COP_FALLBACK_MODEL=qwen2.5:7b` to skip.
3. **CSAR extraction** — 0% recall on CSAR update type. Needs few-shot examples added to the prompt.
4. **Bearing field validation** — LLM produces "240/405" (bullseye format) for bearing, but schema expects a float. Need to handle bullseye notation.

## MASH Deployment Implications

| Constraint | Status |
|------------|--------|
| Single 24GB GPU | Need g5 (A10G) or equivalent — not available on AG as of March 2026 |
| Real-time latency | 6.5s p50 on T4 with 7B — meets requirement |
| Model selection | Qwen3-32B (F1=0.96) needs 24GB VRAM; Qwen2.5:7b (F1=0.92) fits in 16GB |
| Degradation path | Validated: LLM -> regex -> passthrough, no data loss |
| Multi-channel | 10+ simultaneous channels working with single Ollama instance |

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

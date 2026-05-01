# LLM Speed & Capability Benchmarks (March 2026)

Research into LLM inference speed, latency, and structured output capabilities for the chat-to-CoP pipeline.

**Use case:** Process 10-30 short military chat messages per minute, extract structured JSON world-state updates, push to CoP database in <2 seconds.

## Cloud API Options

### Latency Table (short input ~50 tokens, ~100 token JSON output)

| Model | TTFT (median) | Total Latency | Output Speed | JSON Mode | Price/1M output |
|-------|--------------|---------------|-------------|-----------|----------------|
| **Gemini 2.5 Flash-Lite** | 390ms | ~700ms | 341 t/s | Yes | $0.30 |
| **Mercury 2** (Inception) | ~800ms | <1s | 629-1,023 t/s | Schema-aligned | $0.75 |
| **Claude 4.5 Haiku** | 597ms | ~950ms | 94-135 t/s | Yes | $1.25 |
| **GPT-4.1 nano** | 630ms | ~1,000ms | 145-252 t/s | Yes | $0.40 |
| **GPT-5.4 nano** | -- | -- | ~200 t/s | Yes | $1.25 |
| **Gemini 2.5 Flash** | 450ms | ~1,200ms | 219-244 t/s | Yes | $0.60 |
| **GPT-4.1** | 889ms | ~1,770ms | 42-63 t/s | Yes | $8.00 |
| **Claude Sonnet 4** | 1,042ms | ~2,900ms | 42-50 t/s | Yes | $15.00 |

### Specialized Hardware Providers

| Provider | Model | Output Speed | Notes |
|----------|-------|-------------|-------|
| **Cerebras** | Llama 4 Maverick 400B | 2,522 t/s | Wafer-scale engine |
| **Groq** | Llama 4 Maverick 400B | 549 t/s | Best TTFT |
| **SambaNova** | Llama 4 Maverick 400B | 794 t/s | SN40L RDU |
| **Fireworks AI** | Various | 1,000+ t/s | 4x faster structured output vs vLLM |

### Cloud Assessment

Best fits under 2s total latency: **Gemini 2.5 Flash-Lite** (cheapest), **Mercury 2** (fastest), **Claude 4.5 Haiku** (most consistent). Network from Las Vegas to major cloud providers adds 15-60ms — negligible.

## Local Models on 24GB VRAM

### Performance on RTX 4090/5090

| Model | Size | Quant | VRAM | tok/s (4090) | tok/s (5090 est) | JSON Quality |
|-------|------|-------|------|-------------|-----------------|-------------|
| **Qwen 3 30B-A3B MoE** | 30B (3B active) | Q4_K_M | ~18GB | **196 t/s** | ~330 t/s | Good |
| **Qwen 2.5 14B** | 14B | Q4_K_M | ~9GB | 60-80 t/s | -- | Excellent |
| **Phi-4 14B** | 14B | Q4_K_M | ~10GB | 60-80 t/s | -- | Good |
| **Llama 3.3 8B** | 8B | Q4_K_M | ~6GB | 128 t/s | 213 t/s | Good |
| **Gemma 3 27B** | 27B | Q4_K_M | ~22.5GB | 38 t/s | ~64 t/s | Good |
| **Qwen 3 32B** | 32B | Q4_K_M | ~22GB | 34 t/s | ~58 t/s | Excellent |

### Top Pick: Qwen 3 30B-A3B MoE

- Mixture-of-experts: 30B total params but only 3B active per token = 14B-class speed with better quality
- 196 tokens/sec on RTX 4090 — for a 100-token JSON output, that's **~0.5 seconds**
- Fits comfortably in 18GB VRAM, leaving headroom
- Qwen family excels at structured/JSON output

### RTX 5090 vs 4090

| Metric | RTX 4090 | RTX 5090 |
|--------|----------|----------|
| VRAM | 24GB | 32GB |
| Memory Bandwidth | 1,008 GB/s | 1,790 GB/s |
| Speed improvement | baseline | 28-67% faster |
| 8B model | 128 t/s | 213 t/s |
| 32B model | 34 t/s | 61 t/s |

### Inference Engines

| Engine | Best For | JSON Support | Notes |
|--------|----------|-------------|-------|
| **Ollama** | Prototyping, single-user | JSON schema (v0.5+) | Easiest setup, within 13% of vLLM |
| **llama.cpp** | Max control, edge | GBNF grammar (100% valid) | Best guaranteed JSON |
| **vLLM** | Multi-user production | Yes | Overkill for our volume |
| **TensorRT-LLM** | Ultra throughput | Yes | Complex setup, NVIDIA only |

**Recommendation:** Start with **Ollama** for prototyping, move to **llama.cpp server** for production (guaranteed JSON via GBNF grammar constraints).

### Quantization

| Level | Quality Loss | Recommendation |
|-------|-------------|----------------|
| Q8_K | Negligible | If VRAM allows |
| **Q5_K_M** | Very small | Best quality/speed balance |
| **Q4_K_M** | Small | **Recommended default** |
| IQ3_XXS | Noticeable | Only for huge models |

## Military Domain Considerations

### General LLMs vs Military Text

Per TRAC research, general LLMs show "sub-optimal performance on Army use cases due to domain-specific vocabulary." A fine-tuned 7B model (TRACLM-v3) outperformed 56B Mixtral on 3/5 Army tasks.

### Mitigation

1. **System prompt with glossary** — Include exercise-specific abbreviations, callsigns, brevity codes
2. **Few-shot examples** — 3-5 messages with expected JSON in the prompt
3. **Fine-tune** — Qwen 2.5 7B/14B on ~500-1,000 annotated messages from DASH data (we have this data)
4. **Post-processing validation** — Check extracted entities against known track/callsign databases

### Existing Military Models

| Model | Base | Notes |
|-------|------|-------|
| TRACLM-v3 | Mistral 7B | 80M+ tokens Army data, 69.2% on Army tasks |
| Defense Llama | Llama 3 | Scale Donovan platform only |

## Recommended Architecture

```
[Tier 1: Regex/Rules] ~0ms
  Existing regex patterns for track numbers (see config/)
  Known callsign dictionary lookup
  Coordinate regex (lat/lon, MGRS)
  Filter acks ("c", "copy", ".")
  → Fast-path to CoP for high-confidence extractions

[Tier 2: Local LLM] ~0.3-0.6s
  Qwen 3 30B MoE Q4_K_M on Ollama
  JSON schema output enforcement
  System prompt with glossary + few-shot examples
  → Handles 80-90% of messages

[Tier 3: Cloud fallback] ~0.5-1.5s
  Gemini 2.5 Flash or Claude 4.5 Haiku
  For ambiguous/complex messages local model flags low-confidence
  → Rare path, <10% of messages
```

At 30 messages/min, a single RTX 4090 processes all of Tier 2 using <15% of its capacity.

## Sources

- [Artificial Analysis LLM Leaderboard](https://artificialanalysis.ai/leaderboards/models) (March 2026)
- [Home GPU LLM Leaderboard](https://awesomeagents.ai/leaderboards/home-gpu-llm-leaderboard/)
- [Inception Mercury 2 Launch](https://www.inceptionlabs.ai/models) (Feb 2026)
- [TRAC: Fine-Tuning LLMs for the Army Domain](https://arxiv.org/html/2410.20297v1)
- [vLLM vs TensorRT vs Ollama vs llama.cpp on RTX 5090](https://dev.to/soytuber/)
- [LLM Structured Output in 2026](https://dev.to/pockit_tools/)

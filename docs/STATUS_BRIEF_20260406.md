# Chat-to-CoP Status Brief — 6 April 2026

**T-7 weeks to MASH (May 2026, H2O Las Vegas)**

## Executive Summary

The chat-to-cop pipeline is operationally ready. 832+ tests, 34 MRs merged, 7 validated backends across local GPU, DSRC HPC, and cloud APIs. Two key research findings landed this week: RQ1 speaker model evaluation and confidence calibration. Remaining work is research extension, documentation, and deployment configuration.

## Key Findings This Week

### RQ1: Speaker Models (7B Initial Result)

**Finding: No significant benefit from speaker models on Qwen2.5-7B.**

| Metric | With Speakers | Without | Delta |
|--------|:---:|:---:|:---:|
| Type exact match | 19.6% | 20.2% | -0.6% |
| Entity overlap | 27.0% | 29.8% | -2.8% |
| Noise detection | 100% | 100% | 0% |

**Interpretation:** The 7B model likely lacks capacity to leverage speaker context (role, area, jargon) in the system prompt. Multi-backend testing (#52) will test this hypothesis on 14B, 32B, and cloud APIs.

**RQ1 implications for MASH:** Speaker models add no overhead but no measurable benefit at 7B. For MASH target hardware (32B on 24GB GPU), speaker models may become valuable — pending confirmation.

### Confidence Calibration

**Finding: Qwen2.5-7B is bimodally overconfident (ECE = 0.67).**

| Confidence Bin | Predictions | Actually Correct |
|:---:|:---:|:---:|
| Low (0.0-0.1) | 630 | 86% (underconfident) |
| High (0.9-1.0) | 227 | 65% (overconfident) |
| Mid (0.1-0.9) | 17 | 41% |

**Implication:** The tiered write authority threshold of 0.7 for auto-write may be too aggressive. When the model says 0.95, it's right only 65% of the time. Recommend raising auto-write threshold to 0.85+ or requiring per-model calibration before deployment.

## Pipeline Status

| Component | Status | Notes |
|-----------|--------|-------|
| Extraction pipeline | COMPLETE | 100% LLM success on Qwen, 99.6% on Claude |
| Degrading backend | COMPLETE | LLM -> regex -> passthrough, zero data loss |
| Fusion agent | COMPLETE | Cross-channel deconfliction, speaker corroboration |
| Speaker models | COMPLETE | Learning works; benefit unproven on 7B |
| Opus silver labels | COMPLETE | 935 messages, Claude Opus 4.6 via Ask Sage |
| Confidence calibration | COMPLETE | 7B done; other models needed |
| Narwhal HPC | VALIDATED | 5 models tested, 14B best prompt compliance |
| AG GPU deployment | VALIDATED | T4 16GB, full deployment guide |
| Cloud APIs | VALIDATED | Gemini Flash, GPT-4.1 nano, Claude Haiku, Bedrock Sonnet |
| CoP writer | BLOCKED | Waiting on contractor schema |
| Docker deployment | COMPLETE | `docker compose up` |
| Tests | 832+ passing | CI enforced on every push |

## Open Issues (14)

### Blocked (need external input)
- **#17** CoP writer — contractor schema
- **#37** Equifinality Phase 1 — Colin provenance check
- **#48** MACE catalog Part C — Colin

### Active / Ready to Work
- **#50** Metadata dict validation — quick fix (in progress)
- **#51** vLLM on AG — alternative to Ollama (NEW)
- **#52** Multi-backend speaker A/B — RQ1 extension (NEW)
- **#26** 30B on 24GB GPU — true 24GB validation still needed
- **#30** Calibrate other models — framework done, needs runs

### Research (Deferred)
- **#43** Confidence-aware cascading — code exists, needs testing
- **#44** Self-MoA for high-stakes types
- **#45** Triage classifier
- **#46** Speaker model routing (depends on #43)
- **#47** Temporal decay + path entropy

### No Longer Blocking
- ~~#35~~ Speaker model eval — initial results complete, extension = #52

## Recommendations

1. **Run 14B A/B on AG** (free, ~4 hours) — confirm/deny capacity hypothesis before MASH
2. **Raise auto-write threshold** to 0.85+ based on calibration data, or require per-model calibration
3. **Brief Mia on RQ1 results** — her trust calibration expertise directly applies to the overconfidence finding
4. **Nudge Colin** on #37/#48 provenance — two issues blocked, 15-minute conversation needed
5. **Try vLLM on AG** (#51) — may solve Ollama's recurring context window and startup issues
6. **Coordinate with Juan's team** — they pivoted to LLMs; compare approaches before MASH

## MASH Deployment Readiness

| Requirement | Status | Risk |
|-------------|--------|------|
| Single-box deployment | READY | Docker Compose |
| GPU inference | READY | Qwen 7B/14B on T4, 32B on V100 |
| Cloud fallback | READY | Gemini Flash ($0.15/run), Bedrock Sonnet |
| Kill switch | READY | REST endpoint, per-agent control |
| Write authority | READY (design) | Thresholds need recalibration |
| CoP integration | BLOCKED | Contractor schema |
| Ground truth labels | READY | 935 Opus silver labels |
| Monitoring | PARTIAL | metrics.py works; OpenTelemetry swap planned |

**Bottom line:** Pipeline works. Research is generating findings. The remaining 7 weeks are for hardening, calibration, and deployment configuration — not new feature development.

---

*Prepared by Scott Clouse (CAIO) with Claude Code assistance*
*Classification: UNCLASSIFIED // IL2*

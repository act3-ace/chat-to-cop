# MASH Production Configuration and Flip Criteria

Default configuration for the MASH event (May 2026), derived from the RQ1
N=100 speaker model sweep and calibration audit. This document records the
production defaults, the evidence required to change them, and the sign-off
chain. For the operational decision tree and recovery procedures, see
[MASH_GO_NO_GO.md](MASH_GO_NO_GO.md).

---

## Default production configuration

These values are the MASH defaults unless a flip is approved through the
process described below. Environment variable names map to `config.py`
(`PipelineConfig` and its nested configs via pydantic-settings).

| Setting | Value | Env var | Rationale |
|---------|-------|---------|-----------|
| Speaker models | OFF | `CHAT_TO_COP_USE_SPEAKER_MODELS=false` | RQ1 N=100 sweep: speakers hurt Type Exact on all four model sizes (p<0.0001) |
| Primary model | `qwen2.5:14b` (pinned) | `CHAT_TO_COP_LLM_MODEL=qwen2.5:14b` | Best accuracy/compute tradeoff; 14B-to-32B adds only +1.3pp Type Exact |
| Context window | 8192 | `CHAT_TO_COP_LLM_NUM_CTX=8192` | Minimum required for full system prompt + conversation window |
| Fallback model | `qwen2.5:3b` | `CHAT_TO_COP_FALLBACK_MODEL=qwen2.5:3b` | Lightweight fallback when primary circuit breaker opens |
| Auto-write threshold | 0.95 | `CHAT_TO_COP_COP_AUTO_THRESHOLD=0.95` | Calibration finding: 7B says 0.95 but is correct 65% of the time (ECE=0.67); 14B similarly overconfident |
| Flag threshold | 0.50 | `CHAT_TO_COP_COP_FLAG_THRESHOLD=0.5` | Below this -> HUMAN review; mid-range confidence bucket is essentially empty on Qwen2.5 |
| High-risk types | weapons, csar, fire_mission, cyber_ew | `CHAT_TO_COP_COP_HIGH_RISK_TYPES` | Always routed to human review regardless of confidence |
| CoP writer mode | dry-run | `CHAT_TO_COP_COP_API_URL=""` | No external writes until Sarah Bowman confirms schema; flip to real endpoint on-site |
| LLM timeout | 120s | `CHAT_TO_COP_LLM_TIMEOUT=120.0` | Generous for cold starts; reduce to 30s once model is warm |
| Circuit breaker | 3 failures / 30s cooldown | `CHAT_TO_COP_CIRCUIT_FAIL_MAX=3`, `CHAT_TO_COP_CIRCUIT_COOLDOWN=30` | Standard degrading backend settings |

### Minimal .env file for MASH

```bash
CHAT_TO_COP_LLM_URL=http://127.0.0.1:11434/v1
CHAT_TO_COP_LLM_MODEL=qwen2.5:14b
CHAT_TO_COP_LLM_NUM_CTX=8192
CHAT_TO_COP_USE_SPEAKER_MODELS=false
CHAT_TO_COP_DB_PATH=data/mash_live.db
# Set when Sarah confirms the endpoint:
# CHAT_TO_COP_COP_API_URL=http://COP_SERVER:PORT/api/updates
```

---

## Speaker model flip criteria

The following criteria come verbatim from the speaker profile degradation
report (`docs/SPEAKER_MODEL_RESULTS.md`, section "Decision: speakers OFF
for MASH"). They must all be satisfied before speaker models are turned on
for any production run at MASH.

> Keep speakers OFF unless a redesigned approach achieves (a) non-inferior
> Type Exact AND (b) a measurable reduction in false positives on
> "none/noise" messages, measured on a human-audited subset.

### Required evidence to justify a flip

1. **Non-inferior Type Exact.** The proposed speaker approach must produce
   Type Exact accuracy that is not statistically worse than the speakers-OFF
   baseline for the same model, at alpha=0.05 (one-sided non-inferiority
   test). The current 14B baseline is 46.7% Type Exact (95% CI [46.4, 46.9],
   N=200 pooled across temperature).

2. **Reduced false positives on none/noise messages.** The proposed approach
   must show a measurable reduction in false positive extractions on messages
   labeled `none` (noise, chatter, acknowledgments). "Measurable" means the
   point estimate is negative (fewer false positives) and the 95% CI excludes
   zero.

3. **Minimum sample size.** Both conditions above must be evaluated on at
   least N=50 independent replications per cell (speakers ON vs OFF), using
   the same eval harness and silver labels as the N=100 sweep
   (`scripts/eval_speaker_sweep.py`, `data/labels/dash3_silver_labels_opus.jsonl`).

4. **Human-audited subset.** At least 100 messages from the evaluation must
   be independently audited by a human (not the person who built the
   approach) to confirm the silver label quality on the specific message
   types where the improvement is claimed.

5. **Compute budget.** The proposed approach must not increase per-message
   latency by more than 50% relative to the speakers-OFF baseline on the
   same hardware. The current 14B speakers-OFF mean latency is the reference.

### What counts as a "redesigned approach"

The N=100 results ruled out the current prompt-injection mechanism (full
speaker profile injected into the system prompt). The following are
considered redesigned approaches and would need to pass the criteria above:

- Selective retrieval (retrieve relevant prior messages instead of full profile)
- Two-stage extraction (extract first, then adjust with speaker context)
- Per-type routing (enable speakers only for types where they help)
- LoRA fine-tuning (speaker knowledge in model weights, not prompt tokens)
- Behavioral routing (speaker reliability scores adjust confidence thresholds only)

Simply re-running the existing prompt-injection mechanism with a newer or
larger model does not count as a redesign.

---

## Model flip criteria

The default model is `qwen2.5:14b`. To change the production model:

1. Run the full eval sweep (`scripts/eval_speaker_sweep.py`) on the
   candidate model with N>=50 replications, speakers OFF.
2. The candidate must meet or exceed the 14B baseline on Type Exact
   (46.7%) with non-overlapping 95% CIs, or demonstrate a compelling
   latency improvement (>2x faster) with no more than 2pp Type Exact loss.
3. Document the results in `docs/SPEAKER_MODEL_RESULTS.md` or a new
   benchmark report, and update this section with the new baseline.

---

## Confidence threshold flip criteria

The auto-write (0.95) and flag (0.50) thresholds were set based on the
Qwen2.5-7B calibration curve (ECE=0.67). To change them:

1. Run `scripts/calibration_analysis.py` on the production model and
   produce an updated calibration curve.
2. The new thresholds must be justified by the calibration data: the
   auto-write threshold should correspond to the calibrated confidence
   level where precision exceeds 90% on the silver labels.
3. If a `CalibrationModel` JSON is loaded (`CHAT_TO_COP_CALIBRATION_MODEL`),
   thresholds operate on calibrated confidence, so a lower raw threshold
   may be appropriate. Document which mode (raw vs calibrated) the
   threshold applies to.

---

## Sign-off

Both signatures are required before any flip is applied to the production
configuration at MASH. "Flip" means changing any value in the table above
to something other than the documented default.

| Role | Name | Signature | Date |
|------|------|-----------|------|
| Project lead | Scott Clouse | _________________ | ____-__-__ |
| Reviewer | _________________ | _________________ | ____-__-__ |

The reviewer should be someone who did not build the proposed change.
Mia Kollia, Colin Leong, or Jennifer Carlet are all acceptable reviewers.

### Process

1. The person proposing the flip opens a GitLab issue describing the
   change, the evidence collected, and how each criterion above is met.
2. The reviewer checks the evidence against the criteria in this document.
3. Both sign here (or in the MR that updates this document) before the
   flip is applied.
4. The MR updating the defaults in `config.py` and this document is
   merged to `main` before the event.

No flips are applied on the day of the event unless they fix a critical
bug (per hard rule #3 in [MASH_GO_NO_GO.md](MASH_GO_NO_GO.md)).

---

## References

- [SPEAKER_MODEL_RESULTS.md](SPEAKER_MODEL_RESULTS.md) -- RQ1 N=100 sweep results, per-model and per-type breakdowns
- [MASH_GO_NO_GO.md](MASH_GO_NO_GO.md) -- Operational decision tree, recovery procedures, hard rules
- [MASH_DEPLOYMENT.md](MASH_DEPLOYMENT.md) -- Deployment options, setup guide, troubleshooting
- `src/chat_to_cop/config.py` -- Canonical config definitions (pydantic-settings)
- `scripts/eval_speaker_sweep.py` -- Evaluation harness used for the N=100 sweep

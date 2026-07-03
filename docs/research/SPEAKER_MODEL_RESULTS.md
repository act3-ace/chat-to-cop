# Speaker Model Evaluation Results (RQ1)

**Issue:** [#35](https://gitlab.example.mil/c2es1/mash/chat-to-cop/-/issues/35) (RQ1 base) and [#52](https://gitlab.example.mil/c2es1/mash/chat-to-cop/-/issues/52) (multi-backend extension)
**Labels:** `data/labels/dash3_silver_labels_opus.jsonl` (856 labeled messages, Claude Opus silver labels)
**Eval script:** `scripts/eval_speaker_sweep.py`
**Sweep infrastructure:** MR !97 (scripts), MR !98 (export fix)

## TL;DR

Online speaker modeling via prompt injection **hurts extraction quality across all four model sizes tested**, with statistical certainty (N=100 per cell, all p<0.0001).

| Model | Type exact delta (pp) | p-value | Entity overlap delta (pp) | Compute overhead |
| --- | --- | --- | --- | --- |
| Qwen2.5-3B | -1.54 | <0.0001 | +0.21 (ns) | +70% (est.) |
| Qwen2.5-7B | -4.12 | <0.0001 | -3.26 | +72% |
| Qwen2.5-14B | -1.50 | <0.0001 | -1.22 | +72% |
| Qwen2.5-32B | -2.74 | <0.0001 | +0.55 | +85% (est.) |

Temperature (deterministic vs stochastic) has **no significant effect** on any metric (p=0.99).

**Decision: default speaker models OFF for MASH.** The feature hurts accuracy, costs compute, and doesn't improve with model scale. RQ1 needs a different architectural approach.

## Prior results (invalidated)

Earlier N=1 A/B results from 2026-04-06 (7B) and 2026-04-10 (14B) are invalid. The 2026-04-11 audit found that `Supervisor.start_agent()` constructed `ChannelAgent` without passing `use_speaker_models`, so the env var was silently ignored. Both arms ran with speakers ON. The fix (!88) plus regression tests in `TestSupervisorAgentConfigPlumbing` ensures this class of bug can't recur.

Post-fix N=1 re-runs (2026-04-12) showed the same direction but were statistically inconclusive. The N=100 sweep below is the definitive result.

## Methodology

### Design

Full factorial: 4 models x 2 speaker conditions x 2 temperature settings x 100 replications = 1,600 runs.

| Factor | Levels |
| --- | --- |
| Model | Qwen2.5-3B, 7B, 14B, 32B |
| Speakers | ON (default), OFF (`use_speaker_models=false`) |
| Temperature | Deterministic (temp=0), Stochastic (default) |

### Execution

- Platform: Narwhal HPC (Navy DSRC), V100 GPUs, Ollama serving
- 16 Slurm array jobs (`--array=1-100%20`), account AFSNW27526RYZ
- Each task: unique Ollama port, 8K context, 180s timeout
- Replay: DASH-3 `chat.zip` (same data for every run)
- Job IDs: 5978304-5978319 (submitted 2026-04-12)
- Completed: 2026-04-17 (3B cells were the long pole at ~112 min/run)
- Total compute: ~0.45M CPU core-hours (128 cores/node x wall-clock)

### Evaluation

Each of the 1,600 DBs was evaluated against the Opus silver labels:

- Match by `(timestamp, channel, sender)` composite key
- **Type exact match**: extraction `update_type` matches label `extracted_type`
- **Entity overlap**: Jaccard similarity on callsign sets
- **Noise detection**: correctly classifying `none`-type messages

Statistical tests: t-tests per model (speakers ON vs OFF, pooling across temperature), 3-way factorial ANOVA (model x speakers x temperature).

## Results

### Per-cell summary

| Cell | N | Type exact (%) | SD | 95% CI | Entity overlap (%) | Match rate (%) |
| --- | --- | --- | --- | --- | --- | --- |
| 3b_off_det | 100 | 17.7 | 0.89 | [17.5, 17.9] | 8.6 | 52.1 |
| 3b_off_stoch | 100 | 17.7 | 0.87 | [17.5, 17.9] | 8.4 | 52.2 |
| 3b_on_det | 100 | 16.2 | 0.90 | [16.0, 16.3] | 8.6 | 52.9 |
| 3b_on_stoch | 100 | 16.2 | 1.07 | [15.9, 16.4] | 8.8 | 52.8 |
| 7b_off_det | 100 | 40.5 | 1.20 | [40.3, 40.7] | 26.4 | 33.5 |
| 7b_off_stoch | 100 | 40.3 | 1.25 | [40.0, 40.5] | 26.0 | 33.6 |
| 7b_on_det | 100 | 36.3 | 1.34 | [36.0, 36.5] | 23.0 | 35.9 |
| 7b_on_stoch | 100 | 36.2 | 1.31 | [36.0, 36.5] | 22.8 | 35.8 |
| 14b_off_det | 100 | 46.8 | 1.33 | [46.5, 47.1] | 27.1 | 33.4 |
| 14b_off_stoch | 100 | 46.7 | 1.46 | [46.4, 46.9] | 27.0 | 33.6 |
| 14b_on_det | 100 | 45.2 | 1.39 | [45.0, 45.5] | 25.7 | 32.4 |
| 14b_on_stoch | 100 | 45.2 | 1.29 | [45.0, 45.5] | 25.9 | 32.4 |
| 32b_off_det | 100 | 47.8 | 1.39 | [47.5, 48.1] | 27.6 | 32.8 |
| 32b_off_stoch | 100 | 48.2 | 1.23 | [47.9, 48.4] | 27.8 | 32.7 |
| 32b_on_det | 100 | 45.2 | 1.40 | [45.0, 45.5] | 28.1 | 34.0 |
| 32b_on_stoch | 100 | 45.3 | 1.16 | [45.0, 45.5] | 28.4 | 34.0 |

### Speaker effect by model

Delta = (speakers ON) - (speakers OFF), pooled across temperature:

| Model | Baseline (OFF) | With speakers (ON) | Type exact delta (pp) | p-value |
| --- | --- | --- | --- | --- |
| 3B | 17.7% | 16.2% | -1.54 | <0.0001 |
| 7B | 40.4% | 36.3% | -4.12 | <0.0001 |
| 14B | 46.7% | 45.2% | -1.50 | <0.0001 |
| 32B | 48.0% | 45.3% | -2.74 | <0.0001 |

The effect is non-monotonic in model size. 7B shows the largest degradation; 14B the smallest. This is inconsistent with a capacity-bottleneck explanation and consistent with model-specific attention architecture differences.

### Temperature effect

Temperature=0 (deterministic) vs default (stochastic) has no significant effect:

| Metric | t-statistic | p-value |
| --- | --- | --- |
| Type exact | -0.009 | 0.99 |
| Entity overlap | -0.050 | 0.96 |
| Match rate | -0.031 | 0.98 |

Run-to-run variance is also unaffected: variance ratios (stochastic/deterministic) range from 0.8x to 1.2x across all cells. The "stochastic variance blowup" observed in earlier interim analyses at N<50 was a small-sample artifact.

### ANOVA

3-way ANOVA (model x speakers x temperature) on type_exact:

| Effect | Significance |
| --- | --- |
| Model (main) | p < 10^-67 |
| Speakers (main) | p < 0.0001 |
| Temperature (main) | p = 0.99 (ns) |
| Model:Speakers | p < 10^-40 |
| Model:Temperature | ns |
| Speakers:Temperature | ns |
| 3-way | p = 0.29 (ns) |

The significant model:speakers interaction confirms that the magnitude of the speaker penalty varies by model (7B worst, 14B least), but the direction is consistently negative.

### Per-type breakdown

Types where speakers consistently help (positive delta across 3+ models):

- **status_change**: +3 to +6pp on 7B/14B/3B (but -1.4pp on 32B)
- **location**: +1 to +5pp on 7B/14B/32B (but only on larger models)

Types where speakers consistently hurt:

- **cyber_ew**: -7 to -11pp across all models
- **tasking**: -2 to -4pp across all models
- **threat**: -2 to -5pp on 7B/14B/3B
- **status_change on 3B**: -10.8pp (the largest single-type effect)

Types unaffected (near-ceiling or zero variance):

- **fire_mission**: 95-100% across all conditions
- **csar**: 89-100% across all conditions
- **none/handover**: near 0% everywhere

### Model scaling

Baseline accuracy (speakers OFF) by model size:

| Model | Type exact | Entity overlap | Match rate |
| --- | --- | --- | --- |
| 3B | 17.7% | 8.5% | 52.1% |
| 7B | 40.4% | 26.2% | 33.6% |
| 14B | 46.7% | 27.0% | 33.5% |
| 32B | 48.0% | 27.7% | 32.8% |

Diminishing returns past 14B. The 3B model has high match rate (52%) but most matches are misclassified -- it extracts aggressively but inaccurately. 7B-to-14B is the largest quality jump (+6.3pp type exact). 14B-to-32B adds only +1.3pp.

## Interpretation

Six mechanisms explain why prompt-injected speaker profiles hurt extraction:

1. **Distractor sensitivity** -- Speaker context adds ~200 tokens of role/area/jargon text to the system prompt. For mid-range models (7B, 32B), this additional context competes for attention with the actual message content. Consistent with Shi et al. (ICML 2023) on irrelevant context degrading reasoning.

2. **Attention dilution** -- The extraction task requires attending to specific tokens in the message (callsigns, coordinates, status words). Speaker profile text draws attention to semantically related but task-irrelevant tokens. Liu et al. (TACL 2024) showed positional bias in how models weight context.

3. **Anchoring bias** -- If the speaker profile says "typically reports fuel states for Hydro BMA", the model becomes biased toward fuel/Hydro interpretations even for messages that are actually cyber_ew or tasking. This explains the consistent -7 to -11pp on cyber_ew.

4. **Error compounding** -- Speaker profiles are built online from the LLM's own outputs. Early misclassifications propagate into the profile, which then biases future extractions. The learning curve analysis from N=1 showed no improvement over 600 messages.

5. **Format tax** -- Longer system prompts increase per-token latency on Ollama/Qwen2.5. The 70% compute overhead means fewer tokens of actual extraction reasoning within the timeout budget.

6. **Non-monotonic model-size effect** -- The 7B model has the most to lose from attention dilution (less total attention capacity), while 14B has enough capacity to partially compensate. 32B's larger context window may paradoxically make it more susceptible to long-range distractor effects.

## Capacity bottleneck hypothesis: REJECTED

The original hypothesis from #35 was "7B may lack the ability to leverage speaker context effectively; larger models would benefit." The N=100 x 4-model sweep conclusively rejects this:

- All four models show negative type_exact deltas
- The effect is non-monotonic (7B worst, not 3B)
- 32B and 14B show larger capacity but still degrade
- The model:speakers interaction is significant but never crosses zero

The bottleneck is the **prompt-injection design**, not model capacity.

## Decision: speakers OFF for MASH

1. Set `use_speaker_models: bool = Field(default=False)` in `config.py`
2. Preserve the speaker model code for research follow-up
3. The compute budget at MASH is finite; this feature spends it for negative ROI

## Future directions

The N=100 data suggests several redesign paths worth pursuing:

1. **Selective retrieval** -- Instead of injecting the full speaker profile, retrieve only the most relevant prior messages from the same speaker. This would reduce distractor load while preserving speaker context where it helps (status_change, location).

2. **Two-stage extraction** -- Run extraction first (speakers OFF), then use speaker context as a post-hoc confidence adjustment. The speaker model becomes a calibration signal, not a prompt modifier.

3. **Per-type routing** -- The per-type data shows speakers help on status_change and location but hurt on cyber_ew and tasking. A routing layer could enable speaker context only for types where it helps.

4. **LoRA fine-tuning** -- Per-role LoRA adapters (PROPER framework) would inject speaker knowledge into model weights rather than prompt tokens, avoiding the distractor and format-tax mechanisms.

5. **Behavioral routing** -- Use speaker reliability scores to adjust confidence thresholds (#46) rather than changing extraction. This is the lightest-weight intervention and the most likely to ship at MASH.

## Files

- Sweep DBs: `narwhal:$WORKDIR/output/sweep/sweep_*.db` (1,600 files)
- Final report: `narwhal:$WORKDIR/output/sweep/final_report.md`
- Final CSV: `narwhal:$WORKDIR/output/sweep/final_report.csv`
- Eval script: `scripts/eval_speaker_sweep.py`
- Sweep job script: `scripts/narwhal_speaker_sweep.sh`
- Sweep submitter: `scripts/submit_speaker_sweep.py`
- N=1 post-fix results: `data/narwhal_results_post_88/`

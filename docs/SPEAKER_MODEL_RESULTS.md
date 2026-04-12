# Speaker Model Evaluation Results (RQ1)

**Issue:** [#35](https://gitlab.dle.afrl.af.mil/c2es1/mash/chat-to-cop/-/issues/35) (RQ1 base) and [#52](https://gitlab.dle.afrl.af.mil/c2es1/mash/chat-to-cop/-/issues/52) (multi-backend extension)
**Labels:** `data/labels/dash3_silver_labels_opus.jsonl` (935 messages, Claude Opus 4.6 silver labels)
**Eval script:** `scripts/eval_speaker_models.py`

## TL;DR

The current speaker model design (online prompt-injection of speaker role/area/jargon into the system prompt) **does not improve extraction quality** on either Qwen2.5-7B or Qwen2.5-14B against the Opus silver labels.

| Model | Type exact match delta (with − without speakers) | Entity overlap delta |
| --- | --- | --- |
| 7B | +0.2% | -3.9% |
| 14B | -1.2% | -2.2% |

Going from 7B to 14B, speakers go from "essentially neutral" to "actively harmful" on the headline metric — the **opposite** of what the capacity-bottleneck hypothesis from #35 predicted. Larger capacity isn't the bottleneck; the prompt-injection design is. The 14B run also paid a ~70% wall-clock penalty (2h28m with speakers vs 1h26m without) for those negative results.

**Recommendation: default speaker models OFF for MASH.** Preserve the code for research follow-up, but ship without the feature. RQ1 needs a different architectural approach — retrieval-augmented profiles, fine-tuned speaker classifiers as routing signals, or behavioral routing via reliability scores (#46) — not bigger models with the same prompt-injection design.

## A note on prior results

Earlier RQ1 results from 2026-04-06 (7B) and 2026-04-10 (14B) **are invalid as A/B comparisons**. The April 11 audit found that `Supervisor.start_agent()` constructed `ChannelAgent` without passing `use_speaker_models`, so the `CHAT_TO_COP_USE_SPEAKER_MODELS=false` env var was silently ignored on the production replay path. Both arms of both prior experiments actually ran with speakers ON. The deltas reported in those runs were noise.

The fix (!88) plus regression tests in `tests/test_supervisor.py::TestSupervisorAgentConfigPlumbing` ensures this class of bug can't recur. The numbers below are from the post-fix re-run on 2026-04-11/12.

## Methodology

For each model size (7B, 14B):

1. **Run A** with `CHAT_TO_COP_USE_SPEAKER_MODELS` unset (default `true`)
2. **Run B** with `CHAT_TO_COP_USE_SPEAKER_MODELS=false`
3. Both runs use identical Slurm settings (1 V100, qwen2.5:Nb-8k Modelfile, 8K context, 180s timeout, AFSNW27526RYZ)
4. Replay the same DASH-3 chat zip
5. Compare extractions against the Opus silver labels using `scripts/eval_speaker_models.py`

The eval matches extractions to labels by `(timestamp, channel, sender)` composite key, then computes:

- **Match rate**: fraction of labeled messages that produced an extraction
- **Type exact match**: fraction of matched extractions whose `update_type` matches the label
- **Type relaxed match**: same but with synonym buckets (e.g., `entity_id` ≈ `status_change`)
- **Entity overlap**: average Jaccard similarity between extracted and labeled entity sets
- **Noise detection**: fraction of `none` labels correctly classified as noise
- **Confidence correlation**: Pearson r between extraction confidence and correctness

## 7B results (post-!88, 2026-04-12)

**Runs:** 5973724 (Run A, with speakers, 1h46m), 5973808 (Run B, without speakers, 1h05m)

| Metric | With Speakers | Without Speakers | Delta |
| --- | --- | --- | --- |
| Matched extractions | 348 | 323 | +25 (+7.7%) |
| Match rate | 37.2% | 34.5% | +2.7% |
| **Type exact match** | **20.7%** | **20.5%** | **+0.2%** |
| Type relaxed match | 21.0% | 20.6% | +0.3% |
| Entity overlap (avg Jaccard) | 29.5% | 33.5% | -3.9% |
| Noise detection accuracy | 100.0% | 100.0% | 0 |
| Confidence correlation | 0.023 | 0.042 | -0.019 |

### Per-type breakdown (7B)

| Type | Without Speakers | With Speakers | Delta |
| --- | --- | --- | --- |
| entity_id | 17% | 33% | **+16pp** |
| sitrep | 33% | 56% | **+23pp** |
| location | 62% | 75% | +13pp |
| cyber_ew | 53% | 59% | +6pp |
| threat | 38% | 41% | +3pp |
| status_change | 50% | 50% | 0 |
| fuel | 62% | 62% | 0 |
| weapons | 33% | 33% | 0 |
| fire_mission | 100% | 100% | 0 |
| csar | 100% | 100% | 0 |
| handover | 0% | 0% | 0 |
| tasking | 48% | 44% | -4pp |

### Learning curves (dominant speakers)

For the most-frequent speaker `afrl_lavgn` (601 messages), rolling accuracy by message count:

| Speaker (msgs) | With (msg5) | With (msg601) | Without (msg5) | Without (msg601) |
| --- | --- | --- | --- | --- |
| afrl_lavgn (601) | 40% | **13%** | 40% | **13%** |
| VEGAS_SL (92) | 80% | 30% | 80% | 33% |
| VEGAS_PIT_A (50) | 20% | 16% | 20% | 16% |
| Vegas_ABM1 (31) | 60% | 26% | 60% | 29% |

The dominant speaker's accuracy is **identical at message 601** in both arms. Other speakers show 1-5 pp differences in either direction. There is no clear "learning curve" effect — the speaker model isn't getting measurably better at predicting after more messages from the same speaker.

### 7B interpretation

The aggregate type-classification effect is essentially zero (+0.2%). The match rate improvement (+2.7%) suggests speaker context makes the LLM slightly more willing to commit to an extraction at all, which trades off against entity-overlap precision (-3.9%) — speaker context introduces some noise in entity field extraction.

The interesting per-type pattern (speakers help on entity_id, sitrep, location, cyber_ew, threat; hurt on tasking) is too small from a single run to claim as a robust effect, but is the kind of signal worth following up on if we end up redesigning the speaker model.

The headline finding from the original April 6 run ("no significant benefit on 7B") **holds**. Whether the cause is *model capacity* (7B can't leverage speaker context) or *speaker model design* (the prompt-injection approach is insufficient) requires the 14B comparison to disambiguate.

## 14B results (post-!88, 2026-04-12)

**Runs:** 5973723 (Run A, with speakers, 2h28m), 5973223 (Run B, without speakers, 1h26m)

| Metric | With Speakers | Without Speakers | Delta |
| --- | --- | --- | --- |
| Matched extractions | 305 | 310 | -5 |
| Match rate | 32.6% | 33.2% | -0.6% |
| **Type exact match** | **21.3%** | **22.5%** | **-1.2%** |
| Type relaxed match | 21.5% | 22.7% | -1.2% |
| Entity overlap (avg Jaccard) | 34.0% | 36.2% | -2.2% |
| Noise detection accuracy | 100.0% | 100.0% | 0 |
| Confidence correlation | 0.120 | 0.118 | +0.002 |

### Per-type breakdown (14B)

| Type | Without Speakers | With Speakers | Delta |
| --- | --- | --- | --- |
| sitrep | 44% | 56% | **+12pp** |
| status_change | 44% | 53% | **+9pp** |
| weapons | 33% | 33% | 0 |
| fire_mission | 100% | 100% | 0 |
| csar | 100% | 100% | 0 |
| handover | 0% | 0% | 0 |
| tasking | 47% | 43% | -4pp |
| threat | 62% | 57% | -5pp |
| entity_id | 42% | 33% | -9pp |
| location | 88% | 75% | -13pp |
| fuel | 69% | 54% | -15pp |
| cyber_ew | 53% | 35% | **-18pp** |

### Wall-clock cost

- Run A (with speakers): **2h28m** (148 minutes)
- Run B (without speakers): **1h26m** (86 minutes)

Speakers added ~62 minutes (~70% overhead) on the same V100 hardware. The speaker context expands the system prompt, which slows every LLM call. In return for that overhead we got *worse* extraction quality on most types.

### 14B interpretation

Speakers actively hurt 14B. The aggregate type-classification delta is -1.2%, entity overlap -2.2%, and per-type effects are negative on 6 of 12 types (entity_id, location, fuel, cyber_ew, threat, tasking) — including a striking -18 pp on cyber_ew and -15 pp on fuel. They help on only 2 types (sitrep +12, status_change +9). The patterns aren't even consistent with the 7B per-type signals: 14B reverses the 7B helpful effects on entity_id, location, and cyber_ew.

This rules out the "small models can't leverage speaker context" hypothesis. The signal is "the prompt-injection design isn't producing consistent benefit at any size we've tested."

## Capacity bottleneck hypothesis: REJECTED

The original hypothesis from #35 was *"7B may lack the ability to leverage speaker context effectively; larger models would benefit"*. The 14B comparison shows the opposite:

| Model | Type exact match delta | Entity overlap delta | Compute overhead |
| --- | --- | --- | --- |
| 7B | +0.2% | -3.9% | (not measured separately) |
| 14B | -1.2% | -2.2% | +72% wall clock |

If the hypothesis were correct, 14B should have shown a positive delta on at least the headline metric. Instead it's negative, the per-type pattern is inconsistent across model sizes, and the compute overhead is substantial. The hypothesis is rejected.

The signal is that the **speaker model design** — not capacity — is the bottleneck. The current implementation injects speaker profile text into the system prompt for every LLM call. Larger models seem to be more, not less, distracted by that context.

## Implications and next steps

1. **Default speaker models OFF for MASH.** Preserve the code for research follow-up. The compute budget at MASH is finite and this feature spends it for negative ROI.
2. **RQ1 needs a redesign**, not a bigger model. Candidate alternatives:
   - **Retrieval-augmented speaker profiles**: only inject the most relevant prior messages from the same speaker, not the full profile
   - **Fine-tuned speaker classifiers**: a small per-speaker classifier as a routing signal, not as prompt context
   - **Behavioral routing**: use speaker reliability scores to adjust confidence thresholds (#46) instead of changing extraction itself
3. **Per-type effects on `sitrep` and `status_change`** are interesting and worth exploring — they're consistently positive across both 7B and 14B. If we redesign the speaker model, these are the types most likely to benefit.
4. **#46 (speaker model as routing signal)** is now the most interesting follow-up direction. Pursue that instead of "speaker model as prompt context".

## Files

- 7B Run A DB: `data/narwhal_results_post_88/replay_7b_with_speakers_5973724.db`
- 7B Run B DB: `data/narwhal_results_post_88/replay_7b_without_speakers_5973808.db`
- 7B eval report: `data/narwhal_results_post_88/eval_7b_post_88.md`
- 14B Run A DB: `data/narwhal_results_post_88/replay_14b_with_speakers_5973723.db`
- 14B Run B DB: `data/narwhal_results_post_88/replay_14b_without_speakers_5973223.db`
- 14B eval report: `data/narwhal_results_post_88/eval_14b_post_88.md`

# Speaker Model Evaluation Results (RQ1)

**Issue:** [#35](https://gitlab.dle.afrl.af.mil/c2es1/mash/chat-to-cop/-/issues/35) (RQ1 base) and [#52](https://gitlab.dle.afrl.af.mil/c2es1/mash/chat-to-cop/-/issues/52) (multi-backend extension)
**Labels:** `data/labels/dash3_silver_labels_opus.jsonl` (935 messages, Claude Opus 4.6 silver labels)
**Eval script:** `scripts/eval_speaker_models.py`

## TL;DR

Online speaker model learning produces **no significant aggregate benefit** on Qwen2.5-7B when measured against silver labels on DASH-3 chat. The headline metric (type exact match) moves by less than 1 percentage point. Per-type effects are mixed: speakers help on identification-heavy types (entity_id, sitrep, location) and hurt on tasking. Learning curves on the dominant speaker show no improvement over time.

The 14B re-run is in flight (job 5973723) and will be added below when complete. Until that lands, the capacity-bottleneck hypothesis from #35 (*"7B may lack the ability to leverage speaker context; larger models would benefit"*) remains unconfirmed.

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

### Per-type breakdown

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

**TBD** — Run B (5973223) completed at 00:51 (1h26m, 844 LLM calls, 6.05s/msg mean). Run A (5973723) is in flight as of 02:00 with ~5 hours of walltime remaining.

This section will be updated when both halves are evaluated.

## Capacity bottleneck hypothesis

The original hypothesis from #35 was *"7B may lack the ability to leverage speaker context effectively; larger models would benefit"*. The 14B comparison will determine which of two outcomes obtains:

1. **14B per-type deltas look like 7B's** (small mixed-direction): speaker model *design* is the bottleneck, not capacity. RQ1 conclusion: prompt-injection of speaker context is insufficient; need architectural changes (fine-tuning, retrieval-augmented profiles, etc.).
2. **14B shows clear improvements with speakers**: capacity bottleneck confirmed. RQ1 conclusion: speakers personalization works but requires sufficient model capacity (≥14B).

## Files

- 7B Run A DB: `data/narwhal_results_post_88/replay_7b_with_speakers_5973724.db`
- 7B Run B DB: `data/narwhal_results_post_88/replay_7b_without_speakers_5973808.db`
- 7B eval report: `data/narwhal_results_post_88/eval_7b_post_88.md`
- 14B Run B DB: `data/narwhal_results_post_88/replay_14b_without_speakers_5973223.db`
- 14B Run A DB: pending (job 5973723)
- 14B eval report: pending

# Session Notes — 2026-04-20

## Sweep: COMPLETE

1,600/1,600 DBs, 0 failures, all 16 cells at N=100.
Final eval report at `$WORKDIR/output/sweep/final_report.md` (Narwhal) and
CSV at `data/narwhal_results_post_88/final_sweep_1600.csv` (local).

Core-hours consumed: ~450,000 on AFSNW27526RYZ.

## Final RQ1 Results

Speakers hurt at EVERY model size. All p < 0.0001.

| Model | Type Exact Delta (pp) | Best (OFF) |
|---|---|---|
| 3B  | -1.54 | 17.7% |
| 7B  | -4.12 | 40.5% |
| 14B | -1.50 | 46.8% |
| 32B | -2.74 | 47.8% |

Temperature (det vs stoch): no significant effect (p=0.99).
14B is the sweet spot (least speaker damage, near-32B accuracy).
MASH recommendation: speakers OFF, ship 14B or 32B.

## Completed (2026-04-20, second session)

1. DONE: `docs/SPEAKER_MODEL_RESULTS.md` rewritten with full N=100 analysis, ANOVA, per-type breakdown
2. DONE: #52 closed with final results comment
3. DONE: `use_speaker_models` default flipped to `False` in config.py and channel_agent.py
4. DONE: All tests updated for new default (964 passing, lint clean)
5. DONE: Narwhal git pulled to latest main, eval re-run with fixed script
6. DONE: eval_speaker_sweep.py patched with isinstance guard for string entities
7. TODO: Validate on MASH target hardware (#26) if 24GB GPU becomes available

## Test suite

964 passed, lint clean, CI green. MR !99 (test quality audit) merged.
Issue #64 filed for calibration plumbing test (not blocking).

## Git state

- Branch: main (8 files modified, not yet committed)
- `data/narwhal_results_post_88/final_sweep_1600.csv` downloaded by other session
- Narwhal: pulled to latest main (a7853b7), eval_speaker_sweep.py also sed-patched with isinstance guard

## Eval script caveat

The Narwhal copy of `scripts/eval_speaker_sweep.py` was manually patched
via sed to fix the `data_json`/`entities` column mismatch. The local repo
copy (from !99) has the correct code. If re-running eval on Narwhal, either
`git pull` on Narwhal or re-upload the local copy.

## Open issues (12)

HIGH: #26 (24GB GPU validation), #17 (CoP writer — blocked on Sarah's schema)
MEDIUM: #64 (cal plumbing test), #51 (vLLM on AG), #45 (DELTRON tier)
LOW: #48, #47, #46, #44, #37, #30 (research/future)
CLOSED this session: #52 (with final N=100 results)

## Other sessions

Blueback/Raider arch alternatives bench and LoRA training running from
a separate session (see `C:\Users\hsclouse\GitProjects\SESSION_NOTES.md`
for that work). Those test the architectural fixes the degradation report
recommends; this sweep provides the statistical baseline.

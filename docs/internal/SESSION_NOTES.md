# Session Notes -- 2026-04-22 (Issue Queue: #75, #74, #73, #71, #72)

## Current State

Five issues worked across two sessions (started 2026-04-22). Three merged,
two MRs open with CI running:

| Issue | MR | Status |
|-------|----|--------|
| #75 hard timeout on extract() | !119 | Merged |
| #74 num_ctx port-fragile detection | !120 | Merged |
| #73 prompt-regression harness | !121 | Merged |
| #71 adapt-schema CLI | !124 | Open, CI running |
| #72 hot-reload mapping | !123 | Open, CI running |

!122 was closed and superseded by !124 after discovering a stash-pop
conflict had reverted openai_compat.py on the original branch.

## What Changed This Session

### Merged to main

- `src/chat_to_cop/backend/openai_compat.py` -- `hard_timeout` param (#75),
  `is_ollama` flag + `_detect_ollama()` helper (#74)
- `src/chat_to_cop/config.py` -- `llm_extract_hard_timeout`, `llm_is_ollama`,
  `fallback_extract_hard_timeout`, `fallback_is_ollama` fields
- `src/chat_to_cop/replay.py` -- plumb hard_timeout and is_ollama to backends
- `tests/test_openai_backend.py` -- TestHardTimeout (4 tests), TestNumCtxDetection (7 tests)
- `tests/test_config.py` -- config plumbing tests for hard_timeout + is_ollama
- `tests/test_prompt_budget.py` -- CI guard: system prompt under 17,500 chars
- `scripts/replay_bench.py` -- stratified replay benchmark with type_accuracy,
  entity_overlap_jaccard, none_false_positive_rate, latency metrics
- `scripts/blueback_replay_bench.sh` -- Slurm sbatch wrapper for Blueback
- `docs/RUNBOOK.md` -- procedure for running replay bench pre-merge

### Open MRs

- `src/chat_to_cop/cli/adapt_schema.py` -- schema ingestion CLI (!124)
- `src/chat_to_cop/cli/__init__.py`, `__main__.py` -- CLI entry points
- `tests/test_adapt_schema.py` -- 11 tests for adapt-schema
- `src/chat_to_cop/output/schema_adapter.py` -- HotReloadAdapter class (!123)
- `tests/test_schema_adapter.py` -- 5 new + 2 updated hot-reload tests

## Untracked Files

- `SourceCode.pdf` -- generated artifact, not committed
- `scripts/texify_source.py` -- one-off script, not committed

## What to Do Next

1. Merge !124 and !123 once CI is green
2. Consider bumping version to 0.1.5 after both merge
3. The stash-conflict revert issue suggests adding a CI check that diffs
   feature branches against main for accidental file reversions -- low
   priority but worth noting

## Previous Session (2026-04-21)

AG CI build pipeline. v0.1.4 release pipeline green. HPC image built
locally and pushed to DLE registry. `build-hpc-on-ag` CI job written.

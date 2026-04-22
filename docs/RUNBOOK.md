# Runbook

Operational procedures for chat-to-cop development and deployment.

## Running the replay bench before merging a prompt-touching MR

Any MR that modifies `build_system_prompt`, `DEFAULT_GLOSSARY`, few-shot
examples, or the `CoPUpdate` / `EntityUpdate` schemas should include a
pre/post replay bench comparison in the MR description.

### Quick start (local)

```bash
# Before your changes (on main):
python scripts/replay_bench.py --model qwen2.5:7b --n 100
# Note the output path: results/replay_bench_qwen2.5_7b_<ts>.json

# After your changes (on your branch):
python scripts/replay_bench.py --model qwen2.5:7b --n 100

# Diff the two result files and paste into the MR:
diff results/replay_bench_qwen2.5_7b_<before>.json results/replay_bench_qwen2.5_7b_<after>.json
```

### On Blueback (HPC)

```bash
sbatch scripts/blueback_replay_bench.sh
# Or with overrides:
MODEL=qwen3:30b-a3b N=50 sbatch scripts/blueback_replay_bench.sh
```

### What to report

Include these metrics in the MR description (before vs after):

| Metric | Before | After | Delta |
|--------|--------|-------|-------|
| type_accuracy | | | |
| entity_overlap_jaccard | | | |
| none_false_positive_rate | | | |
| mean_latency_ms | | | |
| prompt_approx_tokens | | | |

### When to raise a flag

- type_accuracy drops more than 2pp: the prompt change hurt extraction quality
- none_false_positive_rate increases more than 5pp: the change makes the model
  over-classify messages as noise
- prompt_approx_tokens grows past the CI ceiling (17,500 chars / ~4,375 tokens):
  the `test_prompt_budget.py` CI test will fail; raise PROMPT_CHAR_CEILING and
  explain the trade-off

### CI token-budget guard

`tests/test_prompt_budget.py` asserts the system prompt stays under 17,500
chars (~4,375 tokens at 4 chars/token). If your change pushes past this
ceiling, bump `PROMPT_CHAR_CEILING` in the test file and document the
trade-off in the MR.

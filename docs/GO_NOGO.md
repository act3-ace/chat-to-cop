# MASH Go / No-Go: Configuration, Decision Tree, and Recovery

Single source of truth for MASH operations (May 2026). Covers production
configuration defaults, flip criteria, the operational decision tree, recovery
procedures, and sign-off. If anything in this document conflicts with another
doc, this document wins. The deployer reads this on the morning of the event
before doing anything else.

---

## Hard rules

Non-negotiable. The deployer enforces them.

1. **No new dependencies after Friday before MASH.** Any integration that doesn't work by end of day Friday is shipped *without* for the event. No exceptions.
2. **DELTRON is treated as a degraded backend, not a hard dependency.** If DELTRON's tier signal is missing, chat-to-cop processes the message anyway with no tier filter. The DELTRON dependency is *additive*, never blocking. (See #45.)
3. **No code changes in the 24 hours before MASH** unless they fix a critical bug. A "critical bug" is something that would prevent the system from running, not something that would make it slightly better.
4. **One person is "the deployer" for the duration of the event.** Decisions get made by that person, in real time, with no committee. Default: Hamilton. Backup: Mia.
5. **The deployer's laptop is the only deployment target.** No remote deploys during the event. Everything runs on the same physical box.
6. **The kill switch is the first action under any uncertainty.** When in doubt, pause writes (#56), then think.

---

## Production configuration defaults

These values are the MASH defaults unless a flip is approved through the
sign-off process at the bottom of this document. Environment variable names map
to `config.py` (`PipelineConfig` and its nested configs via pydantic-settings).

| Setting | Value | Env var | Rationale |
|---------|-------|---------|-----------|
| Speaker models | OFF | `CHAT_TO_COP_USE_SPEAKER_MODELS=false` | RQ1 N=100 sweep: speakers hurt Type Exact on all four model sizes (p<0.0001) |
| Primary model | `qwen2.5:7b` (pinned) | `CHAT_TO_COP_LLM_MODEL=qwen2.5:7b` | Fastest viable model; latency is #1 priority. 14B available as upgrade if GPU headroom allows |
| Context window | 8192 | `CHAT_TO_COP_LLM_NUM_CTX=8192` | Minimum required for full system prompt + conversation window |
| Fallback model | `qwen2.5:3b` | `CHAT_TO_COP_FALLBACK_MODEL=qwen2.5:3b` | Lightweight fallback when primary circuit breaker opens |
| Auto-write threshold | 0.85 | `CHAT_TO_COP_COP_AUTO_THRESHOLD=0.85` | Lowered from 0.95 for MASH (#98): 0.95 silently filtered valid extractions in 0.85-0.94 range |
| Flag threshold | 0.50 | `CHAT_TO_COP_COP_FLAG_THRESHOLD=0.5` | Below this -> HUMAN review; mid-range confidence bucket is essentially empty on Qwen2.5 |
| High-risk types | weapons, csar, fire_mission, cyber_ew | `CHAT_TO_COP_COP_HIGH_RISK_TYPES` | Always routed to human review regardless of confidence |
| CoP writer mode | dry-run | `CHAT_TO_COP_COP_API_URL=""` | No external writes until Sarah Bowman confirms schema; flip to real endpoint on-site |
| LLM timeout | 120s | `CHAT_TO_COP_LLM_TIMEOUT=120.0` | Generous for cold starts; reduce to 30s once model is warm |
| Circuit breaker | 3 failures / 30s cooldown | `CHAT_TO_COP_CIRCUIT_FAIL_MAX=3`, `CHAT_TO_COP_CIRCUIT_COOLDOWN=30` | Standard degrading backend settings |

### Minimal .env file for MASH

```bash
CHAT_TO_COP_LLM_URL=http://127.0.0.1:11434/v1
CHAT_TO_COP_LLM_MODEL=qwen2.5:7b
CHAT_TO_COP_LLM_NUM_CTX=8192
CHAT_TO_COP_USE_SPEAKER_MODELS=false
CHAT_TO_COP_DB_PATH=data/mash_live.db
# Set when Sarah confirms the endpoint:
# CHAT_TO_COP_COP_API_URL=http://COP_SERVER:PORT/api/updates
```

---

## Flip criteria

### Speaker model flip

The following criteria come from the speaker profile degradation report
(`research/SPEAKER_MODEL_RESULTS.md`, section "Decision: speakers OFF for
MASH"). All must be satisfied before speaker models are turned on for any
production run at MASH.

> Keep speakers OFF unless a redesigned approach achieves (a) non-inferior
> Type Exact AND (b) a measurable reduction in false positives on
> "none/noise" messages, measured on a human-audited subset.

**Required evidence to justify a flip:**

1. **Non-inferior Type Exact.** The proposed speaker approach must produce Type Exact accuracy not statistically worse than the speakers-OFF baseline for the same model, at alpha=0.05 (one-sided non-inferiority test). The 7B baseline is the reference (see `research/SPEAKER_MODEL_RESULTS.md`).
2. **Reduced false positives on none/noise messages.** Measurable reduction in false positive extractions on messages labeled `none`. "Measurable" means the point estimate is negative and the 95% CI excludes zero.
3. **Minimum sample size.** N>=50 independent replications per cell (speakers ON vs OFF), using the same eval harness and silver labels as the N=100 sweep (`scripts/eval_speaker_sweep.py`, `data/labels/dash3_silver_labels_opus.jsonl`).
4. **Human-audited subset.** At least 100 messages independently audited by a human (not the person who built the approach) to confirm silver label quality on the message types where improvement is claimed.
5. **Compute budget.** Per-message latency must not increase by more than 50% relative to the speakers-OFF baseline on the same hardware.

**What counts as a "redesigned approach":**

The N=100 results ruled out the current prompt-injection mechanism. Redesigned approaches that would need to pass the criteria above:

- Selective retrieval (relevant prior messages instead of full profile)
- Two-stage extraction (extract first, then adjust with speaker context)
- Per-type routing (speakers only for types where they help)
- LoRA fine-tuning (speaker knowledge in weights, not prompt tokens)
- Behavioral routing (reliability scores adjust confidence thresholds only)

Re-running the existing mechanism with a newer or larger model does not count.

### Model flip

The default model is `qwen2.5:7b`. To change the production model:

1. Run the full eval sweep (`scripts/eval_speaker_sweep.py`) on the candidate model with N>=50 replications, speakers OFF.
2. The candidate must meet or exceed the 7B baseline on Type Exact with non-overlapping 95% CIs, or demonstrate a compelling latency improvement (>2x faster) with no more than 2pp Type Exact loss. 14B is available where GPU headroom allows, but the 7B default prioritizes latency ("70% is great").
3. Document results in `research/SPEAKER_MODEL_RESULTS.md` or a new benchmark report and update this section.

### Confidence threshold flip

The auto-write (0.85) and flag (0.50) thresholds balance precision against the operational cost of a silent dashboard. The auto-write was lowered from 0.95 to 0.85 for MASH (#98) after Mia observed the system appearing dead during exercise traffic. To change them:

1. Run `scripts/calibration_analysis.py` on the production model and produce an updated calibration curve.
2. The auto-write threshold should correspond to the calibrated confidence level where precision exceeds 90% on the silver labels.
3. If a `CalibrationModel` JSON is loaded (`CHAT_TO_COP_CALIBRATION_MODEL`), thresholds operate on calibrated confidence, so a lower raw threshold may be appropriate. Document which mode (raw vs calibrated) applies.

---

## Pre-event checklist (T-0 = morning of MASH)

The deployer runs through this checklist on the morning of the event, in order.
Every checkbox must be checked before starting the day. If any item fails, see
"Recovery procedures" below before proceeding.

### Hardware and connectivity

- [ ] Laptop physically present, AC plugged in, fully charged
- [ ] Two power outlets verified (one for laptop, one as a spare)
- [ ] Internet connectivity tested by loading two unrelated URLs
- [ ] AG GovCloud accessible (`ssh ag-node` per ag-helpers -- *if* AG is in the deployment plan)
- [ ] Cloud API key environment variables set: `GOOGLE_API_KEY`, `ANTHROPIC_API_KEY`
- [ ] Local Ollama running with the demo model pulled and warm

### chat-to-cop pipeline

- [ ] On `main` branch, working tree clean: `git status`
- [ ] Pulled latest: `git pull`
- [ ] Tests passing: `pytest tests/ -k "not integration" -q`
- [ ] Lint clean: `ruff check src/ tests/ scripts/`
- [ ] Smoke test passes against the chosen backend: `python scripts/quick_test.py --url <endpoint> --model <model>`
- [ ] HYDRO_SL SITREP message in the smoke test produces 4 entity DESTROYED extractions
- [ ] Kill switch verified working (REST endpoint returns 200, then resume)
- [ ] CoP writer dry-run mode verified (writes to local SQLite, not external CoP)

### Backends in priority order

For each backend you intend to use at the event, verify it works *today*:

- [ ] **Primary:** Gemini 2.5 Flash via `quick_test.py --url ... --model gemini-2.5-flash` -- verified working with 5 demo messages
- [ ] **Backup 1:** Anthropic Claude (Haiku or Sonnet) via `--anthropic --model ...`
- [ ] **Backup 2:** Local Ollama with Qwen 7B-8K via `./scripts/demo.sh`
- [ ] **Backup 3:** AG vLLM (if #51 is done) -- *currently aspirational*
- [ ] **Last-resort fallback:** the comparison doc + slide deck. If everything fails, *show*, don't *demo*.

### Data and labels

- [ ] DASH-3 chat data accessible at `data/chat/Dash3-GBC/Data/23Sep/usaf/chat.zip`
- [ ] Opus silver labels accessible at `data/labels/dash3_silver_labels_opus.jsonl`
- [ ] Calibration model loaded if #58 is done: `data/calibration/qwen2.5_7b-8k.json`

### Schema and CoP integration

- [ ] CoP database schema status confirmed with Sarah Bowman (Teams DM the morning of)
- [ ] If schema is final: CoP writer pointed at the real CoP API endpoint
- [ ] If schema is draft: CoP writer pointed at local SQLite, with the schema fields we have today
- [ ] If schema is missing: CoP writer in *dry-run mode*, writes only to local SQLite, never attempts external CoP

### Operator surface

- [ ] Dashboard accessible at `localhost:8000` (or wherever the API serves)
- [ ] Pause-writes button visible and functional (#56)
- [ ] Override button visible on each update row (#59)
- [ ] Confidence shown as HIGH/MEDIUM/LOW, not decimals (#55)
- [ ] Status badge GREEN

### Logging and provenance

- [ ] Logs flowing to disk at the configured path
- [ ] Pipeline run tracker active (`PipelineTracker` started)
- [ ] Override audit log empty and writable (#59)
- [ ] Adversarial test cases verified passing (#60)

---

## Decision matrix

Use this to decide what to do at T-0 based on the actual state of each
component.

| Symbol | Meaning |
|---|---|
| Y | Verified working in the pre-event checklist |
| N | Verified broken or unavailable |
| ? | Status unknown or untested today |

| chat-to-cop | DELTRON | CoP schema | Internet | Action |
|---|---|---|---|---|
| Y | Y | Final (real CoP) | Y | **GO.** Deploy joint pipeline writing to real CoP. Best case. |
| Y | Y | Final (real CoP) | N | **GO with degraded backend.** Local Ollama only. Joint pipeline. CoP writes to real CoP via local network if available, else queue. |
| Y | Y | Draft | Y | **GO with local SQLite.** Joint pipeline. Sarah has confirmed the schema fields we're using are stable. Reconcile after the event. |
| Y | Y | Missing | Y | **GO in demo mode.** Joint pipeline. *No* external CoP writes. Local SQLite only. Show the structured output to operators directly. |
| Y | N | Final | Y | **GO with chat-to-cop standalone.** No tier filter. Process every message. We still produce structured CoP updates. |
| Y | N | Draft | Y | **GO standalone with local SQLite.** Same as above plus the SQLite scope reduction. |
| Y | N | Missing | Y | **GO in demo mode standalone.** Same as above plus no external writes. |
| Y | N | any | N | **GO standalone, local Ollama, local SQLite.** Most degraded GO state. Still produces useful CoP output. |
| N | Y | any | Y | **NO-GO for chat-to-cop.** Try to recover (see procedures below). If not recovered in 30 min, demo *DELTRON* alone and present the chat-to-cop slides. |
| N | N | any | Y | **NO-GO for the live system.** Show the slides. Run the smoke test from `quick_test.py` against Gemini Flash if internet works. Talk through the Opus silver label results. |
| N | N | any | N | **NO-GO entirely.** Show the slides. Walk through the architecture. Discuss results from the prior runs. The work doesn't disappear because the demo failed. |

**When in doubt:** the deployer's default is the most degraded GO state that
still produces useful output (chat-to-cop standalone, local Ollama, local
SQLite, no external CoP writes). This is one command:

```powershell
python -m chat_to_cop.replay data/chat/Dash3-GBC/Data/23Sep/usaf/chat.zip --num-ctx 8192
```

---

## Command sequence at T-0

The actual sequence the deployer runs at T-0 in the best-case scenario. See
[MASH_DEPLOYMENT.md](MASH_DEPLOYMENT.md) for full deployment options and
troubleshooting.

```powershell
# Step 1: Verify state
cd C:\Users\user\GitProjects\act3\chat-to-cop
git status                          # Should be clean, on main
git pull                            # Should be no-op
ruff check src/ tests/ scripts/     # Should pass
pytest tests/ -k "not integration" -q  # Should be 843+ passing

# Step 2: Smoke test the chosen backend
python scripts/quick_test.py --url https://generativelanguage.googleapis.com/v1beta/openai/ --model gemini-2.5-flash

# Step 3: Verify the smoke test produced 4 entity DESTROYED extractions
# If yes: proceed to step 4
# If no: abandon Gemini, try Anthropic, then local Ollama

# Step 4: Start the pipeline against the live IRC server
# (This command will be different for the live event vs replay mode)
python -m chat_to_cop.replay data/chat/Dash3-GBC/Data/23Sep/usaf/chat.zip --num-ctx 8192

# Step 5: Open the operator dashboard in a browser
start http://localhost:8000
```

If step 2 produces the expected output, every other command is mechanical. If
it doesn't, fall through to the recovery procedures.

---

## Recovery procedures

Each procedure is designed to take less than 5 minutes. If a procedure takes
longer than 5 minutes, abandon it and fall back to the next column in the
decision matrix.

### Ollama not responding

**Symptom:** `quick_test.py` against the local Ollama returns connection errors, or `curl http://127.0.0.1:11434/api/tags` fails.

1. `taskkill /F /IM ollama.exe` (Windows) or `pkill ollama` (Unix)
2. Wait 5 seconds
3. `ollama serve &` (or just `ollama serve` in a new terminal)
4. Re-run the smoke test
5. If still failing: switch backend to Gemini Flash via `--url https://generativelanguage.googleapis.com/v1beta/openai/ --model gemini-2.5-flash`

### Cloud API rate-limited

**Symptom:** `quick_test.py` against Gemini Flash returns 429 or rate-limit errors.

1. Switch to Anthropic: `python scripts/quick_test.py --anthropic --model claude-haiku-4-5-20251001`
2. If Anthropic also limits: switch to local Ollama
3. Wait 5 minutes before retrying the original API
4. Do not keep retrying the same API in a loop -- that makes the rate limit worse

### Pipeline crash mid-replay

**Symptom:** `replay.py` exits with an exception during a long-running replay.

1. Don't restart from scratch. The replay is idempotent on the database -- re-running with the same `--db` path will skip already-processed messages.
2. Read the last log line to understand what failed.
3. If it's a backend timeout: increase `--timeout` and re-run
4. If it's a Pydantic validation error: check `data/labels/` for any malformed labels, or check the latest message that failed and add a guard
5. If it's a memory error: drop to a smaller model

### Network down

**Symptom:** No internet, cloud APIs unreachable.

1. Switch to local Ollama: `./scripts/demo.sh`
2. Verify Ollama has a model pulled: `ollama list` should show at least `qwen2.5:7b-8k`
3. If Ollama doesn't have the model, you can't recover during the event -- fall back to demo mode
4. Inform the deployer: this is a known fallback path and the system is designed for it

### Laptop GPU saturated

**Symptom:** Inference is dramatically slower than expected; `nvidia-smi` shows 100% GPU.

1. Drop to a smaller model: `ollama pull qwen2.5:3b` then re-run with `--model qwen2.5:3b`
2. Or drop to CPU mode: `OLLAMA_NUM_GPU=0 ollama serve`
3. If the GPU is saturated by *another process* (not Ollama), kill that process or move chat-to-cop to a different backend (cloud API)

### CoP writer failing

**Symptom:** CoPWriter throws errors writing to the configured CoP endpoint.

1. Flip to local-SQLite-only mode: set `CHAT_TO_COP_COP_WRITER_DRY_RUN=true` in env
2. Pipeline continues, all updates land in the local SQLite database
3. Updates can be replayed to the real CoP after the event
4. Do not try to "fix" the CoP API connection during the event -- that's a post-event problem

### Sarah's schema changes mid-event

**Symptom:** Sarah pings Teams to say the schema fields you're writing to don't match what she has now.

1. Acknowledge but do not change the production code mid-event (hard rule #3)
2. Continue writing to local SQLite with the old fields
3. After the event: write a one-shot translation script that maps the old fields to the new ones, then bulk-import
4. Document the change in the post-event notes

### "I have no idea what's wrong"

**Symptom:** Something is broken and the symptoms don't match any of the above.

1. Hit the kill switch (#56). Pause writes.
2. Take a screenshot of the dashboard.
3. `git status`, `git log --oneline -5`, save them to a file.
4. Snapshot the latest log file.
5. Switch to the most degraded GO state in the decision matrix (chat-to-cop standalone, local Ollama, local SQLite).
6. After the event, we will analyze the snapshot.
7. Do not try to debug live in front of operators.

---

## Catastrophic failure modes

| Severity | Failure | Mitigation |
|---|---|---|
| **Catastrophic** | A wrong CoP update causes an operator to act on bad information | Tiered write authority (#58), operator override (#59), kill switch (#56), audit log |
| **Catastrophic** | The system silently writes garbage to the real CoP database | Dry-run mode default; only enable real CoP writes after Sarah confirms; CoP writer respects `CHAT_TO_COP_COP_WRITER_DRY_RUN` env var |
| **Severe** | Pipeline crash causes complete blackout for >5 min during a critical event moment | Idempotent replay on the database; restart-from-where-it-died; degraded backends |
| **Severe** | Cloud API outage during demo | Three backup backends, all verified at T-0 |
| **Severe** | An operator types a prompt-injection attempt that lands as an extracted update | Adversarial robustness review (#60), prompt injection regex in channel agent |
| **Embarrassing** | The pipeline produces 0 updates because something subtle is wrong | Smoke test catches this at T-0 |
| **Embarrassing** | The dashboard shows decimal confidences nobody can interpret | Operator surface refresh (#55) |
| **Recoverable** | Local Ollama runs out of disk space | Free up `/tmp` or fall back to cloud |
| **Recoverable** | A new model release breaks instructor/Pydantic parsing | Pin the exact model version we tested with; never auto-upgrade |

---

## Post-event checklist

After MASH ends, the deployer:

- [ ] Snapshots all logs from the event
- [ ] Dumps the override audit log (#59) for analysis
- [ ] Captures the pipeline run tracker output
- [ ] Reports total updates produced, by tier, by channel, by speaker
- [ ] Identifies the messages with the most operator overrides -- these are our worst failures
- [ ] Reconciles local SQLite to the real CoP (if running in degraded mode)
- [ ] Drafts a one-page event summary for the team
- [ ] Updates this doc with anything we learned the hard way

---

## Sign-off

Both signatures are required before any flip is applied to the production
configuration at MASH. "Flip" means changing any value in the defaults table
to something other than the documented default.

| Role | Name | Signature | Date |
|------|------|-----------|------|
| Project lead | Hamilton Clouse | _________________ | ____-__-__ |
| Reviewer | _________________ | _________________ | ____-__-__ |

The reviewer should be someone who did not build the proposed change.
Mia Kollia, Colin Leong, or Jennifer Carlet are all acceptable reviewers.

### Sign-off process

1. The person proposing the flip opens a GitLab issue describing the
   change, the evidence collected, and how each criterion above is met.
2. The reviewer checks the evidence against the criteria in this document.
3. Both sign here (or in the MR that updates this document) before the
   flip is applied.
4. The MR updating the defaults in `config.py` and this document is
   merged to `main` before the event.

No flips are applied on the day of the event unless they fix a critical
bug (per hard rule #3).

---

## References

- [research/SPEAKER_MODEL_RESULTS.md](research/SPEAKER_MODEL_RESULTS.md) -- RQ1 N=100 sweep results, per-model and per-type breakdowns
- [research/BENCHMARK_RESULTS.md](research/BENCHMARK_RESULTS.md) -- Backend benchmark results
- [internal/COMPARISON_DELTRON](internal/COMPARISON_DELTRON) -- DELTRON comparison and integration notes
- [MASH_DEPLOYMENT.md](MASH_DEPLOYMENT.md) -- Deployment options, setup guide, troubleshooting
- `src/chat_to_cop/config.py` -- Canonical config definitions (pydantic-settings)
- `scripts/eval_speaker_sweep.py` -- Evaluation harness used for the N=100 sweep
- `scripts/calibration_analysis.py` -- Calibration curve analysis

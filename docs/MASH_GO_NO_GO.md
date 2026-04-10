# MASH Go / No-Go Decision Tree

**Closes #54.** Written 2026-04-09 in response to the Cadre of Critics review (Software Mogul critique CR-1, see `COMPARISON_DELTRON_2026-04-09.md`).

> *"Walk me through the last five minutes before MASH starts. The room is full. Sarah's CoP is up. What are you doing? What is your phone doing? What does your laptop look like? What is the one command you are about to run, and what is the worst thing that could go wrong when you run it?"*

This document is the answer.

---

## Single source of truth

If anything in this document conflicts with another doc, **this document wins for MASH operations**. Update this doc first, then propagate. The deployer (Scott or Mia) reads this on the morning of the event before doing anything else.

---

## The hard rules

These are non-negotiable. The Cadre flagged them; the deployer enforces them.

1. **No new dependencies after Friday before MASH.** Any integration that doesn't work by end of day Friday is shipped *without* for the event. No exceptions.
2. **DELTRON is treated as a degraded backend, not a hard dependency.** If DELTRON's tier signal is missing, chat-to-cop processes the message anyway with no tier filter. The DELTRON dependency is *additive*, never blocking. (See #45.)
3. **No code changes in the 24 hours before MASH** unless they fix a critical bug. A "critical bug" is something that would prevent the system from running, not something that would make it slightly better.
4. **One person is "the deployer" for the duration of the event.** Decisions get made by that person, in real time, with no committee. Default: Scott. Backup: Mia.
5. **The deployer's laptop is the only deployment target.** No remote deploys during the event. Everything runs on the same physical box.
6. **The kill switch is the first action under any uncertainty.** When in doubt, pause writes (#56), then think.

---

## Pre-event checklist (T-0 = morning of MASH)

The deployer runs through this checklist on the morning of the event, in order. **Every checkbox must be checked before starting the day.** If any item fails, see "Recovery procedures" below before proceeding.

### Hardware and connectivity

- [ ] Laptop physically present, AC plugged in, fully charged
- [ ] Two power outlets verified (one for laptop, one as a spare)
- [ ] Internet connectivity tested by loading two unrelated URLs
- [ ] AG GovCloud accessible (`ssh ag-node` per ag-helpers — *if* AG is in the deployment plan)
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

- [ ] **Primary:** Gemini 2.5 Flash via `quick_test.py --url ... --model gemini-2.5-flash` — verified working with 5 demo messages
- [ ] **Backup 1:** Anthropic Claude (Haiku or Sonnet) via `--anthropic --model ...`
- [ ] **Backup 2:** Local Ollama with Qwen 7B-8K via `./scripts/demo.sh`
- [ ] **Backup 3:** AG vLLM (if #51 is done) — *currently aspirational*
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

## The decision matrix

Use this to decide what to do at T-0 based on the actual state of each component.

### Legend

| Symbol | Meaning |
|---|---|
| ✓ | Verified working in the pre-event checklist |
| ✗ | Verified broken or unavailable |
| ? | Status unknown or untested today |

### Matrix

| chat-to-cop | DELTRON | CoP schema | Internet | Action |
|---|---|---|---|---|
| ✓ | ✓ | Final (real CoP) | ✓ | **GO.** Deploy joint pipeline writing to real CoP. Best case. |
| ✓ | ✓ | Final (real CoP) | ✗ | **GO with degraded backend.** Local Ollama only. Joint pipeline. CoP writes to real CoP via local network if available, else queue. |
| ✓ | ✓ | Draft | ✓ | **GO with local SQLite.** Joint pipeline. Sarah has confirmed the schema fields we're using are stable. Reconcile after the event. |
| ✓ | ✓ | Missing | ✓ | **GO in demo mode.** Joint pipeline. *No* external CoP writes. Local SQLite only. Show the structured output to operators directly. |
| ✓ | ✗ | Final | ✓ | **GO with chat-to-cop standalone.** No tier filter. Process every message. We still produce structured CoP updates. |
| ✓ | ✗ | Draft | ✓ | **GO standalone with local SQLite.** Same as above plus the SQLite scope reduction. |
| ✓ | ✗ | Missing | ✓ | **GO in demo mode standalone.** Same as above plus no external writes. |
| ✓ | ✗ | any | ✗ | **GO standalone, local Ollama, local SQLite.** Most degraded GO state. Still produces useful CoP output. |
| ✗ | ✓ | any | ✓ | **NO-GO for chat-to-cop.** Try to recover (see procedures below). If not recovered in 30 min, demo *DELTRON* alone and present the chat-to-cop slides. |
| ✗ | ✗ | any | ✓ | **NO-GO for the live system.** Show the slides. Run the smoke test from `quick_test.py` against Gemini Flash if internet works. Talk through the Opus silver label results. |
| ✗ | ✗ | any | ✗ | **NO-GO entirely.** Show the slides. Walk through the architecture. Discuss results from the prior runs. The work doesn't disappear because the demo failed. |

### When in doubt

If the matrix doesn't cover the situation, the deployer's default is **the most degraded GO state that still produces useful output**, which is: chat-to-cop standalone, local Ollama, local SQLite, no external CoP writes. This is one command:

```powershell
python -m chat_to_cop.replay data/chat/Dash3-GBC/Data/23Sep/usaf/chat.zip --num-ctx 8192
```

---

## Recovery procedures

Each procedure is designed to take **less than 5 minutes**. If a procedure takes longer than 5 minutes, abandon it and fall back to the next column in the decision matrix.

### Ollama not responding

**Symptom:** `quick_test.py` against the local Ollama returns connection errors, or `curl http://127.0.0.1:11434/api/tags` fails.

**Procedure:**

1. `taskkill /F /IM ollama.exe` (Windows) or `pkill ollama` (Unix)
2. Wait 5 seconds
3. `ollama serve &` (or just `ollama serve` in a new terminal)
4. Re-run the smoke test
5. If still failing: switch backend to Gemini Flash via `--url https://generativelanguage.googleapis.com/v1beta/openai/ --model gemini-2.5-flash`

### Cloud API rate-limited

**Symptom:** `quick_test.py` against Gemini Flash returns 429 or rate-limit errors.

**Procedure:**

1. Switch to Anthropic: `python scripts/quick_test.py --anthropic --model claude-haiku-4-5-20251001`
2. If Anthropic also limits: switch to local Ollama
3. Wait 5 minutes before retrying the original API
4. **Do not** keep retrying the same API in a loop — that makes the rate limit worse

### Pipeline crash mid-replay

**Symptom:** `replay.py` exits with an exception during a long-running replay.

**Procedure:**

1. Don't restart from scratch. The replay is **idempotent on the database** — re-running with the same `--db` path will skip already-processed messages.
2. Read the last log line to understand what failed.
3. If it's a backend timeout: increase `--timeout` and re-run
4. If it's a Pydantic validation error: check `data/labels/` for any malformed labels, or check the latest message that failed and add a guard
5. If it's a memory error: drop to a smaller model

### Network down

**Symptom:** No internet, cloud APIs unreachable.

**Procedure:**

1. Switch to local Ollama: `./scripts/demo.sh`
2. Verify Ollama has a model pulled: `ollama list` should show at least `qwen2.5:7b-8k`
3. If Ollama doesn't have the model, you can't recover during the event — fall back to demo mode
4. Inform the deployer: this is a known fallback path and the system is designed for it

### Laptop GPU saturated

**Symptom:** Inference is dramatically slower than expected; `nvidia-smi` shows 100% GPU.

**Procedure:**

1. Drop to a smaller model: `ollama pull qwen2.5:3b` then re-run with `--model qwen2.5:3b`
2. Or drop to CPU mode: `OLLAMA_NUM_GPU=0 ollama serve`
3. If the GPU is saturated by *another process* (not Ollama), kill that process or move chat-to-cop to a different backend (cloud API)

### CoP writer failing

**Symptom:** CoPWriter throws errors writing to the configured CoP endpoint.

**Procedure:**

1. Flip to local-SQLite-only mode: set `CHAT_TO_COP_COP_WRITER_DRY_RUN=true` in env
2. Pipeline continues, all updates land in the local SQLite database
3. Updates can be replayed to the real CoP after the event
4. **Do not** try to "fix" the CoP API connection during the event — that's a post-event problem

### Sarah's schema changes mid-event

**Symptom:** Sarah pings Teams to say the schema fields you're writing to don't match what she has now.

**Procedure:**

1. Acknowledge but **do not** change the production code mid-event (hard rule #3)
2. Continue writing to local SQLite with the old fields
3. After the event: write a one-shot translation script that maps the old fields to the new ones, then bulk-import
4. Document the change in the post-event notes

### "I have no idea what's wrong"

**Symptom:** Something is broken and the symptoms don't match any of the above.

**Procedure:**

1. Hit the kill switch (#56). Pause writes.
2. Take a screenshot of the dashboard.
3. `git status`, `git log --oneline -5`, save them to a file.
4. Snapshot the latest log file.
5. Switch to the most degraded GO state in the decision matrix (chat-to-cop standalone, local Ollama, local SQLite).
6. After the event, we will analyze the snapshot.
7. **Do not** try to debug live in front of operators.

---

## The actual command sequence

Per the Software Mogul's "what is the one command you're about to run" question, here's the actual sequence the deployer runs at T-0 in the most likely (best-case) scenario:

```powershell
# Step 1: Verify state
cd C:\Users\hsclouse\GitProjects\act3\chat-to-cop
git status                          # Should be clean, on main
git pull                            # Should be no-op
ruff check src/ tests/ scripts/     # Should pass
pytest tests/ -k "not integration" -q  # Should be 843+ passing

# Step 2: Smoke test the chosen backend
python scripts/quick_test.py --url https://generativelanguage.googleapis.com/v1beta/openai/ --model gemini-2.5-flash

# Step 3: Verify the smoke test produced 4 entity DESTROYED extractions
# (This is the HYDRO_SL SITREP from Slide 5 of the briefing)
# If yes: proceed to step 4
# If no: abandon Gemini, try Anthropic, then local Ollama

# Step 4: Start the pipeline against the live IRC server
# (This command will be different for the live event vs replay mode)
python -m chat_to_cop.replay data/chat/Dash3-GBC/Data/23Sep/usaf/chat.zip --num-ctx 8192

# Step 5: Open the operator dashboard in a browser
start http://localhost:8000
```

**The single most important thing the deployer does:** verify that step 2 produces the expected output. If it does, every other command is mechanical. If it doesn't, fall through to the recovery procedures above.

---

## What could go catastrophically wrong

The Software Mogul asked "what is the worst thing that could go wrong when you run it?" Here are the actual failure modes ranked by severity:

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

The catastrophic failures all map to existing issues we have plans for. The severe failures all have recovery procedures above. Everything below "severe" is a problem but not a project-killer.

---

## Post-event checklist

After MASH ends, the deployer:

- [ ] Snapshots all logs from the event
- [ ] Dumps the override audit log (#59) for analysis
- [ ] Captures the pipeline run tracker output
- [ ] Reports total updates produced, by tier, by channel, by speaker
- [ ] Identifies the messages with the most operator overrides — these are our worst failures
- [ ] Reconciles local SQLite to the real CoP (if running in degraded mode)
- [ ] Drafts a one-page event summary for Jared and Mia
- [ ] Updates this doc with anything we learned the hard way

The point of the post-event work is to make the next MASH go better. Every failure mode we hit becomes a checklist item or a recovery procedure for next time.

---

## When this doc gets updated

This document is **alive** between now and MASH. Every time we discover a new failure mode, a new recovery procedure, a new dependency, or a new degraded GO state, the deployer updates this document.

The deployer also reads this document **once per week** between now and MASH to make sure the contents still match reality. If a backend gets renamed, a model gets deprecated, an API key expires, or a path changes, this is the place that catches it.

After MASH, this document becomes the template for the *next* MASH-equivalent event.

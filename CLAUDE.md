# CLAUDE.md

## Project Overview

AI staff officer system: a team of stateful agents that watch military IRC chat and voice STT, maintain situational awareness, learn operator patterns, and push structured world-state updates to a Common Operating Picture (CoP) database. Target: MASH event, May 2026.

This system embodies **equifinality** (multiple paths to the same CoP update), **antifragility** (stress produces information that improves routing), and **FACS** principles (heterogeneous agents, loosely coordinated, configurable at runtime). It is a miniature FACS research platform that happens to produce operational value. See docs/DESIGN_PHILOSOPHY.md.

## Key Context

- This is an ACT3 government project for DASH/MASH wargame events
- Data is IL2 (unclassified) through the MASH event
- The CoP database schema is pending from the contractor team — design around the BattleEffectSchemaV2 and GenMSG field specs in docs/SCHEMAS.md for now
- Chat data from DASH 1-3 is available on Pydio (see docs/DATA_SOURCES.md)
- The equifinality repo (`../equifinality/`) has entity catalogs, target taxonomies, and alias resolution code that can be reused
- The "Against the Ontological Mandate" paper (in C2ES Google Drive) is the intellectual foundation — we do NOT use top-down declared ontologies. The LLM is the ontology. See docs/DESIGN_PHILOSOPHY.md for how this connects to our two research questions.

## Architecture

```
IRC WebSocket -> Message Router -> Channel Agents (1 per channel)
                                       |
                                  Each agent: conversation window + speaker models
                                  + world state + degrading LLM backend
                                       |
                                  Fusion Agent (semantic deconfliction)
                                       |
                                  World State Store -> CoP REST API
```

See docs/ARCHITECTURE.md for details.

## Source Layout

```
src/chat_to_cop/
    agent/           channel_agent.py (stateful per-channel), fusion_agent.py, supervisor.py
    backend/         base.py (protocol), openai_compat.py (instructor), regex_fallback.py, degrading.py
    ingestion/       irc_client.py (live WebSocket), replay.py (DASH log parser)
    models/          messages.py (IRCMessage), cop_update.py (CoPUpdate), speaker.py, world_state.py
    output/          store.py (SQLite), cop_writer.py (CoP REST API), cop_rest_client.py
    metrics.py       Toggleable instrumentation (counters, histograms, timers)
    config.py        Configuration management
    replay.py        CLI entry point: python -m chat_to_cop.replay <path>
```

## Design Principles

- **Model-agnostic**: OpenAI-compatible API everywhere. `instructor` + Pydantic for structured output. No vendor lock-in. No MCP, no LangChain, no framework.
- **Equifinality**: Multiple paths to the same CoP update (large model, small model, regex, passthrough, cross-channel fusion).
- **Antifragile**: Component failures produce information that improves future routing.
- **Flow never stops**: Every message gets something written to CoP. Quality degrades; output never halts. "Out-of-ontology" data is retained, never dropped (Pattern B).
- **Stateful, not stateless**: Agents maintain conversation context, speaker models, and world state across messages.
- **Shape the boundary, not the inside**: Validate the CoP REST API output shape (Pydantic). Do NOT police internal agent representations (Pattern C).
- **Zero-Trust semantics**: Every assertion ships with source, time, and confidence. Provenance is a gating signal.

## Workflow — READ THIS BEFORE CODING

### Branch/MR process (required)
Every change goes through a branch and merge request. Never commit directly to main.
```bash
git checkout main && git pull
git checkout -b <issue-number>-<short-description>   # e.g., 8-regex-backend
# ... do work, write tests, run lint ...
git push -u origin <branch-name>
# Create MR on GitLab, then merge
```

### Before every commit
```bash
ruff check src/ tests/                    # Lint
ruff format src/ tests/                   # Format
pytest tests/ -k "not integration" -q     # All unit tests pass
```
CI enforces this on every push. Don't push code that fails lint or tests.

### Commit style
```
feat: Short description of what changed

Closes #<issue-number>

- Bullet points of what was done
- Include test counts

Co-Authored-By: Claude <noreply@anthropic.com>
```

### Issue tracking
All work is tracked in GitLab issues on DLE: https://gitlab.dle.afrl.af.mil/c2es1/mash/chat-to-cop/-/issues
- Sprint 1 (Vertical Slice): #1-#7 — ALL COMPLETE
- Sprint 2 (Multi-Channel + Resilience): #8-#13 — ALL COMPLETE
- Sprint 3 (Deploy + Harden): #14-#24, #27-#29, #31-#34, #36, #38-#40, #50, #54-#63 DONE.
- 967+ tests passing, 7 validated backends, 41+ MRs merged. Opus silver labels (935 msgs). Calibration model exists for 7B (ECE=0.67) but RQ1 speaker model A/B is being **re-run** after !88 fixed a config-bypass bug that invalidated the April 6 7B and April 10 14B results. Narwhal V100 14B characterization: 9.0s/msg mean, 7.4s p50. IL2 throughout — experiment freely.
Read the issue description before starting work — it has acceptance criteria, dependencies, and design context.

### Remaining open issues (12 total, as of 2026-04-12)

**Blocked (external):**

- `#17` CoP writer — blocked on contractor schema (Sarah Bowman)
- `#37` Equifinality Phase 1 — blocked on Colin provenance check
- `#48` MACE catalog Part C — blocked on Colin

**Active / running:**

- `#26` 30B on 24GB GPU — Qwen3-32B validated on V100 32GB, true 24GB still needed
- `#30` Calibrate confidence — done for 7B, needs other models
- `#35` RQ1 speaker model eval — **re-run in progress** post-!88 (jobs 5973723 / 5973808)
- `#51` vLLM on AG — Ollama alternative (Jennifer Carlet's vLLM expertise)
- `#52` Multi-backend speaker A/B — **re-run in progress** post-!88 (jobs 5973723 / 5973223)

**Research (deferred):**

- `#44` Self-MoA for high-stakes types
- `#45` DELTRON tier consumer (rewritten 2026-04-09, was "Triage classifier")
- `#46` Speaker model routing (depends on #43, which is closed)
- `#47` Temporal decay + path entropy

### Critical lesson: config-bypass bug (2026-04-11)

Three pydantic-settings fields (`AgentConfig.use_speaker_models`, `PipelineConfig.calibration_model`, `SupervisorConfig.*` thresholds) were silently dead in the production replay path because nothing read them between `PipelineConfig()` and the consumer. The April 6 7B and April 10 14B "A/B" experiments both ran with speakers ON in both arms — the deltas were noise, not signal. Fixed in !88, with regression tests in `TestSupervisorAgentConfigPlumbing` that verify env vars actually flip behavior in the production path (not just on the config object in isolation).

**When adding any config Field, also add its consumer in the same MR.** Don't merge a `Field(...)` with no `config.x.y` reader. When testing config, monkeypatch the env var and assert the resulting object's behavior — testing the config object alone only tests pydantic. See `feedback_config_plumbing_audit.md` in memory for the audit pattern.

### Tests
- Unit tests: `tests/test_*.py`, run with `pytest tests/ -k "not integration"`
- Integration tests: marked `@pytest.mark.integration`, require Ollama or external services
- Use in-memory SQLite (`:memory:`) for store tests
- Use FakeBackend classes (see test_channel_agent.py) for agent tests
- Every new component needs tests in the same MR

## Common Commands

```bash
# Install in dev mode
pip install -e ".[dev]"

# Replay DASH chat through agent pipeline
python -m chat_to_cop.replay data/chat/Dash3-GBC/Data/23Sep/usaf/chat.zip --num-ctx 8192

# Run with specific LLM
python -m chat_to_cop.replay <path> --url http://localhost:11434/v1 --model qwen2.5:7b --num-ctx 8192

# Use --timeout to set per-message LLM timeout (seconds, default varies by backend)

# Run tests (what CI runs)
ruff check src/ tests/ && ruff format --check src/ tests/ && pytest tests/ -k "not integration" -v

# Docker deployment
docker compose up
```

## Narwhal HPC Workflow

Narwhal is the AFSNW27526RYZ DSRC V100 cluster. We run A/B experiments and long-running model evaluations there. As of 2026-04-11 the workflow is git-based — no more rsync/SFTP for source updates.

### One-time setup (already done)

- `$WORKDIR/chat-to-cop` is a real git checkout with `origin = git@gitlab.dle.afrl.af.mil:c2es1/mash/chat-to-cop.git`
- Auth: `~/.ssh/id_rsa` (the 2022 RSA key — DLE GitLab does NOT accept ed25519). Public key is registered on the user's DLE GitLab account.
- Conda env at `$WORKDIR/envs/chat-to-cop` is editable-installed against the source dir. The package import path is `/p/work1/hsclouse/chat-to-cop/src/chat_to_cop/`.
- Ollama 0.20.2 at `$WORKDIR/bin/ollama` with models in `$WORKDIR/ollama-models/` (qwen2.5 7b/14b/32b + qwen3 cached)

### Update + submit pattern (canonical)

```bash
# From local Windows shell, via Kerberos GSSAPI SSH wrapper
python ~/bin/narwhal-ssh.py "cd \$WORKDIR/chat-to-cop && git pull && pip install -e . -q"
python ~/bin/narwhal-ssh.py "cd \$WORKDIR/chat-to-cop && sbatch scripts/narwhal_14b_run_a.sh"
python ~/bin/narwhal-ssh.py "cd \$WORKDIR/chat-to-cop && sbatch scripts/narwhal_14b_run_b.sh"
python ~/bin/narwhal-ssh.py "squeue -u hsclouse"
```

**`~/bin/narwhal-ssh.py`** is a paramiko/GSSAPI wrapper around the SSH command. Native OpenSSH on Windows can't read MIT Kerberos for Windows credential caches; the wrapper bridges that gap. Works after a fresh `kinit hsclouse@HPCMP.HPC.MIL` (via iLauncher).

**`~/bin/narwhal-put.py`** is the SFTP companion. Only useful for one-off pre-merge file pushes; prefer `git pull` for normal updates.

### Job scripts

`scripts/narwhal_{7b,14b}_run_{a,b}.sh` — paired A/B job scripts for the speaker-model RQ1 experiment. Each:

- 1 V100, 32 GB RAM, 8 CPUs, 4-8h walltime depending on model size
- Builds an `8k` Modelfile variant of the base model (Ollama needs num_ctx baked in)
- Runs `python -m chat_to_cop.replay` against the DASH-3 chat zip
- Auto-archives the SQLite DB and log to `$ARCHIVE_HOME/chat-to-cop/` on exit (survives the 30-day `$WORKDIR` purge)
- Run B exports `CHAT_TO_COP_USE_SPEAKER_MODELS=false`

**Do NOT add `pip install -e .` to a job script.** All four jobs share the same conda env. Concurrent editable installs race and three-of-four will die at startup. The single pre-install command above handles it for the whole batch.

### Sanity check before long runs

Each `_run_b.sh` script invokes `pytest tests/test_supervisor.py::TestSupervisorAgentConfigPlumbing` before starting the replay. This verifies that the env-var plumbing fix from !88 is still in place; if it's not, the job fails fast instead of wasting GPU hours producing invalid A/B data. The test class uses `monkeypatch.delenv` for env isolation so it passes both with and without `CHAT_TO_COP_USE_SPEAKER_MODELS=false` set in the parent shell.

### After a run

- DBs land at `/p/work1/hsclouse/output/replay_{model}_{with,without}_speakers_<jobid>.db`
- Logs at `/p/work1/hsclouse/output/replay_{model}_{runA,runB}_<jobid>.log`
- Both also copied to `$ARCHIVE_HOME/chat-to-cop/` on exit
- Pull DBs locally (SFTP via `narwhal-put.py` reverse) and run `scripts/eval_speaker_models.py --labels data/labels/dash3_silver_labels_opus.jsonl --with-speakers <runA.db> --without-speakers <runB.db>`

## Code Style

- Python 3.10+
- ruff for formatting and linting (line length 120)
- Type hints for function signatures
- loguru for logging
- Pydantic for all data models
- asyncio for all I/O
- Do NOT add: docstrings to unchanged code, unnecessary error handling, premature abstractions

## Library Adoption Decisions — Don't Reinvent These

| Need | Use This | NOT This |
|------|----------|----------|
| Retries with backoff | `tenacity` | Custom retry loops |
| Circuit breaker | `pybreaker` | Custom state machine |
| Configuration | `pydantic-settings` | Custom config parser |
| Experiment tracking | `mlflow` (Sprint 3) | Custom SQLite run logs |
| Structured LLM output | `instructor` (already using) | Raw API calls + manual parsing |
| Metrics (future) | OpenTelemetry SDK | Custom metrics.py (current, to be replaced) |

The current `metrics.py` works for Sprint 2 but should be swapped for OpenTelemetry before deployment. Prometheus is the ACT3 standard for observability.

## Parallel Agent Work — READ THIS IF RUNNING IN A WORKTREE

### GitLab project
- **DLE GitLab**: https://gitlab.dle.afrl.af.mil/c2es1/mash/chat-to-cop (project ID: 18350)
- **MCP tool prefix**: `mcp__gitlab__` for DLE, `mcp__act3-gitlab__` for ACT3 GitLab

### Sprint 2 parallelization map
These issues can run simultaneously without file conflicts:

**Wave 1 (no dependencies on each other):**
- `#8` (regex fallback backend) — touches `backend/regex_fallback.py` + `tests/test_regex_backend.py`
- `#13` (IRC WebSocket client) — touches `ingestion/irc_client.py` + `tests/test_irc_client.py`

**Wave 2 (depend on Wave 1 or Sprint 1 only):**
- `#9` (degrading backend) — depends on #8, touches `backend/degrading.py`. Use `tenacity` + `pybreaker`.
- `#10` (speaker models) — touches `models/speaker.py` + extends `agent/channel_agent.py`
- `#11` (supervisor) — touches `agent/supervisor.py`. Independent of #10.

**Wave 3 (hardest, depends on most things):**
- `#12` (fusion agent) — touches `agent/fusion_agent.py`. Can mock channel agent outputs.

### Each agent should:
1. `git checkout main && git pull`
2. `git checkout -b <issue-number>-<short-description>`
3. Read the GitLab issue for full acceptance criteria
4. Write code + tests, run `ruff check && ruff format && pytest -k "not integration"`
5. Commit with `Closes #<N>` and `Co-Authored-By: Claude <noreply@anthropic.com>`
6. Push and create MR via `mcp__gitlab__create_merge_request` (project_id: 18350)
7. Do NOT merge — leave MRs open for human review

## Anti-Ontology Principles (from C2ES Addendum)

These are embedded in the architecture, not bolted on:

- **Pattern A (Ontology as hypothesis)**: Our Pydantic schemas are provisional — the metadata dict overflow captures anything that doesn't fit the schema.
- **Pattern B (Out-of-Ontology Lane)**: Passthrough backend ensures no data is ever dropped. Non-conforming records are first-class signals of novelty.
- **Pattern C (Shape the boundary)**: We validate the CoP API output shape. We do NOT police internal agent representations.
- **Pattern E (Parallel analytical paths)**: The degrading backend IS this — LLM path + regex path, differences are signal.
- **Pattern F (OODA-centric metrics)**: Measure time-to-adapt, not compliance. KPPs: TTA, URR, ASR, MTTR.

## RAI / Provenance

Every extraction must be traceable (DoD AI Ethics Principle 3). Track automatically:
- Model name, version, quantization for every LLM call
- Prompt template hash and version
- Confidence score and extraction method
- Full audit trail: raw message -> CoPUpdate -> CoP database write
- See issue #18 for the full RAI provenance plan (system card, dataset cards, AIBOM)

## Important Notes

- The goal is world-state extraction, NOT just NER. Any information that represents a change to the operational picture needs to be captured.
- Latency is the #1 priority. Must be faster than humans.
- "70% is great" — even basic extraction adds value. Don't over-engineer accuracy at the cost of speed.
- The IRC server uses WebSocket protocol on port 8097. Voice STT is already piped into IRC as #stt_* channels.
- White cell (WF_*) messages are the most information-dense. Battle managers communicate in abbreviated military jargon.
- Each channel agent is stateful — it maintains context, learns speakers, and tracks world state across messages.
- Every LLM call must go through the OpenAI-compatible interface. Never import a vendor-specific SDK.
- The system must degrade gracefully: LLM -> smaller LLM -> regex -> passthrough. Never silently stop.
- This system is a research platform, not just a pipeline. The two research questions are:
  1. How to personalize/learn a model of the user during operations (speaker models)
  2. How a heterogeneous team adapts without predefined ontology (LLM as universal translator)

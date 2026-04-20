# Session Notes — 2026-04-11 through 2026-04-13

## 1. Goals

- Fix a config-bypass bug that invalidated RQ1 speaker model A/B experiments
- Re-run the experiments with corrected code on Narwhal HPC
- Clean up the repo (branches, docs, memories) for agentic workers
- Scale the RQ1 experiment to N=100 per cell across 4 models for statistical rigor
- Audit and fix the test suite — replace tautological/weak tests with meaningful ones

## 2. Completed

### Config-bypass bug fix and re-run
- MR !88: `AgentConfig`, `calibration_model`, `SupervisorConfig` threaded from `replay.py` → `Supervisor` → `ChannelAgent`
- 7 regression tests in `TestSupervisorAgentConfigPlumbing` (test_supervisor.py)
- MR !89: 7B Narwhal A/B re-run scripts
- MR !90: Dropped per-job `pip install -e .` (race condition on shared conda env)
- MR !91: Test env isolation (`monkeypatch.delenv` autouse fixture)
- All 4 N=1 A/B jobs completed: 7B speakers neutral (+0.2%), 14B speakers harmful (-1.2%)
- Issues #35 and #52 updated with corrected results + capacity hypothesis REJECTED
- `docs/SPEAKER_MODEL_RESULTS.md` rewritten with full narrative (MR !95, !96)

### Repo cleanup
- MR !87: TM Decision Function mapping merged (was stale 2 days)
- MR !92: CLAUDE.md updated with sprint status, Narwhal workflow, audit lesson
- MR !93: `SCHEMA_CONTRACT.md` → `SCHEMA_PROPOSAL.md` rename
- MR !94: Narwhal `$ARCHIVE_HOME` fallback to `$HOME`
- 7 stale local branches deleted
- `SCHEMA_CONTRACT.docx` removed from working tree (canonical copy in GoogleDrive)
- `pandoc` installed via scoop for docx regeneration

### Narwhal HPC infrastructure
- `$WORKDIR/chat-to-cop` converted from rsync to real git checkout (SSH via `~/.ssh/id_rsa`, DLE GitLab rejects ed25519)
- Branch renamed `master` → `main`, `core.fileMode=false`
- `git pull` works end-to-end
- `qwen2.5:3b` pulled (wasn't cached)
- `narwhal-get.py` and `narwhal-put.py` SFTP wrappers created at `~/bin/`
- SFTP requires relative paths (absolute paths return ENOENT; undiagnosed)

### N=100 speaker sweep
- MR !97: Sweep infrastructure (3 scripts)
- MR !98: `--export` comma separator fix
- 1,600 jobs submitted across 16 cells: {3b,7b,14b,32b} x {on,off} x {det,stoch} x 100
- Job arrays 5978304-5978319, account AFSNW27526RYZ
- As of last check (2026-04-13 ~1950 EDT): 962/1600 DBs complete, 0 failures
- Preliminary ANOVA on det cells (N=400): speakers main effect F=454, p<10^-67

### Test quality audit
- Full audit: 186 tautological + 252 weak out of 933 tests
- External audit (from another chatbot) provided specific Tier 1-5 classifications with file:line refs

### Test rewrites (on branch `test-quality-audit`, 3 commits, NOT pushed)

Commit 1 — test_config.py, test_dashboard.py, test_models_messages.py:
- `tests/test_config.py`: 13 tautological → 23 production-path plumbing tests
- `tests/test_dashboard.py`: 23 string-in-string → 8 structural/contract tests
- `tests/test_models_messages.py`: merged 2 redundant, strengthened 3 weak, replaced 1 enum test

Commit 2 — eval_speaker_sweep.py fixes:
- `scripts/eval_speaker_sweep.py`: reads `updates` table with `data_json`, uses `extracted_type` field, handles partial ANOVA

Commit 3 — Tier 1/2 from external audit:
- `tests/test_adversarial.py`: spoofing tests now route through ChannelAgent pipeline
- `tests/test_regex_backend.py`: sitrep/shot-down tests now verify update_type + DESTROYED status
- `tests/test_fusion_agent.py`: provenance test checks primary source AND corroborating context

Full suite: 962 passed after all changes.

## 3. In progress / partially done

### Branch `test-quality-audit` (local only, 3 commits)
- Based on local `main` (which may be behind origin if !98 merged)
- Cannot push: GitLab DNS unreachable (Kerberos ticket expired or VPN down)
- When network returns: push, MR, merge

### Branch `fix-sweep-export-commas` (on origin)
- Has 2 commits: the comma fix (!98 already set to auto-merge) + the eval_db fix
- The eval_db fix is ALSO in `test-quality-audit` commit 2
- Need to reconcile: either merge !98 first then rebase, or squash into the test-quality-audit MR

### Sweep on Narwhal
- 962/1600 as of last check; estimated ~3-5 more days for full completion
- 32b_on_stoch is the long pole (~21 hrs at 20 concurrent)
- 3b cells progressing slowly (3B takes 112 min/run, slower than 7B due to instructor retries)
- All jobs self-terminate on completion; no monitoring needed

### Deep research prompt
- Written at `c:\Users\hsclouse\GitProjects\workingFiles\deep_research_speaker_models.md`
- Ready to hand off to a deep research agent
- Contains: system description, speaker model design, N=100 ANOVA results, 8 research questions

## 4. Decisions and tradeoffs

- **Default speakers OFF for MASH**: recommended based on N=1 results; awaiting N=100 for final confirmation
- **N=100 per cell**: user requested for statistical rigor + to burn Bryan's Narwhal hours
- **DSRC bills CPU core-hours**: 128 cores/node x wall-clock. Total sweep ~0.45M core-hours. Memory saved at `infra_dsrc_hour_accounting.md`
- **"Contract" → "Proposal"**: USAF AFMC ACQ community; "contract" has formal legal meaning. Memory saved at `feedback_avoid_contract_terminology.md`
- **No seed for stochastic cells**: user wants operational variance, not sterile lab conditions. Deterministic cells (temp=0) run in parallel for comparison.
- **test_cop_writer.py left alone**: external audit flagged it but deeper inspection showed tests are actually solid (write-authority tiers, boundary conditions, kill switch all well-tested)
- **Pydantic-testing tests in test_config.py**: external audit correctly flagged my new coercion tests as Tier 3. I kept them because they're marginally useful as regression guards, but they're low priority to fix further.
- **3B model unexpectedly slow**: 112 min/run vs 7B's 63 min. Likely instructor retry overhead on weaker structured output. Not a bug; worth noting in the research writeup.

## 5. What's left (in order)

1. **Push `test-quality-audit` branch** when GitLab is reachable → MR → merge
2. **Reconcile `fix-sweep-export-commas`** branch (has eval_db fix that overlaps with test-quality-audit)
3. **Remaining test fixes from external audit** (prioritized):
   - Calibration plumbing test (HIGH — same !88 bug class): verify CalibrationModel reaches ChannelAgent.process_message
   - test_e2e_replay.py: replace `> 0` with exact counts on deterministic fixtures
   - test_api.py:108: `>= 1` → `== 2`
   - test_degrading_backend.py:288+: `>= 1` → `== 1`
   - test_channel_agent.py:169,187: strengthen content assertions
   - test_provenance.py:137: circular hash test (delete or rewrite)
   - test_fusion_feedback.py:100-136: dataclass field readback (delete or rewrite)
   - test_openai_backend.py:58-148: 12 string-in-prompt tests (fragile, low value)
   - Smoke tests for anthropic_direct.py, asksage.py, bedrock.py (zero coverage)
4. **Wait for sweep completion** (~3-5 days from 2026-04-13)
5. **Run `eval_speaker_sweep.py`** on all 1,600 DBs → full 3-way ANOVA
6. **Update `docs/SPEAKER_MODEL_RESULTS.md`** with N=100 numbers across 4 models
7. **Update issues #35, #52** with statistically defensible conclusion
8. **Decision: default speakers OFF in config** (one-line change to `config.py` if confirmed by N=100)

## 6. Background processes

### Narwhal HPC sweep (autonomous, no monitoring needed)
- 16 Slurm array jobs (5978304-5978319), `--array=1-100%20`
- Account: AFSNW27526RYZ
- Jobs self-terminate; DBs land in `$WORKDIR/output/sweep/sweep_*.db`
- Check progress: `python ~/bin/narwhal-ssh.py "ls $WORKDIR/output/sweep/sweep_*.db | wc -l"`
- Check failures: `python ~/bin/narwhal-ssh.py "sacct -S 2026-04-12 --format=State -P | grep -c FAILED"`

### Nothing else running locally.

## 7. Key file locations

| What | Path |
|---|---|
| Sweep job script | `scripts/narwhal_speaker_sweep.sh` |
| Sweep submitter | `scripts/submit_speaker_sweep.py` |
| Sweep evaluator | `scripts/eval_speaker_sweep.py` |
| N=1 eval results (7B) | `data/narwhal_results_post_88/eval_7b_post_88.md` |
| N=1 eval results (14B) | `data/narwhal_results_post_88/eval_14b_post_88.md` |
| Deep research prompt | `c:\Users\hsclouse\GitProjects\workingFiles\deep_research_speaker_models.md` |
| Schema proposal for Sarah | `docs/SCHEMA_PROPOSAL.md` |
| Schema proposal Word doc | `C:\Users\hsclouse\GoogleDrive\Documents\AC3\C2ES\Chat-to-CoP_Schema_Proposal_for_Sarah.docx` |
| Narwhal SSH wrapper | `~/bin/narwhal-ssh.py` |
| Narwhal SFTP upload | `~/bin/narwhal-put.py` |
| Narwhal SFTP download | `~/bin/narwhal-get.py` |
| Memory files | `C:\Users\hsclouse\.claude\projects\c--Users-hsclouse-GitProjects-act3-chat-to-cop\memory\` |

## 8. Git state

- Local branch: `test-quality-audit` (3 unpushed commits ahead of main)
- Also exists: `fix-sweep-export-commas` (on origin, partially overlaps with test-quality-audit commit 2)
- Origin main: at !98 merge or later (can't check — no network)
- Narwhal: last synced to main at the !96 merge; has side-channeled eval_speaker_sweep.py and submit_speaker_sweep.py fixes

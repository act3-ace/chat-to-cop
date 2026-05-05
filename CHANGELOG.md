# Changelog

All notable changes to this project will be documented in this file.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

While at 0.y.z (initial development), MINOR bumps may include breaking changes.
The major version will advance to 1.0.0 when the API is considered stable.

## [Unreleased]

### Added

- Contractor/vendor backend guide docs/BACKENDS.md (!150): Setup
  instructions for all 6 backends (Ollama, vLLM/LM Studio/llama.cpp,
  Azure OpenAI, Bedrock, Anthropic, Ask Sage), environment variable
  reference table, model selection guide, flash drive section. Linked
  from README.md.
- DLE GitLab portable build validation CI: `test-portable` job in
  `.gitlab-ci.yml` validates flash-drive deployment files (compose YAML
  parsing, required file checks, package import, start script sanity)
  on every MR touching portable/Docker files. Replaces the GitHub Actions
  `portable-build.yml` which violated the GitHub-is-mirror-only rule.
- Azure OpenAI backend support (!149): DoD contractors with Azure
  Government can use `--azure --azure-endpoint URL` or set
  `CHAT_TO_COP_LLM_AZURE_ENDPOINT`. Uses `AsyncAzureOpenAI` from the
  openai SDK with instructor. No new dependencies. Default model gpt-4o,
  default API version 2024-10-21.
- run.bat auto-update and zip-to-clone conversion (!148): On launch, if
  the directory is a git clone, `git pull --ff-only` pulls the latest code.
  If the directory was extracted from a zip (no `.git`), run.bat silently
  converts it into a shallow clone from DLE GitLab via SSH, enabling
  auto-updates on all future runs. Conversion is best-effort -- if SSH
  fails, the script continues normally without auto-updates.
- Push-button Windows setup for MASH participants (!142, !143, !144):
  - `scripts/bootstrap.ps1`: PowerShell one-liner (`irm url | iex`) that
    installs Python, Git, Ollama via winget, clones the repo, and runs a
    smoke test. Handles Win11 PS 5.1, MS Store Python alias, execution
    policy restrictions. Safe to re-run.
  - `setup.bat`: In-repo prerequisite checker/installer with `--check` and
    `--help` modes. Uses `:do_install` subroutine to work around batch
    errorlevel-in-compound-block bug. Falls back to manual URLs if winget
    unavailable.
  - `run.bat`: Updated to point at `setup.bat` for missing prereqs, uses
    `python -m pip` instead of bare `pip`, improved Ollama skip UX. Adds
    pip bootstrap (`ensurepip`), Ollama startup retry loop (30s), and
    pre-smoke connectivity check to prevent 10-minute timeout hangs.
  - `docs/QUICKSTART.md`: Added "Windows One-Click Setup" section with
    both options (one-liner and setup.bat), cmd.exe fallback note.
  - `README.md`: Zero-CLI Windows quick start (download zip from GitLab,
    setup.bat, run.bat) now appears before the CLI instructions.
- GitHub Actions Windows onboarding CI (!145):
  `.github/workflows/windows-onboarding.yml` tests the full onboarding
  path on a fresh `windows-latest` runner: setup.bat, pip install, Ollama
  zip binary install, model pull, 5-message smoke test, and unit tests.
  Triggers on changes to onboarding scripts, pipeline source, or
  pyproject.toml.

- `deploy/ag/start-backend-for-mia.sh`: One-command AG backend launcher.
  Starts a GPU node, launches vLLM and/or LiteLLM, prints copy-paste
  instructions to send to a teammate. Supports `--vllm`, `--bedrock`,
  `--both`, `--status`, `--stop`.
- run.bat rewrite with cloud mode, GPU detection, and logging (!146):
  `run.bat --cloud URL` skips Ollama and points at a remote LLM backend
  (AG vLLM, Bedrock via LiteLLM). nvidia-smi GPU detection auto-selects
  14B/7B/3B model based on VRAM. Logs to `data\run.log` for remote
  troubleshooting. Health summary prints Python/GPU/backend/model after
  smoke test. Vendor wheel support (`--no-index --find-links vendor\`).
- deploy/ollama/Modelfile.14b: qwen2.5:14b with 8K context window.

### Changed

- deploy/ag/launch-vllm-chat2cop.sh (#51, !146): HuggingFace cache default
  changed from ephemeral `/tmp/huggingface` to NFS-persistent
  `${HOME}/.cache/huggingface` so model weights survive AG node restarts.
  Added T4-specific vLLM flags
  (--dtype float16, --max-num-seqs 16) auto-detected from GPU hardware,
  multi-GPU tensor parallelism support, and vLLM env vars
  (VLLM_WORKER_MULTIPROC_METHOD, VLLM_NO_USAGE_STATS). Based on Jennifer
  Carlet's production benchmarks from analytics-gateway/llms-on-ag.
- docs/ANALYTICS_GATEWAY_DEPLOYMENT.md (#51): Added T4-specific notes section,
  Jennifer Carlet's vLLM benchmark table (Qwen3.5 models on g4dn T4), Triton
  patch reference for CC <8.0 GPUs, updated Docker/native examples with T4 flags.

### Added

- CoP schema adapter layer (#70): `PassthroughAdapter` (default, preserves
  existing payload shape) and `JsonSchemaAdapter` (mapping-config driven,
  transforms CoPRecord into target schema). Closed set of transforms (upper,
  lower, int, float, iso_datetime), construct-time validation, no user code
  execution.
- Hot-reload mapping config (#72): `HotReloadAdapter` wraps `JsonSchemaAdapter`
  with mtime-based auto-reload (default 30s check interval). Invalid mappings
  log a warning and keep the previous version. `build_adapter()` now returns
  `HotReloadAdapter` for jsonschema specs.
- adapt-schema CLI (#71): `python -m chat_to_cop.cli.adapt_schema` for at-event
  CoP schema ingestion. Reads SQLite (PRAGMA table_info), Excel/CSV (openpyxl),
  or live database (SQLAlchemy reflect). Generates mapping config via LLM (any
  OpenAI-compatible endpoint) or template fallback with common field name
  matching.
- Prompt-regression replay harness (#73): `scripts/replay_bench.py` with
  stratified sampling by update_type, `scripts/blueback_replay_bench.sh` Slurm
  wrapper, `docs/RUNBOOK.md` procedure for pre-merge prompt regression checks.
- CI token-budget guard (#73): `tests/test_prompt_budget.py` pins system prompt
  at 17,500 chars (current is 15,010 + 15% headroom). Prevents silent prompt
  bloat from breaking context windows.
- HPC image build via AG compute nodes: `build-hpc-on-ag` CI job SSHes to an
  Analytics Gateway m7i.4xlarge node (64GB RAM, 90GB disk, Docker pre-installed),
  runs `docker build`, and pushes to DLE registry. Replaces Kaniko-based
  build-hpc which OOM'd on shared runners. Manual trigger, works on tags and main.
- `deploy/ci/ag-build-hpc.sh`: orchestrator script (qsub with disk allocation,
  snippet poll with TLS CA support, SSH build via ProxyJump, qdel cleanup)
- `deploy/ci/ag-node-build.sh`: self-contained build script for AG compute
  nodes (DoD CA install, Docker insecure-registries config, git clone, docker
  build/push)
- CI variables: AG_SSH_KEY, AG_USERNAME, AG_SNIPPET_ID, DLE_GITLAB_TOKEN

### Fixed

- Hard wall-clock cap on `extract()` (#75): `hard_timeout` parameter bounds the
  full instructor retry chain. Without it, pathological schema-validation loops
  burn `timeout * (max_retries+1)` seconds per message (12 min observed on
  Blueback with 14B / 120s / max_retries=2). Surfaced as `RetryableError` so
  `DegradingBackend` falls through.
- Replace port-fragile `num_ctx` heuristic with explicit `is_ollama` flag (#74):
  old heuristic gated on '11434' or 'ollama' in the URL; any non-standard port
  (HPC array jobs) silently dropped `num_ctx`, causing Ollama's 4K default to
  truncate the system prompt. New: explicit `is_ollama` constructor param +
  auto-detect fallback + loguru warning when detection is ambiguous.

### Failed approaches

- Stash-pop across branches that touch the same file: the #71 branch was cut
  from main after #74 merged, but a `git stash pop` conflict during development
  silently reverted `openai_compat.py` to its pre-#74/#75 state. The commit
  looked clean locally (all tests passed because the test file also lacked the
  #74/#75 tests at that point) but CI caught it. Lesson: after resolving stash
  conflicts, diff the resolved files against main to verify no accidental
  reversions.

## [0.1.4] - 2026-04-20

### Fixed

- HPC build: removed --tar-path from Kaniko (OOM on 20GB+ image); push to
  registry instead
- HPC SIF conversion: use Fedora + skopeo to pull from DLE registry with
  --tls-verify=false, then singularity build from docker-archive

### Changed

- HPC build jobs (build-hpc, build-hpc-sif) are now manual with
  allow_failure: DLE shared runners OOM on the 20GB+ image (13GB of model
  blobs exceeds Kaniko's snapshot memory). Build HPC images locally instead.
- Release job no longer blocks on HPC builds
- Release job: set SSL_CERT_FILE to CI_SERVER_TLS_CA_FILE so release-cli
  trusts the DLE GitLab CA cert

## [0.1.3] - 2026-04-20

### Fixed

- Apptainer builds from local docker-archive tar instead of pulling from DLE
  registry (Singularity cannot skip TLS verification for DoD CA certs)
- HPC Dockerfile: use Ollama install script instead of direct download
  (download format changed from .tgz to .tar.zst)

## [0.1.2] - 2026-04-20

### Fixed

- Apptainer/Singularity CI jobs: added `entrypoint: [""]` so GitLab runner
  can execute shell scripts in the Singularity container image
- HPC Dockerfile: Ollama download URL changed from bare binary to .tgz archive

## [0.1.1] - 2026-04-20

### Fixed

- CI container builds: switched from Docker-in-Docker to Kaniko (DLE shared
  runners don't support DinD privileged mode)
- Added --skip-tls-verify for DLE registry DoD CA certificate
- Added Singularity registry auth for Apptainer build jobs

## [0.1.0] - 2026-04-20

First versioned release, targeting the May 2026 MASH event at H2O Las Vegas.

### Added

- Agent-based architecture with FACS principles (equifinality, antifragility)
- Channel agents with conversation windows, per-speaker models, world state snapshots
- OpenAI-compatible LLM backend with instructor for structured output
- Degrading backend with circuit breakers (LLM -> smaller LLM -> regex -> passthrough)
- Regex fallback backend with domain-specific extractors (track numbers, fuel, weapons)
- Fusion agent with semantic deconfliction, trust scoring, adversarial detection
- Supervisor agent lifecycle, health management, and priority-based load shedding
- Speaker models with CTA/SDAC framework and online inference
- Live IRC WebSocket client with auto-reconnect
- DASH chat log replay for all three formats (DASH 1, DASH 3 per-channel, DASH 3 combined)
- SQLite world state store with entity upsert
- FastAPI REST API for world state queries and SSE streaming
- HTML operator dashboard with pause-writes button and confidence visualization
- CoPWriter with tiered write authority and recalibrated thresholds
- Operator override path with audit log
- Confidence-aware cascading in DegradingBackend
- STT handling, correction messages, and radio check filtering
- Noise pre-filter (URLs, STARTEX/ENDEX, acks, radio checks)
- Synthetic chat data generator with ground truth labels
- Model evaluation harness with precision/recall/F1 per update type
- Calibration model for Qwen2.5-7B-8K confidence scores
- Opus silver labels (856 labeled messages via Claude Opus)
- Docker Compose setup (Ollama + pipeline)
- GitLab CI pipeline: lint, test, Docker build, Apptainer SIF, HPC container, release
- Per-message latency instrumentation in replay
- Adversarial robustness tests (spoofing through ChannelAgent pipeline)
- N=100 speaker model sweep infrastructure (Slurm job arrays, eval harness, ANOVA)
- DELTRON regex patterns ported into regex_fallback.py
- RAI provenance: model_name, prompt_hash on CoPUpdate, PipelineTracker, AIBOM generation
- Documentation: architecture, design philosophy, schemas, data sources, deployment guides

### Fixed

- Config-bypass bug: AgentConfig, calibration_model, and SupervisorConfig now plumbed
  from replay.py through Supervisor to ChannelAgent (!88). Prior A/B experiments were
  silently running with default config in both arms.
- Shared conda env pip install race condition on Narwhal HPC (!90)
- Test environment isolation with monkeypatch.delenv autouse fixture (!91)
- Narwhal ARCHIVE_HOME fallback when symlink target is unprovisioned (!94)
- sbatch --export comma separator (was space-separated, breaking env vars) (!98)
- eval_speaker_sweep.py: correct table name, column names, partial ANOVA handling

### Changed

- Speaker models default to OFF (`use_speaker_models=False`) based on N=100 sweep
  results showing statistically significant degradation across all 4 model sizes
  (3B: -1.54pp, 7B: -4.12pp, 14B: -1.50pp, 32B: -2.74pp, all p<0.0001)
- Schema proposal renamed from "contract" to "proposal" for AFMC terminology
- Test suite: replaced 186 tautological + 252 weak assertions with meaningful tests

### Research

- RQ1 (online user modeling): 1,600-run factorial sweep on Narwhal HPC.
  Prompt-injection speaker models hurt extraction quality at every model size.
  Temperature (deterministic vs stochastic) has no effect (p=0.99).
  Capacity bottleneck hypothesis rejected. See docs/SPEAKER_MODEL_RESULTS.md.
- 7 validated backends: Ollama (3B/7B/14B/32B), Gemini Flash, GPT-4.1-nano, Ask Sage
- Qwen2.5-14B is the cost/performance sweet spot (46.8% type exact, near-32B quality)

[Unreleased]: https://gitlab.example.mil/c2es1/mash/chat-to-cop/-/compare/v0.1.0...main
[0.1.0]: https://gitlab.example.mil/c2es1/mash/chat-to-cop/-/tags/v0.1.0

# Changelog

All notable changes to this project will be documented in this file.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

While at 0.y.z (initial development), MINOR bumps may include breaking changes.
The major version will advance to 1.0.0 when the API is considered stable.

## [Unreleased]

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

[Unreleased]: https://gitlab.dle.afrl.af.mil/c2es1/mash/chat-to-cop/-/compare/v0.1.0...main
[0.1.0]: https://gitlab.dle.afrl.af.mil/c2es1/mash/chat-to-cop/-/tags/v0.1.0

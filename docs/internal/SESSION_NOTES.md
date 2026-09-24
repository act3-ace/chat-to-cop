# Session Notes -- 2026-05-03 (AG Backend Validation + run.bat Cloud Mode)

> Historical record of the 2026-05-03 session. Session notes now live in `SESSION_NOTES.md` at the repo root; this file is kept for the AG and CI lessons it records, not as current state.

## Current State

MRs !142-!146 all merged to main. AG vLLM backend validated on T4 GPU.
GitHub mirror synced. NVIDIA Container Toolkit installed on AG nodes.

| What | Status |
|------|--------|
| MR !142 (bootstrap scripts) | Merged |
| MR !146 (run.bat cloud mode + AG cache) | Merged |
| AG vLLM (model-b-14b-awq on T4) | Validated, model cached in NFS |
| AG Bedrock (Sonnet via LiteLLM) | Validated, IAM role provides creds |
| AG home directory | 19 GB / 30 GB quota |
| AG jobs 1329-1331 | All released (no credits burning) |
| start-backend-for-mia.sh | Created (MR !147) |
| MR !143 (4 low-severity fixes) | Merged |
| MR !144 (Mia onboarding hardening) | Merged |
| MR !145 (GitHub Actions Windows CI) | Merged |
| Issues #83-#86 | All closed (fixed in !143) |
| GitHub mirror | Synced |
| GitHub repo visibility | Still PRIVATE |
| Tests | 1145 passing (no new tests -- scripts + CI only) |

## What Changed This Session

### MR !142: Push-button Windows setup (4 commits)

- `scripts/bootstrap.ps1`: PowerShell one-liner (`irm url | iex`)
- `setup.bat`: In-repo prereq checker/installer
- `run.bat`: Points to setup.bat, `python -m pip`
- `docs/QUICKSTART.md`: Windows One-Click Setup section

### MR !143: Low-severity bootstrap fixes (1 commit)

Fixed all 4 issues from !142 code review round 4:
- #83: Yellow warning block for smoke test failure (not green banner)
- #84: Removed redundant `pause` on Ollama decline
- #85: Added cmd.exe fallback note in QUICKSTART.md
- #86: Added irm|iex one-liner to "re-run" message

### MR !144: Mia onboarding hardening (2 commits)

- `run.bat`: pip bootstrap (`ensurepip`), Ollama startup retry loop
  (30s, 6 tries), pre-smoke connectivity check (avoids 10-min hang)
- `README.md`: Zero-CLI Windows quick start (download zip, setup.bat,
  run.bat) now first section, before CLI instructions. SmartScreen note.

### MR !145: GitHub Actions Windows CI (3 commits)

`.github/workflows/windows-onboarding.yml`:
- Tests full onboarding on fresh `windows-latest` (Server 2025)
- Steps: setup.bat --check, pip install, Ollama zip install, model pull
  (model-b:0.5b), smoke test (5 DASH messages), unit tests (1145)
- 3 iterations to get working:
  1. setup.bat `pause` blocks in CI -- piped `echo.`
  2. Ollama GUI installer hangs on Server runners -- switched to zip binary
  3. Green in 6m31s
- Triggers on push to main touching scripts/source/pyproject.toml

## Issues Created and Closed

| # | Title | Fixed in |
|---|-------|----------|
| #83 | bootstrap.ps1: green banner on smoke test failure | !143 |
| #84 | run.bat: double-prompt on Ollama decline | !143 |
| #85 | QUICKSTART.md: enterprise Win11 cmd.exe default | !143 |
| #86 | bootstrap.ps1: "re-run" lacks one-liner | !143 |

## Lessons Learned

- **Ollama GUI installer hangs on Windows Server**: `OllamaSetup.exe
  /VERYSILENT` blocks indefinitely on Server 2025 (GitHub Actions runner).
  The standalone zip binary (`ollama-windows-amd64.zip`) works perfectly
  and is faster. Use the zip for CI, the installer for desktop users.

- **Batch `pause` blocks CI**: Any batch script with `pause` will hang
  in non-interactive CI. Pipe `echo.` to satisfy it: `echo. | setup.bat`.

- **GitHub Actions PATH persistence**: `$env:Path += ";dir"` only lasts
  for the current step. Use `$dir | Out-File -FilePath $env:GITHUB_PATH
  -Append` to persist across steps.

- **GitHub PR merge creates divergent history**: Merging the same branch
  on both DLE GitLab and GitHub creates two different merge commits. For
  the future: only merge on DLE GitLab, then `git push github main`.
  Never create PRs on GitHub -- it's a push mirror only.

## Mia's Onboarding Path

1. Open repo on DLE GitLab
2. README says: Code > Download zip, extract, setup.bat, run.bat
3. SmartScreen: More info > Run anyway
4. setup.bat installs Python/Git/Ollama via winget
5. run.bat does everything else

No terminal, no git, no PowerShell needed.

## What Changed This Session (2026-05-03)

### MR !146: run.bat cloud mode + AG cache (2 commits)

- `run.bat`: Major rewrite with `--cloud URL` flag, nvidia-smi GPU/VRAM
  detection (auto-selects 14B/7B/3B), logging to `data\run.log`, health
  summary, vendor wheel support (`--no-index --find-links vendor\`)
- `deploy/ollama/Modelfile.14b`: model-b:14b with 8K context
- `deploy/ag/launch-vllm-chat2cop.sh`: HF cache default changed to NFS
  home for persistence across AG node sessions

### AG Backend Validation

- g4dn.xlarge (Tesla T4 16GB, CUDA 12.8)
- Installed NVIDIA Container Toolkit (Docker had no GPU runtime)
- vLLM v0.19.1-cu130 Docker image (19.6 GB)
- model-b-14b-awq: 9.38 GiB VRAM, AWQ Marlin kernel, FlashInfer
- Model weights cached in NFS home (~9.4 GB in ~/.cache/huggingface)
- Smoke test: 5/5 messages, 4/4 extractions correct, 0.90-0.95 confidence
- Startup time: ~105s first run (download), ~20s subsequent (cached)

### AG Home Directory Cleanup

- Increased quota from 11 GB to 20 GB
- Removed junk_from_coord_repo (310 MB), node-v22 (192 MB), ray_results (15 MB)
- Removed failed vLLM pip install artifacts (7.3 GB)
- Current: 19 GB used (9.4 GB HF cache, 2.2 GB gitprojects, 0.8 GB .local)

## Lessons Learned

- AG g4dn nodes have Docker but NOT the NVIDIA Container Toolkit by default.
  Install with: `apt install nvidia-container-toolkit && nvidia-ctk runtime
  configure --runtime=docker && systemctl restart docker`
- HuggingFace xet download backend can fail with "Background writer channel
  closed" on NFS. Retry usually works. Partial downloads are cached.
- vLLM on T4 (CC 7.5): FA2 not supported, falls back to FlashInfer. The
  `--dtype float16 --max-num-seqs 16` flags from Jennifer's benchmarks are
  required.

## What to Do Next

1. Tell Mia the repo is ready (DLE GitLab path)
2. When Mia needs remote backend: start AG GPU node, run
   `bash deploy/ag/launch-vllm-chat2cop.sh`, give her the IP
3. Make GitHub repo public when ready:
   `gh repo edit act3-ace/chat-to-cop --visibility public --accept-visibility-change-consequences`
4. Update bootstrap.ps1 clone URL if GitHub stays private
   (line 180: currently points to GitHub)

## Previous Sessions

- 2026-05-01: Bootstrap scripts + onboarding CI, MRs !142-!145, issues
  #83-#86, GitHub Actions Windows CI, Mia onboarding path
- 2026-05-01 (earlier): Bootstrap scripts, MR !142 code review, issues
  #83-#86 filed
- 2026-04-22: Issues #75, #74, #73, #71, #72 (hard timeout, num_ctx
  detection, prompt regression harness, adapt-schema, hot-reload)
- 2026-04-21: AG CI build pipeline, v0.1.4 release, HPC image build

# Session Notes -- 2026-04-21 (AG CI Build Pipeline)

## Current State

v0.1.4 release pipeline green. HPC image built locally and pushed to DLE
registry (hpc-v0.1.4 + hpc-latest). New `build-hpc-on-ag` CI job written
and CI variables configured. Ready to test on a branch.

## What Changed This Session

Added AG-based HPC image build to the CI pipeline. DLE shared runners OOM
on the 20GB+ image (Kaniko snapshot memory limit). The new approach SSHes
from a DLE shared runner to an AG compute node, which has Docker
pre-installed and 128GB RAM.

### Files Created

- `deploy/ci/ag-build-hpc.sh` -- orchestrator: qsub, snippet poll, SSH, qdel
- `deploy/ci/ag-node-build.sh` -- runs on AG node: CA install, clone, build, push

### Files Modified

- `.gitlab-ci.yml` -- replaced Kaniko `build-hpc` with `build-hpc-on-ag`
- `CHANGELOG.md` -- unreleased entries for AG build pipeline

### CI Variables Added (project 18350)

- AG_SSH_KEY (file, protected) -- AG SSH private key
- AG_USERNAME (protected, masked) -- hsclouse
- AG_SNIPPET_ID (protected) -- 136
- DLE_GITLAB_TOKEN (protected, masked) -- DLE GitLab PAT

## How the AG Build Works

1. DLE CI job (ubuntu:22.04) installs SSH client, configures AG SSH key
2. SSHes to AG head node, submits `qsub` for r5.4xlarge (128GB RAM)
3. Polls DLE GitLab snippet (ID 136) until compute node publishes its IP
4. SSHes to compute node via ProxyJump through ag-head
5. On node: installs DoD CA bundle, clones repo, `docker build`, pushes to DLE registry
6. `qdel` cleans up the AG job (also runs on failure via trap)

## What to Do Next

1. Create a branch, push, and test `build-hpc-on-ag` manually from the pipeline
2. If SSH from DLE runner to AG doesn't work (network/firewall), fallback is
   the laptop build path (already proven today)
3. SIF conversion (`build-hpc-sif`) needs the AG build to succeed first --
   it pulls from DLE registry via skopeo

## Previous Session (2026-04-20)

Converted CI from Docker-in-Docker to Kaniko. Fixed 8+ issues across
pipeline iterations: DinD unsupported, TLS certs, Singularity entrypoints,
Ollama URL changes, Docker Hub rate limits, protected tag variables,
release-cli TLS trust. v0.1.4 release pipeline fully green (5/5 auto jobs).

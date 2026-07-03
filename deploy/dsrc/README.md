# DSRC HPC Deployment Guide

Deploy the chat-to-cop pipeline on DSRC HPCs. Primary target: **Narwhal** (V100 32GB GPUs).

## Key Constraints

| Constraint | Impact |
|------------|--------|
| **No Docker** | Must use Apptainer (Singularity) |
| **No root privileges** | Container runs in userspace |
| **No internet on compute nodes** | Ollama + LLM models baked into container |
| **$WORKDIR purging** | Files older than 14-30 days are deleted |

## Quick Start

### 1. Download the HPC Container (CI-built)

The container is built automatically by GitLab CI on tagged releases. It includes CUDA 12.1, Ollama, Qwen2.5-14B (primary), and Qwen2.5-7B (fallback) — all baked in.

Download the `.sif` from the GitLab release page:
```
https://gitlab.example.mil/c2es1/mash/chat-to-cop/-/releases
```

Look for the **"Apptainer SIF image (HPC, with Ollama + Qwen2.5-14B)"** link.

### 2. Transfer to Narwhal

```bash
# Get a Kerberos ticket
kinit <username>@HPCMP.HPC.MIL

# Transfer container (~12-15GB with baked models)
scp chat-to-cop-hpc_v1.0.0.sif <username>@narwhal.hpc.mil:$WORKDIR/chat-to-cop-hpc.sif

# Transfer DASH 3 chat data
scp -r data/chat/ <username>@narwhal.hpc.mil:$WORKDIR/chat/
```

### 3. Smoke Test

```bash
# Interactive (from a compute node)
salloc --account=YOUR_ACCOUNT --partition=debug --nodes=1 --gres=gpu:1 --mem=16G --time=00:30:00
bash test-hpc.sh

# Or submit as a job
sbatch --account=YOUR_ACCOUNT test-hpc.sh
```

### 4. Run Replay

Edit `submit_slurm.sh` — replace `<your_account>` with your Narwhal allocation.

```bash
# Default: DASH 3 GBC replay with Qwen2.5-14B
sbatch submit_slurm.sh

# Custom chat data
sbatch submit_slurm.sh --export=CHAT_DATA=$WORKDIR/chat/other.zip

# Use smaller fallback model
sbatch submit_slurm.sh --export=MODEL=qwen2.5:7b
```

## CI/CD Pipeline

The HPC container is built entirely in GitLab CI — no local builds needed.

```
Tag a release (v1.x.x)
    ├── build-hpc:      Docker build with Ollama + model download (multi-stage)
    ├── build-hpc-sif:  Convert Docker image → Apptainer .sif
    └── release:        Publish .sif as downloadable artifact
```

### How it works

1. **Stage 1 (model-downloader)**: Downloads Qwen2.5-14B + 7B from Ollama (internet, no GPU)
2. **Stage 2 (runtime)**: CUDA 12.1 base + Ollama binary + model blobs + chat-to-cop package
3. **Conversion**: `singularity build` converts the Docker image to `.sif`

The CI runners need internet access (to pull models) but NOT GPU access. All GPU work happens at runtime on Narwhal.

### To trigger a build

```bash
git tag v1.0.0
git push origin v1.0.0
```

### To build manually (if CI is unavailable)

```bash
# On a machine with Docker + internet
docker build -f deploy/dsrc/Dockerfile.hpc -t chat-to-cop:hpc .

# Convert to .sif (needs Apptainer/Singularity installed)
singularity build chat-to-cop-hpc.sif docker-daemon://chat-to-cop:hpc
```

## GPU Memory Budget (V100 32GB)

| Component | VRAM |
|-----------|------|
| Qwen 2.5 14B (Q4_K_M) | ~9 GB |
| KV cache (8192 ctx) | ~3 GB |
| Overhead | ~1 GB |
| **Total** | **~13 GB** |
| **Available** | **~19 GB free** |

The 14B model is a significant quality upgrade over 7B while staying well within V100 budget. This also validates our path to 24GB MASH GPUs where we'll run Qwen3-32B (~22GB).

## Resource Recommendations

| DASH Dataset | Messages | Estimated Time (V100) | Walltime |
|--------------|----------|-----------------------|----------|
| DASH 3 GBC (1 day) | ~935 | ~2 hours | 04:00:00 |
| DASH 3 full | ~3000+ | ~7 hours | 12:00:00 |
| Quick test (100 msgs) | 100 | ~12 min | 01:00:00 |

The 14B model on V100 should be faster than 7B on T4 (32GB vs 16GB memory bandwidth advantage).

## Storage Best Practices

### $WORKDIR (Lustre)
- **Use for**: Container `.sif`, chat data, processing
- **Performance**: High-throughput parallel I/O
- **Warning**: Subject to automatic purging (14-30 days)

### $ARCHIVE_HOME
- **Use for**: Final results (world_state.db, replay logs)
- **Recommendation**: Copy results immediately after job completes

```bash
# After job completes
cp $WORKDIR/output/world_state_*.db $ARCHIVE_HOME/chat-to-cop/
```

### $HOME
- **Use for**: Small config files, scripts only (small quota)

## Interactive Testing

```bash
# Get a compute node
salloc --account=YOUR_ACCOUNT --partition=debug --nodes=1 --gres=gpu:1 --mem=32G --time=00:30:00

# Load modules
module load apptainer cuda

# Start the container as an instance
apptainer instance start --nv --writable-tmpfs $WORKDIR/chat-to-cop-hpc.sif c2c

# Start Ollama inside
apptainer exec instance://c2c bash -c "OLLAMA_MODELS=/opt/ollama-models ollama serve &"
sleep 10

# Run a quick replay
apptainer exec instance://c2c python -m chat_to_cop.replay $WORKDIR/chat/test.zip --num-ctx 8192

# Stop when done
apptainer instance stop c2c
```

## Troubleshooting

### Container Too Large to Transfer
The HPC .sif is ~12-15GB. Use `rsync` for resumable transfers:
```bash
rsync -avP chat-to-cop-hpc.sif <username>@narwhal.hpc.mil:$WORKDIR/
```

### GPU Not Detected
```bash
nvidia-smi                          # Verify allocation
module load cuda                    # Load CUDA module
apptainer run --nv container.sif    # Ensure --nv flag
```

### Ollama Times Out on First Load
V100 first model load can take 30-60s (loading 14B weights into VRAM). The scripts wait up to 120s.

### Kerberos Ticket Expired
```bash
kinit <username>@HPCMP.HPC.MIL
klist  # verify
```

### Results Purged
$WORKDIR files are purged after 14-30 days. Always archive:
```bash
cp $WORKDIR/output/*.db $ARCHIVE_HOME/chat-to-cop/
```

## File Listing

```
deploy/dsrc/
    README.md           # This file
    Dockerfile.hpc      # Multi-stage Docker build (CI builds this)
    entrypoint.sh       # Container entrypoint (Ollama + replay)
    submit_slurm.sh     # Slurm job script for Narwhal
    test-hpc.sh         # Smoke test (GPU + Ollama + pipeline)
    singularity.def     # Standalone Apptainer def (manual build alternative)
    download_model.sh   # Manual model download (if not using CI)
```

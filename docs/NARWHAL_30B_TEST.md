# Narwhal 30B Validation Test (Issue #26)

Validate Qwen3-30B-A3B on Narwhal V100-PCIE-32GB. The MoE model has 30B total
params with ~3B active, fitting comfortably in 32GB VRAM (~18GB at Q4_K_M).

## Quick Start

All commands run from your Windows machine (Git Bash or terminal).

### 1. Get a Kerberos Ticket

Open HPCMP iLauncher and authenticate, or:

```
kinit hsclouse@HPCMP.HPC.MIL
klist   # verify ticket is valid
```

### 2. Transfer the Script to Narwhal

```bash
python scripts/narwhal_connect.py "mkdir -p \$WORKDIR/scripts"
# Then from local:
scp scripts/narwhal_30b_test.sh hsclouse@narwhal.navydsrc.hpc.mil:/p/work1/hsclouse/scripts/
```

Or use the connect script to create it inline:

```bash
python scripts/narwhal_connect.py "cat > \$WORKDIR/scripts/narwhal_30b_test.sh << 'ENDOFSCRIPT'
$(cat scripts/narwhal_30b_test.sh)
ENDOFSCRIPT"
```

### 3. Submit the Slurm Job

```bash
python scripts/narwhal_connect.py "cd \$WORKDIR && sbatch scripts/narwhal_30b_test.sh"
```

Expected output: `Submitted batch job 12345`

### 4. Monitor Progress

```bash
# Check job status
python scripts/narwhal_connect.py "squeue -u hsclouse"

# Watch output in real time (replace JOB_ID)
python scripts/narwhal_connect.py "tail -50 \$WORKDIR/c2c-30b_JOB_ID.out"

# Check for errors
python scripts/narwhal_connect.py "tail -20 \$WORKDIR/c2c-30b_JOB_ID.err"
```

### 5. Retrieve Results

```bash
# Check output files
python scripts/narwhal_connect.py "ls -lh \$WORKDIR/output/world_state_30b_*.db"

# Copy results to archive (on Narwhal, before purge)
python scripts/narwhal_connect.py "cp \$WORKDIR/output/world_state_30b_*.db \$ARCHIVE_HOME/chat-to-cop/"

# Download to local machine
scp hsclouse@narwhal.navydsrc.hpc.mil:/p/work1/hsclouse/output/world_state_30b_*.db data/results/
scp hsclouse@narwhal.navydsrc.hpc.mil:/p/work1/hsclouse/output/replay_30b_*.log data/results/
```

## Expected Performance

Based on prior V100 runs:

| Model | Duration | Updates | Confidence | VRAM |
|-------|----------|---------|------------|------|
| Qwen2.5-14B (baseline) | 34 min | 362 | 0.25 | ~9 GB |
| Qwen2.5-32B (partial) | ~128 min | 168* | 0.79 | ~22 GB |
| **Qwen3-30B-A3B (target)** | **~60-90 min est.** | **TBD** | **TBD** | **~18 GB** |

The MoE architecture (only ~3B active params per token) should be significantly
faster than dense 32B while approaching its quality. This is the MASH target
model for 24GB desktop GPUs (RTX 4090/5090).

## Prerequisites on Narwhal

These should already be set up from prior runs:

```
/p/work1/hsclouse/
    bin/ollama                 # Ollama binary (NOT ollama/bin/ollama)
    ollama-models/             # Model cache
    envs/chat-to-cop/          # Conda environment with chat-to-cop installed
    chat-to-cop/               # Repo (manually copied, not git clone — DLE GitLab unreachable from Narwhal)
    chat-to-cop/data/dash3/23Sep_usaf_chat.zip  # DASH 3 test data
```

## Critical Lessons Learned

### 1. Ollama binary path is `/p/work1/hsclouse/bin/ollama`

NOT `/p/work1/hsclouse/ollama/bin/ollama` (that directory is empty).

### 2. Partition is `general`, not `MLA`

Account: `afsnw27526ryz`. QOS: `standard`. No `--gres=gpu` needed.

### 3. Compute nodes have no internet

Models MUST be pre-pulled from a **login node** (which has internet):

```bash
python scripts/narwhal_connect.py "OLLAMA_MODELS=/p/work1/hsclouse/ollama-models /p/work1/hsclouse/bin/ollama serve &>/dev/null & sleep 10 && OLLAMA_MODELS=/p/work1/hsclouse/ollama-models /p/work1/hsclouse/bin/ollama pull qwen3:30b-a3b && kill %1"
```

### 4. Always create a Modelfile for 8K context

Default context is 4K which truncates our prompts:

```bash
cat > /tmp/Modelfile <<EOF
FROM qwen3:32b
PARAMETER num_ctx 8192
EOF
ollama create qwen3:32b-8k -f /tmp/Modelfile
```

### 5. Set CHAT_TO_COP_FALLBACK_MODEL to an available model

Default fallback is `qwen2.5:3b` which may not be pulled. Set to `qwen2.5:7b`:

```bash
export CHAT_TO_COP_FALLBACK_MODEL=qwen2.5:7b
```

### 6. Always warm-start the model before replay

```bash
ollama run qwen3:32b-8k "hello" --verbose
```

Verify GPU is detected and tokens/s is reasonable (~20-30 on V100 for 32B).

### 7. DLE GitLab is unreachable from Narwhal

Cannot `git clone` or `git pull`. Transfer files via base64 encoding through SSH:

```bash
# From Windows, encode and send a file:
python3 -c "import base64; print(base64.b64encode(open('file.sh','rb').read()).decode())" | python3 ~/bin/narwhal-ssh.py "base64 -d > /p/work1/hsclouse/chat-to-cop/file.sh"
```

## Troubleshooting

### Job pending too long

Check queue status:

```bash
python scripts/narwhal_connect.py "squeue -u hsclouse"
```

### Kerberos ticket expired

Re-authenticate via iLauncher or `kinit hsclouse@HPCMP.HPC.MIL`.

### SCP fails

Use the narwhal_connect.py wrapper instead of native scp:

```bash
python scripts/narwhal_connect.py "cat \$WORKDIR/output/world_state_30b_12345.db" > data/results/world_state_30b.db
```

## What Success Looks Like

The replay should:
- Complete all 935 messages without hanging
- Produce a world_state DB with 200+ CoPUpdates
- Show average confidence >= 0.50 (cloud-level would be >= 0.70)
- Mean latency under 15 seconds per message
- Total runtime under 3 hours

Compare results against the benchmark table in `docs/BENCHMARK_RESULTS.md`.

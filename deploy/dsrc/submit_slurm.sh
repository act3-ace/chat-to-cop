#!/bin/bash
#SBATCH --job-name=chat-to-cop
#SBATCH --account=<your_account>
#SBATCH --partition=standard
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --gres=gpu:1
#SBATCH --mem=32G
#SBATCH --time=04:00:00
#SBATCH --output=chat-to-cop_%j.out
#SBATCH --error=chat-to-cop_%j.err
#
# =============================================================================
# Chat-to-CoP Replay on DSRC Narwhal (V100 32GB)
# =============================================================================
#
# Runs the DASH chat replay through the AI staff officer pipeline.
# Container includes Ollama + Qwen2.5-14B + 7B fallback, all baked in.
#
# Prerequisites:
#   1. Kerberos ticket:  kinit <username>@HPCMP.HPC.MIL
#   2. Container built by CI — download the HPC .sif from GitLab release:
#        https://gitlab.dle.afrl.af.mil/c2es1/mash/chat-to-cop/-/releases
#   3. Transfer to Narwhal:
#        scp chat-to-cop-hpc_<tag>.sif hsclouse@narwhal.hpc.mil:$WORKDIR/
#        scp -r data/chat/ hsclouse@narwhal.hpc.mil:$WORKDIR/chat/
#
# Usage:
#   1. Edit <your_account> with your Narwhal allocation
#   2. sbatch deploy/dsrc/submit_slurm.sh
#
# Override defaults via --export:
#   sbatch deploy/dsrc/submit_slurm.sh --export=CHAT_DATA=$WORKDIR/chat/other.zip
#   sbatch deploy/dsrc/submit_slurm.sh --export=MODEL=qwen2.5:7b
#   sbatch deploy/dsrc/submit_slurm.sh --export=ALL,TIME_LIMIT=08:00:00
#
# Interactive testing (debug queue):
#   salloc --account=<acct> --partition=debug --nodes=1 --gres=gpu:1 --mem=32G --time=00:30:00
#   bash deploy/dsrc/submit_slurm.sh
#
# =============================================================================

# Load required modules
module load apptainer || module load singularity
module load cuda

# =============================================================================
# Configuration
# =============================================================================

WORK_DIR="${WORKDIR:-$SLURM_SUBMIT_DIR}"
CONTAINER="${CONTAINER:-$WORK_DIR/chat-to-cop-hpc.sif}"
INSTANCE_NAME="chat-to-cop-${SLURM_JOB_ID:-$$}"

# Chat data — default to DASH 3 GBC if not specified
CHAT_DATA="${CHAT_DATA:-$WORK_DIR/chat/Dash3-GBC/Data/23Sep/usaf/chat.zip}"

# Model override (empty = use container default: qwen2.5:14b)
# V100 32GB budget: 14B uses ~9GB VRAM, leaving ~23GB free
MODEL="${MODEL:-}"

# Output database
DB_PATH="${DB_PATH:-$WORK_DIR/output/world_state_${SLURM_JOB_ID:-$$}.db}"

# Context window (must be >= 8192 for chat-to-cop)
NUM_CTX="${NUM_CTX:-8192}"

# =============================================================================
# Validation
# =============================================================================

if [ ! -f "$CONTAINER" ]; then
    echo "ERROR: Container not found: $CONTAINER"
    echo ""
    echo "Download the HPC .sif from the GitLab release:"
    echo "  https://gitlab.dle.afrl.af.mil/c2es1/mash/chat-to-cop/-/releases"
    echo ""
    echo "Then transfer to Narwhal:"
    echo "  kinit <username>@HPCMP.HPC.MIL"
    echo "  scp chat-to-cop-hpc_<tag>.sif <username>@narwhal.hpc.mil:\$WORKDIR/"
    exit 1
fi

if [ ! -f "$CHAT_DATA" ] && [ ! -d "$CHAT_DATA" ]; then
    echo "ERROR: Chat data not found: $CHAT_DATA"
    echo "Transfer it: scp -r data/chat/ <username>@narwhal.hpc.mil:\$WORKDIR/chat/"
    exit 1
fi

mkdir -p "$(dirname "$DB_PATH")"
mkdir -p "$WORK_DIR/output"

# =============================================================================
# Job information
# =============================================================================

echo "=============================================================================="
echo "Chat-to-CoP Replay — Narwhal V100 32GB"
echo "=============================================================================="
echo "Job ID:     ${SLURM_JOB_ID:-interactive}"
echo "Node:       $(hostname)"
echo "GPUs:       ${CUDA_VISIBLE_DEVICES:-not set}"
echo "Container:  $CONTAINER"
echo "Chat data:  $CHAT_DATA"
echo "Output DB:  $DB_PATH"
echo "Model:      ${MODEL:-container default (qwen2.5:14b)}"
echo "Num ctx:    $NUM_CTX"
echo "=============================================================================="

nvidia-smi --query-gpu=name,memory.total,memory.free --format=csv,noheader 2>/dev/null || echo "GPU info unavailable"
echo ""

# =============================================================================
# Start Apptainer instance with Ollama
# =============================================================================

echo "Starting Apptainer instance..."
apptainer instance start \
    --nv \
    --writable-tmpfs \
    --bind "$WORK_DIR:$WORK_DIR" \
    "$CONTAINER" "$INSTANCE_NAME"

# Cleanup on exit
cleanup() {
    echo ""
    echo "Stopping instance..."
    apptainer instance stop "$INSTANCE_NAME" 2>/dev/null || true
}
trap cleanup EXIT

# Start Ollama inside the instance
echo "Starting Ollama server..."
apptainer exec instance://"$INSTANCE_NAME" \
    bash -c "OLLAMA_MODELS=/opt/ollama-models ollama serve &"

# Wait for Ollama
echo "Waiting for Ollama..."
retries=0
until apptainer exec instance://"$INSTANCE_NAME" \
    curl -sf http://127.0.0.1:11434/api/tags > /dev/null 2>&1; do
    retries=$((retries + 1))
    if [ "$retries" -ge 60 ]; then
        echo "ERROR: Ollama did not start after 120 seconds."
        exit 1
    fi
    sleep 2
done

echo "Ollama ready. Available models:"
apptainer exec instance://"$INSTANCE_NAME" \
    curl -sf http://127.0.0.1:11434/api/tags 2>/dev/null | grep -o '"name":"[^"]*"' || true
echo ""

# =============================================================================
# Run the replay pipeline
# =============================================================================

echo "Starting chat-to-cop replay..."
echo "=============================================================================="

# Build replay arguments
REPLAY_ARGS=("$CHAT_DATA" "--db" "$DB_PATH" "--num-ctx" "$NUM_CTX")
[ -n "$MODEL" ] && REPLAY_ARGS+=("--model" "$MODEL")

START_TIME=$(date +%s)

apptainer exec instance://"$INSTANCE_NAME" \
    python -m chat_to_cop.replay "${REPLAY_ARGS[@]}"

EXIT_CODE=$?
END_TIME=$(date +%s)
ELAPSED=$(( END_TIME - START_TIME ))

# =============================================================================
# Job summary
# =============================================================================

echo ""
echo "=============================================================================="
if [ $EXIT_CODE -eq 0 ]; then
    echo "Replay completed successfully!"
    echo "Duration:  $(( ELAPSED / 60 ))m $(( ELAPSED % 60 ))s"
    echo "Output DB: $DB_PATH"
    if [ -f "$DB_PATH" ]; then
        DB_SIZE=$(du -h "$DB_PATH" | cut -f1)
        echo "DB size:   $DB_SIZE"
    fi
    echo ""
    echo "IMPORTANT: Results are in \$WORKDIR which is subject to purging (14-30 days)."
    echo "Archive to \$ARCHIVE_HOME:"
    echo "  cp $DB_PATH \$ARCHIVE_HOME/"
else
    echo "Replay failed with exit code: $EXIT_CODE"
    echo "Check logs: chat-to-cop_${SLURM_JOB_ID}.out / .err"
fi
echo "=============================================================================="

exit $EXIT_CODE

#!/bin/bash
#SBATCH --job-name=c2c-30b
#SBATCH --account=AFRLC2070NSH
#SBATCH --partition=MLA
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --gres=gpu:1
#SBATCH --mem=32G
#SBATCH --time=04:00:00
#SBATCH --output=c2c-30b_%j.out
#SBATCH --error=c2c-30b_%j.err
#
# =============================================================================
# Issue #26: Validate Qwen3-30B-A3B on Narwhal V100-32GB
# =============================================================================
#
# Qwen3-30B-A3B is a Mixture-of-Experts model (30B total, ~3B active params).
# At Q4_K_M quantization it needs ~18GB VRAM, well within V100-32GB budget.
#
# This script:
#   1. Starts Ollama from $WORKDIR (no container needed — native install)
#   2. Pulls the qwen3:30b-a3b model if not already cached
#   3. Creates a Modelfile with num_ctx 8192
#   4. Runs the full 935-message DASH 3 replay
#   5. Saves results to $WORKDIR/output/
#
# Prerequisites:
#   - Ollama binary at $WORKDIR/ollama/bin/ollama
#   - Conda env at $WORKDIR/envs/chat-to-cop
#   - DASH 3 chat data at $WORKDIR/chat/Dash3-GBC/Data/23Sep/usaf/chat.zip
#   - chat-to-cop installed in the conda env
#
# Usage:
#   sbatch scripts/narwhal_30b_test.sh
#
# =============================================================================

set -euo pipefail

# =============================================================================
# Configuration
# =============================================================================

WORK="${WORKDIR:-/p/work1/hsclouse}"
OLLAMA_BIN="$WORK/ollama/bin/ollama"
OLLAMA_PORT=11434
CONDA_ENV="$WORK/envs/chat-to-cop"

# Model config
MODEL_BASE="qwen3:30b-a3b"
MODEL_TAG="qwen3:30b-a3b-8k"
NUM_CTX=8192

# I/O paths
CHAT_DATA="$WORK/chat/Dash3-GBC/Data/23Sep/usaf/chat.zip"
OUTPUT_DIR="$WORK/output"
DB_PATH="$OUTPUT_DIR/world_state_30b_${SLURM_JOB_ID:-$$}.db"
LOG_FILE="$OUTPUT_DIR/replay_30b_${SLURM_JOB_ID:-$$}.log"

# Ollama storage
export OLLAMA_MODELS="$WORK/ollama-models"
export OLLAMA_HOST="127.0.0.1:${OLLAMA_PORT}"

# =============================================================================
# Validation
# =============================================================================

echo "=============================================================================="
echo "Chat-to-CoP: Qwen3-30B-A3B Validation (Issue #26)"
echo "=============================================================================="
echo "Job ID:     ${SLURM_JOB_ID:-interactive}"
echo "Node:       $(hostname)"
echo "GPUs:       ${CUDA_VISIBLE_DEVICES:-not set}"
echo "Model:      $MODEL_TAG ($MODEL_BASE + num_ctx=$NUM_CTX)"
echo "Chat data:  $CHAT_DATA"
echo "Output DB:  $DB_PATH"
echo "Ollama:     $OLLAMA_BIN"
echo "Conda env:  $CONDA_ENV"
echo "=============================================================================="

# GPU info
module load cuda 2>/dev/null || true
nvidia-smi --query-gpu=name,memory.total,memory.free --format=csv,noheader 2>/dev/null || echo "GPU info unavailable"
echo ""

# Validate prerequisites
if [ ! -x "$OLLAMA_BIN" ]; then
    echo "ERROR: Ollama binary not found at $OLLAMA_BIN"
    echo "Install: curl -L https://ollama.com/download/ollama-linux-amd64 -o $OLLAMA_BIN && chmod +x $OLLAMA_BIN"
    exit 1
fi

if [ ! -f "$CHAT_DATA" ]; then
    echo "ERROR: Chat data not found at $CHAT_DATA"
    echo "Transfer from local: scp -r data/chat/ hsclouse@narwhal.navydsrc.hpc.mil:\$WORKDIR/chat/"
    exit 1
fi

mkdir -p "$OUTPUT_DIR"
mkdir -p "$OLLAMA_MODELS"

# =============================================================================
# Activate conda environment
# =============================================================================

echo "Activating conda environment..."
if [ -f "$CONDA_ENV/bin/activate" ]; then
    source "$CONDA_ENV/bin/activate"
elif command -v conda &>/dev/null; then
    eval "$(conda shell.bash hook)"
    conda activate "$CONDA_ENV"
else
    echo "ERROR: Cannot activate conda env at $CONDA_ENV"
    exit 1
fi
echo "Python: $(which python) ($(python --version))"
echo ""

# =============================================================================
# Start Ollama
# =============================================================================

echo "Starting Ollama server..."
"$OLLAMA_BIN" serve &
OLLAMA_PID=$!

cleanup() {
    echo ""
    echo "Stopping Ollama (PID $OLLAMA_PID)..."
    kill "$OLLAMA_PID" 2>/dev/null; wait "$OLLAMA_PID" 2>/dev/null || true
}
trap cleanup EXIT

# Wait for Ollama to be ready
echo "Waiting for Ollama..."
retries=0
until curl -sf "http://127.0.0.1:${OLLAMA_PORT}/api/tags" >/dev/null 2>&1; do
    retries=$((retries + 1))
    if [ "$retries" -ge 60 ]; then
        echo "ERROR: Ollama did not start after 120 seconds"
        exit 1
    fi
    sleep 2
done
echo "Ollama ready."

# =============================================================================
# Pull model (if not already cached)
# =============================================================================

echo ""
echo "Checking for model: $MODEL_BASE"
if "$OLLAMA_BIN" list 2>/dev/null | grep -q "$MODEL_BASE"; then
    echo "Model already cached."
else
    echo "Pulling $MODEL_BASE (this may take 10-20 minutes on first run)..."
    "$OLLAMA_BIN" pull "$MODEL_BASE"
fi

# =============================================================================
# Create Modelfile with 8192 context window
# =============================================================================

MODELFILE_PATH="$OUTPUT_DIR/Modelfile.30b-8k"
cat > "$MODELFILE_PATH" << 'MODELFILE'
FROM qwen3:30b-a3b
PARAMETER num_ctx 8192
MODELFILE

echo ""
echo "Creating custom model tag: $MODEL_TAG (num_ctx=$NUM_CTX)"
"$OLLAMA_BIN" create "$MODEL_TAG" -f "$MODELFILE_PATH"

# Verify model is available
echo ""
echo "Available models:"
"$OLLAMA_BIN" list

# =============================================================================
# Run replay
# =============================================================================

echo ""
echo "=============================================================================="
echo "Starting DASH 3 replay (935 messages, Qwen3-30B-A3B, V100-32GB)"
echo "=============================================================================="

START_TIME=$(date +%s)

python -m chat_to_cop.replay \
    "$CHAT_DATA" \
    --model "$MODEL_TAG" \
    --db "$DB_PATH" \
    --num-ctx "$NUM_CTX" \
    --timeout 180 \
    2>&1 | tee "$LOG_FILE"

EXIT_CODE=${PIPESTATUS[0]}
END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))

# =============================================================================
# Results summary
# =============================================================================

echo ""
echo "=============================================================================="
if [ "$EXIT_CODE" -eq 0 ]; then
    echo "REPLAY COMPLETED SUCCESSFULLY"
else
    echo "REPLAY FAILED (exit code: $EXIT_CODE)"
fi
echo "=============================================================================="
echo "Duration:    $((ELAPSED / 60))m $((ELAPSED % 60))s"
echo "Model:       $MODEL_TAG"
echo "Output DB:   $DB_PATH"

if [ -f "$DB_PATH" ]; then
    DB_SIZE=$(du -h "$DB_PATH" | cut -f1)
    echo "DB size:     $DB_SIZE"

    # Quick stats from the database
    echo ""
    echo "--- Database Summary ---"
    python -c "
import sqlite3, json
db = sqlite3.connect('$DB_PATH')
cur = db.cursor()

# Total updates
total = cur.execute('SELECT COUNT(*) FROM cop_updates').fetchone()[0]
print(f'Total CoPUpdates: {total}')

# By extraction method
methods = cur.execute('SELECT extraction_method, COUNT(*) FROM cop_updates GROUP BY extraction_method ORDER BY COUNT(*) DESC').fetchall()
for m, c in methods:
    print(f'  {m}: {c}')

# Entity/threat/tasking counts
entities = cur.execute('SELECT COUNT(*) FROM cop_updates WHERE json_extract(data, \"\$.entities\") IS NOT NULL').fetchone()[0]
print(f'Updates with entities: {entities}')

# Average confidence
avg_conf = cur.execute('SELECT AVG(confidence) FROM cop_updates WHERE confidence > 0').fetchone()[0]
if avg_conf:
    print(f'Average confidence: {avg_conf:.3f}')

# Mean latency
avg_lat = cur.execute('SELECT AVG(latency_ms) FROM cop_updates WHERE latency_ms > 0').fetchone()[0]
if avg_lat:
    print(f'Mean latency: {avg_lat:.0f}ms ({avg_lat/1000:.1f}s)')

db.close()
" 2>/dev/null || echo "(Could not query database)"
fi

echo ""
echo "IMPORTANT: Archive results before $WORK purge (14-30 days):"
echo "  cp $DB_PATH \$ARCHIVE_HOME/chat-to-cop/"
echo "  cp $LOG_FILE \$ARCHIVE_HOME/chat-to-cop/"
echo "=============================================================================="

exit "$EXIT_CODE"

#!/bin/bash
#SBATCH --job-name=c2c-14b-ab
#SBATCH --account=AFSNW27526RYZ
#SBATCH --qos=standard
#SBATCH --partition=general
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --gres=gpu:v100:1
#SBATCH --mem=32G
#SBATCH --time=04:00:00
#SBATCH --output=c2c-14b-ab_%j.out
#SBATCH --error=c2c-14b-ab_%j.err
#
# =============================================================================
# Issue #52: Multi-backend speaker model A/B on Qwen2.5-14B
# =============================================================================
#
# Runs the DASH 3 replay twice:
#   Run A: WITH speaker models (default)
#   Run B: WITHOUT speaker models (CHAT_TO_COP_USE_SPEAKER_MODELS=false)
#
# Both produce SQLite databases that can be downloaded and compared
# using scripts/eval_speaker_models.py against Opus silver labels.
#
# =============================================================================

set -euo pipefail

WORK="${WORKDIR:-/p/work1/hsclouse}"
OLLAMA_BIN="$WORK/bin/ollama"
OLLAMA_PORT=11434
CONDA_ENV="$WORK/envs/chat-to-cop"
CHAT_DATA="$WORK/chat/Dash3-GBC/Data/23Sep/usaf/chat.zip"
OUTPUT_DIR="$WORK/output"

# Model config — 14B is already cached on Narwhal
MODEL_BASE="qwen2.5:14b"
MODEL_TAG="qwen2.5:14b-8k"
NUM_CTX=8192

# Output paths
JOB_ID="${SLURM_JOB_ID:-$$}"
DB_WITH="$OUTPUT_DIR/replay_14b_with_speakers_${JOB_ID}.db"
DB_WITHOUT="$OUTPUT_DIR/replay_14b_without_speakers_${JOB_ID}.db"
LOG_FILE="$OUTPUT_DIR/replay_14b_ab_${JOB_ID}.log"

export OLLAMA_MODELS="$WORK/ollama-models"
export OLLAMA_HOST="127.0.0.1:${OLLAMA_PORT}"
export PATH="$CONDA_ENV/bin:$PATH"

echo "=============================================================================="
echo "Chat-to-CoP: 14B Speaker Model A/B Experiment (#52)"
echo "=============================================================================="
echo "Job ID:     $JOB_ID"
echo "Node:       $(hostname)"
echo "Account:    AFSNW27526RYZ"
echo "Model:      $MODEL_TAG"
echo "Chat data:  $CHAT_DATA"
echo "Run A DB:   $DB_WITH"
echo "Run B DB:   $DB_WITHOUT"
echo "=============================================================================="

module load cuda 2>/dev/null || true
nvidia-smi --query-gpu=name,memory.total,memory.free --format=csv,noheader 2>/dev/null || echo "GPU info unavailable"
echo ""

# Validate prerequisites
for f in "$OLLAMA_BIN" "$CHAT_DATA" "$CONDA_ENV/bin/python"; do
    if [ ! -f "$f" ]; then
        echo "ERROR: Required file not found: $f"
        exit 1
    fi
done

mkdir -p "$OUTPUT_DIR"

# Install latest source
echo "Installing latest chat-to-cop source..."
cd "$WORK/chat-to-cop"
pip install -e . -q 2>&1 | tail -3
echo ""

# Start Ollama
echo "Starting Ollama server..."
"$OLLAMA_BIN" serve &
OLLAMA_PID=$!
cleanup() {
    echo ""
    echo "Stopping Ollama (PID $OLLAMA_PID)..."
    kill "$OLLAMA_PID" 2>/dev/null; wait "$OLLAMA_PID" 2>/dev/null || true
}
trap cleanup EXIT

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

# Create Modelfile with 8192 context
MODELFILE_PATH="$OUTPUT_DIR/Modelfile.14b-8k"
cat > "$MODELFILE_PATH" << 'MODELFILE'
FROM qwen2.5:14b
PARAMETER num_ctx 8192
MODELFILE

echo "Creating custom model tag: $MODEL_TAG (num_ctx=$NUM_CTX)"
"$OLLAMA_BIN" create "$MODEL_TAG" -f "$MODELFILE_PATH"
echo ""
echo "Available models:"
"$OLLAMA_BIN" list
echo ""

# Warm the model with a single request to avoid cold-start on the first real message
echo "Warming model..."
curl -sf -X POST "http://127.0.0.1:${OLLAMA_PORT}/api/generate" \
    -d '{"model":"'"$MODEL_TAG"'","prompt":"hi","stream":false}' > /dev/null 2>&1 || true
echo "Model warm."
echo ""

# =============================================================================
# Run A: WITH speaker models (default)
# =============================================================================

echo "=============================================================================="
echo "RUN A: WITH speaker models"
echo "=============================================================================="

START_A=$(date +%s)
python -m chat_to_cop.replay \
    "$CHAT_DATA" \
    --model "$MODEL_TAG" \
    --db "$DB_WITH" \
    --num-ctx "$NUM_CTX" \
    --timeout 180 \
    2>&1 | tee -a "$LOG_FILE"
END_A=$(date +%s)
ELAPSED_A=$((END_A - START_A))
echo ""
echo "Run A completed in $((ELAPSED_A / 60))m $((ELAPSED_A % 60))s"
echo ""

# =============================================================================
# Run B: WITHOUT speaker models
# =============================================================================

echo "=============================================================================="
echo "RUN B: WITHOUT speaker models"
echo "=============================================================================="

START_B=$(date +%s)
CHAT_TO_COP_USE_SPEAKER_MODELS=false python -m chat_to_cop.replay \
    "$CHAT_DATA" \
    --model "$MODEL_TAG" \
    --db "$DB_WITHOUT" \
    --num-ctx "$NUM_CTX" \
    --timeout 180 \
    2>&1 | tee -a "$LOG_FILE"
END_B=$(date +%s)
ELAPSED_B=$((END_B - START_B))
echo ""
echo "Run B completed in $((ELAPSED_B / 60))m $((ELAPSED_B % 60))s"
echo ""

# =============================================================================
# Results summary
# =============================================================================

echo "=============================================================================="
echo "A/B EXPERIMENT COMPLETE"
echo "=============================================================================="
echo "Run A (with speakers):    $((ELAPSED_A / 60))m $((ELAPSED_A % 60))s -> $DB_WITH"
echo "Run B (without speakers): $((ELAPSED_B / 60))m $((ELAPSED_B % 60))s -> $DB_WITHOUT"
echo ""

for DB in "$DB_WITH" "$DB_WITHOUT"; do
    if [ -f "$DB" ]; then
        echo "--- $(basename $DB) ---"
        python -c "
import sqlite3
db = sqlite3.connect('$DB')
total = db.execute('SELECT COUNT(*) FROM updates').fetchone()[0]
print(f'  Updates: {total}')
db.close()
" 2>/dev/null || echo "  (could not query)"
    fi
done

echo ""
echo "Next steps:"
echo "  1. Download both databases to your laptop"
echo "  2. Run the comparison:"
echo "     python scripts/eval_speaker_models.py \\"
echo "         --labels data/labels/dash3_silver_labels_opus.jsonl \\"
echo "         --with-speakers <path_to_with_speakers.db> \\"
echo "         --without-speakers <path_to_without_speakers.db>"
echo ""
echo "IMPORTANT: Archive results before purge:"
echo "  cp $DB_WITH \$ARCHIVE_HOME/chat-to-cop/"
echo "  cp $DB_WITHOUT \$ARCHIVE_HOME/chat-to-cop/"
echo "=============================================================================="

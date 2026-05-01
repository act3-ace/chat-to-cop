#!/bin/bash
#SBATCH --account=AFSNW27526RYZ
#SBATCH --qos=standard
#SBATCH --partition=general
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --gres=gpu:v100:1
#SBATCH --mem=32G
#
# =============================================================================
# Speaker Model Sweep — parameterized Slurm array job
# =============================================================================
#
# Set via environment before sbatch (submit_speaker_sweep.py handles this):
#   SWEEP_MODEL_BASE   qwen2.5:3b | qwen2.5:7b | qwen2.5:14b | qwen2.5:32b
#   SWEEP_SPEAKERS     true | false
#   SWEEP_TEMPERATURE  0 | default
#   SWEEP_NAME         e.g. 7b_on_det, 14b_off_stoch
#   SWEEP_NUM_CTX      8192
#   SWEEP_TIMEOUT      120 | 180 | 300
#
# Each array task runs one full DASH-3 replay and produces one SQLite DB.
# Task isolation: unique Ollama port, unique DB path, unique model tag.
# =============================================================================

set -euo pipefail

WORK="${WORKDIR:-/p/work1/$USER}"
OLLAMA_BIN="$WORK/bin/ollama"
CONDA_ENV="$WORK/envs/chat-to-cop"
CHAT_DATA="$WORK/chat/Dash3-GBC/Data/23Sep/usaf/chat.zip"
OUTPUT_DIR="$WORK/output/sweep"

# Sweep parameters (from environment)
MODEL_BASE="${SWEEP_MODEL_BASE:?SWEEP_MODEL_BASE not set}"
SPEAKERS="${SWEEP_SPEAKERS:?SWEEP_SPEAKERS not set}"
TEMPERATURE="${SWEEP_TEMPERATURE:-default}"
SWEEP_NAME="${SWEEP_NAME:?SWEEP_NAME not set}"
NUM_CTX="${SWEEP_NUM_CTX:-8192}"
TIMEOUT="${SWEEP_TIMEOUT:-180}"

# Array task identity
ARRAY_JOB="${SLURM_ARRAY_JOB_ID:-$$}"
TASK_ID="${SLURM_ARRAY_TASK_ID:-1}"

# Unique Ollama port per task (avoids collisions if multiple tasks land on same node)
OLLAMA_PORT=$((11434 + TASK_ID))

# Unique output paths
DB_PATH="$OUTPUT_DIR/sweep_${SWEEP_NAME}_${ARRAY_JOB}_${TASK_ID}.db"
LOG_FILE="$OUTPUT_DIR/sweep_${SWEEP_NAME}_${ARRAY_JOB}_${TASK_ID}.log"

# Model tag: include temperature label so Modelfile variants coexist
if [ "$TEMPERATURE" = "0" ]; then
    MODEL_TAG="${MODEL_BASE}-8k-det"
    TEMP_LABEL="deterministic (temp=0)"
else
    MODEL_TAG="${MODEL_BASE}-8k"
    TEMP_LABEL="stochastic (default temp)"
fi

# Speaker toggle
export CHAT_TO_COP_USE_SPEAKER_MODELS="$SPEAKERS"

# Ollama config
export OLLAMA_MODELS="$WORK/ollama-models"
export OLLAMA_HOST="127.0.0.1:${OLLAMA_PORT}"
export PATH="$CONDA_ENV/bin:$PATH"

echo "=============================================================================="
echo "Speaker Sweep: $SWEEP_NAME  task=$TASK_ID"
echo "=============================================================================="
echo "Job:         $ARRAY_JOB  task=$TASK_ID"
echo "Node:        $(hostname)"
echo "Model:       $MODEL_TAG (num_ctx=$NUM_CTX)"
echo "Speakers:    $SPEAKERS"
echo "Temperature: $TEMP_LABEL"
echo "DB:          $DB_PATH"
echo "Ollama port: $OLLAMA_PORT"
echo "=============================================================================="

module load cuda 2>/dev/null || true
nvidia-smi --query-gpu=name,memory.total,memory.free --format=csv,noheader 2>/dev/null || echo "GPU info unavailable"

# Validate prerequisites
for f in "$OLLAMA_BIN" "$CHAT_DATA" "$CONDA_ENV/bin/python"; do
    [ -f "$f" ] || { echo "ERROR: $f not found"; exit 1; }
done

mkdir -p "$OUTPUT_DIR"

cd "$WORK/chat-to-cop"
echo "Source: $(git log --oneline -1 2>/dev/null || echo 'not a git checkout')"
python -c "import chat_to_cop; print('  chat_to_cop OK')" || { echo "ERROR: import failed"; exit 1; }
echo ""

# Start Ollama on the unique port
"$OLLAMA_BIN" serve &
OLLAMA_PID=$!

cleanup() {
    echo "Stopping Ollama (PID $OLLAMA_PID, port $OLLAMA_PORT)..."
    kill "$OLLAMA_PID" 2>/dev/null; wait "$OLLAMA_PID" 2>/dev/null || true
}
trap cleanup EXIT

# Wait for Ollama
retries=0
until curl -sf "http://127.0.0.1:${OLLAMA_PORT}/api/tags" >/dev/null 2>&1; do
    retries=$((retries + 1))
    [ "$retries" -ge 60 ] && { echo "ERROR: Ollama did not start"; exit 1; }
    sleep 2
done

# Create model variant with appropriate temperature
MODELFILE_PATH="$OUTPUT_DIR/.modelfile_${SWEEP_NAME}_${TASK_ID}"
if [ "$TEMPERATURE" = "0" ]; then
    cat > "$MODELFILE_PATH" <<MODELFILE
FROM $MODEL_BASE
PARAMETER num_ctx $NUM_CTX
PARAMETER temperature 0
MODELFILE
else
    cat > "$MODELFILE_PATH" <<MODELFILE
FROM $MODEL_BASE
PARAMETER num_ctx $NUM_CTX
MODELFILE
fi

"$OLLAMA_BIN" create "$MODEL_TAG" -f "$MODELFILE_PATH" 2>&1 | tail -1
rm -f "$MODELFILE_PATH"

# Warm the model (first inference is always slow due to GPU memory allocation)
curl -sf -X POST "http://127.0.0.1:${OLLAMA_PORT}/api/generate" \
    -d '{"model":"'"$MODEL_TAG"'","prompt":"hi","stream":false}' > /dev/null 2>&1 || true

echo "Model warm. Starting replay..."
echo ""

# Run replay
START=$(date +%s)
python -m chat_to_cop.replay \
    "$CHAT_DATA" \
    --url "http://127.0.0.1:${OLLAMA_PORT}/v1" \
    --model "$MODEL_TAG" \
    --db "$DB_PATH" \
    --num-ctx "$NUM_CTX" \
    --timeout "$TIMEOUT" \
    2>&1 | tee "$LOG_FILE"
EXIT_CODE=${PIPESTATUS[0]}
END=$(date +%s)
ELAPSED=$((END - START))

echo ""
echo "=============================================================================="
echo "Task $TASK_ID ($SWEEP_NAME): $([ $EXIT_CODE -eq 0 ] && echo DONE || echo FAILED)"
echo "Duration: $((ELAPSED / 60))m $((ELAPSED % 60))s"
echo "DB: $DB_PATH"
echo "=============================================================================="

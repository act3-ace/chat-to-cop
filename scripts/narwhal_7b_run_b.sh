#!/bin/bash
#SBATCH --job-name=c2c-7b-B
#SBATCH --account=AFSNW27526RYZ
#SBATCH --qos=standard
#SBATCH --partition=general
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --gres=gpu:v100:1
#SBATCH --mem=32G
#SBATCH --time=04:00:00
#SBATCH --output=c2c-7b-B_%j.out
#SBATCH --error=c2c-7b-B_%j.err
#
# =============================================================================
# Issue #35 / #52: 7B Run B — WITHOUT speaker models (Qwen2.5-7B, 8K context)
# =============================================================================
#
# Re-run after MR !88 fixed the config-bypass bug. Settings IDENTICAL to
# Run A except CHAT_TO_COP_USE_SPEAKER_MODELS=false. Post-fix this env
# var is actually honored — verified by the regression tests in
# tests/test_supervisor.py::TestSupervisorAgentConfigPlumbing.
# =============================================================================

set -euo pipefail

# THE ONLY DIFFERENCE FROM RUN A:
export CHAT_TO_COP_USE_SPEAKER_MODELS=false

WORK="${WORKDIR:-/p/work1/hsclouse}"
OLLAMA_BIN="$WORK/bin/ollama"
OLLAMA_PORT=11434
CONDA_ENV="$WORK/envs/chat-to-cop"
CHAT_DATA="$WORK/chat/Dash3-GBC/Data/23Sep/usaf/chat.zip"
OUTPUT_DIR="$WORK/output"
ARCHIVE_DIR="${ARCHIVE_HOME:-$HOME}/chat-to-cop"

MODEL_BASE="qwen2.5:7b"
MODEL_TAG="qwen2.5:7b-8k"
NUM_CTX=8192

JOB_ID="${SLURM_JOB_ID:-$$}"
DB_PATH="$OUTPUT_DIR/replay_7b_without_speakers_${JOB_ID}.db"
LOG_FILE="$OUTPUT_DIR/replay_7b_runB_${JOB_ID}.log"

export OLLAMA_MODELS="$WORK/ollama-models"
export OLLAMA_HOST="127.0.0.1:${OLLAMA_PORT}"
export PATH="$CONDA_ENV/bin:$PATH"

echo "=============================================================================="
echo "Chat-to-CoP: 7B Run B — WITHOUT Speaker Models (#35 re-run after !88 fix)"
echo "=============================================================================="
echo "Job ID:     $JOB_ID"
echo "Node:       $(hostname)"
echo "Account:    AFSNW27526RYZ"
echo "Model:      $MODEL_TAG (num_ctx=$NUM_CTX)"
echo "Chat data:  $CHAT_DATA"
echo "Output DB:  $DB_PATH"
echo "Log file:   $LOG_FILE"
echo "Archive:    $ARCHIVE_DIR"
echo "Speakers:   DISABLED (Run B)"
echo "CHAT_TO_COP_USE_SPEAKER_MODELS=$CHAT_TO_COP_USE_SPEAKER_MODELS"
echo "=============================================================================="

module load cuda 2>/dev/null || true
nvidia-smi --query-gpu=name,memory.total,memory.free,driver_version --format=csv,noheader 2>/dev/null || echo "GPU info unavailable"
echo ""
echo "Ollama version: $($OLLAMA_BIN --version 2>&1 | grep -o '[0-9.].*' || echo unknown)"
echo "Python: $(python --version 2>&1)"
echo ""

for f in "$OLLAMA_BIN" "$CHAT_DATA" "$CONDA_ENV/bin/python"; do
    if [ ! -f "$f" ]; then
        echo "ERROR: Required file not found: $f"
        exit 1
    fi
done

mkdir -p "$OUTPUT_DIR"
# $ARCHIVE_HOME is set system-wide on Narwhal but its symlink target
# (/archive/g/hsclouse) was never provisioned, so the default
# /archive/home/hsclouse/chat-to-cop isn't writable. Detect and fall back
# to $HOME/chat-to-cop so the cleanup cp at exit actually works.
if ! mkdir -p "$ARCHIVE_DIR" 2>/dev/null; then
    echo "WARNING: Cannot create $ARCHIVE_DIR — falling back to \$HOME/chat-to-cop"
    ARCHIVE_DIR="$HOME/chat-to-cop"
    mkdir -p "$ARCHIVE_DIR"
fi

# NOTE: Do NOT `pip install -e .` here. The conda env is shared across
# concurrent jobs, so simultaneous editable installs race and three-of-four
# jobs die. Pre-install once before submitting (`cd $WORKDIR/chat-to-cop &&
# git pull && pip install -e . -q`). Each job just imports from the env.
cd "$WORK/chat-to-cop"
echo "Source state:"
git log --oneline -1 2>/dev/null || echo "  (not a git checkout)"
python -c "import chat_to_cop, inspect; print('  chat_to_cop OK at', chat_to_cop.__file__)"
echo ""

# Sanity check: post-!88 the supervisor must actually thread use_speaker_models
echo "Sanity check: env-var plumbing test..."
python -m pytest tests/test_supervisor.py::TestSupervisorAgentConfigPlumbing -q 2>&1 | tail -5
echo ""

echo "Starting Ollama server..."
"$OLLAMA_BIN" serve &
OLLAMA_PID=$!

cleanup() {
    echo ""
    echo "=============================================================================="
    echo "CLEANUP: Archiving results..."
    echo "=============================================================================="
    if [ -f "$DB_PATH" ]; then
        cp "$DB_PATH" "$ARCHIVE_DIR/" 2>/dev/null && echo "Archived: $(basename $DB_PATH)" || echo "Archive failed for DB"
    fi
    if [ -f "$LOG_FILE" ]; then
        cp "$LOG_FILE" "$ARCHIVE_DIR/" 2>/dev/null && echo "Archived: $(basename $LOG_FILE)" || echo "Archive failed for log"
    fi
    for f in "$WORK/chat-to-cop/c2c-7b-B_${JOB_ID}".{out,err}; do
        [ -f "$f" ] && cp "$f" "$ARCHIVE_DIR/" 2>/dev/null && echo "Archived: $(basename $f)"
    done
    echo "Stopping Ollama (PID $OLLAMA_PID)..."
    kill "$OLLAMA_PID" 2>/dev/null; wait "$OLLAMA_PID" 2>/dev/null || true
    echo "Cleanup complete."
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

MODELFILE_PATH="$OUTPUT_DIR/Modelfile.7b-8k"
cat > "$MODELFILE_PATH" << 'MODELFILE'
FROM qwen2.5:7b
PARAMETER num_ctx 8192
MODELFILE

echo "Creating custom model tag: $MODEL_TAG"
"$OLLAMA_BIN" create "$MODEL_TAG" -f "$MODELFILE_PATH"
echo ""
"$OLLAMA_BIN" list
echo ""

echo "Warming model..."
curl -sf -X POST "http://127.0.0.1:${OLLAMA_PORT}/api/generate" \
    -d '{"model":"'"$MODEL_TAG"'","prompt":"hi","stream":false}' > /dev/null 2>&1 || true
echo "Model warm."
echo ""

echo "=============================================================================="
echo "STARTING RUN B: WITHOUT speaker models"
echo "=============================================================================="

START=$(date +%s)
python -m chat_to_cop.replay \
    "$CHAT_DATA" \
    --model "$MODEL_TAG" \
    --db "$DB_PATH" \
    --num-ctx "$NUM_CTX" \
    --timeout 120 \
    2>&1 | tee "$LOG_FILE"
EXIT_CODE=${PIPESTATUS[0]}
END=$(date +%s)
ELAPSED=$((END - START))

echo ""
echo "=============================================================================="
if [ "$EXIT_CODE" -eq 0 ]; then
    echo "RUN B COMPLETED SUCCESSFULLY"
else
    echo "RUN B FINISHED (exit code: $EXIT_CODE)"
fi
echo "=============================================================================="
echo "Duration:    $((ELAPSED / 60))m $((ELAPSED % 60))s"
echo "Model:       $MODEL_TAG"
echo "Output DB:   $DB_PATH"
echo ""
echo "Next step: Download both 7B databases and run comparison:"
echo "  python scripts/eval_speaker_models.py \\"
echo "      --labels data/labels/dash3_silver_labels_opus.jsonl \\"
echo "      --with-speakers <Run_A_db> \\"
echo "      --without-speakers <Run_B_db>"
echo "=============================================================================="

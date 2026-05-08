#!/bin/bash
#
# test-hpc.sh -- Smoke test for chat-to-cop on DSRC HPC (Apptainer + GPU)
#
# Tests: Apptainer available, SIF exists, GPU visible, Ollama starts,
#        baked model loads, LLM inference works, chat-to-cop importable
#
# Usage (interactive, from $WORKDIR):
#   bash test-hpc.sh
#
# Usage (SLURM - Narwhal/Raider):
#   sbatch --account=<acct> --partition=standard --gres=gpu:1 --mem=16G --time=00:30:00 test-hpc.sh
#
#SBATCH --job-name=c2c-test
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --gres=gpu:1
#SBATCH --mem=16G
#SBATCH --time=00:30:00
#SBATCH --output=c2c-test_%j.out
#SBATCH --error=c2c-test_%j.err

set -e

PASS=0
FAIL=0
SKIP=0

pass() { echo "  [PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "  [FAIL] $1"; FAIL=$((FAIL + 1)); }
skip() { echo "  [SKIP] $1"; SKIP=$((SKIP + 1)); }

# Determine working directory
if [ -n "$SLURM_SUBMIT_DIR" ]; then
    WORK_DIR="${WORKDIR:-$SLURM_SUBMIT_DIR}"
else
    WORK_DIR="${WORKDIR:-.}"
fi

SIF="${SIF_IMAGE:-$WORK_DIR/chat-to-cop-hpc.sif}"
INSTANCE_NAME="c2c-test-$$"

mkdir -p "$WORK_DIR/output"

echo "========================================"
echo "Chat-to-CoP HPC Smoke Test"
echo "========================================"
echo "Node:      $(hostname)"
echo "SIF:       $SIF"
echo "Work dir:  $WORK_DIR"
if [ -n "$SLURM_JOB_ID" ]; then
    echo "SLURM Job: $SLURM_JOB_ID"
fi
echo "========================================"
echo ""

# ── 1. Apptainer/Singularity available ──────────────────────────────
echo "1. Container runtime"
module load apptainer 2>/dev/null || module load singularity 2>/dev/null || true
if command -v apptainer &> /dev/null; then
    pass "Apptainer found ($(apptainer --version))"
elif command -v singularity &> /dev/null; then
    pass "Singularity found ($(singularity --version))"
    apptainer() { singularity "$@"; }
else
    fail "Neither Apptainer nor Singularity found"
    echo "Try: module load apptainer"
    exit 1
fi

# ── 2. SIF image exists ─────────────────────────────────────────────
echo "2. SIF image"
if [ -f "$SIF" ]; then
    SIF_SIZE=$(du -h "$SIF" | cut -f1)
    pass "SIF image found ($SIF_SIZE)"
else
    fail "SIF image not found at $SIF"
    echo "   Download from GitLab releases:"
    echo "   https://gitlab.dle.afrl.af.mil/c2es1/mash/chat-to-cop/-/releases"
    exit 1
fi

# ── 3. GPU check ────────────────────────────────────────────────────
echo "3. GPU"
module load cuda 2>/dev/null || true
if command -v nvidia-smi &> /dev/null; then
    GPU_INFO=$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null || echo "unknown")
    pass "GPU available: $GPU_INFO"
else
    skip "nvidia-smi not found (CPU-only test)"
fi

# ── 4. Start Apptainer instance ─────────────────────────────────────
echo "4. Apptainer instance"
apptainer instance start --nv --writable-tmpfs "$SIF" "$INSTANCE_NAME" 2>&1
if apptainer instance list 2>/dev/null | grep -q "$INSTANCE_NAME"; then
    pass "Instance started: $INSTANCE_NAME"
else
    fail "Instance failed to start"
    exit 1
fi

# Cleanup trap
cleanup() {
    echo ""
    echo "Cleaning up..."
    apptainer instance stop "$INSTANCE_NAME" 2>/dev/null || true
}
trap cleanup EXIT

# ── 5. Python + chat-to-cop importable ──────────────────────────────
echo "5. Python environment"
PY_VER=$(apptainer exec instance://"$INSTANCE_NAME" python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "")
if [ -n "$PY_VER" ]; then
    pass "Python $PY_VER"
else
    fail "Python not found"
fi

IMPORT_TEST=$(apptainer exec instance://"$INSTANCE_NAME" python -c "import chat_to_cop; print('OK')" 2>/dev/null || echo "FAIL")
if [ "$IMPORT_TEST" = "OK" ]; then
    pass "chat-to-cop package importable"
else
    fail "chat-to-cop package not importable"
fi

# ── 6. Ollama binary ────────────────────────────────────────────────
echo "6. Ollama binary"
if apptainer exec instance://"$INSTANCE_NAME" ollama --version 2>/dev/null; then
    pass "Ollama binary present"
else
    fail "Ollama binary not found in container"
fi

# ── 7. Start Ollama and check health ────────────────────────────────
echo "7. Ollama server"
apptainer exec instance://"$INSTANCE_NAME" \
    bash -c "OLLAMA_MODELS=/opt/ollama-models ollama serve &" 2>/dev/null

retries=0
until apptainer exec instance://"$INSTANCE_NAME" \
    curl -sf http://127.0.0.1:11434/api/tags > /dev/null 2>&1; do
    retries=$((retries + 1))
    if [ "$retries" -ge 30 ]; then
        fail "Ollama did not start after 60 seconds"
        break
    fi
    sleep 2
done

if apptainer exec instance://"$INSTANCE_NAME" \
    curl -sf http://127.0.0.1:11434/api/tags > /dev/null 2>&1; then
    pass "Ollama API responding"
fi

# ── 8. Baked model check ────────────────────────────────────────────
echo "8. Baked models"
TAGS=$(apptainer exec instance://"$INSTANCE_NAME" \
    curl -sf http://127.0.0.1:11434/api/tags 2>/dev/null || echo "{}")

if echo "$TAGS" | grep -q "qwen2.5:7b"; then
    pass "Primary model available: qwen2.5:7b"
else
    fail "Primary model qwen2.5:7b not found — CI build may have failed"
fi

if echo "$TAGS" | grep -q "qwen2.5:14b"; then
    pass "Upgrade model available: qwen2.5:14b"
else
    skip "Upgrade model qwen2.5:14b not found (optional)"
fi

# ── 9. LLM inference (GPU) ──────────────────────────────────────────
echo "9. LLM inference (GPU — may take 10-30s on first load)"
RESPONSE=$(apptainer exec instance://"$INSTANCE_NAME" \
    curl -sf --max-time 120 http://127.0.0.1:11434/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{
        "model": "qwen2.5:7b",
        "messages": [{"role": "user", "content": "Reply with exactly: SMOKE TEST OK"}],
        "max_tokens": 20
    }' 2>/dev/null || echo "TIMEOUT")

if [ "$RESPONSE" = "TIMEOUT" ]; then
    fail "LLM inference timed out after 120 seconds"
elif echo "$RESPONSE" | grep -q "choices"; then
    pass "LLM inference succeeded (GPU)"
else
    fail "LLM inference returned unexpected response"
fi

# ── 10. Structured extraction test ──────────────────────────────────
echo "10. Structured extraction (instructor + Pydantic)"
EXTRACT_RESULT=$(apptainer exec instance://"$INSTANCE_NAME" \
    python -c "
import asyncio
from chat_to_cop.backend.openai_compat import OpenAICompatBackend
from chat_to_cop.models.messages import IRCMessage
from datetime import datetime

async def test():
    backend = OpenAICompatBackend(
        base_url='http://127.0.0.1:11434/v1',
        model='qwen2.5:7b',
        api_key='not-needed',
    )
    msg = IRCMessage(
        channel='#wf_bc', nick='WF_BC1', content='BLUE 1 airborne from KRAP at 1430Z',
        timestamp=datetime.now(), raw='test',
    )
    result = await backend.extract(msg, context=[])
    print(f'OK: {len(result.entities)} entities, conf={result.confidence:.2f}')
    return True

try:
    asyncio.run(test())
except Exception as e:
    print(f'EXTRACT_FAIL: {e}')
" 2>&1 || echo "EXTRACT_FAIL")

if echo "$EXTRACT_RESULT" | grep -q "^OK:"; then
    pass "Structured extraction working: $EXTRACT_RESULT"
else
    fail "Structured extraction failed: $EXTRACT_RESULT"
fi

# ── Summary ──────────────────────────────────────────────────────────
echo ""
echo "========================================"
echo "Results: $PASS passed, $FAIL failed, $SKIP skipped"
echo "========================================"

if [ "$FAIL" -eq 0 ]; then
    echo ""
    echo "All tests passed! Submit a replay job:"
    echo "  sbatch deploy/dsrc/submit_slurm.sh"
fi

[ "$FAIL" -eq 0 ] && exit 0 || exit 1

#!/usr/bin/env bash
# launch-vllm-chat2cop.sh -- Start vLLM on an AG GPU node for chat-to-cop.
#
# Configures and launches vLLM with Qwen2.5-14B-Instruct-AWQ by default.
# Supports both Docker and native Python paths; auto-detects which is
# available.
#
# Based on Jennifer Carlet's scripts:
#   https://gitlab.dle.afrl.af.mil/analytics-gateway/llms-on-ag
#
# Usage:
#   ./launch-vllm-chat2cop.sh              # Docker if available, else native
#   MODEL_NAME=Qwen/Qwen3-0.6B ./launch-vllm-chat2cop.sh   # override model
#   VLLM_MODE=native ./launch-vllm-chat2cop.sh              # force native
#
# Environment variables (all overridable):
#   MODEL_NAME        -- HuggingFace model ID (default: Qwen/Qwen2.5-14B-Instruct-AWQ)
#   MAX_MODEL_LEN     -- Context window length (default: 8192)
#   VLLM_PORT         -- Port to serve on (default: 8000)
#   VLLM_HOST         -- Bind address (default: 0.0.0.0)
#   HF_CACHE_DIR      -- Where to cache model weights (default: /tmp/huggingface)
#   TRUST_REMOTE_CODE -- Trust remote code in model repo (default: 1)
#   VLLM_IMAGE        -- Docker image (default: vllm/vllm-openai:v0.19.1-cu130)
#   VLLM_MODE         -- "docker" | "native" | "auto" (default: auto)
#   GPU_MEMORY_UTIL   -- Fraction of GPU memory to use (default: 0.90)

set -euo pipefail

# ── Defaults ──────────────────────────────────────────────────────────

MODEL_NAME="${MODEL_NAME:-Qwen/Qwen2.5-14B-Instruct-AWQ}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-8192}"
VLLM_PORT="${VLLM_PORT:-8000}"
VLLM_HOST="${VLLM_HOST:-0.0.0.0}"
HF_CACHE_DIR="${HF_CACHE_DIR:-/tmp/huggingface}"
TRUST_REMOTE_CODE="${TRUST_REMOTE_CODE:-1}"
VLLM_IMAGE="${VLLM_IMAGE:-vllm/vllm-openai:v0.19.1-cu130}"
VLLM_MODE="${VLLM_MODE:-auto}"
GPU_MEMORY_UTIL="${GPU_MEMORY_UTIL:-0.90}"
LOG_FILE="/tmp/vllm-chat2cop.log"
PID_FILE="/tmp/vllm-chat2cop.pid"

# T4-specific flags, populated by detect_gpu()
T4_EXTRA_ARGS=""

# ── GPU detection ─────────────────────────────────────────────────────

detect_gpu() {
    if ! command -v nvidia-smi &>/dev/null; then
        echo "ERROR: nvidia-smi not found. Are NVIDIA drivers installed?" >&2
        echo "  On AG, run:  sudo apt-get install -y nvidia-driver-535 && sudo modprobe nvidia" >&2
        exit 1
    fi

    local gpu_name
    gpu_name=$(nvidia-smi --query-gpu=gpu_name --format=csv,noheader 2>/dev/null | head -1)
    local gpu_mem
    gpu_mem=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1)
    GPU_COUNT=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | grep -v "^$" | wc -l)

    if [ -z "$gpu_name" ]; then
        echo "ERROR: nvidia-smi found no GPU. Check driver status." >&2
        exit 1
    fi

    echo "GPU detected: ${gpu_name} (${gpu_mem} MiB), count: ${GPU_COUNT}"

    # Estimate VRAM usage for the model
    case "$MODEL_NAME" in
        *AWQ*|*awq*|*GPTQ*|*gptq*)
            echo "  Model: ${MODEL_NAME} (quantized -- ~9 GB VRAM estimated)"
            ;;
        *14B*|*14b*)
            echo "  Model: ${MODEL_NAME} (full precision -- ~28 GB VRAM estimated)"
            echo "  WARNING: 14B full precision needs V100 32GB or better" >&2
            ;;
        *32B*|*32b*)
            echo "  Model: ${MODEL_NAME} (large -- may not fit on single GPU)"
            ;;
        *)
            echo "  Model: ${MODEL_NAME}"
            ;;
    esac

    # T4-specific tuning (compute capability 7.5)
    # From Jennifer Carlet's benchmarks: --dtype float16 required for T4,
    # --max-num-seqs 16 reduces memory pressure. Triton shared memory
    # exhaustion is a known issue on CC <8.0 GPUs.
    # Ref: gitlab.dle.afrl.af.mil/analytics-gateway/llms-on-ag
    if echo "$gpu_name" | grep -qi "T4"; then
        echo "  T4 detected (CC 7.5) -- applying T4-specific flags"
        T4_EXTRA_ARGS="--dtype float16 --max-num-seqs 16"

        case "$MODEL_NAME" in
            *14B*|*14b*)
                if ! echo "$MODEL_NAME" | grep -qiE "AWQ|GPTQ|bnb|4bit"; then
                    echo "  WARNING: 14B full-precision may not fit on T4 (16 GB)." >&2
                    echo "  Consider Qwen/Qwen2.5-14B-Instruct-AWQ instead." >&2
                fi
                ;;
        esac
    fi
}

# ── Mode selection ────────────────────────────────────────────────────

select_mode() {
    if [ "$VLLM_MODE" = "docker" ]; then
        if ! command -v docker &>/dev/null; then
            echo "ERROR: VLLM_MODE=docker but docker is not installed." >&2
            exit 1
        fi
        echo "docker"
    elif [ "$VLLM_MODE" = "native" ]; then
        echo "native"
    elif [ "$VLLM_MODE" = "auto" ]; then
        if command -v docker &>/dev/null && docker info &>/dev/null 2>&1; then
            echo "docker"
        else
            echo "native"
        fi
    else
        echo "ERROR: VLLM_MODE must be 'docker', 'native', or 'auto'." >&2
        exit 1
    fi
}

# ── Docker launch ─────────────────────────────────────────────────────

launch_docker() {
    echo ""
    echo "Starting vLLM via Docker..."
    echo "  Image:    ${VLLM_IMAGE}"
    echo "  Model:    ${MODEL_NAME}"
    echo "  Context:  ${MAX_MODEL_LEN}"
    echo "  Port:     ${VLLM_PORT}"
    echo "  Cache:    ${HF_CACHE_DIR}"
    echo ""

    mkdir -p "${HF_CACHE_DIR}"

    local trust_flag=""
    if [ "${TRUST_REMOTE_CODE}" = "1" ]; then
        trust_flag="--trust-remote-code"
    fi

    # Remove any stopped container with the same name from a previous run
    docker rm vllm-chat2cop 2>/dev/null || true

    # Scale shared memory for multi-GPU setups
    local shm_size="4g"
    local tp_args=""
    if [ "${GPU_COUNT:-1}" -gt 1 ]; then
        shm_size="$((GPU_COUNT * 3))g"
        tp_args="--tensor-parallel-size ${GPU_COUNT}"
        echo "  Multi-GPU: tensor-parallel-size=${GPU_COUNT}, shm=${shm_size}"
    fi

    docker run -d \
        --name vllm-chat2cop \
        --gpus all \
        --shm-size "${shm_size}" \
        -p "${VLLM_PORT}:8000" \
        -v "${HF_CACHE_DIR}:/root/.cache/huggingface" \
        -e "HUGGING_FACE_HUB_TOKEN=${HUGGING_FACE_HUB_TOKEN:-}" \
        -e "VLLM_WORKER_MULTIPROC_METHOD=spawn" \
        -e "VLLM_NO_USAGE_STATS=1" \
        "${VLLM_IMAGE}" \
        --model "${MODEL_NAME}" \
        --max-model-len "${MAX_MODEL_LEN}" \
        --gpu-memory-utilization "${GPU_MEMORY_UTIL}" \
        ${T4_EXTRA_ARGS:---dtype auto} \
        ${trust_flag} \
        ${tp_args}

    echo ""
    echo "Container started: vllm-chat2cop"
    echo "Logs: docker logs -f vllm-chat2cop"
}

# ── Native launch ─────────────────────────────────────────────────────

launch_native() {
    echo ""
    echo "Starting vLLM natively (no Docker)..."
    echo ""
    echo "  If vllm is not installed, see Jennifer Carlet's install script:"
    echo "    https://gitlab.dle.afrl.af.mil/analytics-gateway/llms-on-ag"
    echo "    vllm_scripts/install_build_deps.sh  (CUDA 12.4 + python3-dev)"
    echo "    pip install vllm                     (or: uv pip install vllm)"
    echo ""

    if ! python3 -c "import vllm" 2>/dev/null; then
        echo "ERROR: vllm Python package not found." >&2
        echo "Install with:  pip install vllm" >&2
        echo "Or for CUDA 12.4: pip install vllm --extra-index-url https://download.pytorch.org/whl/cu124" >&2
        exit 1
    fi

    mkdir -p "${HF_CACHE_DIR}"
    export HF_HOME="${HF_CACHE_DIR}"

    echo "  Model:    ${MODEL_NAME}"
    echo "  Context:  ${MAX_MODEL_LEN}"
    echo "  Port:     ${VLLM_PORT}"
    echo "  Cache:    ${HF_CACHE_DIR}"
    echo ""

    local trust_flag=""
    if [ "${TRUST_REMOTE_CODE}" = "1" ]; then
        trust_flag="--trust-remote-code"
    fi

    local tp_args=""
    if [ "${GPU_COUNT:-1}" -gt 1 ]; then
        tp_args="--tensor-parallel-size ${GPU_COUNT}"
        echo "  Multi-GPU: tensor-parallel-size=${GPU_COUNT}"
    fi

    export VLLM_WORKER_MULTIPROC_METHOD=spawn
    export VLLM_NO_USAGE_STATS=1

    nohup python3 -m vllm.entrypoints.openai.api_server \
        --model "${MODEL_NAME}" \
        --max-model-len "${MAX_MODEL_LEN}" \
        --host "${VLLM_HOST}" \
        --port "${VLLM_PORT}" \
        --gpu-memory-utilization "${GPU_MEMORY_UTIL}" \
        ${T4_EXTRA_ARGS:---dtype auto} \
        ${trust_flag} \
        ${tp_args} \
        > "${LOG_FILE}" 2>&1 &

    VLLM_PID=$!
    echo "${VLLM_PID}" > "${PID_FILE}"
    echo "vLLM PID: ${VLLM_PID} (logging to ${LOG_FILE})"
}

# ── Health check ──────────────────────────────────────────────────────

wait_for_health() {
    local url="http://127.0.0.1:${VLLM_PORT}/v1/models"
    local max_wait=600  # 10 minutes -- first run downloads ~9 GB model
    local interval=5
    local elapsed=0

    echo ""
    echo "Waiting for vLLM to become healthy (${url})..."
    echo "  This may take several minutes on first run (model download)."
    echo ""

    while [ $elapsed -lt $max_wait ]; do
        if curl -sf "${url}" >/dev/null 2>&1; then
            echo ""
            echo "vLLM is ready."
            echo ""
            echo "  API endpoint:  http://127.0.0.1:${VLLM_PORT}/v1"
            echo "  Models:        $(curl -sf "${url}" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(", ".join(m["id"] for m in d.get("data",[])))' 2>/dev/null || echo '(check manually)')"
            echo ""
            echo "  For chat-to-cop, set:"
            echo "    export CHAT_TO_COP_LLM_URL=http://127.0.0.1:${VLLM_PORT}/v1"
            echo "    export CHAT_TO_COP_LLM_MODEL=${MODEL_NAME}"
            echo "    export CHAT_TO_COP_LLM_IS_OLLAMA=false"
            echo ""
            return 0
        fi
        printf "."
        sleep $interval
        elapsed=$((elapsed + interval))
    done

    echo ""
    echo "ERROR: vLLM did not become healthy within ${max_wait}s." >&2
    echo "Check logs:" >&2
    echo "  Docker:  docker logs vllm-chat2cop" >&2
    echo "  Native:  tail -50 ${LOG_FILE}" >&2
    return 1
}

# ── Main ──────────────────────────────────────────────────────────────

main() {
    echo "================================================================"
    echo "  chat-to-cop vLLM launcher for Analytics Gateway"
    echo "================================================================"
    echo ""

    # ── Idempotent: skip if already running ──────────────────────────
    if [ -f "${PID_FILE}" ]; then
        OLD_PID=$(cat "${PID_FILE}")
        if kill -0 "${OLD_PID}" 2>/dev/null; then
            echo "vLLM already running (PID ${OLD_PID})."
            echo "  To restart: kill ${OLD_PID} && bash $0"
            if curl -sf "http://127.0.0.1:${VLLM_PORT}/v1/models" >/dev/null 2>&1; then
                echo "  Health check: OK"
            else
                echo "  Health check: not yet ready (may still be loading model)"
            fi
            exit 0
        else
            echo "Stale PID file (PID ${OLD_PID} not running), cleaning up."
            rm -f "${PID_FILE}"
        fi
    fi

    detect_gpu

    local mode
    mode=$(select_mode)
    echo "Launch mode: ${mode}"

    case "$mode" in
        docker)
            launch_docker
            ;;
        native)
            launch_native
            ;;
    esac

    wait_for_health
}

main "$@"

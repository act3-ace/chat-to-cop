#!/usr/bin/env bash
# setup-ag-backends.sh -- Bring up LLM backends on an AG GPU node.
#
# Manages vLLM (local GPU inference) and optionally LiteLLM (Bedrock proxy).
# Prints a status dashboard showing which backends are live and the env vars
# operators need.
#
# Usage:
#   ./setup-ag-backends.sh              # start both (if litellm proxy exists)
#   ./setup-ag-backends.sh --vllm-only  # vLLM only
#   ./setup-ag-backends.sh --litellm-only  # LiteLLM proxy only
#   ./setup-ag-backends.sh --both       # both explicitly
#   ./setup-ag-backends.sh --status     # show status without starting anything
#   ./setup-ag-backends.sh --stop       # tear down all backends

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

VLLM_PORT="${VLLM_PORT:-8000}"
LITELLM_PORT="${LITELLM_PORT:-4000}"

# ── Argument parsing ──────────────────────────────────────────────────

MODE="both"
case "${1:-}" in
    --vllm-only)   MODE="vllm" ;;
    --litellm-only) MODE="litellm" ;;
    --both)        MODE="both" ;;
    --status)      MODE="status" ;;
    --stop)        MODE="stop" ;;
    -h|--help)
        echo "Usage: $0 [--vllm-only|--litellm-only|--both|--status|--stop]"
        echo ""
        echo "  --vllm-only     Start vLLM only (local GPU inference)"
        echo "  --litellm-only  Start LiteLLM Bedrock proxy only"
        echo "  --both          Start both backends (default)"
        echo "  --status        Show status dashboard without starting anything"
        echo "  --stop          Stop all backends"
        echo ""
        echo "Environment variables:"
        echo "  VLLM_PORT       vLLM port (default: 8000)"
        echo "  LITELLM_PORT    LiteLLM port (default: 4000)"
        echo "  MODEL_NAME      HuggingFace model for vLLM"
        echo "  See launch-vllm-chat2cop.sh for additional vLLM variables."
        exit 0
        ;;
    "")            MODE="both" ;;
    *)
        echo "Unknown option: $1" >&2
        echo "Run $0 --help for usage." >&2
        exit 1
        ;;
esac

# ── Checks ────────────────────────────────────────────────────────────

check_vllm_running() {
    curl -sf "http://127.0.0.1:${VLLM_PORT}/v1/models" >/dev/null 2>&1
}

check_litellm_running() {
    curl -sf "http://127.0.0.1:${LITELLM_PORT}/health" >/dev/null 2>&1
}

check_litellm_available() {
    # Check if the LiteLLM launch script exists (from issue #77)
    [ -f "${SCRIPT_DIR}/launch-litellm.sh" ] || \
    command -v litellm &>/dev/null
}

# ── Status dashboard ──────────────────────────────────────────────────

print_dashboard() {
    echo ""
    echo "================================================================"
    echo "  AG Backend Status Dashboard"
    echo "================================================================"
    echo ""

    # vLLM status
    if check_vllm_running; then
        local models
        models=$(curl -sf "http://127.0.0.1:${VLLM_PORT}/v1/models" | \
            python3 -c 'import json,sys; d=json.load(sys.stdin); print(", ".join(m["id"] for m in d.get("data",[])))' 2>/dev/null || echo "unknown")
        echo "  vLLM (port ${VLLM_PORT}):      RUNNING"
        echo "    Models: ${models}"
    else
        echo "  vLLM (port ${VLLM_PORT}):      STOPPED"
    fi

    # LiteLLM status
    if check_litellm_running; then
        echo "  LiteLLM (port ${LITELLM_PORT}):   RUNNING"
    elif check_litellm_available; then
        echo "  LiteLLM (port ${LITELLM_PORT}):   STOPPED (script available)"
    else
        echo "  LiteLLM (port ${LITELLM_PORT}):   NOT AVAILABLE (no launch script)"
    fi

    # Docker containers
    if command -v docker &>/dev/null; then
        local vllm_container
        vllm_container=$(docker ps --filter name=vllm-chat2cop --format "{{.Status}}" 2>/dev/null || true)
        if [ -n "$vllm_container" ]; then
            echo "  Docker (vllm-chat2cop): ${vllm_container}"
        fi
    fi

    echo ""
    echo "────────────────────────────────────────────────────────────────"
    echo "  Configuration for chat-to-cop"
    echo "────────────────────────────────────────────────────────────────"
    echo ""

    if check_vllm_running && check_litellm_running; then
        echo "  Tier 1 (vLLM primary) + Tier 2 (Bedrock fallback):"
        echo ""
        echo "    export CHAT_TO_COP_LLM_URL=http://127.0.0.1:${VLLM_PORT}/v1"
        echo "    export CHAT_TO_COP_LLM_MODEL=${MODEL_NAME:-Qwen/Qwen2.5-14B-Instruct-AWQ}"
        echo "    export CHAT_TO_COP_LLM_IS_OLLAMA=false"
        echo "    export CHAT_TO_COP_FALLBACK_URL=http://127.0.0.1:${LITELLM_PORT}/v1"
        echo "    export CHAT_TO_COP_FALLBACK_MODEL=bedrock/anthropic.claude-sonnet-4-5-20250929-v1:0"
        echo ""
    elif check_vllm_running; then
        echo "  vLLM only (no cloud fallback):"
        echo ""
        echo "    export CHAT_TO_COP_LLM_URL=http://127.0.0.1:${VLLM_PORT}/v1"
        echo "    export CHAT_TO_COP_LLM_MODEL=${MODEL_NAME:-Qwen/Qwen2.5-14B-Instruct-AWQ}"
        echo "    export CHAT_TO_COP_LLM_IS_OLLAMA=false"
        echo ""
    elif check_litellm_running; then
        echo "  LiteLLM / Bedrock only (no local GPU):"
        echo ""
        echo "    export CHAT_TO_COP_LLM_URL=http://127.0.0.1:${LITELLM_PORT}/v1"
        echo "    export CHAT_TO_COP_LLM_MODEL=bedrock/anthropic.claude-sonnet-4-5-20250929-v1:0"
        echo ""
    else
        echo "  No backends running. Start with:"
        echo "    $0 --vllm-only     # local GPU"
        echo "    $0 --litellm-only  # Bedrock proxy"
        echo "    $0 --both          # recommended"
        echo ""
    fi

    echo "================================================================"
    echo ""
}

# ── Start backends ────────────────────────────────────────────────────

start_vllm() {
    if check_vllm_running; then
        echo "vLLM is already running on port ${VLLM_PORT}."
        return 0
    fi
    echo "Starting vLLM..."
    bash "${SCRIPT_DIR}/launch-vllm-chat2cop.sh"
}

start_litellm() {
    if check_litellm_running; then
        echo "LiteLLM is already running on port ${LITELLM_PORT}."
        return 0
    fi

    if [ -f "${SCRIPT_DIR}/launch-litellm.sh" ]; then
        echo "Starting LiteLLM proxy..."
        bash "${SCRIPT_DIR}/launch-litellm.sh"
    elif command -v litellm &>/dev/null; then
        echo "Starting LiteLLM from system install..."
        litellm --port "${LITELLM_PORT}" &
        echo "LiteLLM started on port ${LITELLM_PORT}"
    else
        echo "WARNING: No LiteLLM launch script found." >&2
        echo "  Expected: ${SCRIPT_DIR}/launch-litellm.sh (from issue #77)" >&2
        echo "  LiteLLM backend will not be available." >&2
        return 1
    fi
}

# ── Stop backends ─────────────────────────────────────────────────────

stop_all() {
    echo "Stopping backends..."

    # Stop Docker vLLM container
    if command -v docker &>/dev/null; then
        if docker ps --filter name=vllm-chat2cop -q 2>/dev/null | grep -q .; then
            echo "  Stopping Docker container vllm-chat2cop..."
            docker stop vllm-chat2cop 2>/dev/null || true
            docker rm vllm-chat2cop 2>/dev/null || true
        fi
    fi

    # Stop native vLLM processes
    pkill -f "vllm.entrypoints.openai.api_server" 2>/dev/null; true

    # Stop LiteLLM
    pkill -f "litellm" 2>/dev/null; true

    echo "All backends stopped."
}

# ── Main ──────────────────────────────────────────────────────────────

case "$MODE" in
    status)
        print_dashboard
        ;;
    stop)
        stop_all
        ;;
    vllm)
        start_vllm
        print_dashboard
        ;;
    litellm)
        start_litellm
        print_dashboard
        ;;
    both)
        start_vllm
        if check_litellm_available; then
            start_litellm || echo "  (continuing without LiteLLM)"
        else
            echo ""
            echo "Note: LiteLLM launch script not found (issue #77)."
            echo "  Only vLLM backend will be available."
        fi
        print_dashboard
        ;;
esac

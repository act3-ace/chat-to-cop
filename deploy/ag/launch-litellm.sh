#!/bin/bash
#
# launch-litellm.sh -- Install and start LiteLLM proxy on an AG node
#
# Exposes AWS Bedrock Claude models via OpenAI-compatible API so operators
# can point CHAT_TO_COP_LLM_URL at this endpoint without AWS credentials.
#
# Usage (from the deploy/ag/ directory on an AG node):
#   bash launch-litellm.sh
#
# Prerequisites:
#   - AWS credentials configured (AG node IAM role or env vars)
#   - Python 3.10+ with pip
#
# The script is idempotent: re-running it will skip install if litellm is
# already present, and skip launch if the proxy is already running.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${SCRIPT_DIR}/litellm_config.yaml"
PORT=4000
HOST=0.0.0.0
LOG_FILE=/tmp/litellm.log
PID_FILE=/tmp/litellm.pid

echo "========================================"
echo "LiteLLM Bedrock Proxy Launcher"
echo "========================================"
echo "Config:  ${CONFIG_FILE}"
echo "Port:    ${PORT}"
echo "Log:     ${LOG_FILE}"
echo "========================================"
echo ""

# ── 1. Check prerequisites ────────────────────────────────────────────

if [ ! -f "${CONFIG_FILE}" ]; then
    echo "[ERROR] Config file not found: ${CONFIG_FILE}"
    exit 1
fi

if ! command -v python3 &> /dev/null && ! command -v python &> /dev/null; then
    echo "[ERROR] Python not found. Install Python 3.10+ first."
    exit 1
fi

PYTHON=$(command -v python3 2>/dev/null || command -v python)
echo "[INFO] Using Python: ${PYTHON} ($(${PYTHON} --version 2>&1))"

# ── 2. Check if already running ───────────────────────────────────────

if [ -f "${PID_FILE}" ]; then
    OLD_PID=$(cat "${PID_FILE}")
    if kill -0 "${OLD_PID}" 2>/dev/null; then
        echo "[INFO] LiteLLM already running (PID ${OLD_PID})"
        echo "[INFO] To restart: kill ${OLD_PID} && bash $0"
        echo ""
        echo "Verifying health..."
        if curl -sf --max-time 5 "http://127.0.0.1:${PORT}/v1/models" > /dev/null 2>&1; then
            echo "[OK] Proxy is healthy on port ${PORT}"
            curl -s "http://127.0.0.1:${PORT}/v1/models" | python3 -m json.tool 2>/dev/null || true
        else
            echo "[WARN] PID exists but health check failed -- proxy may be starting up"
        fi
        exit 0
    else
        echo "[INFO] Stale PID file found (PID ${OLD_PID} not running), cleaning up"
        rm -f "${PID_FILE}"
    fi
fi

# ── 3. Install litellm ────────────────────────────────────────────────

VENV_DIR="/tmp/litellm-venv"

litellm_works() {
    # Verify litellm proxy is actually functional, not just importable
    local py="$1"
    "${py}" -c "from litellm.proxy.proxy_cli import run_server" 2>/dev/null
}

litellm_ver() {
    local py="$1"
    "${py}" -c "import importlib.metadata; print(importlib.metadata.version('litellm'))" 2>/dev/null || echo "unknown"
}

if [ -d "${VENV_DIR}" ] && litellm_works "${VENV_DIR}/bin/python"; then
    echo "[INFO] litellm already installed in ${VENV_DIR} (version $(litellm_ver "${VENV_DIR}/bin/python"))"
    PYTHON="${VENV_DIR}/bin/python"
elif litellm_works "${PYTHON}"; then
    echo "[INFO] litellm already installed system-wide (version $(litellm_ver "${PYTHON}"))"
else
    echo "[INFO] Installing litellm[proxy] into ${VENV_DIR}..."
    echo "[INFO] Using /tmp to avoid AG home directory disk quota."
    if ${PYTHON} -m venv "${VENV_DIR}" 2>/dev/null; then
        TMPDIR=/tmp PIP_CACHE_DIR=/tmp/pip-cache "${VENV_DIR}/bin/pip" install "litellm[proxy]" 2>&1 | tail -5
        PYTHON="${VENV_DIR}/bin/python"
    else
        echo "[INFO] python3-venv unavailable, falling back to pip install --target"
        TMPDIR=/tmp PIP_CACHE_DIR=/tmp/pip-cache pip3 install --target "${VENV_DIR}" "litellm[proxy]" 2>&1 | tail -5
        export PYTHONPATH="${VENV_DIR}:${PYTHONPATH:-}"
    fi
    echo "[OK] litellm installed in ${VENV_DIR}"
fi

# ── 4. Start LiteLLM proxy ────────────────────────────────────────────

echo "[INFO] Starting LiteLLM proxy on ${HOST}:${PORT}..."

# Resolve the litellm CLI: prefer the venv's bin/ entry point, fall back to
# running via the Python module path (older litellm versions).
if [ -x "${VENV_DIR}/bin/litellm" ]; then
    LITELLM_CMD="${VENV_DIR}/bin/litellm"
elif command -v litellm &>/dev/null; then
    LITELLM_CMD="litellm"
else
    LITELLM_CMD="${PYTHON} -m litellm.proxy.proxy_cli"
fi

nohup ${LITELLM_CMD} \
    --config "${CONFIG_FILE}" \
    --port "${PORT}" \
    --host "${HOST}" \
    > "${LOG_FILE}" 2>&1 &

LITELLM_PID=$!
echo "${LITELLM_PID}" > "${PID_FILE}"
echo "[INFO] LiteLLM started (PID ${LITELLM_PID}), logging to ${LOG_FILE}"

# ── 5. Wait for startup and health check ──────────────────────────────

echo "[INFO] Waiting for proxy to become healthy..."
retries=0
until curl -sf --max-time 5 "http://127.0.0.1:${PORT}/health" > /dev/null 2>&1; do
    retries=$((retries + 1))
    if [ "${retries}" -ge 30 ]; then
        echo "[ERROR] LiteLLM did not start after 60 seconds"
        echo "[ERROR] Check log: tail -50 ${LOG_FILE}"
        exit 1
    fi
    sleep 2
done

echo "[OK] LiteLLM proxy is healthy"
echo ""

# ── 6. Show available models ──────────────────────────────────────────

echo "Available models:"
curl -s "http://127.0.0.1:${PORT}/v1/models" | ${PYTHON} -m json.tool 2>/dev/null || \
    curl -s "http://127.0.0.1:${PORT}/v1/models"
echo ""

# ── 7. Print operator instructions ────────────────────────────────────

AG_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "<AG_NODE_IP>")

echo "========================================"
echo "Proxy is ready. Operator config:"
echo ""
echo "  export CHAT_TO_COP_LLM_URL=http://${AG_IP}:${PORT}/v1"
echo "  export CHAT_TO_COP_LLM_MODEL=claude-sonnet"
echo ""
echo "If AG friendly name is configured:"
echo "  export CHAT_TO_COP_LLM_URL=https://litellm-bedrock.act3.analyticsgateway.com/v1"
echo ""
echo "Stop proxy:  kill \$(cat ${PID_FILE})"
echo "View logs:   tail -f ${LOG_FILE}"
echo "========================================"

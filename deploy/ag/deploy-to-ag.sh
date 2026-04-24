#!/bin/bash
# deploy-to-ag.sh -- One-command deploy of chat-to-cop backends to an AG node.
#
# Runs from the operator's laptop. Provisions an AG compute node (or reuses
# an existing one), transfers ag-quickstart.sh, and executes it remotely.
#
# Prerequisites:
#   1. ag-helpers sourced in your shell (source /path/to/ag-helpers/local/profile.sh)
#   2. ~/.dle_gitlab_token with a DLE GitLab PAT
#   3. ~/.ag_snippet_id with your AG snippet ID
#   4. ~/.ssh/config with ag-head and ag-node hosts (see ag-helpers README)
#
# Usage:
#   bash deploy-to-ag.sh              # provision GPU node + deploy vLLM
#   bash deploy-to-ag.sh --litellm    # provision CPU node + deploy LiteLLM only
#   bash deploy-to-ag.sh --both       # provision GPU node + deploy vLLM + LiteLLM
#   bash deploy-to-ag.sh --reuse      # skip provisioning, deploy to existing ag-node
#   bash deploy-to-ag.sh --status     # check what is running on ag-node

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SSH="ssh"
SSH_OPTS="-o ConnectTimeout=15 -o ServerAliveInterval=30"
SSH_RETRIES=6

# Use Windows native SSH if running under MSYS2/Git Bash
if [ -x "/c/Windows/System32/OpenSSH/ssh.exe" ]; then
    SSH="/c/Windows/System32/OpenSSH/ssh.exe"
fi

# ── Parse arguments ──────────────────────────────────────────────────

BACKEND_MODE="vllm"
REUSE_NODE=false
WALLTIME="2:00:00"
NODE_TYPE="g4dn.xlarge"
DISK_GB=90

for arg in "$@"; do
    case "$arg" in
        --litellm)  BACKEND_MODE="litellm"; NODE_TYPE="cpu"; DISK_GB=30 ;;
        --both)     BACKEND_MODE="both" ;;
        --vllm)     BACKEND_MODE="vllm" ;;
        --reuse)    REUSE_NODE=true ;;
        --status)
            if $SSH $SSH_OPTS ag-node "bash ~/chat-to-cop/deploy/ag/setup-ag-backends.sh --status" 2>/dev/null; then
                exit 0
            else
                echo "Could not reach ag-node. Is a compute node running? Try: agip"
                exit 1
            fi
            ;;
        --help|-h)
            sed -n '2,/^[^#]/s/^# \?//p' "$0" | head -20
            exit 0
            ;;
        *)
            echo "Unknown option: $arg" >&2
            echo "Usage: $0 [--vllm|--litellm|--both|--reuse|--status]" >&2
            exit 1
            ;;
    esac
done

echo "================================================================"
echo "  chat-to-cop AG Deployment"
echo "================================================================"
echo "  Backend: ${BACKEND_MODE}"
echo "  Node:    ${NODE_TYPE} (${DISK_GB}GB disk, ${WALLTIME})"
echo "  Reuse:   ${REUSE_NODE}"
echo "================================================================"
echo ""

# ── Check prerequisites ─────────────────────────────────────────────

TOKEN_FILE="$HOME/.dle_gitlab_token"
SNIPPET_FILE="$HOME/.ag_snippet_id"

if [ ! -f "$TOKEN_FILE" ]; then
    echo "ERROR: Missing $TOKEN_FILE" >&2
    echo "  Create it with your DLE GitLab PAT (read_api + read_repository scope):" >&2
    echo "    echo 'glpat-xxxxxxxxxxxxxxxxxxxx' > $TOKEN_FILE" >&2
    echo "    chmod 600 $TOKEN_FILE" >&2
    exit 1
fi

if [ ! -f "$SNIPPET_FILE" ]; then
    echo "ERROR: Missing $SNIPPET_FILE" >&2
    echo "  Create it with your AG snippet ID:" >&2
    echo "    echo '42' > $SNIPPET_FILE" >&2
    exit 1
fi

DLE_GITLAB_TOKEN=$(tr -d '[:space:]' < "$TOKEN_FILE")

# ── Phase 1: Provision AG node ──────────────────────────────────────

if [ "$REUSE_NODE" = true ]; then
    echo "--- Reusing existing AG node ---"
    echo "Checking connectivity..."
    if ! $SSH $SSH_OPTS ag-node "echo ok" >/dev/null 2>&1; then
        echo "Cannot reach ag-node. Refreshing IP from snippet..."
        agip 2>/dev/null || true
        if ! $SSH $SSH_OPTS ag-node "echo ok" >/dev/null 2>&1; then
            echo "ERROR: Cannot SSH to ag-node. Is there a running compute job?" >&2
            echo "  Check: ag-jobs" >&2
            echo "  Start: $0 (without --reuse)" >&2
            exit 1
        fi
    fi
    echo "Connected."
else
    echo "--- Phase 1: Provisioning AG compute node ---"
    echo "Submitting: ${NODE_TYPE}, ${DISK_GB}GB disk, walltime ${WALLTIME}"
    echo ""

    if ! ag-start "$WALLTIME" "$NODE_TYPE" 1 hold '~/job-publish.sh' 300 "$DISK_GB"; then
        echo "ERROR: ag-start failed. Check ag-jobs for status." >&2
        exit 1
    fi
fi

# ── Phase 2: Wait for SSH ───────────────────────────────────────────

echo ""
echo "--- Phase 2: Establishing SSH connection ---"

for i in $(seq 1 $SSH_RETRIES); do
    if $SSH $SSH_OPTS ag-node "echo ok" >/dev/null 2>&1; then
        echo "SSH connected (attempt $i)"
        break
    fi
    if [ "$i" -eq "$SSH_RETRIES" ]; then
        echo "ERROR: Could not SSH to ag-node after $SSH_RETRIES attempts" >&2
        exit 1
    fi
    echo "  Attempt $i/$SSH_RETRIES failed, retrying in 10s..."
    sleep 10
done

# ── Phase 3: Transfer and execute quickstart ────────────────────────

echo ""
echo "--- Phase 3: Deploying chat-to-cop backends ---"

# Transfer the quickstart script
$SSH $SSH_OPTS ag-node "cat > /tmp/ag-quickstart.sh" < "${SCRIPT_DIR}/ag-quickstart.sh"

# Execute with the operator's DLE token and chosen backend mode
$SSH $SSH_OPTS ag-node "DLE_GITLAB_TOKEN='${DLE_GITLAB_TOKEN}' bash /tmp/ag-quickstart.sh --${BACKEND_MODE}"

echo ""
echo "================================================================"
echo "  Deployment complete"
echo "================================================================"
echo ""
echo "To check status later:     $0 --status"
echo "To SSH to the node:        ssh ag-node"
echo "To stop the AG job:        ag-stop"
echo ""

# Print the node IP for convenience
NODE_IP=$($SSH $SSH_OPTS ag-node "hostname -I" 2>/dev/null | awk '{print $1}')
if [ -n "$NODE_IP" ]; then
    echo "AG node IP: ${NODE_IP}"
    echo ""
    echo "Remote endpoints (from your laptop):"
    if [ "$BACKEND_MODE" != "litellm" ]; then
        echo "  vLLM:    ssh -L 8000:127.0.0.1:8000 ag-node"
    fi
    if [ "$BACKEND_MODE" != "vllm" ]; then
        echo "  LiteLLM: ssh -L 4000:127.0.0.1:4000 ag-node"
    fi
    echo ""
fi

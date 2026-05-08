#!/bin/bash
# ag-quickstart.sh -- Bootstrap chat-to-cop on a fresh AG compute node.
#
# This script is transferred to the AG node and executed remotely by
# deploy-to-ag.sh. It handles everything needed to go from a bare
# compute node to a running LLM backend.
#
# What it does:
#   1. Installs the DoD CA bundle (needed for DLE GitLab HTTPS)
#   2. Clones (or updates) the chat-to-cop repo
#   3. Launches vLLM with Qwen2.5-14B-Instruct-AWQ (if GPU present)
#   4. Optionally launches LiteLLM Bedrock proxy
#   5. Prints operator env vars
#
# Expected environment:
#   DLE_GITLAB_TOKEN   DLE GitLab PAT (read from ~/.dle_gitlab_token if unset)
#
# Usage:
#   bash ag-quickstart.sh              # vLLM only (default)
#   bash ag-quickstart.sh --both       # vLLM + LiteLLM Bedrock
#   bash ag-quickstart.sh --litellm    # LiteLLM Bedrock only (no GPU needed)
#   bash ag-quickstart.sh --status     # check what is running

set -euo pipefail

MODE="${1:-vllm}"
case "$MODE" in
    --both)    MODE="both" ;;
    --litellm) MODE="litellm" ;;
    --status)  MODE="status" ;;
    --vllm)    MODE="vllm" ;;
    -*)        echo "Usage: $0 [--vllm|--litellm|--both|--status]"; exit 1 ;;
esac

REPO_DIR="$HOME/chat-to-cop"
CA_BUNDLE="$HOME/.local/share/dod-ca-bundle.pem"
DLE_GITLAB="gitlab.dle.afrl.af.mil"
REPO_URL="https://${DLE_GITLAB}/c2es1/mash/chat-to-cop.git"

# ── Load token ────────────────────────────────────────────────────────

if [ -z "${DLE_GITLAB_TOKEN:-}" ]; then
    if [ -f "$HOME/.dle_gitlab_token" ]; then
        DLE_GITLAB_TOKEN=$(tr -d '[:space:]' < "$HOME/.dle_gitlab_token")
    else
        echo "ERROR: No DLE GitLab token. Set DLE_GITLAB_TOKEN or create ~/.dle_gitlab_token" >&2
        exit 1
    fi
fi

# ── Status shortcut ───────────────────────────────────────────────────

if [ "$MODE" = "status" ]; then
    if [ -f "$REPO_DIR/deploy/ag/setup-ag-backends.sh" ]; then
        bash "$REPO_DIR/deploy/ag/setup-ag-backends.sh" --status
    else
        echo "chat-to-cop not cloned yet. Run: bash ag-quickstart.sh"
    fi
    exit 0
fi

echo "================================================================"
echo "  chat-to-cop AG Quickstart"
echo "================================================================"
echo ""

# ── 1. DoD CA bundle ─────────────────────────────────────────────────

echo "--- Step 1/4: DoD CA bundle ---"
if [ -f "$CA_BUNDLE" ]; then
    echo "Already present ($(grep -c 'BEGIN CERT' "$CA_BUNDLE") certs)"
else
    echo "Installing..."
    TMP=$(mktemp -d)
    trap "rm -rf $TMP" EXIT
    mkdir -p "$(dirname "$CA_BUNDLE")"
    cd "$TMP"
    curl -ksSL "https://dl.dod.cyber.mil/wp-content/uploads/pki-pke/zip/unclass-certificates_pkcs7_DoD.zip" -o dod.zip
    unzip -q -o dod.zip
    CERT_DIR=$(find . -maxdepth 2 -type d -name 'Certificates_PKCS7*' | head -n 1)
    P7B=$(find "$CERT_DIR" -name '*pem.p7b' | head -n 1)
    openssl pkcs7 -in "$P7B" -print_certs -out "$CA_BUNDLE"
    chmod 644 "$CA_BUNDLE"
    cd /
    rm -rf "$TMP"
    trap - EXIT
    echo "Installed $(grep -c 'BEGIN CERT' "$CA_BUNDLE") certs"
fi

git config --global http.sslCAInfo "$CA_BUNDLE"

# ── 2. Clone or update repo ──────────────────────────────────────────

echo ""
echo "--- Step 2/4: Repository ---"
AUTH_URL="https://oauth2:${DLE_GITLAB_TOKEN}@${DLE_GITLAB}/c2es1/mash/chat-to-cop.git"

if [ -d "$REPO_DIR/.git" ]; then
    echo "Updating existing clone..."
    cd "$REPO_DIR"
    git remote set-url origin "$AUTH_URL" 2>/dev/null || true
    git fetch origin main 2>&1 | tail -3
    git checkout main 2>/dev/null
    git reset --hard origin/main 2>/dev/null
    echo "Updated to $(git rev-parse --short HEAD)"
else
    echo "Cloning..."
    git clone "$AUTH_URL" "$REPO_DIR" 2>&1 | tail -3
    cd "$REPO_DIR"
    echo "Cloned at $(git rev-parse --short HEAD)"
fi

# Scrub token from stored remote (security hygiene)
git remote set-url origin "$REPO_URL" 2>/dev/null || true

# ── 3. Check disk space ──────────────────────────────────────────────

echo ""
echo "--- Step 3/4: Preflight checks ---"
AVAIL_GB=$(df --output=avail / 2>/dev/null | tail -1 | awk '{printf "%.0f", $1/1024/1024}')
echo "Disk available: ${AVAIL_GB} GB"
if [ "$AVAIL_GB" -lt 40 ] && [ "$MODE" != "litellm" ]; then
    echo "WARNING: Less than 40 GB free. vLLM Docker image (~20 GB) + model (~9 GB)"
    echo "  may not fit. Provision with at least 90 GB disk:"
    echo "    ag-start 2:00:00 g4dn.xlarge 1 hold ~/job-publish.sh 300 90"
    echo ""
fi

if [ "$MODE" != "litellm" ]; then
    if command -v nvidia-smi &>/dev/null; then
        GPU=$(nvidia-smi --query-gpu=gpu_name,memory.total --format=csv,noheader 2>/dev/null | head -1)
        echo "GPU: $GPU"
    else
        echo "WARNING: No GPU detected. vLLM requires a GPU node (g4dn.xlarge)."
        if [ "$MODE" = "vllm" ]; then
            echo "  Falling back to LiteLLM Bedrock only."
            MODE="litellm"
        fi
    fi
fi

# ── 4. Launch backends ───────────────────────────────────────────────

echo ""
echo "--- Step 4/4: Launch backends ---"

cd "$REPO_DIR"
chmod +x deploy/ag/*.sh

case "$MODE" in
    vllm)
        bash deploy/ag/launch-vllm-chat2cop.sh
        ;;
    litellm)
        bash deploy/ag/launch-litellm.sh
        ;;
    both)
        bash deploy/ag/launch-vllm-chat2cop.sh
        echo ""
        bash deploy/ag/launch-litellm.sh
        ;;
esac

echo ""
bash deploy/ag/setup-ag-backends.sh --status

#!/bin/bash
# ag-build-flash-drive.sh — Build flash drive package on an AG compute node.
#
# Runs on a DLE GitLab shared runner. SSHes to AG, submits a qsub job,
# waits for the node to boot, then drives the flash drive build over SSH.
# The AG node builds the Docker images (chat-to-cop + ollama with baked
# model), saves them as tarballs, assembles the portable directory, and
# SCPs the result back to the CI runner.
#
# Required CI variables (set in GitLab project settings):
#   AG_SSH_KEY          File variable — AG SSH private key
#   AG_USERNAME         AG login username
#   AG_SNIPPET_ID       DLE GitLab snippet ID for IP discovery
#   DLE_GITLAB_TOKEN    DLE GitLab PAT with api scope
#
# Optional CI variables:
#   AG_NODE_TYPE        Node type for qsub (default: m7i.4xlarge)
#   AG_WALLTIME         Job walltime (default: 1:00:00)
#   AG_DISK_GB          Ephemeral disk in GB (default: 50; needs ~20GB for images+model)
#
# Also uses standard GitLab CI variables:
#   CI_PROJECT_URL, CI_COMMIT_SHORT_SHA, CI_COMMIT_TAG
#
# Usage:
#   bash deploy/ci/ag-build-flash-drive.sh
#   bash deploy/ci/ag-build-flash-drive.sh --model qwen2.5:14b

set -euo pipefail

# ---------------------------------------------------------------------------
# Parse args
# ---------------------------------------------------------------------------

MODEL="qwen2.5:7b"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --model) MODEL="$2"; shift 2 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

NODE_TYPE="${AG_NODE_TYPE:-m7i.4xlarge}"
WALLTIME="${AG_WALLTIME:-1:00:00}"
DISK_GB="${AG_DISK_GB:-50}"
POLL_INTERVAL=5
POLL_TIMEOUT=600
SSH_RETRIES=6
AG_HEAD="ag-head"
JOBID=""

DLE_API="https://gitlab.dle.afrl.af.mil/api/v4"
SNIPPET_URL="$DLE_API/snippets/$AG_SNIPPET_ID/files/main/ag_node.json/raw"

SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=15 -o ServerAliveInterval=30 -o ServerAliveCountMax=3"

# ---------------------------------------------------------------------------
# Cleanup trap — always release the AG job
# ---------------------------------------------------------------------------

cleanup() {
    if [ -n "$JOBID" ]; then
        echo "--- Cleanup: deleting AG job $JOBID ---"
        ssh $SSH_OPTS "$AG_HEAD" "qdel $JOBID" 2>/dev/null || true
    fi
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Phase 1: Submit AG compute job
# ---------------------------------------------------------------------------

echo "=== Phase 1: Submit AG compute job ==="
echo "Node type: $NODE_TYPE  Walltime: $WALLTIME  Disk: ${DISK_GB}GB  Model: $MODEL"

OUTPUT=$(ssh $SSH_OPTS "$AG_HEAD" \
    "qsub -l select=1:type=$NODE_TYPE -l disk=$DISK_GB -l walltime=$WALLTIME -N flash-drive-build ~/job-publish.sh" 2>&1) || true
echo "qsub output: $OUTPUT"

JOBID=$(printf '%s\n' "$OUTPUT" | grep -E '^[0-9]+$' | head -n1)
if [ -z "$JOBID" ]; then
    echo "ERROR: qsub did not return a job ID. Full output:"
    printf '  %s\n' "$OUTPUT"
    exit 1
fi
echo "Submitted job $JOBID"

# ---------------------------------------------------------------------------
# Phase 2: Wait for compute node IP
# ---------------------------------------------------------------------------

echo "=== Phase 2: Wait for compute node IP (up to ${POLL_TIMEOUT}s) ==="
DEADLINE=$(( $(date +%s) + POLL_TIMEOUT ))
NODE_IP=""

while [ "$(date +%s)" -lt "$DEADLINE" ]; do
    sleep "$POLL_INTERVAL"

    SNIPPET=$(curl -sS --cacert "${CI_SERVER_TLS_CA_FILE:-/dev/null}" -H "PRIVATE-TOKEN: $DLE_GITLAB_TOKEN" "$SNIPPET_URL" 2>/dev/null) || continue
    HOSTNAME=$(printf '%s' "$SNIPPET" | python3 -c "import json,sys; print(json.load(sys.stdin).get('hostname',''))" 2>/dev/null) || continue

    case "$HOSTNAME" in
        compute-*-"$JOBID"-*)
            NODE_IP=$(printf '%s' "$SNIPPET" | python3 -c "import json,sys; print(json.load(sys.stdin).get('ip',''))" 2>/dev/null)
            echo "Node ready: $HOSTNAME ($NODE_IP)"
            break
            ;;
    esac
    printf '.'
done

if [ -z "$NODE_IP" ]; then
    echo ""
    echo "ERROR: Timed out waiting for AG node. Last snippet:"
    echo "  $SNIPPET"
    exit 1
fi

# Write ProxyJump SSH config for the compute node
cat >> ~/.ssh/config <<EOF

Host ag-node
    HostName $NODE_IP
    User $AG_USERNAME
    IdentityFile ~/.ssh/ag_key
    IdentitiesOnly yes
    ProxyJump $AG_HEAD
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null
EOF

# ---------------------------------------------------------------------------
# Phase 3: Build on AG compute node
# ---------------------------------------------------------------------------

echo "=== Phase 3: Build flash drive package on AG compute node ==="

IMAGE_TAG="${CI_COMMIT_TAG:-${CI_COMMIT_SHORT_SHA:-latest}}"

# Retry SSH connection (node may still be initializing sshd)
for i in $(seq 1 $SSH_RETRIES); do
    if ssh $SSH_OPTS ag-node "echo ok" 2>/dev/null; then
        echo "SSH connected (attempt $i)"
        break
    fi
    if [ "$i" -eq "$SSH_RETRIES" ]; then
        echo "ERROR: Could not SSH to AG compute node after $SSH_RETRIES attempts"
        exit 1
    fi
    echo "SSH attempt $i failed, retrying in 10s..."
    sleep 10
done

# Transfer the node build script and execute it
ssh $SSH_OPTS ag-node "cat > /tmp/ag-node-flash-drive.sh && chmod +x /tmp/ag-node-flash-drive.sh" \
    < "$(dirname "$0")/ag-node-flash-drive.sh"

ssh $SSH_OPTS ag-node \
    "DLE_GITLAB_TOKEN='$DLE_GITLAB_TOKEN' \
     CI_PROJECT_URL='${CI_PROJECT_URL:-}' \
     COMMIT_SHA='${CI_COMMIT_SHORT_SHA:-HEAD}' \
     IMAGE_TAG='$IMAGE_TAG' \
     MODEL='$MODEL' \
     bash /tmp/ag-node-flash-drive.sh"

# ---------------------------------------------------------------------------
# Phase 4: Retrieve the package
# ---------------------------------------------------------------------------

echo "=== Phase 4: Retrieve flash drive package ==="

scp $SSH_OPTS ag-node:/tmp/chat-to-cop-portable.tar.gz ./chat-to-cop-portable.tar.gz

TARBALL_SIZE=$(stat -c%s chat-to-cop-portable.tar.gz 2>/dev/null || stat -f%z chat-to-cop-portable.tar.gz 2>/dev/null || echo "unknown")
echo "Retrieved chat-to-cop-portable.tar.gz ($TARBALL_SIZE bytes)"

echo "=== Phase 5: Cleanup ==="
echo "Build complete. Releasing AG node."

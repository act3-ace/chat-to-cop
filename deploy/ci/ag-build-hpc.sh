#!/bin/bash
# ag-build-hpc.sh — Orchestrate HPC Docker image build on an AG compute node.
#
# Runs on a DLE GitLab shared runner. SSHes to AG, submits a qsub job,
# waits for the node to boot, then drives the build over SSH.
#
# Required CI variables (set in GitLab project settings):
#   AG_SSH_KEY          File variable — AG SSH private key
#   AG_USERNAME         AG login username
#   AG_SNIPPET_ID       DLE GitLab snippet ID for IP discovery
#   DLE_GITLAB_TOKEN    DLE GitLab PAT with api scope
#
# Optional CI variables:
#   AG_NODE_TYPE        Node type for qsub (default: r5.4xlarge)
#   AG_WALLTIME         Job walltime (default: 2:00:00)
#
# Also uses standard GitLab CI variables:
#   CI_REGISTRY, CI_REGISTRY_USER, CI_REGISTRY_PASSWORD
#   CI_COMMIT_TAG, CI_COMMIT_SHORT_SHA, CONTAINER_IMAGE
#   CI_PROJECT_URL

set -euo pipefail

NODE_TYPE="${AG_NODE_TYPE:-r5.4xlarge}"
WALLTIME="${AG_WALLTIME:-2:00:00}"
POLL_INTERVAL=5
POLL_TIMEOUT=600
SSH_RETRIES=3
AG_HEAD="ag-head"
JOBID=""

DLE_API="https://gitlab.dle.afrl.af.mil/api/v4"
SNIPPET_URL="$DLE_API/snippets/$AG_SNIPPET_ID/files/main/ag_node.json/raw"

SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=15 -o ServerAliveInterval=30 -o ServerAliveCountMax=3"

cleanup() {
    if [ -n "$JOBID" ]; then
        echo "--- Cleanup: deleting AG job $JOBID ---"
        ssh $SSH_OPTS "$AG_HEAD" "qdel $JOBID" 2>/dev/null || true
    fi
}
trap cleanup EXIT

echo "=== Phase 1: Submit AG compute job ==="
echo "Node type: $NODE_TYPE  Walltime: $WALLTIME"

# AG head node only allows: qsub, qstat, qdel, qextend, nc.
OUTPUT=$(ssh $SSH_OPTS "$AG_HEAD" \
    "qsub -l select=1:type=$NODE_TYPE -l walltime=$WALLTIME -N hpc-build ~/job-publish.sh" 2>&1) || true
echo "qsub output: $OUTPUT"

JOBID=$(printf '%s\n' "$OUTPUT" | grep -E '^[0-9]+$' | head -n1)
if [ -z "$JOBID" ]; then
    echo "ERROR: qsub did not return a job ID. Full output:"
    printf '  %s\n' "$OUTPUT"
    exit 1
fi
echo "Submitted job $JOBID"

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

echo "=== Phase 3: Build on AG compute node ==="

IMAGE_TAG="${CI_COMMIT_TAG:-$CI_COMMIT_SHORT_SHA}"

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

# Transfer the build script and execute it
ssh $SSH_OPTS ag-node "cat > /tmp/ag-node-build.sh && chmod +x /tmp/ag-node-build.sh" \
    < "$(dirname "$0")/ag-node-build.sh"

ssh $SSH_OPTS ag-node \
    "DLE_GITLAB_TOKEN='$DLE_GITLAB_TOKEN' \
     CI_REGISTRY='$CI_REGISTRY' \
     CI_REGISTRY_USER='$CI_REGISTRY_USER' \
     CI_REGISTRY_PASSWORD='$CI_REGISTRY_PASSWORD' \
     CI_PROJECT_URL='$CI_PROJECT_URL' \
     CONTAINER_IMAGE='$CONTAINER_IMAGE' \
     IMAGE_TAG='$IMAGE_TAG' \
     bash /tmp/ag-node-build.sh"

echo "=== Phase 4: Cleanup ==="
echo "Build complete. Releasing AG node."

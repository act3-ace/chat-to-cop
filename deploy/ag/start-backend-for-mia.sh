#!/usr/bin/env bash
# start-backend-for-mia.sh -- Start AG backend(s) and print instructions for Mia.
#
# Run this from your laptop when Mia's local run.bat fails and she needs
# a remote backend. It SSHes to AG, starts a GPU node, launches vLLM
# and/or LiteLLM, and prints the exact text to send her.
#
# Usage:
#   bash deploy/ag/start-backend-for-mia.sh              # vLLM (Qwen 14B)
#   bash deploy/ag/start-backend-for-mia.sh --bedrock     # LiteLLM (Sonnet)
#   bash deploy/ag/start-backend-for-mia.sh --both        # both backends
#   bash deploy/ag/start-backend-for-mia.sh --status      # check running backends
#   bash deploy/ag/start-backend-for-mia.sh --stop        # tear down everything
#
# Prerequisites:
#   - SSH config with ag-head and ag-node hosts (see ag-helpers/local/)
#   - AG account with qsub access

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SSH_OPTS="-o ConnectTimeout=15 -o StrictHostKeyChecking=no"

VLLM_PORT=8000
LITELLM_PORT=4000

# ── Argument parsing ─────────────────────────────────────────────────

MODE="${1:---vllm}"
case "$MODE" in
    --vllm)     WANT_VLLM=1; WANT_LITELLM=0 ;;
    --bedrock)  WANT_VLLM=0; WANT_LITELLM=1 ;;
    --both)     WANT_VLLM=1; WANT_LITELLM=1 ;;
    --status)   WANT_VLLM=0; WANT_LITELLM=0; CHECK_ONLY=1 ;;
    --stop)     WANT_VLLM=0; WANT_LITELLM=0; STOP=1 ;;
    -h|--help)
        echo "Usage: $0 [--vllm|--bedrock|--both|--status|--stop]"
        echo ""
        echo "  --vllm      Start vLLM with Qwen2.5-14B-AWQ (default)"
        echo "  --bedrock   Start LiteLLM proxy to Bedrock Sonnet"
        echo "  --both      Start both backends"
        echo "  --status    Check what's running without starting anything"
        echo "  --stop      Stop all backends and release the AG node"
        echo ""
        echo "After startup, prints instructions you can text/email to Mia."
        exit 0
        ;;
    *)
        echo "Unknown option: $MODE (try --help)" >&2
        exit 1
        ;;
esac

# ── Helpers ──────────────────────────────────────────────────────────

ag_head() { ssh $SSH_OPTS ag-head "$@" 2>&1; }
ag_node() { ssh $SSH_OPTS ag-node "$@" 2>&1; }

get_node_ip() {
    ag_head 'qstat -f '"$1"' 2>/dev/null | grep exec_host' | \
        grep -oP '[\d.]+' | head -1
}

update_ssh_config() {
    local ip="$1"
    if grep -q "HostName.*#AG_NODE_IP" ~/.ssh/config 2>/dev/null; then
        sed -i.bak "s/HostName .* #AG_NODE_IP/HostName ${ip} #AG_NODE_IP/" ~/.ssh/config
        echo "  Updated ag-node SSH config to ${ip}"
    fi
}

wait_for_job() {
    local job_id="$1"
    local max_wait=120
    local elapsed=0
    echo -n "  Waiting for AG node to start"
    while [ $elapsed -lt $max_wait ]; do
        local state
        state=$(ag_head "qstat -f ${job_id} 2>/dev/null | grep job_state" | awk '{print $NF}')
        if [ "$state" = "R" ]; then
            echo " running."
            return 0
        fi
        echo -n "."
        sleep 5
        elapsed=$((elapsed + 5))
    done
    echo " timeout."
    echo "ERROR: AG job $job_id did not start within ${max_wait}s" >&2
    return 1
}

# ── Status check ─────────────────────────────────────────────────────

check_status() {
    echo ""
    echo "Checking AG backend status..."
    echo ""

    # Find running job
    local jobs
    jobs=$(ag_head 'qstat -u deployer 2>/dev/null' | grep -E "^\d" || true)
    if [ -z "$jobs" ]; then
        echo "  No AG jobs running."
        echo "  Start with: $0 --vllm (or --bedrock or --both)"
        return 1
    fi

    echo "  AG jobs:"
    echo "$jobs" | sed 's/^/    /'

    # Check backends
    local vllm_ok=0 litellm_ok=0
    if ag_node "curl -sf http://127.0.0.1:${VLLM_PORT}/v1/models >/dev/null 2>&1"; then
        local model
        model=$(ag_node "curl -sf http://127.0.0.1:${VLLM_PORT}/v1/models" | \
            python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["data"][0]["id"])' 2>/dev/null || echo "unknown")
        echo "  vLLM (port ${VLLM_PORT}): RUNNING -- ${model}"
        vllm_ok=1
    else
        echo "  vLLM (port ${VLLM_PORT}): not running"
    fi

    if ag_node "curl -sf http://127.0.0.1:${LITELLM_PORT}/health >/dev/null 2>&1"; then
        echo "  LiteLLM (port ${LITELLM_PORT}): RUNNING -- Bedrock Sonnet"
        litellm_ok=1
    else
        echo "  LiteLLM (port ${LITELLM_PORT}): not running"
    fi

    if [ $vllm_ok -eq 1 ] || [ $litellm_ok -eq 1 ]; then
        local node_ip
        node_ip=$(ag_node 'hostname -I 2>/dev/null' | awk '{print $1}')
        echo ""
        print_mia_instructions "$node_ip" $vllm_ok $litellm_ok
    fi
}

# ── Stop ─────────────────────────────────────────────────────────────

stop_all() {
    echo ""
    echo "Stopping AG backends..."

    # Stop backends on node
    ag_node 'docker stop vllm-chat2cop 2>/dev/null; docker rm vllm-chat2cop 2>/dev/null; kill $(cat /tmp/litellm.pid 2>/dev/null) 2>/dev/null' || true
    echo "  Backends stopped."

    # Release AG jobs
    local jobs
    jobs=$(ag_head 'qstat -u deployer 2>/dev/null' | grep -oP '^\d+' || true)
    if [ -n "$jobs" ]; then
        for job in $jobs; do
            ag_head "qdel ${job}" || true
            echo "  Released AG job ${job}"
        done
    else
        echo "  No AG jobs to release."
    fi
    echo ""
    echo "Done. No AG credits being used."
}

# ── Print instructions for Mia ───────────────────────────────────────

print_mia_instructions() {
    local node_ip="$1"
    local has_vllm="${2:-0}"
    local has_litellm="${3:-0}"

    echo "================================================================"
    echo ""
    echo "TEXT/EMAIL FOR MIA:"
    echo ""
    echo "----------------------------------------------------------------"

    if [ "$has_vllm" -eq 1 ] && [ "$has_litellm" -eq 1 ]; then
        cat <<INSTRUCTIONS
I have a server running for you. In your chat-to-cop folder,
open a command prompt and run:

  run.bat --cloud http://${node_ip}:${VLLM_PORT}/v1

That uses the Qwen 14B model (faster). If you want to try
Sonnet instead (slower but sometimes better):

  run.bat --cloud http://${node_ip}:${LITELLM_PORT}/v1

Both are ready right now. Let me know when you're done so
I can shut it down.
INSTRUCTIONS
    elif [ "$has_vllm" -eq 1 ]; then
        cat <<INSTRUCTIONS
I have a server running for you. In your chat-to-cop folder,
open a command prompt and run:

  run.bat --cloud http://${node_ip}:${VLLM_PORT}/v1

Let me know when you're done so I can shut it down.
INSTRUCTIONS
    elif [ "$has_litellm" -eq 1 ]; then
        cat <<INSTRUCTIONS
I have a server running for you. In your chat-to-cop folder,
open a command prompt and run:

  run.bat --cloud http://${node_ip}:${LITELLM_PORT}/v1

That uses Claude Sonnet via our cloud account.
Let me know when you're done so I can shut it down.
INSTRUCTIONS
    fi

    echo "----------------------------------------------------------------"
    echo ""
    echo "================================================================"
    echo ""
    echo "Stop backends when done:  $0 --stop"
}

# ── Main ─────────────────────────────────────────────────────────────

if [ "${CHECK_ONLY:-0}" = "1" ]; then
    check_status
    exit $?
fi

if [ "${STOP:-0}" = "1" ]; then
    stop_all
    exit 0
fi

echo ""
echo "================================================================"
echo "  AG Backend Launcher for Mia"
echo "================================================================"
echo ""

# Check for existing AG job
EXISTING_JOB=$(ag_head 'qstat -u deployer 2>/dev/null' | grep -oP '^\d+' | head -1 || true)

if [ -n "$EXISTING_JOB" ]; then
    echo "  Found existing AG job: ${EXISTING_JOB}"
    NODE_IP=$(get_node_ip "$EXISTING_JOB")
    if [ -z "$NODE_IP" ]; then
        echo "  Job exists but no exec_host yet -- waiting..."
        wait_for_job "$EXISTING_JOB"
        NODE_IP=$(get_node_ip "$EXISTING_JOB")
    fi
else
    echo "  Submitting AG job (g4dn.xlarge GPU node)..."
    JOB_ID=$(ag_head 'qsub -l select=1:ncpus=1:instance_type=g4dn.xlarge -l walltime=02:00:00 -q standard -N mia-backend /p/home/deployer/ag-helpers/ag-side/job-publish.sh 2>&1' | grep -oP '^\d+')

    if [ -z "$JOB_ID" ]; then
        echo "ERROR: qsub failed. Check AG access." >&2
        exit 1
    fi
    echo "  AG job: ${JOB_ID}"
    wait_for_job "$JOB_ID"
    NODE_IP=$(get_node_ip "$JOB_ID")
fi

if [ -z "$NODE_IP" ]; then
    echo "ERROR: Could not determine compute node IP." >&2
    exit 1
fi

echo "  Node IP: ${NODE_IP}"
update_ssh_config "$NODE_IP"

# Install NVIDIA Container Toolkit if needed (for vLLM Docker)
if [ "$WANT_VLLM" -eq 1 ]; then
    echo ""
    echo "Checking Docker GPU support..."
    HAS_NVIDIA_DOCKER=$(ag_node 'docker info 2>/dev/null | grep -c nvidia' || echo "0")
    if [ "$HAS_NVIDIA_DOCKER" = "0" ]; then
        echo "  Installing NVIDIA Container Toolkit..."
        ag_node 'curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg 2>/dev/null && curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | sed "s#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g" | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list >/dev/null && sudo apt-get update -qq 2>/dev/null && sudo apt-get install -y -qq nvidia-container-toolkit 2>/dev/null && sudo nvidia-ctk runtime configure --runtime=docker 2>/dev/null && sudo systemctl restart docker 2>/dev/null' || true
        echo "  NVIDIA Container Toolkit installed."
    else
        echo "  Docker GPU support: OK"
    fi
fi

# Launch backends
VLLM_OK=0
LITELLM_OK=0

if [ "$WANT_VLLM" -eq 1 ]; then
    echo ""
    echo "Starting vLLM (Qwen2.5-14B-AWQ)..."
    echo "  Model weights are cached in NFS -- this should be fast."

    # Check if already running
    if ag_node "curl -sf http://127.0.0.1:${VLLM_PORT}/v1/models >/dev/null 2>&1"; then
        echo "  vLLM already running."
        VLLM_OK=1
    else
        ag_node "cd /p/home/deployer/chat-to-cop && VLLM_MODE=docker HF_CACHE_DIR=/p/home/deployer/.cache/huggingface bash deploy/ag/launch-vllm-chat2cop.sh" &
        VLLM_WAIT_PID=$!
    fi
fi

if [ "$WANT_LITELLM" -eq 1 ]; then
    echo ""
    echo "Starting LiteLLM (Bedrock Sonnet)..."

    if ag_node "curl -sf http://127.0.0.1:${LITELLM_PORT}/health >/dev/null 2>&1"; then
        echo "  LiteLLM already running."
        LITELLM_OK=1
    else
        ag_node "cd /p/home/deployer/chat-to-cop && bash deploy/ag/launch-litellm.sh" &
        LITELLM_WAIT_PID=$!
    fi
fi

# Wait for launches to complete
if [ "$WANT_VLLM" -eq 1 ] && [ "$VLLM_OK" -eq 0 ]; then
    wait $VLLM_WAIT_PID 2>/dev/null || true
    if ag_node "curl -sf http://127.0.0.1:${VLLM_PORT}/v1/models >/dev/null 2>&1"; then
        VLLM_OK=1
        echo "  vLLM: ready."
    else
        echo "  WARNING: vLLM did not start. Check: ssh ag-node docker logs vllm-chat2cop"
    fi
fi

if [ "$WANT_LITELLM" -eq 1 ] && [ "$LITELLM_OK" -eq 0 ]; then
    wait $LITELLM_WAIT_PID 2>/dev/null || true
    if ag_node "curl -sf http://127.0.0.1:${LITELLM_PORT}/health >/dev/null 2>&1"; then
        LITELLM_OK=1
        echo "  LiteLLM: ready."
    else
        echo "  WARNING: LiteLLM did not start. Check: ssh ag-node tail -50 /tmp/litellm.log"
    fi
fi

# Print instructions
echo ""
print_mia_instructions "$NODE_IP" "$VLLM_OK" "$LITELLM_OK"

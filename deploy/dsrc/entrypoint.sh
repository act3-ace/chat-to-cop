#!/bin/bash
# =============================================================================
# Entrypoint for chat-to-cop HPC container
# =============================================================================
#
# Starts Ollama in the background, waits for it, then runs the replay pipeline.
#
# Usage:
#   apptainer run --nv chat-to-cop-hpc.sif /path/to/chat.zip [--num-ctx 8192]
#   apptainer run --nv chat-to-cop-hpc.sif --server  # API server mode
# =============================================================================

set -e

export OLLAMA_MODELS="${OLLAMA_MODELS:-/opt/ollama-models}"

# Start Ollama in background
ollama serve &
OLLAMA_PID=$!

# Wait for Ollama to be ready
echo "[entrypoint] Waiting for Ollama..."
retries=0
until curl -sf http://127.0.0.1:11434/api/tags > /dev/null 2>&1; do
    retries=$((retries + 1))
    if [ "$retries" -ge 60 ]; then
        echo "[entrypoint] ERROR: Ollama did not start after 120 seconds"
        kill $OLLAMA_PID 2>/dev/null
        exit 1
    fi
    sleep 2
done

echo "[entrypoint] Ollama ready. Available models:"
curl -sf http://127.0.0.1:11434/api/tags | grep -o '"name":"[^"]*"' || true
echo ""

# Dispatch based on arguments
if [ "$1" = "--server" ]; then
    # API server mode
    shift
    echo "[entrypoint] Starting FastAPI server..."
    exec uvicorn chat_to_cop.api:app --host 0.0.0.0 --port 8000 "$@"
elif [ -n "$1" ]; then
    # Replay mode (default)
    echo "[entrypoint] Starting replay: $*"
    python -m chat_to_cop.replay "$@"
    EXIT_CODE=$?
    kill $OLLAMA_PID 2>/dev/null
    exit $EXIT_CODE
else
    echo "[entrypoint] No arguments. Use:"
    echo "  <container> /path/to/chat.zip [--num-ctx 8192]  # Replay mode"
    echo "  <container> --server                             # API server mode"
    echo ""
    echo "Keeping Ollama alive for manual use..."
    wait $OLLAMA_PID
fi

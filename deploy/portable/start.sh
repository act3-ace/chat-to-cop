#!/usr/bin/env bash
# Start the chat-to-cop portable pipeline.
#
# Usage:
#   bash start.sh              # GPU mode (default)
#   bash start.sh --no-gpu     # CPU-only mode
#   bash start.sh --stop       # shut down all services
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

NO_GPU=0
STOP=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-gpu) NO_GPU=1; shift ;;
        --stop)   STOP=1;   shift ;;
        -h|--help)
            echo "Usage: start.sh [--no-gpu] [--stop]"
            echo "  --no-gpu   Run without GPU acceleration (slower but works anywhere)"
            echo "  --stop     Shut down all services"
            exit 0
            ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# ---------------------------------------------------------------------------
# Stop mode
# ---------------------------------------------------------------------------

if [[ "$STOP" -eq 1 ]]; then
    echo "[chat-to-cop] Stopping all services..."
    docker compose down
    echo "[chat-to-cop] Stopped."
    exit 0
fi

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------

echo "[chat-to-cop] Checking Docker..."
if ! command -v docker &>/dev/null; then
    echo "ERROR: Docker is not installed. Install Docker Desktop from https://docker.com"
    exit 1
fi

if ! docker info &>/dev/null; then
    echo "ERROR: Docker daemon is not running. Start Docker Desktop and try again."
    exit 1
fi

# ---------------------------------------------------------------------------
# Load images
# ---------------------------------------------------------------------------

echo "[chat-to-cop] Loading Docker images from tarballs..."

for tarball in images/*.tar; do
    if [[ -f "$tarball" ]]; then
        echo "  Loading $(basename "$tarball")..."
        docker load -i "$tarball"
    fi
done

echo "[chat-to-cop] Images loaded."

# ---------------------------------------------------------------------------
# Create data directory
# ---------------------------------------------------------------------------

mkdir -p data

# ---------------------------------------------------------------------------
# Handle GPU / CPU mode
# ---------------------------------------------------------------------------

COMPOSE_CMD="docker compose up -d"

if [[ "$NO_GPU" -eq 1 ]]; then
    echo "[chat-to-cop] Running in CPU-only mode (no GPU acceleration)."
    echo "[chat-to-cop] LLM inference will be slower. First response may take 30-60 seconds."
    echo ""

    # Create a temporary override that removes the GPU reservation
    cat > docker-compose.override.yml <<'EOF'
services:
  ollama:
    deploy: {}
EOF
    COMPOSE_CMD="docker compose -f docker-compose.yml -f docker-compose.override.yml up -d"
else
    # Remove any leftover CPU override
    rm -f docker-compose.override.yml
fi

# ---------------------------------------------------------------------------
# Start services
# ---------------------------------------------------------------------------

echo "[chat-to-cop] Starting services..."
echo ""
$COMPOSE_CMD

echo ""
echo "============================================"
echo "  chat-to-cop is starting up"
echo "============================================"
echo ""
echo "  Services:"
echo "    Ollama (LLM):     http://localhost:11434"
echo "    Mock IRC server:  ws://localhost:8097"
echo "    REST API:         http://localhost:8001/docs"
echo ""
echo "  The pipeline is processing canned demo messages."
echo "  Watch extraction output:"
echo "    docker compose logs -f pipeline"
echo ""
echo "  To stop everything:"
echo "    bash start.sh --stop"
echo ""
echo "  First startup takes 30-60 seconds while the LLM loads."
echo "  Run 'docker compose logs -f' to see all service logs."
echo ""

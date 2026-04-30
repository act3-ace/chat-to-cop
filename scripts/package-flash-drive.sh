#!/usr/bin/env bash
# Package chat-to-cop for air-gapped flash drive distribution.
#
# Creates a self-contained directory with Docker images, compose files,
# and start scripts. The recipient needs only Docker Desktop installed.
#
# Usage:
#   ./scripts/package-flash-drive.sh
#   ./scripts/package-flash-drive.sh --include-data /path/to/chat.zip
#   ./scripts/package-flash-drive.sh --output /media/usb/chat-to-cop --tag v0.1.4
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Defaults
OUTPUT_DIR="${REPO_ROOT}/chat-to-cop-portable"
INCLUDE_DATA=""
TAG="latest"
PIPELINE_IMAGE="chat-to-cop"
OLLAMA_BASE_IMAGE="ollama/ollama:latest"
OLLAMA_BAKED_IMAGE="chat-to-cop/ollama-with-model"
MODEL="qwen2.5:7b"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

log()  { printf "[package] %s\n" "$*"; }
err()  { printf "[package] ERROR: %s\n" "$*" >&2; }
die()  { err "$@"; exit 1; }

usage() {
    cat <<'EOF'
Usage: package-flash-drive.sh [OPTIONS]

Options:
  --include-data PATH   Copy a sample DASH chat.zip into the output
  --output PATH         Output directory (default: ./chat-to-cop-portable)
  --tag TAG             Image tag for chat-to-cop (default: latest)
  --model MODEL         Ollama model to bake in (default: qwen2.5:7b)
  -h, --help            Show this help
EOF
    exit 0
}

human_size() {
    local bytes=$1
    if   (( bytes >= 1073741824 )); then printf "%.1f GB" "$(echo "$bytes / 1073741824" | bc -l)"
    elif (( bytes >= 1048576 ));    then printf "%.1f MB" "$(echo "$bytes / 1048576" | bc -l)"
    elif (( bytes >= 1024 ));       then printf "%.1f KB" "$(echo "$bytes / 1024" | bc -l)"
    else printf "%d bytes" "$bytes"
    fi
}

# ---------------------------------------------------------------------------
# Parse args
# ---------------------------------------------------------------------------

while [[ $# -gt 0 ]]; do
    case "$1" in
        --include-data) INCLUDE_DATA="$2"; shift 2 ;;
        --output)       OUTPUT_DIR="$2";   shift 2 ;;
        --tag)          TAG="$2";          shift 2 ;;
        --model)        MODEL="$2";        shift 2 ;;
        -h|--help)      usage ;;
        *) die "Unknown option: $1" ;;
    esac
done

# ---------------------------------------------------------------------------
# Preflight checks
# ---------------------------------------------------------------------------

log "Checking prerequisites..."

if ! command -v docker &>/dev/null; then
    die "Docker is not installed or not on PATH."
fi

if ! docker info &>/dev/null; then
    die "Docker daemon is not running. Start Docker Desktop and try again."
fi

if [[ -n "$INCLUDE_DATA" && ! -f "$INCLUDE_DATA" ]]; then
    die "Data file not found: $INCLUDE_DATA"
fi

# Check available disk space (need ~10 GB for images)
if command -v df &>/dev/null; then
    avail_kb=$(df -k "$(dirname "$OUTPUT_DIR")" 2>/dev/null | awk 'NR==2 {print $4}' || echo 0)
    if [[ "$avail_kb" =~ ^[0-9]+$ ]] && (( avail_kb < 10485760 )); then
        log "WARNING: Less than 10 GB free on target volume. Ollama + model images are large."
    fi
fi

# ---------------------------------------------------------------------------
# Step 1: Build chat-to-cop image
# ---------------------------------------------------------------------------

log "Building chat-to-cop Docker image (tag: $TAG)..."
docker build -t "${PIPELINE_IMAGE}:${TAG}" "$REPO_ROOT"

# ---------------------------------------------------------------------------
# Step 2: Pull ollama base image
# ---------------------------------------------------------------------------

log "Pulling $OLLAMA_BASE_IMAGE..."
docker pull "$OLLAMA_BASE_IMAGE"

# ---------------------------------------------------------------------------
# Step 3: Bake the model into the ollama image
# ---------------------------------------------------------------------------

log "Baking model $MODEL into ollama image..."
CONTAINER_NAME="chat-to-cop-model-bake-$$"

# Start a temporary ollama container
docker run -d --name "$CONTAINER_NAME" "$OLLAMA_BASE_IMAGE" serve
log "Waiting for ollama to be ready..."
for i in $(seq 1 30); do
    if docker exec "$CONTAINER_NAME" ollama list &>/dev/null; then
        break
    fi
    if (( i == 30 )); then
        docker rm -f "$CONTAINER_NAME" &>/dev/null || true
        die "Ollama container failed to start within 30 seconds."
    fi
    sleep 1
done

log "Pulling $MODEL inside container (this may take a while)..."
docker exec "$CONTAINER_NAME" ollama pull "$MODEL"
log "Model pull complete."

# Commit the container with the model baked in
docker commit "$CONTAINER_NAME" "${OLLAMA_BAKED_IMAGE}:${TAG}"
docker rm -f "$CONTAINER_NAME" &>/dev/null || true
log "Created image ${OLLAMA_BAKED_IMAGE}:${TAG} with $MODEL baked in."

# ---------------------------------------------------------------------------
# Step 4: Save images to tarballs
# ---------------------------------------------------------------------------

log "Preparing output directory: $OUTPUT_DIR"
rm -rf "$OUTPUT_DIR"
mkdir -p "$OUTPUT_DIR/images"

log "Saving chat-to-cop image to tarball..."
docker save "${PIPELINE_IMAGE}:${TAG}" -o "$OUTPUT_DIR/images/chat-to-cop.tar"

log "Saving ollama-with-model image to tarball..."
docker save "${OLLAMA_BAKED_IMAGE}:${TAG}" -o "$OUTPUT_DIR/images/ollama-with-model.tar"

# ---------------------------------------------------------------------------
# Step 5: Copy deploy files
# ---------------------------------------------------------------------------

log "Copying deployment files..."
cp "$REPO_ROOT/deploy/portable/docker-compose.yml" "$OUTPUT_DIR/docker-compose.yml"
cp "$REPO_ROOT/deploy/portable/start.sh"           "$OUTPUT_DIR/start.sh"
cp "$REPO_ROOT/deploy/portable/start.bat"          "$OUTPUT_DIR/start.bat"
cp "$REPO_ROOT/deploy/portable/README.txt"         "$OUTPUT_DIR/README.txt"
chmod +x "$OUTPUT_DIR/start.sh"

# Create .env with the tag
cat > "$OUTPUT_DIR/.env" <<ENVEOF
# chat-to-cop portable configuration
# Edit these values to customize behavior.

# LLM model (baked into the ollama image)
CHAT_TO_COP_LLM_MODEL=${MODEL}

# IRC channels to monitor
CHAT_TO_COP_IRC_CHANNELS=#c2_coord,#fires,#isr_reports,#jprc,#stt_hydroBMA,#stt_crusherBMA
ENVEOF

# ---------------------------------------------------------------------------
# Step 6: Optional sample data
# ---------------------------------------------------------------------------

if [[ -n "$INCLUDE_DATA" ]]; then
    log "Including sample data: $INCLUDE_DATA"
    mkdir -p "$OUTPUT_DIR/data"
    cp "$INCLUDE_DATA" "$OUTPUT_DIR/data/sample.zip"
fi

# ---------------------------------------------------------------------------
# Step 7: Summary
# ---------------------------------------------------------------------------

total_bytes=0
while IFS= read -r -d '' f; do
    size=$(stat -c%s "$f" 2>/dev/null || stat -f%z "$f" 2>/dev/null || echo 0)
    total_bytes=$(( total_bytes + size ))
done < <(find "$OUTPUT_DIR" -type f -print0)

log ""
log "============================================"
log "  Flash drive package ready"
log "============================================"
log ""
log "  Output:     $OUTPUT_DIR"
log "  Total size: $(human_size $total_bytes)"
log "  Model:      $MODEL"
log "  Tag:        $TAG"
log ""
log "  Contents:"
find "$OUTPUT_DIR" -type f | sed "s|$OUTPUT_DIR/|    |" | sort
log ""
log "  Copy the entire directory to a flash drive."
log "  On the target machine:"
log "    Linux/Mac:  cd chat-to-cop-portable && bash start.sh"
log "    Windows:    cd chat-to-cop-portable && start.bat"
log ""

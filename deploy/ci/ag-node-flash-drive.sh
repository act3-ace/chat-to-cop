#!/bin/bash
# ag-node-flash-drive.sh — Build flash drive package on an AG compute node.
#
# Transferred to the node and executed by ag-build-flash-drive.sh. All
# configuration arrives via environment variables.
#
# Expected environment:
#   DLE_GITLAB_TOKEN      DLE GitLab PAT (for git clone)
#   CI_PROJECT_URL        HTTPS URL of the project on DLE GitLab
#   COMMIT_SHA            Commit to check out (short SHA or HEAD)
#   IMAGE_TAG             Tag for the chat-to-cop image
#   MODEL                 Ollama model to bake (e.g. qwen2.5:7b)

set -euo pipefail

BUILD_DIR="/tmp/chat-to-cop-build"
OUTPUT_DIR="/tmp/chat-to-cop-portable"
CA_BUNDLE="$HOME/.local/share/dod-ca-bundle.pem"

echo "=== [node] Step 1: DoD CA bundle ==="
if [ ! -f "$CA_BUNDLE" ]; then
    echo "Installing DoD CA bundle..."
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
    echo "Installed $(grep -c 'BEGIN CERT' "$CA_BUNDLE") certs to $CA_BUNDLE"
else
    echo "CA bundle already present ($(grep -c 'BEGIN CERT' "$CA_BUNDLE") certs)"
fi

echo "=== [node] Step 2: Clone repository ==="
rm -rf "$BUILD_DIR"

CLONE_URL=$(echo "$CI_PROJECT_URL" | sed "s|https://|https://oauth2:${DLE_GITLAB_TOKEN}@|")
GIT_SSL_CAINFO="$CA_BUNDLE" git clone --depth 1 "$CLONE_URL" "$BUILD_DIR"
cd "$BUILD_DIR"

if [ "$COMMIT_SHA" != "HEAD" ]; then
    GIT_SSL_CAINFO="$CA_BUNDLE" git fetch origin "$COMMIT_SHA" --depth 1 2>/dev/null || true
    git checkout "$COMMIT_SHA" 2>/dev/null || echo "Using default branch HEAD"
fi
echo "Working at $(git rev-parse --short HEAD)"

echo "=== [node] Step 3: Build chat-to-cop image ==="
docker system prune -f 2>/dev/null || true
docker build -t "chat-to-cop:${IMAGE_TAG}" .

echo "=== [node] Step 4: Pull ollama and bake model ==="
docker pull ollama/ollama:latest

CONTAINER_NAME="model-bake-$$"
docker run -d --name "$CONTAINER_NAME" ollama/ollama:latest serve

echo "Waiting for ollama to be ready..."
for i in $(seq 1 30); do
    if docker exec "$CONTAINER_NAME" ollama list &>/dev/null; then
        break
    fi
    if [ "$i" -eq 30 ]; then
        docker rm -f "$CONTAINER_NAME" &>/dev/null || true
        echo "ERROR: Ollama container failed to start within 30 seconds."
        exit 1
    fi
    sleep 1
done

echo "Pulling model $MODEL (this may take a while)..."
docker exec "$CONTAINER_NAME" ollama pull "$MODEL"
echo "Model pull complete."

docker commit "$CONTAINER_NAME" "chat-to-cop/ollama-with-model:${IMAGE_TAG}"
docker rm -f "$CONTAINER_NAME" &>/dev/null || true
docker rmi ollama/ollama:latest 2>/dev/null || true
docker system prune -f 2>/dev/null || true
echo "Created image chat-to-cop/ollama-with-model:${IMAGE_TAG}"

echo "=== [node] Step 5: Save images to tarballs ==="
rm -rf "$OUTPUT_DIR"
mkdir -p "$OUTPUT_DIR/images"

docker save "chat-to-cop:${IMAGE_TAG}" -o "$OUTPUT_DIR/images/chat-to-cop.tar"
echo "Saved chat-to-cop.tar"

docker save "chat-to-cop/ollama-with-model:${IMAGE_TAG}" -o "$OUTPUT_DIR/images/ollama-with-model.tar"
echo "Saved ollama-with-model.tar"

echo "=== [node] Step 6: Copy deployment files ==="
cp "$BUILD_DIR/deploy/portable/docker-compose.yml" "$OUTPUT_DIR/docker-compose.yml"
cp "$BUILD_DIR/deploy/portable/start.sh"           "$OUTPUT_DIR/start.sh"
cp "$BUILD_DIR/deploy/portable/start.bat"          "$OUTPUT_DIR/start.bat"
cp "$BUILD_DIR/deploy/portable/README"              "$OUTPUT_DIR/README.txt"
chmod +x "$OUTPUT_DIR/start.sh"

cat > "$OUTPUT_DIR/.env" <<ENVEOF
# chat-to-cop portable configuration
# Edit these values to customize behavior.

# LLM model (baked into the ollama image)
CHAT_TO_COP_LLM_MODEL=${MODEL}

# IRC channels to monitor
CHAT_TO_COP_IRC_CHANNELS=#c2_coord,#fires,#isr_reports,#jprc,#stt_hydroBMA,#stt_crusherBMA
ENVEOF

echo "=== [node] Step 7: Create tarball ==="
tar czf /tmp/chat-to-cop-portable.tar.gz -C "$OUTPUT_DIR" .
TARBALL_SIZE=$(stat -c%s /tmp/chat-to-cop-portable.tar.gz 2>/dev/null || stat -f%z /tmp/chat-to-cop-portable.tar.gz 2>/dev/null || echo "unknown")
echo "Package ready: /tmp/chat-to-cop-portable.tar.gz ($TARBALL_SIZE bytes)"

echo "=== [node] Step 8: Cleanup ==="
rm -rf "$BUILD_DIR"
docker rmi "chat-to-cop:${IMAGE_TAG}" 2>/dev/null || true
docker rmi "chat-to-cop/ollama-with-model:${IMAGE_TAG}" 2>/dev/null || true
docker rmi ollama/ollama:latest 2>/dev/null || true

echo "Flash drive build complete."

#!/bin/bash
# ag-node-build.sh — Build and push HPC Docker image from an AG compute node.
#
# Transferred to the node and executed by ag-build-hpc.sh. All configuration
# arrives via environment variables; the script is fully self-contained.
#
# Expected environment:
#   DLE_GITLAB_TOKEN      DLE GitLab PAT (for git clone and API access)
#   CI_REGISTRY           DLE container registry hostname
#   CI_REGISTRY_USER      Registry username (from GitLab CI)
#   CI_REGISTRY_PASSWORD  Registry password (from GitLab CI)
#   CI_PROJECT_URL        HTTPS URL of the project on DLE GitLab
#   CONTAINER_IMAGE       Full registry path (e.g. registry.../c2es1/mash/chat-to-cop)
#   IMAGE_TAG             Tag for the image (version or commit SHA)

set -euo pipefail

BUILD_DIR="/tmp/chat-to-cop-build"
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

echo "=== [node] Step 2: Configure Docker for DLE registry ==="
REGISTRY_HOST=$(echo "$CI_REGISTRY" | cut -d/ -f1)
if ! grep -q "$REGISTRY_HOST" /etc/docker/daemon.json 2>/dev/null; then
    sudo mkdir -p /etc/docker
    echo "{\"insecure-registries\":[\"$REGISTRY_HOST\"]}" | sudo tee /etc/docker/daemon.json
    sudo systemctl restart docker
    echo "Docker restarted with insecure-registries for $REGISTRY_HOST"
    sleep 3
else
    echo "Docker already configured for $REGISTRY_HOST"
fi

echo "=== [node] Step 3: Clone repository ==="
rm -rf "$BUILD_DIR"

CLONE_URL=$(echo "$CI_PROJECT_URL" | sed "s|https://|https://oauth2:${DLE_GITLAB_TOKEN}@|")
GIT_SSL_CAINFO="$CA_BUNDLE" git clone --depth 1 "$CLONE_URL" "$BUILD_DIR"
cd "$BUILD_DIR"
echo "Cloned at $(git rev-parse --short HEAD)"

echo "=== [node] Step 4: Docker build ==="
docker build \
    -f deploy/dsrc/Dockerfile.hpc \
    -t "$CONTAINER_IMAGE:hpc-$IMAGE_TAG" \
    -t "$CONTAINER_IMAGE:hpc-latest" \
    .

echo "=== [node] Step 5: Push to DLE registry ==="
echo "$CI_REGISTRY_PASSWORD" | docker login "$REGISTRY_HOST" -u "$CI_REGISTRY_USER" --password-stdin

docker push "$CONTAINER_IMAGE:hpc-$IMAGE_TAG"
docker push "$CONTAINER_IMAGE:hpc-latest"

echo "=== [node] Step 6: Cleanup ==="
rm -rf "$BUILD_DIR"
docker logout "$REGISTRY_HOST" 2>/dev/null || true

echo "Build and push complete."
echo "  $CONTAINER_IMAGE:hpc-$IMAGE_TAG"
echo "  $CONTAINER_IMAGE:hpc-latest"

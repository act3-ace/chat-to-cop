#!/bin/bash
#
# download_model.sh -- Download Ollama models for baking into the container
#
# Run this on a machine WITH internet access BEFORE building the .sif.
# Models are saved to deploy/dsrc/ollama-models/ which gets copied into
# the container during build.
#
# Usage:
#   cd <project-root>
#   bash deploy/dsrc/download_model.sh
#
# Models downloaded:
#   - qwen2.5:7b  (~5GB, primary model, fits 32GB V100 with room to spare)
#   - qwen2.5:3b  (~2GB, fallback model for degraded mode)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODEL_DIR="$SCRIPT_DIR/ollama-models"

echo "=============================================================================="
echo "Chat-to-CoP: Download Ollama Models for HPC"
echo "=============================================================================="
echo "Model directory: $MODEL_DIR"
echo ""

# Check for Ollama
if ! command -v ollama &> /dev/null; then
    echo "ERROR: Ollama not found. Install it first:"
    echo "  curl -fsSL https://ollama.com/install.sh | sh"
    exit 1
fi

# Start Ollama if not running
if ! curl -sf http://127.0.0.1:11434/api/tags > /dev/null 2>&1; then
    echo "Starting Ollama server..."
    ollama serve &
    OLLAMA_PID=$!
    sleep 5
    STOP_OLLAMA=1
fi

# Pull models
echo "Pulling qwen2.5:7b (primary model, ~5GB)..."
ollama pull qwen2.5:7b

echo ""
echo "Pulling qwen2.5:3b (fallback model, ~2GB)..."
ollama pull qwen2.5:3b

# Copy model blobs from Ollama's storage to our build directory
# Ollama stores models in ~/.ollama/models on Linux, or platform-specific paths
OLLAMA_HOME="${OLLAMA_MODELS:-$HOME/.ollama}"

echo ""
echo "Copying model files to $MODEL_DIR..."
rm -rf "$MODEL_DIR"
cp -r "$OLLAMA_HOME/models" "$MODEL_DIR"

# Verify
echo ""
echo "Model files:"
du -sh "$MODEL_DIR"/* 2>/dev/null || du -sh "$MODEL_DIR"
echo ""

# Cleanup
if [ -n "$STOP_OLLAMA" ]; then
    kill $OLLAMA_PID 2>/dev/null
fi

echo "=============================================================================="
echo "Done! Models saved to: $MODEL_DIR"
echo ""
echo "Next steps:"
echo "  1. Build container: apptainer build chat-to-cop.sif deploy/dsrc/singularity.def"
echo "  2. Transfer to HPC: scp chat-to-cop.sif user@narwhal.hpc.mil:\$WORKDIR/"
echo "=============================================================================="

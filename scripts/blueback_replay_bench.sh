#!/bin/bash
#SBATCH --job-name=replay-bench
#SBATCH --account=AFSNW27526RYZ
#SBATCH --partition=debug
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --gres=gpu:1
#SBATCH --time=00:15:00
#SBATCH --output=results/replay_bench_%j.out
#SBATCH --error=results/replay_bench_%j.err

# Issue #73 Part A — Slurm wrapper for replay_bench.py on Blueback.
# Runs a stratified N=100 benchmark against silver labels.
#
# Usage:
#   sbatch scripts/blueback_replay_bench.sh
#   sbatch scripts/blueback_replay_bench.sh --model qwen3:30b-a3b --n 50

set -euo pipefail

MODEL="${MODEL:-qwen2.5:7b}"
N="${N:-100}"
OLLAMA_PORT="${OLLAMA_PORT:-11434}"

cd "${SLURM_SUBMIT_DIR:-.}"

mkdir -p results

# Start Ollama if not already running
if ! curl -s "http://127.0.0.1:${OLLAMA_PORT}/api/version" > /dev/null 2>&1; then
    echo "Starting Ollama on port ${OLLAMA_PORT}..."
    OLLAMA_HOST="127.0.0.1:${OLLAMA_PORT}" ollama serve &
    OLLAMA_PID=$!
    sleep 5
    # Pull model if needed
    OLLAMA_HOST="127.0.0.1:${OLLAMA_PORT}" ollama pull "${MODEL}" 2>&1 | tail -3
else
    echo "Ollama already running on port ${OLLAMA_PORT}"
    OLLAMA_PID=""
fi

echo "Running replay bench: model=${MODEL} n=${N}"
python scripts/replay_bench.py \
    --model "${MODEL}" \
    --url "http://127.0.0.1:${OLLAMA_PORT}/v1" \
    --n "${N}" \
    --num-ctx 8192 \
    "$@"

# Cleanup
if [ -n "${OLLAMA_PID:-}" ]; then
    kill "${OLLAMA_PID}" 2>/dev/null || true
fi

echo "Done. Results in results/"

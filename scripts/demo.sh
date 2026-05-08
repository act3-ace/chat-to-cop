#!/usr/bin/env bash
# Demo runner for chat-to-cop external presentations.
#
# Runs the quick_test smoke test (5 real DASH 3 messages, ~30-60s on GPU)
# after verifying Ollama is up and the model is loaded.
#
# Usage:
#   ./scripts/demo.sh                    # default: qwen2.5:7b on local Ollama
#   ./scripts/demo.sh qwen2.5:3b         # use a smaller/faster model
#   MODEL=qwen2.5:7b URL=http://127.0.0.1:11434/v1 ./scripts/demo.sh
#
# Fallback: if local Ollama is unreachable, see docs/DEMO_RUNBOOK.md for
# cloud API alternatives (Bedrock, Gemini Flash).

set -e

MODEL="${1:-${MODEL:-qwen2.5:7b}}"
URL="${URL:-http://127.0.0.1:11434/v1}"
OLLAMA_HOST="$(echo "$URL" | sed -E 's|^https?://([^/]+).*|\1|; s|/v1$||')"

echo "================================================================"
echo "  chat-to-cop demo: smoke test (5 real DASH 3 messages)"
echo "================================================================"
echo "  Model: $MODEL"
echo "  URL:   $URL"
echo

# Pre-flight 1: Ollama reachable?
echo "[1/3] Checking Ollama at $OLLAMA_HOST..."
if ! python -c "import urllib.request; urllib.request.urlopen('http://${OLLAMA_HOST}/api/tags', timeout=3)" 2>/dev/null; then
    echo "  FAIL: Ollama not reachable at $URL"
    echo
    echo "  Start it with:  ollama serve &"
    echo "  Or fall back to a cloud backend — see docs/DEMO_RUNBOOK.md"
    exit 1
fi
echo "  OK"

# Pre-flight 2: model loaded?
echo "[2/3] Checking that $MODEL is available..."
if ! python -c "
import json, urllib.request
data = json.loads(urllib.request.urlopen('http://${OLLAMA_HOST}/api/tags', timeout=3).read())
models = [m['name'] for m in data.get('models', [])]
assert any('${MODEL}' in m for m in models), f'not found in {models}'
" 2>/dev/null; then
    echo "  FAIL: $MODEL not loaded"
    echo "  Pull it with:  ollama pull $MODEL"
    exit 1
fi
echo "  OK"

# Pre-flight 3: warm the model with a tiny request to avoid first-request latency
echo "[3/3] Warming the model (avoids slow first message in demo)..."
python -c "
import json, urllib.request
req = urllib.request.Request(
    'http://${OLLAMA_HOST}/api/generate',
    data=json.dumps({'model': '${MODEL}', 'prompt': 'hi', 'stream': False}).encode(),
    headers={'Content-Type': 'application/json'},
)
urllib.request.urlopen(req, timeout=60).read()
" 2>/dev/null && echo "  OK" || echo "  WARN: warm-up failed (demo may have slow first message)"

echo
echo "================================================================"
echo "  Pre-flight complete. Starting demo..."
echo "================================================================"
echo

python scripts/quick_test.py --url "$URL" --model "$MODEL"

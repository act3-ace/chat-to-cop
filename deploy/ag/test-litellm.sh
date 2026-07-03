#!/bin/bash
#
# test-litellm.sh -- Smoke test for the LiteLLM Bedrock proxy
#
# Tests: proxy reachable, models listed, chat completion works,
#        and (optionally) a chat-to-cop replay against the proxy.
#
# Usage:
#   bash test-litellm.sh                          # test localhost:4000
#   bash test-litellm.sh http://172.33.68.166:4000 # test remote AG node
#   LITELLM_URL=https://litellm-bedrock.ag.example.mil bash test-litellm.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LITELLM_URL="${1:-${LITELLM_URL:-http://127.0.0.1:4000}}"
# Strip trailing slash
LITELLM_URL="${LITELLM_URL%/}"

PASS=0
FAIL=0
SKIP=0

pass() { echo "  [PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "  [FAIL] $1"; FAIL=$((FAIL + 1)); }
skip() { echo "  [SKIP] $1"; SKIP=$((SKIP + 1)); }

echo "========================================"
echo "LiteLLM Bedrock Proxy Smoke Test"
echo "========================================"
echo "Endpoint: ${LITELLM_URL}"
echo "========================================"
echo ""

# ── 1. Health check ────────────────────────────────────────────────────
echo "1. Health check"
if curl -sf --max-time 10 "${LITELLM_URL}/health" > /dev/null 2>&1; then
    pass "Proxy health endpoint responding"
else
    fail "Proxy health endpoint not reachable at ${LITELLM_URL}/health"
    echo "   Is the proxy running? Try: bash deploy/ag/launch-litellm.sh"
    exit 1
fi

# ── 2. List models ────────────────────────────────────────────────────
echo "2. Model listing"
MODELS=$(curl -sf --max-time 10 "${LITELLM_URL}/v1/models" 2>/dev/null || echo "")
if [ -z "${MODELS}" ]; then
    fail "Could not retrieve model list"
else
    if echo "${MODELS}" | grep -q "claude-sonnet"; then
        pass "claude-sonnet model available"
    else
        fail "claude-sonnet not found in model list"
    fi
fi

# ── 3. Chat completion (claude-sonnet) ─────────────────────────────────
echo "3. Chat completion (claude-sonnet)"
HTTP_CODE=$(curl -s -o /tmp/litellm-test-response.json -w "%{http_code}" --max-time 60 \
    "${LITELLM_URL}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -d '{
        "model": "claude-sonnet",
        "messages": [
            {"role": "user", "content": "Reply with exactly: LITELLM SMOKE TEST OK"}
        ],
        "max_tokens": 20
    }' 2>/dev/null || echo "000")
RESPONSE=$(cat /tmp/litellm-test-response.json 2>/dev/null || echo "")

if [ "${HTTP_CODE}" = "000" ]; then
    fail "Chat completion timed out or connection refused"
elif [ "${HTTP_CODE}" = "200" ] && echo "${RESPONSE}" | grep -q "choices"; then
    pass "Chat completion succeeded (claude-sonnet)"
elif [ "${HTTP_CODE}" = "401" ] || echo "${RESPONSE}" | grep -qi "credentials\|authentication"; then
    skip "Chat completion returned auth error (expected without AWS credentials)"
elif echo "${RESPONSE}" | grep -q "error"; then
    ERROR_MSG=$(echo "${RESPONSE}" | python3 -c "import sys,json; print(json.load(sys.stdin).get('error',{}).get('message','unknown')[:120])" 2>/dev/null || echo "unknown")
    fail "Chat completion returned error (HTTP ${HTTP_CODE}): ${ERROR_MSG}"
else
    fail "Chat completion returned unexpected response (HTTP ${HTTP_CODE})"
fi

rm -f /tmp/litellm-test-response.json

# ── 4. chat-to-cop quick_test (optional) ───────────────────────────────
echo "4. chat-to-cop integration"
REPO_ROOT=$(cd "${SCRIPT_DIR}/../.." 2>/dev/null && pwd)
if python3 -c "import chat_to_cop" 2>/dev/null; then
    if [ -f "${REPO_ROOT}/scripts/quick_test.py" ]; then
        echo "   Running quick_test against proxy (may take 30-60s)..."
        if python3 "${REPO_ROOT}/scripts/quick_test.py" \
            --url "${LITELLM_URL}/v1" \
            --model claude-sonnet \
            2>&1 | tail -5; then
            pass "chat-to-cop quick_test succeeded"
        else
            fail "chat-to-cop quick_test failed"
        fi
    else
        skip "quick_test.py not found at ${REPO_ROOT}/scripts/quick_test.py"
    fi
else
    skip "chat-to-cop not installed (pip install -e '.[dev]' to enable)"
fi

# ── Summary ────────────────────────────────────────────────────────────
echo ""
echo "========================================"
echo "Results: ${PASS} passed, ${FAIL} failed, ${SKIP} skipped"
echo "========================================"

if [ "${FAIL}" -eq 0 ]; then
    echo ""
    echo "Proxy is working. Operator config for chat-to-cop:"
    echo ""
    echo "  export CHAT_TO_COP_LLM_URL=${LITELLM_URL}/v1"
    echo "  export CHAT_TO_COP_LLM_MODEL=claude-sonnet"
    echo ""
fi

[ "${FAIL}" -eq 0 ] && exit 0 || exit 1

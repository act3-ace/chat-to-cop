#!/usr/bin/env bash
# run.sh -- One-click launcher for chat-to-cop on macOS/Linux.
#
# Run this from a terminal. It will:
#   1. Check that Python and Ollama are available
#   2. Detect your GPU/chip and pick the best model
#   3. Install the package into a local .venv if needed
#   4. Pull and configure the LLM model
#   5. Run the smoke test
#   6. Replay the bundled DASH 3 sample
#   7. Start the dashboard and open it in your browser
#
# Usage:
#   bash run.sh                 - Full setup + demo replay
#   bash run.sh --smoke-only    - Just run the smoke test
#   bash run.sh --dashboard     - Start the dashboard only
#   bash run.sh --live URL      - Connect to a live IRC server
#   bash run.sh --cloud URL     - Use a remote LLM backend (skip local Ollama)

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# -------------------------------------------------------------------
# Function definitions (must come before any calls)
# -------------------------------------------------------------------

show_help() {
    echo ""
    echo "chat-to-cop -- AI staff officer for MASH wargame events"
    echo ""
    echo "Usage:"
    echo "  bash run.sh                     Full setup: smoke test + demo replay + dashboard"
    echo "  bash run.sh --smoke-only        Just verify everything works (30 seconds)"
    echo "  bash run.sh --dashboard         Start the REST API dashboard only"
    echo "  bash run.sh --live URL          Connect to a live IRC server"
    echo "  bash run.sh --cloud URL         Use a remote LLM backend (skip local Ollama)"
    echo ""
    echo "Chip/GPU Detection (macOS):"
    echo "  The script detects your hardware and picks the best model:"
    echo "    Apple Silicon 32GB+ RAM  ->  qwen2.5:14b (best quality)"
    echo "    Apple Silicon 16GB+ RAM  ->  qwen2.5:7b  (good quality)"
    echo "    Apple Silicon  <16GB RAM ->  qwen2.5:3b  (fast)"
    echo "    Intel Mac / no GPU       ->  qwen2.5:3b  (CPU fallback, slow)"
    echo ""
    echo "Cloud Backend (remote server):"
    echo "  bash run.sh --cloud http://SERVER_IP:PORT/v1"
    echo "  Skips Ollama entirely. Uses a remote LLM server for all inference."
    echo "  The model name is auto-detected from the server."
    echo ""
    echo "Prerequisites:"
    echo "  - Python 3.10+  (https://www.python.org/downloads/)"
    echo "  - Ollama        (https://ollama.com/download) -- not needed with --cloud"
    echo ""
    echo "First run will install dependencies and pull the LLM model (~4.7 GB)."
    echo "Subsequent runs start in seconds."
    echo ""
}

auto_update_pull() {
    if ! command -v git &>/dev/null; then return; fi
    echo "  Checking for updates..."
    if git pull --ff-only &>/dev/null 2>&1; then
        echo "  Updated to latest version."
        echo "  [OK] git pull succeeded" >> "$LOGFILE"
    else
        echo "  Could not auto-update -- running current version."
        echo "  [WARN] git pull failed" >> "$LOGFILE"
    fi
    echo ""
}

auto_update_convert() {
    if ! command -v git &>/dev/null; then return; fi
    echo "  Setting up auto-updates..."
    echo "  [AUTO-UPDATE] Converting zip to git clone" >> "$LOGFILE"
    if git init &>/dev/null \
        && git remote add origin git@gitlab.dle.afrl.af.mil:c2es1/mash/chat-to-cop.git &>/dev/null \
        && { echo "  Connecting to code server (may take a moment)..."; git fetch --depth 1 origin main &>/dev/null; } \
        && git checkout -f -B main origin/main &>/dev/null; then
        echo "  Auto-updates enabled -- future runs will pull the latest code."
        echo "  [OK] Converted zip to git clone" >> "$LOGFILE"
    else
        rm -rf .git 2>/dev/null || true
        echo "  Could not connect to code server -- continuing without auto-updates."
        echo "  (This is fine. Everything still works, you just won't get automatic updates.)"
        echo "  [WARN] zip-to-clone conversion failed" >> "$LOGFILE"
    fi
    echo ""
}

wait_for_ollama() {
    local tries=0
    while [[ $tries -lt 6 ]]; do
        tries=$(( tries + 1 ))
        sleep 5
        if curl -s --max-time 3 http://127.0.0.1:11434/v1/models &>/dev/null; then
            echo "  Ollama is running."
            return 0
        fi
        echo "  Waiting for Ollama to start... ($tries/6)"
    done
    echo "  Ollama did not respond after 30 seconds."
    echo "  On macOS: open the Ollama app from your Applications folder."
    echo "  [WARN] Ollama did not start after 30s" >> "$LOGFILE"
}

start_dashboard() {
    echo ""
    echo "============================================"
    echo "  Starting dashboard"
    echo "============================================"
    echo ""

    # Open browser after a short delay
    ( sleep 3 && { open "http://localhost:8000/docs" 2>/dev/null || xdg-open "http://localhost:8000/docs" 2>/dev/null || true; } ) &

    echo "Dashboard starting at http://localhost:8000/docs"
    echo "Press Ctrl+C to stop."
    echo ""
    uvicorn chat_to_cop.api:app --host 0.0.0.0 --port 8000
}

start_live() {
    echo ""
    echo "============================================"
    echo "  Connecting to live IRC: $LIVE_URL"
    echo "============================================"
    echo ""
    export CHAT_TO_COP_IRC_URL="$LIVE_URL"
    : "${CHAT_TO_COP_LLM_URL:=http://127.0.0.1:11434/v1}"
    : "${CHAT_TO_COP_LLM_MODEL:=$RECOMMENDED_MODEL}"
    export CHAT_TO_COP_DB_PATH="data/mash_live.db"
    echo "  Channel discovery: ON (auto-joins new IRC channels)"
    echo "  Glossary: ${CHAT_TO_COP_GLOSSARY_FILE:-none}"
    echo ""
    "$PYTHON_CMD" -m chat_to_cop.replay
}

health_summary() {
    echo ""
    echo "============================================"
    echo "  Health Summary"
    echo "============================================"
    echo ""
    echo "  Python:    $PYVER"
    echo "  Chip/GPU:  $GPU_NAME"
    echo "  Backend:   ${CHAT_TO_COP_LLM_URL:-not set}"
    echo "  Model:     ${CHAT_TO_COP_LLM_MODEL:-not set}"
    if [[ "$USE_CLOUD" -eq 1 ]]; then
        echo "  Mode:      Remote (cloud)"
    else
        echo "  Mode:      Local (Ollama)"
    fi
    echo "  Log file:  $PWD/$LOGFILE"
    echo ""
    {
        echo "  [SUMMARY] Python=$PYVER GPU=$GPU_NAME Backend=${CHAT_TO_COP_LLM_URL:-} Model=${CHAT_TO_COP_LLM_MODEL:-}"
        echo "  run.sh finished: $(date)"
    } >> "$LOGFILE"
    echo ""
    echo "If something went wrong, send data/run.log to the team for help."
    echo ""
}

# -------------------------------------------------------------------
# Main script starts here
# -------------------------------------------------------------------

mkdir -p data
LOGFILE="data/run.log"

{
    echo ""
    echo "================================================================"
    echo "  run.sh started: $(date)"
    echo "  Arguments: $*"
    echo "  User: $(whoami)"
    echo "  Machine: $(hostname)"
    echo "================================================================"
} >> "$LOGFILE"

# -------------------------------------------------------------------
# Parse arguments
# -------------------------------------------------------------------

CLOUD_URL=""
USE_CLOUD=0
MODE=""
LIVE_URL=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --help|-h)
            show_help
            exit 0
            ;;
        --cloud)
            if [[ -z "${2:-}" ]]; then
                echo "ERROR: --cloud requires a URL."
                echo "Usage: bash run.sh --cloud http://REMOTE_IP:PORT/v1"
                exit 1
            fi
            CLOUD_URL="$2"
            USE_CLOUD=1
            echo "  Using remote LLM backend: $2"
            echo "  [cloud] URL=$2" >> "$LOGFILE"
            shift 2
            ;;
        --smoke-only) MODE="smoke-only"; shift ;;
        --dashboard)  MODE="dashboard";  shift ;;
        --live)
            if [[ -z "${2:-}" ]]; then
                echo "ERROR: --live requires an IRC server URL."
                echo "Usage: bash run.sh --live ws://IRC_SERVER_IP:8097"
                exit 1
            fi
            MODE="live"
            LIVE_URL="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1"
            show_help
            exit 1
            ;;
    esac
done

# -------------------------------------------------------------------
# Preflight banner
# -------------------------------------------------------------------

echo ""
echo "============================================"
echo "  chat-to-cop setup"
echo "============================================"
echo ""

# -------------------------------------------------------------------
# Auto-update
# -------------------------------------------------------------------

if [[ -d ".git" ]]; then
    auto_update_pull
else
    auto_update_convert
fi

echo "[1/5] Checking prerequisites..."
echo ""

# -------------------------------------------------------------------
# Check Python
# -------------------------------------------------------------------

PYTHON_CMD=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        PYTHON_CMD="$cmd"
        break
    fi
done

if [[ -z "$PYTHON_CMD" ]]; then
    echo "  Python: NOT FOUND"
    echo "  [FAIL] Python not found" >> "$LOGFILE"
    echo ""
    echo "  Run \"bash setup.sh\" to install prerequisites automatically,"
    echo "  or install Python 3.12 manually from https://www.python.org/downloads/"
    echo ""
    exit 1
fi

PYVER="$($PYTHON_CMD --version 2>&1 | awk '{print $2}')"
echo "  Python: $PYVER"
echo "  [OK] Python $PYVER" >> "$LOGFILE"

# -------------------------------------------------------------------
# Check Git
# -------------------------------------------------------------------

if ! command -v git &>/dev/null; then
    echo "  Git: NOT FOUND"
    echo "  [FAIL] Git not found" >> "$LOGFILE"
    echo ""
    echo "  Run \"bash setup.sh\" to install prerequisites automatically,"
    echo "  or install Git manually: xcode-select --install"
    echo ""
    exit 1
fi

GITVER="$(git --version | awk '{print $3}')"
echo "  Git: $GITVER"
echo "  [OK] Git $GITVER" >> "$LOGFILE"

# -------------------------------------------------------------------
# Check Ollama (skip if using cloud backend)
# -------------------------------------------------------------------

OLLAMA_OK=0
if [[ "$USE_CLOUD" -eq 1 ]]; then
    echo "  Ollama: skipped (using remote backend)"
    echo "  [SKIP] Ollama -- cloud mode" >> "$LOGFILE"
else
    if ! command -v ollama &>/dev/null; then
        echo "  Ollama: NOT FOUND"
        echo "  [WARN] Ollama not found" >> "$LOGFILE"
        echo ""
        echo "  Ollama runs the local AI model and is needed for the demo."
        echo "  To install: run \"bash setup.sh\" or download from https://ollama.com/download"
        echo ""
        echo "  If you can't install Ollama, ask Scott for a remote server URL"
        echo "  and re-run as:  bash run.sh --cloud http://SERVER_IP:PORT/v1"
        echo ""
        read -rp "Continue without Ollama? (advanced -- only if you have a cloud LLM) [y/N] " SKIP_OLLAMA
        if [[ "${SKIP_OLLAMA,,}" != "y" ]]; then
            exit 1
        fi
    else
        echo "  Ollama: found"
        echo "  [OK] Ollama found" >> "$LOGFILE"
        OLLAMA_OK=1
    fi
fi

# -------------------------------------------------------------------
# GPU / chip detection -- pick the best model for this hardware
# -------------------------------------------------------------------

echo ""
echo "[2/5] Detecting GPU/chip..."

GPU_NAME="none"
RECOMMENDED_MODEL="qwen2.5:3b"
RECOMMENDED_PULL="qwen2.5:3b"
MODELFILE=""

ARCH="$(uname -m)"

if [[ "$ARCH" == "arm64" ]]; then
    # Apple Silicon -- unified memory is shared between CPU and GPU (Metal/MPS)
    TOTAL_RAM_BYTES="$(sysctl -n hw.memsize 2>/dev/null || echo 0)"
    TOTAL_RAM_GB=$(( TOTAL_RAM_BYTES / 1073741824 ))
    CHIP="$(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo "Apple Silicon")"
    GPU_NAME="$CHIP (${TOTAL_RAM_GB}GB unified)"
    echo "  Chip: $GPU_NAME"
    echo "  [OK] Apple Silicon: $GPU_NAME" >> "$LOGFILE"

    if [[ "$TOTAL_RAM_GB" -ge 32 ]]; then
        echo "  Recommended model: qwen2.5:14b (best quality for your chip)"
        RECOMMENDED_MODEL="qwen2.5:14b-8k"
        RECOMMENDED_PULL="qwen2.5:14b"
        MODELFILE="deploy/ollama/Modelfile.14b"
    elif [[ "$TOTAL_RAM_GB" -ge 16 ]]; then
        echo "  Recommended model: qwen2.5:7b (good match for your chip)"
        RECOMMENDED_MODEL="qwen2.5:7b-8k"
        RECOMMENDED_PULL="qwen2.5:7b"
        MODELFILE="deploy/ollama/Modelfile.7b"
    else
        echo "  Recommended model: qwen2.5:3b (your chip has limited RAM)"
    fi
else
    # Intel Mac or Linux x86 -- check for NVIDIA GPU
    if command -v nvidia-smi &>/dev/null && nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits &>/dev/null; then
        GPU_INFO="$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits | head -1)"
        GPU_NAME="$(echo "$GPU_INFO" | cut -d',' -f1 | xargs)"
        GPU_VRAM_MB="$(echo "$GPU_INFO" | cut -d',' -f2 | xargs)"
        echo "  GPU: $GPU_NAME (${GPU_VRAM_MB} MB VRAM)"
        echo "  [OK] GPU: $GPU_NAME, ${GPU_VRAM_MB} MB VRAM" >> "$LOGFILE"

        if [[ "$GPU_VRAM_MB" -ge 10240 ]]; then
            echo "  Recommended model: qwen2.5:14b (best quality for your GPU)"
            RECOMMENDED_MODEL="qwen2.5:14b-8k"
            RECOMMENDED_PULL="qwen2.5:14b"
            MODELFILE="deploy/ollama/Modelfile.14b"
        elif [[ "$GPU_VRAM_MB" -ge 6144 ]]; then
            echo "  Recommended model: qwen2.5:7b (good match for your GPU)"
            RECOMMENDED_MODEL="qwen2.5:7b-8k"
            RECOMMENDED_PULL="qwen2.5:7b"
            MODELFILE="deploy/ollama/Modelfile.7b"
        else
            echo "  Recommended model: qwen2.5:3b (your GPU has limited VRAM)"
        fi
    else
        GPU_NAME="none (CPU mode)"
        echo "  GPU: No supported GPU detected (CPU mode)"
        echo "  [WARN] No supported GPU" >> "$LOGFILE"
        if [[ "$USE_CLOUD" -eq 0 ]]; then
            echo ""
            echo "  WARNING: Without a GPU, local inference will be very slow (5-30x)."
            echo "  Consider using a remote backend instead:"
            echo "    bash run.sh --cloud http://REMOTE_SERVER:PORT/v1"
            echo ""
            echo "  Falling back to smallest model (qwen2.5:3b) for CPU..."
        fi
    fi
fi

echo "  [MODEL] $RECOMMENDED_MODEL" >> "$LOGFILE"

# -------------------------------------------------------------------
# Refresh supplemental glossary from live DELTRON (falls back to cached)
# -------------------------------------------------------------------

if [[ -z "${CHAT_TO_COP_GLOSSARY_FILE:-}" ]]; then
    echo "  Refreshing glossary from DELTRON..."
    if python scripts/bootstrap_mash_glossary.py &>/dev/null; then
        echo "  Glossary refreshed from DELTRON"
        echo "  [OK] Glossary refreshed from DELTRON" >> "$LOGFILE"
    else
        if [[ -f "data/mash_glossary.txt" ]]; then
            echo "  DELTRON unreachable -- using cached glossary"
            echo "  [WARN] DELTRON unreachable, using cached glossary" >> "$LOGFILE"
        else
            echo "  DELTRON unreachable -- no glossary available"
            echo "  [WARN] DELTRON unreachable, no glossary" >> "$LOGFILE"
        fi
    fi
    if [[ -f "data/mash_glossary.txt" ]]; then
        export CHAT_TO_COP_GLOSSARY_FILE="data/mash_glossary.txt"
    fi
fi

# -------------------------------------------------------------------
# Check Docker (optional)
# -------------------------------------------------------------------

if ! command -v docker &>/dev/null; then
    echo "  Docker: not found (optional -- needed only for containerized deployment)"
else
    DOCKVER="$(docker --version | awk '{print $3}' | tr -d ',')"
    echo "  Docker: $DOCKVER"
fi

echo ""

# -------------------------------------------------------------------
# Install package if needed (into .venv to avoid PEP 668 conflicts)
# -------------------------------------------------------------------

echo "[3/5] Checking chat-to-cop installation..."

VENV_DIR="$SCRIPT_DIR/.venv"

# Activate or create the virtual environment
if [[ -z "${VIRTUAL_ENV:-}" ]]; then
    if [[ ! -d "$VENV_DIR" ]]; then
        echo "  Creating virtual environment at .venv/ ..."
        "$PYTHON_CMD" -m venv "$VENV_DIR"
        echo "  [OK] venv created" >> "$LOGFILE"
    fi
    # shellcheck disable=SC1091
    source "$VENV_DIR/bin/activate"
fi
PYTHON_CMD="python"

# Ensure pip is up-to-date inside the venv
if ! python -m pip --version &>/dev/null; then
    echo "  pip not found. Bootstrapping pip..."
    python -m ensurepip --upgrade
    echo "  [FIX] Bootstrapped pip via ensurepip" >> "$LOGFILE"
fi

if ! python -c "from chat_to_cop.models.cop_update import CoPUpdate" &>/dev/null 2>&1; then
    if ls vendor/*.whl &>/dev/null 2>&1; then
        echo "  Installing package from bundled wheels (offline)..."
        python -m pip install --no-index --find-links vendor/ -e ".[dev]"
        echo "  [INSTALL] Used vendor wheels" >> "$LOGFILE"
    else
        echo "  Installing package (first run only, may take 2-5 minutes)..."
        python -m pip install -e ".[dev]"
    fi
    if [[ $? -ne 0 ]]; then
        echo ""
        echo "ERROR: Installation failed. Try running manually:"
        echo "  source .venv/bin/activate && pip install -e \".[dev]\""
        echo ""
        echo "  [FAIL] pip install failed" >> "$LOGFILE"
        exit 1
    fi
    echo "  Installed successfully."
    echo "  [OK] Package installed" >> "$LOGFILE"
else
    echo "  chat-to-cop: installed"
fi

# -------------------------------------------------------------------
# Configure LLM backend
# -------------------------------------------------------------------

CHAT_TO_COP_LLM_URL=""
CHAT_TO_COP_LLM_MODEL=""

if [[ "$USE_CLOUD" -eq 1 ]]; then
    echo ""
    echo "[4/5] Checking remote backend..."

    if ! curl -s --max-time 10 "${CLOUD_URL}/models" &>/dev/null && \
       ! curl -s --max-time 10 "${CLOUD_URL}" &>/dev/null; then
        echo "  WARNING: Remote backend at $CLOUD_URL is not responding."
        echo "  [WARN] Cloud backend unreachable: $CLOUD_URL" >> "$LOGFILE"
        echo "  The smoke test may fail. Check the URL and try again."
        echo ""
        read -rp "Continue anyway? [y/N] " CONTINUE_CLOUD
        if [[ "${CONTINUE_CLOUD,,}" != "y" ]]; then exit 1; fi
    fi

    echo "  Remote backend: $CLOUD_URL"
    CHAT_TO_COP_LLM_URL="$CLOUD_URL"

    if [[ -z "$CHAT_TO_COP_LLM_MODEL" ]]; then
        echo "  Detecting available models..."
        CHAT_TO_COP_LLM_MODEL="$(python -c "
import json, urllib.request
try:
    d = json.load(urllib.request.urlopen('${CLOUD_URL}/models'))
    print(d['data'][0]['id'])
except Exception:
    print('qwen2.5:7b')
" 2>/dev/null || echo "qwen2.5:7b")"
        echo "  Detected model: $CHAT_TO_COP_LLM_MODEL"
    fi
    echo "  [BACKEND] Cloud: $CLOUD_URL, model: $CHAT_TO_COP_LLM_MODEL" >> "$LOGFILE"

elif [[ "$OLLAMA_OK" -eq 1 ]]; then
    echo ""
    echo "[4/5] Checking Ollama model..."

    if ! curl -s http://127.0.0.1:11434/v1/models &>/dev/null; then
        echo "  Ollama is installed but not running."
        echo "  Starting Ollama..."
        ollama serve &>/dev/null &
        wait_for_ollama
    fi

    if ! ollama list 2>/dev/null | grep -qF "$RECOMMENDED_MODEL"; then
        echo "  Pulling $RECOMMENDED_PULL (this may take a few minutes on first run)..."
        ollama pull "$RECOMMENDED_PULL"
        if [[ -n "$MODELFILE" ]] && [[ -f "$MODELFILE" ]]; then
            echo "  Creating 8K context variant..."
            ollama create "$RECOMMENDED_MODEL" -f "$MODELFILE"
        fi
    else
        echo "  Model $RECOMMENDED_MODEL: ready"
    fi

    CHAT_TO_COP_LLM_URL="http://127.0.0.1:11434/v1"
    CHAT_TO_COP_LLM_MODEL="$RECOMMENDED_MODEL"
    echo "  [BACKEND] Ollama local: $RECOMMENDED_MODEL" >> "$LOGFILE"
else
    echo ""
    echo "[4/5] Skipping backend setup (no Ollama, no cloud URL)"
fi

export CHAT_TO_COP_LLM_URL
export CHAT_TO_COP_LLM_MODEL

# -------------------------------------------------------------------
# Route to the requested mode
# -------------------------------------------------------------------

if [[ "$MODE" == "dashboard" ]]; then
    start_dashboard
    exit 0
fi

if [[ "$MODE" == "live" ]]; then
    start_live
    exit 0
fi

# -------------------------------------------------------------------
# Smoke test (runs for both default and --smoke-only)
# -------------------------------------------------------------------

if [[ "$USE_CLOUD" -eq 0 ]] && [[ "$OLLAMA_OK" -eq 1 ]]; then
    if ! curl -s --max-time 5 http://127.0.0.1:11434/v1/models &>/dev/null; then
        echo ""
        echo "  WARNING: Ollama is not responding at http://127.0.0.1:11434"
        echo "  Try starting Ollama manually: open the Ollama app from Applications."
        echo "  Then re-run this script."
        echo ""
        read -rp "Continue anyway? [y/N] " SKIP_SMOKE
        if [[ "${SKIP_SMOKE,,}" != "y" ]]; then exit 1; fi
    fi
fi

echo ""
echo "============================================"
echo "  Running smoke test (5 messages)"
echo "============================================"
echo ""
python scripts/quick_test.py --url "$CHAT_TO_COP_LLM_URL" --model "$CHAT_TO_COP_LLM_MODEL"
SMOKE_EXIT=$?

if [[ $SMOKE_EXIT -ne 0 ]]; then
    echo ""
    echo "============================================"
    echo "  Smoke test failed"
    echo "============================================"
    echo ""
    if [[ "$USE_CLOUD" -eq 0 ]]; then
        echo "  Your local LLM backend did not respond correctly."
        echo "  Common fixes:"
        echo "    1. Make sure Ollama is running (open the Ollama app from Applications)"
        echo "    2. Try a smaller model:  ollama pull qwen2.5:3b"
        echo "    3. Use a remote server instead:"
        echo ""
        echo "       bash run.sh --cloud http://SERVER_IP:PORT/v1"
        echo ""
        echo "  Ask Scott for a remote server URL if you need one."
    else
        echo "  The remote backend at $CLOUD_URL did not respond correctly."
        echo "  Check that the URL is correct and the server is running."
    fi
    echo ""
    echo "  Full log saved to: $PWD/$LOGFILE"
    echo "  [FAIL] Smoke test failed" >> "$LOGFILE"
    health_summary
    exit 1
fi

echo "  [OK] Smoke test passed" >> "$LOGFILE"

if [[ "$MODE" == "smoke-only" ]]; then
    health_summary
    exit 0
fi

# -------------------------------------------------------------------
# Replay DASH 3 sample data
# -------------------------------------------------------------------

echo ""
echo "============================================"
echo "  Replaying DASH 3 sample data"
echo "============================================"
echo ""
echo "Processing bundled chat messages through the pipeline..."
echo "(This takes 2-5 minutes depending on your hardware)"
echo ""

python -m chat_to_cop.replay data/dash3/23Sep_usaf_chat.zip \
    --db data/demo_run.db \
    --url "$CHAT_TO_COP_LLM_URL" \
    --model "$CHAT_TO_COP_LLM_MODEL"

# -------------------------------------------------------------------
# Start dashboard
# -------------------------------------------------------------------

start_dashboard

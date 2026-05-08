#!/usr/bin/env bash
# setup.sh -- Install prerequisites for chat-to-cop on macOS/Linux.
#
# This script checks for Python, Git, Ollama, and Docker and offers to
# install any that are missing using Homebrew (macOS) or system package
# managers (Linux). It will NOT reinstall or reconfigure anything already
# present.
#
# After prerequisites are installed, run "run.sh" to start the pipeline.
#
# Usage:
#   bash setup.sh              - Interactive: check and install missing prereqs
#   bash setup.sh --check      - Just check what's installed, don't install
#   bash setup.sh --help       - Show this help

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

CHECK_ONLY=0

show_help() {
    echo ""
    echo "setup.sh -- Install prerequisites for chat-to-cop"
    echo ""
    echo "Usage:"
    echo "  bash setup.sh              Check and install missing prerequisites"
    echo "  bash setup.sh --check      Just check what's installed, don't install"
    echo "  bash setup.sh --help       Show this help"
    echo ""
    echo "Checks for: Python 3.10+, Git, Ollama"
    echo "Installs missing tools via Homebrew (macOS) or apt/dnf (Linux)."
    echo "Will NOT reinstall or reconfigure anything already present."
    echo ""
    echo "If Homebrew is not available, prints manual download links instead."
    echo ""
}

for arg in "$@"; do
    case "$arg" in
        --help|-h) show_help; exit 0 ;;
        --check)   CHECK_ONLY=1 ;;
    esac
done

echo ""
echo "============================================"
echo "  chat-to-cop prerequisite check"
echo "============================================"
echo ""

MISSING_PYTHON=0
MISSING_GIT=0
MISSING_OLLAMA=0
HAS_BREW=0

# -------------------------------------------------------------------
# Detect package manager
# -------------------------------------------------------------------

if command -v brew &>/dev/null; then
    HAS_BREW=1
fi

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
    echo "  Python:  NOT FOUND"
    MISSING_PYTHON=1
else
    PYVER="$($PYTHON_CMD --version 2>&1 | awk '{print $2}')"
    PY_MAJOR="${PYVER%%.*}"
    PY_REST="${PYVER#*.}"
    PY_MINOR="${PY_REST%%.*}"
    echo "  Python:  $PYVER"
    if [[ "$PY_MAJOR" -lt 3 ]] || { [[ "$PY_MAJOR" -eq 3 ]] && [[ "$PY_MINOR" -lt 10 ]]; }; then
        echo "           WARNING: Python 3.10+ required, you have $PYVER"
        MISSING_PYTHON=1
    fi
fi

# -------------------------------------------------------------------
# Check Git
# -------------------------------------------------------------------

if ! command -v git &>/dev/null; then
    echo "  Git:     NOT FOUND"
    MISSING_GIT=1
else
    GITVER="$(git --version | awk '{print $3}')"
    echo "  Git:     $GITVER"
fi

# -------------------------------------------------------------------
# Check Ollama
# -------------------------------------------------------------------

if ! command -v ollama &>/dev/null; then
    echo "  Ollama:  NOT FOUND"
    MISSING_OLLAMA=1
else
    echo "  Ollama:  found"
fi

# -------------------------------------------------------------------
# Check Docker (optional)
# -------------------------------------------------------------------

if ! command -v docker &>/dev/null; then
    echo "  Docker:  not found (optional)"
else
    DOCKVER="$(docker --version | awk '{print $3}' | tr -d ',')"
    echo "  Docker:  $DOCKVER"
fi

# -------------------------------------------------------------------
# Summary
# -------------------------------------------------------------------

echo ""

TOTAL_MISSING=$(( MISSING_PYTHON + MISSING_GIT + MISSING_OLLAMA ))

if [[ "$TOTAL_MISSING" -eq 0 ]]; then
    echo "All required prerequisites are installed."
    echo ""
    echo "Run \"bash run.sh\" to start the pipeline."
    echo ""
    exit 0
fi

if [[ "$CHECK_ONLY" -eq 1 ]]; then
    echo "Missing $TOTAL_MISSING required prerequisite(s)."
    [[ "$MISSING_PYTHON" -eq 1 ]] && echo "  - Python 3.10+"
    [[ "$MISSING_GIT" -eq 1 ]]    && echo "  - Git"
    [[ "$MISSING_OLLAMA" -eq 1 ]] && echo "  - Ollama"
    echo ""
    exit 1
fi

# -------------------------------------------------------------------
# Install missing prerequisites
# -------------------------------------------------------------------

echo "Missing $TOTAL_MISSING required prerequisite(s). Attempting to install..."
echo ""

if [[ "$HAS_BREW" -eq 0 ]]; then
    echo "  Homebrew (macOS package manager) is not available on this machine."
    echo ""
    echo "  Please install the missing tools manually:"
    echo ""
    if [[ "$MISSING_PYTHON" -eq 1 ]]; then
        echo "  Python 3.12:  https://www.python.org/downloads/"
        echo "                Or install Homebrew first: https://brew.sh"
        echo ""
    fi
    if [[ "$MISSING_GIT" -eq 1 ]]; then
        echo "  Git:          https://git-scm.com/downloads"
        echo "                Or: xcode-select --install"
        echo ""
    fi
    if [[ "$MISSING_OLLAMA" -eq 1 ]]; then
        echo "  Ollama:       https://ollama.com/download"
        echo "                Click \"Download for Mac\" and run the installer."
        echo ""
    fi
    echo "  After installing, close and reopen this terminal, then run this"
    echo "  script again to verify."
    echo ""
    exit 1
fi

# -------------------------------------------------------------------
# Install with Homebrew
# -------------------------------------------------------------------

INSTALL_COUNT=0
INSTALL_FAILED=0

do_install() {
    local pkg="$1"
    local display_name="$2"
    local manual_url="$3"
    echo "--------------------------------------------"
    echo "  Installing $display_name..."
    echo "--------------------------------------------"
    echo ""
    if brew install "$pkg"; then
        echo "  $display_name installed."
        INSTALL_COUNT=$(( INSTALL_COUNT + 1 ))
    else
        echo ""
        echo "  $display_name install failed."
        echo "  You may need to install manually from: $manual_url"
        echo ""
        INSTALL_FAILED=$(( INSTALL_FAILED + 1 ))
    fi
    echo ""
}

if [[ "$MISSING_PYTHON" -eq 1 ]]; then
    do_install "python@3.12" "Python 3.12" "https://www.python.org/downloads/"
fi
if [[ "$MISSING_GIT" -eq 1 ]]; then
    do_install "git" "Git" "https://git-scm.com/downloads"
fi
if [[ "$MISSING_OLLAMA" -eq 1 ]]; then
    # Ollama is available as a cask on macOS
    echo "--------------------------------------------"
    echo "  Installing Ollama..."
    echo "--------------------------------------------"
    echo ""
    if brew install --cask ollama 2>/dev/null || brew install ollama 2>/dev/null; then
        echo "  Ollama installed."
        INSTALL_COUNT=$(( INSTALL_COUNT + 1 ))
    else
        echo "  Ollama brew install failed."
        echo "  Install manually from: https://ollama.com/download"
        INSTALL_FAILED=$(( INSTALL_FAILED + 1 ))
    fi
    echo ""
fi

# -------------------------------------------------------------------
# Post-install
# -------------------------------------------------------------------

echo "============================================"
echo "  Setup complete"
echo "============================================"
echo ""

if [[ "$INSTALL_COUNT" -gt 0 ]]; then
    echo "  Installed $INSTALL_COUNT package(s)."
fi
if [[ "$INSTALL_FAILED" -gt 0 ]]; then
    echo "  $INSTALL_FAILED package(s) failed to install (see above)."
fi

if [[ "$INSTALL_COUNT" -gt 0 ]]; then
    echo ""
    echo "  Newly installed programs may need a fresh terminal to appear on PATH."
    echo "  Close this terminal and open a new one, then run \"bash run.sh\"."
elif [[ "$INSTALL_FAILED" -eq 0 ]]; then
    echo "  Everything was already installed. Run \"bash run.sh\" to start."
fi

echo ""

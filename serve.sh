#!/usr/bin/env bash
# Expose chat-to-cop API + dashboard on the network.
# Run this in a second terminal while the pipeline is running.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

CHAT_TO_COP_DB_PATH="data/world_state.db"
if [[ -f "data/mash_live.db" ]]; then
    CHAT_TO_COP_DB_PATH="data/mash_live.db"
fi
export CHAT_TO_COP_DB_PATH

echo ""
echo " chat-to-cop API server"
echo " ----------------------"
echo " Dashboard:  http://localhost:8000/dashboard"
echo " Swagger:    http://localhost:8000/docs"
echo " Updates:    http://localhost:8000/updates"
echo ""
echo " For others on the network, replace \"localhost\" with your IP."
echo " Run: ipconfig getifaddr en0   (or: ifconfig | grep 'inet ')"
echo ""

uvicorn chat_to_cop.api:app --host 0.0.0.0 --port 8000

# chat-to-cop

AI staff officer pipeline for wargame exercises. Monitors IRC chat channels,
uses a local LLM to extract structured world-state information (tracks, fuel
states, weapons status, CSAR events, ISR contacts), and writes updates to a
queryable database.

Designed for MASH/DASH-style multi-team air operations exercises where typed
chat and speech-to-text channels carry real-time operational traffic.

## Quick Start

```bash
# Start all services (GPU mode)
docker compose up -d

# Pull the LLM model (one-time, ~4.7 GB download)
docker compose exec ollama ollama pull qwen2.5:7b

# Watch the pipeline process demo messages
docker compose logs -f pipeline

# Browse the REST API
open http://localhost:8001/docs
```

The demo starts automatically with canned exercise messages (fuel reports,
weapons status, CSAR events, ISR contacts, STT voice transcriptions).

## CPU-Only Mode

If you do not have an NVIDIA GPU, remove the `deploy` block from the `ollama`
service in `docker-compose.yml`:

```yaml
  ollama:
    image: ollama/ollama:latest
    # Remove or comment out the entire deploy section:
    # deploy:
    #   resources:
    #     reservations:
    #       devices:
    #         - driver: nvidia
    #           count: 1
    #           capabilities: [gpu]
```

CPU inference works but is significantly slower (30-60s per message vs sub-second
with GPU).

## Configuration

Configuration is via environment variables, set in `docker-compose.yml` or a
`.env` file in the same directory.

| Variable | Default | Description |
|----------|---------|-------------|
| `CHAT_TO_COP_LLM_MODEL` | `qwen2.5:7b` | Ollama model for extraction |
| `CHAT_TO_COP_IRC_URL` | `ws://mock-irc:8097` | IRC WebSocket URL |
| `CHAT_TO_COP_IRC_CHANNELS` | `#c2_coord,#fires,...` | Comma-separated channel list |
| `CHAT_TO_COP_DB_PATH` | `/app/data/world_state.db` | SQLite database path |

## Connecting to a Real IRC Server

1. Edit `docker-compose.yml`
2. Change `CHAT_TO_COP_IRC_URL` in the pipeline service to your IRC WebSocket
   URL (e.g., `ws://10.0.0.1:8097`)
3. Update `CHAT_TO_COP_IRC_CHANNELS` to match the channels on your server
4. Remove the `mock-irc` service and its `depends_on` reference in `pipeline`
5. Restart: `docker compose up -d`

## Building from Source

```bash
git clone https://github.com/act3-ace/chat-to-cop.git
cd chat-to-cop
pip install -e ".[dev]"

# Run tests
pytest tests/ -k "not integration" -v

# Build Docker image
docker build -t chat-to-cop .
```

## Architecture

The pipeline deploys one stateful agent per IRC channel. Each agent maintains a
conversation window, learns speaker patterns, and uses a degrading backend stack
(LLM -> smaller LLM -> regex -> passthrough) so that extraction never stops, even
if the LLM goes down.

A fusion layer correlates reports across channels (typed chat confirming STT, ISR
reports linking to fire missions) and suppresses duplicates.

```
IRC channels --> Channel Agents (one per channel, LLM-backed)
                    |
                 Fusion Agent (cross-channel correlation)
                    |
                 World State Store (SQLite + REST API)
```

## Ports

| Port | Service |
|------|---------|
| 11434 | Ollama LLM API |
| 8097 | Mock IRC server (demo only) |
| 8001 | REST API + interactive docs |

## Stopping

```bash
docker compose down
```

## License

Apache 2.0

# Quickstart Guide

Get the chat-to-cop pipeline running and see results in 15 minutes.

## Prerequisites

- Python 3.10+
- Git
- 8 GB RAM minimum (16 GB recommended for larger models)
- Optional: GPU with 6+ GB VRAM for faster inference

## Windows One-Click Setup

If you're on Windows 10+ and want the fastest path to a working setup:

**Option A -- PowerShell one-liner** (clones the repo and installs everything).
Open PowerShell (right-click the Start button > **Terminal**), paste this, hit Enter:

```powershell
irm https://raw.githubusercontent.com/act3-ace/chat-to-cop/main/scripts/bootstrap.ps1 | iex
```

**Option B -- Already have the repo?** Double-click `setup.bat` in the repo root.
It checks what's installed and uses `winget` to install anything missing. Then
run `run.bat` for the full demo.

Both options check for existing installations first and will not reinstall or
reconfigure tools you already have.

If either option fails (enterprise-locked winget, no admin rights), fall back to
the manual steps below.

## Step 1: Clone and Install

```bash
git clone https://github.com/act3-ace/chat-to-cop.git
cd chat-to-cop
python -m pip install -e ".[dev]"
```

On Windows, if `python` is not found, re-run the Python installer and check
"Add python.exe to PATH".

Verify:

```bash
python -c "from chat_to_cop.models.cop_update import CoPUpdate; print('OK')"
```

## Step 2: Install Ollama and Pull a Model

**Linux / macOS:**

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

**Windows:** Download from [ollama.com/download](https://ollama.com/download) and run the installer.

**Pull a model** (3B runs on CPU, fast enough for development):

```bash
ollama pull qwen2.5:3b
# For better quality (needs 6+ GB VRAM):
ollama pull qwen2.5:7b
```

**Verify** Ollama is running:

```bash
# Linux/macOS
curl http://127.0.0.1:11434/v1/models
# Windows (PowerShell)
Invoke-RestMethod http://127.0.0.1:11434/v1/models
# Or open http://127.0.0.1:11434/v1/models in a browser
```

## Step 3: Run the Smoke Test

Sends 5 real DASH 3 messages through the extraction pipeline:

```bash
python scripts/quick_test.py
python scripts/quick_test.py --model qwen2.5:7b                    # different model
python scripts/quick_test.py --url http://192.168.1.100:11434/v1   # remote Ollama
```

Expected output (varies by model):

```
INPUT:  [#c2_coord] HYDRO_SL: SITREP / AIR: ZEUS 12,13,14 shot down by TTG
OUTPUT: type=sitrep, confidence=0.85, method=llm
        entity: {'callsign': 'ZEUS12', 'operational_status': 'DESTROYED'}
        ...
```

If you see `type=` lines with extracted entities, it works. Move on.

## Step 4: Run a DASH Replay

Replay real DASH 3 chat logs through the full pipeline. A sample chat.zip is included:

```bash
python -m chat_to_cop.replay data/dash3/23Sep_usaf_chat.zip
python -m chat_to_cop.replay data/dash3/23Sep_usaf_chat.zip --model qwen2.5:7b  # override model
python -m chat_to_cop.replay path/to/chat.zip --speed 1.0   # real-time (0=instant, 2.0=2x)
python -m chat_to_cop.replay path/to/chat.zip --db data/test_run.db  # custom output DB
```

Output goes to `data/world_state.db` (SQLite) by default. For the full DASH
dataset, ask a teammate or download from Pydio (see `docs/DATA_SOURCES.md`).

## Step 5: View Results

Start the FastAPI server to browse extracted world state:

```bash
uvicorn chat_to_cop.api:app --reload
```

| Endpoint | What it shows |
|----------|---------------|
| http://localhost:8000/health | System health + counts |
| http://localhost:8000/updates | Recent CoPUpdates (JSON) |
| http://localhost:8000/updates?channel=%23c2_coord | Filter by channel |
| http://localhost:8000/updates?limit=10 | Last 10 updates |
| http://localhost:8000/entities | All tracked entities |
| http://localhost:8000/entities/ZEUS12 | Single entity by callsign |
| http://localhost:8000/stats | Extraction metrics |
| http://localhost:8000/docs | Interactive Swagger UI |

## Step 6: Run the Eval Harness

Measures extraction quality (precision, recall, F1) against synthetic labeled data.

### With Groq (free, fast, no GPU needed)

1. Get a free API key at [console.groq.com](https://console.groq.com)
2. Set the key:

```bash
# Linux/macOS
export GROQ_API_KEY=gsk_your_key_here
# Windows (PowerShell)
$env:GROQ_API_KEY = "gsk_your_key_here"
```

3. Run evals:

```bash
# Single model
python scripts/eval_models.py \
    --url https://api.groq.com/openai/v1 --model qwen/qwen3-32b --count 50 --verbose

# Compare all configured models (qwen3-32b, llama-3.3-70b, llama-4-scout, llama-3.1-8b)
python scripts/eval_models.py --compare --count 50 --rate-delay 18
```

Use `--rate-delay 18` if you hit Groq's free-tier rate limit (429 errors).

### With local Ollama

```bash
python scripts/eval_models.py --url http://127.0.0.1:11434/v1 --model qwen2.5:3b --count 30 --verbose
```

### Replay test (real DASH data)

```bash
python scripts/replay_test.py --count 30
python scripts/replay_test.py --count 50 --model qwen2.5:7b
```

## Step 7: Run Tests

```bash
ruff check src/ tests/ && ruff format --check src/ tests/ && pytest tests/ -k "not integration" -v
```

Current test count: 1145 passing.

## Configuration

All settings can be overridden with environment variables using the `CHAT_TO_COP_` prefix:

| Variable | Default | Description |
|----------|---------|-------------|
| `CHAT_TO_COP_LLM_URL` | `http://127.0.0.1:11434/v1` | LLM endpoint |
| `CHAT_TO_COP_LLM_MODEL` | `qwen2.5:7b` | Primary model |
| `CHAT_TO_COP_FALLBACK_MODEL` | `qwen2.5:3b` | Smaller fallback model |
| `CHAT_TO_COP_LLM_API_KEY` | `not-needed` | API key (set for Groq/OpenAI) |
| `CHAT_TO_COP_LLM_TIMEOUT` | `120.0` | Seconds per LLM call |
| `CHAT_TO_COP_LLM_NUM_CTX` | `8192` | Context window size for Ollama |
| `CHAT_TO_COP_DB_PATH` | `data/world_state.db` | SQLite database path |
| `CHAT_TO_COP_METRICS` | `true` | Enable instrumentation |
| `CHAT_TO_COP_COP_API_URL` | (empty=dry-run) | CoP REST API URL |
| `CHAT_TO_COP_COP_AUTO_THRESHOLD` | `0.7` | Confidence for auto-write to CoP |

## Docker (alternative)

### Linux / macOS

```bash
docker compose up
```

Builds the container, starts FastAPI on port 8000, uses Ollama on the host.

```bash
docker run -v ./data:/app/data chat-to-cop python -m chat_to_cop.replay /app/data/chat.zip
```

### Windows (Docker Desktop)

1. Install [Docker Desktop](https://www.docker.com/products/docker-desktop/)
   with the WSL 2 backend enabled.
2. For GPU support, install the
   [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html).
   Requires NVIDIA drivers on Windows (not inside WSL). Verify with `nvidia-smi`.
3. Restart Windows if prompted, then verify:

```bash
docker --version && docker compose version
```

### Start the stack (all platforms)

```bash
docker compose up -d                      # with GPU (RTX 3060+)
docker compose --profile cpu up -d        # CPU-only (slower)
docker compose exec ollama ollama pull qwen2.5:7b   # pull a model
docker compose exec chat-to-cop python -m chat_to_cop.replay /app/data/chat.zip
docker compose down                       # stop
```

## What You Need from the Team

| Item | Who to ask | Why |
|------|-----------|-----|
| DASH 3 chat.zip test data | Scott | Test replays before MASH |
| AWS GovCloud creds (optional) | Jared | Bedrock cloud inference |
| IRC server IP at MASH | White cell / exercise staff | Live mode at the event |
| CoP database endpoint (optional) | Sarah Bowman | Write to the actual CoP |

## Troubleshooting

### "Connection refused" when running smoke test

Ollama is not running. Start it:

```bash
systemctl start ollama           # Linux
# macOS: starts automatically -- check Activity Monitor for "ollama"
ollama serve                     # Windows: or start from the Start menu
```

### "Model not found" errors

```bash
ollama pull qwen2.5:3b   # pull the model
ollama list               # check what's available
```

### Ollama context window (num_ctx)

Ollama defaults to a 2048-token context window, too small for our system
prompt plus conversation history. The pipeline sets `num_ctx=8192` via the
OpenAI SDK's `extra_body` parameter automatically.

For production deployments where you want this guaranteed, create a derived
model:

```bash
ollama create qwen2.5:7b-8k -f deploy/ollama/Modelfile.7b
python -m chat_to_cop.replay path/to/chat.zip --model qwen2.5:7b-8k
```

### Slow performance (>5s per message)

- Use a smaller model: `qwen2.5:3b` instead of `7b`
- Check GPU utilization: `nvidia-smi` (Linux) or Task Manager (Windows)
- Ollama falls back to CPU if no GPU is detected -- 5-10x slower

### Import errors / "No module named chat_to_cop"

```bash
pip install -e ".[dev]"   # must be in dev mode
cd chat-to-cop            # run from project root, not inside src/
```

If using a virtual environment, make sure it is activated.

### Groq rate limit errors (429)

```bash
python scripts/eval_models.py --rate-delay 18 --count 30
```

Waits 18 seconds between LLM calls. A 50-message eval takes ~10 minutes.

### Docker won't start (Windows)

Enable virtualization in BIOS (VT-x / AMD-V). WSL 2 requires it.

### Pipeline produces all "passthrough" results

LLM not responding. Check Ollama is running (`ollama list`) and the model is
loaded. The pipeline still works via regex fallback, but quality is lower.

### Ollama model pull hangs

Large downloads are 4-8 GB. On slow connections, use `qwen2.5:3b` (2 GB).

### DASH data not found

A sample is bundled at `data/dash3/23Sep_usaf_chat.zip`. For the full dataset,
ask a teammate or download from Pydio (see [DATA_SOURCES.md](DATA_SOURCES.md)).

## Next Steps

- [ARCHITECTURE.md](ARCHITECTURE.md) -- agent pipeline design
- [research/CHAT_DATA_ANALYSIS.md](research/CHAT_DATA_ANALYSIS.md) -- all 13 update types
- [research/BENCHMARK_RESULTS.md](research/BENCHMARK_RESULTS.md) -- model evaluation results
- [LABELING_GUIDE.md](LABELING_GUIDE.md) -- ground truth labeling
- [DESIGN_PHILOSOPHY.md](DESIGN_PHILOSOPHY.md) -- equifinality and anti-ontology principles
- Check GitLab issues for current sprint work

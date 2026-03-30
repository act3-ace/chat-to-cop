# Quickstart Guide

Get the chat-to-cop pipeline running and see results in 15 minutes.

## Prerequisites

- Python 3.10+
- Git
- 8 GB RAM minimum (16 GB recommended for larger models)
- Optional: GPU with 6+ GB VRAM for faster inference

## Step 1: Clone and Install

```bash
git clone https://gitlab.dle.afrl.af.mil/c2es1/mash/chat-to-cop.git
cd chat-to-cop
pip install -e ".[dev]"
```

Verify installation:

```bash
python -c "from chat_to_cop.models.cop_update import CoPUpdate; print('OK')"
```

## Step 2: Install Ollama and Pull a Model

### Linux / macOS

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

### Windows

Download from [ollama.com/download](https://ollama.com/download) and run the installer.

### Pull a model

Start small. The 3B model runs on CPU and is fast enough for development:

```bash
ollama pull qwen2.5:3b
```

For better extraction quality (needs 6+ GB VRAM):

```bash
ollama pull qwen2.5:7b
```

Verify Ollama is running:

```bash
curl http://127.0.0.1:11434/v1/models
```

You should see JSON listing the pulled model(s).

## Step 3: Run the Smoke Test

The smoke test sends 5 real DASH 3 messages through the extraction pipeline:

```bash
python scripts/quick_test.py
```

Use a different model:

```bash
python scripts/quick_test.py --model qwen2.5:7b
```

Point at a remote Ollama instance:

```bash
python scripts/quick_test.py --url http://192.168.1.100:11434/v1
```

Expected output (varies by model):

```
============================================================
SMOKE TEST: qwen2.5:3b @ http://127.0.0.1:11434/v1
============================================================

INPUT:  [#c2_coord] HYDRO_SL: SITREP / AIR: ZEUS 12,13,14 shot down by TTG; YAMA11 flight shot down by TTG
OUTPUT: type=sitrep, confidence=0.85, method=llm
        entity: {'callsign': 'ZEUS12', 'operational_status': 'DESTROYED'}
        entity: {'callsign': 'ZEUS13', 'operational_status': 'DESTROYED'}
        ...

INPUT:  [#c2_coord] VEGAS_ABM2: .
OUTPUT: [filtered -- no world-state change detected]
```

If you see `type=` lines with extracted entities, it works. Move on.

## Step 4: Run a DASH Replay

Replay real DASH 3 chat logs through the full pipeline (supervisor + fusion + store):

```bash
python -m chat_to_cop.replay data/chat/Dash3-GBC/Data/23Sep/usaf/chat.zip
```

If you don't have the DASH data yet, download it first:

```bash
python scripts/explore_and_download_chat.py download --output data/chat
```

Override the model or endpoint:

```bash
python -m chat_to_cop.replay data/chat/Dash3-GBC/Data/23Sep/usaf/chat.zip \
    --url http://127.0.0.1:11434/v1 \
    --model qwen2.5:7b
```

Control playback speed:

```bash
# Instant (default) -- processes as fast as the model allows
python -m chat_to_cop.replay path/to/chat.zip --speed 0

# Real-time -- simulates live message rate
python -m chat_to_cop.replay path/to/chat.zip --speed 1.0

# 2x speed
python -m chat_to_cop.replay path/to/chat.zip --speed 2.0
```

Output goes to `data/world_state.db` (SQLite) by default. Override with `--db`:

```bash
python -m chat_to_cop.replay path/to/chat.zip --db /tmp/test_run.db
```

## Step 5: View Results

Start the FastAPI server to browse extracted world state:

```bash
uvicorn chat_to_cop.api:app --reload
```

Then open your browser or use curl:

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

Example:

```bash
curl http://localhost:8000/updates?limit=3 | python -m json.tool
```

## Step 6: Run the Eval Harness

The eval harness measures extraction quality (precision, recall, F1) using synthetic labeled data. It works with any OpenAI-compatible endpoint.

### With Groq (free, fast, no GPU needed)

1. Get a free API key at [console.groq.com](https://console.groq.com)
2. Set the key:

```bash
# Linux/macOS
export GROQ_API_KEY=gsk_your_key_here

# Windows (PowerShell)
$env:GROQ_API_KEY = "gsk_your_key_here"
```

3. Run a single model eval:

```bash
python scripts/eval_models.py \
    --url https://api.groq.com/openai/v1 \
    --model qwen/qwen3-32b \
    --count 50 \
    --verbose
```

Note: Groq's free tier has rate limits. Use `--rate-delay 18` if you get 429 errors:

```bash
python scripts/eval_models.py \
    --url https://api.groq.com/openai/v1 \
    --model qwen/qwen3-32b \
    --count 50 \
    --rate-delay 18
```

4. Compare multiple models:

```bash
python scripts/eval_models.py --compare --count 50 --rate-delay 18
```

This runs all configured Groq models (qwen3-32b, llama-3.3-70b, llama-4-scout, llama-3.1-8b) and prints a comparison table.

### With local Ollama

```bash
python scripts/eval_models.py \
    --url http://127.0.0.1:11434/v1 \
    --model qwen2.5:3b \
    --count 30 \
    --verbose
```

### Replay test (real DASH data)

The replay test runs N real DASH 3 messages through the pipeline and reports latency and update type distribution:

```bash
python scripts/replay_test.py --count 30
python scripts/replay_test.py --count 50 --model qwen2.5:7b
```

This requires DASH 3 data to be downloaded (see Step 4).

## Step 7: Run Tests

Before making any code changes, confirm all tests pass:

```bash
# Lint + format check + unit tests (same as CI)
ruff check src/ tests/ && ruff format --check src/ tests/ && pytest tests/ -k "not integration" -v
```

Current test count: 588 passing (549 unit + 15 integration + worktree tests).

## Configuration

All settings can be overridden with environment variables using the `CHAT_TO_COP_` prefix:

| Variable | Default | Description |
|----------|---------|-------------|
| `CHAT_TO_COP_LLM_URL` | `http://127.0.0.1:11434/v1` | LLM endpoint |
| `CHAT_TO_COP_LLM_MODEL` | `qwen2.5:7b` | Primary model |
| `CHAT_TO_COP_FALLBACK_MODEL` | `qwen2.5:3b` | Smaller fallback model |
| `CHAT_TO_COP_LLM_API_KEY` | `not-needed` | API key (set for Groq/OpenAI) |
| `CHAT_TO_COP_LLM_TIMEOUT` | `120.0` | Seconds per LLM call (generous for cold starts) |
| `CHAT_TO_COP_LLM_NUM_CTX` | `8192` | Context window size for Ollama |
| `CHAT_TO_COP_DB_PATH` | `data/world_state.db` | SQLite database path |
| `CHAT_TO_COP_METRICS` | `true` | Enable instrumentation |
| `CHAT_TO_COP_COP_API_URL` | `` (empty=dry-run) | CoP REST API URL |
| `CHAT_TO_COP_COP_AUTO_THRESHOLD` | `0.7` | Confidence for auto-write to CoP |

## Docker (alternative)

If you prefer not to install Python locally:

```bash
docker compose up
```

This builds the container, starts the FastAPI server on port 8000, and uses Ollama on the host. For replay:

```bash
docker run -v /path/to/data:/app/data chat-to-cop \
    python -m chat_to_cop.replay /app/data/chat.zip
```

## Troubleshooting

### "Connection refused" when running smoke test

Ollama is not running. Start it:

```bash
# Linux
systemctl start ollama

# macOS
# Ollama starts automatically. Check Activity Monitor for "ollama".

# Windows
# Start the Ollama app from the Start menu, or run:
ollama serve
```

### "Model not found" errors

Pull the model first:

```bash
ollama pull qwen2.5:3b
```

List available models:

```bash
ollama list
```

### Ollama context window (num_ctx)

By default, Ollama models use a 2048-token context window, which is too small for our system prompt (~2000 tokens) plus conversation history. The pipeline sets `num_ctx=8192` via the OpenAI SDK's `extra_body` parameter, and this is forwarded through instructor to Ollama's `/v1/chat/completions` endpoint.

If you need to guarantee the context window (e.g., for production deployments), create a derived model with a Modelfile:

```bash
# Create a model with 8192-token context baked in
echo 'FROM qwen2.5:7b
PARAMETER num_ctx 8192' | ollama create qwen2.5-7b-8k -f -

# Use it
python -m chat_to_cop.replay path/to/chat.zip --model qwen2.5-7b-8k
```

Both approaches work. The `extra_body` passthrough is simpler (no custom model needed), while the Modelfile approach is more explicit and doesn't depend on client-side configuration.

### Slow performance (>5s per message)

- Use a smaller model: `qwen2.5:3b` instead of `7b`
- Check GPU utilization: `nvidia-smi` (Linux) or Task Manager (Windows)
- Ollama falls back to CPU if no GPU is detected. This is 5-10x slower.

### Import errors after `pip install`

Make sure you installed in dev mode:

```bash
pip install -e ".[dev]"
```

If using a virtual environment, make sure it is activated.

### "No module named chat_to_cop" when running scripts

Run from the project root directory, not from inside `src/`:

```bash
cd chat-to-cop
python scripts/quick_test.py
```

### Groq rate limit errors (429)

Groq's free tier allows ~6000 tokens/min. Use the rate delay flag:

```bash
python scripts/eval_models.py --rate-delay 18 --count 30
```

This waits 18 seconds between LLM calls. A 50-message eval takes ~10 minutes.

### Tests fail with "pytest-asyncio" errors

Install dev dependencies:

```bash
pip install -e ".[dev]"
```

### DASH data not found

Download from Pydio:

```bash
python scripts/explore_and_download_chat.py download --output data/chat
```

This requires a Pydio PAT. See [docs/DATA_SOURCES.md](DATA_SOURCES.md) for access instructions.

## Next Steps

- Read [ARCHITECTURE.md](ARCHITECTURE.md) to understand the agent pipeline
- Read [CHAT_DATA_ANALYSIS.md](CHAT_DATA_ANALYSIS.md) to see all 13 update types
- Read [LABELING_GUIDE.md](LABELING_GUIDE.md) to start labeling ground truth data
- Read [DESIGN_PHILOSOPHY.md](DESIGN_PHILOSOPHY.md) for equifinality and anti-ontology principles
- Check GitLab issues for current sprint work

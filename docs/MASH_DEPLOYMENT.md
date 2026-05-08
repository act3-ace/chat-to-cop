# MASH Deployment Guide: H2O Las Vegas, May 2026

The definitive setup guide for deploying chat-to-cop at the MASH wargame event.

---

## 1. Overview

Chat-to-cop is an AI staff officer that runs **in the background** on a single workstation. It connects to the exercise IRC server via WebSocket, reads every message across all channels, extracts world-state information using LLMs, and pushes structured updates to the Common Operating Picture (CoP) database. It is not a chatbot. Operators do not interact with it directly during the exercise.

**Setup time:** Under 10 minutes if pre-event preparation is complete.

**Hardware:** One workstation with GPU (recommended) or internet access for cloud APIs. No special infrastructure required.

**Seven deployment options:**

| Option | Requires GPU? | Requires Internet? | Latency | Notes |
|--------|--------------|-------------------|---------|-------|
| A: Local GPU + Ollama | Yes (RTX 4090/5090) | No | ~7s | Self-contained, no external dependencies |
| B: AWS Bedrock (Claude Sonnet) | No | Yes | ~5s | Best quality, requires AWS credentials |
| C: Ask Sage (NIPRNet) | No | Yes (NIPRNet) | ~10-15s | CAC + VPN required |
| D: Docker Compose | Optional | Optional | Varies | Containerized, cleanest setup |
| E: Hybrid (recommended) | Yes | Yes | ~5s primary | Cloud primary + local fallback |
| F: AG-Proxied Bedrock | No | Yes (AG network) | ~5s | No AWS creds on client, LiteLLM proxy on AG |
| G: AG vLLM | Yes (AG GPU node) | Yes (AG network) | ~3-5s | Best throughput, continuous batching |

---

## 2. Pre-Event Setup (Do Before Arriving at H2O)

Complete these steps at your desk before traveling. The event network may have surprises; eliminate all software/model issues beforehand.

**Windows users:** This guide shows bash syntax for environment variables
(`export FOO=bar`). On Windows, use the equivalent for your shell:

| Bash | PowerShell | CMD |
|------|------------|-----|
| `export FOO="bar"` | `$env:FOO = "bar"` | `set FOO=bar` |
| `command &` (background) | Open a second terminal | Open a second terminal |
| `$FOO` (use variable) | `$env:FOO` | `%FOO%` |

Alternatively, copy `.env.example` to `.env` and edit it -- Docker Compose
and pydantic-settings both read `.env` files automatically, avoiding shell
variable syntax entirely.

### 2.1 Clone and install

```bash
git clone https://gitlab.dle.afrl.af.mil/c2es1/mash/chat-to-cop.git
cd chat-to-cop
pip install -e ".[dev]"
```

Verify:

```bash
python -c "from chat_to_cop.models.cop_update import CoPUpdate; print('OK')"
```

### 2.2 Pull models (if using local GPU)

Install Ollama from https://ollama.com/download, then pull the model:

```bash
ollama pull qwen2.5:7b
```

Create a model with 8K context (required -- the default 2K truncates our prompts).
A Modelfile is included in the repo:

```bash
ollama create qwen2.5:7b-8k -f deploy/ollama/Modelfile.7b
```

For better quality with 24GB+ VRAM (RTX 4090/5090):

```bash
ollama pull qwen3:30b-a3b
ollama create qwen3:30b-a3b-8k -f deploy/ollama/Modelfile.30b
```

### 2.3 Verify API keys (if using cloud)

**Bedrock:**

```bash
pip install anthropic boto3
# Verify credentials work
python -c "import boto3; print(boto3.client('bedrock-runtime', region_name='us-gov-west-1').meta.region_name)"
```

**Ask Sage:**

```bash
pip install asksageclient pip_system_certs requests
# Set env vars or use --asksage-email and --asksage-key flags
export ASKSAGE_EMAIL="your.email@mail.mil"
export ASKSAGE_API_KEY="your-key-here"
```

### 2.4 Run smoke test

The smoke test uses hardcoded DASH 3 messages -- no data download needed:

```bash
# Local Ollama
python scripts/quick_test.py --url http://127.0.0.1:11434/v1 --model qwen2.5:7b-8k

# Bedrock
python scripts/quick_test.py --bedrock

# Ask Sage
python scripts/quick_test.py --asksage --asksage-email $ASKSAGE_EMAIL --asksage-key $ASKSAGE_API_KEY
```

Run the full pipeline against the bundled DASH 3 sample (included in the repo):

```bash
python -m chat_to_cop.replay data/dash3/23Sep_usaf_chat.zip \
    --url http://127.0.0.1:11434/v1 --model qwen2.5:7b-8k --db data/pre_event_test.db
```

If you see `Updates extracted: N` where N > 0 in the summary, the pipeline works. Run tests to confirm nothing is broken:

```bash
ruff check src/ tests/ && ruff format --check src/ tests/ && pytest tests/ -k "not integration" -q
```

### 2.5 (Optional) Download full DASH exercise data

To replay complete DASH exercises (multiple days, all teams), download from
Pydio. This requires a Pydio PAT (separate from your DLE GitLab PAT -- file
a DLE support ticket if you need one). **This is not required for setup
verification or MASH deployment.**

```bash
python scripts/explore_and_download_chat.py download --output data/chat
```

If you don't have Pydio access, ask a teammate who does to share the
`data/chat/` directory (it's ~50MB).

### 2.6 Pack list

Bring to H2O:

- [ ] Laptop/workstation with chat-to-cop installed and tested
- [ ] GPU (if using local inference) -- RTX 4090/5090 or equivalent
- [ ] Ollama installed with models already pulled
- [ ] Power adapter, ethernet cable, ethernet-to-USB adapter if needed
- [ ] DASH 3 chat data (optional -- bundled sample is enough for smoke testing)
- [ ] This document (printed or offline copy)
- [ ] AWS credentials configured (if using Bedrock)
- [ ] CAC reader + VPN software (if using Ask Sage)

---

## 3. Option A: Local GPU + Ollama

Self-contained. No internet required once models are pulled. Best for air-gapped or unreliable network scenarios.

### Requirements

- NVIDIA GPU with 8+ GB VRAM (RTX 4090/5090 recommended for 30B models)
- Ollama installed
- Model pre-pulled (see section 2.2)

### Step-by-step

1. Start Ollama (if not running as a service). On Windows, Ollama runs as a
   system tray app -- launch it from the Start menu. On Linux/Mac:

```bash
ollama serve &
```

2. Verify the model is available:

```bash
ollama list
# Should show qwen2.5:7b-8k (or your chosen model)
```

3. Connect to the IRC server and start the pipeline:

```bash
python -m chat_to_cop.replay /path/to/live/feed \
    --url http://127.0.0.1:11434/v1 \
    --model qwen2.5:7b-8k \
    --db data/mash_live.db
```

For live IRC (not replay), set env vars and run. You can either set them in
your shell (see the translation table in section 2) or edit `.env`:

```bash
export CHAT_TO_COP_LLM_URL=http://127.0.0.1:11434/v1
export CHAT_TO_COP_LLM_MODEL=qwen2.5:7b-8k
export CHAT_TO_COP_DB_PATH=data/mash_live.db
python -m chat_to_cop.replay --irc-url ws://IRC_SERVER_IP:8097
```

4. Start the dashboard (in a second terminal on Windows):

```bash
uvicorn chat_to_cop.api:app --host 0.0.0.0 --port 8000
```

5. Open `http://localhost:8000/dashboard` in a browser to verify updates are flowing.

**Important:** Use `127.0.0.1` not `localhost` for the Ollama URL. IPv6 resolution causes connection failures on some systems.

---

## 4. Option B: AWS Bedrock (Claude Sonnet)

No GPU needed. Best extraction quality (35% more threats, 69% more tasking vs local 7B). Requires internet connectivity and AWS GovCloud credentials.

### Requirements

- Internet access from H2O
- AWS GovCloud credentials (from AG IAM role or env vars)
- `pip install anthropic boto3`

### Step-by-step

1. Set AWS credentials:

```bash
export AWS_ACCESS_KEY_ID="..."
export AWS_SECRET_ACCESS_KEY="..."
export AWS_DEFAULT_REGION="us-gov-west-1"
```

Or use an AWS profile if configured.

2. Start the pipeline with the `--bedrock` flag:

```bash
python -m chat_to_cop.replay /path/to/live/feed \
    --bedrock \
    --bedrock-region us-gov-west-1 \
    --db data/mash_bedrock.db
```

The default Bedrock model is `us-gov.anthropic.claude-sonnet-4-5-20250929-v1:0`. Override with `--model`:

```bash
python -m chat_to_cop.replay /path/to/live/feed \
    --bedrock --model us-gov.anthropic.claude-haiku-4-5-20251001-v1:0 \
    --db data/mash_bedrock.db
```

3. Start the dashboard:

```bash
uvicorn chat_to_cop.api:app --host 0.0.0.0 --port 8000
```

### Verified performance (DASH 3 replay, 935 messages)

| Metric | Value |
|--------|-------|
| LLM success rate | 99.6% |
| Mean extraction latency | 4.8s |
| Max latency | 10.3s |
| Updates extracted | 256 |
| Entities tracked | 206 |

---

## 5. Option C: Ask Sage (NIPRNet)

Uses the Ask Sage API on NIPRNet. Requires CAC, VPN, and an Ask Sage account. Best for IL5 data handling requirements.

### Requirements

- NIPRNet access (VPN + CAC)
- Ask Sage account with API key
- `pip install asksageclient pip_system_certs requests`

### Step-by-step

1. Set credentials:

```bash
export ASKSAGE_EMAIL="your.email@mail.mil"
export ASKSAGE_API_KEY="your-api-key"
```

2. Start the pipeline:

```bash
python -m chat_to_cop.replay /path/to/live/feed \
    --asksage \
    --asksage-email $ASKSAGE_EMAIL \
    --asksage-key $ASKSAGE_API_KEY \
    --model claude-opus-4-6 \
    --db data/mash_asksage.db
```

3. Start the dashboard:

```bash
uvicorn chat_to_cop.api:app --host 0.0.0.0 --port 8000
```

**Note:** Ask Sage has rate limits. For high message volume, consider pairing with a local fallback (see Option E).

---

## 6. Option D: Docker Compose

Cleanest setup. Everything runs in containers. Good for reproducibility and isolating dependencies.

### Requirements

- Docker and Docker Compose installed
- NVIDIA Container Toolkit (for GPU mode)

### Step-by-step

**GPU mode (default):**

```bash
docker compose up -d
```

**CPU mode (no GPU):**

```bash
docker compose --profile cpu up -d
```

After containers are running, pull a model:

```bash
docker compose exec ollama ollama pull qwen2.5:7b
```

Create the 8K context variant:

```bash
docker compose cp deploy/ollama/Modelfile.7b ollama:/tmp/Modelfile.7b
docker compose exec ollama ollama create qwen2.5:7b-8k -f /tmp/Modelfile.7b
```

Run a replay:

```bash
docker compose exec chat-to-cop python -m chat_to_cop.replay /app/data/chat.zip
```

The FastAPI server runs automatically on port 8000. Dashboard: `http://localhost:8000/dashboard`.

**For Bedrock via Docker (no local GPU):**

```bash
docker run -e AWS_ACCESS_KEY_ID=... -e AWS_SECRET_ACCESS_KEY=... -e AWS_DEFAULT_REGION=us-gov-west-1 \
    -v ./data:/app/data -p 8000:8000 \
    chat-to-cop python -m chat_to_cop.replay /app/data/chat.zip --bedrock
```

---

## 7. Option E: Hybrid (Recommended)

Cloud primary (Bedrock or Ask Sage) with local Ollama as automatic fallback. The degrading backend handles failover transparently. If the cloud API goes down or the network drops, extraction continues on the local GPU at reduced quality. When the cloud comes back, the circuit breaker recovers automatically.

### Step-by-step

1. Start Ollama with a local model (on Windows, launch from the Start menu;
   on Linux/Mac, run `ollama serve` in a separate terminal). The model should
   already be pulled from pre-event setup.

2. Set environment variables for hybrid mode:

```bash
# Primary: Bedrock
export AWS_ACCESS_KEY_ID="..."
export AWS_SECRET_ACCESS_KEY="..."
export AWS_DEFAULT_REGION="us-gov-west-1"

# Fallback: local Ollama
export CHAT_TO_COP_FALLBACK_URL=http://127.0.0.1:11434/v1
export CHAT_TO_COP_FALLBACK_MODEL=qwen2.5:7b-8k
```

3. Start the pipeline with Bedrock as primary:

```bash
python -m chat_to_cop.replay /path/to/live/feed \
    --bedrock \
    --db data/mash_hybrid.db
```

The degrading backend stack is:

```
Level 1: Bedrock (Claude Sonnet)  -- cloud, best quality
    |  [circuit breaker: 3 failures -> open, 30s cooldown]
    v
Level 2: Local Ollama (Qwen 7B)  -- GPU fallback
    |  [circuit breaker]
    v
Level 3: Regex patterns           -- known military formats only
    |
    v
Level 4: Passthrough              -- raw message, confidence 0.0 (never fails)
```

**Why hybrid is recommended:** At DASH 3, the circuit breaker recovered from cloud outages in under 60 seconds on GPU. Zero messages were dropped across 935 messages on any backend configuration. The hybrid approach gives you cloud quality when available and guaranteed continuity when not.

---

## 7b. Option F: AG-Proxied Bedrock

Claude Sonnet via AWS Bedrock, proxied through a LiteLLM instance on Analytics Gateway. The operator's laptop needs no AWS credentials, no GPU, and no special SDK -- just an HTTP endpoint. The LiteLLM proxy on AG translates OpenAI-compatible requests to Bedrock calls using the AG node's IAM role.

### Requirements

- Network path from operator laptop to the AG node (AG VPN or same network)
- LiteLLM proxy running on an AG node (see deploy/ag/)
- No client-side AWS credentials needed

### One-liner setup

```bash
export CHAT_TO_COP_LLM_URL=https://litellm-bedrock.act3.analyticsgateway.com/v1
export CHAT_TO_COP_LLM_MODEL=claude-sonnet
```

Or using the direct AG node IP (if the friendly name is not yet configured):

```bash
export CHAT_TO_COP_LLM_URL=http://172.33.68.166:4000/v1
export CHAT_TO_COP_LLM_MODEL=claude-sonnet
```

### Available models

| Alias | Bedrock Model ID | Notes |
|-------|------------------|-------|
| `claude-sonnet` | `us-gov.anthropic.claude-sonnet-4-5-20250929-v1:0` | Primary, best quality |

Only Claude Sonnet is enabled in AG Bedrock Model Access. Other models
(Haiku, Opus) are not available on GovCloud at this time.

### Step-by-step (operator)

1. Verify the proxy is reachable:

```bash
curl https://litellm-bedrock.act3.analyticsgateway.com/v1/models
```

2. Set environment and run:

```bash
export CHAT_TO_COP_LLM_URL=https://litellm-bedrock.act3.analyticsgateway.com/v1
export CHAT_TO_COP_LLM_MODEL=claude-sonnet

python -m chat_to_cop.replay /path/to/live/feed --db data/mash_ag.db
uvicorn chat_to_cop.api:app --host 0.0.0.0 --port 8000
```

### Hybrid config with local Ollama fallback

Combine the AG proxy as primary with a local Ollama fallback for resilience. If the AG proxy or network goes down, extraction continues locally.

```bash
# Primary: AG-proxied Bedrock (Claude Sonnet)
export CHAT_TO_COP_LLM_URL=https://litellm-bedrock.act3.analyticsgateway.com/v1
export CHAT_TO_COP_LLM_MODEL=claude-sonnet

# Fallback: local Ollama
export CHAT_TO_COP_FALLBACK_URL=http://127.0.0.1:11434/v1
export CHAT_TO_COP_FALLBACK_MODEL=qwen2.5:7b-8k
```

The degrading backend handles failover transparently, same as Option E.

### Setting up the proxy (AG admin)

See `deploy/ag/` for the full setup:

```bash
# On the AG node:
cd chat-to-cop/deploy/ag
bash launch-litellm.sh

# Verify:
bash test-litellm.sh
```

The launch script is idempotent (safe to re-run). LiteLLM runs on port 4000 and logs to `/tmp/litellm.log`.

For a stable HTTPS URL, register the AG plugin using `litellm_plugin.json`. This gives the proxy a friendly name like `https://litellm-bedrock.act3.analyticsgateway.com`. Until that is configured, use the direct IP.

### Friendly name URL vs direct IP

| URL Type | Example | When to use |
|----------|---------|-------------|
| Friendly name | `https://litellm-bedrock.act3.analyticsgateway.com/v1` | AG plugin registered, HTTPS, stable |
| Direct IP | `http://172.33.68.166:4000/v1` | No plugin yet, testing, or plugin unavailable |

The friendly name provides TLS termination and a stable hostname. Prefer it for production use. The direct IP works for testing and when the plugin has not been registered with AG yet.

---

## 7c. Option G: Analytics Gateway vLLM

Run vLLM on an AG GPU node (g4dn.xlarge with T4, or p3.2xlarge with V100) serving Qwen2.5-14B-Instruct-AWQ. This gives the best throughput of any local inference option thanks to vLLM's continuous batching. The recommended AG configuration pairs vLLM as the primary backend with Bedrock (via LiteLLM proxy) as the fallback.

### Requirements

- AG GPU instance (g4dn.xlarge recommended, 92 credits/hr)
- NVIDIA drivers installed on the AG node
- Docker (recommended) or Python 3.10+ with CUDA toolkit
- For hybrid: AWS GovCloud credentials (Bedrock access)

### Step-by-step

1. Launch a g4dn.xlarge GPU instance from the AG dashboard.

2. SSH to the instance and install drivers if needed:

```bash
nvidia-smi || (sudo apt-get install -y nvidia-driver-535 && sudo modprobe nvidia)
```

3. Clone chat-to-cop and launch vLLM:

```bash
cd /tmp
git clone https://gitlab.dle.afrl.af.mil/c2es1/mash/chat-to-cop.git
cd chat-to-cop
bash deploy/ag/launch-vllm-chat2cop.sh
```

The script auto-detects the GPU, downloads the model on first run (cached to /tmp/huggingface), and waits for the health check to pass.

4. Connect chat-to-cop to vLLM:

```bash
pip install -e ".[dev]"

export CHAT_TO_COP_LLM_URL=http://127.0.0.1:8000/v1
export CHAT_TO_COP_LLM_MODEL=Qwen/Qwen2.5-14B-Instruct-AWQ
export CHAT_TO_COP_LLM_IS_OLLAMA=false

python scripts/quick_test.py --url http://127.0.0.1:8000/v1 --model Qwen/Qwen2.5-14B-Instruct-AWQ
```

5. For the recommended hybrid configuration (vLLM primary + Bedrock fallback), use the combined setup script:

```bash
bash deploy/ag/setup-ag-backends.sh --both
```

This starts both vLLM and the LiteLLM Bedrock proxy (from issue #77) and prints the env vars to configure the DegradingBackend:

```
Tier 1: vLLM (Qwen2.5-14B-AWQ on T4)     -- local GPU, ~3-5s latency
    |  [circuit breaker: 3 failures -> open]
    v
Tier 2: Bedrock (Claude Sonnet via LiteLLM) -- cloud fallback, ~5s latency
    |  [circuit breaker]
    v
Tier 3: Regex patterns
    v
Tier 4: Passthrough
```

```bash
# Full hybrid configuration
export CHAT_TO_COP_LLM_URL=http://127.0.0.1:8000/v1
export CHAT_TO_COP_LLM_MODEL=Qwen/Qwen2.5-14B-Instruct-AWQ
export CHAT_TO_COP_LLM_IS_OLLAMA=false
export CHAT_TO_COP_FALLBACK_URL=http://127.0.0.1:4000/v1
export CHAT_TO_COP_FALLBACK_MODEL=bedrock/anthropic.claude-sonnet-4-5-20250929-v1:0
```

6. Start the pipeline and dashboard:

```bash
python -m chat_to_cop.replay --irc-url ws://IRC_SERVER_IP:8097
uvicorn chat_to_cop.api:app --host 0.0.0.0 --port 8080 &
```

### Why vLLM over Ollama on AG

- Continuous batching handles 10+ concurrent channel agents without queuing
- AWQ quantization support lets 14B fit on T4 with room to spare (Ollama's AWQ support is limited)
- No Modelfile hack for context window -- `--max-model-len 8192` at startup
- Higher generation throughput: ~80-120 tok/s vs Ollama's ~40 tok/s for comparable models
- Docker image is self-contained with CUDA runtime -- no driver compatibility issues

### Credit cost estimate

| Duration | g4dn.xlarge (T4) | p3.2xlarge (V100) |
|----------|------------------|-------------------|
| 1 hour test | 92 | 510 |
| 4 hour exercise block | 368 | 2,040 |
| Full day (8 hours) | 736 | 4,080 |

Use g4dn.xlarge with 14B-AWQ. The V100 is only needed for full-precision 14B or experimental 32B models.

---

## 8. MASH-Specific Configuration

These values will be confirmed at the event. Update before starting the pipeline.

### IRC server

The IRC server address will be provided by the exercise control team. At DASH 3 it was:

```
ws://10.5.185.72:8097
```

Set via environment variable or CLI flag:

```bash
export CHAT_TO_COP_IRC_URL=ws://IRC_SERVER_IP:8097
```

### Channel list

The IRC client auto-discovers channels by sending LIST to the server every
2 minutes and joining any new channels with active users (up to 50 total).
No pre-configuration needed. The default seed channels based on DASH 3:

- `#c2_coord` -- cross-BMA coordination (highest value)
- `#fires` -- fire missions
- `#isr_reports` -- intelligence (71% informative at DASH 3)
- `#jprc` -- personnel recovery
- `#vegas_internal`, `#hydro_internal`, `#crusher_internal`, `#taipan_internal`, `#mesquite_internal`
- `#stt_hydroBMA`, `#stt_crusherBMA`, `#stt_C2Coord`, etc. (voice-to-text)

Channels that appear mid-exercise are joined automatically. To disable
discovery or adjust the cap:

```bash
set CHAT_TO_COP_IRC_DISCOVER_CHANNELS=false
set CHAT_TO_COP_IRC_MAX_CHANNELS=30
```

### Supplemental glossary (DELTRON integration)

run.bat automatically pulls confirmed entities from DELTRON on startup and
writes `data\mash_glossary.txt`. This glossary is appended to the built-in
DEFAULT_GLOSSARY so the LLM recognizes exercise-specific callsigns, threats,
and track numbers. If DELTRON is unreachable, the cached glossary from the
previous run is used.

To manually rebuild from a saved DELTRON export:

```bash
python scripts\bootstrap_mash_glossary.py --from-file path\to\messages.json
```

### Bullseye reference point

Required for converting bearing/range position reports to geographic coordinates. The exercise bullseye will be provided at the event. Configure:

```bash
export CHAT_TO_COP_BULLSEYE_LAT="..."
export CHAT_TO_COP_BULLSEYE_LON="..."
```

### CoP REST API endpoint

The CoP database endpoint will be provided by the contractor team. Until then, the pipeline runs in dry-run mode (extractions stored locally in SQLite, no HTTP writes).

```bash
# When the endpoint is available:
export CHAT_TO_COP_COP_API_URL="http://COP_SERVER:PORT/api/updates"
export CHAT_TO_COP_COP_AUTO_THRESHOLD=0.85
```

### All configuration variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CHAT_TO_COP_LLM_URL` | `http://127.0.0.1:11434/v1` | LLM endpoint |
| `CHAT_TO_COP_LLM_MODEL` | `qwen2.5:7b` | Primary model |
| `CHAT_TO_COP_FALLBACK_MODEL` | `qwen2.5:3b` | Fallback model |
| `CHAT_TO_COP_LLM_API_KEY` | `not-needed` | API key (set for cloud endpoints) |
| `CHAT_TO_COP_LLM_TIMEOUT` | `120.0` | Seconds per LLM call |
| `CHAT_TO_COP_LLM_NUM_CTX` | `8192` | Context window size |
| `CHAT_TO_COP_DB_PATH` | `data/world_state.db` | SQLite database path |
| `CHAT_TO_COP_METRICS` | `true` | Enable instrumentation |
| `CHAT_TO_COP_COP_API_URL` | (empty = dry-run) | CoP REST API URL |
| `CHAT_TO_COP_COP_AUTO_THRESHOLD` | `0.85` | Auto-write confidence threshold |

---

## 9. Pre-Event Checklist

Run through this list the morning of, before the exercise starts.

### Software

- [ ] `python -c "from chat_to_cop.models.cop_update import CoPUpdate; print('OK')"` succeeds
- [ ] `ruff check src/ tests/` passes (no lint errors in your checkout)
- [ ] `pytest tests/ -k "not integration" -q` passes

### LLM backend

- [ ] **If local GPU:** `nvidia-smi` shows GPU. `ollama list` shows your model. `curl http://127.0.0.1:11434/v1/models` returns JSON.
- [ ] **If Bedrock:** `python -c "import boto3; c=boto3.client('bedrock-runtime', region_name='us-gov-west-1'); print('OK')"` works.
- [ ] **If Ask Sage:** `ASKSAGE_EMAIL` and `ASKSAGE_API_KEY` are set. NIPRNet VPN is connected. CAC is inserted.

### Smoke test

- [ ] `python scripts/quick_test.py` completes with at least 3/5 messages producing extractions
- [ ] Extraction latency is under 15 seconds per message

### Network

- [ ] Can reach the IRC server: verify the IP and port provided by exercise control
- [ ] Can reach the CoP API endpoint (if available)
- [ ] Internet connectivity (if using cloud backend)

### Dashboard

- [ ] `uvicorn chat_to_cop.api:app --host 0.0.0.0 --port 8000` starts
- [ ] `http://localhost:8000/health` returns JSON
- [ ] `http://localhost:8000/docs` shows Swagger UI

---

## 10. During Event Monitoring

### Dashboard

Open `http://localhost:8000/dashboard` in a browser. Key metrics to watch:

| Metric | Healthy | Warning | Action |
|--------|---------|---------|--------|
| Extraction rate | >0 updates/min | 0 updates for 2+ min | Check IRC connection, LLM health |
| Mean latency | <10s | >15s | Check GPU utilization, consider smaller model |
| Circuit breaker | All closed | Any open | Check LLM endpoint, network connectivity |
| Error rate | <5% | >10% | Check logs for schema errors, timeout issues |
| Channels active | 5+ | <3 | Verify IRC server connection, channel list |

### API endpoints for monitoring

```bash
# System health
curl http://localhost:8000/health

# Recent updates
curl http://localhost:8000/updates?limit=10

# All tracked entities
curl http://localhost:8000/entities

# Extraction statistics
curl http://localhost:8000/stats

# Filter by channel
curl http://localhost:8000/updates?channel=%23c2_coord
```

### What to watch for

**Circuit breakers opening:** The degrading backend logs when it falls back. Look for lines like:

```
WARNING | Circuit breaker OPEN for OpenAICompatibleBackend — falling back to RegexBackend
```

Brief openings (<60s) are normal under load. Sustained open breakers mean the LLM endpoint is down.

**Queue depth:** If the human review queue grows large, the pipeline is producing low-confidence extractions. This is normal for noisy STT channels. High-confidence channels (#c2_coord, #fires) should be mostly auto-written.

**Fusion deduplication:** The fusion agent logs when it suppresses duplicates across channels. This is working as designed -- the same event reported on IRC and STT should be deduplicated.

### Kill switch

To pause CoP writes without stopping extraction:

```bash
# Pause all CoP writes (extractions continue to SQLite)
curl -X POST http://localhost:8000/cop/pause

# Resume CoP writes
curl -X POST http://localhost:8000/cop/resume
```

### Human review queue

High-risk update types (weapons, CSAR, fire missions, cyber/EW) always go to human review regardless of confidence. Check the queue:

```bash
curl http://localhost:8000/cop/review-queue
```

---

## 11. Post-Event Data Collection

After the exercise ends, collect all artifacts for analysis.

### Copy the SQLite database

```bash
cp data/mash_live.db data/mash_may2026_BACKUP.db
```

This contains every raw message, every extraction, every CoPUpdate, full provenance, and speaker models.

### Run analysis

```bash
# Label analysis (compare against silver labels if available)
python scripts/analyze_labels.py --db data/mash_live.db

# Export metrics summary
python -c "
from chat_to_cop.output.store import WorldStateStore
import asyncio
async def report():
    async with WorldStateStore('data/mash_live.db') as s:
        print(f'Total updates: {await s.count_updates()}')
        print(f'Total entities: {await s.count_entities()}')
asyncio.run(report())
"
```

### Export for further analysis

```bash
# All CoPUpdates as JSON
curl http://localhost:8000/updates?limit=10000 > mash_updates.json

# All entities
curl http://localhost:8000/entities > mash_entities.json
```

### Collect logs

Pipeline logs are written to stderr by loguru. Capture them to a file:

```bash
# Linux/Mac
python -m chat_to_cop.replay ... 2>&1 | tee mash_pipeline.log

# Windows (PowerShell)
python -m chat_to_cop.replay ... 2>&1 | Tee-Object mash_pipeline.log
```

---

## 12. Troubleshooting

### "Connection refused" to Ollama

Ollama is not running.

- **Windows:** Launch Ollama from the Start menu (it runs as a system tray app).
  If it's not installed, download from https://ollama.com/download.
- **Linux:** `systemctl start ollama` or run `ollama serve` in a separate terminal.
- **Mac:** Launch Ollama.app from Applications.

### "Model not found" (404)

The model was not pulled or the custom Modelfile variant was not created.

```bash
ollama list                                             # See what's available
ollama pull qwen2.5:7b                                  # Pull if missing
ollama create qwen2.5:7b-8k -f deploy/ollama/Modelfile.7b  # Recreate 8K variant
```

### localhost vs 127.0.0.1

Some systems resolve `localhost` to IPv6 (`::1`), but Ollama only listens on IPv4. Always use `http://127.0.0.1:11434/v1`.

### Noisy [GIN] log lines from Ollama

Ollama logs every HTTP request with `[GIN]` prefixes. Harmless but noisy.

- **Windows:** Run Ollama as a system tray app (default) -- logs go to a file, not your terminal.
- **Linux/Mac:** Redirect output: `ollama serve 2>/dev/null` in a separate terminal.

### num_ctx / context window truncation

If you see `truncating input prompt` warnings, the context window is too small. Recreate the model with 8K context:

```bash
ollama create qwen2.5:7b-8k -f deploy/ollama/Modelfile.7b
```

The pipeline also passes `num_ctx` via the API, but baking it into the model is more reliable.

### Rate limiting on cloud APIs

**Bedrock:** Increase timeout with `--timeout 60`. Bedrock has per-account quotas; request a limit increase if needed.

**Ask Sage:** Has built-in rate limits. The pipeline retries automatically via tenacity. If sustained 429 errors occur, reduce throughput by processing fewer channels or increasing the circuit breaker cooldown:

```bash
export CHAT_TO_COP_CIRCUIT_COOLDOWN=60
```

### Slow inference (>15s per message on GPU)

1. Check GPU is being used: `nvidia-smi` should show the Ollama process with GPU memory allocated.
2. If GPU shows 0% utilization, drivers may not be loaded. On Windows, reinstall
   NVIDIA drivers from https://www.nvidia.com/drivers. On Linux: `sudo modprobe nvidia`.
3. Try a smaller model: `qwen2.5:3b` instead of `7b`.
4. Cold start: the first message after model load takes 30-90s. Subsequent messages should be 6-10s.

### IRC WebSocket connection drops

The IRC client has automatic reconnection with exponential backoff (1s to 60s). Check logs for:

```
WARNING | WebSocket connection lost, reconnecting...
INFO | Reconnected to ws://...
```

If reconnection fails repeatedly, verify the IRC server IP and port with exercise control.

### "disk quota exceeded" (Analytics Gateway)

AG home directories have quotas. Store models and data on local disk:

```bash
export OLLAMA_MODELS=/tmp/ollama-models
mkdir -p /tmp/ollama-models
OLLAMA_MODELS=/tmp/ollama-models ollama serve &
```

### Import errors

Make sure you installed in dev mode from the project root:

```bash
cd chat-to-cop
pip install -e ".[dev]"
```

---

## AG Quickstart (Options F and G)

This section covers the automated deployment path for AG-based backends
(Options F and G). One script handles everything: provisioning the AG node,
installing DoD CA certs, cloning the repo, and launching the backends.

### Prerequisites

1. **ag-helpers** sourced in your shell. Ask Hamilton for the repo, then:

```bash
source /path/to/ag-helpers/local/profile.sh
```

2. **DLE GitLab PAT** saved to `~/.dle_gitlab_token` (mode 600):

```bash
echo 'glpat-xxxxxxxxxxxxxxxxxxxx' > ~/.dle_gitlab_token
chmod 600 ~/.dle_gitlab_token
```

3. **AG snippet ID** saved to `~/.ag_snippet_id`:

```bash
echo '42' > ~/.ag_snippet_id
```

4. **SSH config** with `ag-head` and `ag-node` hosts. See the ag-helpers
   `local/ssh-config-snippet` for the template.

### One-command deploy

From your laptop, in the chat-to-cop repo:

```bash
# GPU node with vLLM (recommended for MASH):
bash deploy/ag/deploy-to-ag.sh

# GPU node with vLLM + Bedrock fallback:
bash deploy/ag/deploy-to-ag.sh --both

# CPU node with Bedrock proxy only (no GPU needed):
bash deploy/ag/deploy-to-ag.sh --litellm

# Reuse an already-running AG node:
bash deploy/ag/deploy-to-ag.sh --reuse --both
```

The script will:
- Submit a qsub job with the right node type and disk size (90GB for GPU)
- Wait for the node to boot and publish its IP
- SSH in and install the DoD CA bundle
- Clone chat-to-cop using your DLE token
- Launch the selected backends
- Print the env vars to paste into your shell

### After deploy

Check status:
```bash
bash deploy/ag/deploy-to-ag.sh --status
```

SSH to the node directly:
```bash
ssh ag-node
```

Stop the AG job when done:
```bash
ag-stop
```

### Disk space

The vLLM Docker image is ~20GB and the model is ~9GB. The deploy script
requests 90GB by default. If you see "no space left on device", the node
was provisioned with too little disk. Re-provision with:

```bash
ag-stop
bash deploy/ag/deploy-to-ag.sh
```

### Troubleshooting

| Problem | Fix |
|---------|-----|
| `ag-start` times out | Run `ag-jobs` to check queue. The node may still be booting. Try `agip` manually once it shows as running. |
| SSH to ag-node fails | Run `agip` to refresh the IP in `~/.ssh/config`. |
| No GPU detected | Verify node type is `g4dn.xlarge`, not `cpu`. Use `--litellm` for CPU-only nodes. |
| vLLM "no space left" | Node disk too small. Re-provision (deploy script uses 90GB by default). |
| LiteLLM auth error | Expected without AWS credentials. Bedrock IAM role is bound to the AG node, not your laptop. |
| Git clone fails | Check that `~/.dle_gitlab_token` exists and the PAT has `read_api` scope (includes repository read). |

---

## Quick Reference Card

Print this and tape it to your monitor.

```
DEPLOY TO AG (one command):
  bash deploy/ag/deploy-to-ag.sh              # GPU + vLLM
  bash deploy/ag/deploy-to-ag.sh --both       # GPU + vLLM + Bedrock fallback
  bash deploy/ag/deploy-to-ag.sh --litellm    # CPU + Bedrock only
  bash deploy/ag/deploy-to-ag.sh --status     # check what's running

START PIPELINE (local GPU):
  # Start Ollama first (Windows: launch from Start menu)
  python -m chat_to_cop.replay <data> --url http://127.0.0.1:11434/v1 --model qwen2.5:7b-8k --db data/mash.db
  uvicorn chat_to_cop.api:app --host 0.0.0.0 --port 8000

START PIPELINE (Bedrock):
  python -m chat_to_cop.replay <data> --bedrock --db data/mash.db
  uvicorn chat_to_cop.api:app --host 0.0.0.0 --port 8000

MONITOR:
  http://localhost:8000/dashboard
  http://localhost:8000/health
  http://localhost:8000/stats

PAUSE CoP WRITES:
  curl -X POST http://localhost:8000/cop/pause

RESUME CoP WRITES:
  curl -X POST http://localhost:8000/cop/resume

CHECK REVIEW QUEUE:
  curl http://localhost:8000/cop/review-queue
```

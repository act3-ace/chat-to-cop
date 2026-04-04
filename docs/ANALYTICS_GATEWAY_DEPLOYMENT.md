# Analytics Gateway (AG) GPU Deployment Guide

How to run chat-to-cop on Analytics Gateway with GPU acceleration.

## Instance Selection

| Instance | GPU | VRAM | Credits/hr | Models That Fit |
|----------|-----|------|-----------|-----------------|
| **g4dn.xlarge** | 1x T4 | 16GB | 92 | 7B (4.5GB), 14B (9GB) |
| g4dn.2xlarge | 1x T4 | 16GB | 131 | Same GPU, more CPU/RAM |

**Note:** As of March 2026, g5 (A10G) and p3 instances may not be available in GovCloud. The g4dn.xlarge with T4 is the reliable option.

## Step-by-Step Setup

### 1. Launch Instance

Request a `g4dn.xlarge` GPU instance from the AG dashboard. Wait for it to spin up.

### 2. Install NVIDIA Drivers

The AG GPU instances may not have NVIDIA drivers pre-installed:

```bash
# Check if drivers are already installed
nvidia-smi

# If not found, install the latest available driver:
sudo apt-get update
sudo apt-get install -y nvidia-driver-$(apt-cache search nvidia-driver | grep -oP 'nvidia-driver-\K\d+' | sort -n | tail -1)
sudo modprobe nvidia

# Verify — should show Tesla T4 with 16GB
nvidia-smi
```

**This step takes 5-10 minutes** (downloads ~2GB of packages, builds DKMS kernel module).

### 3. Install Ollama

```bash
curl -fsSL https://ollama.ai/install.sh | sh
```

### 4. Start Ollama Server

**CRITICAL: AG does not use systemd, so Ollama will NOT auto-start.** You must manually start the server in the background:

```bash
export OLLAMA_MODELS=/tmp/ollama-models
mkdir -p /tmp/ollama-models
OLLAMA_MODELS=/tmp/ollama-models ollama serve &>/dev/null &
sleep 10

# Verify it's running:
curl -s http://127.0.0.1:11434/api/tags | head -1
```

If you skip this step, everything else will fail with `Connection error` or `could not connect to ollama server`.

**Disk quota:** AG home directories have a quota. `/tmp` is local SSD (~34GB). Models stored here are lost when the instance shuts down — re-pull each session.

To suppress noisy `[GIN]` HTTP access logs: `OLLAMA_MODELS=/tmp/ollama-models ollama serve 2>/dev/null &`

### 5. Pull a Model and Set Context Window

```bash
# 7B — fits easily, good quality
OLLAMA_MODELS=/tmp/ollama-models ollama pull qwen2.5:7b

# Create a custom model with 8K context (required — default 4K truncates our prompts)
cat > /tmp/Modelfile <<EOF
FROM qwen2.5:7b
PARAMETER num_ctx 8192
EOF
OLLAMA_MODELS=/tmp/ollama-models ollama create qwen2.5:7b-8k -f /tmp/Modelfile
```

**Important:** Always use `qwen2.5:7b-8k` (not `qwen2.5:7b`) when running the pipeline. The default 4096 context truncates our system prompt + conversation window, causing extraction failures. The Modelfile bakes 8192 context into the model.

For 14B:

```bash
OLLAMA_MODELS=/tmp/ollama-models ollama pull qwen2.5:14b
cat > /tmp/Modelfile14b <<EOF
FROM qwen2.5:14b
PARAMETER num_ctx 8192
EOF
OLLAMA_MODELS=/tmp/ollama-models ollama create qwen2.5:14b-8k -f /tmp/Modelfile14b
```

**Provenance note:** The `-8k` suffix is a deployment configuration, not a different model. The weights are identical to the base model; only the context window parameter changes. Provenance records will report the Ollama model name including the suffix (e.g., `qwen2.5:7b-8k`). See [SYSTEM_CARD.md](SYSTEM_CARD.md) for details.

### 6. Clone and Install chat-to-cop

```bash
cd /tmp  # Use local disk to avoid quota issues
git clone https://gitlab.dle.afrl.af.mil/c2es1/mash/chat-to-cop.git
cd chat-to-cop
pip install --user -e ".[dev]"
```

If prompted for credentials, use your DLE username and personal access token.

### 7. Run Smoke Test

```bash
python scripts/quick_test.py --url http://127.0.0.1:11434/v1 --model qwen2.5:7b-8k
```

First message includes model loading (~90s cold start). Subsequent messages: **6-15 seconds**.

**Important:** Use `127.0.0.1`, not `localhost` (IPv6 resolution issue).

### 8. Run Eval Harness

```bash
python scripts/eval_models.py --url http://127.0.0.1:11434/v1 --model qwen2.5:7b-8k --count 100 -v
```

### 9. Run Full Pipeline Replay

If you have DASH chat data available:

```bash
python -m chat_to_cop.replay /path/to/chat.zip \
    --url http://127.0.0.1:11434/v1 \
    --model qwen2.5:7b-8k \
    --db /tmp/gpu_test.db
```

To view results, start the API:

```bash
uvicorn chat_to_cop.api:app --host 0.0.0.0 --port 8000 &
# Open http://<instance-ip>:8000/dashboard in your browser
```

## Verified Performance Numbers

Tested March 30, 2026 on g4dn.xlarge (T4 16GB):

| Model | p50 Latency | F1 (type) | F1 (entity) | Field Acc | Errors |
|-------|-------------|-----------|-------------|-----------|--------|
| qwen2.5:7b | **6.5s** | 0.92 | 0.91 | 73% | 1% |

Per-type recall (7B on T4):
- entity_id: 100%, fuel: 100%, tasking: 100%, weapons: 100%
- threat: 92%, status_change: 71%
- csar: 0% (needs prompt improvement)

## Troubleshooting

### "nvidia-smi not found" / "NVIDIA driver not loaded"
```bash
sudo apt-get install -y nvidia-driver-535
sudo modprobe nvidia
```

### "model not found" (404 from Ollama API)
The model directory wasn't set correctly. Make sure `OLLAMA_MODELS=/tmp/ollama-models` is set when running both `ollama serve` and `ollama pull`.

### "disk quota exceeded"
Use `/tmp` instead of home directory for models and repo.

### Slow inference (>30s per message)
Check that GPU is being used: look for `device=CUDA0` in Ollama logs. If it says `device=CPU`, the NVIDIA drivers aren't loaded.

### Noisy `[GIN]` log lines in output
These are Ollama's internal HTTP access logs (e.g., `[GIN] 200 | 7.75s | POST "/v1/chat/completions"`). They're harmless but noisy. Suppress by redirecting Ollama's stderr:

```bash
OLLAMA_MODELS=/tmp/ollama-models ollama serve 2>/dev/null &
```

Or if you want to keep Ollama errors but suppress GIN specifically:

```bash
OLLAMA_MODELS=/tmp/ollama-models ollama serve 2>&1 | grep -v '^\[GIN\]' &
```

### "truncating input prompt"
The conversation window exceeds the model's context length. This is normal for later messages in a long sequence. Consider reducing `window_size` in config.

## Cost Estimation

| Test | Instance | Duration | Credits |
|------|----------|----------|---------|
| Smoke test (5 messages) | g4dn.xlarge | 5 min | ~8 |
| Eval harness (100 messages) | g4dn.xlarge | 20 min | ~31 |
| Full DASH replay (149 messages) | g4dn.xlarge | 30 min | ~46 |
| **Total for a complete test session** | | **~1 hour** | **~92** |

Including driver install and model pull: budget ~2 hours (184 credits) for a complete test session.

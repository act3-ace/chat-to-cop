# Analytics Gateway (AG) GPU Deployment Guide

How to run chat-to-cop on Analytics Gateway with GPU acceleration.

## Instance Selection

| Instance | GPU | VRAM | Credits/hr | Models That Fit |
|----------|-----|------|-----------|-----------------|
| **g4dn.xlarge** | 1x T4 | 16GB | 92 | 7B (4.5GB), 14B-AWQ (9GB) |
| g4dn.2xlarge | 1x T4 | 16GB | 131 | Same GPU, more CPU/RAM |
| p3.2xlarge | 1x V100 | 16GB | 510 | 14B full (28GB -- tight), 7B |

**Note:** As of March 2026, g5 (A10G) instances may not be available in GovCloud. The g4dn.xlarge with T4 is the reliable and cost-effective option. p3 (V100) is available but expensive.

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

**Provenance note:** The `-8k` suffix is a deployment configuration, not a different model. The weights are identical to the base model; only the context window parameter changes. See [SYSTEM_CARD.md](SYSTEM_CARD.md) for details.

### 6. Warm Start the Model

Pre-load the model into GPU memory before running the pipeline. This avoids a cold start penalty (~30-90s) on the first real extraction:

```bash
OLLAMA_MODELS=/tmp/ollama-models ollama run qwen2.5:7b-8k "hello" --verbose
```

Verify the output shows `device=CUDA0` (GPU) not `device=CPU`. Expected: ~40 tokens/s eval rate on T4. If you see <5 tokens/s, the GPU drivers aren't loaded — go back to Step 2.

### 7. Clone and Install chat-to-cop

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

## vLLM Setup (Recommended for MASH)

vLLM provides significantly better throughput and lower latency than Ollama for the same model, especially with quantized models (AWQ/GPTQ) and continuous batching. This is the recommended backend for AG GPU deployments.

Reference: Jennifer Carlet's production vLLM scripts at
https://gitlab.dle.afrl.af.mil/analytics-gateway/llms-on-ag

### Docker Path (Recommended)

Docker is simpler and avoids dependency conflicts with AG's system Python.

```bash
# One-command launch with chat-to-cop defaults
cd deploy/ag
./launch-vllm-chat2cop.sh
```

Or run Docker directly (T4 example with recommended flags):

```bash
mkdir -p /tmp/huggingface

docker run -d \
    --name vllm-chat2cop \
    --gpus all \
    --shm-size 4g \
    -p 8000:8000 \
    -v /tmp/huggingface:/root/.cache/huggingface \
    -e VLLM_WORKER_MULTIPROC_METHOD=spawn \
    -e VLLM_NO_USAGE_STATS=1 \
    vllm/vllm-openai:v0.19.1-cu130 \
    --model Qwen/Qwen2.5-14B-Instruct-AWQ \
    --max-model-len 8192 \
    --gpu-memory-utilization 0.90 \
    --trust-remote-code \
    --dtype float16 \
    --max-num-seqs 16
```

For non-T4 GPUs (V100, A10G, etc.), replace `--dtype float16 --max-num-seqs 16` with `--dtype auto`.

Verify:

```bash
curl http://127.0.0.1:8000/v1/models
```

### Native Python Path

Use when Docker is unavailable or you need tighter control. Requires CUDA toolkit and build dependencies.

```bash
# Install build deps (from Jennifer Carlet's repo)
# See: https://gitlab.dle.afrl.af.mil/analytics-gateway/llms-on-ag
#      vllm_scripts/install_build_deps.sh
sudo apt-get install -y python3-dev build-essential
pip install vllm

# Set cache to /tmp (avoid disk quota)
export HF_HOME=/tmp/huggingface
mkdir -p /tmp/huggingface

# Launch (T4 example -- use --dtype auto on V100/A10G)
python3 -m vllm.entrypoints.openai.api_server \
    --model Qwen/Qwen2.5-14B-Instruct-AWQ \
    --max-model-len 8192 \
    --host 0.0.0.0 \
    --port 8000 \
    --gpu-memory-utilization 0.90 \
    --trust-remote-code \
    --dtype float16 \
    --max-num-seqs 16
```

### T4-Specific Notes

The T4 has compute capability 7.5 (Turing architecture). vLLM needs two flags for reliable T4 operation:

- `--dtype float16` -- T4 does not support bfloat16; vLLM's auto-detect may pick the wrong dtype
- `--max-num-seqs 16` -- reduces concurrent sequence memory pressure on 16 GB VRAM

The `launch-vllm-chat2cop.sh` script auto-detects T4 and applies these flags. If running Docker manually, add them explicitly.

There is also a known Triton shared memory exhaustion issue on CC <8.0 GPUs. Jennifer Carlet's repo includes a patch (`vllm_scripts/patch-vllm-triton.sh`) that may be needed for some models. See https://github.com/vllm-project/vllm/issues/38918.

### Model Options

| Model | Size | VRAM | Instance | Notes |
|-------|------|------|----------|-------|
| **Qwen/Qwen2.5-14B-Instruct-AWQ** | ~9 GB | T4 (16GB) | g4dn.xlarge | Recommended -- best quality/cost on T4 |
| Qwen/Qwen2.5-7B-Instruct | ~14 GB | T4 (16GB) | g4dn.xlarge | Full precision, fits T4 but tight |
| Qwen/Qwen2.5-14B-Instruct | ~28 GB | V100 (16GB) | p3.2xlarge | Full precision -- needs V100 or better |
| Qwen/Qwen2.5-32B-Instruct-AWQ | ~18 GB | V100 (16GB) | p3.2xlarge | Experimental -- tight on V100, untested |

Jennifer Carlet's vLLM benchmarks on T4 (from `analytics-gateway/llms-on-ag`):

| Model (vLLM, g4dn T4) | GPU Mem | Tokens/s |
|------------------------|---------|----------|
| Qwen3.5-2B | 14211 MiB | 50/s |
| Qwen3.5-4B (optimized) | 12545 MiB | 25/s |
| RedHatAI/Qwen3.5-4B-quantized.w8a8 | 12557 MiB | 30/s |
| Qwen3.5-9B | OOM | -- |
| google/gemma-4-E2B-it | 14199 MiB | 40/s |

These benchmarks confirm that 9B+ full-precision models do not fit on T4 via vLLM. Quantized 14B (AWQ) does fit because it compresses to ~9 GB.

AWQ quantization loses negligible quality on structured extraction tasks while cutting VRAM usage by ~60%. For chat-to-cop, 14B-AWQ on T4 is the sweet spot.

### vLLM vs Ollama Performance

| Metric | Ollama (7B on T4) | vLLM (14B-AWQ on T4) |
|--------|--------------------|-----------------------|
| Tokens/sec (generation) | ~40 | ~80-120 |
| First-token latency | ~200ms | ~100ms |
| Concurrent requests | 1 (sequential) | Batched (continuous) |
| Context window | Requires Modelfile hack | Native --max-model-len |
| Cold start | 30-90s | 60-120s (model download on first run) |

vLLM's continuous batching means multiple channel agents can send concurrent requests without queuing. With 10+ IRC channels active during MASH, this matters.

### Connecting chat-to-cop to vLLM

```bash
export CHAT_TO_COP_LLM_URL=http://127.0.0.1:8000/v1
export CHAT_TO_COP_LLM_MODEL=Qwen/Qwen2.5-14B-Instruct-AWQ
export CHAT_TO_COP_LLM_IS_OLLAMA=false

python -m chat_to_cop.replay /path/to/chat.zip --db /tmp/vllm_test.db
```

**Important:** Set `CHAT_TO_COP_LLM_IS_OLLAMA=false` (or use `--no-ollama`) so the pipeline does not send Ollama-specific `num_ctx` options in the request body. vLLM handles context length via `--max-model-len` at server startup.

### AG Plugin Definition

The file `deploy/ag/vllm_chat2cop_plugin.json` defines chat-to-cop's vLLM setup as an AG custom plugin. This follows the format from Jennifer Carlet's `vllm_docker.json` and can be registered with the AG dashboard for managed deployments.

## Cost Estimation

| Test | Instance | Duration | Credits |
|------|----------|----------|---------|
| Smoke test (5 messages) | g4dn.xlarge | 5 min | ~8 |
| Eval harness (100 messages) | g4dn.xlarge | 20 min | ~31 |
| Full DASH replay (149 messages) | g4dn.xlarge | 30 min | ~46 |
| **Total for a complete test session** | | **~1 hour** | **~92** |
| Full MASH event (8 hours) | g4dn.xlarge | 8 hr | ~736 |
| Full MASH event (8 hours) | p3.2xlarge | 8 hr | ~4080 |

Including driver install and model pull: budget ~2 hours (184 credits) for a complete test session on g4dn.

For a multi-day event, prefer g4dn.xlarge with 14B-AWQ over p3.2xlarge with 14B full. The quality difference is negligible but the credit cost is 5.5x lower.

# LLM Backend Guide

How to connect chat-to-cop to different LLM backends. The pipeline is
model-agnostic: every LLM call goes through the same OpenAI-compatible
interface. The only difference between backends is configuration.

## Which Backend Should I Use?

| Backend | Best for | Setup time | Cost | FedRAMP | GPU needed |
|---------|----------|-----------|------|---------|------------|
| Ollama (local) | Development, MASH event | 5 min | Free | N/A | Recommended |
| vLLM (local/remote) | Multi-user, production throughput | 15 min | Free | N/A | Required |
| LM Studio | Individual analyst, GUI preference | 5 min | Free | N/A | Recommended |
| llama.cpp / llamafile | Minimal install, CPU-only OK | 5 min | Free | N/A | Optional |
| Azure OpenAI (Gov) | DoD contractors, CMMC 2.0 | 30 min | Per-token | High | No |
| AWS Bedrock (GovCloud) | DoD organizations with AWS | 30 min | Per-token | High | No |
| Anthropic API | Commercial, best quality | 5 min | Per-token | Via Bedrock | No |
| Ask Sage | NIPRNet users, IL4/IL5 | 15 min | Per-token | IL4/IL5 | No |

**Recommended starting point:** Ollama with qwen2.5:7b. It's free, local,
and the default configuration works out of the box.


## Local Backends (No Cloud Required)

Local backends keep all data on your machine. No FedRAMP or CMMC concerns
because nothing leaves your network.

### Ollama (Default)

The easiest path. Ollama manages model downloads and serves them locally.

**Install:**
- Windows: `winget install Ollama.Ollama` or https://ollama.com/download
- Mac: `brew install ollama` or https://ollama.com/download
- Linux: `curl -fsSL https://ollama.com/install.sh | sh`

**Run:**
```bash
ollama pull qwen2.5:7b
python -m chat_to_cop.replay data/dash3/23Sep_usaf_chat.zip
```

No additional configuration needed. The pipeline defaults to
`http://127.0.0.1:11434/v1` and `qwen2.5:7b`.

**Model selection by GPU VRAM:**

| VRAM | Model | Flag |
|------|-------|------|
| 10+ GB | qwen2.5:14b | `--model qwen2.5:14b` |
| 6+ GB | qwen2.5:7b | (default) |
| 2+ GB or CPU | qwen2.5:3b | `--model qwen2.5:3b` |

### vLLM

Higher throughput than Ollama for multi-user scenarios. Supports tensor
parallelism across multiple GPUs.

**Install:**
```bash
pip install vllm
```

**Run:**
```bash
vllm serve Qwen/Qwen2.5-7B-Instruct --dtype auto --port 8000
```

**Connect:**
```bash
python -m chat_to_cop.replay data/dash3/23Sep_usaf_chat.zip \
  --url http://localhost:8000/v1 \
  --model Qwen/Qwen2.5-7B-Instruct
```

For Docker deployment (recommended for production):
```bash
docker run --gpus all -p 8000:8000 \
  vllm/vllm-openai:latest \
  --model Qwen/Qwen2.5-7B-Instruct --dtype auto
```

### LM Studio

GUI application with a built-in server mode. Good for analysts who prefer
a visual interface for model management.

1. Download from https://lmstudio.ai
2. Search for and download a Qwen 2.5 7B model
3. Start the local server (port 1234 by default)
4. Connect:
```bash
python -m chat_to_cop.replay data/dash3/23Sep_usaf_chat.zip \
  --url http://localhost:1234/v1 \
  --model <model-name-from-lm-studio>
```

### llama.cpp / llamafile

Minimal dependencies. llamafile is a single binary that runs on any OS
without installation.

**llamafile (easiest):**
1. Download a GGUF model file from https://huggingface.co
2. Download llamafile from https://github.com/Mozilla-Ocho/llamafile
3. Run: `llamafile --server --model qwen2.5-7b.gguf --port 8080`

**llama.cpp server:**
```bash
llama-server -m qwen2.5-7b-instruct-q4_k_m.gguf --port 8080
```

**Connect (same for both):**
```bash
python -m chat_to_cop.replay data/dash3/23Sep_usaf_chat.zip \
  --url http://localhost:8080/v1 \
  --model qwen2.5
```


## Cloud Backends (FedRAMP / CMMC 2.0)

For organizations that need FedRAMP-authorized cloud services. All cloud
backends require API keys or IAM credentials.

### Azure OpenAI (Azure Government)

FedRAMP High authorized. The most common choice for DoD contractors under
CMMC 2.0.

**Prerequisites:**
- Azure Government subscription with an OpenAI resource deployed
- A model deployment (e.g., gpt-4o) in your Azure OpenAI resource
- API key from the Azure portal (Keys and Endpoint section)

**Run via CLI flags:**
```bash
python -m chat_to_cop.replay data/dash3/23Sep_usaf_chat.zip \
  --azure \
  --azure-endpoint https://YOUR-RESOURCE.openai.azure.com \
  --model YOUR-DEPLOYMENT-NAME
```

**Or via environment variables:**
```bash
export CHAT_TO_COP_LLM_AZURE_ENDPOINT=https://YOUR-RESOURCE.openai.azure.com
export CHAT_TO_COP_LLM_API_KEY=your-azure-api-key
export CHAT_TO_COP_LLM_MODEL=your-deployment-name
python -m chat_to_cop.replay data/dash3/23Sep_usaf_chat.zip --azure
```

**Notes:**
- The `--model` value is your Azure deployment name, not the underlying
  model name (e.g., `my-gpt4o-deployment`, not `gpt-4o`).
- Default API version is `2024-10-21`. Override with `--azure-api-version`
  or `CHAT_TO_COP_LLM_AZURE_API_VERSION`.
- No additional Python packages required beyond the standard `openai` SDK.

### AWS Bedrock (GovCloud)

FedRAMP High authorized. Uses Anthropic Claude models via AWS.

**Prerequisites:**
- AWS GovCloud account with Bedrock model access enabled
- AWS credentials configured (IAM role, `~/.aws/credentials`, or env vars)

**Run:**
```bash
pip install "chat-to-cop[bedrock]"

python -m chat_to_cop.replay data/dash3/23Sep_usaf_chat.zip \
  --bedrock \
  --bedrock-region us-gov-west-1
```

Default model: `us-gov.anthropic.claude-sonnet-4-5-20250929-v1:0`.
Override with `--model`.

**Environment variables for credentials:**
```bash
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_DEFAULT_REGION=us-gov-west-1
```

On EC2 instances with an IAM role attached, credentials are provided
automatically via the instance metadata service.

### Anthropic API (Direct)

Commercial Anthropic API. Not FedRAMP-authorized on its own, but
available via Bedrock (see above) for FedRAMP use.

**Prerequisites:**
- Anthropic API key from https://console.anthropic.com

**Run:**
```bash
export ANTHROPIC_API_KEY=sk-ant-...
python -m chat_to_cop.replay data/dash3/23Sep_usaf_chat.zip \
  --anthropic \
  --model claude-haiku-4-5-20251001
```

### Ask Sage (NIPRNet / IL4-IL5)

DoD-specific AI gateway on NIPRNet. Wraps multiple LLM providers behind
a single API.

**Prerequisites:**
- Ask Sage account (request at https://api.genai.army.mil)
- API key and account email from the Ask Sage portal

**Run:**
```bash
python -m chat_to_cop.replay data/dash3/23Sep_usaf_chat.zip \
  --asksage \
  --asksage-email your.email@mil \
  --asksage-key your-api-key

# Or via environment variables:
export ASKSAGE_EMAIL=your.email@mil
export ASKSAGE_API_KEY=your-api-key
python -m chat_to_cop.replay data/dash3/23Sep_usaf_chat.zip --asksage
```

### Any OpenAI-Compatible Endpoint

If your service exposes `/v1/chat/completions`, it works:

```bash
python -m chat_to_cop.replay data/dash3/23Sep_usaf_chat.zip \
  --url https://your-service.example.com/v1 \
  --model model-name
```

Set `CHAT_TO_COP_LLM_API_KEY` if authentication is required.

On Windows with run.bat:
```
run.bat --cloud https://your-service.example.com/v1
```


## Environment Variable Reference

All variables use the `CHAT_TO_COP_` prefix.

| Variable | Default | Description |
|----------|---------|-------------|
| `CHAT_TO_COP_LLM_URL` | `http://127.0.0.1:11434/v1` | OpenAI-compatible endpoint URL |
| `CHAT_TO_COP_LLM_MODEL` | `qwen2.5:7b` | Model name or deployment name |
| `CHAT_TO_COP_LLM_API_KEY` | `not-needed` | API key (not needed for Ollama) |
| `CHAT_TO_COP_LLM_TIMEOUT` | `120` | Per-request timeout (seconds) |
| `CHAT_TO_COP_LLM_AZURE_ENDPOINT` | (empty) | Azure OpenAI endpoint URL |
| `CHAT_TO_COP_LLM_AZURE_API_VERSION` | `2024-10-21` | Azure API version |
| `CHAT_TO_COP_LLM_IS_OLLAMA` | (auto-detect) | Force Ollama mode (true/false) |
| `CHAT_TO_COP_LLM_NUM_CTX` | `8192` | Context window for Ollama models |
| `CHAT_TO_COP_FALLBACK_URL` | `http://127.0.0.1:11434/v1` | Fallback endpoint |
| `CHAT_TO_COP_FALLBACK_MODEL` | `qwen2.5:3b` | Fallback model |
| `ANTHROPIC_API_KEY` | (none) | For `--anthropic` mode |
| `ASKSAGE_EMAIL` | (none) | For `--asksage` mode |
| `ASKSAGE_API_KEY` | (none) | For `--asksage` mode |
| `AWS_DEFAULT_REGION` | `us-gov-west-1` | For `--bedrock` mode |


## Flash Drive (Air-Gapped Deployment)

For environments with no internet access, the flash drive package
bundles everything needed: Docker images with pre-loaded model weights,
start scripts, and a demo mode. See the README.txt in the flash drive
package for instructions. The recipient needs only Docker Desktop.

Build a flash drive package:
```bash
bash scripts/package-flash-drive.sh --model qwen2.5:7b --output /media/usb/chat-to-cop
```

The package is ~5-8 GB depending on the model.

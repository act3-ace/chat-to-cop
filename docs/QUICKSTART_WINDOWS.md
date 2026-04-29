# Quickstart: Windows 11 from Scratch

Everything you need to go from a fresh Windows 11 machine to running
chat-to-cop. Two paths: Docker (recommended, fewer moving parts) or
native Python.

---

## 1. Prerequisites (both paths)

### 1.1 Git for Windows

Download and install from https://git-scm.com/download/win. Use the
defaults. This gives you Git Bash and an SSH client.

After install, open Git Bash and configure:

```bash
git config --global user.name "Your Name"
git config --global user.email "your.email@us.af.mil"
```

### 1.2 DLE GitLab SSH key

Generate an RSA key (DLE rejects ed25519):

```bash
ssh-keygen -t rsa -b 4096 -C "your.email@us.af.mil" -f ~/.ssh/dle_rsa
```

Add to your SSH config (`~/.ssh/config` -- create if it doesn't exist):

```
Host gitlab.dle.afrl.af.mil
    HostName gitlab.dle.afrl.af.mil
    User git
    IdentityFile ~/.ssh/dle_rsa
    IdentitiesOnly yes
```

Copy the public key:

```bash
cat ~/.ssh/dle_rsa.pub
```

Paste it at: https://gitlab.dle.afrl.af.mil/-/user_settings/ssh_keys

Test:

```bash
ssh -T git@gitlab.dle.afrl.af.mil
```

You should see "Welcome to GitLab, @yourname!"

### 1.3 DoD CA bundle (if HTTPS fails)

If you get SSL certificate errors connecting to DLE, you need the DoD CA
bundle. Download the bundle from https://dl.dod.cyber.mil/wp-content/uploads/pki-pke/zip/unclass-certificates_pkcs7_DoD.zip

Extract and install the CA certificates. For Git specifically:

```bash
git config --global http.sslCAInfo /path/to/dod-ca-bundle.pem
```

If you're behind a VPN that interferes with `.mil` sites, temporarily
disconnect or split-tunnel before cloning.

### 1.4 Clone the repo

```bash
git clone git@gitlab.dle.afrl.af.mil:c2es1/mash/chat-to-cop.git
cd chat-to-cop
```

---

## 2. Path A: Docker (recommended)

This is the turn-key path. One command starts everything: the LLM
server, the extraction pipeline, and the REST API. No Python install
needed on the host.

### Minimum-touch option: pull from DLE registry

If you just want to run the pipeline without cloning the repo, you can
pull a pre-built image directly from DLE GitLab's container registry.
You still need Docker Desktop (section 2.1 below) and a DLE GitLab
Personal Access Token (PAT) with `read_registry` scope.

```bash
# Log in to DLE container registry (one-time)
docker login gitlab.dle.afrl.af.mil:5050
# Username: your DLE username
# Password: your DLE PAT (not your password)

# Pull the latest image
docker pull gitlab.dle.afrl.af.mil:5050/c2es1/mash/chat-to-cop:latest
```

Then save the minimal docker-compose.yml below to a local directory and
run `docker compose up`. Skip to section 2.3 for model setup.

```yaml
# docker-compose.yml -- minimal, pulls from DLE registry
services:
  ollama:
    image: ollama/ollama:latest
    ports: ["11434:11434"]
    volumes: [ollama_models:/root/.ollama]
    deploy:
      resources:
        reservations:
          devices: [{driver: nvidia, count: 1, capabilities: [gpu]}]

  chat-to-cop:
    image: gitlab.dle.afrl.af.mil:5050/c2es1/mash/chat-to-cop:latest
    environment:
      - CHAT_TO_COP_LLM_URL=http://ollama:11434/v1
      - CHAT_TO_COP_LLM_MODEL=qwen2.5:7b
      - CHAT_TO_COP_DB_PATH=/app/data/world_state.db
    ports: ["8000:8000"]
    volumes: ["./data:/app/data"]

volumes:
  ollama_models:
```

If you don't have a GPU, remove the `deploy.resources` block from the
ollama service (it will run CPU-only, slower but functional).

### 2.1 Install Docker Desktop

Download from https://www.docker.com/products/docker-desktop/

During install:
- Enable WSL 2 backend (recommended)
- If you have an NVIDIA GPU, also install the NVIDIA Container Toolkit:
  https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html

After install, open Docker Desktop and verify it starts. You may need to
restart Windows.

Verify from Git Bash:

```bash
docker --version
docker compose version
```

### 2.2 Start the stack

**With GPU (RTX 3060+ recommended):**

```bash
cd chat-to-cop
docker compose up -d
```

**Without GPU (CPU-only inference, slower):**

```bash
docker compose --profile cpu up -d
```

This pulls container images and starts Ollama + the pipeline + the REST
API. First run downloads ~2GB of images.

### 2.3 Pull a model

```bash
docker compose exec ollama ollama pull qwen2.5:7b
```

Create the 8K context variant (required):

```bash
docker compose exec ollama sh -c 'echo "FROM qwen2.5:7b
PARAMETER num_ctx 8192" | ollama create qwen2.5:7b-8k -f -'
```

### 2.4 Test with replay

Copy a DASH 3 chat.zip into `data/`:

```bash
cp /path/to/chat.zip data/
docker compose exec chat-to-cop python -m chat_to_cop.replay /app/data/chat.zip
```

You should see messages being processed, with extraction results like:

```
[#c2_coord] fuel_state (conf=0.85, method=llm): Hydro_Tank: RR15 F+40
```

### 2.5 Test with live IRC

At MASH, point to the exercise IRC server:

```bash
docker compose exec chat-to-cop python -m chat_to_cop.replay \
    --irc-url ws://IRC_SERVER_IP:8097 \
    --irc-channels "#c2_coord,#fires,#isr_reports,#jprc"
```

Replace `IRC_SERVER_IP` with the actual exercise server address (was
10.5.185.72 at DASH 3, will be provided at MASH).

### 2.6 Using Bedrock instead of local GPU

If you have AWS GovCloud credentials and prefer cloud inference:

```bash
docker compose up chat-to-cop -d  # skip Ollama
docker compose exec -e AWS_ACCESS_KEY_ID=AKIA... \
    -e AWS_SECRET_ACCESS_KEY=... \
    -e AWS_DEFAULT_REGION=us-gov-west-1 \
    chat-to-cop python -m chat_to_cop.replay /app/data/chat.zip --bedrock
```

### 2.7 Stopping

```bash
docker compose down
```

---

## 3. Path B: Native Python

Use this if you need to modify code or if Docker isn't available.

### 3.1 Install Python

Download Python 3.10+ from https://www.python.org/downloads/

During install, check "Add python.exe to PATH". Verify:

```bash
python --version
pip --version
```

### 3.2 Install chat-to-cop

```bash
cd chat-to-cop
pip install -e ".[dev]"
```

Verify:

```bash
python -c "from chat_to_cop.models.cop_update import CoPUpdate; print('OK')"
```

### 3.3 Install Ollama (for local inference)

Download from https://ollama.com/download/windows

After install, pull a model:

```bash
ollama pull qwen2.5:7b
```

Verify Ollama is serving:

```bash
python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:11434/api/tags').read().decode()[:100])"
```

### 3.4 Run a replay

```bash
python -m chat_to_cop.replay data/chat/Dash3-GBC/Data/23Sep/usaf/chat.zip --num-ctx 8192
```

### 3.5 Run against live IRC

```bash
python -m chat_to_cop.replay --irc-url ws://IRC_SERVER_IP:8097
```

---

## 4. Verifying the setup

After running a replay (Docker or native), check:

1. **Extraction output** in the terminal: you should see update lines
   with confidence scores and extraction methods
2. **SQLite database**: `data/world_state.db` is created with extracted
   entities
3. **REST API** (if running): http://localhost:8000/docs shows the API
   docs, http://localhost:8000/dashboard shows the extraction dashboard

### Quick smoke test (no LLM required)

The pipeline degrades to regex extraction when no LLM is available.
This tests everything except LLM quality:

```bash
# Native
python -m chat_to_cop.replay data/chat/Dash3-GBC/Data/23Sep/usaf/chat.zip --timeout 1

# Docker
docker compose exec chat-to-cop python -m chat_to_cop.replay /app/data/chat.zip --timeout 1
```

You'll see "Connection error" warnings for the LLM (expected), then
regex-based extractions at lower confidence. This proves the pipeline,
degradation chain, and database write path all work.

---

## 5. What you need from the team

| Item | Who to ask | Why |
|------|-----------|-----|
| DLE GitLab account + PAT | DLE support ticket | Clone the repo |
| DASH 3 chat.zip test data | Scott | Test replays before MASH |
| AWS GovCloud creds (optional) | Jared | Bedrock cloud inference |
| IRC server IP at MASH | White cell / exercise staff | Live mode at the event |
| CoP database endpoint (optional) | Sarah Bowman | Write to the actual CoP |

---

## 6. Troubleshooting

**"pip: command not found"** -- Python wasn't added to PATH. Re-run the
installer and check "Add python.exe to PATH", or find it at
`C:\Users\<you>\AppData\Local\Programs\Python\Python3XX\Scripts\`.

**SSL errors cloning from DLE** -- DoD CA bundle not installed. See
section 1.3.

**Docker won't start** -- Enable virtualization in BIOS (VT-x / AMD-V).
WSL 2 requires it.

**"NVIDIA Container Toolkit" errors** -- GPU passthrough requires
Docker Desktop WSL 2 backend + NVIDIA drivers installed on Windows
(not inside WSL). Run `nvidia-smi` from a Windows command prompt to
verify drivers.

**Ollama model pull hangs** -- Large model downloads (4-8GB). On slow
connections, use `qwen2.5:3b` (2GB) instead: change
`CHAT_TO_COP_LLM_MODEL=qwen2.5:3b` in docker-compose.yml or pass
`--model qwen2.5:3b` on the command line.

**Pipeline produces all "passthrough" results** -- LLM not responding.
Check Ollama is running (`ollama list`) and the model is loaded.
The pipeline will still work (regex fallback), but quality is lower.

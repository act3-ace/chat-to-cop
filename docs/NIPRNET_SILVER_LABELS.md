# Generating Silver Labels on NIPRNet

Use Ask Sage (Claude Opus 4.6) or GenAI.Mil (Gemini 2.5 Pro) on NIPRNet to generate
high-quality silver labels for free. Both are OpenAI-compatible — no code changes needed.

## Prerequisites

- AF VPN connected to NIPRNet
- CAC card authenticated
- Ask Sage API key (from https://chat.genai.army.mil portal, use CDAO-OSD link)
- Python 3.10+ with chat-to-cop installed

## Step 1: Get the Code

If chat-to-cop isn't on your NIPRNet machine yet:

```bash
git clone https://gitlab.dle.afrl.af.mil/c2es1/mash/chat-to-cop.git
cd chat-to-cop
pip install -e ".[dev]"
```

If it's already there:

```bash
cd chat-to-cop
git pull
pip install -e ".[dev]"
```

## Step 2: Get Your API Key

### Ask Sage
1. Go to https://chat.genai.army.mil/login?code=CDAO-OSD (must use CDAO-OSD link for 4M token allocation)
2. Log in with CAC
3. Navigate to API settings / API keys
4. Copy your API key

### GenAI.Mil
1. Follow your organization's GenAI.Mil enrollment process
2. Obtain API key from the portal

## Step 3: Generate Labels

### Option A: GenAI.Mil / STARK API with Gemini 2.5 Pro (RECOMMENDED — 50M token budget)

GenAI.Mil uses the STARK API gateway at `https://genai.mil/stark/api`.
It serves OpenAI-compatible `/v1/chat/completions`. 50M token budget — our 935 messages
need ~3.3M tokens (6.6% of budget).

```bash
python scripts/generate_silver_labels.py \
    --url https://genai.mil/stark/api/v1 \
    --api-key STARK_BN-vJWxuRW4ewmrXFdQg95F7bFoFJBhv0GvDfWfYpD0 \
    --model gemini-2.5-pro \
    --rate-delay 2 \
    --output data/labels/dash3_silver_labels_gemini_pro.jsonl
```

**Available models:** Check with `GET /v1/models` (or check the STARK portal).
Gemini 2.5 Pro should be listed. The model name may differ — try `gemini-2.5-pro`
or `gemini-pro` or check the portal for the exact model ID.

**Token budget:** ~3.3M of 50M = 6.6%. You can run this many times with margin to spare.

### Option B: Ask Sage with Claude Opus 4.6 (best quality, tight budget)

Ask Sage is a separate service at `api.genai.army.mil` with a 4M token/month budget.

```bash
export ASKSAGE_API_KEY="your-ask-sage-api-key-here"

python scripts/generate_silver_labels.py \
    --url https://api.genai.army.mil/v1 \
    --api-key $ASKSAGE_API_KEY \
    --model claude-opus-4-6 \
    --rate-delay 5 \
    --output data/labels/dash3_silver_labels_opus.jsonl
```

**Token budget:** ~3.3M of 4M = 82%. Tight but fits. Do NOT list models (wastes ~13K tokens).

### Option C: Run BOTH and merge (BEST — consensus labels)

With 50M on GenAI.Mil and 4M on Ask Sage, you can afford both. Run Gemini Pro first
(cheaper budget), then Opus (tighter budget). Merge for consensus:

```bash
# After both runs complete, the label pipeline auto-merges by trust priority
python scripts/eval_against_labels.py \
    --labels-dir data/labels/ \
    --url http://127.0.0.1:11434/v1 \
    --model qwen2.5:7b-8k
```

Where Opus and Gemini Pro agree, confidence is highest. Where they disagree,
the label pipeline uses trust priority (both are `llm_judge` tier).

## Step 4: Verify Labels

```bash
# Validate format
python scripts/validate_labels.py data/labels/dash3_silver_labels_opus.jsonl

# Quick stats
python -c "
import json
from collections import Counter
types = Counter()
with open('data/labels/dash3_silver_labels_opus.jsonl') as f:
    for line in f:
        types[json.loads(line).get('extracted_type','?')] += 1
for t, c in types.most_common():
    print(f'  {t}: {c}')
"
```

## Step 5: Copy Labels Back

Copy the generated label files from your NIPRNet machine to your development machine.
The label files contain only extracted types and entity fields — no raw DASH data
that would be classification-sensitive.

```bash
# On your dev machine
scp niprnet-machine:chat-to-cop/data/labels/dash3_silver_labels_opus.jsonl \
    data/labels/
```

Or use a USB transfer if SCP isn't available between networks.

## Step 6: Recalibrate (on dev machine)

Once labels are on your dev machine:

```bash
# Eval against labels
python scripts/eval_against_labels.py \
    --labels-dir data/labels/ \
    --url http://127.0.0.1:11434/v1 \
    --model qwen2.5:7b-8k

# Recalibrate confidence
python scripts/recalibrate.py \
    --url http://127.0.0.1:11434/v1 \
    --model qwen2.5:7b-8k
```

## Troubleshooting

### "Connection refused" or timeout
- Verify you're on AF VPN + NIPRNet
- Verify CAC is authenticated
- Check API key is from the CDAO-OSD link (not a different subscription)

### "Model not found"
- Ask Sage model names may differ. Try listing with:
  ```bash
  curl -H "Authorization: Bearer $ASKSAGE_API_KEY" https://api.genai.army.mil/v1/models
  ```
  WARNING: This costs ~13K tokens from your monthly budget.

### Rate limiting (429 errors)
- Increase `--rate-delay` to 10 or 15 seconds
- Ask Sage individual quotas should not rate-limit at 5s delays

### Resume after interruption
- The script supports resume automatically — it skips messages already in the output file
- If you need to restart, just run the same command again

## Token Budget Estimate

| Component | Tokens per call | Total (863 LLM calls) |
|-----------|-----------------|----------------------|
| System prompt | ~3,000 | ~2.6M |
| User message | ~200 | ~0.17M |
| Output | ~300 | ~0.26M |
| **Total** | **~3,500** | **~3.0M** |

**GenAI.Mil (Gemini 2.5 Pro):** 3.3M of 50M budget = 6.6%. Run as many times as needed.

**Ask Sage (Claude Opus 4.6):** 3.3M of 4M budget = 82%. Tight but fits one run.

# Demo Runbook — chat-to-cop External Presentation

**Goal:** Show 5 real DASH 3 messages going through the pipeline live, in 30-60 seconds, in front of an external audience that won't wait for a full replay.

**Key tie-in:** The very first message in the demo is the **HYDRO_SL SITREP** that appears verbatim on **Slide 5** of [BRIEFING_SLIDES.md](BRIEFING_SLIDES.md). Show the slide first, then run the demo and let the audience watch the same message get extracted live. That's the moment the system "lands."

---

## Pick your backend

You have internet on your laptop, so the simplest path is a cloud API. All three options below run the same `scripts/quick_test.py` script — only the backend changes. Pick whichever you have credentials for.

### Recommended: Gemini 2.5 Flash (fastest, cheapest, ~$0.001 for the demo)

```bash
export CHAT_TO_COP_LLM_API_KEY="$GOOGLE_API_KEY"
python scripts/quick_test.py \
    --url https://generativelanguage.googleapis.com/v1beta/openai/ \
    --model gemini-2.5-flash
```

~3.5s per message, ~20s total. Best demo speed.

### Alternative: Anthropic Claude (Haiku for speed, Sonnet for the "name brand")

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
python scripts/quick_test.py --anthropic --model claude-haiku-4-5-20251001
# OR
python scripts/quick_test.py --anthropic --model claude-sonnet-4-5-20250929
```

Haiku: ~4-5s/msg. Sonnet: ~5-8s/msg. Slower than Gemini but Anthropic branding lands well in some rooms.

### Alternative: AWS Bedrock (validated end-to-end on 935 msgs at 99.6% success)

```bash
# Requires AWS credentials configured for us-gov-west-1
python scripts/quick_test.py --bedrock \
    --model us-gov.anthropic.claude-sonnet-4-5-20250929-v1:0
```

~4.8s/msg. The "enterprise" story — the same backend that ran the full DASH 3 replay.

### Local fallback: Ollama on your laptop

```bash
ollama serve &
ollama pull qwen2.5:7b   # ~4.5GB
./scripts/demo.sh        # pre-flight + warm-up + run
```

Use this only if internet is flaky at the venue or your API keys are not at hand. The wrapper script does pre-flight checks.

---

## Tonight (the night before)

1. **Decide which backend you'll use** based on what credentials you have ready.
2. **Run the demo end-to-end once** with that exact command. Make sure it works clean.
3. **Note the actual wall-clock time** so you know what to expect tomorrow.
4. **Have a second backend ready as backup** in case the primary fails (e.g., Gemini primary, Anthropic backup).

If you run into permission/auth issues tonight, fix them tonight — not in front of the audience.

---

## During the demo

### Step 1: Show Slide 5 ("How It Works")

Read the input message out loud:

> `HYDRO_SL: SITREP / AIR: ZEUS 12,13,14 shot down by TTG; YAMA11 flight shot down by TTG`

Say something like: *"This is a real message from DASH 3, 23 September. A pit boss reporting four kills in a single SITREP. Watch what the pipeline does with it."*

### Step 2: Run the demo

In a terminal already in the repo root, with your chosen backend command above. Total runtime: 20-60 seconds depending on backend.

### Step 3: What to point to in the output

You'll see five INPUT/OUTPUT blocks. For each, the audience cares about:

1. **Input:** the raw chat line (unstructured, military jargon)
2. **Output:** `type=...`, `confidence=...`, then the structured `entity` dict

The HYDRO_SL message should produce **four entity updates** (ZEUS12, ZEUS13, ZEUS14, YAMA11) all marked as DESTROYED. Point at this and say:

> *"Four entities, structured, with confidence and provenance. The CoP database can ingest this directly. A human typing this into the CoP would take 90 seconds. The pipeline did it in six."*

Other messages in the demo are also instructive:

- **`Hydro_Tank: RR15 F+40, RL36 F+50`** — Fuel state extraction, two entities. Shows the system handles cryptic brevity codes (`F+40` = 40 minutes of fuel).
- **`VEGAS_ABM2: .`** — A single period. Should be **filtered out** as noise. Point at the "filtered" output and say: *"The system knows this is not operationally relevant. We don't pollute the CoP with chat noise."*
- **`AOC_SIDO: tacrep e10-4, 4th-gen sam active...`** — A threat report. Should produce a `threat` update with the SAM as the entity.
- **`VEGAS_SL: copy harpy12 harpy14 thor13 shark14 all shot down`** — Cross-channel corroboration of kills already reported. Shows multi-entity status changes.

### Step 4: After the demo

If anyone asks *"how does it scale?"* or *"what about a full exercise?"*, point at:

- **Slide 9** — performance table: 7 backends validated, sub-10s latency
- **Slide 9b** — message rate analysis: every backend has 4-14x headroom over peak DASH 3 traffic

If anyone asks *"can you show me the code?"*, the relevant files are:

- `src/chat_to_cop/agent/channel_agent.py` — the per-channel stateful agent
- `src/chat_to_cop/backend/degrading.py` — equifinality / fallback chain
- `src/chat_to_cop/models/cop_update.py` — the Pydantic schema (the "shape of the boundary")

If anyone asks about the *research story* (RQ1/calibration), point at:

- [SPEAKER_MODEL_RESULTS.md](SPEAKER_MODEL_RESULTS.md) — the speaker model A/B finding
- [BENCHMARK_RESULTS.md](BENCHMARK_RESULTS.md) — calibration profile, multi-backend comparison

---

## Things NOT to do

- **Don't run the full DASH 3 replay live.** It takes 20 minutes to 3 hours depending on backend. The smoke test exists for this exact reason.
- **Don't open Swagger UI as your demo.** The Swagger page is impressive to engineers but bores everyone else. Save it for engineer Q&A only.
- **Don't apologize for the latency** if it's a few seconds per message. That's the point — the system is faster than humans typing into the CoP, which is the comparison the audience cares about.
- **Don't dwell on the third "noise" message.** Mention it briefly, move on. Lingering on a filtered message makes it look like nothing happened.
- **Don't switch backends mid-demo.** Pick one tonight, stick with it. Have the backup command ready in a second terminal *if* the first fails.

---

## Cheat sheet (the one command you'll actually run)

```bash
export CHAT_TO_COP_LLM_API_KEY="$GOOGLE_API_KEY"
python scripts/quick_test.py \
    --url https://generativelanguage.googleapis.com/v1beta/openai/ \
    --model gemini-2.5-flash
```

That's it. Five real military chat messages → structured CoP updates in ~20 seconds.

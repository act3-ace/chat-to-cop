# Chat-to-CoP: Technical Walkthrough

A narrative guide to the chat-to-cop AI staff officer system -- what it does, how it works, and why it matters. Written for multiple audiences: battle managers who need to understand what it does for their team, engineers who need to extend it, and researchers who want to study it.

For additional context, reference material, and meeting notes, see the [C2ES Google Drive](https://drive.google.com/drive/u/0/folders/1po6MGtfA5GF8QRub3X5LVpA_spJ9E7zz).

---

## Part 1: The Operational Problem

### What is DASH/MASH?

DASH (Distributed Analytic Synthetic Hybrid) and MASH (Multi-domain Analytic Synthetic Hybrid) are large-scale wargame exercises run by ACT3/C2ES at H2O Las Vegas. Dozens of operators sit in battle management pits -- physical rooms named Vegas, Hydro, Crusher, Taipan, Mesquite -- each responsible for a portion of the simulated battlespace.

Each **pit** is a coordinated unit consisting of:

- A **Pit Boss** who oversees the pit's operations and coordinates with the broader exercise
- **Two Battle Managers** who handle specific functions -- strike planning, tanker coordination, surveillance, etc. (e.g., `HYDRO_Strike`, `HYDRO_Tank`)
- Supporting roles include **intel and fires coordinators** (`Intel_OPS`, `3MARDIV_FIRES`) and **white cell** operators (`WF_BMA_03`, `AOC_SODO`) who inject scenario events

The pit boss and battle managers work as a tight team. Understanding this structure matters because the agent must learn the rhythm of each pit -- who reports to whom, which BM handles which assets, and how the pit boss synthesizes information for higher echelons.

Operators communicate via IRC chat and voice radio. Voice is captured by speech-to-text (STT) systems and piped into IRC as `#stt_*` channels. During DASH 3, there were 10+ active channels running simultaneously, with message rates of 2-7 messages per minute in normal operations, spiking to 8-12 per minute during engagements.

### What is the Common Operating Picture?

The CoP database is the shared ground truth for the exercise. It tracks every entity -- aircraft, ships, ground units, threats -- with their current position, status, and assignments. Vendors provide visualization tools that poll the CoP database and render the battlespace on screens in each pit.

The problem: **chat carries information that never makes it to the CoP.**

- Fuel states (`RR15 F+40, RL36 F+50`)
- Platform status changes (`ORCA01 gadget bent, RTB`)
- Weapons expenditure (`24 TLAMS launched, 38 remaining`)
- Kill results (`splash 2 flankers at bullseye 270/40`)
- Threat assessments (`probable J-15s and J-20s IVO Cigar 272/330`)
- CSAR events (`FG31 bailout, 2 good chutes`)
- BM-to-BM coordination and handovers

Currently, someone manually reads chat and types data into the CoP. This takes minutes per update. During high-tempo engagements, the CoP falls behind. Operators are making decisions on stale data.

### What we are building

An AI system that works **in the background** on behalf of human operators. It sits on the IRC server, reads every message, extracts world-state information, and pushes it to the CoP database in seconds. It highlights relevant information and keeps the human in the loop -- it is not a chatbot. Not an NER pipeline. An AI staff officer that watches radio nets, learns each operator's rhythm, and maintains situational awareness so the battle manager doesn't have to.

The agent learns by observing. It watches what operators do -- how a pit boss synthesizes reports, how a battle manager tracks assets, which jargon a specific operator uses -- and creates a model of their workflow. The goal is to learn from both expert and novice operators, eventually anticipating what information they need before they ask for it.

### May 2026 MASH strategy: virtuous cycle infrastructure

The MASH event in May 2026 is not a product demo. The goal is to deploy a **handwired, rudimentary agent** -- one that works well enough to be useful, but more importantly, one that establishes the **infrastructure for a virtuous cycle**: the agent processes live data, operators interact with the output, and that interaction generates the labeled training data needed to make the next version better.

What we present in May is a **starting point** designed to spark discussion with operators and developers. We need their input on what information matters most, how confidence should be displayed, and where the agent helps versus gets in the way. The infrastructure to capture that feedback is as important as the extraction quality.

### What vendors need from us

The CoP ecosystem depends on vendor visualization tools that poll structured data. From the C2ES All-Day Meeting (March 2026): the critical need is **"a highly structured API endpoint to augment Common Operating Picture data, rather than continuing to process unstructured data."** Our pipeline transforms unstructured IRC chat into structured, schema-conformant CoP updates with confidence scores and full provenance -- exactly the structured endpoint the vendor ecosystem requires.

---

## Part 2: What Chat-to-CoP Does

### Where it sits in the battle management pit

```
Battle Management Pit (Vegas, Hydro, Crusher, Taipan, Mesquite)
  Pit Boss + 2 Battle Managers per pit
     |
Operators type/speak
     |                         Voice radio
     v                             |
IRC Server (port 8097)        STT systems
     |                             |
     |     #stt_hydroBMA  <--------+
     |     #stt_crusherBMA <-------+
     |     #stt_C2Coord    <-------+
     |
     +-- #c2_coord (cross-BMA coordination)
     +-- #fires (fire missions)
     +-- #isr_reports (intelligence)
     +-- #jprc (personnel recovery)
     +-- #vegas_internal, etc.
     |
     v
+---------------------------------------------+
|          Chat-to-CoP Pipeline                |
|  (Desktop workstation w/ GPU, same room)     |
+---------------------------------------------+
     |
     v
CoP Database  <----  Vendors poll via REST API
     |
     v
Visualization tools on pit screens
```

The pipeline runs on a **single box with a single GPU** (confirmed sufficient for the local model), connected to the same network as the IRC server. It is in the room, processing messages in real time. Internet connectivity is expected at H2O Las Vegas, which enables access to commercial cloud models (sub-second latency) as a complement to local inference. Due to the amount of military jargon in DASH/MASH exercises, a customized or few-shot-prompted model is necessary -- general-purpose models miss domain-specific brevity codes and formats.

### Architecture overview

```
IRC WebSocket (ws://server:8097)
        |
   Message Router (async Python, channel fan-out)
        |
   +----+----+----+----+----+----+----+----+----+----+
   |    |    |    |    |    |    |    |    |    |    |
 Agent Agent Agent Agent Agent Agent Agent Agent Agent Agent
 #c2   #fires #isr #jprc #vegas #stt  #stt  #stt  #stt  ...
                                 hydro crush C2Crd mesq
        |
   Each agent maintains:
   - Conversation window (last 50 messages or 15 min)
   - Speaker models (learned per-user profiles)
   - World state snapshot (current belief about the battlespace)
   - Degrading LLM backend (primary -> fallback -> regex -> passthrough)
        |
   Fusion Agent (semantic deconfliction, cross-channel correlation)
        |
   Tiered Write Authority (auto / flagged / human review)
        |
   World State Store (SQLite) -> CoP REST API
```

### End-to-end message walkthrough

Let's trace a real DASH 3 message through the entire pipeline.

**Input message:**

```
[14:03:36] #c2_coord | HYDRO_SL: SITREP / AIR: ZEUS 12,13,14 shot down by TTG;
YAMA11 flight shot down by TTG / LRSAMs: / NAVAL SAMs: / OTHER: //
```

**Step 1: IRC parse**

The message router receives this via WebSocket and parses it into an `IRCMessage`:

```json
{
  "channel": "#c2_coord",
  "speaker": "HYDRO_SL",
  "text": "SITREP / AIR: ZEUS 12,13,14 shot down by TTG; YAMA11 flight shot down by TTG / LRSAMs: / NAVAL SAMs: / OTHER: //",
  "timestamp": "2025-09-23T14:03:36Z",
  "message_type": "PRIVMSG"
}
```

The router sends this to the `#c2_coord` channel agent.

**Step 2: Channel agent context assembly**

The `#c2_coord` agent adds this message to its conversation window. It already has context from prior messages:

```
[13:58:12] VEGAS_SL: @hydro_sl, what's your current air picture?
[14:01:45] HYDRO_SL: engaging TTG with ZEUS and YAMA flights
[14:03:36] HYDRO_SL: SITREP / AIR: ZEUS 12,13,14 shot down by TTG ...  <-- THIS MESSAGE
```

It checks its speaker model for `HYDRO_SL`:

- **Role:** Pit Boss, Hydro BMA
- **Area of interest:** Hydro battle management area
- **Pattern:** Issues SITREPs, uses structured format with `/` separators
- **Reliability:** High (confirmed by other channels 4 times, contradicted 0)

**Step 3: LLM extraction**

The agent sends the message to its degrading backend. The primary LLM (Qwen 2.5 7B) receives a prompt with:
- The conversation window (prior context, clearly separated)
- The target message (marked "EXTRACT FROM THIS MESSAGE ONLY")
- The current world state snapshot
- The output schema (Pydantic model, enforced by `instructor`)

The LLM returns structured output:

```json
{
  "update_type": "sitrep",
  "confidence": 0.92,
  "entities": [
    {
      "callsign": "ZEUS12",
      "entity_type": "FRIENDLY_AIR",
      "operational_status": "DESTROYED",
      "metadata": {"cause": "TTG", "context": "shot down"}
    },
    {
      "callsign": "ZEUS13",
      "entity_type": "FRIENDLY_AIR",
      "operational_status": "DESTROYED",
      "metadata": {"cause": "TTG"}
    },
    {
      "callsign": "ZEUS14",
      "entity_type": "FRIENDLY_AIR",
      "operational_status": "DESTROYED",
      "metadata": {"cause": "TTG"}
    },
    {
      "callsign": "YAMA11",
      "entity_type": "FRIENDLY_AIR",
      "operational_status": "DESTROYED",
      "metadata": {"cause": "TTG", "note": "entire flight"}
    }
  ],
  "source_channel": "#c2_coord",
  "source_speaker": "HYDRO_SL",
  "reasoning": "SITREP from Hydro Pit Boss reports 4 friendly aircraft losses to TTG engagement"
}
```

The `extraction_method` is overridden to `"llm"` and the timestamp is set from the IRC message.

**Step 4: Speaker model update**

The agent updates the `HYDRO_SL` speaker model:
- Confirmed: issues structured SITREPs with air/naval/other sections
- Tracks ZEUS and YAMA flights (Hydro BMA assets)
- Reports losses -- this speaker handles bad news directly (not delegated)

**Step 5: Fusion agent**

The fusion agent receives the 4 `CoPUpdate` entities. It checks:
- **Adversarial input?** No prompt injection patterns detected.
- **Duplicates?** The `#stt_hydroBMA` agent reported "ZEUS flight is down" 30 seconds earlier. Fusion recognizes this as the same event (temporal proximity + entity overlap), suppresses the STT duplicate, keeps the higher-confidence typed chat version.
- **Contradictions?** No conflicting reports about these entities.
- **Corroboration?** The STT report corroborates the typed chat -- confidence boost from 0.92 to 0.95.

After fusion: 4 entities pass through (the STT duplicate is suppressed).

**Step 6: Tiered write authority**

Each entity update is classified:
- Confidence 0.95 >= 0.7 threshold
- Update type `sitrep` is not high-risk (unlike `weapons` or `csar`)
- **Result: AUTO** -- write immediately to CoP database

**Step 7: World state store**

Four `CoPRecord` entries are written to SQLite with full provenance:
- Source channel, speaker, raw message text, timestamp
- Extraction method (`llm`), confidence (0.95)
- Context messages (the conversation window at time of extraction)

These records are also pushed to the CoP REST API, where vendor visualization tools pick them up.

**Total time: approximately 6-7 seconds** from IRC message to CoP database entry (on T4 GPU with Qwen 2.5 7B).

### What 13 types of information it extracts

The system recognizes 13 categories of world-state updates from chat data (see [CHAT_DATA_ANALYSIS.md](CHAT_DATA_ANALYSIS.md) for real examples):

| # | Update Type | % of Messages | Example |
|---|------------|---------------|---------|
| 1 | Entity identification | 10-15% | `TN 44504 is DDG1` |
| 2 | Status changes | 8-12% | `ORCA01 gadget bent, RTB` |
| 3 | Weapons employment | 5-8% | `24 TLAMS launched, 38 remaining` |
| 4 | Location/position | 10-15% | `TBM inbound, IVO N21.77, W72.27` |
| 5 | Threat assessment | 10-15% | `4th-gen SAM active, cigar 316/398` |
| 6 | Mission tasking | 15-20% | `@Hydro_Strike Destroy 2x H-6s` |
| 7 | Fuel/logistics | 5-7% | `RR15 F+40, RL36 F+50` |
| 8 | C2 handover | 3-5% | `You now have SADC duties for Lanes 2 and 3` |
| 9 | Personnel recovery | 2-3% | `FG31 bailout, 2 good chutes` |
| 10 | Fire missions | 1-2% | `FIRE MISSION! MLRS, 17QNE977926` |
| 11 | Cyber/EW | 2-4% | `Lateral movement to AOC file server` |
| 12 | SITREP/handover | 1-2% | Structured status reports (5-10 entities each) |
| 13 | Environmental | 1-2% | `Lightning within 5, ground stop` |

Approximately 55-65% of all chat messages contain actionable world-state information. The rest is operational noise (acknowledgments like "copy", "."), exercise control (STARTEX/ENDEX), or non-operational chatter.

---

## Part 3: Key Design Decisions

### Model-agnostic: OpenAI-compatible API everywhere

Every LLM call goes through the standard OpenAI chat completions API. The `instructor` library enforces Pydantic output schemas. This means any model that speaks the OpenAI protocol works:

- **Ollama** (local, GPU or CPU) -- our primary deployment
- **vLLM** (high-throughput server) -- for Analytics Gateway deployment
- **Groq** (cloud, free tier) -- for benchmarking large models
- **Any OpenAI-compatible endpoint** -- swap with one config change

No vendor SDK. No LangChain. No MCP. No framework. The backend abstraction is 30 lines of protocol code.

### Graceful degradation: flow never stops

The degrading backend is the core safety mechanism. Every channel agent has a stack of backends that it tries in order:

```
Level 1: Primary LLM (Qwen 2.5 7B)     -- full semantic extraction
    |
    | [circuit breaker: 3 failures or timeout -> open]
    v
Level 2: Fallback LLM (Qwen 2.5 3B)    -- reduced context, no speaker updates
    |
    | [circuit breaker]
    v
Level 3: Regex patterns                  -- known military formats only
    |
    | [circuit breaker]
    v
Level 4: Passthrough                     -- raw message, confidence 0.0
    (always available, never fails)
```

This is a **Simplex Architecture** from the Run-Time Assurance (RTA) framework. The passthrough backend is the guaranteed safe state -- it always succeeds, and it never drops data. Pattern B from the anti-ontology framework: out-of-ontology data is retained, not discarded.

**Real results from DASH 3 replay (935 messages, laptop CPU):**
- LLM extracted 100 messages (circuit breaker tripped frequently on CPU)
- Regex caught 876 more
- Passthrough: 0 (regex covered everything the LLM missed)
- **Zero messages dropped**

Circuit breakers use `pybreaker`: 3 consecutive failures opens the breaker, automatic cooldown (30s default) retries. On GPU, recovery takes seconds. On CPU, recovery took 44 minutes (validating that CPU-only is not viable for production).

### LLM as ontology: no predefined schema for the world

Different teams use different terminology for the same concepts:

```
WF_BMA says:     "splash 2 flankers at bullseye 270/40"
WF_ISR says:     "confirmed 2x SU-27 destroyed, grid XK4423"
STT picks up:    "uh... two more down, west of the river"
```

A traditional system requires someone to define a mapping between these representations before the exercise starts. An LLM maps all three to the same world-state update because it understands language.

This is the intellectual foundation from the "Against the Ontological Mandate" paper: the LLM is the universal translator. No ontology, no comms protocol, no pre-coordination. The fusion agent performs semantic deconfliction -- recognizing that reports from different channels describe the same event -- without field matching.

Our Pydantic schemas are provisional (Pattern A: ontology as hypothesis). The `metadata` dict overflow captures anything that doesn't fit the current schema. Non-conforming records are first-class signals of novelty, not errors to be discarded.

### Stateful agents: context matters

"Copy" after a tasking message is an acknowledgment. "Copy" in isolation is noise. A stateless pipeline cannot tell the difference.

Each channel agent maintains:

1. **Conversation window** -- sliding buffer of the last 50 messages or 15 minutes. Included in the LLM prompt so the model has conversational context.

2. **Speaker models** -- learned per-speaker profiles built during the session. The agent doesn't need to be told who `Hydro_Tank` is. After 10-15 messages, it knows:
   - Role: tanker controller for Hydro BMA
   - Tracks: RR15, RL36, MR26, BG01 (tanker assets)
   - Pattern: reports fuel states using `F+XX` notation
   - Reliability: consistently confirmed by other channels

3. **World state snapshot** -- the agent's current belief about the battlespace. When a message says "4 more launched," the agent knows the current weapons inventory and can produce a delta.

### "70% is great": speed over perfection

Even basic extraction adds value. A CoP entry with `confidence: 0.7` that arrives in 6 seconds is more useful than a hand-typed entry with `confidence: 1.0` that arrives 3 minutes later. The design explicitly acknowledges the system will be wrong sometimes:

- Every update carries a confidence score (0.0-1.0) and extraction method label
- Tiered write authority prevents low-confidence updates from auto-writing
- Passthrough messages (confidence 0.0) are clearly marked as unprocessed
- The system works in the background -- it augments human staff, not replaces them

This also prevents **overtrust** -- the "Phantom Signature" problem where a confidently-wrong AI output degrades human decisions. By always showing provenance and confidence, operators can calibrate their trust per-update.

---

## Part 4: Results

### Hardware comparison

| Environment | GPU | Model | Tokens/s | Per-extraction (p50) | Viable for MASH? |
|-------------|-----|-------|----------|---------------------|-------------------|
| AG GPU (g4dn.xlarge) | T4 16GB | qwen2.5:7b | 41.0 | ~6.5s | Yes |
| AG CPU (m7i.2xlarge) | None | qwen2.5:7b | 8.3 | ~4-6 min | No |
| Laptop CPU | None | qwen2.5:3b | ~3 | ~33s | No |

**Key finding:** GPU is required for real-time operations. CPU inference is 5x slower on tokens per second and 30-50x slower on wall clock per extraction.

### Model quality comparison

Tested via eval harness against synthetic labeled data with entity-level scoring:

| Model | F1 (type) | F1 (entity) | Field Accuracy | Schema Errors | Latency (p50) |
|-------|-----------|-------------|----------------|---------------|---------------|
| **Qwen3-32B** (Groq cloud) | **0.96** | **0.95** | **82%** | 0% | 1.2s |
| Qwen2.5:7b (local T4) | 0.92 | 0.91 | 73% | 1% | 6.5s |
| Llama-3.3-70B | 0.88 | 0.85 | 71% | 12% | 1.5s |
| Llama-4-Scout | 0.84 | 0.80 | 68% | 15% | 1.8s |
| Llama-3.1-8B | 0.72 | 0.68 | 58% | 22% | 0.8s |

**Key findings:**
1. Qwen family dominates for structured JSON extraction -- 0-1% schema errors vs 12-22% for Llama.
2. Few-shot prompt engineering matters more than model size -- a 3B model with good examples beats a 70B model with a bare prompt.
3. Qwen3-32B is the target MASH model (F1=0.96), pending 24GB+ VRAM hardware.

### Per-type extraction recall (Qwen2.5:7b on T4)

| Update Type | Recall | Notes |
|-------------|--------|-------|
| entity_id | 100% | Trivial for LLM |
| fuel | 100% | Regex also handles this well |
| tasking | 100% | |
| weapons | 100% | |
| threat | 92% | Misses some implicit threats |
| status_change | 71% | Brevity codes like "gadget bent" are hard |
| csar | 0% | Needs few-shot examples in prompt |

### Full DASH 3 replay statistics

935 real messages from the 23 September exercise, 11 channels, run through the complete pipeline:

| Metric | Value |
|--------|-------|
| Total messages | 935 |
| LLM extractions (successful) | 100 |
| Regex fallback extractions | 876 |
| Updates emitted by agents | 477 |
| Updates after fusion | 376 |
| Fusion deduplicates | 56 |
| Fusion contradictions flagged | 1 |
| Fusion corroborations | 4 |
| Channels discovered | 10 |
| Messages dropped | 0 |

**Channel update distribution:**
- `#vegas_internal`: 145 updates (most active)
- `#stt_crusherBMA`: 88 updates
- `#c2_coord`: 86 updates (highest information density)
- `#stt_hydroBMA`: 75 updates
- `#stt_C2Coord`: 41 updates
- `#isr_reports`: 24 updates
- `#stt_mesquiteBMA`: 7 updates
- `#stt_taipanBMA`: 6 updates
- `#fires`: 5 updates

Note: the laptop CPU run had only 100 LLM extractions out of 935 because the circuit breaker tripped constantly on CPU-bound inference. On GPU, the LLM handles the full message stream without timeouts.

### What we got right

- **Degradation path works.** Zero messages dropped across 935 messages. The fallback chain (LLM -> regex -> passthrough) operates exactly as designed.
- **Multi-channel simultaneous processing.** 10+ channels with a single Ollama instance, no message loss.
- **Entity identification is near-perfect.** Track number resolution (`TN 44504 is DDG1`) is 100% recall.
- **Fuel and weapons extraction is strong.** Both regex and LLM handle the abbreviated formats well.
- **Fusion deduplication works.** 56 duplicates caught -- primarily STT channels echoing typed chat.

### What needs improvement

- **CSAR: 0% recall.** The prompt needs few-shot examples for bailout/rescue scenarios.
- **Status change: 71%.** Brevity codes like "gadget bent" (radar failure) and "winchester" (weapons depleted) need to be in the prompt's few-shot examples or the regex pattern set.
- **Bullseye notation.** The system cannot convert bullseye bearing/range ("270/40") to geographic coordinates without the scenario's reference point.
- **STT noise.** Voice channels are ~60% noise. The fusion agent helps, but noisy STT reports still produce low-confidence updates.
- **No ground truth labels.** Benchmark numbers use synthetic data. Real DASH 3 accuracy requires human-labeled ground truth (see [LABELING_GUIDE.md](LABELING_GUIDE.md)).

---

## Part 5: Research Value

### FACS connection

Chat-to-cop is not analogous to a FACS (Family of Autonomous Combat Systems) -- it IS a miniature FACS. The agent pool exhibits all four FACS properties:

| FACS Property | Chat-to-CoP Implementation | Scale Equivalent |
|---------------|---------------------------|-----------------|
| **Flexible** | Add new channels mid-exercise, reconfigure prompts at runtime | New sensor vs. new channel |
| **Adaptable** | Circuit breaker degradation, speaker model learning, confidence re-weighting | Platform failure vs. model failure |
| **Configurable** | Swap models, adjust timeouts, add/remove agents without restart | Force recomposition vs. agent pool recomposition |
| **Trustworthy** | Every output has confidence, source, method, provenance chain | Sensor fusion trust vs. extraction trust |

Everything ACT3 wants to study at the FACS scale can be studied first at the chat-to-cop scale, with faster iteration cycles and cheaper failures. See [RTA_FACS_FRAMING.md](RTA_FACS_FRAMING.md) for the full mapping to Run-Time Assurance mechanisms.

### Two research questions

**RQ1: Online user modeling during operations**

*How can a software agent personalize and learn a model of the user during operations?*

Each channel agent maintains per-speaker models that grow during the session. When `WF_BMA_03` sends 15 messages, the agent learns their role, jargon, area of interest, and reliability -- all from context, not from pre-configuration.

What makes this hard: the model must be learned *fast* (operators don't have time to train the system), *incrementally* (no batch retraining), and *accurately enough* (a wrong model is worse than no model -- the "Phantom Signature" problem).

Experiments for MASH:
- Compare extraction quality for modeled vs. new speakers
- Measure how many messages it takes to build a useful model
- Test transfer: does learning `Hydro_Tank` help with `Crusher_Tank`?
- A/B test: agents with speaker modeling on vs. off

**RQ2: Ontology-free team adaptation**

*How can a heterogeneous team adapt without defining an ontology beforehand?*

The LLM maps `"splash 2 flankers"`, `"confirmed 2x SU-27 destroyed"`, and `"two more down"` to the same CoPUpdate because it understands language. The fusion agent performs semantic deconfliction without field matching.

Experiments for MASH:
- Compare semantic fusion vs. traditional schema-matching
- Introduce a new team mid-exercise with unfamiliar terminology
- Deliberately omit jargon definitions from the prompt -- measure in-context learning
- Quantify how much "shared understanding" comes from model training vs. session learning

### Anti-ontology KPP measurements

From Pattern F (OODA-centric metrics), four Key Performance Parameters are instrumented:

| KPP | Definition | Benchmark Result |
|-----|-----------|-----------------|
| **TTA** (Time-to-Adapt) | Recovery time after component failure | GPU: <60s, CPU: 44 min |
| **URR** (Unknown Retention Rate) | Fraction of unclassifiable inputs retained | 100% (structural guarantee) |
| **ASR** (Adversarial Semantic Robustness) | Response to prompt injection/manipulation | Regex detection + confidence penalty, never drops |
| **MTTR** (Mean Time to Recover) | Circuit breaker cooldown + first successful retry | GPU: ~35s, CPU: ~60-90s |

---

## Part 6: How to Get Started

### Quickstart

See [QUICKSTART.md](QUICKSTART.md) for a 15-minute setup guide. The short version:

```bash
git clone https://gitlab.dle.afrl.af.mil/c2es1/mash/chat-to-cop.git
cd chat-to-cop
pip install -e ".[dev]"
ollama pull qwen2.5:3b
python scripts/quick_test.py
```

### Key files to read first

| File | What you'll learn |
|------|-------------------|
| `src/chat_to_cop/agent/channel_agent.py` | The core component -- how an agent processes a message |
| `src/chat_to_cop/backend/degrading.py` | The fallback chain with circuit breakers |
| `src/chat_to_cop/agent/fusion_agent.py` | Cross-channel deconfliction and safety checks |
| `src/chat_to_cop/models/cop_update.py` | The Pydantic schema for extracted world-state updates |
| `src/chat_to_cop/output/cop_writer.py` | Tiered write authority to CoP database |
| `src/chat_to_cop/agent/supervisor.py` | Agent pool management, health monitoring, load shedding |

### Open issues to pick up

Check the [GitLab issue board](https://gitlab.dle.afrl.af.mil/c2es1/mash/chat-to-cop/-/issues) for current sprint work. Sprint 3 items that are parallelizable:

- Docker containerization (#15)
- Full DASH 3 replay on GPU (#16, in progress)
- CoP database writer (#17, blocked on contractor schema)
- RAI provenance / MLflow (#18)

### How to label data

We need human-labeled ground truth for real DASH 3 messages. See [LABELING_GUIDE.md](LABELING_GUIDE.md) for the labeling protocol, inter-annotator agreement targets, and how labeled data feeds back into prompt improvement.

### Running the eval harness

```bash
# Local model
python scripts/eval_models.py --url http://127.0.0.1:11434/v1 --model qwen2.5:7b --count 50 -v

# Groq cloud (free, no GPU needed)
GROQ_API_KEY=gsk_... python scripts/eval_models.py \
    --url https://api.groq.com/openai/v1 --model qwen/qwen3-32b --count 50 --rate-delay 18

# Compare multiple models
python scripts/eval_models.py --compare --count 50 --rate-delay 18
```

### Running a DASH replay

```bash
# Full pipeline with supervisor, fusion, and store
python -m chat_to_cop.replay data/chat/Dash3-GBC/Data/23Sep/usaf/chat.zip

# Specify model and endpoint
python -m chat_to_cop.replay data/chat/Dash3-GBC/Data/23Sep/usaf/chat.zip \
    --url http://127.0.0.1:11434/v1 --model qwen2.5:7b

# Real-time playback speed
python -m chat_to_cop.replay data/chat/Dash3-GBC/Data/23Sep/usaf/chat.zip --speed 1.0
```

### Running tests

```bash
# Full CI check (lint + format + tests)
ruff check src/ tests/ && ruff format --check src/ tests/ && pytest tests/ -k "not integration" -v

# Current: 549 tests passing
```

---

## Glossary

| Term | Definition |
| ------ | ----------- |
| **Pit** | A battle management unit: pit boss + 2 battle managers |
| **Pit Boss** | Oversees a pit, synthesizes information for higher echelons |
| **Battle Manager (BM)** | Handles specific functions (strike, tanker, surveillance) within a pit |
| **White Cell** | Exercise control operators who inject scenario events and play roles |
| **CoP** | Common Operating Picture -- the shared ground-truth database |
| **TMDA** | Transformational Model for Decision Advantage -- the broader framework the CoP sits within |
| **HLT** | Human Language Translation team -- responsible for some real-time streaming work after the virtual vendor was cut |
| **DASH** | Distributed Analytic Synthetic Hybrid -- prior wargame exercise series (DASH 1-3) |
| **MASH** | Multi-domain Analytic Synthetic Hybrid -- the May 2026 exercise |
| **STT** | Speech-to-text -- voice radio transcribed and piped into IRC channels |
| **Bullseye** | A reference point for bearing/range position reports |

## Related Documents

- [ARCHITECTURE.md](ARCHITECTURE.md) -- Full system architecture with component details
- [BENCHMARK_RESULTS.md](BENCHMARK_RESULTS.md) -- Detailed performance and quality benchmarks
- [DESIGN_PHILOSOPHY.md](DESIGN_PHILOSOPHY.md) -- Equifinality, antifragility, FACS principles
- [RTA_FACS_FRAMING.md](RTA_FACS_FRAMING.md) -- Run-Time Assurance mapping and FACS connection
- [SYSTEM_CARD.md](SYSTEM_CARD.md) -- DoD AI system card (pre-deployment)
- [CHAT_DATA_ANALYSIS.md](CHAT_DATA_ANALYSIS.md) -- All 13 update types with real examples
- [LABELING_GUIDE.md](LABELING_GUIDE.md) -- Ground truth labeling protocol
- [QUICKSTART.md](QUICKSTART.md) -- 15-minute setup guide
- [C2ES Google Drive](https://drive.google.com/drive/u/0/folders/1po6MGtfA5GF8QRub3X5LVpA_spJ9E7zz) -- Meeting notes, DASH event data, reference library

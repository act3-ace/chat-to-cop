# Chat-to-CoP: What We're Actually Trying to Learn

**Briefing for Cap & Bert — 2026-04-16**
Hamilton "Scott" Clouse, ACT3

---

## What this deck is (and isn't)

**Is:** the fundamental research questions driving our experiments, and what the data has told us so far.

**Isn't:** a feature list, a roadmap, or a request for more compute.

The operational target (MASH, May 2026) is the **context**. The science is about **what an adaptive AI system can and can't do** when it sits next to a human operator and tries to learn them.

---

## The chat-to-cop problem, in one slide

Real military IRC/STT chat. ~935 msgs in the DASH-3 GBC exercise. Telegraphic, jargon-dense, abbreviation-heavy, multi-channel.

An AI staff officer needs to:

1. Read every message in real time.
2. Extract **structured updates** to a Common Operating Picture (entities, threats, taskings).
3. Do this faster than a human, and keep doing it under stress, degradation, and novel content.

This is a workload a single battle captain currently can't keep up with.

---

## Two fundamental research questions

> **RQ1.** Can an LLM personalize to individual operators (their jargon, their patterns, their role) **without regressing** on its core extraction ability?

> **RQ2.** Can a shared schema for the operational picture **emerge** from operator language, rather than being declared top-down?

Everything we've done this year is an experiment on one of these two questions. They are not engineering questions. They are research questions about what LLM-driven autonomous systems can structurally do.

---

## Why these questions, and not others

These are the questions that sit at the intersection of:

- **FACS principles** — equifinality (multiple paths to the same answer), antifragility (failures produce information), heterogeneous loosely-coordinated agents.
- **The "Against the Ontological Mandate" paper** — we do not declare ontologies; the LLM is the ontology.
- **Operational reality** — battle captains don't speak the same way as the exercise manual, and they don't speak the same way as each other.

Pick a different pair of questions and you're doing a different project.

---

## The starting bet we made

For RQ1, our first architectural bet was the obvious one: **build a model of each operator from their past messages (a "speaker profile") and inject it into the prompt every time that operator speaks**.

Plausible. Mirrors how humans orient. Cheap to implement.

So we built it. And then we measured it.

---

## What the literature warned us about

In April 2026 we wrote up a critique of our own design — *Why Speaker Profiles Poison Structured Extraction* — after a literature review pulled six converging mechanisms:

1. **Distractor sensitivity** (Shi 2023) — profiles are semantically overlapping but functionally irrelevant. Worst kind of distractor.
2. **Attention dilution** (Liu TACL 2024, "Lost in the Middle") — adding 10–15 profile blocks steals attention from the actual message.
3. **Anchoring and priming bias** — profile content shifts the model from evidence-based to expectation-based extraction.
4. **Self-consuming output compounding** — LLM-generated profiles fed back into LLM prompts produce broken-telephone drift (Shumailov 2024).
5. **Constrained-decoding amplification** — Pydantic + schema enforcement turns every upstream bias into a structurally-valid wrong answer.
6. **Instruction overload** (Harada 2026) — more "rules" in the system prompt hurt instruction-following, not help.

The prediction from the critique: naive speaker profiles will **hurt type accuracy at every model size**.

---

## We measured it. It hurt.

Held-out eval, 93-record v1 test split, every LoRA adapter we trained vs. Qwen2.5-14B zero-shot baseline:

| Adapter | Type Accuracy | vs base |
|---|---|---|
| **14B base, zero-shot** | **77.4%** | — |
| role-bc (best LoRA) | 69.9% | **−7.5pp** |
| role-wf | 57.0% | −20.4pp |
| base-extraction | 54.8% | −22.6pp |
| role-jtac | 60.2% | −17.2pp |
| role-isr | 49.5% | −27.9pp |
| **role-log** (worst) | **8.6%** | **−68.8pp** |

**Every speaker-conditioned adapter regressed.** The worst dropped type accuracy by 68 percentage points. Entity precision went *up* for some roles (role-bc: 0.44 → 0.68), which is a trap: the model got more confident about wrong types.

This is RQ1's first real answer: **the naive personalization approach doesn't work** — not because LLMs can't learn, but because injecting persona into the prompt is the wrong mechanism.

---

## What the regression was actually telling us

Root-cause diagnosis (documented in `project_lora_regression_finding.md`):

- Adapters were trained on **synthetic** examples with doctrinal roles (`WF`, `BC`, `JTAC`, `ISR`, `LOG`).
- Adapters were evaluated on **real DASH-3 silver labels** with sender-derived roles (`VEGAS`, `afrl`, `AOC`, `CRUSHER` ...).
- Training and test distributions did not overlap on role labels at all.
- The SFT prompt promised fields the training outputs didn't contain.

So the regression is both **a real finding** (the injection approach is structurally fragile) **and a methodological warning** (synthetic training data with schema drift poisons the loop even when the surface setup looks fine).

The methodology lesson is as important as the result.

---

## The architectural alternatives we're now testing (Phase 3)

The critique paper ranked nine alternatives by implementation difficulty and expected impact. We are systematically testing the lowest-cost, highest-leverage four:

| | Alternative | Bet |
|---|---|---|
| **8.1** | Selective RAG (retrieve top-k examples) | Retrieve over inject |
| **8.2** | Current-sender-only profile | One profile, not 15 |
| **8.3** | Two-pass extraction (freeform → reformat) | Decouple understanding from JSON compliance |
| **8.4** | Pipeline separation (classify → extract) | Don't ask one model to do two jobs |

Phase 3.1 (8.2) and Phase 3.2 (8.1) have completed their first runs on Blueback (2026-04-15 to 2026-04-16).

---

## Preliminary signal from Phase 3.1

Current-sender-only injection vs. full profile injection — early read on 186-record run:

- Entity precision holds (which matters operationally).
- Type accuracy *recovers* toward the zero-shot baseline.
- The delta is a **+5.3pp** signal that survived the re-run with channel-scoped profiles.

It's not a publication-grade result yet — the next run uses the fully adjudicated Gold-v1 subset. But it's the first signal that **the mechanism matters more than the magnitude** of the personalization.

Translation: "use less context about the speaker, more carefully" beats "cram everything we know into the prompt."

---

## What we learned about evaluation

We did not start the project with the right eval instrumentation. We had to build it.

- **Type accuracy** is load-bearing. It's the one metric that gates operational usefulness. Everything else is downstream.
- **Entity precision up + type accuracy down** = your model got worse, even though the precision looked like progress.
- **Match rate is a trap** — "the model produced a structurally valid update that mapped to a known type" is not the same as "it was the right type."
- **Parse errors matter** — 6 parse errors on role-log were a leading indicator that the adapter had degenerated.

Bert, Cap: **the evaluation harness is half the science.** If we only have type accuracy, every negative result could be a positive one in disguise and vice versa.

---

## Where the two RQs sit now

### RQ1 (personalization without regression)

**What we've answered:** naive prompt-based injection is not the right mechanism. The data is clear.

**What's open:** whether *any* personalization mechanism (retrieval, per-speaker LoRA trained on real data with early stopping, gated context injection, user embeddings) can beat the zero-shot base on both type accuracy and precision simultaneously. That's the Phase 3 and Phase 4 experiments.

### RQ2 (emergent schema)

**What we've answered:** we can extract structured updates without a declared ontology; the LLM's implicit taxonomy is workable.

**What's open:** when operators use novel terms or role structures, does the system gracefully extend its internal taxonomy, or does it quietly drop those signals into a catch-all? We don't have good measurement of this yet. It's the hardest question on the list.

---

## Current experiments still running

As of this briefing:

- **Phase 3.1 rerun** with channel-scoped profiles on MLA (MI300A) — in flight
- **Phase 3.2 retrieval-k=1** — completed, awaiting adjudicated scoring
- **Phase 3.3 two-pass extraction** — next in queue
- **Phase 3.4 pipeline separation** — after 3.3
- **Ollama vs vLLM serving benchmark** on Blueback L40 — vLLM infrastructure working; full benchmark pending
- **Corrective LoRA training** — blocked pending Phase 3 winner and a real-data-only training loop with early stopping

Resource usage on RYZ account: ~33K core-hours burned since 2026-04-13; ~1.77M remaining on Blueback, healthy runway through MASH.

---

## The MASH go/no-go shape

Based on the data so far, the MASH deployment decision tree should be:

1. **Speakers OFF + 14B base zero-shot** is the defensible floor. 77.4% type accuracy.
2. **A Phase 3 architectural alternative** can ship only if it non-inferiorly beats the floor on type accuracy **and** demonstrably reduces false positives on "none"/noise messages.
3. **No LoRA adapter ships** until a properly trained and held-out-validated version beats the floor on pre-registered criteria.

This is a research-driven deployment rule, not a conservative one. The regression data earns it.

---

## What I'd like to discuss with you

1. **Is the RQ framing right?** Are these the two questions we want to be answering, or are we avoiding a third one (e.g., adaptation speed, human-in-the-loop)?

2. **Eval-as-science.** How much of the time between now and MASH should be spent strengthening the eval harness vs. running more experiments?

3. **The emergent schema question (RQ2).** What would a satisfying answer look like? Today I can't measure it well.

4. **Scope of "personalization."** Are we solving for "learn about this operator" or "route messages to the right specialist model based on who sent them"? The experimental implications differ sharply.

5. **Publication vs. MASH timeline.** The LoRA regression finding is a genuine negative result with a literature-predicted mechanism. Does it stand on its own as a publication before MASH, or do we bundle it with the Phase 3 winner?

---

## Appendix: deeper reading

- `docs/speaker_profile_extraction_degradation_report.pdf` — full six-mechanism literature analysis (origin of the architectural alternatives ladder)
- `project_lora_regression_finding.md` (memory) — confirmed diagnosis, corrective training plan
- `results/lora_eval_summary_20260415_152939.json` — hard numbers backing the regression table
- `results/arch_alternatives_*.jsonl` — raw Phase 3 outputs
- `results/ablation_ladder_20260414_162406.jsonl` — K-capped profile sweep

*Compute: Navy DSRC Blueback (MI300A, L40) on AFSNW27526RYZ; Narwhal historical results on the same account.*

# Mia's Monday Brief

Welcome back! Here's what happened while you were out and what you should focus on.

## What Was Built (Mar 26-29)

The chat-to-cop pipeline is **working end-to-end**: IRC chat → LLM extraction → structured CoP updates → SQLite store → dashboard.

- **496 tests**, full CI/CD with Docker + Apptainer
- **Validated against real DASH 3 data**: 5/5 correct on smoke test, 3% error rate on 30-message replay
- **Model comparison**: Qwen3-32B achieves F1=0.96 on synthetic labeled data (Groq free tier). Confirmed as the MASH target model.
- **Key finding**: few-shot prompt engineering matters more than model size. A 3B model with good examples beats a 70B model with a bare prompt.

### What's in the pipeline
- Channel agents with conversation windows + speaker models (CTA/SDAC framework)
- Degrading backend: LLM → smaller LLM → regex → passthrough (never drops data)
- Fusion agent: cross-channel deconfliction, trust scoring, adversarial detection
- Supervisor: agent lifecycle, health monitoring, priority-based load shedding
- CoP writer: tiered write authority (auto/flagged/human-review) + kill switch
- Confidence calibration prototype
- HTML dashboard at `http://localhost:8000/dashboard`
- Eval harness with entity-level scoring

## Your #1 Task: Label 100 DASH Messages

**This is the most valuable thing you can do this week.**

All our precision/recall numbers are on synthetic data. We need real data labels to know the true accuracy. The labeling guide is at `docs/LABELING_GUIDE.md`.

### How to do it:
1. Get set up: follow `docs/QUICKSTART.md` (15 min)
2. Open DASH 3 typed-chat logs (the replay parser can dump them — see the guide)
3. Label each message with: expected update_type + expected entities
4. Focus on `#c2_coord` and `#isr_reports` channels (highest value)
5. Target: 100 messages, mix of all update types
6. Save as JSONL per the format in the labeling guide

### What this unlocks:
- **Real precision/recall** on actual military chat data
- **Calibration model** trained on real data (not synthetic)
- **Error taxonomy** — which message types does the system get wrong?

## Your #2 Task: Review the Dashboard

1. Run: `python -m chat_to_cop.replay <path-to-dash3-chat.zip> --url http://127.0.0.1:11434/v1 --model qwen2.5:3b`
2. Open: `http://localhost:8000/dashboard`
3. **Tell us what's wrong.** Your human factors eye will catch things we can't:
   - Are the update types correct?
   - Are the entities right?
   - What's missing?
   - Is the confidence meaningful?
   - What would make this useful to a battle manager?

## Your #3 Task: Improve the Prompt

The system prompt is in `src/chat_to_cop/backend/openai_compat.py`. It has 8 few-shot examples. You can improve it:
- Add examples for update types we're weak on (CSAR, fire missions, cyber/EW)
- Add jargon terms to the glossary that we're missing
- Correct any military terminology that's wrong

## Important Notes

- **Use `127.0.0.1` not `localhost`** when connecting to Ollama. There's an IPv6 bug on Windows.
- **Ollama keeps unloading models.** If you get 404 errors, run `ollama pull qwen2.5:3b` again.
- **All work goes through branches + MRs.** See CLAUDE.md for the workflow.
- **Tests must pass before pushing.** Run: `ruff check src/ tests/ && ruff format src/ tests/ && pytest tests/ -k "not integration"`

## Architecture Overview (60-second version)

```
IRC Chat → Message Router → Channel Agents (1 per channel)
                                 |
                            [conversation window + speaker models]
                            [degrading LLM backend: big → small → regex → passthrough]
                                 |
                            Fusion Agent (deconfliction, trust scoring)
                                 |
                            CoP Writer (tiered: auto/flagged/human-review)
                                 |
                            World State Store → Dashboard + REST API
```

The **LLM is the ontology** — no predefined schema for military jargon. Different teams use different terminology, and the LLM maps them to the same structured output. See `docs/DESIGN_PHILOSOPHY.md` for the full rationale.

## Open Questions for You

1. **Are the 13 update types right?** See `docs/CHAT_DATA_ANALYSIS.md`. Anything missing?
2. **Is the SDAC mapping useful?** Speaker models track Sensing/Deciding/Acting/Collaborating. Does this match what operators actually do?
3. **What should the confidence threshold be for auto-writing to the CoP?** Currently 0.7. Too aggressive? Too conservative?
4. **What does a battle manager need to see in the dashboard?** Current version shows a table of updates + entities. What's missing?

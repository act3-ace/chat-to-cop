# DELTRON (HLT) vs chat-to-cop (ACT3) — Comparison

**Source:** HLT ↔ ACT3 meeting, 2026-04-09. Slides shown by Jeremy Gwinnup (AFRL/RHWTE Human Language Technologies Group), transcript captured. Code repo for DELTRON not yet accessible — Jeremy intended to fix permissions but the link was 404 for both Scott and Sarah at meeting time.

**STT correction:** The Microsoft Teams auto-transcript renders the project name as "Beltran". The slides clearly show **DELTRON** — *Discernment Engine for Linguistic Triage, Relevance and Operational Notification*. Use DELTRON in any written reference.

---

## Side-by-side

| Dimension | DELTRON (HLT) | chat-to-cop (ACT3) |
|---|---|---|
| **Owner** | Jeremy Gwinnup, Tim Anderson, Eric Hansen — AFRL/711 HPW/RHWTE HLT | Scott Clouse — AFRL/ACT3 (with Mia Kollia, AI alignment) |
| **Time invested** | "2 to 2.5 weeks" (per Jeremy) | A few weeks of human time + heavy agent assistance |
| **Repo state** | Private; permissions unresolved | Open on DLE GitLab `c2es1/mash/chat-to-cop`, 843 tests passing, 35 MRs merged |
| **Phase** | "JADBANK microservice (FastAPI + RabbitMQ, Phase 2)" — not yet deployed | Single-box `docker compose up`, deploy-ready |
| **Primary goal** | **Message triage / classification** into 5 importance tiers (NOISE → CRITICAL) | **World-state extraction** to populate the CoP database with structured updates |
| **Output shape** | A score per message — which messages humans/downstream systems should look at first | Structured `CoPUpdate` JSON: type, entities, confidence, provenance |
| **Architecture** | Dual-path: **Fast Path** (rules/regex, sub-ms) + **Slow Path** (LLM, ~1s/msg). Lane router chooses one. | Channel-per-agent + speaker-per-agent + fusion agent + degrading backend (LLM → smaller LLM → regex → passthrough) |
| **Inference engine** | **vLLM** on cluster GPUs, served via LiteLLM proxy ("Bergstrom 4000") | **Ollama** locally, plus 7 validated cloud/HPC backends; vLLM on AG is open issue #51 |
| **Models tested** | gpt-oss 20B, gpt-oss 120B, recommending Qwen3-4B / Llama 3.2 3B going forward | Qwen2.5 3B/7B/14B/32B, Qwen3-32B (Narwhal V100), Gemini Flash, GPT-4.1 nano, Claude Haiku/Sonnet, Bedrock Sonnet 4.5 |
| **Hardware target** | Cluster GPUs now (A100s); targeting RTX 6000 / 24 GB workstation for MASH | Single 24 GB GPU (Qwen 32B) OR cloud API; Qwen3-32B validated on V100 32 GB; vLLM on AG planned |
| **Air gap** | "Fully air-gap compatible. No external API dependencies." | Configurable: local Ollama for air gap, cloud APIs when internet is available, validated both ways |
| **Ground truth** | Acknowledged the gap; same conclusion as us — no real labels yet | Opus silver labels (935 messages, Claude Opus 4.6 via Ask Sage on NIPRNet); calibration model fitted (ECE=0.67 on 7B) |
| **Confidence calibration** | Not yet | Done for 7B; framework ready for other models |
| **CoP database integration** | Same blocker as us (waiting on Sarah's schema) | Same blocker (#17) |
| **Speaker / user modeling** | Not present in the architecture | Per-user agent + speaker reliability scoring + RQ1 A/B (no measurable benefit on 7B; capacity hypothesis being tested in #52) |
| **Cross-channel fusion** | Not present | Fusion agent does deconfliction, corroboration, contradiction detection |
| **Message rate handling** | Fast path: 12,090 msg/sec. Slow path: ~1.05 msg/sec at 120B | 4-14× headroom over peak DASH 3 message rate across all backends |
| **Notable engineering choice** | "KISS approach. No LangChain, no off-the-shelf orchestration libraries." | Same — also no LangChain, no MCP, no framework. instructor + Pydantic + tenacity + pybreaker only |

---

## DELTRON results that matter for us

These are the headline numbers from Jeremy's slides. Worth knowing because they will land in any future C2ES briefing on the comparison.

### Throughput
- **Fast Path (rules):** 0.08 ms/msg, ~12,090 msg/sec, full 1,918 msg run in 0.2 sec
- **Slow Path 20B:** 765 ms/msg, ~1.31 msg/sec, full run in (some seconds)
- **Slow Path 120B:** 950 ms/msg, ~1.05 msg/sec, full run in **30.4 minutes**

### Tier distribution (1,918 messages)
- Fast path classifies **91.9% of messages as Tier 1 (Low Value)**
- LLM slow path redistributes heavily: ~35% to Tier 2 (Coordination), ~7% to Tier 3 (High Value)
- Their phrasing: *"the LLM recognizes operational significance that regex patterns miss"*

### Critical detection (Tier 4)
- Fast Path found 8 critical messages
- Slow 20B found 25
- Slow 120B found 20
- Both LLMs agreed on the same 8 — meaning the LLMs find ~12-17 *additional* critical messages the rules miss
- Specific gap patterns the fast path is missing: `boltout`, `bail out`, `crash`, `crash event`, `smoke (in cockpit)`, `rtb`/return-to-base with emergency, `BROKEN ARROW`, brevity codes, `CSAR`/chopsard, `defending`/active engagement, `SA-21 active`, TACREP with `awake`/`active` SAM
- 166 operationally significant messages (Tier 3+) were missed by the fast path; 377 channels account for 60% of misses

### 20B vs 120B
- **73% exact agreement**
- 517 messages differ by ≥1 tier, 90 by ≥2 tiers
- LLM reasoning failures: 20B = 28.3%, 120B = 31.8%
- **Their verdict:** "120B is not clearly better than 20B for this task. The 20B model appears to be the better cost/performance tradeoff."
- 120B is 24% slower and requires substantially more GPU memory

### Future direction (their slide)
- Fine-tune Qwen3-4B or Llama 3.2 3B on labeled DASH data
- Knowledge distillation from 20B to a smaller student model
- Target <100ms per message
- Deploy as JADBANK microservice (FastAPI + RabbitMQ, Phase 2)

---

## Where we agree (good signs for collaboration)

1. **LLM extraction is the right hammer.** Both teams independently chose LLM-based interpretation over rules-only. Convergent design = strong validation.
2. **Smaller models suffice.** Both teams found that bigger ≠ better for this domain. Their 20B vs 120B finding mirrors our Qwen 14B-best-type-match-49% finding.
3. **Air-gap-capable deployment matters.** Both designed for offline use with internet as a bonus.
4. **Schema is the bottleneck.** Both teams are stuck waiting on Sarah's CoP database schema before final integration.
5. **Ground truth is hard.** Both teams settled on LLM-as-judge silver labels rather than waiting for SME labels.
6. **No frameworks.** Both teams explicitly avoided LangChain, CrewAI, etc. Same instinct for keeping the moving parts minimal.
7. **24 GB GPU target.** Both teams independently sized for the same hardware envelope.

---

## Where we differ (where collaboration could compound)

| Their strength | Our strength | Collaboration opportunity |
|---|---|---|
| **Mature regex/rules layer** with concrete missing-pattern list (boltout, crash, BROKEN ARROW, etc.) | Regex backend exists as the third tier of the degrading chain but the patterns are minimal | **They could replace our regex_fallback.py.** Direct port. |
| **Tier-based importance scoring** (5 levels) | Boolean noise filter + per-update confidence | We could ingest their tier as an additional signal upstream of write authority |
| **vLLM serving experience** at scale on cluster GPUs | Ollama with vLLM as open issue (#51) | They can de-risk #51 for us |
| **Critical-message detection focus** (CSAR, BROKEN ARROW, equipment failures) | World-state extraction with tiered write authority | We could route their Tier 4 hits straight to our human-review queue |
| Doing **classification** (one decision per message) | Doing **extraction** (structured fields per message) | These are complementary, not duplicative. Their classifier could be a pre-router upstream of our extraction. |

**The natural integration story:**

```
IRC message
    │
    ▼
DELTRON triage      ──── Tier 0/1 (NOISE/LOW)  ──→ drop or log
    │
    ▼ (Tier 2+)
chat-to-cop extraction ──── structured CoPUpdate ──→ tiered write authority ──→ CoP database
```

DELTRON answers "should we look at this?" — chat-to-cop answers "what does it actually say?" — and Sarah's schema receives the result. Three teams, one pipeline, no duplication.

---

## Things to flag in your next briefing

1. **Convergent validation.** Two independent teams chose the same architectural patterns. This is a strong signal that the approach is right, not just our preference.
2. **Their critical-message gap list is a gift.** Even if we never integrate DELTRON, the missing-pattern list (boltout, crash, BROKEN ARROW, brevity codes) is directly portable into our regex backend tonight.
3. **20B-vs-120B finding lines up with ours.** "Bigger isn't better" was Jeremy's headline. 14B-is-the-sweet-spot is ours. Same conclusion from different angles.
4. **JADBANK microservice is their integration target.** That's a Sarah Bowman / C2ES architecture choice that will affect how we both connect to the CoP. Worth understanding before MASH.
5. **Sarah Bowman is the schema owner.** Not a faceless contractor. Direct working relationship now established. This is the single biggest unblock from this meeting.
6. **The Confluence link** Sarah dropped in chat (`/spaces/JADPACT/pages/548864328/Mash+Documentation`) is where the schema docs live or will live. Mia and others need to be added to that group.

---

## Action items from the meeting

| Action | Owner | Status |
|---|---|---|
| Sarah to add Mia + others to the JADPACT Confluence group | Sarah Bowman | Pending |
| Sarah to talk to Z (Elizabeth Frost) about the next collaboration step | Sarah Bowman | Pending |
| Jeremy to fix permissions on the DELTRON GitLab repo so we can see his code | Jeremy Gwinnup | Pending |
| Scott to fix permissions on chat-to-cop GitLab repo (Jeremy and Sarah saw 404) | Scott Clouse | Pending |
| Eric to ask Z about NVIDIA DGX Spark procurement (not pursuing — Sarah pessimistic) | Z (Elizabeth Frost) | Closed: not pursuing |
| Sarah to share Confluence docs with us; reconvene early next week or tomorrow | Sarah Bowman | Pending |
| Track Orion Hilbrand / RI work — they're also doing some chat parsing, "more data collection oriented" per Sarah; they're physically on-site next week | Sarah Bowman to keep us in the loop | Watching |

---

## What I'd prioritize on our side this week

1. **Open chat-to-cop visibility one notch.** Both Jeremy and Sarah hit 404. Even Internal-on-DLE-GitLab visibility would let them see the README and the architecture docs without granting commit rights. Five minutes.
2. **Port DELTRON's missing-regex-pattern list into our regex_fallback.py.** They literally listed the patterns on a slide. Free precision for our fast path.
3. **Issue #17 unblock probe.** Now that we know Sarah personally, ask whether she can share even a *draft* schema this week. Even a stale draft beats waiting indefinitely.
4. **Don't pre-emptively merge architectures.** Jeremy was clear he wants to iterate on his own first. Respect that — let cross-pollination happen at the *interface* level (her schema, the JADBANK microservice contract) rather than at the code level.

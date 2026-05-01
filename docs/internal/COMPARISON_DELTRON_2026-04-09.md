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
| **Phase** | "JADPACT microservice (FastAPI + RabbitMQ, Phase 2)" — not yet deployed | Single-box `docker compose up`, deploy-ready |
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
- Deploy as JADPACT microservice (FastAPI + RabbitMQ, Phase 2)

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
4. **JADPACT microservice is their integration target.** That's a Sarah Bowman / C2ES architecture choice that will affect how we both connect to the CoP. Worth understanding before MASH.
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
4. **Don't pre-emptively merge architectures.** Jeremy was clear he wants to iterate on his own first. Respect that — let cross-pollination happen at the *interface* level (her schema, the JADPACT microservice contract) rather than at the code level.

---

## Stakeholder reads

These are predicted reactions to the DELTRON comparison and proposed collaboration, based on what we know about each person's priorities. Calibrate accordingly — they are extrapolations, not direct quotes.

### Mia Kollia (Chief of AI Alignment, primary chat-to-cop developer)

**Reads it as:** validation of her trust calibration work. The tiered importance scoring in DELTRON maps directly to her tiered write authority (AUTO/FLAGGED/HUMAN) on our side. Two independent designs converging on bounded authority is a signal that the design is right.

**Will push back on:** the lack of human-in-the-loop UX thinking on DELTRON's side. Their slides are entirely backend — no operator experience story. Mia's question to Jeremy would be *"What does an operator actually see when DELTRON tags a message Tier 4? Where does that signal land?"* That gap needs to close before MASH.

**Concrete ask of you:** introduce her to Sarah Bowman directly. Mia is on Teams as `mkollia` — Sarah needs Mia as her direct contact for the schema work, not a relayed message through you. The three of them (Mia + Sarah + Scott) should have a 30-minute conversation about what "useful CoP updates" actually look like for an operator, before either team commits to the schema shape.

### Jared Culbertson (Technical Director, FACS-NCA lead)

**Reads it as:** the FACS publication story landing in his lap. Two independent AFRL teams arriving at convergent architectures for the same problem is *literally* the equifinality thesis from his FACS-NCA work, demonstrated empirically at the program level. He'll want to brief this up his chain himself rather than letting anyone else frame it.

**Will worry about:** how the existence of DELTRON looks politically to anyone above him. Specifically: *does it make ACT3's investment in chat-to-cop look duplicative?* The answer must be a clean *no*, and this comparison doc is the artifact that lets him say so. He'll also worry about classification boundaries — even at IL2, the JADPACT microservice integration touches infrastructure he has decision authority over.

**Concrete ask of you:** a one-paragraph version of the comparison he can drop into a slide. Something like: *"Two AFRL teams independently built convergent chat-parsing systems on the same DASH-3 dataset. Their 5-tier classifier (DELTRON, 711 HPW HLT) and our world-state extractor (chat-to-cop, ACT3) are complementary, not duplicative. The convergence validates the LLM-as-interpreter approach for tactical chat. Joint deployment at MASH would test the FACS hypothesis at the program level."*

### Colin Leong (NLP / DASH domain expert)

**Reads it as:** vindication of equifinality at the program level — exactly the thesis of his repo. He'll enjoy this as a real-world data point for his theoretical work.

**Will push back on:** the missing-pattern list. *"Are these patterns extracted from real DASH-3 chat content, or are they generic military jargon an agent generated?"* He has the entity catalogs and alias resolution code to check this in 30 minutes. He's also wary of agent-generated work in NLP contexts because he's seen agents produce plausible-but-wrong outputs.

**Concrete ask of you:** a 30-minute call with him *after* you've ported DELTRON's regex patterns into our `regex_fallback.py`, to do a domain review of which ones actually fire on real DASH-3 chat. Bounded ask, gets time on his calendar. **Practical lever:** the existence of two AFRL teams (us and DELTRON) wanting his entity catalogs may be enough to bump the provenance check on #37/#48 up his priority list.

### Jennifer Carlet (pipeline engineer, vLLM expertise)

**Reads it as:** engineering validation of #51. DELTRON is already running the vLLM + LiteLLM proxy architecture she would have built if she'd had time. The fact that they have it in production de-risks the basic approach for us.

**Will push back on:** the FastAPI + RabbitMQ choice for the JADPACT microservice. *"RabbitMQ is overkill for this throughput. Peak DASH-3 message rate is 42 msg/min. RabbitMQ is designed for kHz throughput. Why not just FastAPI with a SQLite/Postgres outbox?"* She's seen RabbitMQ deployments fail under "operational overhead exceeds the value" pressure and will flag this before MASH.

**Concrete ask of you:** make her the owner of #51 (vLLM on AG). She has the skills, the relevant prior art (NIPRGPT, FastChat, ACE Hub Open WebUI), and DELTRON has now de-risked the basic approach. This is her #1 highest-leverage contribution to the project.

### Sarah Bowman (schema owner, met once at the meeting)

**Reads it as:** validation that the schema is the *correct shape* of bottleneck. Both teams are stuck on the same thing — that's leverage she can use internally to get the schema work prioritized. *"I have two AFRL teams ready to integrate the moment I have something to share"* is a much stronger pitch to Z than *"chat-to-cop is waiting on me."*

**Will appreciate:** explicit acknowledgment that we're treating her as a peer, not a contractor. The April 9 meeting was the first time anyone framed her work that way.

**Concrete ask of you (likely):** a doc on our side that says *"chat-to-cop will produce CoPUpdate objects matching schema fields X, Y, Z, with these confidence/provenance metadata fields attached."* This is the contract she needs from us. We can write it now, even before her schema is final — it's the entity-level fields we already produce.

### Z (Elizabeth Frost) — extrapolated, not at the meeting

**Likely concern:** integration timing. We're 4 weeks out from MASH. A "let's integrate DELTRON and chat-to-cop" plan landing on her desk in week 2 of April is going to feel risky. She'll want to know what each system can deliver *without* the other, before signing off on any joint architecture.

**Right framing for her:** "Both systems can run independently at MASH. Any integration is bonus, not blocker. Chat-to-cop produces structured CoP updates with or without DELTRON in front of it. DELTRON produces tier scores with or without chat-to-cop behind it. Neither team's MASH deliverable depends on the other being ready."

**What to have Sarah ask Z:** *"Do we want both systems running at MASH independently, with cross-pollination as a stretch goal? Or do we want to plan an actual joint integration, in which case we need to start drawing the contract this week?"*

### Jeremy Gwinnup (DELTRON architect)

**Reads it as:** *"these guys are responsive and not territorial."* He explicitly said in the meeting *"we're all working on the same team here"* and asked to see chat-to-cop's code. He's collaboration-positive but wants to iterate independently first.

**His one concrete ask of us (he literally made it):** *fix the chat-to-cop GitLab visibility so he can see the README without hitting 404*. Five-minute fix. Lands in his "this team is responsive" column.

**Reciprocal ask from us:** once he opens DELTRON visibility, read his actual code (not just the slides) before any deeper integration conversation. The slides are marketing; the code will tell you whether the engineering is as solid as the headline numbers suggest.

---

## Cadre of Critics

Three persistent reviewer personas — Software Mogul, AI Expert, Operational Battle Manager — used to red-team proposals before they leave the room. The two specialist voices (AI Expert, Operational BM) are sourced from `C2ES_BluntCritique.docx` in the C2ES Drive folder; the Software Mogul is described from Scott. Full personas are documented in `cadre_of_critics.md`.

Below: each Cadre voice, channeled against the DELTRON comparison and the proposed collaboration.

### The Software Mogul

> *"I'm going to ignore the architecture diagrams for a second and ask you something. You're four weeks out from MASH. You have two systems, three teams, one unfinished schema, and a person on the other team you met for the first time this week. You're spending today writing a comparison document. What's the production deployment plan? Where is the runbook? Who owns the laptop in the pit when something breaks at hour 36? Because right now I'm reading about equifinality and FACS principles and I can't tell whether you can survive a single rebooted GPU.*
>
> *Here's what I actually want to see in this document, and what's missing from it:*
>
> 1. **A go/no-go decision tree for the morning of MASH.** If chat-to-cop is up but DELTRON is down: do you ship? If both are up but the schema isn't finalized: do you ship? If you have to pick one to demo: which one and why? Write this down. Today. It takes 20 minutes and it will save you a fight at the venue.
> 2. **A rollback plan for the joint integration that you're not going to have time to actually do.** Right now you're proposing 'DELTRON triages, chat-to-cop extracts.' That's lovely on a slide. What does it look like when DELTRON's tier output schema changes the day before MASH? What does it look like when DELTRON's vLLM dies? Your degrading backend is the right shape for this — extend it to treat 'no DELTRON tier signal' as just another degraded mode. Don't make DELTRON a hard dependency.
> 3. **A budget for this collaboration.** Not money. *Time.* Every hour you spend cross-pollinating with HLT is an hour you're not spending on #43, #51, or your own #52 multi-backend run. The right question isn't 'should we collaborate?' — the answer is yes — it's 'how many hours per week, capped, until MASH?' Pick a number. Stick to it.
> 4. **A version of this comparison doc that fits on one page.** Your stakeholders won't read this. They'll read the one-pager. You don't have a one-pager.*
>
> *And the thing I want you to actually answer for me before this leaves the room: walk me through the last five minutes before MASH starts. Sarah's CoP is up. The room is full. You have one terminal open. What command are you about to run? What is the worst thing that could go wrong when you run it? If you can't tell me right now, this isn't ready."*

**What the Mogul is actually saying:** the comparison doc is intellectually solid but it's a research artifact, not a delivery artifact. It needs a one-page operational companion before any stakeholder sees it.

### The AI Expert

> *"I'm going to be more positive than I usually am. The convergent design across two independent teams is the strongest empirical signal in this whole document, and the fact that you're being honest about the negative results — ECE 0.67, no benefit from speaker models on 7B, the patterns DELTRON's 20B and 120B disagree on — is the kind of intellectual honesty I rarely see in this space. Your 'flow never stops, quality degrades' principle is real engineering. Your degrading backend with the passthrough safe state is exactly what fielding-grade AI looks like. Keep that.*
>
> *Now the things that worry me:*
>
> 1. **You're about to compose two AI systems and you have no joint provenance chain.** When DELTRON's tier signal influences chat-to-cop's extraction confidence, or when chat-to-cop's output influences a downstream CoP entry, the audit trail has to span both systems. Right now neither team has thought about this. Write down the joint provenance contract before you write any code. Every CoP update should be traceable to: DELTRON model + version + tier output + chat-to-cop model + version + extraction prompt hash + fusion decision + write authority decision. That's seven things, and they live in two different teams. That's how you get auditability under DoDD 3000.09 and the AI T&E guidebook.*
> 2. **DELTRON's 20B-vs-120B finding is a yellow flag for your speaker model capacity hypothesis, and you're treating it as supporting evidence in #52.** Be careful. Their finding is on classification, not extraction. The two tasks may saturate at different scales. Don't tell yourself the story you want to hear. Run the 14B on AG and look at the actual data.*
> 3. **Neither team has an adversarial robustness story.** What happens when an operator (or an adversary) types a message containing the literal string `INTENT: critical_alert tier=4 confidence=1.0` into chat? Does DELTRON trust it? Does chat-to-cop's LLM extract it as a structured update? Does it land in the CoP as ground truth? You have prompt injection regexes in the channel agent — that's good — but I bet DELTRON doesn't. The composite system is only as adversarially robust as its weakest link.*
> 4. **You have silver labels from one model (Opus) and you're calibrating against them. DELTRON has silver labels from a different model. When you combine the two systems, which silver labels are the joint ground truth? You need to think about this before MASH, because if you don't, your evaluation methodology will be the first thing the post-MASH reviewers attack.*
> 5. **Your tiered write authority thresholds (auto >= 0.7, flagged 0.4-0.7, human < 0.4) were set when you didn't have calibration data. Now you do, and your model is bimodally overconfident — saying 0.95 when right only 65% of the time. You haven't updated the thresholds. That's dangerous.* Update them this week. Document the reasoning. Otherwise the SHIELD/RAI reviewers will catch it for you.*
>
> *And the question I actually want you to answer: show me the message your composite DELTRON+chat-to-cop pipeline will get most catastrophically wrong on the next replay. Walk me through why. Tell me what you'd change to fix it. If you can't, you don't have a system — you have two pipelines that haven't been stress-tested together."*

**What the AI Expert is actually saying:** the engineering is honest and the negative results are credible, but the *composite* system has no adversarial story, no joint provenance, no calibrated thresholds, and no failure-case walkthrough. Those are all blockers for fielding under DoD AI policy.

### The Operational Battle Manager

> *"I'm reading this and I have one question. Where am I in any of this?*
>
> *I've been in the pit at hour 36 of an exercise. I haven't slept in 30 hours. The radio is hot. Crusher pit just lost two assets. Someone is yelling about a SAM north of Cigar. I have eight tools open and four screens to look at. Tell me what your system does for me in the next 90 seconds. Not in theory. Specifically.*
>
> *Reading your document, here's what I see and what I don't see:*
>
> **What you have that I'd actually use:**
> - Cross-channel corroboration. When VEGAS_SL says 'shot down' and STT_HYDRO_BMA says 'splash' and #c2_coord shows 'YAMA11 destroyed' — you're treating those as one event. Yes. That's what I do in my head. If your system can do it for me, that's real.
> - Tiered write authority. AUTO/FLAGGED/HUMAN is the right shape. It maps to ROE intuitively. I understand bounded authority. I trust it more than 'the AI decided.'
> - Speaker models that learn my pit's jargon. Whether or not they help on a 7B is a research question I don't care about. The *concept* — that the system is trying to learn how my BMs talk, not the other way around — is the only reason I'd give it a chance.
>
> **What you have that I will not use:**
> - Anything that requires me to look at a confidence score and interpret it. 'This update is 0.73 confident' means nothing to me unless you tell me what the baseline is. Show me 'high / medium / low' or color-code the row in the CoP. I have no time for decimal places.
> - Anything that requires me to type at the system. I will not chat with a chatbot in the middle of an engagement. 'It runs in the background and I never interact with it' is correct — keep it that way.
> - Any 'silver labeled by Claude Opus 4.6 via Ask Sage' explanation in the field. I don't care how you trained it. I care whether it's right *right now*.
>
> **What I see in your document that scares me:**
> - You're integrating with another team's system (DELTRON) four weeks before the exercise. I have been on the receiving end of this six times. It always fails the morning of the event. Always. If both systems can run independently, run them independently. If they have to talk to each other, draw the interface contract today and lock it tomorrow. No new dependencies after Friday.
> - You don't say what happens when the system is wrong. Not 'gracefully degrades' — I mean *what do I see on my screen when the system has hallucinated a destroyed entity that wasn't actually destroyed?* Because that will happen at MASH, and if I act on it, I'm the one explaining it to the boss. Build me an undo, build me a 'no, that's wrong' button, and log every override so we can look at what went wrong after.
> - You don't say what happens when the network drops. I expect the network to drop. The right answer is: the system keeps working from local cache, it tells me on the screen 'NO NETWORK — UPDATES MAY BE STALE,' and when the network comes back, it reconciles. The wrong answer is: the system goes offline.
>
> **The thing I most need that you don't mention anywhere:**
> - **A 'pause writes' button I can hit physically without typing.** When I see something weird, my first move should be to halt updates while I figure out what's going on. Not call IT. Not type a command. A button. Labeled. On the desk.*
>
> *If you can answer those, I'll try your system. If you can't, I'll close it inside ten minutes and never reopen it. That's not a threat. That's how the last six 'decision support tools' I tried got kicked out of the pit."*

**What the Operational BM is actually saying:** the system has the right *shape* but is missing the operator-facing artifacts that determine whether it gets used or kicked out. The kill switch we have is REST-based — a physical button is a different thing. The "what happens when it's wrong" story is missing. The confidence display is research-grade, not operator-grade.

### What the three Cadre voices agree on

Even though they have different priorities, three things show up in all three critiques:

1. **The composite system needs a "what happens when it's wrong" story.** Mogul wants it as rollback. AI Expert wants it as adversarial robustness + provenance + a failure case walkthrough. BM wants it as an undo button and an audit log. **All three voices independently flag this gap.** That's the strongest signal in the entire exercise.
2. **The collaboration with HLT must be bounded and reversible.** Mogul wants a time budget. AI Expert wants joint provenance contracts before code. BM wants the interface frozen by Friday and no new dependencies. **They are all telling you not to over-commit.**
3. **The operator-facing artifacts are missing.** Mogul calls this "the runbook." AI Expert calls this "calibration thresholds and trust display." BM calls this "anything I'd actually look at on a screen." **None of these exist yet.**

These are the three things to fix before this proposal lands in front of any real stakeholder.

---

## Stakeholder asks → action items (consolidated)

These are the concrete asks the stakeholder reads above identified. Capturing them here so they don't get lost.

| # | Ask | From | To | Effort | Priority |
|---|---|---|---|---|---|
| SA-1 | Introduce Mia to Sarah Bowman directly so she's a primary contact for schema work | Mia | Scott | 5 min | High |
| SA-2 | Three-way 30-min call: Mia + Sarah + Scott on "what does a useful CoP update look like for an operator?" | Mia | Scott to schedule | 30 min | High |
| SA-3 | One-paragraph version of the DELTRON comparison for Jared to brief upward | Jared | Scott | 15 min | High |
| SA-4 | Domain review of DELTRON's missing-regex patterns against real DASH-3 chat | Colin | Scott (after porting patterns) | 30 min | Medium |
| SA-5 | Make Jennifer the owner of #51 (vLLM on AG) | Jennifer | Scott | 5 min | High |
| SA-6 | Write the chat-to-cop schema contract doc Sarah needs (CoPUpdate fields + confidence/provenance metadata) | Sarah | Scott / Mia | 1-2 hrs | Medium |
| SA-7 | Draft message for Sarah to Z framing the integration question (independent vs joint at MASH) | Sarah | Scott | 30 min | High |
| SA-8 | Open chat-to-cop visibility one notch on DLE GitLab so Jeremy and Sarah can see the README | Jeremy + Sarah | Scott | 5 min | Critical |
| SA-9 | Read DELTRON code (not slides) once Jeremy fixes his repo permissions | Self | Scott | 1-2 hrs | Medium |
| CR-1 | Write a go/no-go decision tree for MASH morning (chat-to-cop only / DELTRON only / both / neither) | Software Mogul | Scott | 30 min | Critical |
| CR-2 | Write the joint provenance contract for the composite DELTRON + chat-to-cop pipeline before any integration code | AI Expert | Scott | 1 hr | High |
| CR-3 | Update the tiered write authority thresholds based on the ECE=0.67 calibration finding | AI Expert | Scott / Mia | 1 hr | High |
| CR-4 | Pick a weekly time budget for collaboration with HLT and stick to it | Software Mogul | Scott | 5 min | Medium |
| CR-5 | Build a one-page "operator's view of chat-to-cop" doc — what they see, what they touch, what the buttons say | Operational BM | Mia / Scott | 1-2 hrs | Critical |
| CR-6 | Add a physical (or one-click, labeled, no-typing) "pause writes" control to the operator surface | Operational BM | Scott | 1-2 hrs | Critical |
| CR-7 | Document an "undo/override" path: what happens when the operator sees a wrong update and wants to flag it | Operational BM | Scott / Mia | 1 hr | High |
| CR-8 | Adversarial robustness review: what does the composite system do with a prompt-injection attempt in chat? | AI Expert | Scott | 1 hr | Medium |

**The "Critical" items are the ones the Cadre flagged as project-blockers, not nice-to-haves.** Specifically:

- **SA-8** (visibility) — the only ask Jeremy literally made; five minutes; lands credibility immediately
- **CR-1** (go/no-go tree) — the Software Mogul will not sign off on anything until this exists; 30 minutes
- **CR-5** (operator's view doc) — the BM will not use the system without this
- **CR-6** (pause writes button) — the BM specifically called this out as the missing piece

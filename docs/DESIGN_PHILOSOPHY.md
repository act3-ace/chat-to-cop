# Design Philosophy

## This System Is the Research Artifact

The chat-to-cop pipeline is not a side project that happens to use AI. It is a **miniature FACS** (Family of Autonomous Combat Systems) — a heterogeneous team of AI agents that maintains shared situational awareness without a predefined ontology, adapts to component failures, and learns operator models online.

Everything ACT3 wants to study at the FACS scale can be studied first at the chat-to-cop scale, with faster iteration cycles and cheaper failures.

## Three Ideas, One Architecture

### Equifinality

**The concept:** Multiple execution paths can achieve the same goal. In BattleCOA analysis, equifinality means a plan remains viable even when individual components fail, because alternative paths exist to achieve the same effect.

**In this system:** Every CoP update has multiple possible paths to production:

```
Path 1: Large LLM (70B) with full context          -> structured update
Path 2: Small LLM (8B) with truncated context       -> structured update
Path 3: Regex pattern matching                      -> structured update
Path 4: Cross-channel fusion (another agent saw it) -> structured update
Path 5: Passthrough (raw message, human reads it)   -> partial update
```

Kill any single path and the system still produces useful output. This is not redundancy (N copies of the same thing) — it's equifinality (N different approaches to the same goal).

**What we can study:** Path entropy — how many viable extraction paths exist for a given message type? How does path diversity change under stress? Which message types are single-path (fragile) vs. multi-path (robust)?

### Antifragility

**The concept:** A system that gets better under stress, not just resilient (survives stress) or robust (ignores stress).

**In this system:** When a component fails, the failure produces information:

- **Backend failure** -> The circuit breaker records which backends fail for which message types. Over time, the system learns that Backend A handles fuel reports well but struggles with CSAR, while Backend B is the opposite. Routing improves.
- **Noisy channel** -> The fusion agent learns that STT channels have lower signal-to-noise. It weights typed chat reports higher. A channel that starts clean and degrades mid-exercise is automatically down-weighted.
- **Contradictory reports** -> When two agents disagree about the same event, the fusion agent flags it. These disagreements are the most valuable data points — they reveal where the system's understanding is weakest and where human attention should focus.
- **Speaker correction** -> When a battle manager corrects a previous report ("Disregard last, TN 44504 is NOT DDG1"), the agent learns that this track is ambiguous and raises the confidence threshold for future updates about it.

**What we can study:** Does extraction quality improve over the course of a session? Do agents that experience more failures early perform better late? Can we quantify the information gain from failures?

### FACS (Family of Autonomous Combat Systems)

**The concept:** A heterogeneous team of autonomous systems that are flexible (adapt to new missions), adaptable (respond to changing conditions), configurable (recompose at runtime), and trustworthy (humans can understand and calibrate trust in their outputs).

**In this system:** The agent pool IS a FACS:

| FACS Property | Chat-to-CoP Implementation |
|---------------|---------------------------|
| **Heterogeneous** | Different agents watch different channels, use different models, are in different degradation states |
| **Loosely coordinated** | Supervisor + fusion agent provide coordination, not central control. Each agent operates independently and produces useful output alone |
| **Configurable at runtime** | Add/remove channels, swap models, change degradation thresholds — all without restarting |
| **Trustworthy** | Every update includes confidence score, extraction method, source message, and context — humans can calibrate trust per-update |

**What we can study:** How does the agent team maintain shared situational awareness without a shared ontology? When a new channel (new team) joins mid-exercise with unfamiliar terminology, how quickly does the system adapt? Does the fusion agent develop implicit "coordination protocols" through learning, or does it need explicit rules?

## Two Research Questions

### 1. Online User Modeling During Operations

**The question:** How can a software agent personalize and learn a model of the user during operations?

**The mechanism:** Each channel agent maintains per-speaker models that grow during the session. When `WF_BMA_03` sends 15 messages, the agent learns their role, jargon, area of interest, and reliability — all from context, not from pre-configuration.

**What makes this hard:** The model must be learned *fast* (operators don't have time to train the system), *incrementally* (no batch retraining), and *accurately enough* (a wrong model is worse than no model — see Mia's "Phantom Signature" vignette on confidently-wrong AI outputs degrading human decisions).

**Experiments we can run at MASH:**
- Compare extraction quality for speakers the agent has modeled vs. new speakers
- Measure how many messages it takes to build a useful speaker model
- Test model transfer: does learning Hydro_Tank help with Crusher_Tank?
- A/B test: agents with speaker modeling on vs. off

### 2. Ontology-Free Team Adaptation

**The question:** How can a heterogeneous team of teams adapt to new challenges without defining an ontology or communications protocol beforehand or during operation?

**The mechanism:** The LLM is the ontology. Different teams use different terminology for the same concepts:

```
WF_BMA says:      "splash 2 flankers at bullseye 270/40"
WF_ISR says:      "confirmed 2x SU-27 destroyed, grid XK4423"
STT picks up:     "uh... two more down, west of the river"
```

A traditional system requires someone to define a mapping between these representations. An LLM maps all three to the same world-state update because it understands language. No ontology, no comms protocol, no pre-coordination.

The fusion agent performs semantic deconfliction — recognizing that reports from different channels describe the same event — without field matching. This is how human J2 (intelligence) staff actually work. We're replicating the cognitive process, not the data format.

**Experiments we can run at MASH:**
- Compare semantic fusion vs. traditional schema-matching on cross-channel events
- Introduce a new team mid-exercise with different terminology. Measure time-to-accurate-extraction
- Deliberately omit jargon definitions from the system prompt and measure whether in-context learning from conversation compensates
- Quantify how much of the "shared understanding" is in the model's training vs. learned from the session

## Why Not Just Build a Pipeline?

A regex-then-LLM pipeline would work for the demo. It would parse chat, extract fields, push to the database. It would be simpler to build and easier to explain.

But it would be a dead end. You can't personalize a regex tier. You can't study ontology-free adaptation in a system that requires predefined schemas. You can't demonstrate FACS principles with a sequential ETL process.

The agent architecture costs us maybe two extra weeks of development. In return, we get a system that is both operationally useful at MASH *and* a research platform for the problems ACT3 is chartered to solve. That's the trade worth making.

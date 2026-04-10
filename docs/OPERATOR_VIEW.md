# Operator View: What the Battle Manager Sees

**Status:** Draft for review by Mia Kollia (Chief of AI Alignment, HCI expert)
**Issue:** #55 — Operator-facing surface: what the BM actually sees
**Date:** 2026-04-08

This document synthesizes HMT (Human-Machine Teaming) research and applies it to the
chat-to-cop operator dashboard. It proposes a concrete design informed by ARL trust
calibration research, CRM principles from aviation, cognitive load theory, and our own
calibration findings (ECE=0.67 on Qwen2.5-7B). Mia: please tear this apart and make
it better. The goal is a starting point, not a final answer.

---

## 1. Design Principles

Seven principles, each grounded in research and mapped to our specific context: a
Battle Manager in the Crusher pit at hour 36 of a MASH exercise, fatigued, radio hot,
two assets just lost, SAM threat active.

### Principle 1: The operator's attention is the scarcest resource — protect it

The cognitive load research is unambiguous: sleep deprivation reduces working memory
capacity by up to 50%, and combat stress narrows attention and impairs executive
function (Ambush Systems, "Cognitive Overload: The Hidden Killer in Combat Systems").
NASA-TLX research shows that if cognitive workload is so high that an operator has
little or no spare capacity to handle a concurrent problem, the task and its supporting
interfaces must be redesigned (NASA-STD-3001).

**Application:** The dashboard must never compete with the radio for the BM's
attention. Default state is quiet. Only critical-confidence or high-risk updates
produce any interruption. Everything else flows into the table for when the BM has
a moment to glance over.

### Principle 2: Show system state, not system internals

Chen's SAT (Situation Awareness-based Agent Transparency) model from ARL defines
three transparency levels: (1) what the agent is doing now, (2) why it chose to do
that, and (3) what it expects to happen next (Chen et al., 2014; Chen & Proctor,
2017). Level 1 is always visible. Levels 2 and 3 are available on demand.

**Application:** The dashboard always shows what the system extracted (Level 1). The
"why" (which backend, what reasoning) is one click away but never in the foreground.
The BM does not need to know that Qwen2.5-7B produced an extraction via the
openai_compat backend — they need to know "CRUSHER 2 reports fuel state BINGO,
confidence: HIGH."

### Principle 3: Confidence must be calibrated to actual accuracy, not model output

De Visser et al. (2024) and our own calibration data both confirm that humans often
have mis-calibrated trust in automation: they are either too trusting (leading to
over-reliance and complacency) or do not trust enough (leading to skepticism and
disuse). Our ECE=0.67 finding on Qwen2.5-7B means the model says 0.95 but is right
only about 65% of the time. Displaying raw confidence scores would actively mislead
the operator.

Research on confidence visualization (CHI 2025; Frontiers in Computer Science, 2025)
shows that the majority of participants trust AI systems more when uncertainty is
shown, and that communicating AI confidence calibration level significantly reduces
participants' trust in uncalibrated AI. The solution is not to hide confidence but to
display *calibrated* confidence using verbal labels.

**Application:** Map raw model confidence through the calibration curve before display.
Show HIGH / MEDIUM / LOW labels with color coding, not decimal scores. The thresholds
correspond to calibrated accuracy, not raw output. See Section 5 for the full scheme.

### Principle 4: The operator controls the tempo — adaptable, not adaptive

Calhoun (2022) distinguishes between adaptive automation (system decides when to
change autonomy level) and adaptable automation (operator decides). For military C2,
adaptable automation is preferred because the operator retains invocation authority.
The NASA adaptive automation research confirms that at lower levels of automation, the
human has more opportunity to interact with the system, providing a better basis for
building trust and maintaining situation awareness.

**Application:** The operator can pause all writes (F12), override any extraction
(WRONG button), filter the view, and switch to critical-only mode. The system never
changes its behavior without the operator's initiation. The BM chooses to trust more
or less — the system does not choose for them.

### Principle 5: Flow never stops, quality degrades gracefully

This is our core equifinality principle (docs/DESIGN_PHILOSOPHY.md). Breaking Defense
(2025) reports that effective military AI design "simplifies the system to keep only
the most important decisions in the loop and moves oversight tasks to a more automated
level." Our tiered write authority (AUTO / FLAGGED / HUMAN) implements exactly this.

**Application:** At high workload, the BM can ignore FLAGGED updates and trust that
AUTO writes are flowing. At low workload, they can review FLAGGED items and correct
errors. The system writes something for every message — the BM decides how much
attention to give it.

### Principle 6: Every action has immediate, visible feedback

Aviation CRM research shows that trust is built through predictable, immediate feedback
(Skybrary, "Crew Resource Management"; NASA CRM-A, 2018). The "Sunday-Monday divide"
problem (Breaking Defense, 2025) highlights that warfighters experience more capable
consumer tools during personal time than military systems provide operationally.

**Application:** Click WRONG — the row is immediately struck through with an
OVERRIDDEN badge. Click pause — the button turns red instantly with queue depth
visible. No spinners, no "processing..." messages, no confirmation dialogs for
emergency actions. The system responds as fast as a light switch.

### Principle 7: The centaur division of labor is explicit

The centaur model (Kasparov, 2005; Harvard Data Science Review, 2024) shows that
human-AI teams outperform either alone when each does what they are best at.
Mia's "Heterogeneous CCA Human-Machine Teaming" report frames this as the core
design challenge: the human provides strategic judgment and context sensitivity while
the AI provides speed and scale.

**Application:** The AI reads 200 messages per minute across 15 channels and extracts
structured data. The human reads 3 flagged items per minute and decides whether they
are correct. The AI never makes a decision that cannot be reversed. The human never
has to read raw chat to maintain SA. This division is the architecture, not a feature.

---

## 2. What the Operator Sees

### Layout

The dashboard is a single dark-background page (dark themes reduce eye strain during
extended operations, per MIL-STD-1472H display guidelines). The layout has four zones:

```
+------------------------------------------------------------------+
| [1] STATUS BAR                                    [WRITES ACTIVE] |
|     Updates: 847  Entities: 23  Overrides: 2          [F12]      |
+------------------------------------------------------------------+
| [2] CRITICAL ALERTS (only when present)                          |
|  >> HIGH-RISK: SAM launch reported by WF_BMA_03 on #wf_fire     |
|     Confidence: LOW -- NEEDS HUMAN REVIEW                        |
+------------------------------------------------------------------+
| [3] RECENT UPDATES TABLE                                         |
|  Time  | Chan | Speaker | Type   | Conf | Entities | Msg | Acts |
|  ------+------+---------+--------+------+----------+-----+------+
|  14:32 | #wf  | BMA_03  | threat | HIGH | SAM-6    | ... | [W]  |
|  14:31 | #cr  | Tank_01 | fuel   | MED  | CRUSH2   | ... | [W]  |
|  14:30 | #wf  | BMA_01  | status | HIGH | F-15E #3 | ... |      |
+------------------------------------------------------------------+
| [4] TRACKED ENTITIES TABLE                                       |
|  Callsign | Type | Affiliation | Status | Position | Last Update|
+------------------------------------------------------------------+
```

**Zone 1 — Status Bar (always visible, top of page):**
- System health indicator: a single word (HEALTHY / DEGRADED / OFFLINE) with
  color coding (green / yellow / red)
- Running counts: total updates, tracked entities, corrections, overrides
- Pause-writes button: top-right, large, color-coded (green = active, red = paused),
  with F12 shortcut hint. Queue depth visible only when paused.

**Zone 2 — Critical Alerts (conditional, appears only when triggered):**
- Shows HUMAN-tier updates that require operator review
- Shows high-risk type extractions (weapons, CSAR, fire_mission, cyber_ew) regardless
  of confidence
- Highlighted with a distinct background color (dark red/amber border)
- Auto-dismisses when the operator acknowledges or overrides

**Zone 3 — Recent Updates Table:**
- Scrolling table of the last 100 CoPUpdates
- Columns: Timestamp, Channel, Speaker, Type, Confidence (as label), Entities,
  Message snippet, Actions
- Each row has a [correct] button and a [WRONG] button
- Overridden rows are struck through with an OVERRIDDEN badge
- FLAGGED updates have a yellow left border; HUMAN updates have a red left border

**Zone 4 — Tracked Entities Table:**
- Current state of all tracked entities (callsigns, tracks)
- Columns: Callsign/Track, Platform Type, Affiliation (color-coded), Status
  (color-coded), Position, Last Updated
- This is the persistent "picture" — what the system believes about the world right now

### Information Hierarchy

At a glance (1 second, across the room):
- Is the system running? (status bar color)
- Are writes flowing or paused? (pause button color)
- Are there critical alerts? (Zone 2 presence/absence)

At a look (5 seconds, from the console):
- What just happened? (top rows of updates table)
- How confident is the system? (HIGH/MEDIUM/LOW labels with color)
- What entities are tracked? (entities table)

On investigation (30 seconds, deliberate focus):
- What exactly was extracted? (expand a row for full details)
- What was the original message? (hover for full text)
- Should I correct this? (click correct, fill modal)

---

## 3. What the Operator Does NOT See

The following are explicitly hidden from the default view. They exist in the system
and are available via the API or post-event analysis tools, but they never appear on
the operator dashboard during operations.

- **Model names or versions.** The BM does not need to know "qwen2.5:7b" vs
  "llama3.1:70b". The system is one entity to the operator.

- **Raw JSON.** All structured data is rendered as human-readable text. No curly
  braces, no field names, no arrays.

- **Confidence as decimal numbers.** No "0.73" or "0.91". Only HIGH / MEDIUM / LOW.
  The decimal is available in the API response and in the database, but the operator
  sees only the calibrated verbal label.

- **Extraction method labels.** No "llm", "regex", "passthrough" in the foreground.
  The method tag exists in the current dashboard for developer use; in the operator
  view it is hidden by default and available only via a developer toggle or post-event
  analysis.

- **Internal reasoning or chain-of-thought.** The "why" behind an extraction is SAT
  Level 2 information. It is available on demand (click to expand) but never shown
  by default.

- **A chatbot interface.** This system is not a conversational AI. The operator does
  not type questions to it. It is a display and control surface, like an instrument
  panel.

- **Animated transitions or progress indicators.** No spinners, no loading bars, no
  fade-in effects. Information appears or it does not. Animations consume attention
  and add latency to the operator's OODA loop.

- **System architecture diagrams or pipeline status.** The supervisor, channel agents,
  fusion agent, and backend pool are invisible. "HEALTHY" or "DEGRADED" is the only
  signal about system internals.

---

## 4. Operator Actions

Five categories of operator interaction, ordered from most urgent to least.

### 4.1 Emergency: Pause / Resume Writes (built, issue #56)

- **Control:** Large toggle button, top-right corner, always visible
- **Shortcut:** F12
- **States:** Green "WRITES ACTIVE" / Red "WRITES PAUSED" with pulsing border
- **Feedback:** Immediate color change, queue depth counter appears
- **Semantics:** Pausing does not stop extraction. It stops writes to the CoP database.
  Extracted updates queue locally and are written when resumed. No data is lost
  (Pattern B).
- **Use case:** "Something is very wrong — the system is writing garbage. Stop writes
  while I figure out what happened."

### 4.2 Correction: Override a Specific Update (built, issue #59)

- **Control:** "WRONG" button on each update row
- **Workflow:** Click WRONG -> optional reason text -> Enter to submit
- **Feedback:** Row immediately struck through, OVERRIDDEN badge appears
- **Semantics:** The override is logged in the audit trail. The original extraction is
  preserved (Pattern B). The override reason, if provided, is stored for post-event
  analysis. The override count increments in the status bar.
- **Use case:** "That extraction is wrong — CRUSHER 2 is not Winchester, they said
  BINGO."

### 4.3 Refinement: Correct an Extraction (built)

- **Control:** "correct" button on each update row -> modal dialog
- **Workflow:** Opens modal with corrected type dropdown, rejection checkbox, notes
  field, corrector name
- **Feedback:** Correction recorded message, row refreshes
- **Semantics:** Corrections feed back into the labeling pipeline for future model
  improvement. They are not overrides — the original extraction remains in the CoP
  but the correction is attached.
- **Use case:** "The extraction type is wrong — this is a fuel report, not a status
  change — but the entities are correct."

### 4.4 Filtering: Focus the View (proposed, not yet built)

- **Controls:** Filter dropdowns or toggles for:
  - Channel (show only #wf_fire, #crusher, etc.)
  - Update type (show only threat, weapons, CSAR)
  - Confidence tier (show only FLAGGED and HUMAN)
  - Speaker (show only WF_BMA_03)
- **Shortcut candidates:** F1-F4 for preset filter profiles (e.g., F1 = critical only,
  F2 = my channels, F3 = all, F4 = flagged only)
- **Use case:** "I only care about the fire channel right now. Show me that and hide
  everything else."

### 4.5 Acknowledgment: Clear Critical Alerts (proposed, not yet built)

- **Control:** "ACK" button on each critical alert in Zone 2
- **Workflow:** Click ACK -> alert dismissed from Zone 2 (remains in table below)
- **Feedback:** Alert slides out of Zone 2
- **Semantics:** Acknowledgment is logged. Unacknowledged alerts older than a
  configurable threshold (e.g., 60 seconds) could trigger an audible tone.
- **Use case:** "I saw the SAM report. I'm handling it through voice. Clear the alert."

### 4.6 Search: Find a Recent Update (proposed, not yet built)

- **Control:** Search box at the top of the updates table
- **Searches:** Callsign, track number, speaker, free text in source message
- **Use case:** "What did CRUSHER 2 report in the last 10 minutes?"

---

## 5. Confidence Display Design

### Why Decimals Are Wrong for This Context

Our calibration data (issue #30, data/calibration/qwen2.5_7b-8k.json) shows that the
Qwen2.5-7B model has an Expected Calibration Error of 0.67. This means when the model
reports confidence of 0.95, actual accuracy is approximately 65%. Displaying "0.95" to
an operator would create systematically mis-calibrated trust (de Visser et al., 2024).

Even with a well-calibrated model, decimal confidence scores are inappropriate for this
context because:

1. **Precision implies accuracy.** Displaying "0.73" suggests the system can
   distinguish between 73% and 74% confidence. It cannot. The difference is noise.
2. **Cognitive load.** Processing a number requires more cognitive resources than
   processing a category. At hour 36, the BM cannot spare those resources.
3. **False sense of precision undermines trust.** When the operator sees "0.91"
   and the extraction is wrong, they lose trust in the entire system. When they see
   "HIGH" and the extraction is wrong, they understand that HIGH does not mean
   certain.

### What the Research Says

CHI 2025 research ("As Confidence Aligns") found that high AI confidence encourages
participants to rely on AI decisions more, which is dangerous when confidence is
uncalibrated. Frontiers in Computer Science (2025) found that showing uncertainty
information increased user trust overall, but only when the uncertainty display was
meaningful and calibrated.

The AI UX Design Guide recommends a three-tier scheme with color coding:
- Green for high confidence (reliable enough to act on)
- Yellow/amber for medium confidence (verify before acting)
- Red for low confidence (requires human judgment)

### Proposed Scheme

The display maps *calibrated* confidence (after applying the calibration curve) to
three tiers:

| Display Label | Color  | Calibrated Accuracy | Raw Model Score (7B) | Meaning                         |
|---------------|--------|--------------------|-----------------------|---------------------------------|
| HIGH          | Green  | >= 80% accurate    | Varies by model       | System is confident, usually right |
| MEDIUM        | Yellow | 50-80% accurate    | Varies by model       | System is uncertain, verify     |
| LOW           | Red    | < 50% accurate     | Varies by model       | System is guessing, human decides |

The mapping from raw model score to calibrated accuracy is per-model and stored in
`data/calibration/`. For Qwen2.5-7B with 8k context, the current mapping is:

- Raw >= 0.95 -> calibrated ~65% -> displayed as MEDIUM (not HIGH, because the
  model is overconfident)
- This means that with the current 7B model, very few extractions will show as HIGH.
  This is correct. The system should not display HIGH confidence when it is not
  warranted.

As we calibrate larger models (issue #30) and improve calibration (issue #43,
confidence-aware cascading), the thresholds will shift. The display scheme stays
the same; only the mapping changes.

### Confidence Display Implementation

Each update row shows the confidence label as a colored badge:
- `HIGH` in green (#3fb950 on dark background)
- `MEDIUM` in yellow (#d29922 on dark background)
- `LOW` in red (#f85149 on dark background)

Additional design details:
- The badge is the same font size as other table content — not larger or smaller
- No progress bars, pie charts, or analog indicators. Just the word and the color.
- Hovering over the badge could show a tooltip: "Calibrated accuracy: ~65%.
  The system gets this type of extraction right about 2 out of 3 times."
  (This is SAT Level 2 information — available on demand, not default.)
- Color is supplemented by the text label for accessibility (color-blind operators)

### Write Authority Mapping

The confidence tiers map to write authority:

| Confidence Tier | Write Authority | What Happens                              |
|-----------------|-----------------|-------------------------------------------|
| HIGH            | AUTO            | Written to CoP immediately                 |
| MEDIUM          | FLAGGED         | Written with review flag, yellow border    |
| LOW             | HUMAN           | Queued for operator review, red border     |

High-risk types (weapons, CSAR, fire_mission, cyber_ew) are always HUMAN regardless
of confidence tier. This is a safety constraint, not a confidence judgment.

---

## 6. Fatigue and Stress Considerations

### What Changes at Hour 36

The research is clear: fatigued operators need a fundamentally different interface
presentation. The underlying data and functionality remain the same; the presentation
adapts.

**Visual changes:**
- Larger base font size (14px minimum, up from 12px)
- Higher contrast ratios (WCAG AAA: 7:1 minimum)
- Reduced information density: fewer columns visible by default, wider row spacing
- No thin fonts or light gray text that disappears in ambient light

**Alert management:**
- Multi-stage alerting: ambient awareness (color change) -> focused attention
  (banner in Zone 2) -> immediate action (audible tone for unacknowledged critical
  alerts after timeout) (per Ambush Systems alert framework)
- Audible alerts for HUMAN-tier updates only. No sounds for AUTO or FLAGGED.
- Alert fatigue prevention: if more than N critical alerts arrive in T seconds,
  batch them into a single "multiple critical updates" banner rather than
  N individual alerts

**Reduced clutter:**
- The "quiet mode" concept: operator presses a key (proposed: F5) to reduce the
  dashboard to critical-only view. Only Zone 1 (status bar) and Zone 2 (critical
  alerts) are visible. Zone 3 and Zone 4 collapse. The BM can focus entirely on
  voice comms and glance at the screen only when a critical alert appears.
- No animations. The current pause button pulsing border animation should be
  reconsidered for the production interface — a static high-contrast red may be
  less distracting while still clearly communicating state.

**Interruption handling:**
- The BM will look away from the dashboard for minutes at a time. When they look
  back, they need instant SA recovery (Endsley SA Principle 6, as cited in the
  current dashboard.py docstring).
- The status bar provides this: one glance tells them whether writes are flowing,
  how many updates have occurred, and whether there are critical alerts.
- The updates table sorts newest-first so the BM immediately sees what happened
  since they last looked.

### The "I Stepped Away" Problem

At DASH/MASH events, battle managers physically leave their console (bathroom, food,
shift change). When they return, they need to rapidly reconstruct what happened.

Proposed features:
- **"Since I last looked" marker:** A subtle horizontal line in the updates table
  marking the last update that was on screen when the page was last actively viewed
  (tracked via page visibility API). Updates above the line are new since the BM
  stepped away.
- **Summary count in status bar:** "12 new updates since last view" that clears when
  the BM scrolls through them.

---

## 7. Post-Event Analysis View

After the exercise, the operator and research team need a different view. The
post-event view prioritizes completeness and auditability over speed and simplicity.

### Override Audit Log

The `scripts/analyze_overrides.py` tool (issue #59) produces:
- Total overrides and override rate
- Overrides by update type (which extraction types did the operator reject most?)
- Overrides by channel and speaker
- Override reasons (free text, for qualitative analysis)
- Time distribution of overrides (did they cluster during high-tempo periods?)

### Per-Speaker Accuracy

From the correction and override data:
- Which speakers produced the most overridden extractions?
- Which speakers were the system most accurate for? (Speaks to RQ1: online user
  modeling)
- Speaker model effectiveness: did accuracy improve over the session for speakers
  with more messages? (This is the core RQ1 metric)

### Per-Channel Extraction Rate

- Messages processed per channel
- Extraction success rate per channel
- Override rate per channel
- STT channels vs. typed chat channels (expected: STT has lower accuracy due to
  transcription errors)

### System Reliability Report

- Backend usage distribution (which backends handled which fraction of messages?)
- Circuit breaker trips and recovery times
- Degradation events (when did the system fall back from LLM to regex to passthrough?)
- Mean extraction latency by backend
- Overall system uptime and availability

### Calibration Analysis

- Predicted vs. actual accuracy curves (reliability diagrams)
- ECE by update type (some types may be better calibrated than others)
- Confidence distribution (is the model using the full confidence range or clustering
  at extremes?)
- This data feeds back into issue #30 (calibrate confidence) for model improvement.

---

## References and Sources

### ARL Trust Calibration
- Chen, J.Y.C. et al. (2014). Situation Awareness-Based Agent Transparency. ARL Technical Report. https://apps.dtic.mil/sti/pdfs/ADA600351.pdf
- Chen, J.Y.C. & Proctor, R.W. (2017). Situation awareness-based agent transparency and human-autonomy teaming effectiveness. Theoretical Issues in Ergonomics Science, 19(3). https://www.tandfonline.com/doi/abs/10.1080/1463922X.2017.1315750
- ARL Center for Agent-Soldier Teaming: https://www.arl.army.mil/cast/

### Trust in Automation
- de Visser, E.J. et al. (2024). Calibrating workers' trust in intelligent automated systems. Patterns. https://www.cell.com/patterns/fulltext/S2666-3899(24)00187-9
- de Visser, E.J. — Measurement of Trust in Automation: A Narrative Review. Frontiers in Psychology (2021). https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2021.604977/full

### Confidence Visualization
- AI UX Design Guide — Confidence Visualization pattern: https://www.aiuxdesign.guide/patterns/confidence-visualization
- CHI 2025 — As Confidence Aligns: Understanding the Effect of AI Confidence on Human Self-confidence. https://dl.acm.org/doi/10.1145/3706598.3713336
- Frontiers in Computer Science (2025) — Trusting AI: does uncertainty visualization affect decision-making? https://www.frontiersin.org/journals/computer-science/articles/10.3389/fcomp.2025.1464348/full
- Designing for Confidence: The Impact of Visualizing AI Decisions (PMC). https://pmc.ncbi.nlm.nih.gov/articles/PMC9263374/
- Miscalibrated AI Confidence effects on trust and reliance. https://arxiv.org/html/2402.07632v4

### Cognitive Load and Combat Systems
- Ambush Systems — Cognitive Overload: The Hidden Killer in Combat Systems. https://www.getambush.com/article/cognitive-load-optimization-in-combat-systems/
- NASA-STD-3001 Technical Brief: Cognitive Workload. https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-032-cognitive-workload.pdf

### Adaptive Automation
- Calhoun, G. (2022). Adaptable (Not Adaptive) Automation: Forefront of Human-Automation Teaming. Human Factors. https://journals.sagepub.com/doi/full/10.1177/00187208211037457
- NASA/TM-2006 — Adaptive and Adaptable Automation Design. https://ntrs.nasa.gov/api/citations/20060053373/downloads/20060053373.pdf
- USAARL (2025) — Optimizing Adaptive Automation in Aviation. https://usaarl.health.mil/assets/docs/techReports/2025-09.pdf

### Military C2 and AI Decision Support
- SCSP (2024) — Reimagining Military C2 in the Age of AI. https://www.scsp.ai/wp-content/uploads/2024/12/DPS-Reimagining-Military-C2-in-the-Age-of-AI.pdf
- Breaking Defense (2025) — How automation and AI can help warfighters in high-stress situations. https://breakingdefense.com/2025/01/how-automation-and-artificial-intelligence-can-help-warfighters-in-high-stress-situations/
- RAND — Improving C2 and Situational Awareness for Operations in the Information Environment. https://www.rand.org/pubs/research_reports/RR2489.html
- Air Force Doctrine Note 25-1: Artificial Intelligence (2025). https://www.doctrine.af.mil/Portals/61/documents/AFDN_25-1/AFDN%2025-1%20Artificial%20Intelligence.pdf

### Aviation CRM
- NASA (2018) — Crew Resource Management for Automated Teammates (CRM-A). https://ntrs.nasa.gov/api/citations/20180004774/downloads/20180004774.pdf
- Skybrary — Crew Resource Management. https://skybrary.aero/articles/crew-resource-management-crm

### Centaur Model
- Harvard Data Science Review (2024) — Effective Generative AI: The Human-Algorithm Centaur. https://hdsr.mitpress.mit.edu/pub/3rvlzjtw
- Modeling the Centaur: Human-Machine Synergy in Sequential Decision Making. https://arxiv.org/html/2412.18593v1

### DARPA Programs
- DARPA KAIROS — Knowledge-directed AI Reasoning Over Schemas. https://www.darpa.mil/research/programs/knowledge-directed-artificial-intelligence-reasoning-over-schemas
- DARPA EMHAT — Exploratory Models of Human-AI Teams. https://www.darpa.mil/research/programs/exploratory-models-of-human-ai-teams

### Internal Team Documents (Google Drive)
- Kollia, M. — "Self-Synchronization in Human-Machine Teams" (2025). Research report on self-synchronization principles applied to HMT, including implications for trust-building through behavioral synchrony.
- Kollia, M. — "Heterogeneous CCA Human-Machine Teaming" (2025). Analysis of trust calibration, cognitive load management, and shared situational awareness for heterogeneous human-machine teams.
- Kollia, M. — "Human-Machine Adaptive Capacity Research" (2025). Synthesis of adaptive capacity research applied to human-AI collaboration, including trust calibration as a core component of adaptive capacity.
- Cooke et al. (2023) — "From Teams to Teamness: Future Directions in the Science of Team Cognition" (shared by Kollia).
- NAS — "Human-AI Teaming Report" (2024, shared by Clouse).

---

## Open Questions for Mia

1. **Self-synchronization as a trust mechanism:** Your "Self-Synchronization in
   Human-Machine Teams" report argues that behavioral and rhythmic synchrony between
   human and machine can build trust through an embodied pathway, complementing
   cognitive trust factors. How does this apply to a dashboard that is primarily
   visual? Could the refresh rate, update rhythm, or information pacing serve as a
   "coupling mechanism" in the Huygens sense?

2. **Adaptive capacity under stress:** Your "Human-Machine Adaptive Capacity Research"
   report defines AC as the ability to "effectively adjust cognitive processes,
   strategies, behaviors, and expectations to anticipate, respond to, and learn from
   the dynamic capabilities, limitations, states, and actions of machine/AI partners."
   How do we measure AC during a MASH event? Is NASA-TLX sufficient, or do we need
   a more specific instrument?

3. **The phantom signature problem:** Your earlier work flagged the risk of
   "confidently-wrong AI outputs degrading human decisions." Our ECE=0.67 finding
   confirms this is a real risk with the current 7B model. Is the verbal label scheme
   (HIGH/MEDIUM/LOW) sufficient mitigation, or do we need additional safeguards?

4. **Endsley vs. emergent SA:** The current dashboard.py already cites Endsley's SA
   principles for the pause button design. The team has specific disagreements with
   Endsley's model — we prefer emergent over architected approaches. Where should we
   draw the line? Which Endsley principles are still useful for a physical display,
   and where does the emergent/FACS perspective diverge?

5. **Cap Rogers connection:** How does Cap Rogers' work bridge between the pragmatic
   HF recommendations in this document and the grander consciousness/emergence
   interests that motivate the team's research direction?

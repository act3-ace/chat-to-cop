# Joint Provenance Contract: DELTRON + chat-to-cop

**Closes #57.** Written 2026-04-10 from the Cadre of Critics review (AI Expert critique CR-2, see `COMPARISON_DELTRON_2026-04-09.md`).

> *"You're about to compose two AI systems and you have no joint provenance chain. When DELTRON's tier signal influences chat-to-cop's extraction confidence, or when chat-to-cop's output influences a downstream CoP entry, the audit trail has to span both systems."*

This document defines the provenance fields that a joint CoP update must carry when both DELTRON and chat-to-cop contribute to the final output. It is our half of the contract — the wire format and DELTRON's fields require HLT agreement.

---

## Scope

This contract applies when DELTRON's tier output is consumed by chat-to-cop as an upstream signal (per #45 rewrite). It does NOT apply when either system runs standalone — each has its own provenance chain for independent operation.

The contract is designed so that **every CoP update is fully traceable** to:
- The IRC message that triggered it
- The DELTRON tier decision (if present)
- The chat-to-cop extraction decision
- The fusion decision
- The write authority decision
- Any operator override

This supports DoD AI Ethics Principle 3 (Traceable) and the AI T&E guidebook requirements for auditable AI decision chains.

---

## The seven provenance fields

Each CoPUpdate written to the CoP database must carry these seven fields. Fields 1-2 are owned by DELTRON; fields 3-7 are owned by chat-to-cop.

### Field 1: DELTRON tier output

| Attribute | Value |
|---|---|
| **Field name** | `deltron_tier` |
| **Type** | integer (0-4) or null |
| **Owner** | DELTRON (HLT team) |
| **Semantics** | 0=NOISE, 1=LOW, 2=COORDINATION, 3=HIGH, 4=CRITICAL. Null = DELTRON was not available. |
| **Propagation** | Received from DELTRON via the wire format (see below), stored in the CoPUpdate metadata dict as `metadata["deltron_tier"]` |
| **Retention** | Permanent — never overwritten, even if the update is later corrected or overridden |

### Field 2: DELTRON model + version

| Attribute | Value |
|---|---|
| **Field name** | `deltron_model` |
| **Type** | string or null |
| **Owner** | DELTRON (HLT team) |
| **Semantics** | The model that produced the tier classification (e.g., `gpt-oss-20b`). Null = DELTRON was not available. |
| **Propagation** | Received from DELTRON alongside the tier signal |
| **Retention** | Permanent |

### Field 3: chat-to-cop model + version

| Attribute | Value |
|---|---|
| **Field name** | `model_name` |
| **Type** | string |
| **Owner** | chat-to-cop |
| **Semantics** | The model that produced the structured extraction (e.g., `qwen2.5:14b-8k`). Already exists on CoPUpdate. |
| **Propagation** | Set by channel_agent.py post-extraction |
| **Retention** | Permanent |

### Field 4: Extraction prompt hash

| Attribute | Value |
|---|---|
| **Field name** | `prompt_hash` |
| **Type** | string (SHA-256 hex, first 16 chars) |
| **Owner** | chat-to-cop |
| **Semantics** | Pins the exact system prompt template used for this extraction. Already exists on CoPUpdate. |
| **Propagation** | Set by channel_agent.py post-extraction |
| **Retention** | Permanent |

### Field 5: Fusion decision

| Attribute | Value |
|---|---|
| **Field name** | `fusion_action` |
| **Type** | string enum: `pass`, `corroborated`, `contradicted`, `deduplicated`, `adversarial_flagged` |
| **Owner** | chat-to-cop (fusion_agent.py) |
| **Semantics** | What the fusion agent did with this update. `pass` = no cross-channel interaction. |
| **Propagation** | Currently implicit in logs; needs to be added as an explicit field on CoPUpdate |
| **Retention** | Permanent |
| **Status** | **NEW FIELD — not yet implemented.** This contract defines it; implementation is a follow-on task. |

### Field 6: Write authority decision

| Attribute | Value |
|---|---|
| **Field name** | `write_authority` |
| **Type** | string enum: `auto`, `flagged`, `human` |
| **Owner** | chat-to-cop (cop_writer.py) |
| **Semantics** | The tiered write authority decision. Already exists on CoPRecord. |
| **Propagation** | Set by `classify_write_authority()` using calibrated confidence and update type |
| **Retention** | Permanent |

### Field 7: Operator override

| Attribute | Value |
|---|---|
| **Field name** | `override` |
| **Type** | object or null |
| **Owner** | chat-to-cop (store.py overrides table) |
| **Semantics** | If an operator marked this update as WRONG: `{operator_id, reason, overridden_at}`. Null = not overridden. Already implemented in #59. |
| **Propagation** | Set via `POST /updates/{id}/override` endpoint |
| **Retention** | Permanent. Original update is preserved (Pattern B). |

---

## Wire format: DELTRON to chat-to-cop

This is the interface between the two systems. It must be agreed with HLT before implementation.

### Proposed: HTTP request per message

The simplest option. chat-to-cop makes a synchronous HTTP request to DELTRON for each message before extraction.

```
GET /classify?message=<url-encoded-text>

Response:
{
    "tier": 3,
    "model": "gpt-oss-20b",
    "confidence": 0.82,
    "timestamp": "2026-05-15T14:03:36Z"
}
```

**Pros:** Simple, stateless, easy to test, easy to mock.
**Cons:** Adds latency per message (~1s based on DELTRON's 765ms 20B benchmark). Sequential.

### Alternative: Shared message bus

Both systems subscribe to the IRC message stream and publish to a shared topic. DELTRON publishes tier classifications; chat-to-cop consumes them.

```
Topic: irc.messages     → both subscribe
Topic: deltron.tiers    → DELTRON publishes, chat-to-cop consumes
Topic: cop.updates      → chat-to-cop publishes, CoP database consumes
```

**Pros:** Async, decoupled, scales to multiple consumers.
**Cons:** Requires RabbitMQ or similar infrastructure at MASH.

### Alternative: DELTRON as library

chat-to-cop imports DELTRON's classifier as a Python module and calls it in-process.

**Pros:** No network latency, no infrastructure.
**Cons:** Tight coupling, shared dependency tree, deployment complexity.

### Recommendation

**Start with HTTP** (Option 1). It's the simplest to implement, test, and mock. The `DeltronTierProvider` protocol in #45 is designed so the wire format is swappable — if we later move to a message bus or in-process call, only the provider implementation changes, not the rest of the pipeline.

For MASH, even the HTTP latency is acceptable: at 765ms per classification + ~7s per extraction, the total is ~8s per message, still well within the per-channel headroom.

---

## What we explicitly do NOT do

1. **No feedback loop from chat-to-cop to DELTRON.** DELTRON classifies independently. It does not see our extraction output. If we ever add a feedback loop (e.g., chat-to-cop tells DELTRON "your Tier 1 was actually Tier 4"), that requires a separate contract with safety analysis.

2. **No shared model state.** Each system manages its own models, weights, and calibration independently. There is no shared model registry.

3. **No shared label pipeline.** Each system uses its own silver labels and calibration data. Cross-validation of labels is a research activity, not an operational coupling.

4. **No DELTRON dependency for chat-to-cop to function.** If DELTRON is unavailable, chat-to-cop processes every message normally with `deltron_tier = null`. The tier signal is *additive*, never blocking.

---

## Implementation plan

| Step | What | Who | When |
|---|---|---|---|
| 1 | Agree on wire format with HLT (HTTP vs bus vs library) | Hamilton + Jeremy | Next HLT sync |
| 2 | Implement `DeltronTierProvider` protocol with mock provider (#45 Phase 1) | chat-to-cop team | This sprint |
| 3 | Add `fusion_action` field to CoPUpdate (Field 5 above) | chat-to-cop team | This sprint |
| 4 | Store `deltron_tier` and `deltron_model` in CoPUpdate metadata | chat-to-cop team | With #45 |
| 5 | DELTRON implements the HTTP endpoint | HLT team | TBD |
| 6 | Integration test with real DELTRON | Both teams | Pre-MASH |
| 7 | Joint adversarial test (CR-8 Gap 2) | Both teams | Pre-MASH |

Steps 1-4 are on our side and unblocked. Steps 5-7 require HLT cooperation and are stretch goals for MASH.

---

## Audit trail example

Here's what a fully-provenanced CoP update looks like when both systems contribute:

```json
{
    "update_type": "threat",
    "confidence": 0.88,
    "extraction_method": "llm",
    "entities": [
        {
            "platform_type": "SA-21",
            "affiliation": "HOSTILE",
            "bearing": 316.0,
            "range_nm": 398.0,
            "metadata": {"status": "active"}
        }
    ],
    "source_channel": "#isr_reports",
    "source_speaker": "AOC_SIDO",
    "source_message": "tacrep e10-4, 4th-gen sam active, cigar 316/398, jtn TM677",
    "timestamp": "2025-09-23T14:20:00+00:00",
    "model_name": "qwen2.5:14b-8k",
    "prompt_hash": "a3f2b184...",
    "reasoning": "TACREP reporting an active SAM threat with track number and location",

    "metadata": {
        "deltron_tier": "3",
        "deltron_model": "gpt-oss-20b"
    },
    "fusion_action": "corroborated",
    "write_authority": "flagged",
    "override": null
}
```

Every field traces back to a decision point. A post-event auditor can reconstruct: DELTRON classified this as Tier 3 (HIGH), chat-to-cop extracted a THREAT with SA-21 entity, fusion corroborated with another channel, write authority flagged it (confidence 0.88 < 0.95 AUTO threshold), and no operator override occurred.

---

## NIST AI RMF alignment

This contract addresses:

- **GOVERN 1.5:** AI actors' roles and responsibilities are documented (Field 1-2 = DELTRON, Field 3-7 = chat-to-cop)
- **MAP 3.4:** Measurable performance objectives across the AI lifecycle include provenance
- **MEASURE 2.5:** AI system outputs are monitored for unintended consequences (the audit trail enables detection)
- **MANAGE 1.3:** Risk response plans include provenance-based root cause analysis

For the Generative AI Profile:
- **GAI 1:** Confabulation risk is traceable to the specific model and prompt hash
- **GAI 6:** Information integrity is maintained through the full provenance chain

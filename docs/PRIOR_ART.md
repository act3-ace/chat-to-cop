# Prior Art: Chat Parsing Approaches from DASH Events

Notes from a vendor presentation on their chat-parsing approach used during DASH events (Mar 2026). This validates several of our architectural decisions and highlights lessons learned.

## Their Approach

**Goal:** Provide a generic system for everyone to utilize for parsing exercise chat.

**Architecture:** Multi-method ensemble with 5 different NLP techniques to parse chats, removing biases from any single method. Every single message was passed through multiple models and the information categorized.

Each method tried to answer:
1. Is this a match-effector request?
2. Parse that to appropriate metadata.

**Methods used:** Simple keyword matching + small LLM models + large LLM models (all off-the-shelf). Started with each method at 20% weight, then methods that consistently performed better were weighted more heavily over time.

## Key Findings

### What worked
- **Semantic matching was more effective than pattern matching** for military jargon. Pattern matching (regex) failed on domain-specific language, but semantic approaches handled it well.
- Multi-model ensemble approach provided robustness — no single method dominated across all message types.

### What didn't work
- **Not enough data** was the biggest hurdle, not scale. They needed more training examples, not more compute.
- Pattern matching alone was insufficient for the jargon-heavy domain.

### Operational context
- Only used pure text chat (no voice/STT).
- The system would trigger COA generation through the HMI when it detected a request — extracting relevant data from messages, then matching that into a structured format that the system expected.
- Operationally, battle managers would need to trawl through hundreds of chat messages in a real scenario.

## Government Requirements (from Q&A)

The government team articulated what they actually need from a chat processing system:

1. **Structured data output is key** — "query the service for structured data, no unstructured data"
2. **Supplement data queries** — fill in missing fields in existing records
3. **Well-defined API request format** — structured output that matches the database schema
4. **Specific data processors:**
   - Associate track number to entity ID
   - Fuel level extraction
   - Weapon state extraction
   - Availability/operational status
5. **Broadcast missing data** — "We would be able to broadcast missing data in these fields"
6. **Inform vs trigger:** The system should be able to both inform (update CoP) and trigger actions (like generating a COA)

## Implications for Our Pipeline

These findings directly validate and refine our architecture:

| Their Finding | Our Design Response |
| ------------- | ------------------- |
| Pattern matching fails on jargon | Tier 1 (regex) handles only high-confidence patterns; Tier 2 (LLM) handles semantics |
| Semantic matching works better | LLM-based interpretation is the right core approach |
| Multi-method ensemble reduces bias | Our tiered architecture naturally provides this |
| Not enough data was the bottleneck | We have DASH 1-3 chat logs (140 files) for training/few-shot examples |
| Structured output is the #1 need | JSON schema enforcement via Ollama/llama.cpp grammar constraints |
| Specific field processors needed | Consider dedicated extractors for fuel, weapons, track ID (Tier 1 fast-path) |
| "Broadcast missing data in fields" | Design the CoP writer to flag which fields were populated vs. inferred |

### New ideas from this feedback

1. **Per-field processors in Tier 1:** Rather than just track number regex, add dedicated fast-path extractors for:
   - Fuel state ("F+40", "playtime 15 Mike")
   - Weapons count ("4xSM6 launched", "38 remaining")
   - Operational status ("RTB", "gadget bent", "out of ammo")

2. **Confidence broadcasting:** When we push an update to the CoP, include which fields were high-confidence (from regex/direct extraction) vs. inferred (from LLM interpretation). This lets downstream consumers decide their own trust threshold.

3. **Weighted ensemble for Tier 2:** If we run multiple small models or prompting strategies, weight their outputs dynamically based on observed accuracy — exactly as the prior team did.

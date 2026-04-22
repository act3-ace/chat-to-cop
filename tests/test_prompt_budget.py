"""CI guard: system prompt must stay under a pinned token-budget ceiling.

Issue #73 Part B. Each prompt-touching MR grows the token count; without a
CI check the growth accumulates silently. This test fails when the prompt
exceeds the ceiling, forcing a review conversation about what's being traded
for the growth.

Ceiling derivation (2026-04-22):
  Current build_system_prompt(glossary=DEFAULT_GLOSSARY) = 15,010 chars.
  At ~4 chars/token that's ~3,750 tokens. +15% headroom → 17,262 chars.
  Rounded to 17,500 for a clean number.
"""

from chat_to_cop.backend.openai_compat import DEFAULT_GLOSSARY, build_system_prompt

PROMPT_CHAR_CEILING = 17_500


def test_system_prompt_under_budget():
    prompt = build_system_prompt(glossary=DEFAULT_GLOSSARY)
    assert len(prompt) <= PROMPT_CHAR_CEILING, (
        f"System prompt is {len(prompt)} chars ({len(prompt) // 4} approx tokens), "
        f"exceeds ceiling of {PROMPT_CHAR_CEILING} chars. "
        "If the growth is intentional, raise PROMPT_CHAR_CEILING in this file "
        "and note the trade-off in the MR description."
    )


def test_prompt_without_glossary_is_compact():
    """The bare prompt (no glossary, no world state, no speakers) should be
    well under the ceiling. If it isn't, the few-shot examples have bloated."""
    prompt = build_system_prompt()
    assert len(prompt) < 10_000, f"Bare prompt is {len(prompt)} chars, unexpectedly large"

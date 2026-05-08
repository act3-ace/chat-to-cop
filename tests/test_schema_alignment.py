"""Regression test: production prompt must advertise every schema field and UpdateType.

**Why this test exists.**

On 2026-04-17 a sibling project (`agent-team`) evaluated a batch of LoRA adapters
against what appeared to be a -68.8pp regression vs. the 14B-base zero-shot
baseline. An alignment audit found the root cause was a drift between the
**training-time** prompt and the **evaluation-time** prompt: training produced
`{update_type, entities}` with the canonical 18-field `EntityUpdate` schema
while the evaluator was asking for `{update_type, entities, threats, taskings}`
with entity fields `{name, type}`. The evaluator was grading adapters on a
task they had never been trained for. 25–50pp of the measured regression
turned out to be measurement artifact. See
`agent-team/docs/eval_prompt_alignment_audit.md` for the full finding.

chat-to-cop has the same structural vulnerability. `CoPUpdate` and
`EntityUpdate` live in `src/chat_to_cop/models/cop_update.py`; the production
extraction prompt is assembled in `src/chat_to_cop/backend/openai_compat.py`
by `build_system_prompt()`. Nothing today enforces they stay byte-aligned — a
refactor on either side can silently desync them and drop production accuracy
without any user-visible error.

This test is the CI guard. If a new `UpdateType` variant is added, or an
`EntityUpdate` field is added, the prompt builder must reference it by name
inside the CANONICAL block (today rendered via iteration over `UpdateType` and
`EntityUpdate.model_fields`) or this test fails.

**Why the check is scoped to the CANONICAL block.**

The earlier implementation asserted `name in rendered_prompt` against the full
~15 KB prompt. Several field names (`metadata`, `heading`, `bearing`,
`callsign`) and enum values (`none`, `location`, `tasking`, `threat`,
`weapons`, `fuel`, `handover`, `sitrep`, `environmental`, `csar`) appear
naturally in the EXAMPLES and GLOSSARY sections. If a future refactor
truncated the CANONICAL line and dropped one of these names, the test would
still pass on the EXAMPLES match — silently losing the drift guard. The
scoped check (see `_canonical_block`) prevents that false negative.

This test runs in the default `pytest -k "not integration"` suite: no external
services, no LLM calls, pure string inspection.
"""

from __future__ import annotations

from functools import lru_cache

import pytest

from chat_to_cop.backend.openai_compat import DEFAULT_GLOSSARY, build_system_prompt
from chat_to_cop.models.cop_update import EntityUpdate, UpdateType


@lru_cache(maxsize=1)
def _rendered_prompt() -> str:
    """Render the production prompt as seen by the LLM.

    Cached so the parametrized tests do not rebuild the ~15 KB string
    each invocation. Uses the default glossary because that is what channel
    agents ship with; the world-state summary and speaker context are
    per-run and intentionally excluded from the schema-alignment contract.
    """
    return build_system_prompt(glossary=DEFAULT_GLOSSARY)


def _canonical_block(kind: str) -> str:
    """Extract one CANONICAL section from the rendered prompt.

    ``kind`` is either ``"update_type VALUES"`` or ``"EntityUpdate FIELDS"``.
    The block runs from the ``CANONICAL <kind>`` marker to the next paragraph
    boundary (blank line separator introduced by the ``"\\n\\n".join(parts)``
    pattern inside ``build_system_prompt``). Anything outside this block is
    untrusted for alignment purposes because it may legitimately use the
    same words in glossary or example text.
    """
    prompt = _rendered_prompt()
    marker = f"CANONICAL {kind}"
    start = prompt.find(marker)
    if start == -1:
        return ""
    end = prompt.find("\n\n", start)
    return prompt[start:] if end == -1 else prompt[start:end]


def _parse_canonical_names(block: str) -> set[str]:
    """Pull the comma-separated identifier list out of a CANONICAL block.

    Format is ``CANONICAL <kind> (human-readable note): a, b, c.`` — take
    everything after the final colon, strip the trailing period, split on
    commas, and strip whitespace. Returns a set so callers can do equality
    checks without caring about order.
    """
    if ":" not in block:
        return set()
    payload = block.rsplit(":", 1)[1].strip().rstrip(".")
    return {name.strip() for name in payload.split(",") if name.strip()}


# ---------------------------------------------------------------------------
# Structural: the CANONICAL sections must be present at all.
# ---------------------------------------------------------------------------


def test_canonical_vocabulary_sections_present() -> None:
    """Both CANONICAL markers must exist in the rendered prompt.

    Catches the case where someone deletes or renames the rendering lines.
    If this fails, restore the two ``"CANONICAL ..."`` entries in
    ``build_system_prompt()`` — they are the primary drift guard and are
    rendered from the Pydantic source of truth.
    """
    prompt = _rendered_prompt()
    assert "CANONICAL update_type VALUES" in prompt, (
        "CANONICAL update_type VALUES section missing from prompt. "
        "This is the primary drift guard — see 2026-04-17 eval-prompt audit."
    )
    assert "CANONICAL EntityUpdate FIELDS" in prompt, (
        "CANONICAL EntityUpdate FIELDS section missing from prompt. "
        "This is the primary drift guard — see 2026-04-17 eval-prompt audit."
    )


# ---------------------------------------------------------------------------
# Forward drift: every schema element appears *inside* the CANONICAL block.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field_name", sorted(EntityUpdate.model_fields))
def test_entity_update_field_appears_in_canonical_block(field_name: str) -> None:
    """Every ``EntityUpdate`` field name must appear inside the CANONICAL
    EntityUpdate FIELDS block. Scoped substring check.

    If the schema gains a field, the CANONICAL rendering must emit it, or
    this test fails. The check is scoped so that fields whose names also
    appear in example text (``metadata``, ``heading``, ``bearing``,
    ``callsign``) cannot satisfy this assertion through EXAMPLES alone —
    drift is real drift, not a false-positive pass on unrelated text.
    """
    block = _canonical_block("EntityUpdate FIELDS")
    assert block, f"CANONICAL EntityUpdate FIELDS section missing — cannot verify field '{field_name}'."
    assert field_name in block, (
        f"EntityUpdate field '{field_name}' is not present in the CANONICAL "
        f"EntityUpdate FIELDS block. This is the 2026-04-17 eval-prompt drift "
        f"class of bug: the schema and the prompt have desynchronized. Either "
        f"add the field to the CANONICAL rendering in build_system_prompt(), "
        f"or remove it from EntityUpdate."
    )


@pytest.mark.parametrize("update_type", sorted(t.value for t in UpdateType))
def test_update_type_value_appears_in_canonical_block(update_type: str) -> None:
    """Every ``UpdateType`` enum value must appear inside the CANONICAL
    update_type VALUES block. Scoped substring check.

    Ensures adding a new UpdateType variant without updating the CANONICAL
    rendering is a CI failure. Scoped so that values like ``none``,
    ``location``, ``threat`` — which appear in example text for unrelated
    reasons — cannot satisfy the assertion through EXAMPLES alone.
    """
    block = _canonical_block("update_type VALUES")
    assert block, f"CANONICAL update_type VALUES section missing — cannot verify type '{update_type}'."
    assert update_type in block, (
        f"UpdateType value '{update_type}' is not present in the CANONICAL "
        f"update_type VALUES block. This is the 2026-04-17 eval-prompt drift "
        f"class of bug: the enum and the prompt have desynchronized. Either "
        f"add the value to the CANONICAL rendering in build_system_prompt(), "
        f"or remove it from UpdateType."
    )


# ---------------------------------------------------------------------------
# Reverse drift: the CANONICAL block must not name anything that is not in
# the schema. Catches stale entries if the rendering ever stops iterating
# over the Pydantic source of truth (e.g., someone hand-writes the list).
# ---------------------------------------------------------------------------


def test_canonical_fields_match_schema_exactly() -> None:
    """CANONICAL EntityUpdate FIELDS must list exactly ``EntityUpdate.model_fields``.

    Forward drift (schema gained a field, CANONICAL missed it) is covered by
    ``test_entity_update_field_appears_in_canonical_block``. This test covers
    reverse drift: CANONICAL names a field that no longer exists in the
    schema. Both drift directions matter — the goal is to prevent the
    operator and the LLM from ever believing the prompt and the schema say
    different things.
    """
    listed = _parse_canonical_names(_canonical_block("EntityUpdate FIELDS"))
    expected = set(EntityUpdate.model_fields)
    missing = expected - listed
    extra = listed - expected
    assert listed == expected, (
        "CANONICAL EntityUpdate FIELDS out of sync with EntityUpdate schema.\n"
        f"  Fields in schema but missing from CANONICAL: {sorted(missing) or 'none'}\n"
        f"  Fields in CANONICAL but not in schema: {sorted(extra) or 'none'}\n"
        "Fix: ensure build_system_prompt() renders the CANONICAL list from "
        "EntityUpdate.model_fields rather than hand-maintaining it."
    )


def test_canonical_types_match_schema_exactly() -> None:
    """CANONICAL update_type VALUES must list exactly ``{t.value for t in UpdateType}``.

    Reverse-drift counterpart to
    ``test_update_type_value_appears_in_canonical_block``.
    """
    listed = _parse_canonical_names(_canonical_block("update_type VALUES"))
    expected = {t.value for t in UpdateType}
    missing = expected - listed
    extra = listed - expected
    assert listed == expected, (
        "CANONICAL update_type VALUES out of sync with UpdateType enum.\n"
        f"  Values in enum but missing from CANONICAL: {sorted(missing) or 'none'}\n"
        f"  Values in CANONICAL but not in enum: {sorted(extra) or 'none'}\n"
        "Fix: ensure build_system_prompt() renders the CANONICAL list from "
        "UpdateType enum iteration rather than hand-maintaining it."
    )

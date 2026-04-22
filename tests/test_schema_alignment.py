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
(today via the `CANONICAL update_type` / `CANONICAL EntityUpdate FIELDS` lines
rendered from the Pydantic source of truth) or this test fails.

This test runs in the default `pytest -k "not integration"` suite: no external
services, no LLM calls, pure string inspection.
"""

from __future__ import annotations

import pytest

from chat_to_cop.backend.openai_compat import DEFAULT_GLOSSARY, build_system_prompt
from chat_to_cop.models.cop_update import EntityUpdate, UpdateType


def _rendered_prompt() -> str:
    """Render the production prompt as seen by the LLM.

    Use the default glossary because that is what channel agents ship with;
    the world-state summary and speaker context are per-run and intentionally
    excluded from the schema-alignment contract.
    """
    return build_system_prompt(glossary=DEFAULT_GLOSSARY)


@pytest.mark.parametrize("field_name", sorted(EntityUpdate.model_fields))
def test_entity_update_field_appears_in_prompt(field_name: str) -> None:
    """Every `EntityUpdate` field name must appear verbatim in the prompt.

    Byte-level substring check — no regex, no case folding. If the schema
    gains a field, the prompt must mention it by name.
    """
    prompt = _rendered_prompt()
    assert field_name in prompt, (
        f"EntityUpdate field '{field_name}' is not present in the production prompt. "
        f"This is the 2026-04-17 eval-prompt drift class of bug: the schema and the "
        f"prompt have desynchronized. Either add the field to the CANONICAL "
        f"EntityUpdate FIELDS line in build_system_prompt(), or remove it from "
        f"EntityUpdate."
    )


@pytest.mark.parametrize("update_type", sorted(t.value for t in UpdateType))
def test_update_type_value_appears_in_prompt(update_type: str) -> None:
    """Every `UpdateType` enum value must appear verbatim in the prompt.

    Ensures adding a new UpdateType variant without updating the prompt is
    a CI failure by design. Byte-level substring check.
    """
    prompt = _rendered_prompt()
    assert update_type in prompt, (
        f"UpdateType value '{update_type}' is not present in the production prompt. "
        f"This is the 2026-04-17 eval-prompt drift class of bug: the enum and the "
        f"prompt have desynchronized. Either add the value to the CANONICAL "
        f"update_type VALUES line in build_system_prompt(), or remove it from "
        f"UpdateType."
    )


def test_canonical_vocabulary_sections_present() -> None:
    """The CANONICAL sections are the mechanism that keeps the schema in sync.

    Detecting their removal explicitly (rather than only via downstream
    parametrize failures) gives a clearer diagnostic when someone deletes or
    renames the rendering. If this test fails, restore the CANONICAL lines
    in build_system_prompt() — they are rendered from the Pydantic source of
    truth and prevent drift by construction.
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

# chat-to-cop

AI staff officer system for MASH wargame events. IRC/STT chat → world-state extraction → CoP database.
See @docs/DESIGN_PHILOSOPHY.md and @docs/ARCHITECTURE.md for details.

## Build & Test

```bash
pip install -e ".[dev]"
ruff check src/ tests/ && ruff format src/ tests/ && pytest tests/ -k "not integration" -v
```

## Replay

```bash
python -m chat_to_cop.replay data/chat/Dash3-GBC/Data/23Sep/usaf/chat.zip --num-ctx 8192
python -m chat_to_cop.replay <path> --url http://localhost:11434/v1 --model qwen2.5:7b --num-ctx 8192
# --timeout sets per-message LLM timeout (seconds)
```

## Docker

```bash
docker compose up
```

## Branch/Commit Workflow

Every change goes through a branch and MR. Never commit directly to main.

```bash
git checkout main && git pull
git checkout -b <issue-number>-<short-description>
# ... work, test, lint ...
git push -u origin <branch-name>
```

Commit style: `feat|fix|refactor: description`, `Closes #<N>`, include test counts.
Always include `Co-Authored-By: Claude <noreply@anthropic.com>`.

## Issue Tracking

All work tracked on DLE GitLab: https://gitlab.dle.afrl.af.mil/c2es1/mash/chat-to-cop/-/issues (project ID: 18350).
Query GitLab for current issue status — do not rely on stale lists.

## Library Choices — Don't Reinvent

| Need | Use This | NOT This |
|------|----------|----------|
| Retries with backoff | `tenacity` | Custom retry loops |
| Circuit breaker | `pybreaker` | Custom state machine |
| Configuration | `pydantic-settings` | Custom config parser |
| Structured LLM output | `instructor` | Raw API calls + manual parsing |
| Experiment tracking | `mlflow` | Custom SQLite run logs |
| Metrics (future) | OpenTelemetry SDK | Custom metrics.py |

## Code Style

Python 3.10+, ruff (line-length 120), type hints, loguru, Pydantic, asyncio.
Do NOT add: docstrings to unchanged code, unnecessary error handling, premature abstractions.
Every LLM call must go through the OpenAI-compatible interface. Never import a vendor-specific SDK.

## Tests

- Unit: `tests/test_*.py`, run with `pytest tests/ -k "not integration"`
- Integration: marked `@pytest.mark.integration`, require Ollama or external services
- Use in-memory SQLite (`:memory:`) for store tests, FakeBackend for agent tests
- Every new component needs tests in the same MR

## Lessons Learned

**Config-bypass bug (2026-04-11):** Three pydantic-settings fields were silently dead in the production replay path because nothing read them between `PipelineConfig()` and the consumer. The April 6/10 A/B experiments both ran with speakers ON in both arms. Fixed in !88, with regression tests in `TestSupervisorAgentConfigPlumbing`.

**Rule: When adding any config Field, also add its consumer in the same MR.** Don't merge a `Field(...)` with no `config.x.y` reader. When testing config, monkeypatch the env var and assert the resulting object's behavior — testing the config object alone only tests pydantic.

## Important Constraints

- IL2 (unclassified) throughout — experiment freely
- Latency is #1 priority. Must be faster than humans. "70% is great."
- System must degrade gracefully: LLM → smaller LLM → regex → passthrough. Never silently stop.
- CoP database schema pending from contractor team — use BattleEffectSchemaV2 and GenMSG specs in @docs/SCHEMAS.md

## Context On Demand

- Architecture: @docs/ARCHITECTURE.md
- Design philosophy / anti-ontology: @docs/DESIGN_PHILOSOPHY.md
- Data sources: @docs/DATA_SOURCES.md
- Schemas: @docs/SCHEMAS.md
- Narwhal HPC: use `narwhal-hpc` skill
- Parallel worktree work: use `parallel-agent-work` skill
- RAI provenance: use `rai-provenance` skill

## Session Recovery

- When asked to update session notes: write SESSION_NOTES.md in project root
- When compacting: preserve the full list of modified files and current task state

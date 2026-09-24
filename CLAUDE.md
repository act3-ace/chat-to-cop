# chat-to-cop

AI staff officer for MASH wargame events: IRC/STT chat -> world-state extraction -> CoP database.
See @docs/DESIGN_PHILOSOPHY.md and @docs/ARCHITECTURE.md.

## What this file is, and is not

It carries only what is specific to this repo. Four other layers already apply, and repeating them
here would mean two copies to keep true:

| Layer | Holds |
|---|---|
| Team defaults, `~/.claude/CLAUDE.md` | CHANGELOG practice, intent markers, `glab` over the GitLab MCP, model delegation, no emoji or AI-attribution in anything a human reads |
| Personal overlay, `~/GitProjects/CLAUDE.md` | SSH on Windows, dual-push remotes, the HPC charge mandate, HPC venue rules |
| Workspace, `~/GitProjects/act3/CLAUDE.md` | Domain separation, the handling and classification standing rule, session recovery, where workspace memory lives |
| The harness | Commit attribution, set per session. Do not hand-write a `Co-Authored-By` line |

Memory for this repo lives in the workspace scope,
`~/.claude/projects/c--Users-hsclouse-GitProjects-act3/memory/`, and syncs through `claude-config`.
Write a memory when something was expensive to learn and is not already in the code or the
CHANGELOG.

Precedence, highest first: the user's request in the conversation, then this file, then the
workspace file, then the team defaults.

## The remotes are not interchangeable

- `origin` and `dle` both point at DLE GitLab, `c2es1/mash/chat-to-cop`, project **18350**. That is
  the tracker and the place work merges. Query it with `glab`; never trust a stale issue list.
- `github` is `act3-ace/chat-to-cop`, a **sanitized snapshot mirror**, written only by
  `scripts/mirror-to-github.sh`. It is not a backup and it is not a place to push by hand.

On 2026-05-05 a mirror run committed its sanitized tree straight onto main (295cb02). It deleted
`CLAUDE.md` and every file under `docs/internal/`, and nobody noticed for four months. It also left
placeholder substitutions inside live files, which are **still there**:
`deploy/ag/start-backend-for-mia.sh` says `deployer` where it means the real account (lines 103,
152, 240, 252, 299, 312) and `docs/GO_NOGO.md:217` says `C:\Users\user`. The script's own
sanitizer produced both (`mirror-to-github.sh:122`, `:104`).

The script was fixed on 2026-07-04 to build its temp branch from `github/main` instead of the
checked-out branch, so the accident should not recur. Still: run it from a clean tree, and read
what it pushed.

`CLAUDE.md` and `docs/internal/` are stripped from the mirror by design (`:129`, `:132`), so
internal working documents belong there and cannot leak to the public repo.

## Build, test, run

```bash
pip install -e ".[dev]"
ruff check src/ tests/ && ruff format src/ tests/ && pytest tests/ -k "not integration" -v
docker compose up
python -m chat_to_cop.replay data/dash3/23Sep_usaf_chat.zip --num-ctx 8192
python -m chat_to_cop.replay <path> --url http://localhost:11434/v1 --model model-b:7b --num-ctx 8192
```

`--timeout` sets the per-message LLM timeout in seconds. Integration tests are marked
`@pytest.mark.integration` and need Ollama or another live service.

## Defaults that are easy to get wrong

- **Speaker models are OFF** (`CHAT_TO_COP_USE_SPEAKER_MODELS=false`). The RQ1 N=100 sweep found
  they hurt Type Exact at every model size, p<0.0001. The criteria for turning them back on are in
  @docs/GO_NOGO.md; the evidence is in @docs/research/SPEAKER_MODEL_RESULTS.md.
- **HPC scripts carry live `#SBATCH --account` lines** (`scripts/narwhal_*.sh`). Every one must read
  `AFSNW27526RYZ`. The mandate itself is in the personal overlay; what is specific here is that this
  repo has seven scripts that can violate it silently.
- **Narwhal's partition is `general`**, with `--gres=gpu:v100:N`. `scripts/narwhal_30b_test.sh` is the
  lone outlier at `--partition=MLA` and is wrong; @docs/internal/NARWHAL_30B_TEST.md lesson 2 settled
  that in April.
- **Neither silent-failure alarm can fire.** `Supervisor.run()` has exactly one caller in the repo,
  a test. Anything that depends on the health loop is dead code in production until that changes.

## Versioning and releases

Semver at 0.y.z while the API is unstable: breaking changes bump MINOR, fixes bump PATCH. The
version lives in two places and they must agree — `pyproject.toml` and
`src/chat_to_cop/__init__.py` (both 0.1.4 today). To cut a release: update both, move
`[Unreleased]` in `CHANGELOG.md` into a new `[0.x.y]`, then `git tag v0.x.y && git push origin
v0.x.y`. CI builds the Docker and Apptainer images and creates the GitLab release.

## Library choices — don't reinvent

| Need | Use | Not |
|---|---|---|
| Retries with backoff | `tenacity` | a custom retry loop |
| Circuit breaker | `pybreaker` | a custom state machine |
| Configuration | `pydantic-settings` | a custom parser |
| Structured LLM output | `instructor` | raw calls plus manual parsing |
| Experiment tracking | `mlflow` | custom SQLite run logs |
| Metrics (future) | OpenTelemetry SDK | a custom `metrics.py` |

## Code style and tests

Python 3.10+, ruff at line-length 120, type hints, loguru, Pydantic, asyncio. Do not add docstrings
to code you did not change, error handling nothing asked for, or abstractions ahead of a second
caller. **Every LLM call goes through the OpenAI-compatible interface — never import a vendor SDK.**

Store tests use in-memory SQLite, agent tests use `FakeBackend`, and a new component ships its
tests in the same MR.

## The lesson that cost the most

**Config-bypass, 2026-04-11.** Three `pydantic-settings` fields were dead between `PipelineConfig()`
and any consumer, so the April 6 and 10 A/B experiments both ran with speakers ON in both arms and
the RQ1 numbers were invalid. Fixed in !88, with regression tests in
`TestSupervisorAgentConfigPlumbing`.

**The rule: when you add a config `Field`, add its consumer in the same MR.** Never merge a
`Field(...)` nothing reads. Test it by monkeypatching the environment variable and asserting on
behaviour — asserting on the config object only tests pydantic.

That failure has a sibling worth remembering: a claim in a commit message is not a fix. Commit
4aa362d said it corrected the `cop_updates` table name; its diff did not, and
`scripts/eval_speaker_sweep.py` stayed broken for five months while the CHANGELOG said otherwise.

## Constraints

- IL2 throughout — experiment freely.
- Latency first. It has to beat a human. "70% is great."
- Degrade gracefully: LLM -> smaller LLM -> regex -> passthrough. Never stop silently.
- The CoP schema is still the contractor's. Use BattleEffectSchemaV2 and the GenMSG specs in
  @docs/SCHEMAS.md.

## Context on demand

- Architecture: @docs/ARCHITECTURE.md
- Design philosophy and the anti-ontology argument: @docs/DESIGN_PHILOSOPHY.md
- Data sources: @docs/DATA_SOURCES.md
- Schemas: @docs/SCHEMAS.md
- Go/no-go criteria: @docs/GO_NOGO.md
- AG deployment: @docs/MASH_DEPLOYMENT.md, with the setup detail in
  @docs/internal/ANALYTICS_GATEWAY_DEPLOYMENT.md
- Internal working notes and briefing records: `docs/internal/` (restored 2026-09-24; several are
  historical records rather than current instructions, and say so in their own headers)

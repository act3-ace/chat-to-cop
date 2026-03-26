# Changelog

All notable changes to this project will be documented in this file.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- IRCMessage model and channel priority types (#1)
- DASH chat log replay parser — DASH 1, DASH 3 per-channel, DASH 3 combined formats (#2)
- Toggleable instrumentation foundation — counters, histograms, timers (#3)
- OpenAI-compatible LLM backend with instructor for structured output (#4)
- CoPUpdate, SpeakerModel, WorldStateSnapshot Pydantic models
- GitLab CI pipeline — lint (ruff) + test (pytest) on every push
- Container build on merge to main, tagged releases to registry
- Docker Compose setup (Ollama + pipeline)
- Design philosophy documentation (equifinality, antifragility, FACS)

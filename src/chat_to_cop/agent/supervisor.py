"""Supervisor: agent lifecycle and health management.

Lightweight Python process (NOT an LLM) that manages the agent pool.

Responsibilities:
- Health monitoring (latency, error rate, backend status per agent)
- Agent lifecycle (start, stop, restart, reassign channels)
- Priority management (shed load by deprioritizing LOW channels)
- New channel detection (spawn agent for channels appearing mid-exercise)
- Metrics exposure (Prometheus-compatible)
"""

from __future__ import annotations


# TODO: Implement Supervisor
# - async run() main loop
# - Agent registry and health tracking
# - Channel priority ordering from config
# - Load shedding logic
# - Dynamic channel assignment

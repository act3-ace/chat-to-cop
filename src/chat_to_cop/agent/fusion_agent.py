"""Fusion Agent: cross-channel semantic deconfliction.

Operates on CoPUpdate outputs from channel agents, NOT raw messages.

Responsibilities:
- Semantic deconfliction (same event reported by different channels)
- Cross-channel correlation (tasking in #c2_coord + status in #fires)
- Confidence aggregation (multiple reports = higher confidence)
- STT denoising (cross-reference STT against typed chat)

Degrades to pass-through if it fails — channel agent outputs go
directly to the world state store without fusion.
"""

from __future__ import annotations


# TODO: Implement FusionAgent
# - async process_updates(updates: list[CoPUpdate]) -> list[CoPUpdate]
# - Semantic similarity for deconfliction (not field matching)
# - Temporal proximity windowing
# - Confidence aggregation
# - Graceful degradation to pass-through

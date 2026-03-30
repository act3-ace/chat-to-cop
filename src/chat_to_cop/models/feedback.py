"""Fusion feedback: cross-channel corroboration signals sent back to channel agents.

When the fusion agent detects corroboration, contradiction, or deduplication
across channels, it emits FusionFeedback objects that flow back to channel
agents. This closes the feedback loop: fusion results improve speaker models.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FusionFeedback:
    """A feedback signal from fusion back to a channel agent's speaker model."""

    update_id: str  # ID of the CoPUpdate this feedback is about
    source_channel: str
    source_speaker: str
    feedback_type: str  # "corroborated", "contradicted", "deduplicated"
    corroborating_channels: list[str] = field(default_factory=list)
    confidence_delta: float = 0.0  # suggested adjustment to speaker reliability

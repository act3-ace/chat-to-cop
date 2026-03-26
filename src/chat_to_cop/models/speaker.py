"""Speaker model: learned per-user profile built during operations.

Each channel agent maintains speaker models that grow as the session
progresses. These are NOT pre-configured — they're learned from context.

Research question 1: How does a software agent personalize/learn a
model of the user during operations?
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class SpeakerModel(BaseModel):
    """Profile of a speaker learned during the session."""

    username: str
    first_seen: datetime
    last_seen: datetime
    message_count: int = 0

    # Learned attributes (all optional, populated as evidence accumulates)
    role: str | None = Field(None, description="Inferred role: BM, intel, fires, tanker, etc.")
    area_of_interest: str | None = Field(None, description="Which BMA, lane, or sector they focus on")
    jargon_notes: list[str] = Field(default_factory=list, description="Notable abbreviations this speaker uses")
    reliability: float = Field(0.5, description="Track record: are their reports confirmed or corrected?")
    topics: list[str] = Field(default_factory=list, description="Topics they typically discuss")

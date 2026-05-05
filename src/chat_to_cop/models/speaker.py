"""Speaker model: learned per-user profile built during operations.

Each channel agent maintains speaker models that grow as the session
progresses. These are NOT pre-configured -- they're learned from context.

Research question 1: How does a software agent personalize/learn a
model of the user during operations?

Structured around CTA (Cognitive Task Analysis) framework:
- Goals -> area of responsibility, mission role
- Cues -> what triggers them to communicate
- Expectancies -> what they monitor and anticipate
- Actions -> how they task, coordinate, report

And mapped to the SDAC model:
- Sensing -> what does this speaker observe/report?
- Deciding -> what decisions do they make?
- Acting -> what do they execute/direct?
- Collaborating -> who do they coordinate with?
"""

from __future__ import annotations

import json
from datetime import datetime

from pydantic import BaseModel, Field

from chat_to_cop.models.messages import IRCMessage


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

    # Cross-channel fusion feedback (updated by FusionFeedback loop)
    cross_channel_corroborations: int = Field(0, description="Times this speaker was corroborated by another channel")
    cross_channel_contradictions: int = Field(0, description="Times this speaker was contradicted by another channel")
    reliability_score: float = Field(0.5, description="Fusion-derived reliability: EMA of corroboration signals")

    # CTA framework fields
    goals: list[str] = Field(default_factory=list, description="Inferred goals/responsibilities from context")
    cues: list[str] = Field(default_factory=list, description="What triggers this speaker to communicate")
    expectancies: list[str] = Field(default_factory=list, description="What they monitor and anticipate")
    typical_actions: list[str] = Field(default_factory=list, description="How they task, coordinate, report")

    # SDAC activity counters (incremented by classification of their messages)
    sensing_count: int = Field(0, description="Messages where speaker reports observations")
    deciding_count: int = Field(0, description="Messages where speaker makes decisions")
    acting_count: int = Field(0, description="Messages where speaker directs/executes actions")
    collaborating_count: int = Field(0, description="Messages where speaker coordinates with others")

    # Raw message samples for LLM-based updates
    recent_messages: list[str] = Field(
        default_factory=list,
        description="Last N raw messages for LLM context (not persisted long-term)",
    )

    def update_from_message(self, message: IRCMessage, max_recent: int = 10) -> None:
        """Incrementally update the speaker model from a new message.

        This handles the lightweight, always-on updates (timestamps, counts,
        recent messages). Heavier inference (role, area, jargon) is done
        periodically by the LLM via update_from_inference().
        """
        self.last_seen = message.timestamp
        self.message_count += 1
        self.recent_messages.append(message.content)
        if len(self.recent_messages) > max_recent:
            self.recent_messages = self.recent_messages[-max_recent:]

    def update_from_inference(
        self,
        *,
        role: str | None = None,
        area_of_interest: str | None = None,
        jargon_notes: list[str] | None = None,
        topics: list[str] | None = None,
        goals: list[str] | None = None,
        cues: list[str] | None = None,
        expectancies: list[str] | None = None,
        typical_actions: list[str] | None = None,
        sdac_category: str | None = None,
    ) -> None:
        """Update learned attributes from LLM inference.

        Called after the LLM has analyzed the speaker's recent messages
        and inferred higher-level attributes. Only non-None fields are updated.
        """
        if role is not None:
            self.role = role
        if area_of_interest is not None:
            self.area_of_interest = area_of_interest
        if jargon_notes is not None:
            # Merge, don't replace -- accumulate jargon over time
            for note in jargon_notes:
                if note not in self.jargon_notes:
                    self.jargon_notes.append(note)
        if topics is not None:
            for topic in topics:
                if topic not in self.topics:
                    self.topics.append(topic)
        if goals is not None:
            for goal in goals:
                if goal not in self.goals:
                    self.goals.append(goal)
        if cues is not None:
            for cue in cues:
                if cue not in self.cues:
                    self.cues.append(cue)
        if expectancies is not None:
            for exp in expectancies:
                if exp not in self.expectancies:
                    self.expectancies.append(exp)
        if typical_actions is not None:
            for action in typical_actions:
                if action not in self.typical_actions:
                    self.typical_actions.append(action)
        if sdac_category is not None:
            cat = sdac_category.lower()
            if cat == "sensing":
                self.sensing_count += 1
            elif cat == "deciding":
                self.deciding_count += 1
            elif cat == "acting":
                self.acting_count += 1
            elif cat == "collaborating":
                self.collaborating_count += 1

    def update_from_fusion_feedback(self, feedback: object) -> None:
        """Update reliability from a FusionFeedback signal.

        Uses exponential moving average (alpha=0.1) so recent evidence
        matters more but history isn't discarded.

        Accepts any object with a feedback_type attribute to avoid circular
        imports with the feedback module.
        """
        feedback_type = getattr(feedback, "feedback_type", None)
        if feedback_type == "corroborated":
            self.cross_channel_corroborations += 1
            self.reliability_score = 0.9 * self.reliability_score + 0.1 * 1.0
        elif feedback_type == "contradicted":
            self.cross_channel_contradictions += 1
            self.reliability_score = 0.9 * self.reliability_score + 0.1 * 0.0
        # Clamp to [0, 1]
        self.reliability_score = max(0.0, min(1.0, self.reliability_score))

    @property
    def primary_sdac_role(self) -> str | None:
        """Return the dominant SDAC category for this speaker, or None if no data."""
        counts = {
            "sensing": self.sensing_count,
            "deciding": self.deciding_count,
            "acting": self.acting_count,
            "collaborating": self.collaborating_count,
        }
        total = sum(counts.values())
        if total == 0:
            return None
        return max(counts, key=counts.get)  # type: ignore[arg-type]

    def format_for_prompt(self) -> str:
        """Format this speaker model as a concise string for the LLM prompt."""
        parts = [self.username]
        if self.role:
            parts.append(f"role={self.role}")
        if self.area_of_interest:
            parts.append(f"area={self.area_of_interest}")
        if self.topics:
            parts.append(f"topics=[{', '.join(self.topics[:5])}]")
        if self.jargon_notes:
            parts.append(f"jargon=[{', '.join(self.jargon_notes[:5])}]")
        sdac = self.primary_sdac_role
        if sdac:
            parts.append(f"primary_activity={sdac}")
        parts.append(f"msgs={self.message_count}")
        parts.append(f"reliability={self.reliability:.1f}")
        return "; ".join(parts)


class SpeakerInference(BaseModel):
    """Schema for LLM-based speaker model updates.

    Passed to the LLM to infer higher-level speaker attributes
    from their recent messages.
    """

    role: str | None = Field(None, description="Inferred role: BM, intel, fires, tanker controller, etc.")
    area_of_interest: str | None = Field(None, description="BMA, lane, sector, or domain they focus on")
    jargon_notes: list[str] = Field(default_factory=list, description="Notable abbreviations or terminology")
    topics: list[str] = Field(default_factory=list, description="Topics they discuss")
    goals: list[str] = Field(default_factory=list, description="Inferred goals/responsibilities")
    cues: list[str] = Field(default_factory=list, description="What triggers them to communicate")
    expectancies: list[str] = Field(default_factory=list, description="What they monitor/anticipate")
    typical_actions: list[str] = Field(default_factory=list, description="How they task, coordinate, report")
    sdac_category: str | None = Field(
        None,
        description="Primary SDAC category for their latest message: sensing, deciding, acting, or collaborating",
    )


class SpeakerRegistry:
    """Manages speaker models for a single channel.

    The registry creates new SpeakerModel instances on first contact
    and provides lookup and formatting for the LLM prompt.
    """

    def __init__(self) -> None:
        self._speakers: dict[str, SpeakerModel] = {}

    def get_or_create(self, username: str, timestamp: datetime) -> SpeakerModel:
        """Get existing speaker model or create a new one."""
        if username not in self._speakers:
            self._speakers[username] = SpeakerModel(
                username=username,
                first_seen=timestamp,
                last_seen=timestamp,
            )
        return self._speakers[username]

    def get(self, username: str) -> SpeakerModel | None:
        """Get a speaker model by username, or None if not seen."""
        return self._speakers.get(username)

    @property
    def speakers(self) -> dict[str, SpeakerModel]:
        return self._speakers

    def format_all_for_prompt(self) -> str:
        """Format all known speakers for inclusion in the LLM system prompt."""
        if not self._speakers:
            return ""
        lines = []
        for model in self._speakers.values():
            if model.message_count > 0:
                lines.append(f"- {model.format_for_prompt()}")
        return "\n".join(lines)

    def load_speaker(self, model: SpeakerModel) -> None:
        """Load a speaker model (e.g., from SQLite on restart)."""
        self._speakers[model.username] = model

    def to_json_list(self) -> str:
        """Serialize all speaker models to JSON for persistence."""
        return json.dumps([m.model_dump(mode="json") for m in self._speakers.values()])

    @classmethod
    def from_json_list(cls, data: str) -> SpeakerRegistry:
        """Deserialize speaker models from JSON."""
        registry = cls()
        for item in json.loads(data):
            model = SpeakerModel.model_validate(item)
            registry._speakers[model.username] = model
        return registry

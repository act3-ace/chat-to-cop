"""IRC message model and routing types.

The IRCMessage is the universal input type that flows through the entire
system — from ingestion (live or replay) through channel agents to the
world state store's audit log.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class ChannelPriority(str, Enum):
    """Channel priority for load shedding.

    When all agents are slow, the supervisor sheds LOW channels first,
    then MEDIUM, keeping HIGH channels processing as long as possible.
    """

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class IRCMessage(BaseModel):
    """A single message from IRC (typed chat or voice STT)."""

    timestamp: datetime
    channel: str = Field(description="IRC channel name, e.g. '#c2_coord' or '#stt_hydroBMA'")
    sender: str = Field(description="Username of the sender, e.g. 'Hydro_Tank' or 'google-speech-to-text'")
    content: str = Field(description="Message text content")
    raw_line: str = Field(default="", description="Original unparsed line from the log or IRC stream")

    @property
    def is_stt(self) -> bool:
        """Whether this message is from a speech-to-text channel."""
        return self.channel.startswith("#stt_")

    @property
    def priority(self) -> ChannelPriority:
        """Channel priority based on channel name.

        Typed chat channels are HIGH, STT channels are MEDIUM,
        exercise control is LOW. Unknown channels default to MEDIUM.
        """
        return CHANNEL_PRIORITIES.get(self.channel, ChannelPriority.MEDIUM)


# Priority mapping loaded from config at startup; these are DASH 3 defaults.
# Overridden by config/irc_channels.json at runtime.
CHANNEL_PRIORITIES: dict[str, ChannelPriority] = {
    "#c2_coord": ChannelPriority.HIGH,
    "#isr_reports": ChannelPriority.HIGH,
    "#fires": ChannelPriority.HIGH,
    "#jprc": ChannelPriority.MEDIUM,
    "#stt_C2Coord": ChannelPriority.MEDIUM,
    "#stt_hydroBMA": ChannelPriority.MEDIUM,
    "#stt_crusherBMA": ChannelPriority.MEDIUM,
    "#stt_mesquiteBMA": ChannelPriority.MEDIUM,
    "#stt_taipanBMA": ChannelPriority.MEDIUM,
    "#vegas_internal": ChannelPriority.LOW,
}

"""Channel Agent: stateful per-channel world-state extractor.

Each channel agent maintains:
- Conversation window (sliding buffer of recent messages)
- Speaker models (learned per-user profiles)
- World state snapshot (current believed battlespace state)
- Degrading LLM backend (best model -> smaller -> regex -> passthrough)

The agent receives messages from the message router and emits CoPUpdate
objects to the world state store.
"""

from __future__ import annotations


# TODO: Implement ChannelAgent
# - __init__(channel_name, backend, config)
# - async process_message(message) -> list[CoPUpdate]
# - Manage conversation window (last N messages or T minutes)
# - Maintain and update speaker models
# - Include world state context in LLM prompts
# - Handle backend degradation transparently

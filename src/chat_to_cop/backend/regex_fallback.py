"""Regex fallback backend: pattern matching when LLMs are unavailable.

Uses track_patterns.json and domain-specific extractors for:
- Track numbers (TM###, DA###, 5-digit TNs)
- Fuel state (F+##, playtime)
- Weapons count (launched/remaining)
- Operational status (RTB, gadget bent, inop)
- Coordinate extraction (lat/lon, bullseye/cigar, MGRS)
- Noise filtering (acks, radio checks)

Returns low-confidence CoPUpdates. Better than nothing.
"""

from __future__ import annotations


# TODO: Implement RegexBackend
# - Load patterns from config/track_patterns.json
# - Add fuel, weapons, status extractors
# - async extract(messages, schema) -> BaseModel
# - Always returns confidence < 0.5 and extraction_method="regex"

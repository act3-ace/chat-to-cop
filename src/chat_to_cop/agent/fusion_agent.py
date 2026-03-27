"""Fusion Agent: cross-channel semantic deconfliction and RTA output monitoring.

Operates on CoPUpdate outputs from channel agents, NOT raw messages.

This component is a Run Time Assurance (RTA) output monitor per
Lyons, Hobbs, Rogers, Clouse (2023). It observes extracted CoPUpdates
and modifies propagation when safety/trust properties are violated.

Responsibilities:
- Temporal deconfliction (same event within N seconds = duplicate)
- Track/callsign matching across channels
- Confidence aggregation (corroborated = boosted, contradictory = flagged)
- STT denoising (down-weight STT channel reports)
- Per-speaker trust scoring
- Adversarial input detection (basic prompt injection scanning)
- Out-of-ontology lane (never drop data)
- Provenance chain (which source updates contributed to fused output)

Degrades to pass-through if fusion itself fails.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import timedelta

from loguru import logger

from chat_to_cop.metrics import metrics
from chat_to_cop.models.cop_update import CoPUpdate, UpdateType

# --- Adversarial input detection patterns ---
# Basic prompt injection / jailbreak patterns in chat messages
_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+(a|an)\s+", re.IGNORECASE),
    re.compile(r"system\s*:\s*", re.IGNORECASE),
    re.compile(r"<\|?(system|assistant|user)\|?>", re.IGNORECASE),
    re.compile(r"forget\s+(everything|all|your)\s+", re.IGNORECASE),
    re.compile(r"override\s+(your|the)\s+(instructions|rules|prompt)", re.IGNORECASE),
]

# STT channel prefix
_STT_PREFIX = "#stt_"

# Default confidence adjustments
_STT_CONFIDENCE_PENALTY = 0.15  # reduce STT-sourced confidence
_CORROBORATION_BOOST = 0.10  # boost when corroborated by another channel
_CONTRADICTION_PENALTY = 0.20  # reduce when contradicted


def detect_injection(text: str) -> bool:
    """Scan a message for basic prompt injection patterns.

    Returns True if any injection pattern is detected.
    """
    return any(p.search(text) for p in _INJECTION_PATTERNS)


def _entity_key(update: CoPUpdate) -> str | None:
    """Extract a deconfliction key from an update's entities.

    Uses track_number or callsign as the primary matching key.
    Returns None if no identifiable entity is present.
    """
    for entity in update.entities:
        if entity.track_number:
            return f"tn:{entity.track_number}"
        if entity.callsign:
            return f"cs:{entity.callsign}"
    return None


def _updates_contradict(a: CoPUpdate, b: CoPUpdate) -> bool:
    """Check if two updates about the same entity contain contradictory info.

    For example, one says OPERATIONAL and the other says DESTROYED.
    """
    for ea in a.entities:
        for eb in b.entities:
            if ea.operational_status and eb.operational_status:
                if ea.operational_status != eb.operational_status:
                    return True
            if ea.affiliation and eb.affiliation:
                if ea.affiliation != eb.affiliation:
                    return True
    return False


class FusionAgent:
    """Cross-channel deconfliction and RTA output monitoring.

    Collects CoPUpdates from channel agents and produces fused outputs:
    - Duplicates are merged (highest confidence kept, provenance combined)
    - Corroborated reports get a confidence boost
    - Contradictions are flagged for human review
    - STT-sourced updates are down-weighted
    - Adversarial inputs are flagged
    - Unclassifiable data is retained (never dropped)

    Degrades to pass-through if fusion logic raises an exception.
    """

    def __init__(
        self,
        temporal_window_seconds: float = 30.0,
        stt_penalty: float = _STT_CONFIDENCE_PENALTY,
        corroboration_boost: float = _CORROBORATION_BOOST,
        contradiction_penalty: float = _CONTRADICTION_PENALTY,
    ) -> None:
        self.temporal_window = timedelta(seconds=temporal_window_seconds)
        self.stt_penalty = stt_penalty
        self.corroboration_boost = corroboration_boost
        self.contradiction_penalty = contradiction_penalty

        # Per-speaker trust tracking: username -> (confirmed, total)
        self._speaker_stats: dict[str, list[int]] = defaultdict(lambda: [0, 0])

        # Per-channel noise estimation: channel -> (noise_count, total)
        self._channel_stats: dict[str, list[int]] = defaultdict(lambda: [0, 0])

    # --- Public API ---

    def process_updates(self, updates: list[CoPUpdate]) -> list[CoPUpdate]:
        """Process a batch of CoPUpdates from channel agents.

        This is the main entry point. Takes raw channel agent outputs
        and returns fused, scored, and deduplicated updates.

        Degrades to pass-through if fusion fails.
        """
        if not updates:
            return []

        try:
            return self._fuse(updates)
        except Exception as e:
            logger.error("Fusion failed, passing through raw updates: {}", e)
            metrics.inc("fusion_errors_total")
            return updates  # Pass-through on failure

    def speaker_reliability(self, username: str) -> float:
        """Get the trust score for a speaker (0.0-1.0, default 0.5)."""
        stats = self._speaker_stats.get(username)
        if stats is None or stats[1] == 0:
            return 0.5
        return stats[0] / stats[1]

    def channel_noise_rate(self, channel: str) -> float:
        """Get the estimated noise rate for a channel (0.0-1.0)."""
        stats = self._channel_stats.get(channel)
        if stats is None or stats[1] == 0:
            return 0.0
        return stats[0] / stats[1]

    def record_speaker_outcome(self, username: str, confirmed: bool) -> None:
        """Record whether a speaker's report was confirmed or corrected.

        Called by external validation (e.g., ground truth comparison,
        cross-channel corroboration, or operator correction).
        """
        stats = self._speaker_stats[username]
        if confirmed:
            stats[0] += 1
        stats[1] += 1

    # --- Internal fusion logic ---

    def _fuse(self, updates: list[CoPUpdate]) -> list[CoPUpdate]:
        """Core fusion pipeline."""
        result: list[CoPUpdate] = []

        # Step 1: Adversarial input detection
        updates = self._scan_adversarial(updates)

        # Step 2: Update channel/speaker stats
        self._update_stats(updates)

        # Step 3: Apply STT penalty
        updates = self._apply_stt_penalty(updates)

        # Step 4: Apply speaker trust scoring
        updates = self._apply_speaker_trust(updates)

        # Step 5: Temporal deconfliction — group by entity key + time window
        groups = self._group_by_entity_and_time(updates)

        # Step 6: For each group, merge or flag
        for key, group in groups.items():
            if key.startswith("_none#"):
                # No identifiable entity — pass through individually
                result.extend(group)
                continue

            if len(group) == 1:
                # Single report, no deconfliction needed
                result.append(group[0])
                continue

            # Multiple reports about the same entity within the time window
            fused = self._merge_group(key, group)
            result.append(fused)

        metrics.inc("fusion_updates_in_total", value=len(updates))
        metrics.inc("fusion_updates_out_total", value=len(result))
        return result

    def _scan_adversarial(self, updates: list[CoPUpdate]) -> list[CoPUpdate]:
        """Scan for prompt injection patterns in source messages."""
        flagged = []
        for update in updates:
            if detect_injection(update.source_message):
                logger.warning(
                    "Adversarial input detected from {}: {}",
                    update.source_speaker,
                    update.source_message[:80],
                )
                metrics.inc("fusion_adversarial_detected_total")
                # Don't drop — flag it (Pattern B: out-of-ontology lane)
                update.confidence = max(0.0, update.confidence - 0.3)
                update.reasoning = (
                    f"FLAGGED: Possible adversarial input detected. Original reasoning: {update.reasoning or 'none'}"
                )
            flagged.append(update)
        return flagged

    def _update_stats(self, updates: list[CoPUpdate]) -> None:
        """Update per-channel noise statistics."""
        for update in updates:
            stats = self._channel_stats[update.source_channel]
            stats[1] += 1
            if update.update_type == UpdateType.NONE:
                stats[0] += 1

    def _apply_stt_penalty(self, updates: list[CoPUpdate]) -> list[CoPUpdate]:
        """Reduce confidence for STT-sourced updates."""
        for update in updates:
            if update.source_channel.startswith(_STT_PREFIX):
                update.confidence = max(0.0, update.confidence - self.stt_penalty)
        return updates

    def _apply_speaker_trust(self, updates: list[CoPUpdate]) -> list[CoPUpdate]:
        """Adjust confidence based on speaker reliability history."""
        for update in updates:
            reliability = self.speaker_reliability(update.source_speaker)
            # Scale confidence by reliability (0.5 = neutral, <0.5 = penalty, >0.5 = boost)
            adjustment = (reliability - 0.5) * 0.2  # max ±0.1 adjustment
            update.confidence = max(0.0, min(1.0, update.confidence + adjustment))
        return updates

    def _group_by_entity_and_time(self, updates: list[CoPUpdate]) -> dict[str, list[CoPUpdate]]:
        """Group updates by entity key within the temporal window.

        Same entity key appearing in different time windows produces
        separate groups (using a counter suffix to keep dict keys unique).
        """
        groups: dict[str, list[CoPUpdate]] = {}
        group_counter = 0

        # Sort by timestamp for consistent grouping
        sorted_updates = sorted(updates, key=lambda u: u.timestamp)

        # Track which updates have been assigned to a group
        assigned: set[int] = set()

        for i, update in enumerate(sorted_updates):
            if i in assigned:
                continue

            key = _entity_key(update)
            group = [update]
            assigned.add(i)

            if key is not None:
                # Look for other updates with the same entity key within the time window
                for j in range(i + 1, len(sorted_updates)):
                    if j in assigned:
                        continue
                    other = sorted_updates[j]
                    # Outside time window — stop looking
                    if other.timestamp - update.timestamp > self.temporal_window:
                        break
                    if _entity_key(other) == key:
                        group.append(other)
                        assigned.add(j)

            # Use unique group key so same entity in different time windows stays separate
            # None-keyed updates each get their own group (no deconfliction possible)
            group_key = f"{key}#{group_counter}" if key is not None else f"_none#{group_counter}"
            groups[group_key] = group
            group_counter += 1

        return groups

    def _merge_group(self, key: str, group: list[CoPUpdate]) -> CoPUpdate:
        """Merge multiple reports about the same entity.

        - If they agree: boost confidence, combine provenance
        - If they contradict: flag, reduce confidence, keep highest-confidence version
        """
        # Sort by confidence descending — primary is the highest confidence report
        group.sort(key=lambda u: u.confidence, reverse=True)
        primary = group[0]

        # Check for cross-channel reports (different channels = independent sources)
        channels = {u.source_channel for u in group}
        is_cross_channel = len(channels) > 1

        # Check for contradictions
        has_contradiction = False
        for i, a in enumerate(group):
            for b in group[i + 1 :]:
                if _updates_contradict(a, b):
                    has_contradiction = True
                    break
            if has_contradiction:
                break

        if has_contradiction:
            # Contradictory reports — flag for human review
            primary.confidence = max(0.0, primary.confidence - self.contradiction_penalty)
            primary.reasoning = (
                f"CONTRADICTION detected across channels {channels}. "
                f"Keeping highest-confidence report. "
                f"Original: {primary.reasoning or 'none'}"
            )
            metrics.inc("fusion_contradictions_total")
            logger.warning("Contradiction for {}: channels={}", key, channels)

            # Record speaker stats — the contradicted speakers are less reliable
            for update in group[1:]:
                if _updates_contradict(primary, update):
                    self.record_speaker_outcome(update.source_speaker, confirmed=False)
            self.record_speaker_outcome(primary.source_speaker, confirmed=True)

        elif is_cross_channel:
            # Corroborated by independent channels — boost confidence
            primary.confidence = min(1.0, primary.confidence + self.corroboration_boost)
            primary.reasoning = (
                f"Corroborated by {len(group)} reports across {channels}. Original: {primary.reasoning or 'none'}"
            )
            metrics.inc("fusion_corroborations_total")

            # Record all speakers as confirmed
            for update in group:
                self.record_speaker_outcome(update.source_speaker, confirmed=True)

        else:
            # Same channel duplicate — just deduplicate
            metrics.inc("fusion_deduplicates_total")

        # Combine provenance from all source updates
        primary.context_messages = list(
            dict.fromkeys(  # deduplicate while preserving order
                primary.context_messages
                + [f"[{u.source_channel}] {u.source_speaker}: {u.source_message}" for u in group[1:]]
            )
        )

        return primary

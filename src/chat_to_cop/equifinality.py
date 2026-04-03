"""Path entropy tracker for equifinality measurement.

Tracks which backends produce which update types and computes Shannon
entropy per update type. This is a READ-ONLY metric — it does not
modify extraction logic.

High entropy = healthy equifinality (multiple paths work for this type)
Low entropy = fragile (only one backend handles this type)
"""

from __future__ import annotations

import math
from collections import defaultdict


class PathEntropyTracker:
    """Tracks which backends extract which update types, computes Shannon entropy."""

    def __init__(self) -> None:
        self._counts: dict[tuple[str, str], int] = defaultdict(int)

    def record(self, update_type: str, backend_name: str) -> None:
        """Record a successful extraction path."""
        self._counts[(update_type, backend_name)] += 1

    def entropy(self, update_type: str) -> float:
        """Shannon entropy for extraction paths of this update type.

        Returns 0.0 if no extractions recorded for this type.
        With N equally-used backends, returns log2(N).
        """
        counts = {k: v for k, v in self._counts.items() if k[0] == update_type}
        total = sum(counts.values())
        if total == 0:
            return 0.0
        probs = [c / total for c in counts.values()]
        return -sum(p * math.log2(p) for p in probs if p > 0)

    def report(self) -> dict[str, float]:
        """Entropy for all observed update types."""
        types = {k[0] for k in self._counts}
        return {t: self.entropy(t) for t in sorted(types)}

    def fragile_types(self, threshold: float = 0.5) -> list[str]:
        """Update types with entropy below threshold (fragile paths)."""
        return [t for t, e in self.report().items() if e < threshold]

    def path_counts(self) -> dict[str, dict[str, int]]:
        """Per-type backend counts for diagnostics."""
        result: dict[str, dict[str, int]] = defaultdict(dict)
        for (utype, backend), count in sorted(self._counts.items()):
            result[utype][backend] = count
        return dict(result)

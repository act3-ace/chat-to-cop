"""Entity catalog: load Blue/Red force reference data for enrichment.

Loads schema_assets.csv (Blue forces) and dash_target_taxonomy.csv (Red targets)
from the equifinality repo's processed data directory. If the files don't exist,
the catalog degrades gracefully — lookups return None, enrichment is a no-op.

The catalog is optional and configured via CHAT_TO_COP_ENTITY_CATALOG_DIR.
"""

from __future__ import annotations

import csv
from pathlib import Path

from loguru import logger

from chat_to_cop.models.cop_update import EntityUpdate


class EntityCatalog:
    """Lookup table for Blue and Red force entities.

    Supports callsign-based lookups (Blue assets) and track/target-class
    lookups (Red targets). Used by channel agents to enrich extracted
    entities with platform_type, affiliation, and super_category.
    """

    def __init__(self) -> None:
        # callsign (upper) -> {asset_id, platform_type, super_category, is_generic, affiliation}
        self._callsigns: dict[str, dict[str, str]] = {}
        # target_class (upper) -> {target_class, parent_classes, aliases, notes, affiliation}
        self._targets: dict[str, dict[str, str]] = {}
        # alias (upper) -> target_class (upper) for reverse lookup
        self._target_aliases: dict[str, str] = {}

    @property
    def callsign_count(self) -> int:
        return len(self._callsigns)

    @property
    def target_count(self) -> int:
        return len(self._targets)

    def load_blue_assets(self, path: str | Path) -> int:
        """Load Blue force assets from schema_assets.csv.

        Expected columns: asset_id, platform_type, super_category, is_generic

        Returns the number of assets loaded.
        """
        path = Path(path)
        if not path.exists():
            logger.warning("Blue assets file not found: {}", path)
            return 0

        count = 0
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                asset_id = row.get("asset_id", "").strip()
                if not asset_id:
                    continue
                # Strip numeric suffix for callsign matching (e.g. THOR -> THOR, BANG -> BANG)
                # The actual callsign in chat might be "THOR11", "BANG72", etc.
                # We index by the base callsign (asset_id) for prefix matching.
                key = asset_id.upper()
                self._callsigns[key] = {
                    "asset_id": asset_id,
                    "platform_type": row.get("platform_type", "").strip(),
                    "super_category": row.get("super_category", "").strip(),
                    "is_generic": row.get("is_generic", "").strip(),
                    "affiliation": "FRIEND",
                }
                count += 1

        logger.info("Loaded {} Blue assets from {}", count, path)
        return count

    def load_red_targets(self, path: str | Path) -> int:
        """Load Red force targets from dash_target_taxonomy.csv.

        Expected columns: target_class, parent_classes, aliases, notes

        Returns the number of targets loaded.
        """
        path = Path(path)
        if not path.exists():
            logger.warning("Red targets file not found: {}", path)
            return 0

        count = 0
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                target_class = row.get("target_class", "").strip()
                if not target_class:
                    continue
                key = target_class.upper()
                entry = {
                    "target_class": target_class,
                    "parent_classes": row.get("parent_classes", "").strip(),
                    "aliases": row.get("aliases", "").strip(),
                    "notes": row.get("notes", "").strip(),
                    "affiliation": "HOSTILE",
                }
                self._targets[key] = entry

                # Index aliases for reverse lookup
                aliases_str = row.get("aliases", "").strip()
                if aliases_str:
                    for alias in aliases_str.split("|"):
                        alias = alias.strip()
                        if alias:
                            self._target_aliases[alias.upper()] = key

                count += 1

        logger.info("Loaded {} Red targets from {}", count, path)
        return count

    def load_directory(self, catalog_dir: str | Path) -> None:
        """Load both Blue and Red catalogs from a directory.

        Expects:
          - <catalog_dir>/schema_assets.csv (Blue)
          - <catalog_dir>/dash_target_taxonomy.csv (Red)

        Missing files are silently skipped (catalog degrades to empty).
        """
        catalog_dir = Path(catalog_dir)
        self.load_blue_assets(catalog_dir / "schema_assets.csv")
        self.load_red_targets(catalog_dir / "dash_target_taxonomy.csv")

    def lookup_callsign(self, callsign: str) -> dict[str, str] | None:
        """Look up a callsign in the Blue asset catalog.

        Tries exact match first (e.g. "THOR"), then strips trailing digits
        for prefix match (e.g. "THOR11" -> "THOR"). This handles the common
        pattern where chat uses numbered variants like BANG72, HADES31, etc.

        Returns a dict with asset_id, platform_type, super_category,
        is_generic, affiliation — or None if not found.
        """
        if not callsign:
            return None
        key = callsign.upper().strip()

        # Exact match
        if key in self._callsigns:
            return dict(self._callsigns[key])

        # Strip trailing digits for prefix match
        base = key.rstrip("0123456789")
        if base and base != key and base in self._callsigns:
            return dict(self._callsigns[base])

        return None

    def lookup_track(self, track_number: str) -> dict[str, str] | None:
        """Look up a track number or target class in the Red target catalog.

        Checks both target_class names and aliases. Returns a dict with
        target_class, parent_classes, aliases, notes, affiliation —
        or None if not found.
        """
        if not track_number:
            return None
        key = track_number.upper().strip()

        # Direct target class match
        if key in self._targets:
            return dict(self._targets[key])

        # Alias match
        if key in self._target_aliases:
            canonical = self._target_aliases[key]
            return dict(self._targets[canonical])

        return None

    def enrich_entity(self, entity: EntityUpdate) -> EntityUpdate:
        """Fill in platform_type and affiliation from catalog if available.

        Tries callsign lookup (Blue) first, then track_number lookup (Red).
        Only fills fields that are currently None/empty on the entity.
        Returns the same entity (mutated in place).
        """
        # Try Blue callsign lookup
        if entity.callsign:
            info = self.lookup_callsign(entity.callsign)
            if info:
                if not entity.platform_type:
                    entity.platform_type = info.get("platform_type") or None
                if not entity.affiliation:
                    entity.affiliation = info.get("affiliation") or None
                # Store super_category in metadata for downstream use
                super_cat = info.get("super_category", "")
                if super_cat and "super_category" not in entity.metadata:
                    entity.metadata["super_category"] = super_cat
                return entity

        # Try Red track/target lookup
        if entity.track_number:
            info = self.lookup_track(entity.track_number)
            if info:
                if not entity.platform_type:
                    entity.platform_type = info.get("target_class") or None
                if not entity.affiliation:
                    entity.affiliation = info.get("affiliation") or None
                # Store parent hierarchy in metadata
                parents = info.get("parent_classes", "")
                if parents and "target_hierarchy" not in entity.metadata:
                    entity.metadata["target_hierarchy"] = parents
                return entity

        return entity

"""Equipment/weapon catalog: optional MACE-derived reference data.

Loads pre-processed weapon/equipment data from JSON or CSV if a file path
is configured via CHAT_TO_COP_EQUIPMENT_CATALOG_PATH. If the path is empty
or the file doesn't exist, all lookups return None gracefully.

This is a tentative integration from the equifinality repo's MACE data.
Easily reversible: remove this file and the config field to disable.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from loguru import logger


class EquipmentCatalog:
    """Lookup table for weapon/equipment reference data.

    Supports loading from JSON (list of dicts) or CSV. Each entry should
    have at minimum a "name" field. Optional fields: designation, type,
    category, description, aliases (pipe-delimited string or list).

    Fuzzy matching: lookup checks exact name, designation, and aliases,
    all case-insensitive. Substring matching on name/designation as fallback.
    """

    def __init__(self) -> None:
        # canonical name (upper) -> full entry dict
        self._entries: dict[str, dict] = {}
        # alias/designation (upper) -> canonical name (upper)
        self._aliases: dict[str, str] = {}

    @property
    def count(self) -> int:
        return len(self._entries)

    def load_from_json(self, path: str | Path) -> int:
        """Load equipment data from a JSON file (list of dicts).

        Each dict should have at least a "name" key.
        Returns the number of entries loaded.
        """
        path = Path(path)
        if not path.exists():
            logger.warning("Equipment catalog JSON not found: {}", path)
            return 0

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to parse equipment catalog {}: {}", path, e)
            return 0

        if not isinstance(data, list):
            logger.warning("Equipment catalog JSON must be a list of dicts, got {}", type(data).__name__)
            return 0

        count = 0
        for entry in data:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name", "").strip()
            if not name:
                continue
            self._index_entry(name, entry)
            count += 1

        logger.info("Loaded {} equipment entries from {}", count, path)
        return count

    def load_from_csv(self, path: str | Path) -> int:
        """Load equipment data from a CSV file.

        Expected columns: name (required), plus any of: designation, type,
        category, description, aliases.
        Returns the number of entries loaded.
        """
        path = Path(path)
        if not path.exists():
            logger.warning("Equipment catalog CSV not found: {}", path)
            return 0

        count = 0
        try:
            with open(path, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    name = row.get("name", "").strip()
                    if not name:
                        continue
                    self._index_entry(name, dict(row))
                    count += 1
        except OSError as e:
            logger.warning("Failed to read equipment catalog {}: {}", path, e)
            return 0

        logger.info("Loaded {} equipment entries from {}", count, path)
        return count

    def _index_entry(self, name: str, entry: dict) -> None:
        """Index a single entry by name, designation, and aliases."""
        key = name.upper()
        self._entries[key] = entry

        # Index designation
        designation = entry.get("designation", "").strip()
        if designation:
            self._aliases[designation.upper()] = key

        # Index aliases (pipe-delimited string or list)
        aliases = entry.get("aliases", "")
        if isinstance(aliases, list):
            alias_list = aliases
        elif isinstance(aliases, str) and aliases.strip():
            alias_list = [a.strip() for a in aliases.split("|") if a.strip()]
        else:
            alias_list = []

        for alias in alias_list:
            self._aliases[alias.upper()] = key

    def lookup_weapon(self, name: str) -> dict | None:
        """Look up a weapon/equipment by name, designation, or alias.

        Tries exact match first, then alias match, then substring match
        on canonical names and designations. All case-insensitive.

        Returns a copy of the entry dict, or None if not found.
        """
        if not name:
            return None
        key = name.upper().strip()

        # Exact name match
        if key in self._entries:
            return dict(self._entries[key])

        # Alias/designation match
        if key in self._aliases:
            canonical = self._aliases[key]
            return dict(self._entries[canonical])

        # Substring match: check if query is contained in any canonical name or designation
        for canon_key, entry in self._entries.items():
            if key in canon_key:
                return dict(entry)
            designation = entry.get("designation", "").upper()
            if designation and key in designation:
                return dict(entry)

        # Reverse substring: check if any canonical name or alias is contained in the query
        for canon_key, entry in self._entries.items():
            if canon_key in key:
                return dict(entry)

        return None

    def list_weapons(self) -> list[str]:
        """Return all canonical weapon/equipment names, sorted."""
        return sorted(entry.get("name", k) for k, entry in self._entries.items())

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

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

from chat_to_cop.metrics import metrics
from chat_to_cop.models.cop_update import CoPUpdate, EntityUpdate, UpdateType

# Default path to track patterns config
DEFAULT_PATTERNS_PATH = Path(__file__).resolve().parent.parent.parent.parent / "config" / "track_patterns.json"

# --- Compiled domain-specific patterns ---

# Fuel: F+##, F-##, playtime ## min/mike
_FUEL_RE = re.compile(
    r"""
    (?:(?P<callsign>[A-Z]{2,}\d{1,2})\s+)?   # optional callsign prefix like RR15
    (?:
        [Ff]\+(?P<fplus>\d{1,3})               # F+40
      | [Ff]-(?P<fminus>\d{1,3})               # F-10
      | playtime\s+(?P<playtime>\d{1,3})\s*(?:min|mike|mikes|m\b)  # playtime 15 min
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Weapons: FOX 1/2/3, splash, TLAM/SM6/AMRAAM + count, launched/remaining/expended
_WEAPONS_RE = re.compile(
    r"""
    (?:
        (?:FOX|fox)\s*(?P<fox>[123])                            # FOX 1/2/3
      | (?P<splash>splash)\s*(?P<splash_count>\d+)?             # splash / splash 2
      | (?P<weapon_type>SM6|SM-6|TLAM|AMRAAM|ESSM|CIWS|SM2|SM-2)
        (?:\s*[xX×]\s*(?P<weapon_count>\d+))?                   # SM6 x4
      | (?P<launched>\d+)\s+(?:launched|expended)                # 2 launched
      | (?P<remaining>\d+)\s+remaining                          # 4 remaining
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Operational status: RTB, inop, gadget bent, degraded, destroyed, on boom, winchester
_STATUS_RE = re.compile(
    r"""
    (?:
        \b(?P<rtb>RTB)\b
      | \b(?P<inop>inop)\b
      | gadget\s+bent
      | \b(?P<degraded>degraded)\b
      | \b(?P<destroyed>destroyed|splashed|killed)\b
      | \bon\s+boom\b
      | \b(?P<winchester>winchester)\b
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Coordinates: bullseye/cigar ###/###, lat/lon decimal, MGRS
_BULLSEYE_RE = re.compile(
    r"(?:bullseye|cigar|BE)\s+(?P<bearing>\d{1,3})\s*/\s*(?P<range>\d{1,4})",
    re.IGNORECASE,
)

_LATLON_RE = re.compile(
    r"(?P<lat>[+-]?\d{1,2}\.\d+)\s*[°]?\s*(?P<lat_dir>[NSns])?\s*[,/]\s*"
    r"(?P<lon>[+-]?\d{1,3}\.\d+)\s*[°]?\s*(?P<lon_dir>[EWew])?",
)

_MGRS_RE = re.compile(
    r"\b(?P<mgrs>\d{1,2}[A-Z]{3}\s*\d{4,10})\b",
)

# --- DELTRON-sourced patterns (from HLT meeting 2026-04-09, slide 10) ---
# These patterns were identified by Jeremy Gwinnup's DELTRON system as
# operationally significant messages their fast path was missing.
# See docs/COMPARISON_DELTRON_2026-04-09.md for context.

# CSAR / emergency: boltout, bail out, CSAR, chopsard (STT mishearing of CSAR)
_CSAR_RE = re.compile(
    r"""
    (?:
        \b(?P<boltout>bolt\s*out|bail\s*(?:out|ed))\b
      | \b(?P<csar>CSAR|chopsard)\b
      | \b(?P<defending>defending)\b
      | \b(?P<eject>eject(?:ed|ing)?)\b
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Crash / emergency: crash, smoke (in cockpit), emergency RTB
_CRASH_RE = re.compile(
    r"""
    (?:
        \b(?P<crash>crash(?:\s+event)?(?:ed)?)\b
      | \bsmoke\s+(?:in\s+)?(?:the\s+)?(?P<smoke>cockpit|cabin)\b
      | \b(?P<emergency_rtb>emergency\s+RTB|RTB\s+emergency|RTB\s+w/?\s*emergency)\b
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Catastrophic brevity codes: BROKEN ARROW, etc.
_BREVITY_CRITICAL_RE = re.compile(
    r"""
    (?:
        \b(?P<broken_arrow>BROKEN\s+ARROW)\b
      | \b(?P<winchester>winchester)\b
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Entity-down patterns: "is down", "8 down", "shot down", "went down"
_ENTITY_DOWN_RE = re.compile(
    r"""
    (?:
        (?P<callsign>[A-Z][A-Za-z]*\s*\d{1,2})\s+
        (?:is|went|going|going)\s+down
      | \b(?P<count>\d+)\s+down\b
      | \bshot\s+down\b
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Threat patterns: SAM active, TACREP with awake/active SAM
_THREAT_RE = re.compile(
    r"""
    (?:
        \b(?P<sam_type>SA-\d{1,2}|S-\d{3}|SAM)\s+(?P<sam_status>active|awake)\b
      | \btacrep\b.*?\b(?P<tacrep_sam>SAM|sam)\s+(?:active|awake)\b
      | \b(?P<gen_sam>\d+(?:st|nd|rd|th)[- ]gen\s+sam)\b
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)


class RegexBackend:
    """Pattern-matching backend for when LLMs are unavailable.

    The minimal degradation tier. Scans messages with compiled regex
    patterns and returns low-confidence CoPUpdates.
    """

    def __init__(self, patterns_path: Path | str | None = None) -> None:
        path = Path(patterns_path) if patterns_path else DEFAULT_PATTERNS_PATH
        self._track_rules: list[re.Pattern] = []
        self._noise_rules: list[re.Pattern] = []
        self._load_patterns(path)

    def _load_patterns(self, path: Path) -> None:
        if not path.exists():
            return
        data = json.loads(path.read_text())
        for rule in data.get("rules", []):
            flags = re.IGNORECASE if "i" in rule.get("flags", "") else 0
            self._track_rules.append(re.compile(rule["pattern"], flags))
        for pat in data.get("noise_patterns", []):
            self._noise_rules.append(re.compile(pat, re.IGNORECASE))

    def is_noise(self, text: str) -> bool:
        """Return True if the message is pure noise (acks, radio checks)."""
        stripped = text.strip()
        return any(r.match(stripped) for r in self._noise_rules)

    def extract_tracks(self, text: str) -> list[str]:
        """Extract track numbers from text using loaded patterns."""
        tracks: list[str] = []
        for rule in self._track_rules:
            for m in rule.finditer(text):
                tn = m.group(1).replace(" ", "")
                if tn not in tracks:
                    tracks.append(tn)
        return tracks

    def extract_fuel(self, text: str) -> list[EntityUpdate]:
        """Extract fuel state mentions."""
        entities: list[EntityUpdate] = []
        for m in _FUEL_RE.finditer(text):
            callsign = m.group("callsign")
            if m.group("fplus"):
                fuel = f"F+{m.group('fplus')}"
            elif m.group("fminus"):
                fuel = f"F-{m.group('fminus')}"
            elif m.group("playtime"):
                fuel = f"playtime {m.group('playtime')} min"
            else:
                continue
            entities.append(EntityUpdate(callsign=callsign, fuel_state=fuel))
        return entities

    def extract_weapons(self, text: str) -> list[EntityUpdate]:
        """Extract weapons-related information."""
        entities: list[EntityUpdate] = []
        for m in _WEAPONS_RE.finditer(text):
            eu = EntityUpdate()
            if m.group("fox"):
                fox_map = {"1": "AIM-7 (semi-active)", "2": "AIM-9 (IR)", "3": "AMRAAM (active)"}
                eu.weapon_type = fox_map.get(m.group("fox"), f"FOX {m.group('fox')}")
                eu.weapon_qty_launched = 1
            elif m.group("splash"):
                eu.operational_status = "DESTROYED"
                count = m.group("splash_count")
                eu.weapon_qty_launched = int(count) if count else 1
            elif m.group("weapon_type"):
                eu.weapon_type = m.group("weapon_type").upper().replace("-", "")
                count = m.group("weapon_count")
                if count:
                    eu.weapon_qty_launched = int(count)
            elif m.group("launched"):
                eu.weapon_qty_launched = int(m.group("launched"))
            elif m.group("remaining"):
                eu.weapon_qty_remaining = int(m.group("remaining"))
            else:
                continue
            entities.append(eu)
        return entities

    def extract_status(self, text: str) -> list[EntityUpdate]:
        """Extract operational status changes."""
        entities: list[EntityUpdate] = []
        for m in _STATUS_RE.finditer(text):
            matched = m.group(0).lower()
            if "rtb" in matched:
                status = "RTB"
            elif "inop" in matched:
                status = "INOP"
            elif "gadget" in matched:
                status = "DEGRADED"
                entities.append(EntityUpdate(operational_status=status, subsystem_status="radar inop"))
                continue
            elif m.group("degraded"):
                status = "DEGRADED"
            elif m.group("destroyed") or "splashed" in matched or "killed" in matched:
                status = "DESTROYED"
            elif "on boom" in matched:
                status = "OPERATIONAL"
                entities.append(EntityUpdate(operational_status=status, metadata={"activity": "aerial refueling"}))
                continue
            elif m.group("winchester"):
                status = "DEGRADED"
                entities.append(EntityUpdate(operational_status=status, metadata={"detail": "winchester (no weapons)"}))
                continue
            else:
                continue
            entities.append(EntityUpdate(operational_status=status))
        return entities

    def extract_coordinates(self, text: str) -> list[EntityUpdate]:
        """Extract coordinate information (bullseye, lat/lon, MGRS)."""
        entities: list[EntityUpdate] = []

        for m in _BULLSEYE_RE.finditer(text):
            entities.append(
                EntityUpdate(
                    bearing=float(m.group("bearing")),
                    range_nm=float(m.group("range")),
                )
            )

        for m in _LATLON_RE.finditer(text):
            lat = float(m.group("lat"))
            lon = float(m.group("lon"))
            if m.group("lat_dir") and m.group("lat_dir").upper() == "S":
                lat = -lat
            if m.group("lon_dir") and m.group("lon_dir").upper() == "W":
                lon = -lon
            entities.append(EntityUpdate(latitude=lat, longitude=lon))

        for m in _MGRS_RE.finditer(text):
            entities.append(EntityUpdate(metadata={"mgrs": m.group("mgrs")}))

        return entities

    # --- DELTRON-sourced extractors (from HLT meeting 2026-04-09, slide 10) ---

    def extract_csar(self, text: str) -> list[EntityUpdate]:
        """Extract CSAR / emergency events: boltout, bail out, CSAR, defending."""
        entities: list[EntityUpdate] = []
        for m in _CSAR_RE.finditer(text):
            if m.group("boltout") or m.group("eject"):
                entities.append(EntityUpdate(operational_status="EJECTED", metadata={"event": "bailout/eject"}))
            elif m.group("csar"):
                entities.append(EntityUpdate(metadata={"event": "CSAR"}))
            elif m.group("defending"):
                entities.append(EntityUpdate(metadata={"event": "defending"}))
        return entities

    def extract_crash(self, text: str) -> list[EntityUpdate]:
        """Extract crash / in-cockpit emergency events."""
        entities: list[EntityUpdate] = []
        for m in _CRASH_RE.finditer(text):
            if m.group("crash"):
                entities.append(EntityUpdate(operational_status="DESTROYED", metadata={"event": "crash"}))
            elif m.group("smoke"):
                entities.append(
                    EntityUpdate(operational_status="DEGRADED", metadata={"event": f"smoke in {m.group('smoke')}"})
                )
            elif m.group("emergency_rtb"):
                entities.append(EntityUpdate(operational_status="RTB", metadata={"event": "emergency RTB"}))
        return entities

    def extract_brevity_critical(self, text: str) -> list[EntityUpdate]:
        """Extract catastrophic brevity codes: BROKEN ARROW, etc."""
        entities: list[EntityUpdate] = []
        for m in _BREVITY_CRITICAL_RE.finditer(text):
            if m.group("broken_arrow"):
                entities.append(EntityUpdate(metadata={"brevity_code": "BROKEN ARROW"}))
            elif m.group("winchester"):
                entities.append(
                    EntityUpdate(operational_status="DEGRADED", metadata={"detail": "winchester (no weapons)"})
                )
        return entities

    def extract_entity_down(self, text: str) -> list[EntityUpdate]:
        """Extract entity-down patterns: 'is down', '8 down', 'shot down'."""
        entities: list[EntityUpdate] = []
        for m in _ENTITY_DOWN_RE.finditer(text):
            callsign = m.group("callsign")
            if callsign:
                entities.append(EntityUpdate(callsign=callsign.strip(), operational_status="DESTROYED"))
            elif m.group("count"):
                entities.append(
                    EntityUpdate(
                        operational_status="DESTROYED",
                        metadata={"count": m.group("count")},
                    )
                )
            else:
                entities.append(EntityUpdate(operational_status="DESTROYED"))
        return entities

    def extract_threats(self, text: str) -> list[EntityUpdate]:
        """Extract threat patterns: SAM active, TACREP with SAM."""
        entities: list[EntityUpdate] = []
        for m in _THREAT_RE.finditer(text):
            if m.group("sam_type"):
                entities.append(
                    EntityUpdate(
                        platform_type=m.group("sam_type"),
                        affiliation="HOSTILE",
                        metadata={"status": m.group("sam_status")},
                    )
                )
            elif m.group("tacrep_sam"):
                entities.append(EntityUpdate(platform_type="SAM", affiliation="HOSTILE", metadata={"status": "active"}))
            elif m.group("gen_sam"):
                entities.append(
                    EntityUpdate(platform_type=m.group("gen_sam"), affiliation="HOSTILE", metadata={"status": "active"})
                )
        return entities

    async def extract(
        self,
        messages: list[dict],
        schema: type[BaseModel],
    ) -> BaseModel:
        """Extract structured data from messages using regex patterns.

        Implements the LLMBackend protocol. Ignores the schema parameter
        and always returns a CoPUpdate with confidence < 0.5.
        """
        labels = {"backend": "regex"}
        metrics.inc("backend_calls_total", labels=labels)

        # Pull the last user message content
        content = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                break

        # Check for noise first
        if self.is_noise(content):
            metrics.inc("backend_successes_total", labels=labels)
            return CoPUpdate(
                update_type=UpdateType.NONE,
                confidence=0.1,
                extraction_method="regex",
                entities=[],
                source_channel="",
                source_speaker="",
                source_message=content,
                timestamp=datetime.now(timezone.utc),
                reasoning="Noise message filtered by regex",
            )

        # Run all extractors
        all_entities: list[EntityUpdate] = []
        update_type = UpdateType.NONE

        # Track numbers
        tracks = self.extract_tracks(content)
        for tn in tracks:
            # Determine prefix-based affiliation
            prefix = ""
            for c in tn:
                if c.isalpha():
                    prefix += c
                else:
                    break
            all_entities.append(EntityUpdate(track_number=tn))
            if update_type == UpdateType.NONE:
                update_type = UpdateType.ENTITY_ID

        # Fuel
        fuel_entities = self.extract_fuel(content)
        if fuel_entities:
            all_entities.extend(fuel_entities)
            if update_type == UpdateType.NONE:
                update_type = UpdateType.FUEL

        # Weapons
        weapon_entities = self.extract_weapons(content)
        if weapon_entities:
            all_entities.extend(weapon_entities)
            if update_type == UpdateType.NONE:
                update_type = UpdateType.WEAPONS

        # Status
        status_entities = self.extract_status(content)
        if status_entities:
            all_entities.extend(status_entities)
            if update_type == UpdateType.NONE:
                update_type = UpdateType.STATUS_CHANGE

        # --- DELTRON-sourced extractors (from HLT meeting 2026-04-09, slide 10) ---
        # These run BEFORE coordinates because operationally significant patterns
        # (CSAR, crash, threats) should take priority over location classification
        # when a message contains both (e.g., "SA-21 active cigar 316/398").

        # CSAR / emergency
        csar_entities = self.extract_csar(content)
        if csar_entities:
            all_entities.extend(csar_entities)
            if update_type == UpdateType.NONE:
                update_type = UpdateType.CSAR

        # Crash / smoke / emergency RTB
        crash_entities = self.extract_crash(content)
        if crash_entities:
            all_entities.extend(crash_entities)
            if update_type == UpdateType.NONE:
                update_type = UpdateType.STATUS_CHANGE

        # Catastrophic brevity codes
        brevity_entities = self.extract_brevity_critical(content)
        if brevity_entities:
            all_entities.extend(brevity_entities)
            if update_type == UpdateType.NONE:
                update_type = UpdateType.STATUS_CHANGE

        # Entity-down patterns
        entity_down = self.extract_entity_down(content)
        if entity_down:
            all_entities.extend(entity_down)
            if update_type == UpdateType.NONE:
                update_type = UpdateType.STATUS_CHANGE

        # Threat patterns (SAM active, TACREP)
        threat_entities = self.extract_threats(content)
        if threat_entities:
            all_entities.extend(threat_entities)
            if update_type == UpdateType.NONE:
                update_type = UpdateType.THREAT

        # Coordinates (run after CSAR/crash/threat so those take priority for type)
        coord_entities = self.extract_coordinates(content)
        if coord_entities:
            all_entities.extend(coord_entities)
            if update_type == UpdateType.NONE:
                update_type = UpdateType.LOCATION

        # Determine confidence: more extractors matched = slightly higher confidence
        # but always < 0.5
        extractor_hits = sum(
            1
            for group in [
                tracks,
                fuel_entities,
                weapon_entities,
                status_entities,
                coord_entities,
                csar_entities,
                crash_entities,
                brevity_entities,
                entity_down,
                threat_entities,
            ]
            if group
        )
        confidence = min(0.1 + 0.08 * extractor_hits, 0.45)

        metrics.inc("backend_successes_total", labels=labels)
        return CoPUpdate(
            update_type=update_type,
            confidence=confidence,
            extraction_method="regex",
            entities=all_entities,
            source_channel="",
            source_speaker="",
            source_message=content,
            timestamp=datetime.now(timezone.utc),
            reasoning=f"Regex extraction: {extractor_hits} extractor(s) matched" if extractor_hits else None,
        )

    def __repr__(self) -> str:
        return f"RegexBackend(track_rules={len(self._track_rules)}, noise_rules={len(self._noise_rules)})"

"""Tests for the equipment/weapon catalog (MACE integration, issue #48)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chat_to_cop.reference.equipment_catalog import EquipmentCatalog

# ---------------------------------------------------------------------------
# Test fixture data
# ---------------------------------------------------------------------------

SAMPLE_EQUIPMENT_JSON = [
    {
        "name": "AIM-120 AMRAAM",
        "designation": "AIM-120",
        "type": "air-to-air missile",
        "category": "missile",
        "description": "Medium-range active radar homing air-to-air missile",
        "aliases": "AMRAAM|Slammer",
    },
    {
        "name": "AGM-158B JASSM-ER",
        "designation": "AGM-158B",
        "type": "cruise missile",
        "category": "air-to-surface missile",
        "description": "Joint Air-to-Surface Standoff Missile, Extended Range",
        "aliases": "JASSM-ER",
    },
    {
        "name": "GBU-31 JDAM",
        "designation": "GBU-31",
        "type": "guided bomb",
        "category": "bomb",
        "description": "2,000 lb GPS-guided bomb (Mk-84 body)",
        "aliases": "JDAM",
    },
    {
        "name": "GBU-24 PAVEWAY III",
        "designation": "GBU-24",
        "type": "laser-guided bomb",
        "category": "bomb",
        "description": "2,000 lb laser-guided bomb (Mk-84 body)",
        "aliases": "PAVEWAY III",
    },
    {
        "name": "AGM-88 HARM",
        "designation": "AGM-88",
        "type": "anti-radiation missile",
        "category": "missile",
        "description": "High-speed Anti-Radiation Missile for SEAD",
        "aliases": "HARM",
    },
]

SAMPLE_EQUIPMENT_CSV = """\
name,designation,type,category,description,aliases
AIM-9X Sidewinder,AIM-9X,air-to-air missile,missile,Short-range IR missile,Sidewinder|AIM-9
GBU-39 SDB,GBU-39,guided bomb,bomb,250 lb Small Diameter Bomb,SDB|Small Diameter Bomb
"""


@pytest.fixture
def json_path(tmp_path: Path) -> Path:
    p = tmp_path / "equipment.json"
    p.write_text(json.dumps(SAMPLE_EQUIPMENT_JSON), encoding="utf-8")
    return p


@pytest.fixture
def csv_path(tmp_path: Path) -> Path:
    p = tmp_path / "equipment.csv"
    p.write_text(SAMPLE_EQUIPMENT_CSV, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Load tests
# ---------------------------------------------------------------------------


class TestEquipmentCatalogLoad:
    def test_load_from_json(self, json_path: Path) -> None:
        cat = EquipmentCatalog()
        count = cat.load_from_json(json_path)
        assert count == 5
        assert cat.count == 5

    def test_load_from_csv(self, csv_path: Path) -> None:
        cat = EquipmentCatalog()
        count = cat.load_from_csv(csv_path)
        assert count == 2
        assert cat.count == 2

    def test_load_missing_json(self, tmp_path: Path) -> None:
        cat = EquipmentCatalog()
        count = cat.load_from_json(tmp_path / "nonexistent.json")
        assert count == 0
        assert cat.count == 0

    def test_load_missing_csv(self, tmp_path: Path) -> None:
        cat = EquipmentCatalog()
        count = cat.load_from_csv(tmp_path / "nonexistent.csv")
        assert count == 0
        assert cat.count == 0

    def test_load_invalid_json(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.json"
        p.write_text("not valid json {{{", encoding="utf-8")
        cat = EquipmentCatalog()
        count = cat.load_from_json(p)
        assert count == 0

    def test_load_json_wrong_type(self, tmp_path: Path) -> None:
        """JSON is valid but not a list."""
        p = tmp_path / "bad.json"
        p.write_text(json.dumps({"name": "oops"}), encoding="utf-8")
        cat = EquipmentCatalog()
        count = cat.load_from_json(p)
        assert count == 0

    def test_load_json_skips_entries_without_name(self, tmp_path: Path) -> None:
        data = [{"designation": "X-99"}, {"name": "Valid Weapon", "designation": "VW-1"}]
        p = tmp_path / "partial.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        cat = EquipmentCatalog()
        count = cat.load_from_json(p)
        assert count == 1


# ---------------------------------------------------------------------------
# Lookup tests
# ---------------------------------------------------------------------------


class TestWeaponLookup:
    def test_exact_name_match(self, json_path: Path) -> None:
        cat = EquipmentCatalog()
        cat.load_from_json(json_path)
        result = cat.lookup_weapon("AIM-120 AMRAAM")
        assert result is not None
        assert result["designation"] == "AIM-120"
        assert result["type"] == "air-to-air missile"

    def test_designation_match(self, json_path: Path) -> None:
        cat = EquipmentCatalog()
        cat.load_from_json(json_path)
        result = cat.lookup_weapon("AGM-158B")
        assert result is not None
        assert result["name"] == "AGM-158B JASSM-ER"

    def test_alias_match(self, json_path: Path) -> None:
        cat = EquipmentCatalog()
        cat.load_from_json(json_path)
        result = cat.lookup_weapon("HARM")
        assert result is not None
        assert result["designation"] == "AGM-88"

    def test_alias_match_amraam(self, json_path: Path) -> None:
        cat = EquipmentCatalog()
        cat.load_from_json(json_path)
        result = cat.lookup_weapon("AMRAAM")
        assert result is not None
        assert result["name"] == "AIM-120 AMRAAM"

    def test_alias_match_slammer(self, json_path: Path) -> None:
        cat = EquipmentCatalog()
        cat.load_from_json(json_path)
        result = cat.lookup_weapon("Slammer")
        assert result is not None
        assert result["name"] == "AIM-120 AMRAAM"

    def test_case_insensitive(self, json_path: Path) -> None:
        cat = EquipmentCatalog()
        cat.load_from_json(json_path)
        result = cat.lookup_weapon("aim-120 amraam")
        assert result is not None
        assert result["designation"] == "AIM-120"

    def test_fuzzy_substring_match(self, json_path: Path) -> None:
        """'AIM-120' as a query should match 'AIM-120 AMRAAM' via substring."""
        cat = EquipmentCatalog()
        cat.load_from_json(json_path)
        result = cat.lookup_weapon("AIM-120")
        assert result is not None
        assert "AMRAAM" in result["name"]

    def test_unknown_weapon_returns_none(self, json_path: Path) -> None:
        cat = EquipmentCatalog()
        cat.load_from_json(json_path)
        assert cat.lookup_weapon("BFG-9000") is None

    def test_empty_query_returns_none(self, json_path: Path) -> None:
        cat = EquipmentCatalog()
        cat.load_from_json(json_path)
        assert cat.lookup_weapon("") is None
        assert cat.lookup_weapon(None) is None

    def test_lookup_returns_copy(self, json_path: Path) -> None:
        """Mutating the result should not affect the catalog."""
        cat = EquipmentCatalog()
        cat.load_from_json(json_path)
        result = cat.lookup_weapon("HARM")
        result["name"] = "MODIFIED"
        assert cat.lookup_weapon("HARM")["name"] == "AGM-88 HARM"

    def test_empty_catalog_returns_none(self) -> None:
        cat = EquipmentCatalog()
        assert cat.lookup_weapon("anything") is None

    def test_csv_alias_match(self, csv_path: Path) -> None:
        cat = EquipmentCatalog()
        cat.load_from_csv(csv_path)
        result = cat.lookup_weapon("Sidewinder")
        assert result is not None
        assert result["designation"] == "AIM-9X"

    def test_csv_multi_alias(self, csv_path: Path) -> None:
        """AIM-9 should match AIM-9X Sidewinder via alias."""
        cat = EquipmentCatalog()
        cat.load_from_csv(csv_path)
        result = cat.lookup_weapon("AIM-9")
        assert result is not None
        assert result["name"] == "AIM-9X Sidewinder"


# ---------------------------------------------------------------------------
# List tests
# ---------------------------------------------------------------------------


class TestListWeapons:
    def test_list_weapons(self, json_path: Path) -> None:
        cat = EquipmentCatalog()
        cat.load_from_json(json_path)
        weapons = cat.list_weapons()
        assert len(weapons) == 5
        assert "AGM-88 HARM" in weapons
        assert "AIM-120 AMRAAM" in weapons

    def test_list_weapons_sorted(self, json_path: Path) -> None:
        cat = EquipmentCatalog()
        cat.load_from_json(json_path)
        weapons = cat.list_weapons()
        assert weapons == sorted(weapons)

    def test_list_weapons_empty(self) -> None:
        cat = EquipmentCatalog()
        assert cat.list_weapons() == []


# ---------------------------------------------------------------------------
# Config integration
# ---------------------------------------------------------------------------


class TestEquipmentCatalogConfig:
    def test_config_default_empty(self) -> None:
        from chat_to_cop.config import ReferenceDataConfig

        config = ReferenceDataConfig()
        assert config.equipment_catalog_path == ""

    def test_config_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from chat_to_cop.config import ReferenceDataConfig

        monkeypatch.setenv("CHAT_TO_COP_EQUIPMENT_CATALOG_PATH", "/data/weapons.json")
        config = ReferenceDataConfig()
        assert config.equipment_catalog_path == "/data/weapons.json"

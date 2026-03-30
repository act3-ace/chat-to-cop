"""Tests for reference data: entity catalog and theater geometry."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chat_to_cop.models.cop_update import EntityUpdate
from chat_to_cop.reference.entity_catalog import EntityCatalog
from chat_to_cop.reference.theater_geometry import TheaterGeometry, _centroid, _point_in_polygon

# ---------------------------------------------------------------------------
# Inline test fixtures — no dependency on the equifinality repo
# ---------------------------------------------------------------------------

SAMPLE_BLUE_CSV = """\
asset_id,platform_type,super_category,is_generic
THOR,F35A,AIRBORNE EFFECTORS,False
HADES,B1B,AIRBORNE EFFECTORS,False
ORCA,E2D,AIRBORNE EFFECTORS,False
BANG,KC135,AIRBORNE EFFECTORS,False
FORD,CVN,NAVAL,False
F35A,F35A,AIRBORNE EFFECTORS,True
"""

SAMPLE_RED_CSV = """\
target_class,parent_classes,aliases,notes
J-20,Fighter 5th gen|Fighter|Air,,Chinese 5th gen stealth fighter (Chengdu)
H-20,Bomber|Air,,Chinese stealth bomber
Renhai CG,Cruiser|Surface Combatant|Maritime,,Type 055 guided-missile cruiser
Geo Comm,Satellite|Space,GEO Comm Sat,Geosynchronous communications satellite
"""

# Simple square polygon: corners at (0,0), (0,10), (10,10), (10,0)
SAMPLE_GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {
                "name": "ALPHA BMA",
                "type": "BMA",
                "use": "JOA",
                "description": "Test Battle Management Area",
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [0.0, 0.0],
                        [10.0, 0.0],
                        [10.0, 10.0],
                        [0.0, 10.0],
                        [0.0, 0.0],
                    ]
                ],
            },
        },
        {
            "type": "Feature",
            "properties": {
                "name": "BRAVO BMA",
                "type": "BMA",
                "use": "JOA",
                "description": "Second test BMA",
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [20.0, 20.0],
                        [30.0, 20.0],
                        [30.0, 30.0],
                        [20.0, 30.0],
                        [20.0, 20.0],
                    ]
                ],
            },
        },
        {
            "type": "Feature",
            "properties": {
                "name": "RAIDERS",
                "type": "Tanker Track",
                "use": "AAR",
                "description": "Test tanker track",
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [50.0, 50.0],
                        [55.0, 50.0],
                        [55.0, 52.0],
                        [50.0, 52.0],
                        [50.0, 50.0],
                    ]
                ],
            },
        },
    ],
}


@pytest.fixture
def blue_csv_path(tmp_path: Path) -> Path:
    p = tmp_path / "schema_assets.csv"
    p.write_text(SAMPLE_BLUE_CSV, encoding="utf-8")
    return p


@pytest.fixture
def red_csv_path(tmp_path: Path) -> Path:
    p = tmp_path / "dash_target_taxonomy.csv"
    p.write_text(SAMPLE_RED_CSV, encoding="utf-8")
    return p


@pytest.fixture
def catalog_dir(tmp_path: Path) -> Path:
    (tmp_path / "schema_assets.csv").write_text(SAMPLE_BLUE_CSV, encoding="utf-8")
    (tmp_path / "dash_target_taxonomy.csv").write_text(SAMPLE_RED_CSV, encoding="utf-8")
    return tmp_path


@pytest.fixture
def geojson_path(tmp_path: Path) -> Path:
    p = tmp_path / "theater_geometry_polygons.geojson"
    p.write_text(json.dumps(SAMPLE_GEOJSON), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# EntityCatalog tests
# ---------------------------------------------------------------------------


class TestEntityCatalogLoad:
    def test_load_blue_assets(self, blue_csv_path: Path) -> None:
        cat = EntityCatalog()
        count = cat.load_blue_assets(blue_csv_path)
        assert count == 6
        assert cat.callsign_count == 6

    def test_load_red_targets(self, red_csv_path: Path) -> None:
        cat = EntityCatalog()
        count = cat.load_red_targets(red_csv_path)
        assert count == 4
        assert cat.target_count == 4

    def test_load_directory(self, catalog_dir: Path) -> None:
        cat = EntityCatalog()
        cat.load_directory(catalog_dir)
        assert cat.callsign_count == 6
        assert cat.target_count == 4

    def test_load_missing_file_returns_zero(self, tmp_path: Path) -> None:
        cat = EntityCatalog()
        count = cat.load_blue_assets(tmp_path / "nonexistent.csv")
        assert count == 0
        assert cat.callsign_count == 0

    def test_load_directory_partial(self, tmp_path: Path) -> None:
        """Only blue assets exist — red targets file missing."""
        (tmp_path / "schema_assets.csv").write_text(SAMPLE_BLUE_CSV, encoding="utf-8")
        cat = EntityCatalog()
        cat.load_directory(tmp_path)
        assert cat.callsign_count == 6
        assert cat.target_count == 0


class TestCallsignLookup:
    def test_exact_match(self, blue_csv_path: Path) -> None:
        cat = EntityCatalog()
        cat.load_blue_assets(blue_csv_path)
        result = cat.lookup_callsign("THOR")
        assert result is not None
        assert result["platform_type"] == "F35A"
        assert result["affiliation"] == "FRIEND"
        assert result["super_category"] == "AIRBORNE EFFECTORS"

    def test_case_insensitive(self, blue_csv_path: Path) -> None:
        cat = EntityCatalog()
        cat.load_blue_assets(blue_csv_path)
        result = cat.lookup_callsign("thor")
        assert result is not None
        assert result["platform_type"] == "F35A"

    def test_numbered_variant(self, blue_csv_path: Path) -> None:
        """THOR11 should match THOR by stripping trailing digits."""
        cat = EntityCatalog()
        cat.load_blue_assets(blue_csv_path)
        result = cat.lookup_callsign("THOR11")
        assert result is not None
        assert result["platform_type"] == "F35A"

    def test_numbered_variant_bang72(self, blue_csv_path: Path) -> None:
        """BANG72 should match BANG."""
        cat = EntityCatalog()
        cat.load_blue_assets(blue_csv_path)
        result = cat.lookup_callsign("BANG72")
        assert result is not None
        assert result["platform_type"] == "KC135"

    def test_unknown_callsign_returns_none(self, blue_csv_path: Path) -> None:
        cat = EntityCatalog()
        cat.load_blue_assets(blue_csv_path)
        assert cat.lookup_callsign("UNKNOWN") is None

    def test_empty_callsign(self, blue_csv_path: Path) -> None:
        cat = EntityCatalog()
        cat.load_blue_assets(blue_csv_path)
        assert cat.lookup_callsign("") is None
        assert cat.lookup_callsign(None) is None

    def test_lookup_returns_copy(self, blue_csv_path: Path) -> None:
        """Mutating the returned dict should not affect the catalog."""
        cat = EntityCatalog()
        cat.load_blue_assets(blue_csv_path)
        result = cat.lookup_callsign("THOR")
        result["platform_type"] = "MODIFIED"
        assert cat.lookup_callsign("THOR")["platform_type"] == "F35A"


class TestTrackLookup:
    def test_direct_target_class(self, red_csv_path: Path) -> None:
        cat = EntityCatalog()
        cat.load_red_targets(red_csv_path)
        result = cat.lookup_track("J-20")
        assert result is not None
        assert result["affiliation"] == "HOSTILE"
        assert "Fighter" in result["parent_classes"]

    def test_alias_match(self, red_csv_path: Path) -> None:
        """GEO Comm Sat is an alias for Geo Comm."""
        cat = EntityCatalog()
        cat.load_red_targets(red_csv_path)
        result = cat.lookup_track("GEO Comm Sat")
        assert result is not None
        assert result["target_class"] == "Geo Comm"

    def test_case_insensitive_track(self, red_csv_path: Path) -> None:
        cat = EntityCatalog()
        cat.load_red_targets(red_csv_path)
        result = cat.lookup_track("j-20")
        assert result is not None

    def test_unknown_track(self, red_csv_path: Path) -> None:
        cat = EntityCatalog()
        cat.load_red_targets(red_csv_path)
        assert cat.lookup_track("TM999") is None

    def test_empty_track(self, red_csv_path: Path) -> None:
        cat = EntityCatalog()
        cat.load_red_targets(red_csv_path)
        assert cat.lookup_track("") is None
        assert cat.lookup_track(None) is None


class TestEnrichEntity:
    def test_enrich_blue_callsign(self, catalog_dir: Path) -> None:
        cat = EntityCatalog()
        cat.load_directory(catalog_dir)
        entity = EntityUpdate(callsign="HADES31")
        enriched = cat.enrich_entity(entity)
        assert enriched.platform_type == "B1B"
        assert enriched.affiliation == "FRIEND"
        assert enriched.metadata.get("super_category") == "AIRBORNE EFFECTORS"

    def test_enrich_red_track(self, catalog_dir: Path) -> None:
        cat = EntityCatalog()
        cat.load_directory(catalog_dir)
        entity = EntityUpdate(track_number="J-20")
        enriched = cat.enrich_entity(entity)
        assert enriched.platform_type == "J-20"
        assert enriched.affiliation == "HOSTILE"
        assert "Fighter" in enriched.metadata.get("target_hierarchy", "")

    def test_enrich_preserves_existing_fields(self, catalog_dir: Path) -> None:
        """If platform_type is already set, don't overwrite."""
        cat = EntityCatalog()
        cat.load_directory(catalog_dir)
        entity = EntityUpdate(callsign="THOR11", platform_type="F-35A CUSTOM", affiliation="FRIEND")
        enriched = cat.enrich_entity(entity)
        assert enriched.platform_type == "F-35A CUSTOM"
        assert enriched.affiliation == "FRIEND"

    def test_enrich_unknown_entity_is_noop(self, catalog_dir: Path) -> None:
        cat = EntityCatalog()
        cat.load_directory(catalog_dir)
        entity = EntityUpdate(callsign="UNKNOWN99")
        enriched = cat.enrich_entity(entity)
        assert enriched.platform_type is None
        assert enriched.affiliation is None

    def test_enrich_empty_catalog(self) -> None:
        """Empty catalog (no files loaded) should be a no-op."""
        cat = EntityCatalog()
        entity = EntityUpdate(callsign="THOR11")
        enriched = cat.enrich_entity(entity)
        assert enriched.platform_type is None

    def test_enrich_callsign_takes_priority_over_track(self, catalog_dir: Path) -> None:
        """If both callsign and track_number are set, callsign (Blue) wins."""
        cat = EntityCatalog()
        cat.load_directory(catalog_dir)
        entity = EntityUpdate(callsign="ORCA", track_number="J-20")
        enriched = cat.enrich_entity(entity)
        assert enriched.platform_type == "E2D"
        assert enriched.affiliation == "FRIEND"


# ---------------------------------------------------------------------------
# TheaterGeometry tests
# ---------------------------------------------------------------------------


class TestTheaterGeometryLoad:
    def test_load_geojson(self, geojson_path: Path) -> None:
        geo = TheaterGeometry()
        count = geo.load_geometry(geojson_path)
        assert count == 3
        assert geo.feature_count == 3

    def test_load_missing_file(self, tmp_path: Path) -> None:
        geo = TheaterGeometry()
        count = geo.load_geometry(tmp_path / "missing.geojson")
        assert count == 0
        assert geo.feature_count == 0

    def test_load_invalid_geojson_type(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.geojson"
        p.write_text(json.dumps({"type": "Feature"}), encoding="utf-8")
        geo = TheaterGeometry()
        count = geo.load_geometry(p)
        assert count == 0


class TestResolveBMA:
    def test_exact_match(self, geojson_path: Path) -> None:
        geo = TheaterGeometry()
        geo.load_geometry(geojson_path)
        result = geo.resolve_bma("ALPHA BMA")
        assert result is not None
        assert result["name"] == "ALPHA BMA"
        assert result["type"] == "BMA"
        assert "centroid_lat" in result
        assert "centroid_lon" in result

    def test_case_insensitive(self, geojson_path: Path) -> None:
        geo = TheaterGeometry()
        geo.load_geometry(geojson_path)
        result = geo.resolve_bma("alpha bma")
        assert result is not None
        assert result["name"] == "ALPHA BMA"

    def test_append_bma_suffix(self, geojson_path: Path) -> None:
        """'ALPHA' should match 'ALPHA BMA'."""
        geo = TheaterGeometry()
        geo.load_geometry(geojson_path)
        result = geo.resolve_bma("ALPHA")
        assert result is not None
        assert result["name"] == "ALPHA BMA"

    def test_unknown_bma(self, geojson_path: Path) -> None:
        geo = TheaterGeometry()
        geo.load_geometry(geojson_path)
        assert geo.resolve_bma("NONEXISTENT") is None

    def test_empty_name(self, geojson_path: Path) -> None:
        geo = TheaterGeometry()
        geo.load_geometry(geojson_path)
        assert geo.resolve_bma("") is None
        assert geo.resolve_bma(None) is None

    def test_centroid_correctness(self, geojson_path: Path) -> None:
        """ALPHA BMA is a 0-10 square, centroid should be at (5, 5)."""
        geo = TheaterGeometry()
        geo.load_geometry(geojson_path)
        result = geo.resolve_bma("ALPHA BMA")
        # Centroid of [0,0],[10,0],[10,10],[0,10] = (5,5) in lat/lon
        assert abs(result["centroid_lat"] - 5.0) < 0.01
        assert abs(result["centroid_lon"] - 5.0) < 0.01


class TestPointInBMA:
    def test_point_inside(self, geojson_path: Path) -> None:
        geo = TheaterGeometry()
        geo.load_geometry(geojson_path)
        result = geo.point_in_bma(lat=5.0, lon=5.0)
        assert result == "ALPHA BMA"

    def test_point_in_second_bma(self, geojson_path: Path) -> None:
        geo = TheaterGeometry()
        geo.load_geometry(geojson_path)
        result = geo.point_in_bma(lat=25.0, lon=25.0)
        assert result == "BRAVO BMA"

    def test_point_outside_all(self, geojson_path: Path) -> None:
        geo = TheaterGeometry()
        geo.load_geometry(geojson_path)
        result = geo.point_in_bma(lat=99.0, lon=99.0)
        assert result is None

    def test_point_in_tanker_track_not_returned(self, geojson_path: Path) -> None:
        """point_in_bma only checks BMA features, not tanker tracks."""
        geo = TheaterGeometry()
        geo.load_geometry(geojson_path)
        result = geo.point_in_bma(lat=51.0, lon=52.0)
        assert result is None

    def test_point_on_edge(self, geojson_path: Path) -> None:
        """Point on the boundary — behavior is implementation-defined but should not crash."""
        geo = TheaterGeometry()
        geo.load_geometry(geojson_path)
        # Just verify no exception
        geo.point_in_bma(lat=0.0, lon=0.0)


class TestListBMAs:
    def test_list_bmas(self, geojson_path: Path) -> None:
        geo = TheaterGeometry()
        geo.load_geometry(geojson_path)
        bmas = geo.list_bmas()
        assert bmas == ["ALPHA BMA", "BRAVO BMA"]

    def test_list_bmas_empty(self) -> None:
        geo = TheaterGeometry()
        assert geo.list_bmas() == []

    def test_list_features_all(self, geojson_path: Path) -> None:
        geo = TheaterGeometry()
        geo.load_geometry(geojson_path)
        features = geo.list_features()
        assert len(features) == 3
        assert "RAIDERS" in features

    def test_list_features_by_type(self, geojson_path: Path) -> None:
        geo = TheaterGeometry()
        geo.load_geometry(geojson_path)
        tankers = geo.list_features("Tanker Track")
        assert tankers == ["RAIDERS"]


# ---------------------------------------------------------------------------
# Low-level geometry function tests
# ---------------------------------------------------------------------------


class TestPointInPolygon:
    def test_inside_square(self) -> None:
        square = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]
        assert _point_in_polygon(5, 5, square) is True

    def test_outside_square(self) -> None:
        square = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]
        assert _point_in_polygon(15, 15, square) is False

    def test_inside_triangle(self) -> None:
        triangle = [[0, 0], [10, 0], [5, 10], [0, 0]]
        assert _point_in_polygon(3, 5, triangle) is True

    def test_outside_triangle(self) -> None:
        triangle = [[0, 0], [10, 0], [5, 10], [0, 0]]
        # Point at lat=11, lon=5 is above the triangle apex at lat=10
        assert _point_in_polygon(11, 5, triangle) is False


class TestCentroid:
    def test_square_centroid(self) -> None:
        square = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]
        lat, lon = _centroid(square)
        assert abs(lat - 5.0) < 0.01
        assert abs(lon - 5.0) < 0.01

    def test_triangle_centroid(self) -> None:
        triangle = [[0, 0], [6, 0], [3, 6], [0, 0]]
        lat, lon = _centroid(triangle)
        assert abs(lat - 2.0) < 0.01
        assert abs(lon - 3.0) < 0.01

    def test_empty_polygon(self) -> None:
        lat, lon = _centroid([])
        assert lat == 0.0
        assert lon == 0.0


# ---------------------------------------------------------------------------
# Config integration test
# ---------------------------------------------------------------------------


class TestReferenceDataConfig:
    def test_config_defaults(self) -> None:
        from chat_to_cop.config import PipelineConfig

        config = PipelineConfig()
        assert config.reference.entity_catalog_dir == ""
        assert config.reference.geometry_path == ""

    def test_config_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from chat_to_cop.config import ReferenceDataConfig

        monkeypatch.setenv("CHAT_TO_COP_ENTITY_CATALOG_DIR", "/some/path")
        monkeypatch.setenv("CHAT_TO_COP_GEOMETRY_PATH", "/some/geo.geojson")
        config = ReferenceDataConfig()
        assert config.entity_catalog_dir == "/some/path"
        assert config.geometry_path == "/some/geo.geojson"

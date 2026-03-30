"""Theater geometry: load GeoJSON and resolve BMA/tanker track names.

Loads theater_geometry_polygons.geojson from the equifinality repo. Provides
spatial lookups: resolve a BMA name to its centroid/polygon, test whether a
coordinate falls inside a BMA, and list all named areas.

Uses simple ray-casting for point-in-polygon — no external geo libraries needed.

The geometry is optional and configured via CHAT_TO_COP_GEOMETRY_PATH.
"""

from __future__ import annotations

import json
from pathlib import Path

from loguru import logger


def _point_in_polygon(lat: float, lon: float, polygon: list[list[float]]) -> bool:
    """Ray-casting algorithm for point-in-polygon test.

    polygon is a list of [lon, lat] pairs (GeoJSON ordering).
    Returns True if the point (lat, lon) is inside the polygon.
    """
    n = len(polygon)
    inside = False
    j = n - 1
    for i in range(n):
        # polygon[i] is [lon, lat]
        xi, yi = polygon[i][0], polygon[i][1]
        xj, yj = polygon[j][0], polygon[j][1]
        # Test if the ray from (lon, lat) going right crosses edge (i, j)
        if ((yi > lat) != (yj > lat)) and (lon < (xj - xi) * (lat - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _centroid(polygon: list[list[float]]) -> tuple[float, float]:
    """Compute the centroid of a polygon as simple average of vertices.

    polygon is a list of [lon, lat] pairs.
    Returns (lat, lon) in decimal degrees.
    """
    # Skip the closing vertex if it duplicates the first
    pts = polygon
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    if not pts:
        return (0.0, 0.0)
    avg_lon = sum(p[0] for p in pts) / len(pts)
    avg_lat = sum(p[1] for p in pts) / len(pts)
    return (avg_lat, avg_lon)


class TheaterGeometry:
    """Spatial reference data for theater areas (BMAs, tanker tracks, holding areas).

    Loads GeoJSON FeatureCollection. Each feature has properties.name,
    properties.type, and a Polygon geometry.
    """

    def __init__(self) -> None:
        # name (upper) -> {name, type, use, description, polygon, centroid_lat, centroid_lon}
        self._features: dict[str, dict] = {}

    @property
    def feature_count(self) -> int:
        return len(self._features)

    def load_geometry(self, path: str | Path) -> int:
        """Load theater geometry from a GeoJSON file.

        Expected format: GeoJSON FeatureCollection with Polygon features.
        Each feature must have properties.name and geometry.coordinates.

        Returns the number of features loaded.
        """
        path = Path(path)
        if not path.exists():
            logger.warning("Theater geometry file not found: {}", path)
            return 0

        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        if data.get("type") != "FeatureCollection":
            logger.warning("GeoJSON is not a FeatureCollection: {}", path)
            return 0

        count = 0
        for feature in data.get("features", []):
            props = feature.get("properties", {})
            name = props.get("name", "").strip()
            if not name:
                continue

            geom = feature.get("geometry", {})
            if geom.get("type") != "Polygon":
                logger.debug("Skipping non-Polygon feature: {}", name)
                continue

            coords = geom.get("coordinates", [])
            if not coords or not coords[0]:
                continue

            # GeoJSON Polygon: coordinates[0] is the outer ring
            ring = coords[0]
            clat, clon = _centroid(ring)

            key = name.upper()
            self._features[key] = {
                "name": name,
                "type": props.get("type", ""),
                "use": props.get("use", ""),
                "description": props.get("description", ""),
                "polygon": ring,
                "centroid_lat": clat,
                "centroid_lon": clon,
            }
            count += 1

        logger.info("Loaded {} theater geometry features from {}", count, path)
        return count

    def resolve_bma(self, name: str) -> dict | None:
        """Resolve a BMA (or other area) name to its boundary info.

        Returns a dict with name, type, centroid_lat, centroid_lon, polygon,
        or None if not found. Matching is case-insensitive. Also tries
        appending " BMA" if the bare name doesn't match (e.g. "Hydro" won't
        match since BMAs are named "HYDRO BMA" — but "MANDALAY" -> "MANDALAY BMA").
        """
        if not name:
            return None
        key = name.upper().strip()

        if key in self._features:
            return self._make_result(self._features[key])

        # Try appending " BMA"
        bma_key = key + " BMA"
        if bma_key in self._features:
            return self._make_result(self._features[bma_key])

        return None

    def point_in_bma(self, lat: float, lon: float) -> str | None:
        """Return the name of the BMA containing the given coordinate.

        Only checks features with type "BMA". Returns the first match
        (BMAs should not overlap in a well-formed ACO).
        Returns None if the point is outside all BMAs.
        """
        for entry in self._features.values():
            if entry["type"] != "BMA":
                continue
            if _point_in_polygon(lat, lon, entry["polygon"]):
                return entry["name"]
        return None

    def list_bmas(self) -> list[str]:
        """Return all BMA names (sorted alphabetically)."""
        return sorted(entry["name"] for entry in self._features.values() if entry["type"] == "BMA")

    def list_features(self, feature_type: str | None = None) -> list[str]:
        """Return all feature names, optionally filtered by type.

        Args:
            feature_type: Filter by type (e.g. "BMA", "Tanker Track", "Holding Area").
                         None returns all features.
        """
        if feature_type is None:
            return sorted(entry["name"] for entry in self._features.values())
        return sorted(
            entry["name"] for entry in self._features.values() if entry["type"].upper() == feature_type.upper()
        )

    def _make_result(self, entry: dict) -> dict:
        """Build a result dict from an internal feature entry."""
        return {
            "name": entry["name"],
            "type": entry["type"],
            "use": entry["use"],
            "description": entry["description"],
            "centroid_lat": entry["centroid_lat"],
            "centroid_lon": entry["centroid_lon"],
            "polygon": entry["polygon"],
        }

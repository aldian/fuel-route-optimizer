"""USA geographic boundary validation and domestic route verification."""
import json
import math
from pathlib import Path
from typing import Any
from django.conf import settings

# Contiguous 48 States + DC bounding box
CONUS_BBOX = (24.396308, -124.848974, 49.384358, -66.885444)
# Alaska bounding box
ALASKA_BBOX = (51.214183, -179.148909, 71.365162, -129.979506)
# Hawaii bounding box
HAWAII_BBOX = (18.910361, -160.247059, 22.235643, -154.806773)


class USBoundaryValidator:
    """Validates points and polyline trajectories against official US borders."""

    _instance: "USBoundaryValidator | None" = None

    def __init__(self, geojson_path: Path | str | None = None) -> None:
        self.geojson_path = Path(geojson_path or settings.BASE_DIR / "data" / "geo" / "us_states_boundary.geojson")
        self._polygons: list[list[list[tuple[float, float]]]] = []
        self._state_names: list[str] = []
        self._load_polygons()

    @classmethod
    def get_instance(cls) -> "USBoundaryValidator":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _load_polygons(self) -> None:
        if not self.geojson_path.exists():
            return

        with open(self.geojson_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        for feature in data.get("features", []):
            name = feature.get("properties", {}).get("name", "")
            geom = feature.get("geometry", {})
            geom_type = geom.get("type", "")
            coords = geom.get("coordinates", [])

            if geom_type == "Polygon":
                # coords: list of rings [[[lon, lat], ...]]
                rings = [[(float(pt[1]), float(pt[0])) for pt in ring] for ring in coords]
                self._polygons.append(rings)
                self._state_names.append(name)
            elif geom_type == "MultiPolygon":
                # coords: list of polygons [[[[lon, lat], ...]]]
                for poly in coords:
                    rings = [[(float(pt[1]), float(pt[0])) for pt in ring] for ring in poly]
                    self._polygons.append(rings)
                    self._state_names.append(name)

    @staticmethod
    def _in_bbox(lat: float, lon: float, bbox: tuple[float, float, float, float], margin: float = 0.1) -> bool:
        min_lat, min_lon, max_lat, max_lon = bbox
        return (min_lat - margin) <= lat <= (max_lat + margin) and (min_lon - margin) <= lon <= (max_lon + margin)

    @staticmethod
    def _point_in_polygon(lat: float, lon: float, rings: list[list[tuple[float, float]]]) -> bool:
        """Standard ray casting algorithm for point in polygon (including outer ring & holes)."""
        if not rings:
            return False

        exterior = rings[0]
        inside = False
        n = len(exterior)
        p1_lat, p1_lon = exterior[0]

        for i in range(1, n + 1):
            p2_lat, p2_lon = exterior[i % n]
            if min(p1_lon, p2_lon) < lon <= max(p1_lon, p2_lon):
                if lat <= max(p1_lat, p2_lat):
                    if p1_lon != p2_lon:
                        lat_inters = (lon - p1_lon) * (p2_lat - p1_lat) / (p2_lon - p1_lon) + p1_lat
                    if p1_lat == p2_lat or lat <= lat_inters:
                        inside = not inside
            p1_lat, p1_lon = p2_lat, p2_lon

        if not inside:
            return False

        # Check holes (interior rings)
        for hole in rings[1:]:
            hole_inside = False
            hn = len(hole)
            h1_lat, h1_lon = hole[0]
            for i in range(1, hn + 1):
                h2_lat, h2_lon = hole[i % hn]
                if min(h1_lon, h2_lon) < lon <= max(h1_lon, h2_lon):
                    if lat <= max(h1_lat, h2_lat):
                        if h1_lon != h2_lon:
                            lat_inters = (lon - h1_lon) * (h2_lat - h1_lat) / (h2_lon - h1_lon) + h1_lat
                        if h1_lat == h2_lat or lat <= lat_inters:
                            hole_inside = not hole_inside
                h1_lat, h1_lon = h2_lat, h2_lon
            if hole_inside:
                return False  # Inside a hole means outside polygon

        return True

    def is_within_usa(self, lat: float, lon: float, coastal_margin_miles: float = 5.0) -> bool:
        """True if point lies within USA (Conus, Alaska, Hawaii)."""
        if lat is None or lon is None:
            return False

        # Fast Bounding Box check first
        in_conus_box = self._in_bbox(lat, lon, CONUS_BBOX, margin=0.15)
        in_alaska_box = self._in_bbox(lat, lon, ALASKA_BBOX, margin=0.15)
        in_hawaii_box = self._in_bbox(lat, lon, HAWAII_BBOX, margin=0.15)

        if not (in_conus_box or in_alaska_box or in_hawaii_box):
            return False

        # If polygons not loaded, trust tight bounding box check
        if not self._polygons:
            return True

        # Quick check: points in Canada or Mexico near border
        # Toronto (43.65, -79.38), Windsor (42.31, -83.03), Montreal (45.50, -73.56), Vancouver (49.28, -123.12)
        # Tijuana (32.51, -117.03), Juarez (31.73, -106.48)
        for rings in self._polygons:
            if self._point_in_polygon(lat, lon, rings):
                return True

        # Check Hawaii archipelago proximity (simplified coastline tolerance)
        if in_hawaii_box:
            for name, rings in zip(self._state_names, self._polygons):
                if name == "Hawaii":
                    for ring in rings:
                        for pt_lat, pt_lon in ring:
                            d_miles = math.hypot(
                                (pt_lat - lat) * 69.0,
                                (pt_lon - lon) * 69.0 * math.cos(math.radians(lat)),
                            )
                            if d_miles <= 25.0:
                                return True

        # Check tolerance along coastal/border waters (approx 0.05 degrees ~ 3.5 miles)
        deg_tolerance = coastal_margin_miles / 69.0
        offsets = [
            (deg_tolerance, 0), (-deg_tolerance, 0),
            (0, deg_tolerance), (0, -deg_tolerance),
        ]
        for dlat, dlon in offsets:
            for rings in self._polygons:
                if self._point_in_polygon(lat + dlat, lon + dlon, rings):
                    return True

        return False

    def is_contiguous_us(self, lat: float, lon: float) -> bool:
        """True if point is within the lower 48 contiguous US states + DC."""
        if not self._in_bbox(lat, lon, CONUS_BBOX, margin=0.15):
            return False
        return self.is_within_usa(lat, lon)

    def validate_route_is_domestic(
        self, coordinates: list[tuple[float, float]], sample_step: int = 5
    ) -> tuple[bool, int, tuple[float, float] | None]:
        """
        Verify that route coordinates remain inside USA.
        Returns (is_domestic, foreign_point_index, foreign_point_coord).
        """
        if not coordinates:
            return True, -1, None

        n = len(coordinates)
        step = 1 if n <= sample_step else sample_step
        indices = list(range(0, n, step))
        if (n - 1) not in indices:
            indices.append(n - 1)

        for idx in indices:
            lat, lon = coordinates[idx]
            if not self.is_within_usa(lat, lon):
                return False, idx, (lat, lon)

        return True, -1, None

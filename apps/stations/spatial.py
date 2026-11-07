"""In-memory 3D spherical cKDTree spatial index for high-speed corridor queries."""
from dataclasses import dataclass
from decimal import Decimal
import hashlib
import math
import threading
from typing import ClassVar
import numpy as np
from scipy.spatial import cKDTree

EARTH_RADIUS_MILES = 3958.7613


@dataclass(slots=True, frozen=True)
class IndexedStation:
    """Lightweight in-memory projection of a fuel station."""

    id: int
    opis_truckstop_id: int
    truckstop_name: str
    address: str
    city: str
    state: str
    rack_id: int
    retail_price: Decimal
    latitude: float
    longitude: float
    ecef_x: float
    ecef_y: float
    ecef_z: float
    geocode_precision: str


class StationSpatialIndex:
    """Thread-safe singleton managing an in-memory 3D cKDTree of US fuel stations."""

    _instance: ClassVar["StationSpatialIndex | None"] = None
    _lock: ClassVar[threading.RLock] = threading.RLock()

    def __init__(self) -> None:
        self._tree: cKDTree | None = None
        self._stations: list[IndexedStation] = []
        self._coords_xyz: np.ndarray | None = None
        self._data_version: str = ""
        self._init_lock = threading.RLock()

    @classmethod
    def get_instance(cls) -> "StationSpatialIndex":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton (used in unit tests)."""
        with cls._lock:
            cls._instance = None

    def is_initialized(self) -> bool:
        return self._tree is not None and len(self._stations) > 0

    @property
    def station_count(self) -> int:
        return len(self._stations)

    @property
    def data_version(self) -> str:
        return self._data_version

    def build_from_stations(self, stations: list[IndexedStation]) -> None:
        """Build 3D cKDTree directly from IndexedStation objects."""
        with self._init_lock:
            if not stations:
                self._tree = None
                self._stations = []
                self._coords_xyz = None
                self._data_version = ""
                return

            self._stations = list(stations)
            n = len(stations)
            coords = np.empty((n, 3), dtype=np.float64)

            hasher = hashlib.sha256()
            for i, s in enumerate(stations):
                # Scale unit sphere to Earth radius in miles
                coords[i, 0] = s.ecef_x * EARTH_RADIUS_MILES
                coords[i, 1] = s.ecef_y * EARTH_RADIUS_MILES
                coords[i, 2] = s.ecef_z * EARTH_RADIUS_MILES
                hasher.update(
                    f"{s.opis_truckstop_id}:{s.retail_price}:{s.latitude:.6f}:{s.longitude:.6f};".encode(
                        "utf-8"
                    )
                )

            self._coords_xyz = coords
            self._tree = cKDTree(coords)
            self._data_version = hasher.hexdigest()[:16]

    def build_from_db(self) -> None:
        """Load all active US FuelStations from the database and build spatial index."""
        from apps.stations.models import FuelStation

        qs = FuelStation.objects.filter(is_active_us=True).only(
            "id",
            "opis_truckstop_id",
            "truckstop_name",
            "address",
            "city",
            "state",
            "rack_id",
            "retail_price",
            "latitude",
            "longitude",
            "ecef_x",
            "ecef_y",
            "ecef_z",
            "geocode_precision",
        )

        indexed_stations: list[IndexedStation] = []
        for s in qs:
            indexed_stations.append(
                IndexedStation(
                    id=s.id,
                    opis_truckstop_id=s.opis_truckstop_id,
                    truckstop_name=s.truckstop_name,
                    address=s.address,
                    city=s.city,
                    state=s.state,
                    rack_id=s.rack_id,
                    retail_price=s.retail_price,
                    latitude=s.latitude,
                    longitude=s.longitude,
                    ecef_x=s.ecef_x,
                    ecef_y=s.ecef_y,
                    ecef_z=s.ecef_z,
                    geocode_precision=s.geocode_precision,
                )
            )

        self.build_from_stations(indexed_stations)

    def ensure_initialized(self) -> None:
        if not self.is_initialized():
            with self._init_lock:
                if not self.is_initialized():
                    self.build_from_db()

    @staticmethod
    def surface_to_chord_distance(surface_distance_miles: float) -> float:
        """Convert great-circle surface distance into 3D Euclidean chord distance."""
        half_angle = surface_distance_miles / (2.0 * EARTH_RADIUS_MILES)
        return 2.0 * EARTH_RADIUS_MILES * math.sin(half_angle)

    @staticmethod
    def lat_lon_to_cartesian(lat: float, lon: float) -> np.ndarray:
        """Convert WGS84 lat/lon in degrees to 3D Cartesian coordinates (miles)."""
        lat_rad = math.radians(lat)
        lon_rad = math.radians(lon)
        cos_lat = math.cos(lat_rad)
        x = EARTH_RADIUS_MILES * cos_lat * math.cos(lon_rad)
        y = EARTH_RADIUS_MILES * cos_lat * math.sin(lon_rad)
        z = EARTH_RADIUS_MILES * math.sin(lat_rad)
        return np.array([x, y, z], dtype=np.float64)

    def query_radius(
        self, lat: float, lon: float, radius_miles: float = 5.0
    ) -> list[tuple[IndexedStation, float]]:
        """Query stations within a radius (miles) of a single point."""
        self.ensure_initialized()
        if self._tree is None or not self._stations:
            return []

        center_xyz = self.lat_lon_to_cartesian(lat, lon)
        r_chord = self.surface_to_chord_distance(radius_miles)
        indices = self._tree.query_ball_point(center_xyz, r=r_chord)

        results: list[tuple[IndexedStation, float]] = []
        for idx in indices:
            st = self._stations[idx]
            # Exact Haversine surface distance for returned candidate
            dist = self.haversine_distance_miles(lat, lon, st.latitude, st.longitude)
            if dist <= radius_miles:
                results.append((st, dist))

        results.sort(key=lambda item: item[1])
        return results

    def query_corridor(
        self, sample_coords: list[tuple[float, float]], radius_miles: float = 5.0
    ) -> list[IndexedStation]:
        """Vectorized query finding all unique stations within radius_miles of a polyline sample."""
        self.ensure_initialized()
        if self._tree is None or not self._stations or not sample_coords:
            return []

        # Vectorized conversion of sample points to (M, 3) Cartesian coordinates
        coords_arr = np.array(sample_coords, dtype=np.float64)
        lats_rad = np.radians(coords_arr[:, 0])
        lons_rad = np.radians(coords_arr[:, 1])
        cos_lats = np.cos(lats_rad)

        m = len(sample_coords)
        samples_xyz = np.empty((m, 3), dtype=np.float64)
        samples_xyz[:, 0] = EARTH_RADIUS_MILES * cos_lats * np.cos(lons_rad)
        samples_xyz[:, 1] = EARTH_RADIUS_MILES * cos_lats * np.sin(lons_rad)
        samples_xyz[:, 2] = EARTH_RADIUS_MILES * np.sin(lats_rad)

        r_chord = self.surface_to_chord_distance(radius_miles)
        indices_nested = self._tree.query_ball_point(samples_xyz, r=r_chord)

        # Flat unique station indices
        unique_indices = set()
        for idx_list in indices_nested:
            unique_indices.update(idx_list)

        return [self._stations[i] for i in sorted(unique_indices)]

    @staticmethod
    def haversine_distance_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Great-circle distance between two points in miles."""
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        delta_phi = math.radians(lat2 - lat1)
        delta_lambda = math.radians(lon2 - lon1)

        sin_half_phi = math.sin(delta_phi / 2.0)
        sin_half_lambda = math.sin(delta_lambda / 2.0)

        a = sin_half_phi * sin_half_phi + math.cos(phi1) * math.cos(phi2) * sin_half_lambda * sin_half_lambda
        c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))
        return EARTH_RADIUS_MILES * c

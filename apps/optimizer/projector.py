"""Route polyline projection, corridor station spatial query, and candidate pruning."""
import math
from typing import Any
import numpy as np
from scipy.spatial import cKDTree
from apps.api.exceptions import InsufficientFuelStationsError
from apps.optimizer.domain import ProjectedStation, VehicleSpec
from apps.stations.spatial import EARTH_RADIUS_MILES, IndexedStation, StationSpatialIndex


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Fast Haversine distance in miles."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    return 2.0 * EARTH_RADIUS_MILES * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))


class RouteProjector:
    """Projects stations along highway routes and prunes dominated candidates."""

    def __init__(self, spatial_index: StationSpatialIndex | None = None) -> None:
        self.spatial_index = spatial_index or StationSpatialIndex.get_instance()

    def densify_polyline(
        self, coordinates: list[tuple[float, float]], max_spacing_miles: float = 1.0
    ) -> tuple[list[tuple[float, float]], list[float]]:
        """
        Subdivides polyline segments longer than max_spacing_miles.
        Returns (densified_coordinates, cumulative_mileages).
        """
        if not coordinates:
            return [], []

        densified: list[tuple[float, float]] = [coordinates[0]]
        mileages: list[float] = [0.0]
        total_m = 0.0

        for i in range(len(coordinates) - 1):
            p1 = coordinates[i]
            p2 = coordinates[i + 1]
            seg_dist = haversine(p1[0], p1[1], p2[0], p2[1])

            if seg_dist > max_spacing_miles:
                steps = int(math.ceil(seg_dist / max_spacing_miles))
                for s in range(1, steps):
                    frac = s / float(steps)
                    lat_inter = p1[0] + frac * (p2[0] - p1[0])
                    lon_inter = p1[1] + frac * (p2[1] - p1[1])
                    densified.append((lat_inter, lon_inter))
                    mileages.append(total_m + frac * seg_dist)

            densified.append(p2)
            total_m += seg_dist
            mileages.append(total_m)

        return densified, mileages

    def project_candidates(
        self,
        coordinates: list[tuple[float, float]],
        total_distance_miles: float,
        vehicle_spec: VehicleSpec,
    ) -> list[ProjectedStation]:
        """Query corridor stations, project onto route, and prune dominated candidates."""
        if not coordinates or total_distance_miles <= 0.0:
            return []

        # 1. Densify polyline at 2.0 mile intervals for spatial corridor query
        samples, cumulative_miles = self.densify_polyline(coordinates, max_spacing_miles=2.0)
        geom_total_miles = cumulative_miles[-1] if cumulative_miles else total_distance_miles
        # Scaling factor aligning geometric chord lengths with OSRM reported road distance
        scale = total_distance_miles / max(1e-6, geom_total_miles)
        cumulative_miles = [m * scale for m in cumulative_miles]

        # 2. Query spatial index along corridor
        corridor_r = vehicle_spec.corridor_miles
        raw_candidates = self.spatial_index.query_corridor(samples, radius_miles=corridor_r)

        # 3. Project each candidate station onto route segments
        projected = self._project_station_list(raw_candidates, samples, cumulative_miles, vehicle_spec)

        # 4. Check for reachability gaps and apply adaptive corridor expansion if needed
        projected = self._ensure_corridor_connectivity(
            projected, samples, cumulative_miles, total_distance_miles, vehicle_spec
        )

        # 5. Bin into 0.5-mile exit buckets and prune dominated stations
        return self._prune_exit_clusters(projected)

    def _project_station_list(
        self,
        stations: list[IndexedStation],
        samples: list[tuple[float, float]],
        cumulative_miles: list[float],
        spec: VehicleSpec,
    ) -> list[ProjectedStation]:
        """Project station coordinates onto polyline segments."""
        if not stations or not samples:
            return []

        results: list[ProjectedStation] = []
        samples_arr = np.array(samples, dtype=np.float64)  # (M, 2)
        n_samples = len(samples)
        sample_tree = cKDTree(samples_arr)

        st_coords = np.array([[s.latitude, s.longitude] for s in stations], dtype=np.float64)
        _, nearest_indices = sample_tree.query(st_coords)

        for i, st in enumerate(stations):
            nearest_idx = int(nearest_indices[i])

            # Orthogonal refinement on neighboring segments [nearest_idx - 1, nearest_idx] and [nearest_idx, nearest_idx + 1]
            best_mile = cumulative_miles[nearest_idx]
            best_detour = haversine(st.latitude, st.longitude, samples[nearest_idx][0], samples[nearest_idx][1])

            candidate_segments = []
            if nearest_idx > 0:
                candidate_segments.append((nearest_idx - 1, nearest_idx))
            if nearest_idx < n_samples - 1:
                candidate_segments.append((nearest_idx, nearest_idx + 1))

            for k1, k2 in candidate_segments:
                v1 = samples_arr[k1]
                v2 = samples_arr[k2]
                seg_vec = v2 - v1
                seg_len_sq = float(np.dot(seg_vec, seg_vec))
                if seg_len_sq > 1e-12:
                    st_vec = np.array([st.latitude, st.longitude]) - v1
                    t = float(np.dot(st_vec, seg_vec) / seg_len_sq)
                    t_clamped = max(0.0, min(1.0, t))
                    proj_lat = v1[0] + t_clamped * seg_vec[0]
                    proj_lon = v1[1] + t_clamped * seg_vec[1]
                    dist_to_proj = haversine(st.latitude, st.longitude, proj_lat, proj_lon)
                    if dist_to_proj < best_detour:
                        best_detour = dist_to_proj
                        best_mile = cumulative_miles[k1] + t_clamped * (cumulative_miles[k2] - cumulative_miles[k1])

            # Effective detour penalty
            effective_detour = best_detour
            if st.geocode_precision in ("CITY_CENTROID", "GEOCODER_FALLBACK"):
                effective_detour = min(best_detour, 0.75)

            # Detour fuel cost impact on effective ranking price
            price_float = float(st.retail_price)
            detour_gallons = (2.0 * effective_detour) / spec.mpg
            effective_price = price_float + (detour_gallons / 25.0) * price_float

            results.append(
                ProjectedStation(
                    station=st,
                    route_mile_marker=round(best_mile, 2),
                    off_route_distance_miles=round(best_detour, 2),
                    effective_price=effective_price,
                )
            )

        results.sort(key=lambda s: s.route_mile_marker)
        return results

    def _ensure_corridor_connectivity(
        self,
        projected: list[ProjectedStation],
        samples: list[tuple[float, float]],
        cumulative_miles: list[float],
        total_distance_miles: float,
        spec: VehicleSpec,
    ) -> list[ProjectedStation]:
        """Adaptive expansion if reachability gap > max_range_miles exists."""
        max_range = spec.max_range_miles

        def find_gap(stations: list[ProjectedStation]) -> tuple[float, float] | None:
            miles = [0.0] + [s.route_mile_marker for s in stations] + [total_distance_miles]
            for idx in range(len(miles) - 1):
                if miles[idx + 1] - miles[idx] > max_range:
                    return miles[idx], miles[idx + 1]
            return None

        gap = find_gap(projected)
        if gap is None:
            return projected

        # Adaptive Expansion to 15.0 miles, then 30.0 miles
        for wider_radius in [15.0, 30.0]:
            wider_candidates = self.spatial_index.query_corridor(samples, radius_miles=wider_radius)
            new_projected = self._project_station_list(wider_candidates, samples, cumulative_miles, spec)
            gap = find_gap(new_projected)
            if gap is None:
                return new_projected

        # Still exceeding max range
        gap_start, gap_end = gap
        raise InsufficientFuelStationsError(
            f"No reachable fuel station along route between mile {gap_start:.1f} and {gap_end:.1f} "
            f"(gap of {gap_end - gap_start:.1f} miles exceeds max vehicle range of {max_range:.1f} miles).",
            details={
                "gap_start_mile": gap_start,
                "gap_end_mile": gap_end,
                "gap_distance_miles": gap_end - gap_start,
                "max_vehicle_range_miles": max_range,
            },
        )

    def _prune_exit_clusters(
        self, stations: list[ProjectedStation], bucket_size_miles: float = 0.5
    ) -> list[ProjectedStation]:
        """Cluster stations into 0.5-mile exit buckets and retain best effective price."""
        if not stations:
            return []

        buckets: dict[int, list[ProjectedStation]] = {}
        for s in stations:
            b_idx = int(s.route_mile_marker / bucket_size_miles)
            buckets.setdefault(b_idx, []).append(s)

        pruned: list[ProjectedStation] = []
        for b_idx in sorted(buckets.keys()):
            cluster = buckets[b_idx]
            # Pick lowest effective price; break ties deterministically
            best = min(
                cluster,
                key=lambda item: (
                    item.effective_price,
                    item.retail_price,
                    item.off_route_distance_miles,
                    item.opis_truckstop_id,
                ),
            )
            pruned.append(best)

        pruned.sort(key=lambda s: s.route_mile_marker)
        return pruned

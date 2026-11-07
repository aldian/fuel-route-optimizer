"""Latency benchmarks verifying the high-performance compute requirement (< 15ms)."""
import json
from pathlib import Path
import sys
import time
from decimal import Decimal
import numpy as np
import pytest
from django.conf import settings
from apps.optimizer.domain import VehicleSpec
from apps.optimizer.projector import RouteProjector
from apps.optimizer.solver import MinCostRefuelSolver
from apps.routing.providers.polyline_codec import decode_polyline, encode_polyline
from apps.stations.spatial import IndexedStation, StationSpatialIndex


@pytest.fixture(scope="session")
def spatial_index_6k() -> StationSpatialIndex:
    """Initialize StationSpatialIndex with all 6,626 real US stations."""
    idx = StationSpatialIndex.get_instance()
    json_path = settings.BASE_DIR / "data" / "processed" / "fuel_stations_us.json"
    if json_path.exists():
        with open(json_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        stations = [
            IndexedStation(
                id=i,
                opis_truckstop_id=r["opis_truckstop_id"],
                truckstop_name=r["truckstop_name"],
                address=r["address"],
                city=r["city"],
                state=r["state"],
                rack_id=r["rack_id"],
                retail_price=Decimal(str(r["retail_price"])),
                latitude=r["latitude"],
                longitude=r["longitude"],
                ecef_x=r["ecef_x"],
                ecef_y=r["ecef_y"],
                ecef_z=r["ecef_z"],
                geocode_precision=r.get("geocode_precision", "CITY_CENTROID"),
            )
            for i, r in enumerate(raw)
        ]
        idx.build_from_stations(stations)
    return idx


def test_coast_to_coast_pipeline_latency_p99(spatial_index_6k: StationSpatialIndex) -> None:
    """
    Verify internal compute pipeline executes in < 15.0 ms median on coast-to-coast route.
    Pipeline: polyline decode -> cKDTree corridor query -> segment projection -> refuel solver.
    """
    projector = RouteProjector(spatial_index=spatial_index_6k)
    solver = MinCostRefuelSolver()
    spec = VehicleSpec(max_range_miles=500.0, mpg=10.0, initial_fuel_gallons=50.0, corridor_miles=8.0)

    # Realistic highway route along I-80 corridor from NYC to LA (~2,790 miles, ~1,000 points)
    interstate_hwy = [
        (40.7128, -74.0060), (41.1192, -78.7600), (41.4993, -81.6944),
        (41.6528, -83.5379), (41.8781, -87.6298), (41.5868, -93.6250),
        (41.2565, -95.9345), (41.1400, -104.8202), (41.7486, -107.2408),
        (40.7608, -111.8910), (39.5296, -119.8138), (36.1699, -115.1398),
        (34.0522, -118.2437)
    ]
    dense_coords = []
    for i in range(len(interstate_hwy) - 1):
        lats = np.linspace(interstate_hwy[i][0], interstate_hwy[i + 1][0], 80)
        lons = np.linspace(interstate_hwy[i][1], interstate_hwy[i + 1][1], 80)
        dense_coords.extend(zip(lats.tolist(), lons.tolist()))

    poly6 = encode_polyline(dense_coords, precision=6)
    total_distance_miles = 2790.0

    # 10 Warmup runs
    for _ in range(10):
        decoded = decode_polyline(poly6, precision=6)
        candidates = projector.project_candidates(decoded, total_distance_miles, spec)
        if candidates:
            _ = solver.solve(total_distance_miles, candidates, spec)

    # 40 timed benchmark iterations
    latencies_ms: list[float] = []
    for _ in range(40):
        t0 = time.perf_counter()

        # Step 1: Decode polyline
        decoded = decode_polyline(poly6, precision=6)

        # Step 2: Spatial Corridor Query & Route Projection
        candidates = projector.project_candidates(decoded, total_distance_miles, spec)

        # Step 3: Optimization Solver
        if candidates:
            _ = solver.solve(total_distance_miles, candidates, spec)

        duration_ms = (time.perf_counter() - t0) * 1000.0
        latencies_ms.append(duration_ms)

    p50 = float(np.percentile(latencies_ms, 50))
    p90 = float(np.percentile(latencies_ms, 90))
    p99 = float(np.percentile(latencies_ms, 99))

    print(f"\n--- Latency Benchmark (Coast-to-Coast 2,790 mi, {spatial_index_6k.station_count} stations) ---")
    print(f"P50: {p50:.2f} ms | P90: {p90:.2f} ms | P99: {p99:.2f} ms")

    # Quality Gate Requirement: P50 < 15.0 ms (with headroom if coverage tracing is active)
    has_coverage = bool(sys.gettrace())
    max_p50 = 40.0 if has_coverage else 15.0
    max_p99 = 75.0 if has_coverage else 35.0
    assert p50 < max_p50, f"P50 latency was {p50:.2f} ms, exceeding {max_p50} ms threshold!"
    assert p99 < max_p99, f"P99 latency was {p99:.2f} ms, exceeding {max_p99} ms threshold!"

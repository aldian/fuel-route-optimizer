"""Targeted test suite to achieve >= 95% unit test coverage across all apps."""
from decimal import Decimal
from unittest.mock import MagicMock, patch
import pytest
import respx
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from apps.api.exceptions import InsufficientFuelStationsError
from apps.optimizer.domain import PlannedStop, ProjectedStation, VehicleSpec
from apps.optimizer.projector import RouteProjector
from apps.optimizer.solver import MinCostRefuelSolver
from apps.routing.boundary import USBoundaryValidator
from apps.routing.call_budget import ExternalCallBudget
from apps.routing.providers.polyline_codec import encode_polyline
from apps.stations.models import FuelStation
from apps.stations.spatial import IndexedStation, StationSpatialIndex


@pytest.mark.django_db
def test_health_view_failure_branches() -> None:
    client = APIClient()

    with patch("apps.stations.models.FuelStation.objects.filter", side_effect=Exception("DB Error")), \
         patch("apps.routing.models.USPlaceGazetteer.objects.count", side_effect=Exception("DB Error")), \
         patch("apps.stations.spatial.StationSpatialIndex.get_instance", side_effect=Exception("Spatial Error")):
        resp = client.get("/api/v1/health/")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert data["active_us_stations"] == 0
        assert data["us_gazetteer_entries"] == 0
        assert data["spatial_index_ready"] is False


def test_map_viewer_urls_reverse() -> None:
    url = reverse("map_viewer:route-map")
    assert url == "/map/"


@pytest.mark.django_db
def test_stations_view_edge_cases_and_filters() -> None:
    client = APIClient()

    # 1. Invalid coordinates error
    resp_inv = client.get("/api/v1/stations/?lat=invalid&lng=-74.0")
    assert resp_inv.status_code == status.HTTP_400_BAD_REQUEST
    assert resp_inv.json()["error"]["code"] == "INVALID_COORDINATES"

    # 2. Proximity with descending price ordering
    StationSpatialIndex.reset_instance()
    idx = StationSpatialIndex.get_instance()
    import math
    lat_r, lon_r = math.radians(30.0), math.radians(-95.0)
    cos_l = math.cos(lat_r)
    st1 = IndexedStation(
        id=1, opis_truckstop_id=10, truckstop_name="S1", address="A1", city="C1", state="TX",
        rack_id=1, retail_price=Decimal("3.00"), latitude=30.0, longitude=-95.0,
        ecef_x=cos_l * math.cos(lon_r), ecef_y=cos_l * math.sin(lon_r), ecef_z=math.sin(lat_r),
        geocode_precision="CITY_CENTROID"
    )
    st2 = IndexedStation(
        id=2, opis_truckstop_id=20, truckstop_name="S2", address="A2", city="C2", state="TX",
        rack_id=2, retail_price=Decimal("4.00"), latitude=30.0, longitude=-95.0,
        ecef_x=cos_l * math.cos(lon_r), ecef_y=cos_l * math.sin(lon_r), ecef_z=math.sin(lat_r),
        geocode_precision="CITY_CENTROID"
    )
    idx.build_from_stations([st1, st2])

    resp_desc = client.get("/api/v1/stations/?lat=30.0&lng=-95.0&radius_miles=50.0&ordering=-retail_price")
    assert resp_desc.status_code == status.HTTP_200_OK
    results = resp_desc.json()["results"]
    assert len(results) == 2
    assert results[0]["retail_price"] == "4.00"

    # 3. Database query with state and city filter
    FuelStation.objects.create(
        opis_truckstop_id=88881,
        truckstop_name="Austin Stop",
        address="100 Main",
        city="Austin",
        state="TX",
        country_code="US",
        rack_id=1,
        retail_price=Decimal("2.999"),
        latitude=30.26,
        longitude=-97.74,
        ecef_x=0.1,
        ecef_y=-0.8,
        ecef_z=0.5,
        is_active_us=True,
    )

    resp_db = client.get("/api/v1/stations/?state=tx&city=austin&ordering=-retail_price&limit=10")
    assert resp_db.status_code == status.HTTP_200_OK
    assert resp_db.json()["count"] >= 1


@pytest.mark.django_db
@respx.mock
def test_route_view_fuel_stops_and_downsampling() -> None:
    client = APIClient()

    # Create stations along route so 1000-mile route has reachable stops
    st1 = FuelStation.objects.create(
        opis_truckstop_id=77771,
        truckstop_name="Midway Fuel 1",
        address="I-80 Mile 300",
        city="North Platte",
        state="NE",
        country_code="US",
        rack_id=99,
        retail_price=Decimal("2.500"),
        latitude=42.5,
        longitude=-102.5,
        is_active_us=True,
    )
    st2 = FuelStation.objects.create(
        opis_truckstop_id=77772,
        truckstop_name="Midway Fuel 2",
        address="I-80 Mile 700",
        city="Grand Island",
        state="NE",
        country_code="US",
        rack_id=99,
        retail_price=Decimal("2.600"),
        latitude=45.0,
        longitude=-100.0,
        is_active_us=True,
    )
    StationSpatialIndex.get_instance().build_from_db()

    # Generate polyline with > 1600 points to exercise coordinate downsampling
    long_coords = [(40.0 + (i * 0.005), -105.0 + (i * 0.005)) for i in range(1600)]
    poly = encode_polyline(long_coords, precision=6)

    respx.get(url__startswith="https://router.project-osrm.org/route/v1/driving/").respond(
        status_code=200,
        json={
            "code": "Ok",
            "routes": [
                {
                    "distance": 1609344.0,  # ~1000 miles, requiring stops!
                    "duration": 50000.0,
                    "geometry": poly,
                }
            ],
        },
    )

    # 1. POST route with downsampling and fuel stops
    resp = client.post(
        "/api/v1/route/",
        data={
            "start": "40.0, -105.0",
            "finish": "48.0, -97.0",
            "vehicle_range_miles": 500.0,
            "mpg": 10.0,
            "include_geojson": True,
        },
        format="json",
    )
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["route"]["total_distance_miles"] > 900.0

    # 2. Verify Map HTML view downsampling
    resp_map = client.get("/api/v1/route/map/?start=40.0,-105.0&finish=48.0,-97.0")
    assert resp_map.status_code == status.HTTP_200_OK


def test_solver_micro_stop_consolidation() -> None:
    solver = MinCostRefuelSolver()
    spec = VehicleSpec(max_range_miles=500.0, mpg=10.0, min_stop_gallons=2.0)

    st1 = IndexedStation(
        id=1, opis_truckstop_id=1, truckstop_name="S1", address="A1", city="C1", state="TX",
        rack_id=1, retail_price=Decimal("3.00"), latitude=30.0, longitude=-95.0,
        ecef_x=0.0, ecef_y=0.0, ecef_z=0.0, geocode_precision="HIGHWAY_EXIT"
    )
    st2 = IndexedStation(
        id=2, opis_truckstop_id=2, truckstop_name="S2", address="A2", city="C2", state="TX",
        rack_id=2, retail_price=Decimal("3.50"), latitude=31.0, longitude=-95.0,
        ecef_x=0.0, ecef_y=0.0, ecef_z=0.0, geocode_precision="HIGHWAY_EXIT"
    )

    raw_stops = [
        {
            "station": st1,
            "route_mile": 200.0,
            "gallons": 20.0,
            "tank_before": 30.0,
            "tank_after": 45.0,  # Room left: 5 gallons
        },
        {
            "station": st2,
            "route_mile": 250.0,
            "gallons": 1.5,  # Micro-stop (< 2.0 gal)
            "tank_before": 40.0,
            "tank_after": 41.5,
        },
    ]

    consolidated = solver._consolidate_micro_stops(raw_stops, spec, [0.0, 200.0, 250.0, 500.0], 3)
    assert len(consolidated) == 1
    assert consolidated[0]["gallons"] == 21.5


def test_projector_adaptive_expansion_and_unbridgeable() -> None:
    projector = RouteProjector()
    spec = VehicleSpec(max_range_miles=500.0, mpg=10.0)

    # Empty stations
    assert projector._prune_exit_clusters([]) == []

    # Corridor with unbridgeable 1000 mile gap
    samples = [(40.0, -100.0), (40.0, -90.0), (40.0, -80.0)]

    with patch.object(projector.spatial_index, "query_corridor", return_value=[]):
        with pytest.raises(InsufficientFuelStationsError):
            projector.project_candidates(samples, 1000.0, spec)


def test_boundary_validator_holes_and_edge_cases() -> None:
    validator = USBoundaryValidator()

    # Test raycasting on polygon with hole
    # Square outer ring [0,0] to [10,10], hole in center [3,3] to [7,7]
    outer = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)]
    hole = [(3.0, 3.0), (7.0, 3.0), (7.0, 7.0), (3.0, 7.0), (3.0, 3.0)]
    rings = [outer, hole]

    # Point inside outer ring but outside hole -> True
    assert validator._point_in_polygon(1.0, 1.0, rings)
    # Point inside hole -> False
    assert not validator._point_in_polygon(5.0, 5.0, rings)
    # Point outside outer ring -> False
    assert not validator._point_in_polygon(15.0, 15.0, rings)

    # Empty coordinates route check
    is_dom, idx, pt = validator.validate_route_is_domestic([])
    assert is_dom

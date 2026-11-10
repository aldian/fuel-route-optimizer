"""Unit tests for REST API endpoints and interactive map view."""
from decimal import Decimal
import json
import pytest
import respx
from rest_framework import status
from rest_framework.test import APIClient
from apps.routing.models import USPlaceGazetteer
from apps.routing.providers.polyline_codec import encode_polyline
from apps.stations.models import FuelStation
from apps.stations.spatial import StationSpatialIndex


@pytest.fixture(autouse=True)
def setup_test_data() -> None:
    # Seed minimal gazetteer
    USPlaceGazetteer.objects.all().delete()
    USPlaceGazetteer.objects.create(
        normalized_query="new york,ny",
        display_name="New York, NY",
        city="New York",
        state_code="NY",
        place_type="CITY",
        latitude=40.7128,
        longitude=-74.0060,
        population=8000000,
    )
    USPlaceGazetteer.objects.create(
        normalized_query="los angeles,ca",
        display_name="Los Angeles, CA",
        city="Los Angeles",
        state_code="CA",
        place_type="CITY",
        latitude=34.0522,
        longitude=-118.2437,
        population=3900000,
    )

    # Seed test stations
    FuelStation.objects.all().delete()
    st1 = FuelStation.objects.create(
        opis_truckstop_id=1,
        truckstop_name="Pennsylvania Travel Plaza",
        address="I-80, EXIT 100",
        city="Dubois",
        state="PA",
        country_code="US",
        rack_id=10,
        retail_price=Decimal("2.99000000"),
        min_retail_price=Decimal("2.99000000"),
        avg_retail_price=Decimal("2.99000000"),
        max_retail_price=Decimal("2.99000000"),
        latitude=41.1192,
        longitude=-78.7600,
        geocode_precision=FuelStation.PRECISION_HIGHWAY_EXIT,
        is_active_us=True,
    )
    st2 = FuelStation.objects.create(
        opis_truckstop_id=2,
        truckstop_name="Ohio Travel Center",
        address="I-80, EXIT 50",
        city="Toledo",
        state="OH",
        country_code="US",
        rack_id=20,
        retail_price=Decimal("2.85000000"),
        min_retail_price=Decimal("2.85000000"),
        avg_retail_price=Decimal("2.85000000"),
        max_retail_price=Decimal("2.85000000"),
        latitude=41.6528,
        longitude=-83.5379,
        geocode_precision=FuelStation.PRECISION_HIGHWAY_EXIT,
        is_active_us=True,
    )

    # Warm spatial index
    StationSpatialIndex.get_instance().build_from_db()


@pytest.mark.django_db
@respx.mock
def test_route_optimization_post_and_get() -> None:
    client = APIClient()

    # Mock OSRM
    coords = [(40.7128, -74.0060), (41.1192, -78.7600), (41.6528, -83.5379), (34.0522, -118.2437)]
    poly = encode_polyline(coords, precision=6)
    respx.get(url__startswith="https://router.project-osrm.org/route/v1/driving/").respond(
        status_code=200,
        json={
            "code": "Ok",
            "routes": [
                {
                    "distance": 800000.0,  # ~497 miles
                    "duration": 28800.0,
                    "geometry": poly,
                }
            ],
        },
    )

    # 1. POST route
    payload = {
        "start": "New York, NY",
        "finish": "Los Angeles, CA",
        "vehicle_range_miles": 500.0,
        "mpg": 10.0,
        "include_geojson": True,
    }
    resp = client.post("/api/v1/route/", data=payload, format="json")
    assert resp.status_code == status.HTTP_200_OK

    data = resp.json()
    assert "route" in data
    assert "fuel_summary" in data
    assert "fuel_stops" in data
    assert "map" in data
    assert "meta" in data
    assert data["map"]["geojson"]["type"] == "FeatureCollection"
    assert data["meta"]["external_api_calls"]["total_count"] == 1  # 1 call ideal!

    # 2. GET route
    resp_get = client.get("/api/v1/route/?start=New+York,+NY&finish=Los+Angeles,+CA")
    assert resp_get.status_code == status.HTTP_200_OK
    assert resp_get.json()["route"]["total_distance_miles"] == data["route"]["total_distance_miles"]


@pytest.mark.django_db
def test_route_optimization_validation_errors() -> None:
    client = APIClient()

    # Missing start/finish
    resp = client.post("/api/v1/route/", data={}, format="json")
    assert resp.status_code == status.HTTP_400_BAD_REQUEST

    # Initial fuel exceeding tank capacity
    resp2 = client.post(
        "/api/v1/route/",
        data={"start": "New York, NY", "finish": "Los Angeles, CA", "vehicle_range_miles": 500.0, "mpg": 10.0, "initial_fuel_gallons": 60.0},
        format="json",
    )
    assert resp2.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_fuel_stations_list_view() -> None:
    client = APIClient()

    # List all
    resp = client.get("/api/v1/stations/")
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["count"] == 2

    # Filter by state
    resp_oh = client.get("/api/v1/stations/?state=OH")
    assert resp_oh.status_code == status.HTTP_200_OK
    assert resp_oh.json()["count"] == 1

    # Radius search around Toledo
    resp_rad = client.get("/api/v1/stations/?lat=41.6528&lng=-83.5379&radius_miles=10.0")
    assert resp_rad.status_code == status.HTTP_200_OK
    assert resp_rad.json()["count"] == 1


@pytest.mark.django_db
@respx.mock
def test_route_map_html_view() -> None:
    client = APIClient()

    coords = [(40.7128, -74.0060), (41.1192, -78.7600), (41.6528, -83.5379), (34.0522, -118.2437)]
    poly = encode_polyline(coords, precision=6)
    respx.get(url__startswith="https://router.project-osrm.org/route/v1/driving/").respond(
        status_code=200,
        json={"code": "Ok", "routes": [{"distance": 800000.0, "duration": 28800.0, "geometry": poly}]},
    )

    resp = client.get("/api/v1/route/map/?start=New+York,+NY&finish=Los+Angeles,+CA")
    assert resp.status_code == status.HTTP_200_OK
    assert "text/html" in resp["Content-Type"]
    content = resp.content.decode("utf-8")
    assert "leaflet.js" in content
    assert "Fuel Route Optimizer" in content
    assert "Trip Overview" in content
    assert "Popular US Freight Corridors:" in content
    assert "Stop-by-Stop Fuel Price &amp; Tank Profile" in content
    assert 'id="route-planner-form"' in content
    # High-contrast CSS text assertion
    assert "--text: #000000;" in content

    # Test invalid numeric query param fallback branches
    resp_invalid = client.get(
        "/api/v1/route/map/?start=New+York,+NY&finish=Los+Angeles,+CA"
        "&vehicle_range_miles=abc&mpg=xyz&initial_fuel_gallons=bad&corridor_miles=bad"
    )
    assert resp_invalid.status_code == status.HTTP_200_OK

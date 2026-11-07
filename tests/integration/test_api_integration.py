"""Integration tests for Fuel Route Optimizer API, Location Resolver, and Routing Provider Chain.

Validates TC-INT-01 through TC-INT-08 from specifications.
"""
from decimal import Decimal
import httpx
import pytest
import respx
from django.core.cache import cache
from rest_framework import status
from rest_framework.test import APIClient
from apps.api.exceptions import ExternalCallBudgetExceededError
from apps.routing.call_budget import ExternalCallBudget
from apps.routing.models import USPlaceGazetteer
from apps.routing.providers.polyline_codec import encode_polyline
from apps.stations.models import FuelStation
from apps.stations.spatial import StationSpatialIndex


@pytest.fixture(autouse=True)
def setup_integration_data() -> None:
    cache.clear()
    USPlaceGazetteer.objects.create(
        normalized_query="new york,ny",
        display_name="New York, NY",
        city="New York",
        state_code="NY",
        place_type="CITY",
        latitude=40.7128,
        longitude=-74.0060,
        population=8804190,
    )
    USPlaceGazetteer.objects.create(
        normalized_query="los angeles,ca",
        display_name="Los Angeles, CA",
        city="Los Angeles",
        state_code="CA",
        place_type="CITY",
        latitude=34.0522,
        longitude=-118.2437,
        population=3898747,
    )
    USPlaceGazetteer.objects.create(
        normalized_query="detroit,mi",
        display_name="Detroit, MI",
        city="Detroit",
        state_code="MI",
        place_type="CITY",
        latitude=42.3314,
        longitude=-83.0458,
        population=639111,
    )
    USPlaceGazetteer.objects.create(
        normalized_query="buffalo,ny",
        display_name="Buffalo, NY",
        city="Buffalo",
        state_code="NY",
        place_type="CITY",
        latitude=42.8864,
        longitude=-78.8784,
        population=278349,
    )
    USPlaceGazetteer.objects.create(
        normalized_query="honolulu,hi",
        display_name="Honolulu, HI",
        city="Honolulu",
        state_code="HI",
        place_type="CITY",
        latitude=21.3069,
        longitude=-157.8583,
        population=350964,
    )
    USPlaceGazetteer.objects.create(
        normalized_query="san francisco,ca",
        display_name="San Francisco, CA",
        city="San Francisco",
        state_code="CA",
        place_type="CITY",
        latitude=37.7749,
        longitude=-122.4194,
        population=873965,
    )

    # Seed 7 representative highway corridor stations
    stations = [
        (101, "PA Turnpike Travel Plaza", "I-76 Exit 150", "Bedford", "PA", Decimal("3.299"), 40.0, -78.5),
        (102, "Ohio Gateway Oasis", "I-80 Exit 70", "Toledo", "OH", Decimal("3.149"), 41.6, -83.5),
        (103, "Iowa Crossroads", "I-80 Exit 284", "Walcott", "IA", Decimal("2.999"), 41.6, -90.8),
        (104, "Nebraska Express Stop", "I-80 Exit 220", "Lexington", "NE", Decimal("2.899"), 40.7, -99.7),
        (105, "Wyoming Frontier Depot", "I-80 Exit 211", "Rawlins", "WY", Decimal("3.099"), 41.5, -107.0),
        (106, "Utah Basin Depot", "I-80 Exit 50", "Wendover", "UT", Decimal("3.399"), 40.7, -114.0),
        (107, "Nevada Silver Express", "I-15 Exit 1", "Primm", "NV", Decimal("3.499"), 36.1, -115.1),
    ]
    for opis_id, name, addr, city, st, price, lat, lon in stations:
        FuelStation.objects.create(
            opis_truckstop_id=opis_id,
            truckstop_name=name,
            address=addr,
            city=city,
            state=st,
            country_code="US",
            rack_id=1,
            retail_price=price,
            latitude=lat,
            longitude=lon,
            is_active_us=True,
        )
    StationSpatialIndex.get_instance().build_from_db()


@pytest.mark.django_db
@respx.mock
def test_tc_int_01_ideal_one_call_city_route() -> None:
    """TC-INT-01: Ideal 1-Call City/ZIP Route: 1 OSRM call, 0 geocoding calls."""
    client = APIClient()

    coords = [
        (40.7128, -74.0060),
        (40.0, -78.5),
        (41.6, -83.5),
        (41.6, -90.8),
        (40.7, -99.7),
        (41.5, -107.0),
        (40.7, -114.0),
        (36.1, -115.1),
        (34.0522, -118.2437),
    ]
    poly = encode_polyline(coords, precision=6)
    osrm_route = respx.get(url__startswith="https://router.project-osrm.org/route/v1/driving/").respond(
        status_code=200,
        json={
            "code": "Ok",
            "routes": [{"distance": 4500000.0, "duration": 140000.0, "geometry": poly}],
        },
    )

    resp = client.post(
        "/api/v1/route/",
        data={
            "start": "New York, NY",
            "finish": "Los Angeles, CA",
            "vehicle_range_miles": 500.0,
            "mpg": 10.0,
        },
        format="json",
    )
    assert resp.status_code == status.HTTP_200_OK
    assert osrm_route.call_count == 1

    data = resp.json()
    assert data["meta"]["external_api_calls"]["total_count"] == 1
    assert data["meta"]["cache_hit"] is False
    assert len(data["fuel_stops"]) >= 5


@pytest.mark.django_db
@respx.mock
def test_tc_int_02_cached_zero_call_repeat_query() -> None:
    """TC-INT-02: Cached Repeat Query: 2nd identical request uses 0 outbound calls."""
    client = APIClient()

    coords = [(40.7128, -74.0060), (34.0522, -118.2437)]
    poly = encode_polyline(coords, precision=6)
    osrm_route = respx.get(url__startswith="https://router.project-osrm.org/route/v1/driving/").respond(
        status_code=200,
        json={
            "code": "Ok",
            "routes": [{"distance": 500000.0, "duration": 20000.0, "geometry": poly}],
        },
    )

    payload = {"start": "New York, NY", "finish": "Los Angeles, CA"}
    resp1 = client.post("/api/v1/route/", data=payload, format="json")
    assert resp1.status_code == status.HTTP_200_OK
    assert osrm_route.call_count == 1

    # Second call -> cache hit!
    resp2 = client.post("/api/v1/route/", data=payload, format="json")
    assert resp2.status_code == status.HTTP_200_OK
    assert osrm_route.call_count == 1  # Still 1 call!
    data2 = resp2.json()
    assert data2["meta"]["cache_hit"] is True
    assert data2["meta"]["external_api_calls"]["total_count"] == 0


@pytest.mark.django_db
@respx.mock
def test_tc_int_03_uncached_street_address_route() -> None:
    """TC-INT-03: Street Address Route uses Census Geocoder fallback within 3-call budget."""
    client = APIClient()

    # Mock Census Geocoder for Start and Finish addresses
    census_route = respx.get(url__startswith="https://geocoding.geo.census.gov/geocoder/locations/onelineaddress").respond(
        status_code=200,
        json={
            "result": {
                "addressMatches": [
                    {
                        "matchedAddress": "1600 Pennsylvania Ave NW, Washington, DC",
                        "coordinates": {"x": -77.0365, "y": 38.8977},
                        "addressComponents": {"state": "DC"},
                    }
                ]
            }
        },
    )

    coords = [(38.8977, -77.0365), (41.6, -83.5), (41.8781, -87.6298)]
    poly = encode_polyline(coords, precision=6)
    respx.get(url__startswith="https://router.project-osrm.org/route/v1/driving/").respond(
        status_code=200,
        json={"code": "Ok", "routes": [{"distance": 1100000.0, "duration": 40000.0, "geometry": poly}]},
    )

    resp = client.post(
        "/api/v1/route/",
        data={
            "start": "1600 Pennsylvania Ave NW, Washington, DC",
            "finish": "233 S Wacker Dr, Chicago, IL",
        },
        format="json",
    )
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["meta"]["external_api_calls"]["total_count"] <= 3


@pytest.mark.django_db
def test_tc_int_04_hard_call_budget_guard() -> None:
    """TC-INT-04: Hard Call-Budget Guard enforces max 3 calls."""
    budget = ExternalCallBudget(max_calls=3)
    budget.record_call("census_geocoder", "geocode_start", 50.0)
    budget.record_call("census_geocoder", "geocode_finish", 50.0)
    budget.record_call("osrm", "route", 100.0)

    assert budget.remaining == 0
    assert budget.total_count == 3

    with pytest.raises(ExternalCallBudgetExceededError):
        budget.check_can_call("valhalla", "fallback_route")


@pytest.mark.django_db
def test_tc_int_05_non_us_rejection() -> None:
    """TC-INT-05: Non-US endpoints rejected with 400 Bad Request and 0 routing calls."""
    client = APIClient()

    resp1 = client.post(
        "/api/v1/route/",
        data={"start": "Toronto, Ontario, Canada", "finish": "Buffalo, NY"},
        format="json",
    )
    assert resp1.status_code == status.HTTP_400_BAD_REQUEST
    assert resp1.json()["error"]["code"] == "LOCATION_OUTSIDE_USA"

    resp2 = client.post(
        "/api/v1/route/",
        data={"start": "Detroit, MI", "finish": "Monterrey, Mexico"},
        format="json",
    )
    assert resp2.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
@respx.mock
def test_tc_int_06_ocean_separated_route_error() -> None:
    """TC-INT-06: Ocean-separated route (Honolulu -> SF) returns 422 No Drivable Route."""
    client = APIClient()

    respx.get(url__startswith="https://router.project-osrm.org/route/v1/driving/").respond(
        status_code=200,
        json={"code": "NoRoute"},
    )
    respx.post("https://valhalla1.openstreetmap.de/route").respond(
        status_code=200,
        json={"trip": {"legs": []}},
    )

    resp = client.post(
        "/api/v1/route/",
        data={"start": "Honolulu, HI", "finish": "San Francisco, CA"},
        format="json",
    )
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert resp.json()["error"]["code"] == "NO_DRIVABLE_ROUTE"


@pytest.mark.django_db
@respx.mock
def test_tc_int_07_cross_border_shortcut_prevention_failover() -> None:
    """TC-INT-07: Detroit -> Buffalo cross-border route fails over to domestic Valhalla."""
    client = APIClient()

    # OSRM returns route cutting through Ontario, Canada (43.0, -80.5)
    foreign_coords = [(42.3314, -83.0458), (43.0, -80.5), (42.8864, -78.8784)]
    foreign_poly = encode_polyline(foreign_coords, precision=6)
    respx.get(url__startswith="https://router.project-osrm.org/route/v1/driving/").respond(
        status_code=200,
        json={"code": "Ok", "routes": [{"distance": 400000.0, "duration": 15000.0, "geometry": foreign_poly}]},
    )

    # Valhalla returns domestic route staying along I-90 in Ohio / Pennsylvania / New York
    domestic_coords = [(42.3314, -83.0458), (41.6, -83.5), (42.1, -80.1), (42.8864, -78.8784)]
    domestic_poly = encode_polyline(domestic_coords, precision=6)
    respx.post("https://valhalla1.openstreetmap.de/route").respond(
        status_code=200,
        json={
            "trip": {
                "summary": {"length": 340.0, "time": 18000.0},
                "legs": [{"shape": domestic_poly}],
            }
        },
    )

    resp = client.post(
        "/api/v1/route/",
        data={"start": "Detroit, MI", "finish": "Buffalo, NY"},
        format="json",
    )
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["route"]["provider"] == "valhalla"


@pytest.mark.django_db
@respx.mock
def test_tc_int_08_provider_failover_and_timeouts() -> None:
    """TC-INT-08: OSRM 503 fails over to Valhalla; dual failure returns 502; timeout returns 504."""
    client = APIClient()

    # 1. OSRM 503 -> Valhalla 200 OK
    respx.get(url__startswith="https://router.project-osrm.org/route/v1/driving/").respond(
        status_code=503, text="OSRM Overloaded"
    )
    coords = [(40.7128, -74.0060), (34.0522, -118.2437)]
    poly = encode_polyline(coords, precision=6)
    respx.post("https://valhalla1.openstreetmap.de/route").respond(
        status_code=200,
        json={
            "trip": {
                "summary": {"length": 500.0, "time": 20000.0},
                "legs": [{"shape": poly}],
            }
        },
    )

    resp_failover = client.post(
        "/api/v1/route/",
        data={"start": "New York, NY", "finish": "Los Angeles, CA"},
        format="json",
    )
    assert resp_failover.status_code == status.HTTP_200_OK
    assert resp_failover.json()["route"]["provider"] == "valhalla"

    # 2. Dual-provider failure -> 502 UPSTREAM_ROUTING_UNAVAILABLE
    cache.clear()
    respx.post("https://valhalla1.openstreetmap.de/route").respond(
        status_code=500, text="Valhalla Error"
    )
    resp_dual_fail = client.post(
        "/api/v1/route/",
        data={"start": "New York, NY", "finish": "Los Angeles, CA"},
        format="json",
    )
    assert resp_dual_fail.status_code == status.HTTP_502_BAD_GATEWAY
    assert resp_dual_fail.json()["error"]["code"] == "UPSTREAM_ROUTING_UNAVAILABLE"

    # 3. Timeout -> 504 UPSTREAM_SERVICE_TIMEOUT
    cache.clear()
    respx.get(url__startswith="https://router.project-osrm.org/route/v1/driving/").mock(
        side_effect=httpx.TimeoutException("Timeout")
    )
    respx.post("https://valhalla1.openstreetmap.de/route").mock(
        side_effect=httpx.TimeoutException("Timeout")
    )
    resp_timeout = client.post(
        "/api/v1/route/",
        data={"start": "New York, NY", "finish": "Los Angeles, CA"},
        format="json",
    )
    assert resp_timeout.status_code == status.HTTP_504_GATEWAY_TIMEOUT
    assert resp_timeout.json()["error"]["code"] == "UPSTREAM_SERVICE_TIMEOUT"

"""Unit tests for routing providers, polyline codec, boundary validator, and location resolver."""
from decimal import Decimal
import json
import pytest
import respx
import httpx
from apps.api.exceptions import (
    DomesticUSRouteUnavailableError,
    ExternalCallBudgetExceededError,
    LocationNotFoundError,
    LocationOutsideUSAError,
    NoDrivableRouteError,
    UpstreamRoutingUnavailableError,
)
from apps.routing.boundary import USBoundaryValidator
from apps.routing.call_budget import ExternalCallBudget
from apps.routing.models import USPlaceGazetteer
from apps.routing.providers.chain import RoutingProviderChain
from apps.routing.providers.osrm import OSRMRoutingClient
from apps.routing.providers.polyline_codec import decode_polyline, encode_polyline
from apps.routing.providers.valhalla import ValhallaRoutingClient
from apps.routing.resolver import USLocationResolver


def test_polyline_codec_roundtrip() -> None:
    original_coords = [
        (40.712800, -74.006000),
        (41.878100, -87.629800),
        (34.052200, -118.243700),
    ]
    encoded6 = encode_polyline(original_coords, precision=6)
    assert isinstance(encoded6, str)
    decoded6 = decode_polyline(encoded6, precision=6)
    assert len(decoded6) == len(original_coords)
    for (lat1, lon1), (lat2, lon2) in zip(original_coords, decoded6):
        assert abs(lat1 - lat2) < 1e-5
        assert abs(lon1 - lon2) < 1e-5

    # Precision 5 roundtrip
    encoded5 = encode_polyline(original_coords, precision=5)
    decoded5 = decode_polyline(encoded5, precision=5)
    assert len(decoded5) == len(original_coords)
    for (lat1, lon1), (lat2, lon2) in zip(original_coords, decoded5):
        assert abs(lat1 - lat2) < 1e-4
        assert abs(lon1 - lon2) < 1e-4


def test_external_call_budget() -> None:
    budget = ExternalCallBudget(max_calls=2)
    assert budget.total_count == 0
    assert budget.remaining == 2

    budget.check_can_call("osrm", "route")
    budget.record_call("osrm", "route", 120.5)
    assert budget.total_count == 1
    assert budget.remaining == 1

    budget.check_can_call("osrm", "route")
    budget.record_call("osrm", "route", 95.2)
    assert budget.total_count == 2
    assert budget.remaining == 0

    with pytest.raises(ExternalCallBudgetExceededError):
        budget.check_can_call("census_geocoder", "geocode")

    data = budget.to_dict()
    assert data["total_count"] == 2
    assert data["budget_limit"] == 2
    assert len(data["calls"]) == 2


def test_us_boundary_validator() -> None:
    validator = USBoundaryValidator.get_instance()

    # Valid US points
    assert validator.is_within_usa(40.7128, -74.0060)  # NYC
    assert validator.is_within_usa(34.0522, -118.2437)  # LA
    assert validator.is_within_usa(64.2008, -149.4937)  # Alaska
    assert validator.is_within_usa(21.3069, -157.8583)  # Hawaii
    assert validator.is_contiguous_us(40.7128, -74.0060)
    assert not validator.is_contiguous_us(64.2008, -149.4937)  # Alaska not CONUS

    # Invalid non-US points
    assert not validator.is_within_usa(43.6532, -79.3832)  # Toronto, Canada
    assert not validator.is_within_usa(19.4326, -99.1332)  # Mexico City
    assert not validator.is_within_usa(51.5074, -0.1278)  # London, UK
    assert not validator.is_within_usa(0.0, 0.0)  # Atlantic Ocean

    # Polyline route domestic check
    domestic_route = [(40.7128, -74.0060), (41.0, -80.0), (34.0522, -118.2437)]
    is_dom, _, _ = validator.validate_route_is_domestic(domestic_route)
    assert is_dom

    cross_border_route = [(42.3314, -83.0458), (43.0, -80.5), (42.8864, -78.8784)]
    is_dom2, bad_idx, bad_pt = validator.validate_route_is_domestic(cross_border_route)
    assert not is_dom2
    assert bad_idx >= 0
    assert bad_pt is not None


@pytest.mark.django_db
def test_location_resolver_coords_and_gazetteer() -> None:
    USPlaceGazetteer.objects.create(
        normalized_query="austin,tx",
        display_name="Austin, TX",
        city="Austin",
        state_code="TX",
        place_type="CITY",
        latitude=30.2672,
        longitude=-97.7431,
        population=961855,
    )

    resolver = USLocationResolver()
    budget = ExternalCallBudget(max_calls=3)

    # 1. Coordinate string
    res_coord = resolver.resolve("30.2672, -97.7431", budget)
    assert res_coord.source == "coordinates"
    assert abs(res_coord.latitude - 30.2672) < 1e-4
    assert budget.total_count == 0  # 0 calls

    # 2. Coordinate dict
    res_dict = resolver.resolve({"lat": 30.2672, "lng": -97.7431}, budget)
    assert res_dict.source == "coordinates"
    assert budget.total_count == 0

    # 3. Local Gazetteer query
    res_gaz = resolver.resolve("Austin, Texas", budget)
    assert res_gaz.source == "local_gazetteer"
    assert res_gaz.state == "TX"
    assert budget.total_count == 0

    # 4. Explicit non-US rejection
    with pytest.raises(LocationOutsideUSAError):
        resolver.resolve("Toronto, Ontario, Canada", budget)

    # 5. Empty query rejection
    with pytest.raises(LocationNotFoundError):
        resolver.resolve("   ", budget)


@respx.mock
def test_osrm_and_chain_routing() -> None:
    # Setup mock OSRM response
    coords = [(40.7128, -74.0060), (41.0, -80.0), (34.0522, -118.2437)]
    poly = encode_polyline(coords, precision=6)
    osrm_data = {
        "code": "Ok",
        "routes": [
            {
                "distance": 4500000.0,  # ~2796 miles
                "duration": 144000.0,  # ~40 hours
                "geometry": poly,
            }
        ],
    }

    respx.get("https://router.project-osrm.org/route/v1/driving/-74.006000,40.712800;-118.243700,34.052200").respond(
        status_code=200,
        json=osrm_data,
    )

    client = OSRMRoutingClient()
    budget = ExternalCallBudget(max_calls=3)
    route_res = client.route((40.7128, -74.0060), (34.0522, -118.2437), budget)

    assert route_res.provider_name == "osrm"
    assert route_res.distance_miles > 2700.0
    assert route_res.is_domestic_us
    assert budget.total_count == 1

    # Test RoutingProviderChain
    chain = RoutingProviderChain(primary=client)
    chain_budget = ExternalCallBudget(max_calls=3)
    chain_res = chain.route((40.7128, -74.0060), (34.0522, -118.2437), chain_budget)
    assert chain_res.distance_miles > 2700.0
    assert chain_budget.total_count == 1

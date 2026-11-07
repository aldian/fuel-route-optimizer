"""Unit tests for USLocationResolver external geocoder fallbacks, error states, and caching."""
import httpx
import pytest
import respx
from apps.api.exceptions import LocationNotFoundError, LocationOutsideUSAError
from apps.routing.call_budget import ExternalCallBudget
from apps.routing.resolver import USLocationResolver


@pytest.fixture
def resolver() -> USLocationResolver:
    return USLocationResolver()


@pytest.fixture
def budget() -> ExternalCallBudget:
    return ExternalCallBudget(max_calls=3)


@pytest.mark.django_db
@respx.mock
def test_resolver_census_geocoder_success(
    resolver: USLocationResolver, budget: ExternalCallBudget
) -> None:
    respx.get("https://geocoding.geo.census.gov/geocoder/locations/onelineaddress").respond(
        status_code=200,
        json={
            "result": {
                "addressMatches": [
                    {
                        "matchedAddress": "1600 Pennsylvania Ave NW, Washington, DC, 20500",
                        "coordinates": {"x": -77.0365, "y": 38.8977},
                        "addressComponents": {"state": "DC"},
                    }
                ]
            }
        },
    )

    res = resolver.resolve("1600 Pennsylvania Ave NW, Washington DC", budget)
    assert res.source == "census_geocoder"
    assert abs(res.latitude - 38.8977) < 1e-4
    assert abs(res.longitude - -77.0365) < 1e-4
    assert res.state == "DC"

    # Second call should hit cache and use 0 external calls
    calls_before = budget.total_count
    res_cached = resolver.resolve("1600 Pennsylvania Ave NW, Washington DC", budget)
    assert res_cached.source == "census_geocoder"
    assert budget.total_count == calls_before


@pytest.mark.django_db
@respx.mock
def test_resolver_photon_fallback_success(
    resolver: USLocationResolver, budget: ExternalCallBudget
) -> None:
    # Census returns empty matches
    respx.get("https://geocoding.geo.census.gov/geocoder/locations/onelineaddress").respond(
        status_code=200,
        json={"result": {"addressMatches": []}},
    )

    # Photon returns valid US match
    respx.get("https://photon.komoot.io/api/").respond(
        status_code=200,
        json={
            "features": [
                {
                    "geometry": {"coordinates": [-122.4194, 37.7749]},
                    "properties": {
                        "name": "Market Street",
                        "city": "San Francisco",
                        "state": "CA",
                        "countrycode": "US",
                    },
                }
            ]
        },
    )

    res = resolver.resolve("Market Street San Francisco", budget)
    assert res.source == "photon_geocoder"
    assert abs(res.latitude - 37.7749) < 1e-4
    assert abs(res.longitude - -122.4194) < 1e-4
    assert res.state == "CA"


@pytest.mark.django_db
@respx.mock
def test_resolver_all_external_fail_raises_not_found(
    resolver: USLocationResolver, budget: ExternalCallBudget
) -> None:
    # Census fails
    respx.get("https://geocoding.geo.census.gov/geocoder/locations/onelineaddress").mock(
        side_effect=httpx.ConnectError("Connection refused")
    )
    # Photon fails
    respx.get("https://photon.komoot.io/api/").mock(
        side_effect=httpx.ConnectError("Connection refused")
    )

    with pytest.raises(LocationNotFoundError):
        resolver.resolve("NonExistentUnknownPlace12345", budget)


@pytest.mark.django_db
def test_resolver_coordinate_bounds_and_invalid(
    resolver: USLocationResolver, budget: ExternalCallBudget
) -> None:
    # Outside USA coordinates
    with pytest.raises(LocationOutsideUSAError):
        resolver.resolve("51.5074, -0.1278", budget)  # London

    with pytest.raises(LocationOutsideUSAError):
        resolver.resolve({"lat": 43.6532, "lng": -79.3832}, budget)  # Toronto

    # Outside USA / invalid coordinate range
    with pytest.raises(LocationOutsideUSAError):
        resolver.resolve("999.0, -74.0", budget)

    # Invalid dict
    with pytest.raises(LocationNotFoundError):
        resolver.resolve({"bad_key": 123}, budget)


@pytest.mark.django_db
def test_resolver_resolve_both_concurrent(
    resolver: USLocationResolver, budget: ExternalCallBudget
) -> None:
    start_res, finish_res = resolver.resolve_both(
        "40.7128, -74.0060",
        "34.0522, -118.2437",
        budget,
    )
    assert start_res.source == "coordinates"
    assert finish_res.source == "coordinates"

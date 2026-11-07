"""Unit tests for OSRM, Valhalla, and RoutingProviderChain error and failover handling."""
import httpx
import pytest
import respx
from apps.api.exceptions import (
    DomesticUSRouteUnavailableError,
    NoDrivableRouteError,
    UpstreamRoutingUnavailableError,
    UpstreamTimeoutError,
)
from apps.routing.call_budget import ExternalCallBudget
from apps.routing.providers.chain import RoutingProviderChain
from apps.routing.providers.osrm import OSRMRoutingClient
from apps.routing.providers.polyline_codec import encode_polyline
from apps.routing.providers.protocol import RouteResult
from apps.routing.providers.valhalla import ValhallaRoutingClient


@pytest.fixture
def mock_budget() -> ExternalCallBudget:
    return ExternalCallBudget(max_calls=3)


@respx.mock
def test_valhalla_routing_client_success(mock_budget: ExternalCallBudget) -> None:
    coords = [(40.7128, -74.0060), (39.9526, -75.1652)]
    poly = encode_polyline(coords, precision=6)
    respx.post("https://valhalla1.openstreetmap.de/route").respond(
        status_code=200,
        json={
            "trip": {
                "summary": {"length": 95.5, "time": 5400.0},
                "legs": [{"shape": poly}],
            }
        },
    )

    client = ValhallaRoutingClient()
    result = client.route((40.7128, -74.0060), (39.9526, -75.1652), mock_budget)

    assert result.provider_name == "valhalla"
    assert abs(result.distance_miles - 95.5) < 0.1
    assert result.duration_seconds == 5400.0
    assert result.is_domestic_us
    assert len(result.coordinates) == 2


@respx.mock
def test_valhalla_routing_client_errors(mock_budget: ExternalCallBudget) -> None:
    client = ValhallaRoutingClient()

    # 1. Timeout
    respx.post("https://valhalla1.openstreetmap.de/route").mock(
        side_effect=httpx.TimeoutException("Timeout")
    )
    with pytest.raises(UpstreamTimeoutError):
        client.route((40.0, -74.0), (41.0, -75.0), mock_budget)

    # 2. HTTP 500 error
    respx.post("https://valhalla1.openstreetmap.de/route").respond(
        status_code=500, text="Internal Server Error"
    )
    with pytest.raises(UpstreamRoutingUnavailableError):
        client.route((40.0, -74.0), (41.0, -75.0), mock_budget)

    # 3. No drivable road legs
    respx.post("https://valhalla1.openstreetmap.de/route").respond(
        status_code=200, json={"trip": {"legs": []}}
    )
    with pytest.raises(NoDrivableRouteError):
        client.route((40.0, -74.0), (41.0, -75.0), mock_budget)


@respx.mock
def test_osrm_routing_client_errors(mock_budget: ExternalCallBudget) -> None:
    client = OSRMRoutingClient()

    # 1. Timeout
    respx.get(url__startswith="https://router.project-osrm.org/route/v1/driving/").mock(
        side_effect=httpx.TimeoutException("Timeout")
    )
    with pytest.raises(UpstreamTimeoutError):
        client.route((40.0, -74.0), (41.0, -75.0), mock_budget)

    # 2. HTTP 503
    respx.get(url__startswith="https://router.project-osrm.org/route/v1/driving/").respond(
        status_code=503, text="Service Unavailable"
    )
    with pytest.raises(UpstreamRoutingUnavailableError):
        client.route((40.0, -74.0), (41.0, -75.0), mock_budget)

    # 3. No route found
    respx.get(url__startswith="https://router.project-osrm.org/route/v1/driving/").respond(
        status_code=200, json={"code": "NoRoute"}
    )
    with pytest.raises(NoDrivableRouteError):
        client.route((40.0, -74.0), (41.0, -75.0), mock_budget)


def test_routing_provider_chain_failover() -> None:
    coords = [(40.7128, -74.0060), (39.9526, -75.1652)]
    poly = encode_polyline(coords, precision=6)
    mock_res_domestic = RouteResult(
        distance_miles=95.0,
        duration_seconds=5000.0,
        polyline6=poly,
        coordinates=coords,
        bbox=(-75.1652, 39.9526, -74.0060, 40.7128),
        provider_name="valhalla",
        is_domestic_us=True,
    )

    class FailingPrimary:
        def route(self, origin, destination, budget, prefer_domestic=True):
            raise UpstreamRoutingUnavailableError("Primary failed")

    class SuccessSecondary:
        def route(self, origin, destination, budget, prefer_domestic=True):
            return mock_res_domestic

    # Primary fails -> fallback succeeds
    chain = RoutingProviderChain(primary=FailingPrimary(), secondary=SuccessSecondary())
    budget = ExternalCallBudget(max_calls=3)
    res = chain.route((40.0, -74.0), (39.9, -75.1), budget)
    assert res.provider_name == "valhalla"
    assert res.is_domestic_us


def test_routing_provider_chain_all_fail() -> None:
    class FailingPrimary:
        def route(self, origin, destination, budget, prefer_domestic=True):
            raise UpstreamRoutingUnavailableError("Primary down")

    class FailingSecondary:
        def route(self, origin, destination, budget, prefer_domestic=True):
            raise UpstreamRoutingUnavailableError("Secondary down")

    chain = RoutingProviderChain(primary=FailingPrimary(), secondary=FailingSecondary())
    budget = ExternalCallBudget(max_calls=3)
    with pytest.raises(UpstreamRoutingUnavailableError):
        chain.route((40.0, -74.0), (39.9, -75.1), budget)


def test_routing_provider_chain_domestic_border_rejection() -> None:
    coords = [(42.3314, -83.0458), (43.0, -80.5), (42.8864, -78.8784)]
    poly = encode_polyline(coords, precision=6)
    non_domestic_res = RouteResult(
        distance_miles=200.0,
        duration_seconds=10000.0,
        polyline6=poly,
        coordinates=coords,
        bbox=(-83.0, 42.0, -78.0, 43.0),
        provider_name="osrm",
        is_domestic_us=False,
    )

    class NonDomesticPrimary:
        def route(self, origin, destination, budget, prefer_domestic=True):
            return non_domestic_res

    class NonDomesticSecondary:
        def route(self, origin, destination, budget, prefer_domestic=True):
            return non_domestic_res

    chain = RoutingProviderChain(primary=NonDomesticPrimary(), secondary=NonDomesticSecondary())
    budget = ExternalCallBudget(max_calls=3)
    with pytest.raises(DomesticUSRouteUnavailableError):
        chain.route((42.3, -83.0), (42.8, -78.8), budget, prefer_domestic=True)

"""Valhalla driving route client implementing the RoutingProvider protocol."""
import time
from typing import Any
import httpx
from django.conf import settings
from apps.api.exceptions import NoDrivableRouteError, UpstreamRoutingUnavailableError, UpstreamTimeoutError
from apps.routing.boundary import USBoundaryValidator
from apps.routing.call_budget import ExternalCallBudget
from apps.routing.providers.polyline_codec import decode_polyline
from apps.routing.providers.protocol import RouteResult


class ValhallaRoutingClient:
    """Secondary zero-key routing client querying FOSSGIS Valhalla with border penalties."""

    def __init__(
        self,
        base_url: str | None = None,
        http_client: httpx.Client | None = None,
        validator: USBoundaryValidator | None = None,
    ) -> None:
        self.base_url = (base_url or getattr(settings, "VALHALLA_ROUTE_URL", "https://valhalla1.openstreetmap.de/route")).rstrip("/")
        self._client = http_client
        self.validator = validator or USBoundaryValidator.get_instance()

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=httpx.Timeout(
                    connect=getattr(settings, "EXTERNAL_CONNECT_TIMEOUT_SEC", 2.0),
                    read=getattr(settings, "EXTERNAL_READ_TIMEOUT_SEC", 4.0),
                    write=2.0,
                    pool=5.0,
                ),
                headers={"User-Agent": "FuelRouteOptimizer/1.0 (spotter-assessment)"},
            )
        return self._client

    def route(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        budget: ExternalCallBudget,
        prefer_domestic: bool = True,
    ) -> RouteResult:
        """Fetch driving route via Valhalla with high country crossing penalties."""
        budget.check_can_call("valhalla", "driving_route")
        lat1, lon1 = origin
        lat2, lon2 = destination

        payload: dict[str, Any] = {
            "locations": [
                {"lat": lat1, "lon": lon1},
                {"lat": lat2, "lon": lon2},
            ],
            "costing": "auto",
            "costing_options": {
                "auto": {
                    "country_crossing_penalty": 100000.0 if prefer_domestic else 0.0,
                    "country_crossing_cost": 100000.0 if prefer_domestic else 0.0,
                }
            },
            "directions_options": {"units": "miles"},
        }

        t0 = time.perf_counter()
        try:
            client = self._get_client()
            response = client.post(self.base_url, json=payload)
            duration_ms = (time.perf_counter() - t0) * 1000.0
            budget.record_call("valhalla", "driving_route", duration_ms)
        except httpx.TimeoutException as exc:
            raise UpstreamTimeoutError(f"Valhalla routing request timed out after {time.perf_counter() - t0:.2f}s.") from exc
        except Exception as exc:
            raise UpstreamRoutingUnavailableError(f"Failed to connect to Valhalla: {exc}") from exc

        if response.status_code != 200:
            raise UpstreamRoutingUnavailableError(
                f"Valhalla returned HTTP {response.status_code}: {response.text[:200]}"
            )

        data = response.json()
        trip = data.get("trip")
        if not trip or not trip.get("legs"):
            raise NoDrivableRouteError(
                f"No drivable road route found by Valhalla between ({lat1:.4f}, {lon1:.4f}) and ({lat2:.4f}, {lon2:.4f})."
            )

        summary = trip["summary"]
        distance_miles = float(summary["length"])
        duration_seconds = float(summary["time"])

        shape_polyline6 = trip["legs"][0]["shape"]
        coordinates = decode_polyline(shape_polyline6, precision=6)
        is_domestic, _, _ = self.validator.validate_route_is_domestic(coordinates)

        lats = [c[0] for c in coordinates]
        lons = [c[1] for c in coordinates]
        bbox = (min(lons), min(lats), max(lons), max(lats))

        return RouteResult(
            distance_miles=round(distance_miles, 2),
            duration_seconds=round(duration_seconds, 1),
            polyline6=shape_polyline6,
            coordinates=coordinates,
            bbox=bbox,
            provider_name="valhalla",
            is_domestic_us=is_domestic,
        )

"""OSRM driving route client implementing the RoutingProvider protocol."""
import time
from typing import Any
import httpx
from django.conf import settings
from apps.api.exceptions import NoDrivableRouteError, UpstreamRoutingUnavailableError, UpstreamTimeoutError
from apps.routing.boundary import USBoundaryValidator
from apps.routing.call_budget import ExternalCallBudget
from apps.routing.providers.polyline_codec import decode_polyline
from apps.routing.providers.protocol import METERS_TO_MILES, RouteResult


class OSRMRoutingClient:
    """Primary zero-key routing client querying public Project OSRM."""

    def __init__(
        self,
        base_url: str | None = None,
        http_client: httpx.Client | None = None,
        validator: USBoundaryValidator | None = None,
    ) -> None:
        self.base_url = (base_url or getattr(settings, "OSRM_ROUTE_URL", "https://router.project-osrm.org/route/v1/driving")).rstrip("/")
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
        """Fetch driving route via single OSRM call."""
        budget.check_can_call("osrm", "driving_route")
        lat1, lon1 = origin
        lat2, lon2 = destination

        # OSRM coordinate order is {lon},{lat}
        url = f"{self.base_url}/{lon1:.6f},{lat1:.6f};{lon2:.6f},{lat2:.6f}"
        params = {
            "overview": "full",
            "geometries": "polyline6",
            "alternatives": "true" if prefer_domestic else "false",
            "steps": "false",
        }

        t0 = time.perf_counter()
        try:
            client = self._get_client()
            response = client.get(url, params=params)
            duration_ms = (time.perf_counter() - t0) * 1000.0
            budget.record_call("osrm", "driving_route", duration_ms)
        except httpx.TimeoutException as exc:
            raise UpstreamTimeoutError(f"OSRM routing request timed out after {time.perf_counter() - t0:.2f}s.") from exc
        except Exception as exc:
            raise UpstreamRoutingUnavailableError(f"Failed to connect to OSRM: {exc}") from exc

        if response.status_code != 200:
            raise UpstreamRoutingUnavailableError(
                f"OSRM returned HTTP {response.status_code}: {response.text[:200]}"
            )

        data = response.json()
        code = data.get("code")
        if code != "Ok" or not data.get("routes"):
            raise NoDrivableRouteError(
                f"No drivable road route found between ({lat1:.4f}, {lon1:.4f}) and ({lat2:.4f}, {lon2:.4f})."
            )

        routes = data["routes"]
        selected_route: dict[str, Any] = routes[0]
        selected_coords: list[tuple[float, float]] = decode_polyline(selected_route["geometry"], precision=6)
        is_domestic, _, _ = self.validator.validate_route_is_domestic(selected_coords)

        # If primary route leaves US (e.g. Detroit to Buffalo via Ontario), check alternatives
        if prefer_domestic and not is_domestic and len(routes) > 1:
            for alt in routes[1:]:
                alt_coords = decode_polyline(alt["geometry"], precision=6)
                alt_domestic, _, _ = self.validator.validate_route_is_domestic(alt_coords)
                if alt_domestic:
                    selected_route = alt
                    selected_coords = alt_coords
                    is_domestic = True
                    break

        distance_meters = float(selected_route["distance"])
        duration_seconds = float(selected_route["duration"])
        distance_miles = distance_meters * METERS_TO_MILES

        # Compute bounding box (min_lon, min_lat, max_lon, max_lat)
        lats = [c[0] for c in selected_coords]
        lons = [c[1] for c in selected_coords]
        bbox = (min(lons), min(lats), max(lons), max(lats))

        return RouteResult(
            distance_miles=round(distance_miles, 2),
            duration_seconds=round(duration_seconds, 1),
            polyline6=selected_route["geometry"],
            coordinates=selected_coords,
            bbox=bbox,
            provider_name="osrm",
            is_domestic_us=is_domestic,
        )

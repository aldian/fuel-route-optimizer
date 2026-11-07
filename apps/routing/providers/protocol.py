"""Routing provider interfaces and result dataclasses."""
from dataclasses import dataclass
from typing import Protocol
from apps.routing.call_budget import ExternalCallBudget

METERS_TO_MILES = 1.0 / 1609.344


@dataclass(slots=True, frozen=True)
class RouteResult:
    """Standardized driving route result from an upstream routing engine."""

    distance_miles: float
    duration_seconds: float
    polyline6: str
    coordinates: list[tuple[float, float]]  # (latitude, longitude)
    bbox: tuple[float, float, float, float]  # (min_lon, min_lat, max_lon, max_lat)
    provider_name: str
    is_domestic_us: bool = True


class RoutingProvider(Protocol):
    """Protocol for pluggable driving route providers."""

    def route(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        budget: ExternalCallBudget,
        prefer_domestic: bool = True,
    ) -> RouteResult:
        """Fetch driving route between origin (lat, lon) and destination (lat, lon)."""
        ...

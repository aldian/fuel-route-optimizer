"""Routing provider chain coordinating primary and fallback routing engines."""
from apps.api.exceptions import DomesticUSRouteUnavailableError, UpstreamRoutingUnavailableError
from apps.routing.call_budget import ExternalCallBudget
from apps.routing.providers.osrm import OSRMRoutingClient
from apps.routing.providers.protocol import RouteResult, RoutingProvider
from apps.routing.providers.valhalla import ValhallaRoutingClient


class RoutingProviderChain:
    """Tries primary OSRM provider and seamlessly fails over to Valhalla within call budget."""

    def __init__(
        self,
        primary: RoutingProvider | None = None,
        secondary: RoutingProvider | None = None,
    ) -> None:
        self.primary = primary or OSRMRoutingClient()
        self.secondary = secondary or ValhallaRoutingClient()

    def route(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        budget: ExternalCallBudget,
        prefer_domestic: bool = True,
    ) -> RouteResult:
        """Fetch driving route with graceful failover."""
        primary_err: Exception | None = None
        route_res: RouteResult | None = None

        try:
            route_res = self.primary.route(
                origin, destination, budget, prefer_domestic=prefer_domestic
            )
            # If primary succeeded and route is domestic (or domestic not preferred), we are done!
            if not prefer_domestic or route_res.is_domestic_us:
                return route_res
        except Exception as exc:
            primary_err = exc

        # Failover to secondary if primary failed or primary route crossed international borders
        if self.secondary is not None and budget.remaining > 0:
            try:
                secondary_res = self.secondary.route(
                    origin, destination, budget, prefer_domestic=prefer_domestic
                )
                if not prefer_domestic or secondary_res.is_domestic_us:
                    return secondary_res
                # If secondary route also crossed border, return domestic error if preferred
                if prefer_domestic and not secondary_res.is_domestic_us:
                    raise DomesticUSRouteUnavailableError(
                        "Route requires crossing international borders and no domestic US route was available."
                    )
                return secondary_res
            except Exception:
                pass

        if route_res is not None:
            if prefer_domestic and not route_res.is_domestic_us:
                raise DomesticUSRouteUnavailableError(
                    "Route requires crossing international borders and no domestic US route was available."
                )
            return route_res

        if primary_err:
            raise primary_err

        raise UpstreamRoutingUnavailableError("All routing providers failed to produce a valid route.")

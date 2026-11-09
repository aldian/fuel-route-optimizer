"""Central route optimization service orchestrating resolution, routing, spatial search, and solver."""
from dataclasses import dataclass
import hashlib
import time
from typing import Any
from django.core.cache import cache
from apps.optimizer.domain import FuelPlan, VehicleSpec
from apps.optimizer.projector import RouteProjector
from apps.optimizer.solver import MinCostRefuelSolver
from apps.routing.call_budget import ExternalCallBudget
from apps.routing.providers.chain import RoutingProviderChain
from apps.routing.providers.protocol import RouteResult
from apps.routing.resolver import ResolvedLocation, USLocationResolver
from apps.stations.spatial import StationSpatialIndex


@dataclass(slots=True)
class OptimizationResult:
    plan: FuelPlan
    route: RouteResult
    origin: ResolvedLocation
    destination: ResolvedLocation
    budget: ExternalCallBudget
    compute_time_ms: float
    cache_hit: bool = False


class RouteOptimizationService:
    """Orchestrates end-to-end route optimization with two-tier caching and call budget tracking."""

    def __init__(
        self,
        resolver: USLocationResolver | None = None,
        router: RoutingProviderChain | None = None,
        projector: RouteProjector | None = None,
        solver: MinCostRefuelSolver | None = None,
    ) -> None:
        self.resolver = resolver or USLocationResolver()
        self.router = router or RoutingProviderChain()
        self.projector = projector or RouteProjector()
        self.solver = solver or MinCostRefuelSolver()

    def optimize_route(
        self,
        start_input: str | dict[str, Any],
        finish_input: str | dict[str, Any],
        vehicle_spec: VehicleSpec | None = None,
        budget: ExternalCallBudget | None = None,
        prefer_domestic: bool = True,
    ) -> OptimizationResult:
        t0 = time.perf_counter()
        budget = budget or ExternalCallBudget(max_calls=3)
        vehicle_spec = vehicle_spec or VehicleSpec()
        spatial_idx = StationSpatialIndex.get_instance()
        data_ver = spatial_idx.data_version

        # 1. Check Full Optimization Cache
        plan_cache_key = self._build_plan_cache_key(start_input, finish_input, vehicle_spec, data_ver)
        try:
            cached_result = cache.get(plan_cache_key)
        except Exception:
            cached_result = None
        if cached_result is not None:
            cached_result.budget = budget
            cached_result.cache_hit = True
            cached_result.compute_time_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            return cached_result

        # 2. Resolve Start & Finish Locations (Local Gazetteer -> Geocoders)
        origin_loc, dest_loc = self.resolver.resolve_both(start_input, finish_input, budget)

        # 3. Fetch Driving Route Geometry (OSRM -> Valhalla) with Route Cache
        route_cache_key = f"route:v1:{origin_loc.latitude:.4f},{origin_loc.longitude:.4f}:{dest_loc.latitude:.4f},{dest_loc.longitude:.4f}"
        try:
            route_result: RouteResult | None = cache.get(route_cache_key)
        except Exception:
            route_result = None

        if route_result is None:
            origin_coords = (origin_loc.latitude, origin_loc.longitude)
            dest_coords = (dest_loc.latitude, dest_loc.longitude)
            route_result = self.router.route(
                origin_coords, dest_coords, budget, prefer_domestic=prefer_domestic
            )
            try:
                cache.set(route_cache_key, route_result, timeout=86400)
            except Exception:
                pass

        # 4. Spatial Corridor Query & Candidate Projection
        candidates = self.projector.project_candidates(
            route_result.coordinates, route_result.distance_miles, vehicle_spec
        )

        # 5. Exact Minimum-Cost Refueling Solver
        fuel_plan = self.solver.solve(
            route_result.distance_miles, candidates, vehicle_spec
        )

        compute_elapsed_ms = (time.perf_counter() - t0) * 1000.0
        opt_result = OptimizationResult(
            plan=fuel_plan,
            route=route_result,
            origin=origin_loc,
            destination=dest_loc,
            budget=budget,
            compute_time_ms=round(compute_elapsed_ms, 2),
            cache_hit=False,
        )

        # Store in optimization cache
        try:
            cache.set(plan_cache_key, opt_result, timeout=3600)
        except Exception:
            pass
        return opt_result

    @staticmethod
    def _build_plan_cache_key(
        start: str | dict[str, Any],
        finish: str | dict[str, Any],
        spec: VehicleSpec,
        data_ver: str,
    ) -> str:
        s_str = json.dumps(start, sort_keys=True) if isinstance(start, dict) else str(start).strip().lower()
        f_str = json.dumps(finish, sort_keys=True) if isinstance(finish, dict) else str(finish).strip().lower()
        spec_str = f"{spec.max_range_miles}:{spec.mpg}:{spec.initial_fuel_gallons}:{spec.corridor_miles}"
        raw = f"{s_str}:{f_str}:{spec_str}:{data_ver}"
        return f"plan:v1:{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:24]}"

"""Domain models and data structures for fuel route optimization."""
from dataclasses import dataclass, field
from decimal import Decimal
from apps.stations.spatial import IndexedStation


@dataclass(slots=True, frozen=True)
class VehicleSpec:
    """Vehicle range and consumption parameters."""

    max_range_miles: float = 500.0
    mpg: float = 10.0
    initial_fuel_gallons: float = 50.0  # Default full tank (500 miles / 10 mpg)
    corridor_miles: float = 5.0
    min_stop_gallons: float = 2.0

    @property
    def tank_capacity_gallons(self) -> float:
        return self.max_range_miles / self.mpg


@dataclass(slots=True)
class ProjectedStation:
    """Candidate station projected onto the route polyline."""

    station: IndexedStation
    route_mile_marker: float
    off_route_distance_miles: float
    effective_price: float

    @property
    def opis_truckstop_id(self) -> int:
        return self.station.opis_truckstop_id

    @property
    def retail_price(self) -> Decimal:
        return self.station.retail_price


@dataclass(slots=True, frozen=True)
class PlannedStop:
    """An optimal scheduled refueling stop along the route."""

    stop_number: int
    station: IndexedStation
    route_mile_marker: float
    distance_from_previous_miles: float
    off_route_distance_miles: float
    retail_price_per_gallon_usd: Decimal
    gallons_purchased: Decimal
    cost_usd: Decimal
    tank_gallons_before: Decimal
    tank_gallons_after: Decimal


@dataclass(slots=True)
class FuelPlan:
    """Complete fuel optimization result with transparent dual cost metrics."""

    total_distance_miles: float
    total_money_spent_on_fuel_usd: Decimal
    en_route_pump_cost_usd: Decimal
    initial_tank_fuel_value_usd: Decimal
    vehicle_max_range_miles: float
    vehicle_mpg: float
    tank_capacity_gallons: float
    initial_fuel_gallons: float
    ending_fuel_gallons: Decimal
    total_fuel_consumed_gallons: Decimal
    total_gallons_purchased_en_route: Decimal
    stops: list[PlannedStop] = field(default_factory=list)
    recommended_cheapest_station: ProjectedStation | None = None

    @property
    def stop_count(self) -> int:
        return len(self.stops)

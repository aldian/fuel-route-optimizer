"""Unit tests for RouteProjector and MinCostRefuelSolver."""
from decimal import Decimal
import pytest
from apps.api.exceptions import InsufficientFuelStationsError
from apps.optimizer.domain import ProjectedStation, VehicleSpec
from apps.optimizer.projector import RouteProjector
from apps.optimizer.solver import MinCostRefuelSolver
from apps.stations.models import FuelStation
from apps.stations.spatial import IndexedStation, StationSpatialIndex


def make_indexed_station(
    opis_id: int,
    name: str,
    price: str,
    lat: float,
    lon: float,
    city: str = "TestCity",
    state: str = "TX",
) -> IndexedStation:
    return IndexedStation(
        id=opis_id,
        opis_truckstop_id=opis_id,
        truckstop_name=name,
        address="I-40",
        city=city,
        state=state,
        rack_id=1,
        retail_price=Decimal(price),
        latitude=lat,
        longitude=lon,
        ecef_x=0.0,
        ecef_y=0.0,
        ecef_z=0.0,
        geocode_precision=FuelStation.PRECISION_HIGHWAY_EXIT,
    )


def test_refuel_solver_short_route_zero_stops() -> None:
    solver = MinCostRefuelSolver()
    st1 = ProjectedStation(
        station=make_indexed_station(1, "Primm Station", "3.250", 35.6, -115.3, "Primm", "NV"),
        route_mile_marker=180.0,
        off_route_distance_miles=0.2,
        effective_price=3.250,
    )

    # 270-mile route, vehicle starts with 50 gal (500-mile range)
    spec = VehicleSpec(max_range_miles=500.0, mpg=10.0, initial_fuel_gallons=50.0)
    plan = solver.solve(total_distance_miles=270.0, stations=[st1], spec=spec)

    assert len(plan.stops) == 0
    assert plan.en_route_pump_cost_usd == Decimal("0.00")
    assert plan.ending_fuel_gallons == Decimal("23.000")  # 50 - 27
    assert plan.total_fuel_consumed_gallons == Decimal("27.000")
    # Initial fuel valuation: 27 gal * $3.250 = $87.75
    assert plan.total_money_spent_on_fuel_usd == Decimal("87.75")
    assert plan.recommended_cheapest_station is not None
    assert plan.recommended_cheapest_station.opis_truckstop_id == 1


def test_refuel_solver_zero_distance() -> None:
    solver = MinCostRefuelSolver()
    plan = solver.solve(total_distance_miles=0.0, stations=[], spec=VehicleSpec())
    assert plan.total_distance_miles == 0.0
    assert len(plan.stops) == 0
    assert plan.total_money_spent_on_fuel_usd == Decimal("0.00")
    assert plan.en_route_pump_cost_usd == Decimal("0.00")


def test_refuel_solver_opportunistic_fill_and_step_down() -> None:
    solver = MinCostRefuelSolver()
    # Stations at mile 200 ($2.80), mile 450 ($4.50), mile 700 ($3.10) along a 1000-mile route
    st1 = ProjectedStation(
        station=make_indexed_station(1, "Cheap Oasis", "2.800", 35.0, -100.0),
        route_mile_marker=200.0,
        off_route_distance_miles=0.1,
        effective_price=2.800,
    )
    st2 = ProjectedStation(
        station=make_indexed_station(2, "Expensive Desert", "4.500", 35.0, -104.0),
        route_mile_marker=450.0,
        off_route_distance_miles=0.1,
        effective_price=4.500,
    )
    st3 = ProjectedStation(
        station=make_indexed_station(3, "Midland Station", "3.100", 35.0, -108.0),
        route_mile_marker=700.0,
        off_route_distance_miles=0.1,
        effective_price=3.100,
    )

    spec = VehicleSpec(max_range_miles=500.0, mpg=10.0, initial_fuel_gallons=50.0)
    plan = solver.solve(total_distance_miles=1000.0, stations=[st1, st2, st3], spec=spec)

    # Should top up at cheap oasis (mile 200) to full tank (50 gal), completely avoiding expensive desert!
    assert len(plan.stops) >= 1
    stop_stations = [s.station.opis_truckstop_id for s in plan.stops]
    assert 1 in stop_stations  # Bought at Cheap Oasis
    assert 2 not in stop_stations  # Successfully skipped expensive station!
    assert plan.total_fuel_consumed_gallons == Decimal("100.000")  # 1000 mi / 10 mpg
    assert plan.en_route_pump_cost_usd == sum(s.cost_usd for s in plan.stops)


def test_refuel_solver_unbridgeable_gap() -> None:
    solver = MinCostRefuelSolver()
    # Gap from 0 to 600 miles exceeds 500-mile range
    st1 = ProjectedStation(
        station=make_indexed_station(1, "Too Far", "3.000", 35.0, -100.0),
        route_mile_marker=600.0,
        off_route_distance_miles=0.1,
        effective_price=3.000,
    )
    with pytest.raises(InsufficientFuelStationsError):
        solver.solve(total_distance_miles=800.0, stations=[st1], spec=VehicleSpec(max_range_miles=500.0))


def test_route_projector_densify_and_projection() -> None:
    coords = [(40.0, -74.0), (41.0, -75.0)]
    projector = RouteProjector()
    densified, mileages = projector.densify_polyline(coords, max_spacing_miles=5.0)
    assert len(densified) > len(coords)
    assert len(densified) == len(mileages)
    assert mileages[0] == 0.0
    assert mileages[-1] > 80.0

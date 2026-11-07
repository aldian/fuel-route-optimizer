"""Exact Minimum-Cost Refueling Solver (O(K log K)) and Decimal Cost Accounting."""
from decimal import Decimal, ROUND_HALF_UP
from typing import Any
from apps.api.exceptions import InsufficientFuelStationsError
from apps.optimizer.domain import FuelPlan, PlannedStop, ProjectedStation, VehicleSpec


class MinCostRefuelSolver:
    """Solves the fixed-path gas station problem minimizing monetary fuel spend."""

    def solve(
        self,
        total_distance_miles: float,
        stations: list[ProjectedStation],
        spec: VehicleSpec | None = None,
    ) -> FuelPlan:
        spec = spec or VehicleSpec()
        max_range = spec.max_range_miles
        mpg = spec.mpg
        tank_capacity = spec.tank_capacity_gallons
        initial_fuel = min(spec.initial_fuel_gallons, tank_capacity)

        # 1. Edge Case: 0-distance route
        if total_distance_miles <= 0.0:
            return FuelPlan(
                total_distance_miles=0.0,
                total_money_spent_on_fuel_usd=Decimal("0.00"),
                en_route_pump_cost_usd=Decimal("0.00"),
                initial_tank_fuel_value_usd=Decimal("0.00"),
                vehicle_max_range_miles=max_range,
                vehicle_mpg=mpg,
                tank_capacity_gallons=tank_capacity,
                initial_fuel_gallons=initial_fuel,
                ending_fuel_gallons=Decimal(str(round(initial_fuel, 3))),
                total_fuel_consumed_gallons=Decimal("0.000"),
                total_gallons_purchased_en_route=Decimal("0.000"),
                stops=[],
                recommended_cheapest_station=stations[0] if stations else None,
            )

        # Identify cheapest overall station along the corridor
        cheapest_station: ProjectedStation | None = None
        if stations:
            cheapest_station = min(stations, key=lambda s: (s.retail_price, s.route_mile_marker))

        # 2. Check if route is achievable with 0 stops on initial tank fuel
        initial_reachable_miles = initial_fuel * mpg
        if total_distance_miles <= initial_reachable_miles + 1e-7:
            # Reaches destination without stops
            gallons_consumed = Decimal(str(round(total_distance_miles / mpg, 3)))
            ending_fuel_float = initial_fuel - (total_distance_miles / mpg)
            ending_fuel = Decimal(str(round(max(0.0, ending_fuel_float), 3)))

            # Valuation rate for fuel consumed from starting tank
            ref_rate = cheapest_station.retail_price if cheapest_station else Decimal("3.00000000")
            trip_fuel_cost = (gallons_consumed * ref_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            return FuelPlan(
                total_distance_miles=round(total_distance_miles, 2),
                total_money_spent_on_fuel_usd=trip_fuel_cost,
                en_route_pump_cost_usd=Decimal("0.00"),
                initial_tank_fuel_value_usd=trip_fuel_cost,
                vehicle_max_range_miles=max_range,
                vehicle_mpg=mpg,
                tank_capacity_gallons=tank_capacity,
                initial_fuel_gallons=initial_fuel,
                ending_fuel_gallons=ending_fuel,
                total_fuel_consumed_gallons=gallons_consumed,
                total_gallons_purchased_en_route=Decimal("0.000"),
                stops=[],
                recommended_cheapest_station=cheapest_station,
            )

        # 3. Solver: Nodes 0 (Start), 1..K (Stations), K+1 (Finish)
        K = len(stations)
        miles = [0.0] + [s.route_mile_marker for s in stations] + [total_distance_miles]
        eff_prices = [float("inf")] + [s.effective_price for s in stations] + [0.0]

        # Verify reachability from origin with initial fuel
        if K == 0 and total_distance_miles > initial_reachable_miles:
            raise InsufficientFuelStationsError(
                f"No fuel stations available on route of {total_distance_miles:.1f} miles.",
                details={"initial_range": initial_reachable_miles, "total_miles": total_distance_miles},
            )
        if K > 0 and miles[1] > initial_reachable_miles:
            raise InsufficientFuelStationsError(
                f"First fuel station at mile {miles[1]:.1f} exceeds initial fuel range of {initial_reachable_miles:.1f} miles.",
                details={"first_station_mile": miles[1], "initial_range": initial_reachable_miles},
            )

        for idx in range(1, K + 1):
            if miles[idx + 1] - miles[idx] > max_range:
                raise InsufficientFuelStationsError(
                    f"Segment between mile {miles[idx]:.1f} and {miles[idx + 1]:.1f} exceeds max range of {max_range:.1f} miles.",
                    details={"gap_start": miles[idx], "gap_end": miles[idx + 1]},
                )

        # Monotonic Stack (right-to-left): find smallest j > i with eff_prices[j] < eff_prices[i]
        first_cheaper = [K + 1] * (K + 2)
        stack: list[int] = []
        for i in range(K + 1, -1, -1):
            while stack and eff_prices[stack[-1]] >= eff_prices[i]:
                stack.pop()
            if stack:
                first_cheaper[i] = stack[-1]
            stack.append(i)

        # Forward Tank Simulation
        fuel = initial_fuel
        raw_stops: list[dict[str, Any]] = []

        for i in range(0, K + 1):
            if i > 0:
                dist_traveled = miles[i] - miles[i - 1]
                burned = dist_traveled / mpg
                fuel = max(0.0, fuel - burned)

            if i == 0:
                continue

            j = first_cheaper[i]
            dist_to_cheaper = miles[j] - miles[i]

            if dist_to_cheaper <= max_range:
                target_fuel = dist_to_cheaper / mpg
            else:
                target_fuel = tank_capacity

            # Ensure we have enough fuel to reach at least the very next node i+1
            min_needed_for_next = (miles[i + 1] - miles[i]) / mpg
            target_fuel = max(target_fuel, min_needed_for_next)
            target_fuel = min(target_fuel, tank_capacity)

            if target_fuel > fuel + 1e-6:
                gallons_bought = target_fuel - fuel
                raw_stops.append(
                    {
                        "node_idx": i,
                        "station": stations[i - 1],
                        "mile": miles[i],
                        "gallons": gallons_bought,
                        "tank_before": fuel,
                        "tank_after": target_fuel,
                    }
                )
                fuel = target_fuel

        # Final leg burn to destination
        ending_fuel_float = max(0.0, fuel - (miles[K + 1] - miles[K]) / mpg)

        # 4. Consolidate Micro-Stops (< min_stop_gallons)
        consolidated = self._consolidate_micro_stops(raw_stops, spec, miles, K)

        # 5. Build Decimal Financials
        total_gal_consumed = Decimal(str(round(total_distance_miles / mpg, 3)))
        ending_fuel_dec = Decimal(str(round(ending_fuel_float, 3)))

        planned_stops: list[PlannedStop] = []
        en_route_spend = Decimal("0.00")
        total_gallons_purchased = Decimal("0.000")
        prev_mile = 0.0

        for stop_num, st_info in enumerate(consolidated, start=1):
            st = st_info["station"]
            mile = st_info["mile"]
            gals_dec = Decimal(str(round(st_info["gallons"], 3)))
            cost_dec = (gals_dec * st.retail_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            en_route_spend += cost_dec
            total_gallons_purchased += gals_dec

            planned_stops.append(
                PlannedStop(
                    stop_number=stop_num,
                    station=st.station,
                    route_mile_marker=mile,
                    distance_from_previous_miles=round(mile - prev_mile, 2),
                    off_route_distance_miles=st.off_route_distance_miles,
                    retail_price_per_gallon_usd=st.retail_price,
                    gallons_purchased=gals_dec,
                    cost_usd=cost_dec,
                    tank_gallons_before=Decimal(str(round(st_info["tank_before"], 3))),
                    tank_gallons_after=Decimal(str(round(st_info["tank_after"], 3))),
                )
            )
            prev_mile = mile

        # Starting tank fuel consumed valuation
        initial_fuel_used_gal = Decimal(str(round(max(0.0, initial_fuel - ending_fuel_float), 3)))
        ref_rate = planned_stops[0].retail_price_per_gallon_usd if planned_stops else (cheapest_station.retail_price if cheapest_station else Decimal("3.00000000"))
        initial_tank_val = (initial_fuel_used_gal * ref_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        total_trip_spend = en_route_spend + initial_tank_val

        return FuelPlan(
            total_distance_miles=round(total_distance_miles, 2),
            total_money_spent_on_fuel_usd=total_trip_spend,
            en_route_pump_cost_usd=en_route_spend,
            initial_tank_fuel_value_usd=initial_tank_val,
            vehicle_max_range_miles=max_range,
            vehicle_mpg=mpg,
            tank_capacity_gallons=tank_capacity,
            initial_fuel_gallons=initial_fuel,
            ending_fuel_gallons=ending_fuel_dec,
            total_fuel_consumed_gallons=total_gal_consumed,
            total_gallons_purchased_en_route=total_gallons_purchased,
            stops=planned_stops,
            recommended_cheapest_station=cheapest_station,
        )

    def _consolidate_micro_stops(
        self,
        raw_stops: list[dict[str, Any]],
        spec: VehicleSpec,
        miles: list[float],
        K: int,
    ) -> list[dict[str, Any]]:
        """Merge impractical < 2-gallon stops when tank capacity permits."""
        if not raw_stops or spec.min_stop_gallons <= 0.0:
            return raw_stops

        min_g = spec.min_stop_gallons
        tank_cap = spec.tank_capacity_gallons
        merged: list[dict[str, Any]] = []

        for st in raw_stops:
            if not merged:
                merged.append(st)
                continue

            prev = merged[-1]
            # If current stop is micro and previous stop had room to carry it forward
            if st["gallons"] < min_g:
                available_room = tank_cap - prev["tank_after"]
                if available_room >= st["gallons"]:
                    prev["gallons"] += st["gallons"]
                    prev["tank_after"] += st["gallons"]
                    continue

            merged.append(st)

        return merged

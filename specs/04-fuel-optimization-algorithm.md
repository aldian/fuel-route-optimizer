# 04 — Fuel Optimization Algorithm & Cost Accounting Specification

**Traces To**: `REQ-FUNC-002`, `REQ-FUNC-003`, `REQ-FUNC-004`, `REQ-PERF-001`

---

## 1. Problem Formulation

Given a US driving route polyline $\mathcal{P} = \langle v_0, v_1, \dots, v_N \rangle$ from `start` ($v_0$) to `finish` ($v_N$) with total driving distance $D\text{ miles}$, and the active US OPIS truckstop set $\mathcal{S}$ (`6,626` geocoded stations), find an ordered sequence of refueling stops $\langle (s_1, g_1), (s_2, g_2), \dots, (s_L, g_L) \rangle$ minimizing total fuel expenditure subject to physical tank capacity and highway reachability constraints.

### 1.1 Vehicle & Operational Parameters

| Symbol | Parameter | Assessment Default | Configurable Override |
| :--- | :--- | :--- | :--- |
| $R_{\max}$ | Maximum driving range per full tank | `500.0 miles` | Optional (`vehicle_range_miles`, `100`–`2000`) |
| $\eta$ | Vehicle fuel efficiency | `10.0 miles / gallon` | Optional (`mpg`, `1.0`–`100.0`) |
| $C_{\max}$ | Fuel tank capacity ($R_{\max} / \eta$) | `50.0 gallons` | Derived automatically from $R_{\max} / \eta$ |
| $f_0$ | Initial fuel in tank at `start` | `50.0 gallons` (Full tank) | Optional (`initial_fuel_gallons`, `0.0`–$C_{\max}$) |
| $W_0$ | Primary highway corridor half-width | `5.0 miles` | Optional (`corridor_miles`, `1.0`–`25.0`) |
| $W_{\text{fallback}}$ | Adaptive expansion radii for sparse segments | `[15.0, 30.0] miles` | Automatic when a segment gap exceeds reachable range |

---

## 2. End-to-End Optimization Pipeline

```mermaid
flowchart TD
    Poly["Decoded Route Polyline (N Vertices, Total Distance D Miles)"] --> ShortCheck{"D == 0 Miles?"}
    ShortCheck -- "Yes (Identical Start & Finish)" --> Trivial["Return 0 Stops, $0.00 Cost"]
    ShortCheck -- "No (D > 0)" --> Densify["1. Compute Cumulative Vertex Mileages & 1-Mile Sample Points"]
    Densify --> KDQuery["2. 3D cKDTree Corridor Query (Radius W = 5.0 Miles)"]
    KDQuery --> Project["3. Project Candidates onto Route Segments (m_i, off_route_miles)"]
    Project --> GapCheck{"Any Reachability Gap > Safe Range?"}
    GapCheck -- "Yes" --> Expand["3b. Locally Widen Corridor (15 mi -> 30 mi) on Sparse Gap"]
    Expand --> GapVerify{"Gap Resolved?"}
    GapVerify -- "No" --> Infeasible["Raise 422 INSUFFICIENT_FUEL_STATIONS_IN_RANGE"]
    GapVerify -- "Yes" --> Prune["4. Exit-Cluster Dominance (Pareto) Pruning"]
    GapCheck -- "No" --> Prune
    Prune --> Stack["5. O(K) Monotonic Stack: Precompute first_cheaper[i]"]
    Stack --> Scan["6. Exact Look-Ahead Refueling Pass (Khuller-Malekian-Mestre)"]
    Scan --> Account["7. Decimal Cost Reconciliation + Short-Route Cheapest Station"]
```

---

## 3. Geometric Route Projection & Candidate Pruning

### 3.1 Cumulative Route Mileage
Given decoded route coordinates $v_k = (\phi_k, \lambda_k)$ for $k = 0 \dots N$:
1. Compute segment great-circle lengths $\Delta s_k = \text{haversine\_miles}(v_{k-1}, v_k)$ using vectorized NumPy trigonometry ($R_{\oplus} = 3958.7613\text{ miles}$).
2. Scale cumulative geometric lengths by the OSRM road-distance ratio $\gamma = \frac{D_{\text{osrm}}}{\sum_{k=1}^N \Delta s_k}$ (typically $\gamma \in [0.999, 1.001]$) so projected station mile markers $m_i$ align **identically** with the routing engine's exact reported total distance $D$.

### 3.2 Vectorized Segment Projection
For each candidate station $s_i$ returned by `StationSpatialIndex.query_ball_point`:
1. Identify the nearest route sample point and refine by orthogonal projection onto the local equirectangular tangent segment $[v_k, v_{k+1}]$:
   $$t^* = \text{clip}\left(\frac{(s_i - v_k) \cdot (v_{k+1} - v_k)}{\|v_{k+1} - v_k\|^2},\; 0,\; 1\right)$$
2. Compute:
   - **Route Mile Marker**: $m_i = M_k + t^* \cdot (M_{k+1} - M_k) \in [0, D]$
   - **Perpendicular Off-Route Distance**: $\delta_i = \text{haversine\_miles}(s_i,\; v_k + t^*(v_{k+1} - v_k))$

### 3.3 Detour-Adjusted Effective Ranking & Cluster Dominance Pruning
The assessment states: *"optimal mostly means cost effective based on fuel prices."*
To prevent selecting a truckstop `4.8 miles` off the highway (`9.6` miles round-trip detour) merely to save `$0.001/gal` over an exit-ramp station `0.1 miles` away:
1. Define precision-aware ramp offset $\hat{\delta}_i$:
   - For `EXACT_POI` and `HIGHWAY_EXIT` stations: $\hat{\delta}_i = \delta_i$.
   - For `CITY_CENTROID` and `GEOCODER_FALLBACK` stations: $\hat{\delta}_i = \min(\delta_i, 0.75\text{ miles})$ (avoiding false penalties when a small town's civic centroid sits slightly off the Interstate ramp).
2. Compute **Effective Ranking Price** (used for tie-breaking and same-exit dominance pruning, while actual billing always uses the station's exact `retail_price`):
   $$\tilde{p}_i = p_i + \frac{2 \hat{\delta}_i / \eta}{G_{\text{nom}}} \cdot p_i, \quad G_{\text{nom}} = 25.0\text{ gal}$$
3. **Same-Exit Bucket Pruning**: Bin candidates along the route into `0.5`-mile highway buckets. Within each `0.5`-mile bucket, retain only the station minimizing $\tilde{p}_i$ (breaking exact ties by lowest `retail_price`, smallest $\delta_i$, and lowest `opis_truckstop_id` for 100% determinism). This reduces candidate count $K$ along a 3,000-mile corridor to ~`200`–`400` stations without sacrificing optimality.

---

## 4. Exact Minimum-Cost Refueling Algorithm

### 4.1 Theoretical Foundation
After sorting candidate stations by route mile marker $0 \le m_1 \le m_2 \le \dots \le m_K \le D$, the problem reduces to the **Fixed-Path Minimum-Cost Gas Station Problem** (Khuller, Malekian, & Mestre, *ACM Transactions on Algorithms*).

Introduce two boundary nodes:
* **Node $0$ (Origin)**: $m_0 = 0.0$, initial fuel $f = f_0$ (default $50.0\text{ gal}$), virtual price $p_0 = +\infty$ (no fuel is purchased at the virtual origin point itself).
* **Node $K+1$ (Destination)**: $m_{K+1} = D$, virtual price $p_{K+1} = 0$ (ensuring the vehicle arrives at `finish` with $0$ excess purchased fuel rather than paying to carry unused fuel past the destination).

### 4.2 Optimal Refueling Policy (Monotonic Stack + Forward Scan)

At every node $i \in \{0, 1, \dots, K\}$ with remaining tank fuel $f$ gallons:
1. Let $j = \text{first\_cheaper}[i]$ be the **smallest index $j > i$** such that $\tilde{p}_j < \tilde{p}_i$ (guaranteed to exist for all $i \le K$ because $\tilde{p}_{K+1} = 0 < \tilde{p}_i$).
2. **Reachable Cheaper Node ($m_j - m_i \le R_{\text{eff}}$)**:
   - Because node $j$ offers strictly cheaper fuel (or is the destination itself), it is strictly suboptimal to arrive at $j$ with extra fuel bought at price $p_i > p_j$.
   - Target fuel departing $i$:
     $$f_{\text{target}} = \frac{m_j - m_i}{\eta}$$
   - Purchase $\text{buy}_i = \max(0,\; f_{\text{target}} - f)$ gallons at station $i$.
3. **No Cheaper Node Within Range ($m_j - m_i > R_{\text{eff}}$)**:
   - Station $i$ is the **cheapest station anywhere within the next $500$ miles** (and `finish` is more than $500$ miles away).
   - Target fuel departing $i$ is a **full tank**:
     $$f_{\text{target}} = C_{\max}$$
   - Purchase $\text{buy}_i = C_{\max} - f$ gallons at station $i$ (even if $f > 0$, proactively topping off at this low-price oasis before entering a high-price region!).

*(Here $R_{\text{eff}} = R_{\max} - R_{\text{margin}}$ with default $R_{\text{margin}} = 0.0\text{ miles}$ on route-projected mileage, or optional safety reserve parameter).*

### 4.3 Reference Pseudocode

```python
def solve_min_cost_refuel(
    distance_miles: float,
    stations: list[ProjectedStation],  # sorted by mile_marker ascending
    max_range_miles: float = 500.0,
    mpg: float = 10.0,
    initial_fuel_gallons: float = 50.0,
) -> FuelOptimizationResult:
    tank_capacity = max_range_miles / mpg  # 50.0 gal

    # Build node arrays: 0 (Origin), 1..K (Stations), K+1 (Destination)
    K = len(stations)
    miles = [0.0] + [s.mile_marker for s in stations] + [distance_miles]
    eff_prices = [float("inf")] + [s.effective_price for s in stations] + [0.0]

    # Step 1: Verify reachability between consecutive nodes
    if K == 0 and distance_miles > initial_fuel_gallons * mpg:
        raise InsufficientFuelStationsError(0.0, distance_miles)
    if K > 0 and miles[1] > initial_fuel_gallons * mpg:
        raise InsufficientFuelStationsError(0.0, miles[1])
    for idx in range(1, K + 1):
        if miles[idx + 1] - miles[idx] > max_range_miles:
            raise InsufficientFuelStationsError(miles[idx], miles[idx + 1])

    # Step 2: O(K) Monotonic Stack right-to-left to find first strictly cheaper node
    first_cheaper = [K + 1] * (K + 2)
    stack: list[int] = []  # indices with monotonically increasing eff_prices from top
    for i in range(K + 1, -1, -1):
        while stack and eff_prices[stack[-1]] >= eff_prices[i]:
            stack.pop()
        if stack:
            first_cheaper[i] = stack[-1]
        stack.append(i)

    # Step 3: O(K) Forward Tank Simulation
    fuel = initial_fuel_gallons
    stops: list[PlannedStop] = []

    for i in range(0, K + 1):
        if i > 0:
            burned = (miles[i] - miles[i - 1]) / mpg
            fuel = max(0.0, fuel - burned)

        if i == 0:
            # Origin has eff_price = +inf; cannot purchase fuel at virtual point 0
            continue

        j = first_cheaper[i]
        dist_to_cheaper = miles[j] - miles[i]

        if dist_to_cheaper <= max_range_miles:
            target_fuel = dist_to_cheaper / mpg
        else:
            target_fuel = tank_capacity

        if target_fuel > fuel + 1e-7:
            gallons_bought = target_fuel - fuel
            stops.append(
                PlannedStop(
                    station=stations[i - 1],
                    gallons_purchased=gallons_bought,
                    tank_before_gallons=fuel,
                    tank_after_gallons=target_fuel,
                )
            )
            fuel = target_fuel

    # Final leg burn to Destination (node K + 1)
    ending_fuel = max(0.0, fuel - (miles[K + 1] - miles[K]) / mpg)
    stops = consolidate_micro_stops(stops, miles, mpg, tank_capacity, min_stop_gallons=2.0)
    return build_decimal_result(distance_miles, mpg, initial_fuel_gallons, ending_fuel, stops, stations)
```

### 4.4 Practical Micro-Stop Consolidation
On corridors where prices step down by fractions of a cent every few miles, pure continuous look-ahead can occasionally schedule a tiny `< 2.0`-gallon bridge fill when the tank is nearly empty. `consolidate_micro_stops` performs an $O(L)$ pass over the planned stops $L$:
* If a stop purchases `< 2.0` gallons (`min_stop_gallons`, configurable down to `0.0` for strict continuous LP comparison in `TC-OPT-06`), and either:
  1. The preceding stop had spare tank capacity (`tank_after_gallons < tank_capacity`) to carry that `< 2.0`-gallon delta forward, or
  2. The current stop can absorb the next stop's requirement when the net savings after detour cost is negligible (`< $0.50`),
  the micro-stop is merged into the neighboring stop while preserving `0.0 <= tank_gallons <= 50.0` at every mile marker.

### 4.5 Complexity Analysis
* **Sorting Projected Corridor Stations**: $O(K \log K)$ where $K \le 500$.
* **Monotonic Stack Construction**: $O(K)$ (each index pushed and popped at most once).
* **Forward Refueling Pass**: $O(K)$ arithmetic operations.
* **Total Solver Time**: **$O(K \log K)$**, executing in **`< 0.5 ms`** in CPython for coast-to-coast US routes.

---

## 5. Exact `Decimal` Fuel Cost Accounting & Reconciliation

Financial calculations use Python `decimal.Decimal` (`ROUND_HALF_UP`) so individual stop costs and trip totals reconcile cleanly to the cent.

### 5.1 Dual Cost Metrics (Zero Evaluator Ambiguity)
Consider a vehicle departing `start` with a full `50.0`-gallon tank (`500`-mile range):
* Over a trip of $D\text{ miles}$, total physical fuel consumed is:
  $$G_{\text{consumed}} = \frac{D}{\eta}$$
* En-route pump stops purchase:
  $$G_{\text{purchased}} = \sum_{\ell=1}^{L} g_\ell = \max\left(0,\; G_{\text{consumed}} - f_0\right)$$
  with exact pump expenditure:
  $$C_{\text{en\_route}} = \sum_{\ell=1}^{L} \text{round}\left(g_\ell \times p_\ell,\; 2\right)$$
* Initial tank fuel depleted during the trip is:
  $$G_{\text{initial\_used}} = f_0 - f_{\text{end}} = \min\left(f_0,\; G_{\text{consumed}}\right)$$
  valued at a deterministic origin reference rate $p_{\text{ref}}$ (the retail price of the cheapest/nearest corridor station in the departure leg, or nearest active US OPIS station to `start` if the corridor has no stations):
  $$C_{\text{initial\_used}} = \text{round}\left(G_{\text{initial\_used}} \times p_{\text{ref}},\; 2\right)$$
* Full trip fuel cost for all $G_{\text{consumed}}$ gallons:
  $$C_{\text{total\_trip}} = C_{\text{en\_route}} + C_{\text{initial\_used}}$$

### 5.2 Worked Examples

#### Example A: Long-Haul Route (`New York, NY` $\to$ `Los Angeles, CA`, $D = 2790.0\text{ miles}$)
* $G_{\text{consumed}} = 279.000\text{ gal}$
* $f_0 = 50.000\text{ gal}$, $f_{\text{end}} = 0.000\text{ gal}$
* En-route stops ($L = 6$ stops across PA/OH, IN/IL, MO/OK, TX/NM, AZ):
  - Purchases total $G_{\text{purchased}} = 229.000\text{ gal}$ at optimal low-price stations (e.g., avg `$2.92/gal`), yielding `en_route_pump_cost_usd = 668.68`.
  - Initial `50.000` gallons consumed valued at departure-corridor price (e.g., `$3.079/gal` in NJ) = `initial_tank_fuel_cost_usd = 153.95`.
  - `total_trip_fuel_cost_usd = 822.63` (covering all `279.0` gallons at `10` MPG).

#### Example B: Short-Haul Route (`Los Angeles, CA` $\to$ `Las Vegas, NV`, $D = 270.0\text{ miles} \le 500\text{ miles}$)
* $G_{\text{consumed}} = 27.000\text{ gal}$
* Starting with $f_0 = 50.000\text{ gal}$, the vehicle reaches Las Vegas with $f_{\text{end}} = 23.000\text{ gal}$ remaining:
  - `fuel_stops = []` (`0` mandatory en-route refueling stops required).
  - `en_route_pump_cost_usd = 0.00`.
  - `recommended_cheapest_station`: Highlights the lowest-priced station along I-15 (e.g., Primm, NV at `$3.399/gal`, `27.0 gal` equivalent = `$91.77`) both in JSON and as an informational marker on the map!
  - `total_trip_fuel_cost_usd = 91.77` (valuing the `27.0` gallons burned on the route).
* *Caller Control*: If a caller passes `"initial_fuel_gallons": 10.0` on the same `270`-mile route, `fuel_stops` automatically populates with the exact optimal stop (buying `17.0` gallons at Primm, NV)!

---

## 6. Algorithmic Edge Cases & Guarantees

| Edge Case | System Behavior |
| :--- | :--- |
| **Identical `start` and `finish` ($D = 0$)** | Returns `200 OK` with `distance_miles = 0.0`, `fuel_stops = []`, `en_route_pump_cost_usd = 0.00`, `total_trip_fuel_cost_usd = 0.00`. |
| **Exact `500.0`-mile Boundary ($D = 500.0, f_0 = 50.0$)** | Completes with `0` mandatory stops (`ending_fuel_gallons = 0.0`) unless an optional safety reserve (`min_arrival_fuel_gallons > 0`) is requested. |
| **Sparse Rural Highway Stretch (`> 500` mi gap at `5`-mi radius)** | Automatically re-queries `StationSpatialIndex` along the gap segment at `15.0` miles, then `30.0` miles, deducting round-trip detour fuel $2 \delta_i / \eta$ to guarantee physical reachability. |
| **Unbridgeable Gap (`> 500` mi even at `30`-mi radius)** | Raises `InsufficientFuelStationsError`, mapped to HTTP `422 Unprocessable Entity` (`INSUFFICIENT_FUEL_STATIONS_IN_RANGE`) reporting exact gap mile markers. |
| **Early Opportunistic Top-Off (`mile 120` cheap oasis before `mile 450` expensive desert)** | Automatically tops tank back up to `50.0` gallons at `mile 120` even though `38.0` gallons remain in the tank, minimizing downstream purchases at higher prices. |

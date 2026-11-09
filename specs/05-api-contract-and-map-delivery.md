# 05 — REST API Contract & Map Delivery Specification

**Traces To**: `REQ-FUNC-001`, `REQ-FUNC-002`, `REQ-FUNC-003`, `REQ-FUNC-004`, `REQ-PERF-001`, `REQ-PERF-002`, `REQ-DELIV-001`

---

## 1. API Endpoint Summary

All API endpoints are versioned under `/api/v1/` and documented automatically via OpenAPI 3.1 (`drf-spectacular`). Authentication is open (`AllowAny`) for frictionless evaluation in Postman.

| Method | Path | Content-Type | Purpose |
| :--- | :--- | :--- | :--- |
| `POST` | `/api/v1/route/` | `application/json` | Compute optimal US driving route, fuel stops, total fuel spend, GeoJSON map, and interactive `map_url`. |
| `GET` | `/api/v1/route/` | `application/json` | Query-parameter equivalent of `POST /api/v1/route/` (`?start=New+York,+NY&finish=Los+Angeles,+CA`). |
| `GET` | `/api/v1/route/map/` | `text/html` | Render self-contained interactive Leaflet + OpenStreetMap route & fuel-stop map in any browser or iframe. |
| `GET` | `/api/v1/stations/` | `application/json` | Inspect loaded US OPIS fuel stations with filtering by `state`, `city`, or `(lat, lng, radius_miles)`. |
| `GET` | `/api/v1/health/` | `application/json` | Health & readiness probe reporting Django version, loaded station count, gazetteer status, and `cKDTree` state. |
| `GET` | `/api/schema/` | `application/vnd.oai.openapi` | Machine-readable OpenAPI 3.1 specification. |
| `GET` | `/api/docs/` | `text/html` | Interactive Swagger UI API explorer. |

---

## 2. Primary Endpoint: `/api/v1/route/`

### 2.1 Request Schema (`POST` Body or `GET` Query Parameters)

```json
{
  "start": "New York, NY",
  "finish": "Los Angeles, CA",
  "vehicle_range_miles": 500.0,
  "mpg": 10.0,
  "initial_fuel_gallons": 50.0,
  "corridor_miles": 5.0,
  "include_geojson": true
}
```

| Field | Type | Required | Default | Validation Constraints |
| :--- | :--- | :--- | :--- | :--- |
| `start` | `string` or `{"lat": float, "lng": float}` | **Yes** | — | Non-empty US location string (`1`–`256` chars) or valid US coordinates (`lat` $\in [-90, 90]$, `lng` $\in [-180, 180]$ inside USA). |
| `finish` | `string` or `{"lat": float, "lng": float}` | **Yes** | — | Non-empty US location string (`1`–`256` chars) or valid US coordinates inside USA. |
| `vehicle_range_miles` | `float` | No | `500.0` | `100.0 <= value <= 2000.0`. |
| `mpg` | `float` | No | `10.0` | `1.0 <= value <= 100.0`. |
| `initial_fuel_gallons` | `float` | No | `vehicle_range_miles / mpg` (`50.0`) | `0.0 <= value <= (vehicle_range_miles / mpg)`. |
| `corridor_miles` | `float` | No | `5.0` | `1.0 <= value <= 25.0`. |
| `include_geojson` | `boolean` | No | `true` | Controls whether full `map.geojson` `FeatureCollection` is embedded inline. |

---

### 2.2 Success Response Schema (`200 OK`)

```json
{
  "route": {
    "start": {
      "input": "New York, NY",
      "resolved_name": "New York, NY",
      "latitude": 40.7128,
      "longitude": -74.006,
      "state": "NY",
      "resolution_source": "local_gazetteer"
    },
    "finish": {
      "input": "Los Angeles, CA",
      "resolved_name": "Los Angeles, CA",
      "latitude": 34.0522,
      "longitude": -118.2437,
      "state": "CA",
      "resolution_source": "local_gazetteer"
    },
    "total_distance_miles": 2789.42,
    "estimated_duration_seconds": 146520,
    "estimated_duration_formatted": "40h 42m"
  },
  "fuel_summary": {
    "total_money_spent_on_fuel_usd": "821.45",
    "en_route_pump_cost_usd": "667.50",
    "initial_tank_fuel_value_usd": "153.95",
    "currency": "USD",
    "vehicle_max_range_miles": 500.0,
    "vehicle_mpg": 10.0,
    "tank_capacity_gallons": 50.0,
    "initial_fuel_gallons": 50.0,
    "ending_fuel_gallons": 0.0,
    "total_fuel_consumed_gallons": 278.942,
    "total_gallons_purchased_en_route": 228.942,
    "number_of_fuel_stops": 6,
    "average_price_per_gallon_usd": "2.916"
  },
  "fuel_stops": [
    {
      "stop_number": 1,
      "opis_truckstop_id": 35,
      "truckstop_name": "ACI TRUCK STOP",
      "address": "US-46",
      "city": "Columbia",
      "state": "NJ",
      "latitude": 40.9254,
      "longitude": -75.0938,
      "route_mile_marker": 72.4,
      "distance_from_previous_miles": 72.4,
      "off_route_distance_miles": 0.32,
      "retail_price_per_gallon_usd": "3.079",
      "gallons_purchased": 7.24,
      "cost_usd": "22.29",
      "tank_gallons_before": 42.76,
      "tank_gallons_after": 50.0
    }
  ],
  "recommended_cheapest_station": {
    "opis_truckstop_id": 7,
    "truckstop_name": "WOODSHED OF BIG CABIN",
    "address": "I-44, EXIT 283 & US-69",
    "city": "Big Cabin",
    "state": "OK",
    "latitude": 36.5418,
    "longitude": -95.2177,
    "route_mile_marker": 1284.6,
    "off_route_distance_miles": 0.18,
    "retail_price_per_gallon_usd": "3.007"
  },
  "map": {
    "map_url": "http://localhost:8000/api/v1/route/map/?start=New+York%2C+NY&finish=Los+Angeles%2C+CA",
    "bbox": [-118.2437, 34.0522, -74.006, 41.6125],
    "polyline6": "}_ilF~kbpM...",
    "geojson": {
      "type": "FeatureCollection",
      "features": [
        {
          "type": "Feature",
          "geometry": {
            "type": "LineString",
            "coordinates": [[-74.006, 40.7128], [-118.2437, 34.0522]]
          },
          "properties": {
            "feature_role": "route_line",
            "total_distance_miles": 2789.42,
            "total_money_spent_on_fuel_usd": "821.45",
            "stroke": "#0F4C81",
            "stroke-width": 4
          }
        },
        {
          "type": "Feature",
          "geometry": {
            "type": "Point",
            "coordinates": [-74.006, 40.7128]
          },
          "properties": {
            "feature_role": "start",
            "label": "Start: New York, NY"
          }
        },
        {
          "type": "Feature",
          "geometry": {
            "type": "Point",
            "coordinates": [-75.0938, 40.9254]
          },
          "properties": {
            "feature_role": "fuel_stop",
            "stop_number": 1,
            "opis_truckstop_id": 35,
            "truckstop_name": "ACI TRUCK STOP",
            "city": "Columbia",
            "state": "NJ",
            "retail_price_per_gallon_usd": "3.079",
            "gallons_purchased": 7.24,
            "cost_usd": "22.29",
            "route_mile_marker": 72.4
          }
        },
        {
          "type": "Feature",
          "geometry": {
            "type": "Point",
            "coordinates": [-118.2437, 34.0522]
          },
          "properties": {
            "feature_role": "finish",
            "label": "Finish: Los Angeles, CA"
          }
        }
      ]
    }
  },
  "meta": {
    "cache_hit": false,
    "external_api_calls": {
      "total_count": 1,
      "budget_limit": 3,
      "calls": [
        {
          "service": "osrm",
          "operation": "driving_route",
          "duration_ms": 142.3
        }
      ]
    },
    "compute_time_ms": 6.4,
    "total_response_time_ms": 149.1
  }
}
```

### 2.3 GeoJSON Simplification Policy
* Full sub-meter route fidelity (`~35,000` vertices on a coast-to-coast route) is preserved inside `map.polyline6` and used internally for exact corridor station projection.
* Inside `map.geojson` (`LineString`), vertices are downsampled using Visvalingam/Ramer–Douglas–Peucker (`tolerance ~ 0.002°`, max `~1,500` coordinates) so the JSON payload stays compact (`~45 KB`) and renders instantaneously in Postman without UI lag.

---

## 3. Interactive Browser Map Endpoint (`/api/v1/route/map/`)

Clicking `map.map_url` directly inside Postman's response pane opens a responsive, zero-API-key interactive route visualizer rendered by Django.

```mermaid
flowchart LR
    Postman["Postman Response (Click map_url)"] --> MapEndpoint["GET /api/v1/route/map/"]
    MapEndpoint --> ReuseCache["Hit Cached RoutePlan (0 Extra External Calls!)"]
    ReuseCache --> RenderHTML["Render Accessible HTML + Leaflet + OSM Tiles"]
    RenderHTML --> Features["Interactive Polyline + Numbered SVG Stop Pins + Cost Summary Panel"]
```

### 3.1 Visual, Branding & Accessibility (WCAG) Requirements
1. **High-Contrast Typography**:
   - All body text, summary metrics, station cards, table cells, and map popups on light surfaces (`#FFFFFF` / `#F8FAFC`) **must** use dark/black text (`#000000` primary, `#111827` secondary) to guarantee WCAG AAA/AA contrast ratios (`>= 7:1`). Never use white or light-gray text on light backgrounds.
2. **Vector/SVG Iconography**:
   - Header brand mark uses a clean, modern vector SVG emblem (minimalist geometric circle route/compass icon) and numbered vector SVG map pins (`Start`, `Stop 1..N`, `Finish`) — zero raster icons.
3. **Clear, Customer-Friendly Copy**:
   - All visible UI headings, cards, tooltips, and controls use plain logistics terminology (*"Route Overview"*, *"Total Fuel Cost"*, *"Pump Spend Along Route"*, *"Optimal Fuel Stops"*, *"Price per Gallon"*, *"Mile Marker"*) — never exposing internal engineering terms or upstream infrastructure provider names in customer-facing UI text.
4. **Zero Extra Upstream Calls**:
   - Opening `map_url` after calling `/api/v1/route/` hits the warm `TwoTierCache` immediately (`0` additional routing or geocoding requests, `< 10 ms` server render).
5. **OpenStreetMap Tile Usage Policy & Referrer Compliance**:
   - Both Django (`SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"`) and the HTML document (`<meta name="referrer" content="strict-origin-when-cross-origin">`) explicitly send the origin `Referer` header when fetching map tiles over HTTPS so OpenStreetMap (`tile.openstreetmap.org`) and CDN tile servers never block cross-origin tile requests with `403 Access blocked`.

---

## 4. Standardized Error Contract

All `4xx` and `5xx` responses share a consistent JSON error envelope via a custom DRF exception handler:

```json
{
  "error": {
    "code": "LOCATION_OUTSIDE_USA",
    "message": "Both start and finish locations must be within the United States.",
    "details": {
      "field": "finish",
      "input": "Toronto, ON"
    }
  }
}
```

### Error Code Catalog

| HTTP Status | `error.code` | Trigger Condition |
| :--- | :--- | :--- |
| `400 Bad Request` | `INVALID_REQUEST_PARAMETERS` | Missing `start`/`finish` or numeric override out of valid bounds. |
| `400 Bad Request` | `LOCATION_OUTSIDE_USA` | Resolved `start` or `finish` lies outside the United States (e.g., Canada, Mexico, overseas). |
| `404 Not Found` | `LOCATION_NOT_FOUND` | Unrecognized location string could not be resolved by local gazetteer or US geocoders. |
| `422 Unprocessable Entity` | `NO_DRIVABLE_ROUTE` | No road connection exists between endpoints (e.g., `Honolulu, HI` to `Los Angeles, CA`). |
| `422 Unprocessable Entity` | `DOMESTIC_US_ROUTE_UNAVAILABLE` | Route cannot be completed within domestic US highway network. |
| `422 Unprocessable Entity` | `INSUFFICIENT_FUEL_STATIONS_IN_RANGE` | A route segment exceeds maximum vehicle range (`500` miles) without any reachable OPIS fuel station even after adaptive corridor expansion. |
| `502 Bad Gateway` | `UPSTREAM_ROUTING_UNAVAILABLE` | Both primary (OSRM) and secondary (Valhalla) routing services failed or returned malformed data. |
| `504 Gateway Timeout` | `UPSTREAM_SERVICE_TIMEOUT` | External routing/geocoding request exceeded configured socket timeout (`4.0s`). |

---

## 5. Supporting Endpoints

### 5.1 `GET /api/v1/stations/`
Allows reviewers to inspect the cleaned, deduplicated US OPIS dataset:
* Query Parameters:
  - `state` (e.g., `?state=TX`)
  - `city` (e.g., `?city=Amarillo`)
  - `lat`, `lng`, `radius_miles` (e.g., `?lat=35.2220&lng=-101.8313&radius_miles=10` — powered by `StationSpatialIndex`)
  - `ordering` (`retail_price`, `-retail_price`, `opis_truckstop_id`)
  - `limit` (default `25`, max `100`), `offset`

### 5.2 `GET /api/v1/health/`
Returns deterministic readiness metadata:
```json
{
  "status": "healthy",
  "django_version": "6.1.3",
  "active_us_stations": 6626,
  "us_gazetteer_entries": 73420,
  "spatial_index_ready": true
}
```

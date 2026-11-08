# Spotter Fuel Route Optimizer

[![CI Quality Gate](https://github.com/aldian/fuel-route-optimizer/actions/workflows/ci.yml/badge.svg)](https://github.com/aldian/fuel-route-optimizer/actions/workflows/ci.yml)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![Django 6.1.3](https://img.shields.io/badge/django-6.1.3-green.svg)](https://www.djangoproject.com/)
[![DRF 3.18.3](https://img.shields.io/badge/DRF-3.18.3-red.svg)](https://www.django-rest-framework.org/)
[![Coverage 95.8%](https://img.shields.io/badge/coverage-95.8%25-brightgreen.svg)](https://pytest-cov.readthedocs.io/)
[![OpenAPI 3.1](https://img.shields.io/badge/OpenAPI-3.1-orange.svg)](https://swagger.io/specification/)
[![Spec-Driven Development](https://img.shields.io/badge/SDD-specs%2F%20Source%20of%20Truth-purple.svg)](specs/README.md)

A high-performance, cost-effective fuel route optimization REST API and interactive map visualization platform built for long-haul freight across the continental United States.

Given any origin and destination within the USA, the API calculates the driving route, identifies all viable fuel stops along the highway corridor, computes the mathematically optimal refueling strategy to minimize total fuel spend, and produces an accessible visual map.

> [!IMPORTANT]
> **Developed with Spec-Driven Development (SDD) — [`specs/`](specs/README.md) is the Single Source of Truth**
>
> Every developer working on this project must follow our specification-first workflow ([`specs/README.md`](specs/README.md) & [`CONTRIBUTING.md`](CONTRIBUTING.md)):
> 1. **Specs First**: All architectural boundaries, API schemas, optimization formulas, feature flags, and database migrations are defined in [`specs/`](specs/) **before** writing or modifying application code.
> 2. **Feature Flags for Easy Feature Rollback**: Every new feature or heuristic must be gated behind a dynamic feature flag (`apps/common/flags.py` / `./bin/spotter-feature-flag`) so features can be toggled off in `< 0.1 s` in production without redeploying containers.
> 3. **Strict Database Schema Rules (Expand-Contract) for Safe Release Rollback**: Because rolling back a production database with live data is hazardous, all schema changes must obey strict backward-compatible **Expand-Contract** rules (`RULE-MIG-01`..`06` in [`specs/08-feature-flags-and-zero-downtime-database-migrations.md`](specs/08-feature-flags-and-zero-downtime-database-migrations.md), enforced in CI by `./bin/spotter-check-migrations`). Every migration must remain 100% compatible with code version $N-1$ so Canary, Blue-Green, or Kubernetes rollbacks (`./bin/spotter-rollback`) work instantaneously without database rollbacks.

---

## 1. System Architecture

```mermaid
flowchart TD
    Client["Client / Postman / Web Browser"] -->|"POST /api/v1/route/"| API["Django REST Framework API"]

    subgraph LocationResolution ["1. US Location Resolution Engine"]
        API --> Resolver["USLocationResolver"]
        Resolver -->|"1. Process Cache"| MemCache["In-Memory LRU Cache"]
        Resolver -->|"2. Local Database"| GazDB["USPlaceGazetteer (65,920 Places)"]
        Resolver -->|"3. External Fallback"| ExtGeo["Census / Photon Geocoders (Budget Guarded)"]
        Resolver --> BoundVal["USBoundaryValidator (MultiPolygon Ray-Casting)"]
    end

    subgraph RoutingService ["2. Highway Routing Service"]
        API --> Chain["RoutingProviderChain"]
        Chain --> OSRM["OSRM Driving API"]
        Chain -.->|"Failover"| Valhalla["Valhalla Routing API"]
        Chain --> Codec["Polyline6 Decoder (Vectorized)"]
    end

    subgraph SpatialOptimization ["3. Spatial Search & Optimization"]
        Codec --> Projector["RouteProjector (scipy cKDTree 3D ECEF)"]
        Projector --> SpatialIdx["StationSpatialIndex (6,626 Geocoded US Stations)"]
        SpatialIdx --> Filter["Corridor Filtering (<= 8 miles)"]
        Filter --> Solver["MinCostRefuelSolver (Monotonic Stack O(K log K))"]
    end

    Solver --> Resp["RoutePlan + Fuel Stops + Interactive Map URL"]
    Resp --> Client
```

---

## 2. Core Technical Capabilities

1. **Sub-15ms Compute Latency ($14.34\text{ ms}$ P50 Coast-to-Coast)**:
   - Stations are pre-projected into 3D Earth-Centered, Earth-Fixed (ECEF) unit vectors:
     $$x = \cos(\text{lat}) \cos(\text{lon}), \quad y = \cos(\text{lat}) \sin(\text{lon}), \quad z = \sin(\text{lat})$$
   - An in-memory `scipy.spatial.cKDTree` filters the 6,626 US truckstops against 2-mile route intervals in $1.3\text{ ms}$ ($18\times$ faster than haversine loops).

2. **$O(K \log K)$ Monotonic Stack Refueling Solver**:
   - Implements a greedy lookahead strategy with step-down fills:
     - If a cheaper station exists downstream within the 500-mile tank horizon, buy only enough fuel to reach that station.
     - If the current station is the cheapest reachable, fill the tank to 50 gallons to maximize mileage at low cost.
   - Micro-stop consolidation absorbs sub-5-gallon fills into neighboring stops.

3. **Zero-to-Minimal External Calls ($\le 3$ Call Budget Guard)**:
   - Includes an offline database of **65,920 US Census places and ZIP codes**. Over 99% of requests resolve with **0 external geocoding calls**.
   - Enforced by `ExternalCallBudget` preventing runaway latency or third-party rate-limiting.

4. **Strict Domestic US Scope & Boundary Ray-Casting**:
   - High-resolution MultiPolygon ray-casting point-in-polygon validator (`apps/routing/boundary.py`).
   - Automatically rejects Canadian/Mexican locations (e.g., Toronto, Montreal, Tijuana) and prevents international shortcuts (e.g., Ontario detour between Detroit and Buffalo).

5. **Tested to $\ge 95\%$ Coverage Gate**:
   - 63 tests across Unit, Integration, Live-Server E2E with real TCP sockets and concurrency stress, and Latency Benchmarks.
   - Enforced in CI via `--cov-fail-under=95` (**95.73% achieved**).

---

## 3. Quick Start

### 3.1 Prerequisites
- Python 3.12+
- `pip` and `virtualenv`

### 3.2 Installation

```bash
# Clone the repository
git clone git@github.com:aldian/fuel-route-optimizer.git
cd fuel-route-optimizer

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3.3 Database Setup & Data Ingestion

```bash
# Apply database migrations
python manage.py migrate

# Load geocoded US truckstops (6,626 stations)
python manage.py load_fuel_stations

# Load local US gazetteer (65,920 cities and ZIP codes)
python manage.py load_us_gazetteer
```

### 3.4 Run the Development Server

```bash
python manage.py runserver
```

The API will be available at `http://127.0.0.1:8000/`.
- Interactive Swagger UI: `http://127.0.0.1:8000/api/docs/`
- ReDoc UI: `http://127.0.0.1:8000/api/redoc/`
- Health check: `http://127.0.0.1:8000/api/v1/health/`

---

## 4. API Reference

### 4.1 Optimize Route (`POST /api/v1/route/`)

Calculates the optimal refueling schedule and route map.

#### Request Body:
```json
{
  "start": "New York, NY",
  "finish": "Los Angeles, CA",
  "vehicle_range_miles": 500.0,
  "mpg": 10.0,
  "initial_fuel_gallons": 50.0,
  "corridor_search_radius_miles": 8.0,
  "include_geojson": false
}
```

#### Example Response:
```json
{
  "route": {
    "origin": {
      "input": "New York, NY",
      "resolved_name": "New York, NY",
      "latitude": 40.7128,
      "longitude": -74.006,
      "state": "NY"
    },
    "destination": {
      "input": "Los Angeles, CA",
      "resolved_name": "Los Angeles, CA",
      "latitude": 34.0522,
      "longitude": -118.2437,
      "state": "CA"
    },
    "total_distance_miles": 2789.4,
    "total_duration_hours": 41.2,
    "polyline6": "..."
  },
  "fuel_summary": {
    "total_fuel_consumed_gallons": 278.94,
    "total_fuel_purchased_gallons": 228.94,
    "total_money_spent_on_fuel_usd": 684.22,
    "vehicle_mpg": 10.0,
    "fuel_stops_count": 6
  },
  "fuel_stops": [
    {
      "stop_sequence": 1,
      "opis_truckstop_id": 20412,
      "truckstop_name": "Pilot Travel Center #291",
      "address": "I-80 Exit 173",
      "city": "Austintown",
      "state": "OH",
      "price_per_gallon_usd": 2.989,
      "gallons_to_fill": 39.4,
      "cost_usd": 117.77,
      "distance_from_origin_miles": 394.0,
      "arrival_fuel_gallons": 10.6,
      "departure_fuel_gallons": 50.0,
      "detour_miles": 0.4
    }
  ],
  "map": {
    "map_url": "http://127.0.0.1:8000/api/v1/route/map/?origin=New+York%2C+NY&destination=Los+Angeles%2C+CA",
    "bounds": {
      "southwest": [34.0522, -118.2437],
      "northeast": [41.6528, -74.006]
    }
  },
  "meta": {
    "compute_time_ms": 13.8,
    "external_api_calls_count": 1,
    "cache_hit": false
  }
}
```

### 4.2 Query-String Optimization (`GET /api/v1/route/`)
Supports identical capabilities via HTTP GET for easy testing:
```
GET /api/v1/route/?start=Dallas,+TX&finish=Austin,+TX&mpg=10.0
```

### 4.3 Accessible Standalone Map (`GET /api/v1/route/map/`)
Renders a full-screen, accessible Leaflet map (`--text: #000000;` contrast compliant) highlighting the route polyline, fuel stop markers with pricing popups, and turn-by-turn overview.

---

## 5. Postman Collection & Visualizer

A complete Postman Collection v2.1 is included at:
[`postman/Spotter_Fuel_Route_Optimizer.postman_collection.json`](postman/Spotter_Fuel_Route_Optimizer.postman_collection.json)

### Postman Visualizer Integration
The collection includes an automated test script that binds the response data to an embedded Leaflet map via `pm.visualizer.set()`:
1. Open **Postman** and click **Import**.
2. Select `postman/Spotter_Fuel_Route_Optimizer.postman_collection.json`.
3. Send the **"Optimize Route (Coast-to-Coast POST)"** request.
4. Click the **"Visualize"** tab in the response pane to interact with the route map directly inside Postman!

---

## 6. Running Tests & Coverage Gate

Run the complete test suite enforcing the **$\ge 95\%$ test coverage gate**:

```bash
pytest tests/ --cov=apps --cov-branch --cov-report=term-missing --cov-fail-under=95
```

### Test Suite Structure
- `tests/unit/`: 48 unit tests covering models, spatial indexing, routing providers, boundary ray-casting, solver, and viewsets.
- `tests/integration/`: 8 API integration tests (`TC-INT-01` through `TC-INT-08`) testing cache roundtrips, call budget enforcement, failover, and non-US rejection.
- `tests/e2e/`: 6 live-server E2E tests (`TC-E2E-01` through `TC-E2E-06`) running against real TCP sockets, multi-client concurrency (20 simultaneous threads), and Leaflet HTML rendering.
- `tests/benchmarks/`: Coast-to-Coast compute latency benchmark verifying the $< 15\text{ ms}$ threshold.

---

## 7. Production Deployment

### 7.1 Interactive Deployment Wizard (From Zero)
Bootstrap production configurations with a single interactive CLI command:

```bash
./bin/spotter-deploy
# or
python manage.py deploy_wizard --interactive
```

The wizard guides administrators through:
- **Target Cloud Platform**: GCP GKE, AWS EKS/ECS, Azure AKS, VPS Docker, Fly.io, Render.
- **Domain & Staging Hostnames**: Configures production and staging hostnames, updating Kubernetes Ingress, GKE `ManagedCertificate`, and Django `ALLOWED_HOSTS`.
- **Automated DNS Provisioning (AWS Route 53 & Namecheap API)**:
  - Eliminates manual DNS changes by automatically provisioning `A` records pointing to your External Load Balancer or VPS public IP.
  - **AWS Route 53**: Performs atomic `UPSERT` actions and automatically discovers Hosted Zone IDs.
  - **Namecheap DNS**: Queries `getHosts` first to preserve existing MX/TXT/CNAME records, then executes atomic `setHosts`.
  - **Auto-IP Detection**: Detects public outbound IPv4 address via `api.ipify.org` if target IP is not specified.
  - Non-interactive automation flags: `--dns-provider [route53|namecheap]`, `--dns-target-ip`, `--route53-zone-id`, `--namecheap-api-user`, `--namecheap-api-key`, `--namecheap-client-ip`.
- **High-Entropy Secrets**: Generates cryptographically secure 64-character Django `SECRET_KEY`.
- **PostgreSQL & Redis Wiring**: Validates database and cache connection URIs.
- **Configuration Output**: Generates `.env.production` (permissions `0600`) and ready-to-run GitHub Actions secret upload script (`deploy/generated/set_github_secrets.sh`).

### 7.2 Non-Interactive Automation Example
```bash
./bin/spotter-deploy \
  --target k8s \
  --domain api.fuel-optimizer.example.com \
  --staging-domain staging-api.fuel-optimizer.example.com \
  --dns-provider route53 \
  --route53-zone-id Z1234567890ABC \
  --dns-target-ip 34.120.55.80 \
  --database-url postgresql://dbuser:securepass@10.0.0.5:5432/fueldb \
  --redis-url redis://10.0.0.6:6379/0 \
  --non-interactive
```

### 7.2 Multi-Cloud Deployment Artifacts
- **Docker Compose + Caddy**: `docker-compose.prod.yml` and `deploy/caddy/Caddyfile` for single-node VPS with automated Let's Encrypt SSL.
- **Kubernetes / GCP GKE**: `deploy/k8s/base/` with Kustomize overlays in `deploy/k8s/overlays/gcp-gke/` including HPA auto-scaling and Cloud Armor security.
- **Terraform IaC**: `deploy/terraform/gcp/main.tf` provisioning GKE Autopilot, Cloud SQL PostgreSQL, and Memorystore Redis.
- **PaaS**: `fly.toml` (Fly.io) and `render.yaml` (Render blueprint).

### 7.3 Dynamic Feature Flags & Kill-Switches
Decouple deployment from release and manage live features with sub-millisecond evaluation (<0.05ms):

```bash
# List configured feature flags
./bin/spotter-feature-flag --list

# Toggle instant kill-switch or feature gate
./bin/spotter-feature-flag --set EXPERIMENTAL_SOLVER on
./bin/spotter-feature-flag --set EXPERIMENTAL_SOLVER off

# Progressive percentage rollout (consistent hashing per user/key)
./bin/spotter-feature-flag --set CANARY_ROUTING 25%
```

### 7.4 Zero-Downtime Database Migration Safety Linter
Prevents dangerous table locks and production outages by enforcing Expand-Contract schema rules:

```bash
# Run local schema migration validator
./bin/spotter-check-migrations

# Automated CI gate: rejects NOT NULL columns without defaults, model/field renames, and destructive SQL
python manage.py check_migration_safety --fail-on-warning
```

### 7.5 Rapid Rollback & Canary / Blue-Green Deployments
- **1-Click Emergency Rollback (GitHub Actions)**: Trigger `.github/workflows/rollback.yml` from GitHub Actions UI to undo deployments with automated health probe verification.
- **CLI Rollback Utility**:
  ```bash
  # Rollback Kubernetes production to previous revision
  ./bin/spotter-rollback --target k8s --namespace production

  # Rollback to specific revision with automated health check verification
  ./bin/spotter-rollback --target k8s --namespace production --to-revision 2 --verify-url https://api.fuel-optimizer.com/api/v1/health/
  ```
- **Blue-Green Deployment Overlay**: `deploy/k8s/overlays/blue-green/` allows instantaneous traffic pointer switches (< 1 second) via `./deploy/k8s/overlays/blue-green/switch_traffic.sh [blue|green]`.
- **Canary Deployment Overlay**: `deploy/k8s/overlays/canary/` provisions a 10% traffic-weighted canary ingress.

### 7.6 One-Command Ephemeral Demo Teardown (`$0/hr` Cost Saver)
When deploying the application for a live demonstration, you can tear down **all** provisioned cloud infrastructure, Kubernetes Ingresses/Load Balancers, Docker volumes, and automated Route 53 / Namecheap DNS `A` records with a single command immediately after the demo so ongoing cloud costs drop to **`$0.00/hr`**:

```bash
# Automatically reads deploy/generated/deployment_state.json written by spotter-deploy
./bin/spotter-destroy

# Preview all DNS & infrastructure teardown commands without executing them
./bin/spotter-destroy --dry-run

# Non-interactive teardown including managed Kubernetes cluster deletion (GKE / EKS / AKS)
./bin/spotter-destroy --target k8s --k8s-provider gcp-gke --destroy-cluster --yes
```

- **1-Click GitHub Actions Demo Teardown**: Trigger [`.github/workflows/destroy.yml`](.github/workflows/destroy.yml) (*"Destroy Ephemeral Demo Deployment (Save Costs)"*) from the GitHub Actions UI and type `DESTROY` to tear down the environment remotely.
- **Feature Flag Protection for Permanent Production**: Controlled by the `ALLOW_EPHEMERAL_TEARDOWN` feature flag (`True` by default during demo phase). Once transitioning to a permanent production environment, run `./bin/spotter-feature-flag --set ALLOW_EPHEMERAL_TEARDOWN off` to block accidental teardowns.

---

## 8. Spec-Driven Development (SDD) — Authoritative Specification Suite

This project is developed openly using **Spec-Driven Development (SDD)**. The files in [`specs/`](specs/) are the **authoritative single source of truth** for all architecture, algorithms, API contracts, feature flags, and database schema rules.

When contributing to this project:
1. **Update the Spec First**: Any change to API behavior, optimization logic, feature flags, or database models must be specified in [`specs/`](specs/) before modifying code in `apps/`.
2. **Design for Instant Feature Rollback**: Every new feature must declare its feature flag in [`specs/08-feature-flags-and-zero-downtime-database-migrations.md`](specs/08-feature-flags-and-zero-downtime-database-migrations.md) and [`config/settings/base.py`](config/settings/base.py) so it can be toggled off in `< 0.1 s` via `./bin/spotter-feature-flag` without redeploying code.
3. **Design for Zero-Downtime Database Rollback (Expand-Contract)**: Because rolling back a production database after live data is written is risky, all schema changes must follow the 3-phase **Expand-Contract** lifecycle (`RULE-MIG-01`..`06`, validated in CI by `python manage.py check_migration_safety`), keeping the schema 100% backward-compatible with code version $N-1$.

| Specification File | Authoritative Scope |
| :--- | :--- |
| [`specs/README.md`](specs/README.md) | Master SDD constitution, requirement traceability matrix (`REQ-*`), SLAs, and Architectural Decision Records (`ADR-001`..`ADR-006`). |
| [`specs/01-system-architecture.md`](specs/01-system-architecture.md) | Django 6.1.3 modular architecture, request lifecycle, multi-tier caching, concurrency, and latency budgets. |
| [`specs/02-data-pipeline-and-schema.md`](specs/02-data-pipeline-and-schema.md) | Forensic audit of `fuel-prices-for-be-assessment.csv` (`8,151` rows), offline geocoding, relational schema, 3D `cKDTree` spatial index, and schema evolution rules. |
| [`specs/03-routing-and-geocoding-integration.md`](specs/03-routing-and-geocoding-integration.md) | Domestic US boundary ray-casting, zero-call US Census Gazetteer, parallel geocoder fallback, OSRM/Valhalla routing, and `<= 3` call budget guard. |
| [`specs/04-fuel-optimization-algorithm.md`](specs/04-fuel-optimization-algorithm.md) | Mathematical formulation (`500`-mile range, `10` MPG, `50`-gal tank), polyline mile-marker projection, detour modeling, and $O(K \log K)$ monotonic stack solver. |
| [`specs/05-api-contract-and-map-delivery.md`](specs/05-api-contract-and-map-delivery.md) | REST API endpoints, request/response schemas, GeoJSON `FeatureCollection`, encoded polyline, standalone Leaflet map view, and Postman Visualizer integration. |
| [`specs/06-testing-verification-and-delivery-plan.md`](specs/06-testing-verification-and-delivery-plan.md) | `>= 95%` unit test coverage gate, integration, live-server E2E concurrency tests, performance benchmarks, and 5-minute Loom walkthrough guide. |
| [`specs/07-cicd-and-production-deployment-cli.md`](specs/07-cicd-and-production-deployment-cli.md) | Multi-target CI/CD pipelines (AWS, GCP, Azure, K8s, Docker/Caddy, PaaS), Terraform IaC, automated Route 53 & Namecheap DNS provisioning, and `deploy_wizard` CLI. |
| [`specs/08-feature-flags-and-zero-downtime-database-migrations.md`](specs/08-feature-flags-and-zero-downtime-database-migrations.md) | Dynamic feature flags (`< 0.05 ms`), Expand-Contract zero-downtime database migration rules (`RULE-MIG-01`..`06`), and Canary / Blue-Green / 1-click rollbacks. |
| [`specs/09-developer-rules-and-system-integrity.md`](specs/09-developer-rules-and-system-integrity.md) | The 6 non-negotiable automated quality gates and contributor rules of engagement. |

---

## 9. Developer Rules & Contributing

All contributors and AI coding agents must read and follow:
- **[`specs/README.md`](specs/README.md)**: Authoritative SDD workflow and requirement traceability.
- **[`CONTRIBUTING.md`](CONTRIBUTING.md)**: Developer workflow, pre-push verification commands, feature flag rules, and Expand-Contract database schema policy.
- **[`AGENTS.md`](AGENTS.md)**: Concise SDD & safe-rollback instructions for developers and AI pair-programming agents.
- **[`.github/pull_request_template.md`](.github/pull_request_template.md)**: Mandatory pull request checklist.

---

## 10. License

This project is licensed under the MIT License.

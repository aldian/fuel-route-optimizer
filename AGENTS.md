# AI Agent & Developer Instructions (`AGENTS.md`)

> [!IMPORTANT]
> **Spec-Driven Development (SDD) — `specs/` is the Single Source of Truth**
> This repository is built and maintained strictly using **Spec-Driven Development (SDD)**. The specification files in [`specs/`](./specs/README.md) are the authoritative **Single Source of Truth** for all architecture, data models, algorithms, feature flags, and database migration safety rules.
>
> **Never write application code, add a feature, or generate a database migration without first reading and updating the corresponding specification in `specs/`.**

---

## 1. Authoritative Specification Index (`specs/`)

Before planning or implementing any change, consult the master index in [`specs/README.md`](./specs/README.md) and the relevant domain specification:

| Spec ID | File | Scope & Source-of-Truth Responsibility |
| :--- | :--- | :--- |
| **Index** | [`specs/README.md`](./specs/README.md) | Master SDD Governance Rules, Requirement Traceability Matrix (`REQ-*`), and Architecture Decision Records (`ADR-001` – `ADR-006`). |
| **Spec 01** | [`specs/01-system-architecture.md`](./specs/01-system-architecture.md) | Django 6.1.3 / Python 3.12 runtime, request lifecycle, multi-tier caching, concurrency, and latency budgets. |
| **Spec 02** | [`specs/02-data-pipeline-and-schema.md`](./specs/02-data-pipeline-and-schema.md) | `FuelStation` model, OPIS CSV deduplication, 3D `cKDTree`, and **Section 6: Expand-Contract Database Schema Rules (`REQ-OPS-005`)**. |
| **Spec 03** | [`specs/03-routing-and-geocoding-integration.md`](./specs/03-routing-and-geocoding-integration.md) | Domestic US ray-casting boundary, zero-call US Census Gazetteer, OSRM/Valhalla routing, and `<= 3` external call budget guard. |
| **Spec 04** | [`specs/04-fuel-optimization-algorithm.md`](./specs/04-fuel-optimization-algorithm.md) | Mathematical formulation (`500`-mile range, `10` MPG, `50`-gal tank) and $O(K \log K)$ monotonic queue DP solver. |
| **Spec 05** | [`specs/05-api-contract-and-map-delivery.md`](./specs/05-api-contract-and-map-delivery.md) | REST API endpoints, GeoJSON `FeatureCollection`, encoded polyline, and interactive Leaflet map delivery. |
| **Spec 06** | [`specs/06-testing-verification-and-delivery-plan.md`](./specs/06-testing-verification-and-delivery-plan.md) | Red-Green-Refactor TDD workflow, $\ge 95.0\%$ coverage gate (`--cov-fail-under=95`), E2E suite, and 5-minute Loom plan. |
| **Spec 07** | [`specs/07-cicd-and-production-deployment-cli.md`](./specs/07-cicd-and-production-deployment-cli.md) | Zero-to-Production wizard (`./bin/spotter-deploy`), Route 53 & Namecheap DNS automation, and **One-Command Ephemeral Demo Teardown (`./bin/spotter-destroy`, `REQ-OPS-006`)**. |
| **Spec 08** | [`specs/08-feature-flags-and-zero-downtime-database-migrations.md`](./specs/08-feature-flags-and-zero-downtime-database-migrations.md) | **Feature Flag Catalog (`apps/common/flags.py`), Multi-Layer Rollback Architecture (`./bin/spotter-rollback`), and Zero-Downtime Migration Linter (`./bin/spotter-check-migrations`)**. |
| **Spec 09** | [`specs/09-developer-rules-and-system-integrity.md`](./specs/09-developer-rules-and-system-integrity.md) | Mandatory developer & agent integrity rules and CI enforcement gates. |
| **Spec 10** | [`specs/10-sre-and-sla-observability.md`](./specs/10-sre-and-sla-observability.md) | SRE SLI/SLO telemetry (`apps/common/sre.py`), Error Budget burn tracking, `/api/v1/sre/sla/`, Prometheus `/api/v1/sre/metrics/`, and `./bin/spotter-sre` CLI auditor (`REQ-OPS-007`). |

---

## 2. Mandatory SDD Workflow for Every Change

```mermaid
flowchart LR
    A["1. Update Spec in specs/<br/>(Define Requirement, Feature Flag,<br/>& Expand-Contract Schema Plan)"] --> B["2. Register Feature Flag<br/>in apps/common/flags.py"]
    B --> C["3. Write Failing Tests<br/>(Both Flag=ON & Flag=OFF)"]
    C --> D["4. Implement Code &<br/>Safe Expand Migration"]
    D --> E["5. Verify Linter & Coverage<br/>(./bin/spotter-check-migrations<br/>& pytest >= 95%)"]
```

1. **Spec First (`specs/`)**:
   - Update the relevant specification in `specs/` **before** modifying application code.
   - Document the new/updated Requirement ID (`REQ-*`), the controlling **Feature Flag** name and rollback behavior, and (if touching models) the **Expand-Contract** migration phase.
2. **Feature Flag First for Effortless Feature Rollback ([Spec 08](./specs/08-feature-flags-and-zero-downtime-database-migrations.md))**:
   - Every behavioral, algorithmic, or API change must be gated behind a feature flag registered in [`apps/common/flags.py`](./apps/common/flags.py) (`DEFAULT_FLAGS`).
   - Why? So any feature can be rolled back in **$< 0.1\text{ s}$** via `./bin/spotter-feature-flag disable <flag_name>` without redeploying containers or touching the database.
   - Unit tests must test both `flag=True` (new behavior) and `flag=False` (safe fallback behavior).
3. **Strict Expand-Contract Database Schema Rules ([Spec 02 §6](./specs/02-data-pipeline-and-schema.md) & [Spec 08 §3](./specs/08-feature-flags-and-zero-downtime-database-migrations.md))**:
   - Because rolling back a production PostgreSQL/PostGIS database containing live data is dangerous and impractical, **all schema changes must be 100% backward-compatible with the currently running production code (`v1`)**.
   - Never drop a column (`RemoveField`), drop a table (`DeleteModel`), rename a column/table (`RenameField`/`RenameModel`), add a non-nullable column without a default, or build a blocking index in the same release as an application change.
   - Always run `./bin/spotter-check-migrations` (`RULE-MIG-01` through `RULE-MIG-06`) before committing. This guarantees that rolling back application containers (`./bin/spotter-rollback`) works cleanly against the already-migrated database without running `migrate` backwards.

---

## 3. Essential Commands (Use `.venv/bin/python`)

```bash
# 1. Validate zero-downtime database migration safety (RULE-MIG-01 .. RULE-MIG-06)
./bin/spotter-check-migrations

# 2. Run unit tests with the mandatory >= 95% coverage gate
.venv/bin/pytest tests/unit/ -m unit --cov=apps --cov-report=term-missing --cov-fail-under=95

# 3. Inspect or toggle feature flags
./bin/spotter-feature-flag --list
./bin/spotter-feature-flag --set <FLAG_NAME> off

# 4. Perform application rollback (Canary / Blue-Green / K8s / Docker)
./bin/spotter-rollback --target k8s --namespace production --dry-run

# 5. Tear down ephemeral demo deployment & DNS records ($0/hr cost saver)
./bin/spotter-destroy --dry-run

# 6. Audit all 6 SLAs / SLOs and Error Budget via SRE CLI
./bin/spotter-sre
```

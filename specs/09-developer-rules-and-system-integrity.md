# Spec 09 — Developer Rules of Engagement & System Integrity

> **Status**: Approved & Active  
> **Audience**: All engineers and contributors  
> **Reference**: [CONTRIBUTING.md](../CONTRIBUTING.md)  

---

## 1. Purpose & Core Tenets: Spec-Driven Development (SDD)

To ensure the long-term reliability, low-latency performance ($< 15\text{ ms}$), and zero-downtime availability of the **Spotter Fuel Route Optimizer**, this project is openly developed using **Spec-Driven Development (SDD)**.

> [!IMPORTANT]
> **`specs/` Is the Authoritative Single Source of Truth**
> The specification files in [`specs/`](./README.md) are the single source of truth for the entire system. Specifically:
> 1. **Feature Flags Originate in `specs/` ([Spec 08](./08-feature-flags-and-zero-downtime-database-migrations.md))**: Every behavioral or algorithmic change defines its feature flag and fallback behavior in `specs/08-feature-flags-and-zero-downtime-database-migrations.md` first, enabling `< 0.1s` feature rollback (`./bin/spotter-feature-flag disable <flag_name>`) without redeploying containers.
> 2. **Database Schema Rules Originate in `specs/` ([Spec 02 §6](./02-data-pipeline-and-schema.md) & [Spec 08 §3](./08-feature-flags-and-zero-downtime-database-migrations.md))**: Because rolling back a production database with live data risks data loss and lock contention, every schema change is designed in `specs/` using the **Expand-Contract** pattern (`RULE-MIG-01`..`06`). This guarantees that rolling back application code (`./bin/spotter-rollback`) works seamlessly without rolling back the database.

```mermaid
flowchart TD
    subgraph CoreTenets ["Core Tenets (specs/ as Single Source of Truth)"]
        T1["1. Spec-Driven Development (specs/ First)"]
        T2["2. Feature Flags in Specs for < 0.1s Rollback"]
        T3["3. Expand-Contract Schema Rules (RULE-MIG-01..06)"]
        T4["4. Non-Negotiable Test Gate (>= 95% Coverage)"]
        T5["5. Hard External API Call Budget (<= 3 Calls)"]
        T6["6. Domestic US Boundary Ray-Casting"]
    end
```

---

## 2. The 6 Non-Negotiable Automated Gates

Every branch and pull request targeting `main` must pass all 6 automated quality gates:

```mermaid
graph TD
    PR["Developer Code Submission"] --> G1{"1. Python Syntax (py_compile)"}
    G1 -->|Pass| G2{"2. Zero-Downtime Migrations (RULE-MIG-01..06)"}
    G2 -->|Pass| G3{"3. >= 95.0% Coverage & 100% Tests Pass"}
    G3 -->|Pass| G4{"4. External Call Budget Check (<= 3 Calls)"}
    G4 -->|Pass| G5{"5. Domestic US Boundary Ray-Casting"}
    G5 -->|Pass| G6{"6. Git Commit Standard"}
    G6 -->|Pass| Merge["Deploy to Staging"]
```

### 2.1 Gate 1: Python Compilation & Typing
- **Rule**: Run `python -m py_compile <file>` after significant changes.
- **Typing**: All functions, methods, and serializers must declare complete type annotations.
- **CI Step**: Automated `py_compile` checks all files in `manage.py`, `config/`, and `apps/`.

### 2.2 Gate 2: Database Schema Safety (Expand-Contract)
- **Problem**: In online production databases, direct column deletions or additions with `NOT NULL` lock tables and break $N-1$ instances during rolling updates.
- **Enforced Rules**:
  - `RULE-MIG-01`: No `AddField` with `null=False` and no default.
  - `RULE-MIG-02`: No `RemoveField` in active releases.
  - `RULE-MIG-03`: No `RenameField`.
  - `RULE-MIG-04`: No `RenameModel`.
  - `RULE-MIG-05`: No raw SQL with `DROP TABLE`, `DROP COLUMN`, `TRUNCATE`.
  - `RULE-MIG-06`: No altering nullable to `null=False` without defaults.
- **Verification Tool**: `./bin/spotter-check-migrations`.

### 2.3 Gate 3: Test Suite Quality & $\ge 95\%$ Coverage
- **Threshold**: Line and branch coverage must meet or exceed **$95.0\%$** across `apps/`.
- **Pass Rate**: Must be **$100\%$**; no skipped or failing tests allowed.
- **Isolation**: Unit tests must not make external network requests; all geocoders, routing APIs, and DNS providers must be mocked.
- **Command**: `pytest tests/ --cov=apps --cov-fail-under=95`.

### 2.4 Gate 4: External API Call Budget ($\le 3\text{ Calls}$)
- **Offline Gazetteer**: 65,920 Census places and ZIP codes must resolve offline with **0 external API calls**.
- **Hard Guard**: `ExternalCallBudget` guarantees no route optimization request exceeds 3 external calls.

### 2.5 Gate 5: Domestic US Scope & Geo-Boundary Enforcement
- **Ray-Casting Algorithm**: High-resolution ray-casting point-in-polygon validator (`apps/routing/boundary.py`) validates all route points against the US border.
- **Foreign Rejection**: Requests starting, ending, or traversing non-US territory (e.g. Ontario shortcut between Detroit and Buffalo) must be rejected with `LOCATION_OUTSIDE_USA`.

### 2.6 Gate 6: Git Commit Message Quality
- **Rule**: Commit messages must describe the **content change** (what was added, modified, or removed), never the action taken or process details.
  - ✅ `"Add Category and Title facet bar charts to browse page"`
  - ❌ `"Sync from dev server: latest browse.tt"`

---

## 3. UI, Accessibility (a11y) & UX Standards

- **Text Contrast**: Never use white or light gray text on light backgrounds. Ensure all body, summary, and informational texts are dark or black (`#000000` / `--text`) to guarantee high accessibility contrast.
- **No Technical Jargon in UI Copy**: Never expose internal software engineering jargon (such as `"payload"`, `"hydration"`, `"JWT"`, `"endpoint"`, `"UUID"`, `"deeplink"`) or cloud infrastructure vendor names to end users.
- **Branding Assets**: Use modern vector SVG icons rather than raster graphics.

---

## 4. Operational Emergency Hierarchy

If an error or degradation occurs in production:
1. **Level 1 (Feature Kill-Switch)**: `./bin/spotter-feature-flag disable <flag_name>` (`< 0.1s`, no restarts or database touch).
2. **Level 2 (Blue-Green Pointer Switch)**: `./bin/spotter-rollback --target k8s-blue-green --namespace production` (`~1s`).
3. **Level 3 (1-Click GitHub Actions Rollback)**: Trigger `.github/workflows/rollback.yml`.
4. **Level 4 (CLI Rollback)**: `./bin/spotter-rollback --target k8s-canary --namespace production`.

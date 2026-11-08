## Summary of Changes
<!-- Describe the content change: what was added, modified, or removed. -->

### Related Specification
- [ ] Aligned with [`specs/`](specs/) (Spec document updated if behavior changed)

---

## Pre-Merge Quality Checklist

Please confirm that all items below have been verified locally before requesting review:

### 1. Code Quality & Syntax
- [ ] `python -m py_compile` passed on all modified Python files with zero syntax errors.
- [ ] Strict type annotations added to all new functions and methods.
- [ ] `python manage.py check` passes with 0 issues.

### 2. Database Schema Safety (Zero-Downtime Expand-Contract)
- [ ] `./bin/spotter-check-migrations` passed with 0 errors.
- [ ] No `NOT NULL` columns added without an explicit default value.
- [ ] No direct `RemoveField`, `RenameField`, or destructive `RunSQL` operations.

### 3. Test Coverage & Verification
- [ ] `pytest tests/ --cov=apps --cov-fail-under=95` passes with $\ge 95.0\%$ coverage.
- [ ] 100% test pass rate (no skipped or failing tests).
- [ ] External network calls are mocked; no real external HTTP requests during unit tests.

### 4. System Integrity & Boundaries
- [ ] External API call budget guarded ($\le 3$ external calls per route request).
- [ ] Domestic US boundary ray-casting enforced (Canada/Mexico coordinates rejected).
- [ ] New user-facing capabilities wrapped in dynamic feature flags if experimental.

### 5. UI & Accessibility (a11y)
- [ ] Text contrast verified (no white/light gray text on light backgrounds).
- [ ] No internal software engineering jargon (`payload`, `hydration`, `endpoint`, etc.) exposed to end users.

### 6. Git Standards
- [ ] Commit messages follow the standard describing the *content change* rather than session actions.

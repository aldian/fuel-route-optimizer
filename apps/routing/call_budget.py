"""External API call budget tracking and hard limit enforcement."""
from dataclasses import dataclass, field
import time
from typing import Any
from apps.api.exceptions import ExternalCallBudgetExceededError


@dataclass(slots=True, frozen=True)
class CallRecord:
    service: str
    operation: str
    duration_ms: float


@dataclass(slots=True)
class ExternalCallBudget:
    """Thread-safe / request-scoped budget tracker enforcing <= 3 external HTTP calls."""

    max_calls: int = 3
    calls: list[CallRecord] = field(default_factory=list)

    @property
    def total_count(self) -> int:
        return len(self.calls)

    @property
    def remaining(self) -> int:
        return max(0, self.max_calls - len(self.calls))

    def check_can_call(self, service: str, operation: str) -> None:
        """Verify that another call is permitted within the budget ceiling."""
        if len(self.calls) >= self.max_calls:
            raise ExternalCallBudgetExceededError(
                f"External call budget exceeded ({len(self.calls)}/{self.max_calls}). "
                f"Cannot execute {service}:{operation}."
            )

    def record_call(self, service: str, operation: str, duration_ms: float) -> None:
        """Record a completed external API request."""
        self.calls.append(
            CallRecord(
                service=service,
                operation=operation,
                duration_ms=round(duration_ms, 2),
            )
        )

    def to_dict(self) -> dict[str, Any]:
        """Format for API response metadata."""
        return {
            "total_count": len(self.calls),
            "budget_limit": self.max_calls,
            "calls": [
                {
                    "service": c.service,
                    "operation": c.operation,
                    "duration_ms": c.duration_ms,
                }
                for c in self.calls
            ],
        }

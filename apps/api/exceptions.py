"""Standardized API exception handling and domain exceptions."""
from typing import Any
from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.views import exception_handler


class DomainAPIError(APIException):
    """Base domain exception with structured error code and details."""

    status_code = status.HTTP_400_BAD_REQUEST
    default_code = "DOMAIN_ERROR"
    default_message = "A domain error occurred."

    def __init__(
        self,
        message: str | None = None,
        code: str | None = None,
        details: dict[str, Any] | None = None,
        status_code: int | None = None,
    ) -> None:
        self.message = message or self.default_message
        self.code = code or self.default_code
        self.details = details or {}
        if status_code is not None:
            self.status_code = status_code
        super().__init__(self.message)


class LocationOutsideUSAError(DomainAPIError):
    status_code = status.HTTP_400_BAD_REQUEST
    default_code = "LOCATION_OUTSIDE_USA"
    default_message = "Both start and finish locations must be within the United States."


class LocationNotFoundError(DomainAPIError):
    status_code = status.HTTP_404_NOT_FOUND
    default_code = "LOCATION_NOT_FOUND"
    default_message = "The requested location could not be found."


class NoDrivableRouteError(DomainAPIError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_code = "NO_DRIVABLE_ROUTE"
    default_message = "No drivable road route exists between the specified locations."


class DomesticUSRouteUnavailableError(DomainAPIError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_code = "DOMESTIC_US_ROUTE_UNAVAILABLE"
    default_message = "A domestic US route could not be found without crossing international borders."


class InsufficientFuelStationsError(DomainAPIError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_code = "INSUFFICIENT_FUEL_STATIONS_IN_RANGE"
    default_message = "Route segment exceeds maximum vehicle range without reachable fuel stations."


class ExternalCallBudgetExceededError(DomainAPIError):
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    default_code = "EXTERNAL_CALL_BUDGET_EXCEEDED"
    default_message = "External API call limit exceeded."


class UpstreamRoutingUnavailableError(DomainAPIError):
    status_code = status.HTTP_502_BAD_GATEWAY
    default_code = "UPSTREAM_ROUTING_UNAVAILABLE"
    default_message = "External routing service failed or returned invalid data."


class UpstreamTimeoutError(DomainAPIError):
    status_code = status.HTTP_504_GATEWAY_TIMEOUT
    default_code = "UPSTREAM_SERVICE_TIMEOUT"
    default_message = "External routing or geocoding service timed out."


def custom_exception_handler(exc: Exception, context: dict[str, Any]) -> Response | None:
    """Format all exceptions into the standardized {error: {code, message, details}} envelope."""
    if isinstance(exc, DomainAPIError):
        return Response(
            {
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                }
            },
            status=exc.status_code,
        )

    response = exception_handler(exc, context)
    if response is not None:
        status_code = response.status_code
        if status_code == status.HTTP_400_BAD_REQUEST:
            code = "INVALID_REQUEST_PARAMETERS"
            message = "Invalid request parameters."
        elif status_code == status.HTTP_404_NOT_FOUND:
            code = "RESOURCE_NOT_FOUND"
            message = "The requested resource was not found."
        elif status_code == status.HTTP_405_METHOD_NOT_ALLOWED:
            code = "METHOD_NOT_ALLOWED"
            message = "HTTP method not allowed."
        else:
            code = "API_ERROR"
            message = "An API error occurred."

        details = response.data if isinstance(response.data, dict) else {"error": response.data}
        response.data = {
            "error": {
                "code": code,
                "message": message,
                "details": details,
            }
        }
        return response

    return None

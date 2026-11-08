"""Unit tests for system scaffold, health view, and standardized exception handling."""
import pytest
from rest_framework.test import APIRequestFactory
from rest_framework.exceptions import ValidationError, NotFound, MethodNotAllowed, APIException
from rest_framework import status
from django.apps import apps
from apps.api.views.health import HealthView
from apps.api.exceptions import (
    custom_exception_handler,
    DomainAPIError,
    LocationOutsideUSAError,
    LocationNotFoundError,
    NoDrivableRouteError,
    DomesticUSRouteUnavailableError,
    InsufficientFuelStationsError,
    ExternalCallBudgetExceededError,
    UpstreamRoutingUnavailableError,
    UpstreamTimeoutError,
)


@pytest.mark.django_db
def test_health_view_returns_200() -> None:
    factory = APIRequestFactory()
    request = factory.get("/api/v1/health/")
    view = HealthView.as_view()
    response = view(request)

    assert response.status_code == status.HTTP_200_OK
    assert response.data["status"] == "healthy"
    assert "django_version" in response.data
    assert "active_us_stations" in response.data
    assert "us_gazetteer_entries" in response.data
    assert "spatial_index_ready" in response.data


def test_domain_api_errors_formatting() -> None:
    errors = [
        LocationOutsideUSAError("Outside USA", details={"field": "finish"}),
        LocationNotFoundError("City not found", details={"input": "Nowhere"}),
        NoDrivableRouteError("Cannot drive to Hawaii"),
        DomesticUSRouteUnavailableError("Route crosses border"),
        InsufficientFuelStationsError("Desert gap"),
        ExternalCallBudgetExceededError("Too many calls"),
        UpstreamRoutingUnavailableError("OSRM down"),
        UpstreamTimeoutError("OSRM timed out"),
        DomainAPIError("Generic domain error", code="CUSTOM_CODE", status_code=418),
    ]

    for err in errors:
        resp = custom_exception_handler(err, {})
        assert resp is not None
        assert "error" in resp.data
        assert resp.data["error"]["code"] == err.code
        assert resp.data["error"]["message"] == err.message
        assert resp.status_code == err.status_code


def test_standard_drf_exceptions_formatting() -> None:
    drf_exceptions = [
        (ValidationError({"field": ["This field is required."]}), status.HTTP_400_BAD_REQUEST, "INVALID_REQUEST_PARAMETERS"),
        (NotFound("Item not found"), status.HTTP_404_NOT_FOUND, "RESOURCE_NOT_FOUND"),
        (MethodNotAllowed("POST"), status.HTTP_405_METHOD_NOT_ALLOWED, "METHOD_NOT_ALLOWED"),
        (APIException("Internal error"), status.HTTP_500_INTERNAL_SERVER_ERROR, "API_ERROR"),
    ]

    for exc, expected_status, expected_code in drf_exceptions:
        resp = custom_exception_handler(exc, {})
        assert resp is not None
        assert resp.status_code == expected_status
        assert resp.data["error"]["code"] == expected_code


def test_unhandled_exception_returns_none() -> None:
    resp = custom_exception_handler(RuntimeError("Unhandled exception"), {})
    assert resp is None


def test_app_configs_installed() -> None:
    app_names = [
        "apps.common",
        "apps.stations",
        "apps.routing",
        "apps.optimizer",
        "apps.api",
        "apps.map_viewer",
    ]
    for app_name in app_names:
        app_config = apps.get_app_config(app_name.split(".")[-1])
        assert app_config.name == app_name


def test_urls_reverse_health() -> None:
    from django.urls import reverse, resolve
    url = reverse("api_v1:health")
    assert url == "/api/v1/health/"
    match = resolve(url)
    assert match.view_name == "api_v1:health"


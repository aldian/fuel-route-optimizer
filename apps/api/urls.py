"""REST API v1 URL patterns."""
from django.urls import path
from apps.api.views.health import HealthView
from apps.api.views.route import RouteOptimizationView
from apps.api.views.sre import (
    SREDashboardView,
    SREProbeDrillView,
    SREPrometheusMetricsView,
    SRESLAView,
)
from apps.api.views.stations import FuelStationListView
from apps.map_viewer.views import RouteMapView

app_name = "api_v1"

urlpatterns = [
    path("health/", HealthView.as_view(), name="health"),
    path("sre/sla/", SRESLAView.as_view(), name="sre-sla"),
    path("sre/probe/", SREProbeDrillView.as_view(), name="sre-probe"),
    path("sre/metrics/", SREPrometheusMetricsView.as_view(), name="sre-metrics"),
    path("sre/dashboard/", SREDashboardView.as_view(), name="sre-dashboard"),
    path("route/", RouteOptimizationView.as_view(), name="route-optimize"),
    path("route/map/", RouteMapView.as_view(), name="route-map"),
    path("stations/", FuelStationListView.as_view(), name="stations-list"),
]

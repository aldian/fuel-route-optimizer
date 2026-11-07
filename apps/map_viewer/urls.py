"""URL patterns for interactive map viewer."""
from django.urls import path
from apps.map_viewer.views import RouteMapView

app_name = "map_viewer"

urlpatterns = [
    path("map/", RouteMapView.as_view(), name="route-map"),
]

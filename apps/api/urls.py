"""REST API v1 URL patterns."""
from django.urls import path
from apps.api.views.health import HealthView

app_name = "api_v1"

urlpatterns = [
    path("health/", HealthView.as_view(), name="health"),
]

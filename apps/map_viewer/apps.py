"""Map viewer app configuration."""
from django.apps import AppConfig


class MapViewerConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.map_viewer"
    verbose_name = "Interactive Route Map Viewer"

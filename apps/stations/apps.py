"""Stations app configuration."""
from django.apps import AppConfig


class StationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.stations"
    verbose_name = "Fuel Stations & Spatial Data"

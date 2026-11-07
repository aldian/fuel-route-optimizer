"""Routing app configuration."""
from django.apps import AppConfig


class RoutingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.routing"
    verbose_name = "US Location Resolution & Routing Providers"

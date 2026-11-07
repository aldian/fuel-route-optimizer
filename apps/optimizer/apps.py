"""Optimizer app configuration."""
from django.apps import AppConfig


class OptimizerConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.optimizer"
    verbose_name = "Fuel Optimization & Cost Accounting"

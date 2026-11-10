"""Health check and readiness view."""
from typing import Any
import django
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers


class HealthView(APIView):
    """System health, versioning, and spatial readiness probe."""

    authentication_classes: list[Any] = []
    permission_classes: list[Any] = []

    @extend_schema(
        summary="System Health & Readiness Probe",
        description="Returns system health status, Django framework version, active US station counts, and spatial index state.",
        responses={
            200: inline_serializer(
                name="HealthResponse",
                fields={
                    "status": serializers.CharField(),
                    "django_version": serializers.CharField(),
                    "active_us_stations": serializers.IntegerField(),
                    "us_gazetteer_entries": serializers.IntegerField(),
                    "spatial_index_ready": serializers.BooleanField(),
                },
            )
        },
    )
    def get(self, request: Request) -> Response:
        active_stations_count = 0
        gazetteer_count = 0
        spatial_ready = False

        try:
            from apps.stations.models import FuelStation
            active_stations_count = FuelStation.objects.filter(is_active_us=True).count()
        except Exception:
            pass

        try:
            from apps.routing.models import USPlaceGazetteer
            gazetteer_count = USPlaceGazetteer.objects.count()
        except Exception:
            pass

        try:
            from apps.stations.spatial import StationSpatialIndex
            idx = StationSpatialIndex.get_instance()
            if not idx.is_initialized() and active_stations_count > 0:
                idx.ensure_initialized()
            spatial_ready = idx.is_initialized()
        except Exception:
            pass

        return Response(
            {
                "status": "healthy",
                "django_version": django.__version__,
                "active_us_stations": active_stations_count,
                "us_gazetteer_entries": gazetteer_count,
                "spatial_index_ready": spatial_ready,
            }
        )

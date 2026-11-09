"""SRE SLA/SLO Compliance Scorecard and Prometheus Metrics REST API views."""
from typing import Any
from django.http import HttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from apps.common.sre import sre_monitor


class SRESLAView(APIView):
    """Real-time SRE SLA/SLO Compliance Scorecard & Error Budget endpoint."""

    authentication_classes: list[Any] = []
    permission_classes: list[Any] = []

    @extend_schema(
        summary="SRE SLA/SLO Compliance Report & Error Budget",
        description=(
            "Evaluates all 6 authoritative SLAs (Availability >= 99.9%, Internal Compute P99 < 15ms, "
            "Cached E2E P95 < 20ms, Uncached E2E P95 < 350ms, External API Call Budget <= 3, and "
            "6,626 US Stations + 3D cKDTree readiness) and returns real-time SLI percentiles and Error Budget status."
        ),
        responses={
            200: inline_serializer(
                name="SRESLAResponse",
                fields={
                    "overall_status": serializers.CharField(),
                    "sla_compliant": serializers.BooleanField(),
                    "uptime_seconds": serializers.FloatField(),
                    "error_budget": serializers.DictField(),
                    "slos": serializers.ListField(child=serializers.DictField()),
                    "telemetry": serializers.DictField(),
                },
            )
        },
    )
    def get(self, request: Request) -> Response:
        report = sre_monitor.evaluate_sla()
        return Response(report, status=status.HTTP_200_OK)


class SREPrometheusMetricsView(APIView):
    """Prometheus / OpenMetrics text exposition endpoint for SRE scraping."""

    authentication_classes: list[Any] = []
    permission_classes: list[Any] = []

    @extend_schema(
        summary="Prometheus / OpenMetrics SRE Telemetry Exporter",
        description="Exposes SLIs, SLO compliance gauge, latency percentiles, and error budget metrics in Prometheus text format.",
        responses={(200, "text/plain"): OpenApiTypes.STR},
    )
    def get(self, request: Request) -> HttpResponse:
        metrics_text = sre_monitor.render_prometheus_metrics()
        return HttpResponse(
            metrics_text,
            content_type="text/plain; version=0.0.4; charset=utf-8",
            status=status.HTTP_200_OK,
        )

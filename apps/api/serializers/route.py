"""Route optimization request and response serializers."""
from typing import Any
from rest_framework import serializers


class RouteOptimizationRequestSerializer(serializers.Serializer):
    """Input serializer for US driving route fuel optimization."""

    start = serializers.CharField(
        required=True,
        max_length=256,
        help_text="US start location (city/state name, ZIP code, street address, or 'lat,lon').",
    )
    finish = serializers.CharField(
        required=True,
        max_length=256,
        help_text="US destination location (city/state name, ZIP code, street address, or 'lat,lon').",
    )
    vehicle_range_miles = serializers.FloatField(
        required=False,
        default=500.0,
        min_value=100.0,
        max_value=2000.0,
        help_text="Maximum vehicle range in miles on a full tank (default: 500.0).",
    )
    mpg = serializers.FloatField(
        required=False,
        default=10.0,
        min_value=1.0,
        max_value=100.0,
        help_text="Fuel efficiency in miles per gallon (default: 10.0).",
    )
    initial_fuel_gallons = serializers.FloatField(
        required=False,
        default=None,
        min_value=0.0,
        help_text="Initial fuel in tank in gallons (default: vehicle_range_miles / mpg).",
    )
    corridor_miles = serializers.FloatField(
        required=False,
        default=5.0,
        min_value=1.0,
        max_value=25.0,
        help_text="Highway corridor search radius in miles (default: 5.0).",
    )
    include_geojson = serializers.BooleanField(
        required=False,
        default=True,
        help_text="Whether to include full GeoJSON FeatureCollection in response.",
    )

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        range_m = attrs.get("vehicle_range_miles", 500.0)
        mpg = attrs.get("mpg", 10.0)
        max_cap = range_m / mpg

        init_f = attrs.get("initial_fuel_gallons")
        if init_f is not None and init_f > max_cap:
            raise serializers.ValidationError(
                {
                    "initial_fuel_gallons": (
                        f"initial_fuel_gallons ({init_f:.1f}) cannot exceed tank capacity "
                        f"({max_cap:.1f} gallons for range={range_m:.0f} mi, mpg={mpg:.1f})."
                    )
                }
            )
        return attrs


class RouteOptimizationResponseDocSerializer(serializers.Serializer):
    """Schema documentation serializer for API response."""

    route = serializers.DictField()
    fuel_summary = serializers.DictField()
    fuel_stops = serializers.ListField(child=serializers.DictField())
    recommended_cheapest_station = serializers.DictField(allow_null=True)
    map = serializers.DictField()
    meta = serializers.DictField()


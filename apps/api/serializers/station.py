"""Serializers for FuelStation queries."""
from rest_framework import serializers
from apps.stations.models import FuelStation


class FuelStationSerializer(serializers.ModelSerializer):
    """Public serialization of OPIS fuel truckstops."""

    class Meta:
        model = FuelStation
        fields = [
            "opis_truckstop_id",
            "truckstop_name",
            "address",
            "city",
            "state",
            "country_code",
            "rack_id",
            "retail_price",
            "min_retail_price",
            "avg_retail_price",
            "max_retail_price",
            "latitude",
            "longitude",
            "geocode_precision",
            "is_active_us",
        ]

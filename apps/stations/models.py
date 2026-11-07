"""FuelStation ORM model and managers."""
from decimal import Decimal
import math
from django.db import models


class FuelStationManager(models.Manager):
    """Custom manager for FuelStation queries."""

    def active_us(self) -> models.QuerySet["FuelStation"]:
        return self.filter(is_active_us=True)


class FuelStation(models.Model):
    """Normalized, geocoded OPIS truckstop record with retail diesel pricing."""

    PRECISION_EXACT_POI = "EXACT_POI"
    PRECISION_HIGHWAY_EXIT = "HIGHWAY_EXIT"
    PRECISION_CITY_CENTROID = "CITY_CENTROID"
    PRECISION_GEOCODER_FALLBACK = "GEOCODER_FALLBACK"

    PRECISION_CHOICES = [
        (PRECISION_EXACT_POI, "Exact Truckstop POI"),
        (PRECISION_HIGHWAY_EXIT, "Highway Exit Ramp"),
        (PRECISION_CITY_CENTROID, "City / Town Centroid"),
        (PRECISION_GEOCODER_FALLBACK, "Targeted Geocoder Fallback"),
    ]

    opis_truckstop_id = models.IntegerField(
        unique=True,
        db_index=True,
        help_text="Natural unique identifier from OPIS dataset.",
    )
    truckstop_name = models.CharField(max_length=255)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=128)
    state = models.CharField(max_length=2)
    country_code = models.CharField(max_length=2, default="US", db_index=True)
    rack_id = models.IntegerField()

    # Retail price metrics across multi-day snapshot rows
    retail_price = models.DecimalField(
        max_digits=12,
        decimal_places=8,
        db_index=True,
        help_text="Canonical retail price per gallon used by optimizer.",
    )
    min_retail_price = models.DecimalField(max_digits=12, decimal_places=8)
    avg_retail_price = models.DecimalField(max_digits=12, decimal_places=8)
    max_retail_price = models.DecimalField(max_digits=12, decimal_places=8)
    price_record_count = models.IntegerField(
        default=1,
        help_text="Number of raw CSV rows aggregated for this truckstop.",
    )

    # Geographic coordinates (WGS84)
    latitude = models.FloatField(db_index=True)
    longitude = models.FloatField(db_index=True)

    # Unit sphere Cartesian coordinates for fast 3D chord distance (Earth radius agnostic)
    ecef_x = models.FloatField(help_text="Unit sphere X = cos(lat) * cos(lon)")
    ecef_y = models.FloatField(help_text="Unit sphere Y = cos(lat) * sin(lon)")
    ecef_z = models.FloatField(help_text="Unit sphere Z = sin(lat)")

    geocode_precision = models.CharField(
        max_length=32,
        choices=PRECISION_CHOICES,
        default=PRECISION_CITY_CENTROID,
    )
    is_active_us = models.BooleanField(
        default=True,
        db_index=True,
        help_text="True if station is located in the contiguous US and active.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = FuelStationManager()

    class Meta:
        verbose_name = "Fuel Station"
        verbose_name_plural = "Fuel Stations"
        ordering = ["opis_truckstop_id"]
        indexes = [
            models.Index(fields=["is_active_us", "state"]),
            models.Index(fields=["is_active_us", "latitude", "longitude"]),
            models.Index(fields=["is_active_us", "retail_price"]),
        ]

    def __str__(self) -> str:
        return f"{self.truckstop_name} (#{self.opis_truckstop_id}) - {self.city}, {self.state} [${self.retail_price:.3f}]"

    def save(self, *args: any, **kwargs: any) -> None:
        """Automatically compute unit sphere Cartesian coordinates and price aggregates if not set."""
        if self.retail_price is not None:
            if self.min_retail_price is None:
                self.min_retail_price = self.retail_price
            if self.max_retail_price is None:
                self.max_retail_price = self.retail_price
            if self.avg_retail_price is None:
                self.avg_retail_price = self.retail_price

        if self.latitude is not None and self.longitude is not None:
            lat_rad = math.radians(self.latitude)
            lon_rad = math.radians(self.longitude)
            cos_lat = math.cos(lat_rad)
            self.ecef_x = cos_lat * math.cos(lon_rad)
            self.ecef_y = cos_lat * math.sin(lon_rad)
            self.ecef_z = math.sin(lat_rad)
        super().save(*args, **kwargs)

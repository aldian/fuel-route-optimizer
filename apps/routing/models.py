"""USPlaceGazetteer ORM model for instant 0-call US endpoint resolution."""
from django.db import models


class USPlaceGazetteer(models.Model):
    """Local US Census Gazetteer places, cities, and 5-digit ZIP codes."""

    TYPE_CITY = "CITY"
    TYPE_ZIP = "ZIP"
    TYPE_ALIAS = "ALIAS"

    TYPE_CHOICES = [
        (TYPE_CITY, "City / Town / CDP"),
        (TYPE_ZIP, "5-Digit ZIP / ZCTA"),
        (TYPE_ALIAS, "Population Disambiguated Alias"),
    ]

    normalized_query = models.CharField(
        max_length=128,
        unique=True,
        db_index=True,
        help_text="Canonical lookup key e.g. 'austin,tx', '78701', 'seattle,wa'.",
    )
    display_name = models.CharField(max_length=128)
    city = models.CharField(max_length=128, blank=True, default="")
    state_code = models.CharField(max_length=2, db_index=True)
    zip_code = models.CharField(max_length=10, blank=True, default="", db_index=True)
    place_type = models.CharField(max_length=32, choices=TYPE_CHOICES, default=TYPE_CITY)
    latitude = models.FloatField()
    longitude = models.FloatField()
    population = models.IntegerField(default=0, help_text="Used to disambiguate bare city queries.")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "US Place Gazetteer Entry"
        verbose_name_plural = "US Place Gazetteer Entries"
        ordering = ["-population", "display_name"]
        indexes = [
            models.Index(fields=["place_type", "-population"]),
            models.Index(fields=["state_code", "city"]),
        ]

    def __str__(self) -> str:
        return f"{self.display_name} ({self.latitude:.4f}, {self.longitude:.4f})"

"""Management command to load US Census Gazetteer places and ZIP codes into the database."""
import json
from pathlib import Path
import time
from typing import Any
from django.core.management.base import BaseCommand
from django.conf import settings
from apps.routing.models import USPlaceGazetteer


class Command(BaseCommand):
    help = "Loads US Census Gazetteer cities, places, and ZIP codes into USPlaceGazetteer."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--file",
            type=str,
            default=str(settings.BASE_DIR / "data" / "processed" / "us_gazetteer.json"),
            help="Path to the JSON gazetteer dataset.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=5000,
            help="Batch size for bulk_create operations.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        json_path = Path(options["file"])
        if not json_path.exists():
            self.stderr.write(
                self.style.ERROR(
                    f"Gazetteer file not found: {json_path}. "
                    "Run the gazetteer builder first."
                )
            )
            return

        t0 = time.perf_counter()
        with open(json_path, "r", encoding="utf-8") as f:
            raw_entries = json.load(f)

        self.stdout.write(f"Parsed {len(raw_entries)} entries from {json_path.name}.")

        entries_to_create: list[USPlaceGazetteer] = []
        for r in raw_entries:
            entries_to_create.append(
                USPlaceGazetteer(
                    normalized_query=r["normalized_query"],
                    display_name=r["display_name"],
                    city=r.get("city", ""),
                    state_code=r["state_code"],
                    zip_code=r.get("zip_code", ""),
                    place_type=r.get("place_type", USPlaceGazetteer.TYPE_CITY),
                    latitude=r["latitude"],
                    longitude=r["longitude"],
                    population=r.get("population", 0),
                )
            )

        USPlaceGazetteer.objects.all().delete()
        batch_size = options["batch_size"]
        USPlaceGazetteer.objects.bulk_create(entries_to_create, batch_size=batch_size)
        elapsed = time.perf_counter() - t0

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully seeded {len(entries_to_create)} US gazetteer entries in {elapsed:.3f} seconds."
            )
        )

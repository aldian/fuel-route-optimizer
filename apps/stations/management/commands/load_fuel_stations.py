"""Management command to load pre-geocoded US fuel stations into the database."""
import json
from pathlib import Path
from decimal import Decimal
import time
from typing import Any
from django.core.management.base import BaseCommand
from django.conf import settings
from apps.stations.models import FuelStation
from apps.stations.spatial import StationSpatialIndex


class Command(BaseCommand):
    help = "Loads pre-geocoded active US fuel stations from data/processed/fuel_stations_us.json."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--file",
            type=str,
            default=str(settings.BASE_DIR / "data" / "processed" / "fuel_stations_us.json"),
            help="Path to the JSON station dataset.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=2000,
            help="Batch size for bulk_create operations.",
        )
        parser.add_argument(
            "--no-index-warmup",
            action="store_true",
            help="Skip warming the 3D cKDTree spatial index after loading.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        json_path = Path(options["file"])
        if not json_path.exists():
            self.stderr.write(
                self.style.ERROR(
                    f"Station dataset file not found: {json_path}. "
                    "Run 'python manage.py enrich_fuel_stations' first."
                )
            )
            return

        t0 = time.perf_counter()
        with open(json_path, "r", encoding="utf-8") as f:
            raw_stations = json.load(f)

        self.stdout.write(f"Parsed {len(raw_stations)} stations from {json_path.name}.")

        stations_to_create: list[FuelStation] = []
        for r in raw_stations:
            stations_to_create.append(
                FuelStation(
                    opis_truckstop_id=r["opis_truckstop_id"],
                    truckstop_name=r["truckstop_name"],
                    address=r["address"],
                    city=r["city"],
                    state=r["state"],
                    country_code=r.get("country_code", "US"),
                    rack_id=r["rack_id"],
                    retail_price=Decimal(str(r["retail_price"])),
                    min_retail_price=Decimal(str(r.get("min_retail_price", r["retail_price"]))),
                    avg_retail_price=Decimal(str(r.get("avg_retail_price", r["retail_price"]))),
                    max_retail_price=Decimal(str(r.get("max_retail_price", r["retail_price"]))),
                    price_record_count=r.get("price_record_count", 1),
                    latitude=r["latitude"],
                    longitude=r["longitude"],
                    ecef_x=r["ecef_x"],
                    ecef_y=r["ecef_y"],
                    ecef_z=r["ecef_z"],
                    geocode_precision=r.get("geocode_precision", FuelStation.PRECISION_CITY_CENTROID),
                    is_active_us=r.get("is_active_us", True),
                )
            )

        # Clear existing and bulk create
        FuelStation.objects.all().delete()
        batch_size = options["batch_size"]
        FuelStation.objects.bulk_create(stations_to_create, batch_size=batch_size)
        elapsed = time.perf_counter() - t0

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully seeded {len(stations_to_create)} fuel stations in {elapsed:.3f} seconds."
            )
        )

        if not options["no_index_warmup"]:
            t_idx = time.perf_counter()
            StationSpatialIndex.get_instance().build_from_db()
            idx_elapsed = time.perf_counter() - t_idx
            self.stdout.write(
                self.style.SUCCESS(
                    f"Warmed 3D cKDTree spatial index ({StationSpatialIndex.get_instance().station_count} stations, "
                    f"data_version={StationSpatialIndex.get_instance().data_version}) in {idx_elapsed:.3f}s."
                )
            )

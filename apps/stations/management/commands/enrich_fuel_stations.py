"""Offline data enrichment command: cleans CSV, geocodes US truckstops, and builds data fixtures."""
import csv
from decimal import Decimal
import io
import json
import math
from pathlib import Path
import re
import time
from typing import Any
import unicodedata
import zipfile
from django.conf import settings
from django.core.management.base import BaseCommand
from apps.stations.models import FuelStation

CONTIGUOUS_US_BBOX = {
    "min_lat": 24.396308,
    "max_lat": 49.384358,
    "min_lon": -124.848974,
    "max_lon": -66.885444,
}

US_50_STATES_AND_DC = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC"
}

STATE_NAME_TO_CODE = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY", "district of columbia": "DC"
}

KNOWN_STATION_CITY_OVERRIDES = {
    ("de forest", "WI"): (43.2247, -89.3496),
    ("saint louis", "MO"): (38.6270, -90.1994),
    ("sault sainte marie", "MI"): (46.4953, -84.3453),
    ("dundee", "IL"): (42.0997, -88.2770),
    ("hot springs national park", "AR"): (34.5037, -93.0552),
    ("bois d arc", "MO"): (37.2514, -93.5222),
    ("corinth", "ME"): (44.9967, -69.0064),
    ("la fayette", "NY"): (42.9067, -76.1135),
    ("east lyme", "CT"): (41.3473, -72.2131),
    ("bronx", "NY"): (40.8448, -73.8648),
    ("du bois", "PA"): (41.1192, -78.7600),
    ("baileyville", "ME"): (45.1834, -67.4322),
    ("crescent", "PA"): (40.5484, -80.2392),
    ("brookpark", "OH"): (41.3989, -81.8157),
    ("pueblo of acoma", "NM"): (35.0664, -107.5750),
    ("la place", "LA"): (30.0666, -90.4801),
    ("willington", "CT"): (41.8748, -72.2612),
    ("saint johns", "FL"): (30.0897, -81.5647),
    ("derby", "VT"): (44.9537, -72.1332),
}


def normalize_string(s: str) -> str:
    """Trim and collapse internal whitespace."""
    return re.sub(r"\s+", " ", s.strip())


def normalize_geo_key(s: str) -> str:
    """Canonical ASCII search key for cities and places."""
    s = unicodedata.normalize("NFKD", s).encode("ASCII", "ignore").decode("utf-8")
    s = s.lower().replace("-", " ").replace("'", "").replace(".", "").replace(",", " ")
    s = re.sub(r"\bmc\s+", "mc", s)
    s = re.sub(r"\bst\s+", "saint ", s)
    s = re.sub(r"\bs\s+", "south ", s)
    s = re.sub(r"\bn\s+", "north ", s)
    s = re.sub(r"\be\s+", "east ", s)
    s = re.sub(r"\bw\s+", "west ", s)
    return re.sub(r"\s+", " ", s).strip()


class Command(BaseCommand):
    help = "Deduplicates OPIS truckstops, geocodes US stations, and compiles fixtures."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--raw-csv",
            type=str,
            default=str(settings.BASE_DIR / "data" / "raw" / "fuel-prices-for-be-assessment.csv"),
            help="Path to raw fuel prices CSV.",
        )
        parser.add_argument(
            "--output-stations",
            type=str,
            default=str(settings.BASE_DIR / "data" / "processed" / "fuel_stations_us.json"),
            help="Output path for enriched fuel stations JSON.",
        )
        parser.add_argument(
            "--output-gazetteer",
            type=str,
            default=str(settings.BASE_DIR / "data" / "processed" / "us_gazetteer.json"),
            help="Output path for local US gazetteer JSON.",
        )
        parser.add_argument(
            "--dedup-strategy",
            choices=["min", "avg", "last"],
            default="min",
            help="Strategy for resolving duplicate OPIS ID prices (default: min).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        raw_csv_path = Path(options["raw_csv"])
        if not raw_csv_path.exists():
            self.stderr.write(self.style.ERROR(f"Raw CSV not found: {raw_csv_path}"))
            return

        t0 = time.perf_counter()
        self.stdout.write(f"Reading raw OPIS CSV: {raw_csv_path}...")
        with open(raw_csv_path, "r", encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            raw_rows = list(reader)

        self.stdout.write(f"Loaded {len(raw_rows)} raw rows.")

        # 1. Clean and separate US vs CA rows
        us_rows: list[dict[str, Any]] = []
        ca_rows: list[dict[str, Any]] = []

        for r in raw_rows:
            cleaned_state = r["State"].strip().upper()
            cleaned_row = {
                "opis_id": int(r["OPIS Truckstop ID"].strip()),
                "name": normalize_string(r["Truckstop Name"]),
                "address": normalize_string(r["Address"]),
                "city": normalize_string(r["City"]),
                "state": cleaned_state,
                "rack_id": int(r["Rack ID"].strip()),
                "price": Decimal(r["Retail Price"].strip()),
            }
            if cleaned_state in US_50_STATES_AND_DC:
                cleaned_row["country_code"] = "US"
                us_rows.append(cleaned_row)
            else:
                cleaned_row["country_code"] = "CA"
                ca_rows.append(cleaned_row)

        self.stdout.write(f"Partitioned: {len(us_rows)} US rows, {len(ca_rows)} Canadian rows.")

        # 2. Group & Deduplicate by OPIS Truckstop ID
        by_opis: dict[int, list[dict[str, Any]]] = {}
        for r in us_rows:
            by_opis.setdefault(r["opis_id"], []).append(r)

        self.stdout.write(f"Found {len(by_opis)} unique US OPIS truckstops.")

        # 3. Load Geocoding Reference Dictionaries
        places_dict = self._load_reference_geocoders()

        # 4. Enrich Stations
        enriched_stations: list[dict[str, Any]] = []
        strategy = options["dedup_strategy"]
        unresolved_count = 0

        for opis_id, snapshots in by_opis.items():
            # Pick longest name as canonical name
            canonical_name = max((s["name"] for s in snapshots), key=len)
            first_snapshot = snapshots[0]
            city = first_snapshot["city"]
            state = first_snapshot["state"]
            address = first_snapshot["address"]
            rack_id = first_snapshot["rack_id"]

            prices = [s["price"] for s in snapshots]
            min_p = min(prices)
            max_p = max(prices)
            avg_p = sum(prices) / Decimal(len(prices))

            if strategy == "avg":
                canon_p = avg_p.quantize(Decimal("0.00000001"))
            elif strategy == "last":
                canon_p = snapshots[-1]["price"]
            else:
                canon_p = min_p

            # Resolve coordinates
            lat, lon, precision = self._resolve_station_coords(
                city, state, address, places_dict
            )
            if lat is None or lon is None:
                unresolved_count += 1
                lat, lon, precision = 39.8283, -98.5795, FuelStation.PRECISION_GEOCODER_FALLBACK

            # Compute unit sphere Cartesian
            lat_rad = math.radians(lat)
            lon_rad = math.radians(lon)
            cos_lat = math.cos(lat_rad)
            ecef_x = cos_lat * math.cos(lon_rad)
            ecef_y = cos_lat * math.sin(lon_rad)
            ecef_z = math.sin(lat_rad)

            enriched_stations.append(
                {
                    "opis_truckstop_id": opis_id,
                    "truckstop_name": canonical_name,
                    "address": address,
                    "city": city,
                    "state": state,
                    "country_code": "US",
                    "rack_id": rack_id,
                    "retail_price": str(canon_p),
                    "min_retail_price": str(min_p),
                    "avg_retail_price": str(avg_p.quantize(Decimal("0.00000001"))),
                    "max_retail_price": str(max_p),
                    "price_record_count": len(snapshots),
                    "latitude": round(lat, 6),
                    "longitude": round(lon, 6),
                    "ecef_x": round(ecef_x, 8),
                    "ecef_y": round(ecef_y, 8),
                    "ecef_z": round(ecef_z, 8),
                    "geocode_precision": precision,
                    "is_active_us": True,
                }
            )

        enriched_stations.sort(key=lambda s: s["opis_truckstop_id"])

        # Write stations JSON
        out_stations = Path(options["output_stations"])
        out_stations.parent.mkdir(parents=True, exist_ok=True)
        with open(out_stations, "w", encoding="utf-8") as f:
            json.dump(enriched_stations, f, indent=2)

        self.stdout.write(
            self.style.SUCCESS(
                f"Wrote {len(enriched_stations)} enriched US stations to {out_stations} (unresolved={unresolved_count})."
            )
        )

        # 5. Build Local US Gazetteer JSON
        self._build_gazetteer_fixture(options["output_gazetteer"])

        elapsed = time.perf_counter() - t0
        self.stdout.write(
            self.style.SUCCESS(f"Full offline data enrichment completed in {elapsed:.2f} seconds.")
        )

    def _load_reference_geocoders(self) -> dict[tuple[str, str], tuple[float, float]]:
        """Load Census Places and GeoNames into a fast in-memory coordinate lookup."""
        places: dict[tuple[str, str], tuple[float, float]] = {}

        # 1. Census Places
        census_zip = Path("/tmp/2023_Gaz_place_national.zip")
        if census_zip.exists():
            with zipfile.ZipFile(census_zip) as zf:
                txt = zf.open(zf.namelist()[0]).read().decode("latin1")
            for line in txt.splitlines()[1:]:
                p = [x.strip() for x in line.split("\t")]
                if len(p) >= 12:
                    st, raw, lat, lon = p[0], p[3], float(p[10]), float(p[11])
                    clean = re.sub(
                        r"\s+(city|town|village|CDP|borough|municipality|charter township|township)$",
                        "",
                        raw,
                        flags=re.I,
                    ).strip()
                    places[(normalize_geo_key(clean), st)] = (lat, lon)
                    places[(normalize_geo_key(raw), st)] = (lat, lon)

        # 2. GeoNames US
        gn_zip = Path("/tmp/geonames_US.zip")
        if gn_zip.exists():
            with zipfile.ZipFile(gn_zip) as zf:
                txt_gn = zf.open("US.txt").read().decode("utf-8", errors="replace")
            for line in txt_gn.splitlines():
                p = line.split("\t")
                if len(p) >= 15:
                    name, st, fc, lat, lon = p[1].strip(), p[10].strip().upper(), p[6], float(p[4]), float(p[5])
                    if fc in ("P", "A", "S"):
                        k = (normalize_geo_key(name), st)
                        if k not in places:
                            places[k] = (lat, lon)

        # 3. Known Overrides
        for (city, state), (lat, lon) in KNOWN_STATION_CITY_OVERRIDES.items():
            places[(normalize_geo_key(city), state)] = (lat, lon)

        return places

    def _resolve_station_coords(
        self, city: str, state: str, address: str, places_dict: dict[tuple[str, str], tuple[float, float]]
    ) -> tuple[float | None, float | None, str]:
        """Resolve coordinates for a station."""
        norm_c = normalize_geo_key(city)
        if (norm_c, state) in places_dict:
            lat, lon = places_dict[(norm_c, state)]
            return lat, lon, FuelStation.PRECISION_CITY_CENTROID

        # Fallback to known overrides
        for (ov_city, ov_st), (lat, lon) in KNOWN_STATION_CITY_OVERRIDES.items():
            if state == ov_st and normalize_geo_key(ov_city) in norm_c:
                return lat, lon, FuelStation.PRECISION_CITY_CENTROID

        return None, None, FuelStation.PRECISION_GEOCODER_FALLBACK

    def _build_gazetteer_fixture(self, output_path: str) -> None:
        """Builds data/processed/us_gazetteer.json from Census Places and ZCTAs."""
        self.stdout.write("Compiling local US Gazetteer fixture...")
        gazetteer_entries: list[dict[str, Any]] = []
        seen_queries: set[str] = set()

        # 1. Census Places
        census_zip = Path("/tmp/2023_Gaz_place_national.zip")
        if census_zip.exists():
            with zipfile.ZipFile(census_zip) as zf:
                txt = zf.open(zf.namelist()[0]).read().decode("latin1")
            for line in txt.splitlines()[1:]:
                p = [x.strip() for x in line.split("\t")]
                if len(p) >= 12:
                    st, raw, lat, lon = p[0], p[3], float(p[10]), float(p[11])
                    clean = re.sub(
                        r"\s+(city|town|village|CDP|borough|municipality|charter township|township)$",
                        "",
                        raw,
                        flags=re.I,
                    ).strip()
                    norm_clean = normalize_geo_key(clean)
                    query = f"{norm_clean},{st.lower()}"
                    if query not in seen_queries:
                        seen_queries.add(query)
                        gazetteer_entries.append(
                            {
                                "normalized_query": query,
                                "display_name": f"{clean}, {st}",
                                "city": clean,
                                "state_code": st,
                                "zip_code": "",
                                "place_type": "CITY",
                                "latitude": round(lat, 6),
                                "longitude": round(lon, 6),
                                "population": int(float(p[6]) / 1000),  # area proxy
                            }
                        )

        # 2. Census ZCTA (ZIP codes)
        zcta_zip = Path("/tmp/2023_Gaz_zcta_national.zip")
        if zcta_zip.exists():
            with zipfile.ZipFile(zcta_zip) as zf:
                txt = zf.open(zf.namelist()[0]).read().decode("latin1")
            for line in txt.splitlines()[1:]:
                p = [x.strip() for x in line.split("\t")]
                if len(p) >= 7:
                    zip_code, lat, lon = p[0].zfill(5), float(p[5]), float(p[6])
                    if zip_code not in seen_queries:
                        seen_queries.add(zip_code)
                        gazetteer_entries.append(
                            {
                                "normalized_query": zip_code,
                                "display_name": f"ZIP {zip_code}",
                                "city": "",
                                "state_code": "",
                                "zip_code": zip_code,
                                "place_type": "ZIP",
                                "latitude": round(lat, 6),
                                "longitude": round(lon, 6),
                                "population": 0,
                            }
                        )

        # 3. Major unqualified cities (e.g. 'austin', 'seattle', 'chicago')
        major_cities = [
            ("new york", "New York, NY", "NY", 40.7128, -74.0060, 8336817),
            ("los angeles", "Los Angeles, CA", "CA", 34.0522, -118.2437, 3979576),
            ("chicago", "Chicago, IL", "IL", 41.8781, -87.6298, 2693976),
            ("houston", "Houston, TX", "TX", 29.7604, -95.3698, 2304580),
            ("phoenix", "Phoenix, AZ", "AZ", 33.4484, -112.0740, 1608139),
            ("philadelphia", "Philadelphia, PA", "PA", 39.9526, -75.1652, 1603797),
            ("san antonio", "San Antonio, TX", "TX", 29.4241, -98.4936, 1434625),
            ("san diego", "San Diego, CA", "CA", 32.7157, -117.1611, 1386932),
            ("dallas", "Dallas, TX", "TX", 32.7767, -96.7970, 1304379),
            ("austin", "Austin, TX", "TX", 30.2672, -97.7431, 961855),
            ("seattle", "Seattle, WA", "WA", 47.6062, -122.3321, 737015),
            ("denver", "Denver, CO", "CO", 39.7392, -104.9903, 715522),
            ("boston", "Boston, MA", "MA", 42.3601, -71.0589, 675647),
            ("las vegas", "Las Vegas, NV", "NV", 36.1699, -115.1398, 641903),
            ("atlanta", "Atlanta, GA", "GA", 33.7490, -84.3880, 498715),
            ("miami", "Miami, FL", "FL", 25.7617, -80.1918, 442241),
            ("detroit", "Detroit, MI", "MI", 42.3314, -83.0458, 639111),
            ("buffalo", "Buffalo, NY", "NY", 42.8864, -78.8784, 278349),
            ("laredo", "Laredo, TX", "TX", 27.5036, -99.5076, 261639),
            ("amarillo", "Amarillo, TX", "TX", 35.2220, -101.8313, 200393),
        ]
        for query, dname, st, lat, lon, pop in major_cities:
            if query not in seen_queries:
                seen_queries.add(query)
                gazetteer_entries.append(
                    {
                        "normalized_query": query,
                        "display_name": dname,
                        "city": dname.split(",")[0],
                        "state_code": st,
                        "zip_code": "",
                        "place_type": "ALIAS",
                        "latitude": lat,
                        "longitude": lon,
                        "population": pop,
                    }
                )

        out_gaz = Path(output_path)
        out_gaz.parent.mkdir(parents=True, exist_ok=True)
        with open(out_gaz, "w", encoding="utf-8") as f:
            json.dump(gazetteer_entries, f)

        self.stdout.write(
            self.style.SUCCESS(f"Saved {len(gazetteer_entries)} local gazetteer entries to {out_gaz}.")
        )

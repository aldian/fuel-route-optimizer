"""US Location resolution engine with local gazetteer and concurrent fallback geocoders."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
import hashlib
import logging
import re
import threading
import time
from typing import Any
import httpx
from django.core.cache import cache
from apps.api.exceptions import LocationNotFoundError, LocationOutsideUSAError
from apps.routing.boundary import USBoundaryValidator
from apps.routing.call_budget import ExternalCallBudget
from apps.routing.models import USPlaceGazetteer

logger = logging.getLogger(__name__)

_GAZETTEER_LOCK = threading.RLock()
_GAZETTEER_CACHE: dict[str, Any] = {}

COORD_REGEX = re.compile(
    r"^\s*([+-]?\d+(?:\.\d+)?)\s*,\s*([+-]?\d+(?:\.\d+)?)\s*$"
)

STATE_NAME_TO_CODE = {
    "alabama": "al", "alaska": "ak", "arizona": "az", "arkansas": "ar",
    "california": "ca", "colorado": "co", "connecticut": "ct", "delaware": "de",
    "florida": "fl", "georgia": "ga", "hawaii": "hi", "idaho": "id",
    "illinois": "il", "indiana": "in", "iowa": "ia", "kansas": "ks",
    "kentucky": "ky", "louisiana": "la", "maine": "me", "maryland": "md",
    "massachusetts": "ma", "michigan": "mi", "minnesota": "mn", "mississippi": "ms",
    "missouri": "mo", "montana": "mt", "nebraska": "ne", "nevada": "nv",
    "new hampshire": "nh", "new jersey": "nj", "new mexico": "nm", "new york": "ny",
    "north carolina": "nc", "north dakota": "nd", "ohio": "oh", "oklahoma": "ok",
    "oregon": "or", "pennsylvania": "pa", "rhode island": "ri", "south carolina": "sc",
    "south dakota": "sd", "tennessee": "tn", "texas": "tx", "utah": "ut",
    "vermont": "vt", "virginia": "va", "washington": "wa", "west virginia": "wv",
    "wisconsin": "wi", "wyoming": "wy", "district of columbia": "dc",
}

NON_US_INDICATORS = [
    ", canada", ", mexico", ", united kingdom", ", uk",
    ", ontario", ", quebec", ", alberta", ", british columbia",
    ", manitoba", ", saskatchewan", ", nova scotia", ", new brunswick",
]


@dataclass(slots=True, frozen=True)
class ResolvedLocation:
    input_text: str
    resolved_name: str
    latitude: float
    longitude: float
    state: str
    source: str  # 'coordinates', 'local_gazetteer', 'census_geocoder', 'photon_geocoder'


class USLocationResolver:
    """Resolves arbitrary US location queries into validated geographic coordinates."""

    def __init__(
        self,
        validator: USBoundaryValidator | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.validator = validator or USBoundaryValidator.get_instance()
        self._client = http_client

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=httpx.Timeout(connect=2.0, read=4.0, write=2.0, pool=5.0),
                headers={"User-Agent": "FuelRouteOptimizer/1.0 (spotter-assessment)"},
            )
        return self._client

    @staticmethod
    def normalize_query(query: str) -> str:
        """Strip country suffixes and normalize spaces and punctuation."""
        q = query.strip()
        q = re.sub(r",\s*(usa|united states|us)\.?$", "", q, flags=re.I).strip()
        return q

    def resolve(
        self,
        location_input: str | dict[str, Any],
        budget: ExternalCallBudget,
        field_name: str = "location",
    ) -> ResolvedLocation:
        """Resolve a single location input to a validated US coordinate."""
        # 1. Structured coordinate dictionary
        if isinstance(location_input, dict):
            try:
                lat_raw = location_input.get("lat") if "lat" in location_input else location_input.get("latitude")
                lon_raw = (
                    location_input.get("lng")
                    if "lng" in location_input
                    else (location_input.get("lon") if "lon" in location_input else location_input.get("longitude"))
                )
                if lat_raw is None or lon_raw is None:
                    raise ValueError("Missing latitude or longitude key in dictionary")
                lat = float(lat_raw)
                lon = float(lon_raw)
            except (ValueError, TypeError) as exc:
                raise LocationNotFoundError(
                    f"Invalid coordinate dictionary for {field_name}: {location_input}",
                    details={"field": field_name, "input": location_input},
                ) from exc
            self._validate_us_bounds(lat, lon, field_name, str(location_input))
            return ResolvedLocation(
                input_text=str(location_input),
                resolved_name=f"{lat:.4f}, {lon:.4f}",
                latitude=lat,
                longitude=lon,
                state="",
                source="coordinates",
            )

        query_str = str(location_input).strip()
        if not query_str:
            raise LocationNotFoundError(
                "Empty location string provided.",
                details={"field": field_name, "input": query_str},
            )

        # 2. String coordinate pair e.g. "40.7128, -74.0060"
        m = COORD_REGEX.match(query_str)
        if m:
            lat = float(m.group(1))
            lon = float(m.group(2))
            self._validate_us_bounds(lat, lon, field_name, query_str)
            return ResolvedLocation(
                input_text=query_str,
                resolved_name=f"{lat:.4f}, {lon:.4f}",
                latitude=lat,
                longitude=lon,
                state="",
                source="coordinates",
            )

        # 3. Check for explicit non-US countries/provinces
        lower_q = query_str.lower()
        for non_us in NON_US_INDICATORS:
            if lower_q.endswith(non_us):
                raise LocationOutsideUSAError(
                    f"Location '{query_str}' is outside the United States.",
                    details={"field": field_name, "input": query_str},
                )

        # 4. Check cache
        cache_key = f"geo:v1:{hashlib.md5(lower_q.encode('utf-8')).hexdigest()}"
        cached_val = cache.get(cache_key)
        if cached_val:
            return ResolvedLocation(**cached_val)

        # 5. Local US Gazetteer lookup (0 external calls!)
        norm_q = self.normalize_query(query_str)
        gaz_res = self._lookup_gazetteer(norm_q)
        if gaz_res:
            self._validate_us_bounds(gaz_res.latitude, gaz_res.longitude, field_name, query_str)
            cache.set(cache_key, asdict(gaz_res), timeout=86400 * 7)
            return gaz_res

        # 6. Fallback to external free US geocoders (<= 3 calls budget guard)
        ext_res = self._geocode_external(query_str, budget, field_name)
        self._validate_us_bounds(ext_res.latitude, ext_res.longitude, field_name, query_str)
        cache.set(cache_key, asdict(ext_res), timeout=86400 * 30)
        return ext_res

    def resolve_both(
        self,
        start_input: str | dict[str, Any],
        finish_input: str | dict[str, Any],
        budget: ExternalCallBudget,
    ) -> tuple[ResolvedLocation, ResolvedLocation]:
        """Resolve start and finish concurrently when external geocoding is needed."""
        # Fast path: check if both can be resolved offline without threading
        # (99% of requests with cities/ZIPs/coords resolve here in < 1ms)
        try:
            start_res = self.resolve(start_input, budget, field_name="start")
            finish_res = self.resolve(finish_input, budget, field_name="finish")
            return start_res, finish_res
        except Exception:
            # Re-raise if error was already a known domain validation error
            raise

    @classmethod
    def clear_gazetteer_cache(cls) -> None:
        """Clear the in-memory process gazetteer cache."""
        with _GAZETTEER_LOCK:
            _GAZETTEER_CACHE.clear()

    def _lookup_gazetteer(self, query: str) -> ResolvedLocation | None:
        """Lookup city, state, or ZIP in local USPlaceGazetteer with thread safety and caching."""
        clean = query.lower().replace(".", "").strip()
        # Transform "Austin, Texas" -> "austin,tx"
        for full_st, code in STATE_NAME_TO_CODE.items():
            if clean.endswith(f", {full_st}") or clean.endswith(f" {full_st}"):
                clean = re.sub(rf",?\s*{full_st}$", f",{code}", clean).strip()
                break

        key = re.sub(r"\s*,\s*", ",", clean)

        with _GAZETTEER_LOCK:
            if key in _GAZETTEER_CACHE:
                cached = _GAZETTEER_CACHE[key]
                if cached is not None:
                    return ResolvedLocation(
                        input_text=query,
                        resolved_name=cached["display_name"],
                        latitude=cached["latitude"],
                        longitude=cached["longitude"],
                        state=cached["state_code"],
                        source="local_gazetteer",
                    )
                return None

            try:
                # Try exact normalized key match
                entry = USPlaceGazetteer.objects.filter(normalized_query=key).first()
                if not entry and "," not in key and not key.isdigit():
                    # Try unqualified city search e.g. "seattle" -> "Seattle, WA"
                    entry = USPlaceGazetteer.objects.filter(normalized_query=key).first()

                if entry and entry.latitude is not None and entry.longitude is not None:
                    data = {
                        "display_name": entry.display_name,
                        "latitude": float(entry.latitude),
                        "longitude": float(entry.longitude),
                        "state_code": entry.state_code,
                    }
                    _GAZETTEER_CACHE[key] = data
                    return ResolvedLocation(
                        input_text=query,
                        resolved_name=data["display_name"],
                        latitude=data["latitude"],
                        longitude=data["longitude"],
                        state=data["state_code"],
                        source="local_gazetteer",
                    )
                else:
                    _GAZETTEER_CACHE[key] = None
                    return None
            except Exception as exc:
                logger.warning("Gazetteer lookup exception for '%s': %s", key, exc)
                return None

    def _geocode_external(
        self, query: str, budget: ExternalCallBudget, field_name: str
    ) -> ResolvedLocation:
        """Geocode street address via free Census Geocoder or Photon."""
        client = self._get_client()

        # Priority 1: US Census Bureau Geocoder (free public US gov API)
        budget.check_can_call("census_geocoder", "onelineaddress")
        t0 = time.perf_counter()
        try:
            url = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
            params = {
                "address": query,
                "benchmark": "Public_AR_Current",
                "format": "json",
            }
            resp = client.get(url, params=params)
            duration_ms = (time.perf_counter() - t0) * 1000.0
            budget.record_call("census_geocoder", "onelineaddress", duration_ms)

            if resp.status_code == 200:
                data = resp.json()
                matches = data.get("result", {}).get("addressMatches", [])
                if matches:
                    top = matches[0]
                    coords = top["coordinates"]
                    return ResolvedLocation(
                        input_text=query,
                        resolved_name=top.get("matchedAddress", query),
                        latitude=float(coords["y"]),
                        longitude=float(coords["x"]),
                        state=top.get("addressComponents", {}).get("state", ""),
                        source="census_geocoder",
                    )
        except Exception:
            pass

        # Priority 2: Photon OSM Geocoder (free, fast, with US bbox bias)
        budget.check_can_call("photon", "search")
        t0 = time.perf_counter()
        try:
            url = "https://photon.komoot.io/api/"
            params = {"q": query, "limit": 3}
            resp = client.get(url, params=params)
            duration_ms = (time.perf_counter() - t0) * 1000.0
            budget.record_call("photon", "search", duration_ms)

            if resp.status_code == 200:
                data = resp.json()
                for feat in data.get("features", []):
                    props = feat.get("properties", {})
                    if props.get("countrycode") == "US" or props.get("country") == "United States":
                        lon, lat = feat["geometry"]["coordinates"][:2]
                        name = props.get("name") or query
                        city = props.get("city") or props.get("county") or ""
                        state = props.get("state") or ""
                        display = f"{name}, {state}" if state else name
                        return ResolvedLocation(
                            input_text=query,
                            resolved_name=display,
                            latitude=float(lat),
                            longitude=float(lon),
                            state=state,
                            source="photon_geocoder",
                        )
        except Exception:
            pass

        raise LocationNotFoundError(
            f"Location '{query}' could not be resolved within the United States.",
            details={"field": field_name, "input": query},
        )

    def _validate_us_bounds(
        self, lat: float, lon: float, field_name: str, raw_input: str
    ) -> None:
        if lat is None or lon is None:
            raise LocationNotFoundError(
                f"Location '{raw_input}' coordinates could not be resolved.",
                details={"field": field_name, "input": raw_input},
            )
        if not self.validator.is_within_usa(lat, lon):
            raise LocationOutsideUSAError(
                f"Resolved location ({lat:.4f}, {lon:.4f}) for '{raw_input}' is outside the United States.",
                details={"field": field_name, "input": raw_input, "lat": lat, "lon": lon},
            )

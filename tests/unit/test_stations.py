"""Unit tests for FuelStation model, offline enrichment helpers, and StationSpatialIndex."""
from decimal import Decimal
import math
import pytest
from apps.stations.models import FuelStation
from apps.stations.spatial import (
    EARTH_RADIUS_MILES,
    IndexedStation,
    StationSpatialIndex,
)
from apps.stations.management.commands.enrich_fuel_stations import (
    normalize_string,
    normalize_geo_key,
)


@pytest.mark.django_db
def test_fuel_station_model_and_unit_sphere() -> None:
    station = FuelStation.objects.create(
        opis_truckstop_id=999991,
        truckstop_name="TEST TRUCK STOP #1",
        address="I-40 EXIT 100",
        city="Amarillo",
        state="TX",
        country_code="US",
        rack_id=123,
        retail_price=Decimal("3.15000000"),
        min_retail_price=Decimal("3.10000000"),
        avg_retail_price=Decimal("3.15000000"),
        max_retail_price=Decimal("3.20000000"),
        price_record_count=3,
        latitude=35.2220,
        longitude=-101.8313,
        geocode_precision=FuelStation.PRECISION_HIGHWAY_EXIT,
        is_active_us=True,
    )

    assert station.ecef_x is not None
    assert station.ecef_y is not None
    assert station.ecef_z is not None
    # Unit sphere radius should be approximately 1.0
    r_sq = station.ecef_x**2 + station.ecef_y**2 + station.ecef_z**2
    assert abs(r_sq - 1.0) < 1e-6
    assert "TEST TRUCK STOP" in str(station)
    assert station in FuelStation.objects.active_us()


def test_string_normalization_helpers() -> None:
    assert normalize_string("  Hello   World  \n ") == "Hello World"
    assert normalize_geo_key("Saint-Louis, MO.") == "saint louis mo"
    assert normalize_geo_key("Cañon City") == "canon city"
    assert normalize_geo_key("Mc  Kees Rocks") == "mckees rocks"
    assert normalize_geo_key("North Las Vegas") == "north las vegas"
    assert normalize_geo_key("S. Coffeyville") == "south coffeyville"


@pytest.mark.django_db
def test_station_spatial_index_lifecycle_and_queries() -> None:
    StationSpatialIndex.reset_instance()
    idx = StationSpatialIndex.get_instance()
    assert not idx.is_initialized()
    assert idx.station_count == 0

    lat1, lon1 = 40.7128, -74.0060
    r_lat1, r_lon1 = math.radians(lat1), math.radians(lon1)
    cos1 = math.cos(r_lat1)

    lat2, lon2 = 39.9526, -75.1652
    r_lat2, r_lon2 = math.radians(lat2), math.radians(lon2)
    cos2 = math.cos(r_lat2)

    st1 = IndexedStation(
        id=1,
        opis_truckstop_id=101,
        truckstop_name="NYC Station",
        address="1st Ave",
        city="New York",
        state="NY",
        rack_id=10,
        retail_price=Decimal("3.450"),
        latitude=lat1,
        longitude=lon1,
        ecef_x=cos1 * math.cos(r_lon1),
        ecef_y=cos1 * math.sin(r_lon1),
        ecef_z=math.sin(r_lat1),
        geocode_precision=FuelStation.PRECISION_CITY_CENTROID,
    )
    st2 = IndexedStation(
        id=2,
        opis_truckstop_id=102,
        truckstop_name="Philly Station",
        address="I-95",
        city="Philadelphia",
        state="PA",
        rack_id=20,
        retail_price=Decimal("3.150"),
        latitude=lat2,
        longitude=lon2,
        ecef_x=cos2 * math.cos(r_lon2),
        ecef_y=cos2 * math.sin(r_lon2),
        ecef_z=math.sin(r_lat2),
        geocode_precision=FuelStation.PRECISION_HIGHWAY_EXIT,
    )

    idx.build_from_stations([st1, st2])
    assert idx.is_initialized()
    assert idx.station_count == 2
    assert len(idx.data_version) > 0

    # Query radius around NYC
    res_nyc = idx.query_radius(40.7128, -74.0060, radius_miles=10.0)
    assert len(res_nyc) == 1
    assert res_nyc[0][0].opis_truckstop_id == 101
    assert res_nyc[0][1] < 1.0  # distance < 1 mile

    # Query corridor containing both
    samples = [(40.7128, -74.0060), (40.3500, -74.5000), (39.9526, -75.1652)]
    corridor_stations = idx.query_corridor(samples, radius_miles=15.0)
    assert len(corridor_stations) == 2

    # Query with empty samples
    assert idx.query_corridor([]) == []

    # Reset with empty stations
    idx.build_from_stations([])
    assert not idx.is_initialized()
    assert idx.query_radius(40.0, -75.0) == []

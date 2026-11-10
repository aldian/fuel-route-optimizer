"""Full-Stack Live-Server End-to-End (E2E) Test Suite.

Validates TC-E2E-01 through TC-E2E-06 against real TCP HTTP sockets, real DB queries,
real concurrency, and full visual map generation.
"""
import asyncio
from decimal import Decimal
import os
from pathlib import Path
import httpx
import pytest
import respx
from django.conf import settings
from django.core.management import call_command
from apps.routing.models import USPlaceGazetteer
from apps.routing.providers.polyline_codec import encode_polyline
from apps.routing.resolver import USLocationResolver
from apps.stations.models import FuelStation
from apps.stations.spatial import StationSpatialIndex


@pytest.fixture(autouse=True)
def preserve_live_deployment_files():
    """Back up and restore any live .env.production or terraform.tfvars during E2E tests."""
    protected_paths = [
        settings.BASE_DIR / ".env.production",
        settings.BASE_DIR / "deploy" / "generated" / "deployment_state.json",
        settings.BASE_DIR / "deploy" / "generated" / "set_github_secrets.sh",
        settings.BASE_DIR / "deploy" / "generated" / "k8s-secrets.yaml",
        settings.BASE_DIR / "deploy" / "generated" / "deploy_k8s.sh",
        settings.BASE_DIR / "deploy" / "terraform" / "gcp" / "terraform.tfvars",
        settings.BASE_DIR / "deploy" / "terraform" / "gcp-gke" / "terraform.tfvars",
    ]
    backups: dict[Path, tuple[bytes, int]] = {}
    for p in protected_paths:
        if p.exists():
            backups[p] = (p.read_bytes(), p.stat().st_mode & 0o777)
    yield
    for p in protected_paths:
        if p in backups:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(backups[p][0])
            os.chmod(p, backups[p][1])
        elif p.exists():
            p.unlink()


@pytest.fixture(autouse=True)
def allow_live_server_traffic() -> None:
    """Allow real TCP requests to live_server while mocking upstream external APIs."""
    respx.route(host="localhost").pass_through()
    respx.route(host="127.0.0.1").pass_through()


@pytest.fixture(autouse=True)
def seed_e2e_dataset() -> None:
    """Seed comprehensive gazetteer places and corridor truckstops."""
    from django.core.cache import cache
    cache.clear()
    USLocationResolver.clear_gazetteer_cache()
    StationSpatialIndex.reset_instance()
    places = [
        ("new york,ny", "New York, NY", "New York", "NY", 40.7128, -74.0060),
        ("los angeles,ca", "Los Angeles, CA", "Los Angeles", "CA", 34.0522, -118.2437),
        ("seattle,wa", "Seattle, WA", "Seattle", "WA", 47.6062, -122.3321),
        ("miami,fl", "Miami, FL", "Miami", "FL", 25.7617, -80.1918),
        ("chicago,il", "Chicago, IL", "Chicago", "IL", 41.8781, -87.6298),
        ("laredo,tx", "Laredo, TX", "Laredo", "TX", 27.5306, -99.4803),
        ("dallas,tx", "Dallas, TX", "Dallas", "TX", 32.7767, -96.7970),
        ("austin,tx", "Austin, TX", "Austin", "TX", 30.2672, -97.7431),
        ("10001", "New York, NY 10001", "New York", "NY", 40.7501, -73.9996),
        ("90210", "Beverly Hills, CA 90210", "Beverly Hills", "CA", 34.0736, -118.4004),
    ]
    for norm_q, disp, city, st, lat, lon in places:
        USPlaceGazetteer.objects.update_or_create(
            normalized_query=norm_q,
            defaults={
                "display_name": disp,
                "city": city,
                "state_code": st,
                "place_type": "CITY",
                "latitude": lat,
                "longitude": lon,
            },
        )

    # Seed highway stations across the US
    stations_data = [
        (201, "PA Turnpike Travelplaza", "I-76 Exit 150", "Bedford", "PA", Decimal("3.299"), 40.0, -78.5),
        (202, "Ohio Gateway Oasis", "I-80 Exit 70", "Toledo", "OH", Decimal("3.149"), 41.6, -83.5),
        (203, "Iowa Crossroads", "I-80 Exit 284", "Walcott", "IA", Decimal("2.999"), 41.6, -90.8),
        (204, "Nebraska Express Stop", "I-80 Exit 220", "Lexington", "NE", Decimal("2.899"), 40.7, -99.7),
        (205, "Wyoming Frontier Depot", "I-80 Exit 211", "Rawlins", "WY", Decimal("3.099"), 41.5, -107.0),
        (206, "Utah Basin Depot", "I-80 Exit 50", "Wendover", "UT", Decimal("3.399"), 40.7, -114.0),
        (207, "Nevada Silver Express", "I-15 Exit 1", "Primm", "NV", Decimal("3.499"), 36.1, -115.1),
        (208, "Texas Lone Star Plaza", "I-35 Exit 290", "Salado", "TX", Decimal("2.799"), 30.9, -97.5),
    ]
    for opis_id, name, addr, city, st, price, lat, lon in stations_data:
        FuelStation.objects.update_or_create(
            opis_truckstop_id=opis_id,
            defaults={
                "truckstop_name": name,
                "address": addr,
                "city": city,
                "state": st,
                "country_code": "US",
                "rack_id": 1,
                "retail_price": price,
                "latitude": lat,
                "longitude": lon,
                "is_active_us": True,
            },
        )
    StationSpatialIndex.get_instance().build_from_db()


@pytest.mark.django_db(transaction=True)
@respx.mock
def test_tc_e2e_01_cold_start_seeding_and_coast_to_coast(live_server) -> None:
    """TC-E2E-01: Cold-Start Seeding -> Health -> Real TCP Coast-to-Coast Route."""
    coords = [
        (40.7128, -74.0060),
        (40.0, -78.5),
        (41.6, -83.5),
        (41.6, -90.8),
        (40.7, -99.7),
        (41.5, -107.0),
        (40.7, -114.0),
        (36.1, -115.1),
        (34.0522, -118.2437),
    ]
    poly = encode_polyline(coords, precision=6)
    respx.get(url__startswith="https://router.project-osrm.org/route/v1/driving/").respond(
        status_code=200,
        json={"code": "Ok", "routes": [{"distance": 4500000.0, "duration": 140000.0, "geometry": poly}]},
    )

    with httpx.Client(base_url=live_server.url, timeout=10.0) as client:
        # 1. Health check over real TCP
        health_resp = client.get("/api/v1/health/")
        assert health_resp.status_code == 200
        health_data = health_resp.json()
        assert health_data["status"] == "healthy"
        assert health_data["spatial_index_ready"] is True

        # 2. Coast-to-coast optimization
        payload = {
            "start": "New York, NY",
            "finish": "Los Angeles, CA",
            "vehicle_range_miles": 500.0,
            "mpg": 10.0,
            "include_geojson": True,
        }
        route_resp = client.post("/api/v1/route/", json=payload)
        assert route_resp.status_code == 200

        data = route_resp.json()
        assert "route" in data
        assert "fuel_summary" in data
        assert "fuel_stops" in data
        assert "map" in data

        dist = data["route"]["total_distance_miles"]
        expected_consumed = round(dist / 10.0, 3)
        assert abs(data["fuel_summary"]["total_fuel_consumed_gallons"] - expected_consumed) < 0.1
        assert len(data["fuel_stops"]) >= 5


@pytest.mark.django_db(transaction=True)
@respx.mock
def test_tc_e2e_02_multi_corridor_matrix(live_server) -> None:
    """TC-E2E-02: Multi-corridor testing: short-haul, ZIP-to-ZIP, coords."""
    # Short route: Dallas, TX -> Austin, TX (< 500 miles)
    short_coords = [(32.7767, -96.7970), (30.9, -97.5), (30.2672, -97.7431)]
    poly_short = encode_polyline(short_coords, precision=6)
    respx.get(url__startswith="https://router.project-osrm.org/route/v1/driving/").respond(
        status_code=200,
        json={"code": "Ok", "routes": [{"distance": 313000.0, "duration": 10800.0, "geometry": poly_short}]},
    )

    with httpx.Client(base_url=live_server.url, timeout=10.0) as client:
        # 1. Short route: Dallas to Austin (< 500 mi: 0 stops required)
        resp_short = client.post(
            "/api/v1/route/",
            json={"start": "Dallas, TX", "finish": "Austin, TX"},
        )
        assert resp_short.status_code == 200
        short_data = resp_short.json()
        assert short_data["fuel_summary"]["number_of_fuel_stops"] == 0
        assert short_data["recommended_cheapest_station"] is not None

        # 2. ZIP to ZIP: 10001 -> 90210
        resp_zip = client.post(
            "/api/v1/route/",
            json={"start": "10001", "finish": "90210"},
        )
        assert resp_zip.status_code == 200
        assert resp_zip.json()["route"]["start"]["state"] == "NY"

        # 3. Raw Coordinate Inputs
        resp_coords = client.post(
            "/api/v1/route/",
            json={"start": "32.7767, -96.7970", "finish": "30.2672, -97.7431"},
        )
        assert resp_coords.status_code == 200


@pytest.mark.django_db(transaction=True)
@respx.mock
def test_tc_e2e_03_get_vs_post_parity_and_recalculation(live_server) -> None:
    """TC-E2E-03: Verify GET vs POST parity and dynamic recalculation with custom MPG."""
    coords = [(32.7767, -96.7970), (30.9, -97.5), (30.2672, -97.7431)]
    poly = encode_polyline(coords, precision=6)
    respx.get(url__startswith="https://router.project-osrm.org/route/v1/driving/").respond(
        status_code=200,
        json={"code": "Ok", "routes": [{"distance": 313000.0, "duration": 10800.0, "geometry": poly}]},
    )

    with httpx.Client(base_url=live_server.url, timeout=10.0) as client:
        post_resp = client.post("/api/v1/route/", json={"start": "Dallas, TX", "finish": "Austin, TX"})
        assert post_resp.status_code == 200
        post_data = post_resp.json()

        get_resp = client.get("/api/v1/route/?start=Dallas,+TX&finish=Austin,+TX")
        assert get_resp.status_code == 200
        get_data = get_resp.json()

        assert post_data["route"]["total_distance_miles"] == get_data["route"]["total_distance_miles"]
        assert post_data["fuel_summary"]["total_money_spent_on_fuel_usd"] == get_data["fuel_summary"]["total_money_spent_on_fuel_usd"]

        # Recalculate with smaller tank / initial fuel
        recalc_resp = client.post(
            "/api/v1/route/",
            json={
                "start": "Dallas, TX",
                "finish": "Austin, TX",
                "vehicle_range_miles": 500.0,
                "mpg": 8.0,
                "initial_fuel_gallons": 20.0,
            },
        )
        assert recalc_resp.status_code == 200
        recalc_data = recalc_resp.json()
        assert recalc_data["fuel_summary"]["vehicle_mpg"] == 8.0


@pytest.mark.django_db(transaction=True)
@respx.mock
def test_tc_e2e_04_map_url_follow_through_and_geojson(live_server) -> None:
    """TC-E2E-04: Follow map_url over TCP, verify accessible Leaflet HTML and GeoJSON."""
    coords = [(32.7767, -96.7970), (30.9, -97.5), (30.2672, -97.7431)]
    poly = encode_polyline(coords, precision=6)
    respx.get(url__startswith="https://router.project-osrm.org/route/v1/driving/").respond(
        status_code=200,
        json={"code": "Ok", "routes": [{"distance": 313000.0, "duration": 10800.0, "geometry": poly}]},
    )

    with httpx.Client(base_url=live_server.url, timeout=10.0) as client:
        resp = client.post(
            "/api/v1/route/",
            json={"start": "Dallas, TX", "finish": "Austin, TX", "include_geojson": True},
        )
        assert resp.status_code == 200
        data = resp.json()

        # Follow map_url
        map_url = data["map"]["map_url"]
        map_resp = client.get(map_url)
        assert map_resp.status_code == 200
        assert "text/html" in map_resp.headers["content-type"]
        html = map_resp.text
        assert "leaflet.js" in html
        assert "Trip Overview" in html
        assert "--text: #000000;" in html  # Accessibility high contrast requirement

        # Verify GeoJSON
        geojson = data["map"]["geojson"]
        assert geojson["type"] == "FeatureCollection"
        features = geojson["features"]
        assert any(f["geometry"]["type"] == "LineString" for f in features)


@pytest.mark.django_db(transaction=True)
def test_tc_e2e_05_negative_and_error_flows(live_server) -> None:
    """TC-E2E-05: Exercise validation errors, Canadian rejection, and OpenAPI schema."""
    with httpx.Client(base_url=live_server.url, timeout=10.0) as client:
        # Missing parameters
        resp_missing = client.post("/api/v1/route/", json={})
        assert resp_missing.status_code == 400

        # Negative MPG
        resp_mpg = client.post("/api/v1/route/", json={"start": "Dallas, TX", "finish": "Austin, TX", "mpg": -5.0})
        assert resp_mpg.status_code == 400

        # Canadian city rejection
        resp_can = client.post("/api/v1/route/", json={"start": "Toronto, Ontario, Canada", "finish": "Dallas, TX"})
        assert resp_can.status_code == 400
        assert resp_can.json()["error"]["code"] == "LOCATION_OUTSIDE_USA"

        # OpenAPI schema
        resp_schema = client.get("/api/schema/")
        assert resp_schema.status_code == 200
        assert "openapi" in resp_schema.text


@pytest.mark.django_db(transaction=True)
@respx.mock
def test_tc_e2e_06_concurrent_requests_stress(live_server) -> None:
    """TC-E2E-06: 20 simultaneous concurrent requests against live_server TCP socket."""
    coords = [(32.7767, -96.7970), (30.9, -97.5), (30.2672, -97.7431)]
    poly = encode_polyline(coords, precision=6)
    respx.get(url__startswith="https://router.project-osrm.org/route/v1/driving/").respond(
        status_code=200,
        json={"code": "Ok", "routes": [{"distance": 313000.0, "duration": 10800.0, "geometry": poly}]},
    )

    async def run_concurrent() -> list[int]:
        async with httpx.AsyncClient(base_url=live_server.url, timeout=15.0) as async_client:
            # Warm up connection
            warmup = await async_client.post(
                "/api/v1/route/",
                json={"start": "Dallas, TX", "finish": "Austin, TX"},
            )
            assert warmup.status_code == 200

            # 20 simultaneous concurrent requests
            tasks = [
                async_client.post(
                    "/api/v1/route/",
                    json={"start": "Dallas, TX", "finish": "Austin, TX"},
                )
                for _ in range(20)
            ]
            responses = await asyncio.gather(*tasks)
            return [r.status_code for r in responses]

    status_codes = asyncio.run(run_concurrent())
    assert len(status_codes) == 20
    assert all(code == 200 for code in status_codes)


@pytest.mark.django_db(transaction=True)
def test_tc_e2e_11_deploy_wizard_sre_and_destroy_lifecycle(live_server) -> None:
    """TC-E2E-11: End-to-End Zero-to-Production Wizard -> Live SRE SLA Check -> Ephemeral Demo Teardown."""
    import io
    from unittest.mock import patch
    from django.conf import settings

    # 1. Run deploy_wizard in non-interactive mode
    out_deploy = io.StringIO()
    call_command(
        "deploy_wizard",
        target="docker-vps",
        domain="api.e2e-demo.example.com",
        staging_domain="staging-api.e2e-demo.example.com",
        secret_key="e2e-super-secret-production-key-37cec3b1487d-long-enough",
        non_interactive=True,
        stdout=out_deploy,
    )
    assert (settings.BASE_DIR / ".env.production").exists()
    assert (settings.BASE_DIR / "deploy" / "generated" / "deployment_state.json").exists()

    # 2. Query live_server /api/v1/sre/sla/ and /api/v1/sre/metrics/ over TCP
    with httpx.Client(base_url=live_server.url, timeout=10.0) as client:
        sla_resp = client.get("/api/v1/sre/sla/")
        assert sla_resp.status_code == 200
        sla_json = sla_resp.json()
        assert "overall_status" in sla_json
        assert len(sla_json["slos"]) == 6

        prom_resp = client.get("/api/v1/sre/metrics/")
        assert prom_resp.status_code == 200
        assert "spotter_sla_compliant" in prom_resp.text

    # 3. Tear down demo artifacts with destroy_deployment
    out_destroy = io.StringIO()
    with patch("subprocess.run"):
        call_command("destroy_deployment", yes=True, stdout=out_destroy)

    assert not (settings.BASE_DIR / ".env.production").exists()
    assert not (settings.BASE_DIR / "deploy" / "generated" / "deployment_state.json").exists()

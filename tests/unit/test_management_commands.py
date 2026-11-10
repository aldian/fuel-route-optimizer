"""Unit tests for Django management commands: load_fuel_stations, load_us_gazetteer, and deploy_wizard."""
from io import StringIO
import json
import os
from pathlib import Path
from unittest.mock import patch
import pytest
from django.conf import settings
from django.core.management import call_command
from apps.routing.models import USPlaceGazetteer
from apps.stations.models import FuelStation


@pytest.fixture(autouse=True)
def preserve_live_deployment_files():
    """Back up and restore any live .env.production or terraform.tfvars during unit tests."""
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


@pytest.mark.django_db
def test_load_fuel_stations_command(tmp_path: Path) -> None:
    # 1. Missing file error handling
    out = StringIO()
    err = StringIO()
    call_command("load_fuel_stations", file="/non/existent/file.json", stdout=out, stderr=err)
    assert "Station dataset file not found" in err.getvalue()

    # 2. Valid file loading
    sample_stations = [
        {
            "opis_truckstop_id": 999901,
            "truckstop_name": "CMD TEST STOP #1",
            "address": "123 Main St",
            "city": "Dallas",
            "state": "TX",
            "country_code": "US",
            "rack_id": 100,
            "retail_price": "3.199",
            "latitude": 32.7767,
            "longitude": -96.7970,
            "ecef_x": 0.0469,
            "ecef_y": -0.8398,
            "ecef_z": 0.5413,
            "geocode_precision": "CITY_CENTROID",
            "is_active_us": True,
        }
    ]
    test_file = tmp_path / "test_stations.json"
    test_file.write_text(json.dumps(sample_stations), encoding="utf-8")

    out2 = StringIO()
    call_command(
        "load_fuel_stations",
        file=str(test_file),
        batch_size=100,
        no_index_warmup=False,
        stdout=out2,
    )
    assert "Successfully seeded 1 fuel stations" in out2.getvalue()
    assert FuelStation.objects.filter(opis_truckstop_id=999901).exists()

    # 3. Reload with --no-index-warmup
    out3 = StringIO()
    call_command(
        "load_fuel_stations",
        file=str(test_file),
        no_index_warmup=True,
        stdout=out3,
    )
    assert "Successfully seeded 1 fuel stations" in out3.getvalue()


@pytest.mark.django_db
def test_load_us_gazetteer_command(tmp_path: Path) -> None:
    # 1. Missing file error handling
    out = StringIO()
    err = StringIO()
    call_command("load_us_gazetteer", file="/non/existent/gazetteer.json", stdout=out, stderr=err)
    assert "Gazetteer file not found" in err.getvalue()

    # 2. Valid file loading
    sample_places = [
        {
            "normalized_query": "plano,tx",
            "display_name": "Plano, TX",
            "city": "Plano",
            "state_code": "TX",
            "place_type": "CITY",
            "latitude": 33.0198,
            "longitude": -96.6989,
            "population": 285494,
        }
    ]
    test_file = tmp_path / "test_gazetteer.json"
    test_file.write_text(json.dumps(sample_places), encoding="utf-8")

    out2 = StringIO()
    call_command("load_us_gazetteer", file=str(test_file), batch_size=50, stdout=out2)
    assert "Successfully seeded 1 US gazetteer entries" in out2.getvalue()
    assert USPlaceGazetteer.objects.filter(normalized_query="plano,tx").exists()


@pytest.mark.django_db
def test_deploy_wizard_interactive_and_production_write(tmp_path: Path) -> None:
    # Mock inputs:
    # 1. target = "k8s" [5]
    # 2. domain = "api.example.com"
    # 3. staging domain = "staging-api.example.com"
    # 4. secret key choice = 1 (auto-generate)
    # 5. database url = "postgresql://user:pass@db:5432/app"
    # 6. redis url = "redis://redis:6379/0"
    # 7. k8s provider = "1" (gcp-gke)
    # 8. dns provider = "1" (none)
    inputs = [
        "5",  # k8s
        "api.example.com",  # domain
        "staging-api.example.com",  # staging domain
        "1",  # auto-gen secret key
        "postgresql://user:pass@db:5432/app",  # db url
        "redis://redis:6379/0",  # redis url
        "1",  # gcp-gke
        "1",  # dns provider: none
    ]

    out = StringIO()
    with patch("builtins.input", side_effect=inputs):
        call_command(
            "deploy_wizard",
            stdout=out,
        )

    output = out.getvalue()
    assert "Deployment configuration for target 'k8s' is ready!" in output
    assert "Active US Fuel Stations" in output
    assert "DNS & DOMAIN CONFIGURATION GUIDE" in output

    # Check generated .env.production
    from django.conf import settings
    env_path = settings.BASE_DIR / ".env.production"
    assert env_path.exists()
    content = env_path.read_text(encoding="utf-8")
    assert "ALLOWED_HOSTS=api.example.com,staging-api.example.com,127.0.0.1,localhost" in content
    assert "PRIMARY_DOMAIN=api.example.com" in content
    assert "STAGING_DOMAIN=staging-api.example.com" in content
    assert "DATABASE_URL=postgresql://user:pass@db:5432/app" in content
    assert "REDIS_URL=redis://redis:6379/0" in content

    # Clean up generated test file and reset K8s overlays
    from unittest.mock import MagicMock
    with patch("subprocess.run", return_value=MagicMock(stdout="", returncode=0)):
        call_command("destroy_deployment", yes=True, stdout=StringIO())


@pytest.mark.django_db
def test_deploy_wizard_manual_secret_key_short_warning() -> None:
    inputs = [
        "1",  # docker-vps
        "localhost",  # domain
        "2",  # manual key choice
        "postgresql://user:pass@db:5432/app",  # db url
        "",  # redis url
    ]

    out = StringIO()
    with patch("builtins.input", side_effect=inputs), patch("getpass.getpass", return_value="short-key"):
        call_command(
            "deploy_wizard",
            dry_run=True,
            stdout=out,
        )

    output = out.getvalue()
    assert "Warning: Key is shorter than 32 characters." in output
    assert "[DRY RUN]" in output

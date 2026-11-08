"""Unit tests for the deploy_wizard management command."""
import io
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from django.conf import settings
from django.core.management import call_command
from apps.stations.dns.base import DNSRecordResult
from apps.stations.management.commands.deploy_wizard import generate_secure_secret_key


def test_generate_secure_secret_key() -> None:
    key1 = generate_secure_secret_key()
    key2 = generate_secure_secret_key()
    assert len(key1) >= 50
    assert len(key2) >= 50
    assert key1 != key2


def test_deploy_wizard_dry_run() -> None:
    out = io.StringIO()
    call_command(
        "deploy_wizard",
        target="docker-vps",
        domain="api.test.example.com",
        secret_key="my-super-secret-key-for-test-37cec3b1",
        non_interactive=True,
        dry_run=True,
        stdout=out,
    )
    output = out.getvalue()
    assert "[DRY RUN]" in output
    assert "target selected: docker-vps" in output.lower()
    assert "[REDACTED]" in output


def test_deploy_wizard_json_config(tmp_path: Path) -> None:
    cfg_file = tmp_path / "deploy_config.json"
    cfg_file.write_text(
        json.dumps(
            {
                "target": "aws",
                "domain": "aws.example.com",
                "database_url": "postgres://user:pass@db:5432/fuel_db",
                "redis_url": "redis://cache:6379/0",
            }
        )
    )

    out = io.StringIO()
    call_command(
        "deploy_wizard",
        config_json=str(cfg_file),
        dry_run=True,
        stdout=out,
    )
    output = out.getvalue()
    assert "target selected: aws" in output.lower()


def test_deploy_wizard_dry_run_with_route53_dns() -> None:
    out = io.StringIO()
    call_command(
        "deploy_wizard",
        target="docker-vps",
        domain="api.test.example.com",
        staging_domain="staging-api.test.example.com",
        secret_key="my-super-secret-key-for-test-37cec3b1",
        dns_provider="route53",
        route53_zone_id="Z123456",
        dns_target_ip="198.51.100.22",
        non_interactive=True,
        dry_run=True,
        stdout=out,
    )
    output = out.getvalue()
    assert "[DRY RUN]" in output
    assert "Would update ROUTE53 A-records: api.test.example.com, staging-api.test.example.com -> 198.51.100.22" in output


@pytest.mark.django_db
def test_deploy_wizard_live_with_route53_dns() -> None:
    out = io.StringIO()
    mock_res = DNSRecordResult(
        success=True,
        domain="api.route53.example.com",
        record_type="A",
        target_ip="198.51.100.22",
        provider="AWS Route 53",
        message="Record updated",
    )
    with patch("apps.stations.dns.route53.Route53DNSProvider.upsert_a_record", return_value=mock_res):
        call_command(
            "deploy_wizard",
            target="docker-vps",
            domain="api.route53.example.com",
            staging_domain="staging-api.route53.example.com",
            secret_key="my-super-secret-key-for-test-37cec3b1",
            dns_provider="route53",
            route53_zone_id="Z123456",
            dns_target_ip="198.51.100.22",
            non_interactive=True,
            stdout=out,
        )
    output = out.getvalue()
    assert "Applying automated DNS record updates via ROUTE53" in output
    assert "✓ api.route53.example.com -> 198.51.100.22" in output

    # Clean up generated .env.production
    env_path = settings.BASE_DIR / ".env.production"
    if env_path.exists():
        env_path.unlink()


@pytest.mark.django_db
def test_deploy_wizard_live_with_namecheap_dns_failure_branch() -> None:
    out = io.StringIO()
    mock_fail = DNSRecordResult(
        success=False,
        domain="api.namecheap.example.com",
        record_type="A",
        target_ip="198.51.100.33",
        provider="Namecheap",
        message="Whitelisted IP mismatch",
    )
    with patch("apps.stations.dns.namecheap.NamecheapDNSProvider.upsert_a_record", return_value=mock_fail):
        call_command(
            "deploy_wizard",
            target="docker-vps",
            domain="api.namecheap.example.com",
            staging_domain="staging-api.namecheap.example.com",
            secret_key="my-super-secret-key-for-test-37cec3b1",
            dns_provider="namecheap",
            namecheap_api_user="ncuser",
            namecheap_api_key="nckey",
            dns_target_ip="198.51.100.33",
            non_interactive=True,
            stdout=out,
        )
    output = out.getvalue()
    assert "Applying automated DNS record updates via NAMECHEAP" in output
    assert "⚠ api.namecheap.example.com -> Whitelisted IP mismatch" in output

    env_path = settings.BASE_DIR / ".env.production"
    if env_path.exists():
        env_path.unlink()


def test_deploy_wizard_interactive_route53_prompts() -> None:
    inputs = [
        "1",  # docker-vps
        "api.example.com",  # domain
        "staging-api.example.com",  # staging domain
        "1",  # auto-gen secret key
        "postgresql://user:pass@db:5432/app",  # db url
        "",   # redis url
        "2",  # dns provider: route53
        "Z987654",  # hosted zone id
        "198.51.100.88",  # target public IP
    ]
    out = io.StringIO()
    with patch("builtins.input", side_effect=inputs):
        call_command(
            "deploy_wizard",
            dry_run=True,
            stdout=out,
        )
    output = out.getvalue()
    assert "[DRY RUN] Would update ROUTE53 A-records: api.example.com, staging-api.example.com -> 198.51.100.88" in output


def test_deploy_wizard_interactive_namecheap_prompts() -> None:
    inputs = [
        "1",  # docker-vps
        "api.example.com",  # domain
        "staging-api.example.com",  # staging domain
        "1",  # auto-gen secret key
        "postgresql://user:pass@db:5432/app",  # db url
        "",   # redis url
        "3",  # dns provider: namecheap
        "my_nc_user",  # namecheap api user
        "198.51.100.1",  # whitelisted client ip
        "203.0.113.77",  # target public IP
    ]
    out = io.StringIO()
    with patch("builtins.input", side_effect=inputs), patch("getpass.getpass", return_value="my_nc_key"):
        call_command(
            "deploy_wizard",
            dry_run=True,
            stdout=out,
        )
    output = out.getvalue()
    assert "[DRY RUN] Would update NAMECHEAP A-records: api.example.com, staging-api.example.com -> 203.0.113.77" in output

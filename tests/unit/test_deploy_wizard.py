"""Unit tests for the deploy_wizard management command."""
import io
import json
import os
import tempfile
from pathlib import Path
import pytest
from django.core.management import call_command
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

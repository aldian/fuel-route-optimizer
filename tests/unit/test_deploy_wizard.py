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


# ============================================================================
# Ephemeral Demo Teardown Tests (destroy_deployment / ./bin/spotter-destroy)
# ============================================================================

def test_destroy_deployment_blocked_when_feature_flag_disabled() -> None:
    from django.core.management.base import CommandError
    from apps.common.flags import flags

    flags.set_flag("ALLOW_EPHEMERAL_TEARDOWN", False)
    try:
        with pytest.raises(CommandError, match="ALLOW_EPHEMERAL_TEARDOWN"):
            call_command("destroy_deployment", target="docker-vps", yes=True, dry_run=True)

        # Verify --force overrides the disabled feature flag
        out = io.StringIO()
        call_command("destroy_deployment", target="docker-vps", yes=True, force=True, dry_run=True, stdout=out)
        assert "Teardown complete!" in out.getvalue()
    finally:
        flags.delete_flag("ALLOW_EPHEMERAL_TEARDOWN")


def test_destroy_deployment_interactive_cancel() -> None:
    out = io.StringIO()
    with patch("builtins.input", return_value="n"):
        call_command("destroy_deployment", target="docker-vps", stdout=out)
    assert "Teardown cancelled by operator" in out.getvalue()


def test_destroy_deployment_k8s_and_cluster_dry_run() -> None:
    out = io.StringIO()
    call_command(
        "destroy_deployment",
        target="k8s",
        k8s_provider="gcp-gke",
        destroy_cluster=True,
        cluster_name="demo-gke-cluster",
        region="us-central1",
        domain="api.demo.example.com",
        staging_domain="staging-api.demo.example.com",
        dns_provider="route53",
        dry_run=True,
        stdout=out,
    )
    output = out.getvalue()
    assert "[DRY RUN] Would delete ROUTE53 A-records for: api.demo.example.com, staging-api.demo.example.com" in output
    assert "gcloud container clusters delete demo-gke-cluster --region=us-central1 --quiet" in output

    # Also test aws-eks and azure-aks cluster deletion dry-runs
    out_eks = io.StringIO()
    call_command(
        "destroy_deployment",
        target="k8s",
        k8s_provider="aws-eks",
        destroy_cluster=True,
        cluster_name="demo-eks",
        dry_run=True,
        stdout=out_eks,
    )
    assert "eksctl delete cluster --name demo-eks" in out_eks.getvalue()

    out_aks = io.StringIO()
    call_command(
        "destroy_deployment",
        target="k8s",
        k8s_provider="azure-aks",
        destroy_cluster=True,
        cluster_name="demo-aks",
        dry_run=True,
        stdout=out_aks,
    )
    assert "az aks delete --name demo-aks" in out_aks.getvalue()

    # Test gcp-gke cluster teardown when deploy/terraform/gcp-gke/terraform.tfstate exists
    gke_tfstate = settings.BASE_DIR / "deploy" / "terraform" / "gcp-gke" / "terraform.tfstate"
    had_state = gke_tfstate.exists()
    try:
        if not had_state:
            gke_tfstate.write_text("{}", encoding="utf-8")
        out_tf_gke = io.StringIO()
        with patch("subprocess.run", return_value=MagicMock(stdout="Destroyed GKE\n", returncode=0)) as mock_run:
            call_command(
                "destroy_deployment",
                target="k8s",
                k8s_provider="gcp-gke",
                destroy_cluster=True,
                keep_local_artifacts=True,
                yes=True,
                stdout=out_tf_gke,
            )
            executed_cmds = [" ".join(call.args[0]) for call in mock_run.call_args_list]
            assert "terraform -chdir=deploy/terraform/gcp-gke destroy -auto-approve" in executed_cmds
    finally:
        if not had_state and gke_tfstate.exists():
            gke_tfstate.unlink()


def test_destroy_deployment_terraform_and_paas_dry_run() -> None:
    for cloud_target in ("gcp", "aws", "azure"):
        out = io.StringIO()
        call_command("destroy_deployment", target=cloud_target, dry_run=True, stdout=out)
        assert f"terraform -chdir=deploy/terraform/{cloud_target} destroy -auto-approve" in out.getvalue()

    out_paas = io.StringIO()
    call_command("destroy_deployment", target="paas", dry_run=True, stdout=out_paas)
    assert "flyctl apps destroy fuel-route-optimizer --yes" in out_paas.getvalue()


@pytest.mark.django_db
def test_deploy_then_destroy_full_lifecycle_execution() -> None:
    # 1. Run deploy_wizard to generate .env.production and deployment_state.json
    mock_upsert = DNSRecordResult(
        success=True,
        domain="api.demo.example.com",
        record_type="A",
        target_ip="198.51.100.42",
        provider="AWS Route 53",
        message="Created",
    )
    with patch("apps.stations.dns.route53.Route53DNSProvider.upsert_a_record", return_value=mock_upsert):
        call_command(
            "deploy_wizard",
            target="docker-vps",
            domain="api.demo.example.com",
            staging_domain="staging-api.demo.example.com",
            secret_key="my-super-secret-key-for-test-37cec3b1",
            dns_provider="route53",
            route53_zone_id="ZDEMO123",
            dns_target_ip="198.51.100.42",
            non_interactive=True,
            stdout=io.StringIO(),
        )

    state_path = settings.BASE_DIR / "deploy" / "generated" / "deployment_state.json"
    env_path = settings.BASE_DIR / ".env.production"
    assert state_path.exists()
    assert env_path.exists()

    # 2. Run destroy_deployment (auto-reads deployment_state.json, deletes DNS, runs docker compose down, cleans files)
    mock_del = DNSRecordResult(
        success=True,
        domain="api.demo.example.com",
        record_type="A",
        target_ip="198.51.100.42",
        provider="AWS Route 53",
        message="Deleted from Route 53",
    )
    mock_proc = MagicMock(stdout="Removed container fuel-route-optimizer\n", returncode=0)
    out = io.StringIO()
    with (
        patch("apps.stations.dns.route53.Route53DNSProvider.delete_a_record", return_value=mock_del),
        patch("subprocess.run", return_value=mock_proc),
    ):
        call_command("destroy_deployment", yes=True, stdout=out)

    output = out.getvalue()
    assert "Loaded deployment state" in output
    assert "Deleting DNS A-records via ROUTE53" in output
    assert "Deleted from Route 53" in output
    assert not env_path.exists()
    assert not state_path.exists()


def test_destroy_deployment_missing_cli_tool_and_error_handling() -> None:
    from django.core.management.base import CommandError
    import subprocess

    # Missing CLI binary should log a warning and continue gracefully
    out = io.StringIO()
    with patch("subprocess.run", side_effect=FileNotFoundError("docker not found")):
        call_command("destroy_deployment", target="docker-vps", keep_local_artifacts=True, yes=True, stdout=out)
    assert "not installed locally; skipped" in out.getvalue()

    # Non-zero exit code from CLI tool should raise CommandError
    with patch(
        "subprocess.run",
        side_effect=subprocess.CalledProcessError(returncode=1, cmd=["docker"], stderr="daemon error"),
    ):
        with pytest.raises(CommandError, match="Infrastructure teardown command failed"):
            call_command("destroy_deployment", target="docker-vps", keep_local_artifacts=True, yes=True)


def test_bin_spotter_scripts_executable_and_cli_execution() -> None:
    import subprocess

    bin_dir = settings.BASE_DIR / "bin"
    expected_scripts = [
        "spotter-deploy",
        "spotter-destroy",
        "spotter-sre",
        "spotter-feature-flag",
        "spotter-check-migrations",
        "spotter-rollback",
    ]
    for script_name in expected_scripts:
        script_path = bin_dir / script_name
        assert script_path.exists(), f"Missing CLI wrapper: {script_path}"
        assert os.access(script_path, os.X_OK), f"CLI wrapper is not executable (chmod +x required): {script_path}"

    # Execute ./bin/spotter-deploy directly as an OS subprocess
    deploy_bin = str(bin_dir / "spotter-deploy")
    proc = subprocess.run(
        [
            deploy_bin,
            "--target",
            "docker-vps",
            "--domain",
            "api.cli-test.example.com",
            "--non-interactive",
            "--dry-run",
        ],
        cwd=str(settings.BASE_DIR),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"spotter-deploy failed: {proc.stderr}"
    assert "[DRY RUN] Generated .env.production Preview:" in proc.stdout
    assert "api.cli-test.example.com" in proc.stdout


@pytest.mark.django_db
def test_deploy_wizard_cloud_generated_domain_and_provision_postgres_from_scratch() -> None:
    # 1. Interactive GCP Cloud Run with blank domain ('auto' -> .run.app), interactive Project ID, and 'create' PostgreSQL + Redis
    inputs = [
        "3",                  # [3] gcp (Cloud Run + Cloud SQL + Terraform)
        "",                   # Leave domain blank -> uses GCP Cloud Run generated domain (*.a.run.app)
        "1",                  # Auto-generate DJANGO_SECRET_KEY
        "spotter-demo-proj",  # GCP Project ID prompt
        "create",             # Create PostgreSQL 16 from scratch on GCP Cloud SQL
        "create",             # Create Redis 7 from scratch on GCP Cloud Memorystore
    ]
    out = io.StringIO()
    with patch("builtins.input", side_effect=inputs):
        call_command("deploy_wizard", stdout=out)

    output = out.getvalue()
    assert "Configured Terraform to create Cloud SQL for PostgreSQL 16" in output
    assert "Configured Terraform to create Google Cloud Memorystore for Redis 7" in output
    assert "Using Cloud-Generated Public HTTPS Domain (GCP)" in output
    assert ".run.app,.a.run.app" in output

    env_path = settings.BASE_DIR / ".env.production"
    tfvars_path = settings.BASE_DIR / "deploy" / "terraform" / "gcp" / "terraform.tfvars"
    assert env_path.exists()
    assert tfvars_path.exists()

    env_text = env_path.read_text(encoding="utf-8")
    assert "ALLOWED_HOSTS=.run.app,.a.run.app,localhost,127.0.0.1" in env_text
    assert "PRIMARY_DOMAIN=auto" in env_text
    assert "/cloudsql/spotter-demo-proj:us-central1:fuel-route-optimizer-pg" in env_text
    assert "REDIS_URL=redis://fuel-route-optimizer-redis:6379/0" in env_text

    tfvars_text = tfvars_path.read_text(encoding="utf-8")
    assert 'project_id         = "spotter-demo-proj"' in tfvars_text
    assert "provision_postgres = true" in tfvars_text
    assert "provision_redis    = true" in tfvars_text

    # 2. Verify AWS, Azure, and Docker-VPS live tfvars generation + --database-url create & --redis-url create
    for target_name in ("aws", "azure", "docker-vps"):
        out_t = io.StringIO()
        call_command(
            "deploy_wizard",
            target=target_name,
            domain="auto",
            database_url="create",
            redis_url="create",
            db_password="explicit-test-db-password-123456",
            non_interactive=True,
            stdout=out_t,
        )
        assert "Deployment configuration for target" in out_t.getvalue()

    # 3. Verify _detect_gcp_project_id fallback when gcloud is missing
    from apps.stations.management.commands.deploy_wizard import Command as DeployWizardCommand
    with patch("subprocess.run", side_effect=FileNotFoundError("no gcloud")):
        assert DeployWizardCommand()._detect_gcp_project_id() == ""

    # 4. Verify Kubernetes (k8s) target generates k8s-secrets.yaml and deploy_k8s.sh
    out_k8s = io.StringIO()
    call_command(
        "deploy_wizard",
        target="k8s",
        domain="k8s.spotter.example.com",
        database_url="create",
        redis_url="create",
        db_password="k8s-test-db-pass-1234",
        non_interactive=True,
        stdout=out_k8s,
    )
    k8s_secrets_file = settings.BASE_DIR / "deploy" / "generated" / "k8s-secrets.yaml"
    k8s_deploy_script = settings.BASE_DIR / "deploy" / "generated" / "deploy_k8s.sh"
    assert k8s_secrets_file.exists()
    assert k8s_deploy_script.exists()
    assert "k8s-test-db-pass-1234" in k8s_secrets_file.read_text(encoding="utf-8")
    assert "kubectl apply -k" in k8s_deploy_script.read_text(encoding="utf-8")

    # 5. Clean up via destroy_deployment (verifying all terraform.tfvars and k8s files are also removed)
    with patch("subprocess.run", return_value=MagicMock(stdout="Destroyed\n", returncode=0)):
        call_command("destroy_deployment", yes=True, stdout=io.StringIO())
    assert not env_path.exists()
    assert not tfvars_path.exists()
    assert not k8s_secrets_file.exists()
    assert not k8s_deploy_script.exists()




"""Interactive and automated Zero-to-Production deployment CLI wizard."""
import getpass
import json
import os
from pathlib import Path
import re
import secrets
from typing import Any
from django.conf import settings
from django.core.management.base import BaseCommand


def generate_secure_secret_key() -> str:
    """Generate a high-entropy 64-character URL-safe secret key."""
    return secrets.token_urlsafe(50)


class Command(BaseCommand):
    help = "Guides operators through zero-to-production deployment and generates hardened configurations."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--target",
            choices=["docker-vps", "aws", "gcp", "azure", "k8s", "paas"],
            help="Deployment target environment.",
        )
        parser.add_argument(
            "--domain",
            type=str,
            help="Primary public domain (e.g. api.example.com).",
        )
        parser.add_argument(
            "--secret-key",
            type=str,
            help="Explicit DJANGO_SECRET_KEY (if omitted, auto-generated).",
        )
        parser.add_argument(
            "--database-url",
            type=str,
            help="Database connection URL (e.g. postgres://user:pass@host:5432/dbname).",
        )
        parser.add_argument(
            "--redis-url",
            type=str,
            help="Redis cache URL (e.g. redis://host:6379/0).",
        )
        parser.add_argument(
            "--non-interactive",
            action="store_true",
            help="Run without interactive prompts using provided arguments or defaults.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview generated configurations without writing files to disk.",
        )
        parser.add_argument(
            "--config-json",
            type=str,
            help="Path to JSON file containing deployment answers.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        self.stdout.write(self.style.MIGRATE_HEADING("=== Spotter Fuel Route Optimizer: Zero-to-Production Deploy Wizard ==="))

        cfg: dict[str, Any] = {}
        if options.get("config_json"):
            cfg_path = Path(options["config_json"])
            if cfg_path.exists():
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)

        non_interactive = options.get("non_interactive") or bool(options.get("config_json"))
        dry_run = options.get("dry_run", False)

        # 1. Target
        target = options.get("target") or cfg.get("target")
        if not target:
            if non_interactive:
                target = "docker-vps"
            else:
                self.stdout.write("\nSupported Deployment Targets:")
                self.stdout.write("  [1] docker-vps : VPS / Bare-Metal with Docker Compose + Caddy Auto-TLS (Default)")
                self.stdout.write("  [2] aws        : AWS ECS Fargate / App Runner + RDS + Terraform")
                self.stdout.write("  [3] gcp        : Google Cloud Run + Cloud SQL + Terraform")
                self.stdout.write("  [4] azure      : Microsoft Azure Container Apps + Postgres + Terraform")
                self.stdout.write("  [5] k8s        : Kubernetes (GCP GKE, AWS EKS, Azure AKS, or Generic K8s)")
                self.stdout.write("  [6] paas       : Managed PaaS (Fly.io, Render, Railway)")
                ans = input("Select target [1-6, default: 1]: ").strip()
                target_map = {
                    "1": "docker-vps", "docker-vps": "docker-vps", "": "docker-vps",
                    "2": "aws", "aws": "aws",
                    "3": "gcp", "gcp": "gcp",
                    "4": "azure", "azure": "azure",
                    "5": "k8s", "k8s": "k8s",
                    "6": "paas", "paas": "paas",
                }
                target = target_map.get(ans, "docker-vps")

        # 2. Domain & ALLOWED_HOSTS
        domain = options.get("domain") or cfg.get("domain")
        if not domain:
            if non_interactive:
                domain = "localhost"
            else:
                ans = input("\nEnter public domain for API (e.g. api.fuel-optimizer.com) [default: localhost]: ").strip()
                domain = ans if ans else "localhost"

        allowed_hosts = f"{domain},127.0.0.1,localhost"

        # 3. Secret Key
        secret_key = options.get("secret_key") or cfg.get("secret_key")
        if not secret_key:
            if non_interactive:
                secret_key = generate_secure_secret_key()
            else:
                self.stdout.write("\nSecret Key Configuration:")
                self.stdout.write("  [1] Auto-generate cryptographically secure key (Recommended)")
                self.stdout.write("  [2] Enter existing secret key")
                ans = input("Choice [1/2, default: 1]: ").strip()
                if ans == "2":
                    secret_key = getpass.getpass("Enter DJANGO_SECRET_KEY: ").strip()
                    if len(secret_key) < 32:
                        self.stdout.write(self.style.WARNING("Warning: Key is shorter than 32 characters."))
                else:
                    secret_key = generate_secure_secret_key()
                    self.stdout.write(self.style.SUCCESS("Generated high-entropy 64-char key."))

        # 4. Database & Cache URLs
        database_url = options.get("database_url") or cfg.get("database_url", "")
        if not database_url and not non_interactive:
            ans = input("\nPostgreSQL DATABASE_URL (leave blank for local SQLite WAL): ").strip()
            database_url = ans

        redis_url = options.get("redis_url") or cfg.get("redis_url", "")
        if not redis_url and not non_interactive:
            ans = input("Redis REDIS_URL (leave blank for local memory cache): ").strip()
            redis_url = ans

        # 5. Cloud-Specific Prompts
        cloud_region = cfg.get("region", "us-east-1" if target == "aws" else ("us-central1" if target == "gcp" else "eastus"))
        k8s_provider = cfg.get("k8s_provider", "gcp-gke")
        if target == "k8s" and not non_interactive:
            self.stdout.write("\nKubernetes Provider:")
            self.stdout.write("  [1] gcp-gke   : Google Kubernetes Engine (GKE Autopilot / Standard)")
            self.stdout.write("  [2] aws-eks   : AWS Elastic Kubernetes Service")
            self.stdout.write("  [3] azure-aks : Azure Kubernetes Service")
            self.stdout.write("  [4] generic   : Generic / Self-Managed K8s (ingress-nginx + cert-manager)")
            ans = input("Select provider [1-4, default: 1]: ").strip()
            k8s_map = {"1": "gcp-gke", "2": "aws-eks", "3": "azure-aks", "4": "generic", "": "gcp-gke"}
            k8s_provider = k8s_map.get(ans, "gcp-gke")

        # 6. Render Configuration Artifacts
        env_content = self._render_env_production(
            secret_key=secret_key,
            allowed_hosts=allowed_hosts,
            database_url=database_url,
            redis_url=redis_url,
            domain=domain,
        )

        gh_secrets_content = self._render_github_secrets_script(
            secret_key=secret_key,
            allowed_hosts=allowed_hosts,
            database_url=database_url,
            redis_url=redis_url,
        )

        if dry_run:
            self.stdout.write(self.style.SUCCESS("\n[DRY RUN] Generated .env.production Preview:"))
            # Redact secret key in preview
            redacted_env = re.sub(r"DJANGO_SECRET_KEY=.*", "DJANGO_SECRET_KEY=********[REDACTED]", env_content)
            self.stdout.write(redacted_env)
            self.stdout.write(self.style.SUCCESS(f"\n[DRY RUN] Target selected: {target} (K8s: {k8s_provider}). Dry run complete."))
            return

        # Write .env.production with 0600 permissions
        env_path = settings.BASE_DIR / ".env.production"
        with open(env_path, "w", encoding="utf-8") as f:
            f.write(env_content)
        os.chmod(env_path, 0o600)

        # Write deploy helper
        deploy_dir = settings.BASE_DIR / "deploy" / "generated"
        deploy_dir.mkdir(parents=True, exist_ok=True)
        gh_script_path = deploy_dir / "set_github_secrets.sh"
        with open(gh_script_path, "w", encoding="utf-8") as f:
            f.write(gh_secrets_content)
        os.chmod(gh_script_path, 0o755)

        self.stdout.write(self.style.SUCCESS(f"\n[1/3] Successfully wrote {env_path} (mode 0600)."))
        self.stdout.write(self.style.SUCCESS(f"[2/3] Generated GitHub Actions secret helper at {gh_script_path}."))

        # 7. Pre-flight verification
        self.stdout.write("\n[3/3] Running production pre-flight checks...")
        from apps.stations.models import FuelStation
        station_count = FuelStation.objects.filter(is_active_us=True).count()
        self.stdout.write(f"  * Active US Fuel Stations: {station_count} / 6626")

        from apps.routing.models import USPlaceGazetteer
        gaz_count = USPlaceGazetteer.objects.count()
        self.stdout.write(f"  * US Gazetteer Entries: {gaz_count}")

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDeployment configuration for target '{target}' is ready! "
                f"Run 'docker compose -f docker-compose.prod.yml up -d' or push to Git for automated CI/CD."
            )
        )

    def _render_env_production(
        self,
        secret_key: str,
        allowed_hosts: str,
        database_url: str,
        redis_url: str,
        domain: str,
    ) -> str:
        return f"""# Production Environment Configuration
# Generated by manage.py deploy_wizard
DJANGO_SETTINGS_MODULE=config.settings.production
DJANGO_SECRET_KEY={secret_key}
ALLOWED_HOSTS={allowed_hosts}
PRIMARY_DOMAIN={domain}

# Security Headers & HSTS
SECURE_SSL_REDIRECT=True
SECURE_HSTS_SECONDS=31536000

# Database & Cache
DATABASE_URL={database_url}
REDIS_URL={redis_url}

# Upstream Geocoder & Routing Timeouts
EXTERNAL_CONNECT_TIMEOUT_SEC=2.0
EXTERNAL_READ_TIMEOUT_SEC=4.0
"""

    def _render_github_secrets_script(
        self,
        secret_key: str,
        allowed_hosts: str,
        database_url: str,
        redis_url: str,
    ) -> str:
        return f"""#!/usr/bin/env bash
# Helper script to set GitHub Actions Secrets via gh CLI
# Usage: ./deploy/generated/set_github_secrets.sh

set -e

echo "Setting GitHub Actions Secrets..."
gh secret set DJANGO_SECRET_KEY --body "{secret_key}"
gh secret set ALLOWED_HOSTS --body "{allowed_hosts}"
if [ -n "{database_url}" ]; then
  gh secret set DATABASE_URL --body "{database_url}"
fi
if [ -n "{redis_url}" ]; then
  gh secret set REDIS_URL --body "{redis_url}"
fi
echo "Done! GitHub repository secrets have been configured."
"""

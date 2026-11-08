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
from apps.stations.dns.manager import DNSManager


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
            "--staging-domain",
            type=str,
            help="Staging domain (e.g. staging-api.example.com).",
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
            "--dns-provider",
            choices=["none", "route53", "namecheap"],
            default="none",
            help="Automated DNS provider for A-record provisioning (route53 or namecheap).",
        )
        parser.add_argument(
            "--dns-target-ip",
            type=str,
            help="Target External IP for DNS A-records (if omitted, auto-detected).",
        )
        parser.add_argument(
            "--route53-zone-id",
            type=str,
            help="AWS Route 53 Hosted Zone ID.",
        )
        parser.add_argument(
            "--namecheap-api-user",
            type=str,
            help="Namecheap API username.",
        )
        parser.add_argument(
            "--namecheap-api-key",
            type=str,
            help="Namecheap API key.",
        )
        parser.add_argument(
            "--namecheap-client-ip",
            type=str,
            help="Namecheap whitelisted client IP.",
        )
        parser.add_argument(
            "--namecheap-sandbox",
            action="store_true",
            help="Use Namecheap Sandbox endpoint.",
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

        staging_domain = options.get("staging_domain") or cfg.get("staging_domain")
        if not staging_domain:
            if non_interactive or domain == "localhost":
                staging_domain = f"staging-{domain}" if domain != "localhost" else "staging.localhost"
            else:
                staging_default = f"staging-{domain}"
                ans = input(f"Enter staging domain [default: {staging_default}]: ").strip()
                staging_domain = ans if ans else staging_default

        allowed_hosts = f"{domain},{staging_domain},127.0.0.1,localhost" if domain != "localhost" else "localhost,127.0.0.1"

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

        # 6. Automated DNS Configuration
        dns_provider = options.get("dns_provider") or cfg.get("dns_provider", "none")
        dns_target_ip = options.get("dns_target_ip") or cfg.get("dns_target_ip", "")
        route53_zone_id = options.get("route53_zone_id") or cfg.get("route53_zone_id", "")
        nc_user = options.get("namecheap_api_user") or cfg.get("namecheap_api_user", "")
        nc_key = options.get("namecheap_api_key") or cfg.get("namecheap_api_key", "")
        nc_client_ip = options.get("namecheap_client_ip") or cfg.get("namecheap_client_ip", "")
        nc_sandbox = bool(options.get("namecheap_sandbox") or cfg.get("namecheap_sandbox", False))

        if not non_interactive and dns_provider == "none" and domain not in ("localhost", "127.0.0.1"):
            self.stdout.write("\nAutomated DNS Record Provisioning:")
            self.stdout.write("  [1] none      : None / Manual DNS Record Setup (Default)")
            self.stdout.write("  [2] route53   : AWS Route 53 (Automated A-record creation via API)")
            self.stdout.write("  [3] namecheap : Namecheap DNS (Automated A-record creation via API)")
            ans = input("Select DNS provider [1-3, default: 1]: ").strip()
            dns_map = {"1": "none", "none": "none", "": "none", "2": "route53", "route53": "route53", "3": "namecheap", "namecheap": "namecheap"}
            dns_provider = dns_map.get(ans, "none")

            if dns_provider == "route53":
                route53_zone_id = input("Route 53 Hosted Zone ID (leave blank to auto-discover): ").strip()
            elif dns_provider == "namecheap":
                nc_user = input("Namecheap API User: ").strip()
                nc_key = getpass.getpass("Namecheap API Key: ").strip()
                nc_client_ip = input("Namecheap Whitelisted Client IP (default: 127.0.0.1): ").strip() or "127.0.0.1"

            if dns_provider != "none" and not dns_target_ip:
                detected_ip = DNSManager.detect_public_ip() or "127.0.0.1"
                ip_ans = input(f"Target Public IP for A-records [default: {detected_ip}]: ").strip()
                dns_target_ip = ip_ans if ip_ans else detected_ip

        # 7. Render Configuration Artifacts
        env_content = self._render_env_production(
            secret_key=secret_key,
            allowed_hosts=allowed_hosts,
            database_url=database_url,
            redis_url=redis_url,
            domain=domain,
            staging_domain=staging_domain,
        )

        gh_secrets_content = self._render_github_secrets_script(
            secret_key=secret_key,
            allowed_hosts=allowed_hosts,
            database_url=database_url,
            redis_url=redis_url,
            domain=domain,
            staging_domain=staging_domain,
        )

        if dry_run:
            self.stdout.write(self.style.SUCCESS("\n[DRY RUN] Generated .env.production Preview:"))
            # Redact secret key in preview
            redacted_env = re.sub(r"DJANGO_SECRET_KEY=.*", "DJANGO_SECRET_KEY=********[REDACTED]", env_content)
            self.stdout.write(redacted_env)
            if dns_provider != "none" and domain not in ("localhost", "127.0.0.1"):
                tip = dns_target_ip or DNSManager.detect_public_ip() or "127.0.0.1"
                self.stdout.write(self.style.SUCCESS(f"\n[DRY RUN] Would update {dns_provider.upper()} A-records: {domain}, {staging_domain} -> {tip}"))
            self._print_dns_guide(domain, staging_domain, target)
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

        if target == "k8s":
            self._patch_k8s_domains(domain, staging_domain)
            self.stdout.write(self.style.SUCCESS("[2.5/3] Patched Kubernetes Ingress & Certificate manifests with domain."))

        if dns_provider != "none" and domain not in ("localhost", "127.0.0.1"):
            tip = dns_target_ip or DNSManager.detect_public_ip() or "127.0.0.1"
            dns_cfg = {
                "dns_provider": dns_provider,
                "route53_zone_id": route53_zone_id,
                "namecheap_api_user": nc_user,
                "namecheap_api_key": nc_key,
                "namecheap_client_ip": nc_client_ip or "127.0.0.1",
                "namecheap_sandbox": nc_sandbox,
            }
            dns_mgr = DNSManager.from_config(dns_cfg)
            self.stdout.write(f"\n[2.8/3] Applying automated DNS record updates via {dns_provider.upper()}...")
            sync_domains = [d for d in [domain, staging_domain] if d and d not in ("localhost", "127.0.0.1")]
            results = dns_mgr.sync_domains(sync_domains, target_ip=tip)
            for res in results:
                if res.success:
                    self.stdout.write(self.style.SUCCESS(f"  ✓ {res.domain} -> {res.target_ip} ({res.message})"))
                else:
                    self.stdout.write(self.style.WARNING(f"  ⚠ {res.domain} -> {res.message}"))

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

        self._print_dns_guide(domain, staging_domain, target)

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDeployment configuration for target '{target}' is ready! "
                f"Run 'docker compose -f docker-compose.prod.yml up -d' or push to Git for automated CI/CD."
            )
        )

    def _print_dns_guide(self, domain: str, staging_domain: str, target: str) -> None:
        self.stdout.write("\n" + "=" * 76)
        self.stdout.write(self.style.MIGRATE_HEADING("                  DNS & DOMAIN CONFIGURATION GUIDE"))
        self.stdout.write("=" * 76)
        self.stdout.write("Configure the following DNS records with your domain registrar or DNS provider:")
        self.stdout.write(f"  * Production Hostname : {domain}")
        self.stdout.write(f"  * Staging Hostname    : {staging_domain}")
        self.stdout.write("\nDNS Records to create:")
        self.stdout.write(f"  [A Record]     {domain:<32} -> <External Load Balancer IP>")
        self.stdout.write(f"  [A Record]     {staging_domain:<32} -> <External Load Balancer IP>")
        if target == "k8s":
            self.stdout.write("\nTo retrieve your External Ingress IP in GCP GKE:")
            self.stdout.write("  kubectl get ingress -n production fuel-route-optimizer-ingress")
            self.stdout.write("  kubectl get ingress -n staging staging-fuel-route-optimizer-ingress")
            self.stdout.write("\nGoogle-managed SSL certificates will automatically provision once DNS propagates.")
        elif target == "docker-vps":
            self.stdout.write("\nTo retrieve your VPS public IP:")
            self.stdout.write("  curl -s https://api.ipify.org")
            self.stdout.write("\nCaddy reverse proxy will automatically provision Let's Encrypt TLS certificates.")
        self.stdout.write("=" * 76 + "\n")

    def _patch_k8s_domains(self, domain: str, staging_domain: str) -> None:
        """Patch Kubernetes overlay manifests with user-specified domains."""
        # Production overlay
        prod_cert = settings.BASE_DIR / "deploy" / "k8s" / "overlays" / "gcp-gke" / "certificate.yaml"
        if prod_cert.exists():
            prod_cert.write_text(
                f"apiVersion: networking.gke.io/v1\nkind: ManagedCertificate\nmetadata:\n  name: fuel-optimizer-certificate\nspec:\n  domains:\n    - {domain}\n",
                encoding="utf-8",
            )

        prod_ing = settings.BASE_DIR / "deploy" / "k8s" / "overlays" / "gcp-gke" / "ingress_patch.yaml"
        if prod_ing.exists():
            prod_ing.write_text(
                f"apiVersion: networking.k8s.io/v1\nkind: Ingress\nmetadata:\n  name: fuel-route-optimizer-ingress\n  annotations:\n    kubernetes.io/ingress.class: \"gce\"\n    networking.gke.io/managed-certificates: \"fuel-optimizer-certificate\"\nspec:\n  rules:\n    - host: {domain}\n      http:\n        paths:\n          - path: /*\n            pathType: ImplementationSpecific\n            backend:\n              service:\n                name: fuel-route-optimizer\n                port:\n                  number: 80\n",
                encoding="utf-8",
            )

        # Staging overlay
        stag_cert = settings.BASE_DIR / "deploy" / "k8s" / "overlays" / "gcp-gke-staging" / "certificate.yaml"
        if stag_cert.exists():
            stag_cert.write_text(
                f"apiVersion: networking.gke.io/v1\nkind: ManagedCertificate\nmetadata:\n  name: staging-fuel-optimizer-certificate\nspec:\n  domains:\n    - {staging_domain}\n",
                encoding="utf-8",
            )

        stag_ing = settings.BASE_DIR / "deploy" / "k8s" / "overlays" / "gcp-gke-staging" / "ingress_patch.yaml"
        if stag_ing.exists():
            stag_ing.write_text(
                f"apiVersion: networking.k8s.io/v1\nkind: Ingress\nmetadata:\n  name: fuel-route-optimizer-ingress\n  annotations:\n    kubernetes.io/ingress.class: \"gce\"\n    networking.gke.io/managed-certificates: \"staging-fuel-optimizer-certificate\"\nspec:\n  rules:\n    - host: {staging_domain}\n      http:\n        paths:\n          - path: /*\n            pathType: ImplementationSpecific\n            backend:\n              service:\n                name: fuel-route-optimizer\n                port:\n                  number: 80\n",
                encoding="utf-8",
            )

    def _render_env_production(
        self,
        secret_key: str,
        allowed_hosts: str,
        database_url: str,
        redis_url: str,
        domain: str,
        staging_domain: str = "",
    ) -> str:
        return f"""# Production Environment Configuration
# Generated by manage.py deploy_wizard
DJANGO_SETTINGS_MODULE=config.settings.production
DJANGO_SECRET_KEY={secret_key}
ALLOWED_HOSTS={allowed_hosts}
PRIMARY_DOMAIN={domain}
STAGING_DOMAIN={staging_domain}

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
        domain: str = "",
        staging_domain: str = "",
    ) -> str:
        return f"""#!/usr/bin/env bash
# Helper script to set GitHub Actions Secrets via gh CLI
# Usage: ./deploy/generated/set_github_secrets.sh

set -e

echo "Setting GitHub Actions Secrets..."
gh secret set DJANGO_SECRET_KEY --body "{secret_key}"
gh secret set ALLOWED_HOSTS --body "{allowed_hosts}"
if [ -n "{domain}" ]; then
  gh secret set PRIMARY_DOMAIN --body "{domain}"
fi
if [ -n "{staging_domain}" ]; then
  gh secret set STAGING_DOMAIN --body "{staging_domain}"
fi
if [ -n "{database_url}" ]; then
  gh secret set DATABASE_URL --body "{database_url}"
fi
if [ -n "{redis_url}" ]; then
  gh secret set REDIS_URL --body "{redis_url}"
fi
echo "Done! GitHub repository secrets have been configured."
"""

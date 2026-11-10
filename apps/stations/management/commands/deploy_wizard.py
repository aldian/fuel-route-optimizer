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


CLOUD_DOMAIN_SUFFIXES: dict[str, str] = {
    "gcp": ".run.app,.a.run.app",
    "aws": ".awsapprunner.com,.elb.amazonaws.com",
    "azure": ".azurecontainerapps.io",
    "paas": ".fly.dev,.onrender.com,.up.railway.app",
}


def generate_secure_secret_key() -> str:
    """Generate a high-entropy 64-character URL-safe secret key."""
    return secrets.token_urlsafe(50)


def generate_secure_db_password() -> str:
    """Generate a high-entropy 32-character URL-safe database password."""
    return secrets.token_urlsafe(24)


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
            help=(
                "Primary public domain (e.g. api.example.com), or 'auto' to use the cloud provider's "
                "generated domain (e.g. *.a.run.app on GCP Cloud Run)."
            ),
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
            "--provision-db",
            action="store_true",
            help=(
                "Provision a new PostgreSQL 16 database from scratch on the selected target "
                "(Cloud SQL on GCP, RDS on AWS, Flexible Server on Azure, or Postgres container on Docker)."
            ),
        )
        parser.add_argument(
            "--db-password",
            type=str,
            help="Explicit password when provisioning a new PostgreSQL instance (auto-generated if omitted).",
        )
        parser.add_argument(
            "--database-url",
            type=str,
            help=(
                "Database connection URL (e.g. postgres://user:pass@host:5432/dbname), "
                "or 'create' to provision PostgreSQL from scratch."
            ),
        )
        parser.add_argument(
            "--provision-redis",
            action="store_true",
            help=(
                "Provision a new Managed Redis 7 cache from scratch on the selected target "
                "(Memorystore on GCP, ElastiCache on AWS, Azure Cache for Redis on Azure, or Redis container on Docker)."
            ),
        )
        parser.add_argument(
            "--redis-url",
            type=str,
            help="Redis cache URL (e.g. redis://host:6379/0), or 'create' to provision Redis 7 from scratch.",
        )
        parser.add_argument(
            "--gcp-project-id",
            type=str,
            help="GCP Project ID (when target is gcp or k8s/gcp-gke).",
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
        self.stdout.write(
            self.style.MIGRATE_HEADING("=== Spotter Fuel Route Optimizer: Zero-to-Production Deploy Wizard ===")
        )

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
        cloud_suffix = CLOUD_DOMAIN_SUFFIXES.get(target, "")
        if not domain:
            if non_interactive:
                domain = "auto" if target in CLOUD_DOMAIN_SUFFIXES else "localhost"
            elif target in CLOUD_DOMAIN_SUFFIXES:
                ans = input(
                    f"\nEnter public domain for API (leave blank or type 'auto' to use {target.upper()}-generated "
                    f"domain [{cloud_suffix}]): "
                ).strip()
                domain = ans if ans else "auto"
            else:
                ans = input("\nEnter public domain for API (e.g. api.fuel-optimizer.com) [default: localhost]: ").strip()
                domain = ans if ans else "localhost"

        staging_domain = options.get("staging_domain") or cfg.get("staging_domain")
        if not staging_domain:
            if domain == "auto":
                staging_domain = "auto"
            elif non_interactive or domain == "localhost":
                staging_domain = f"staging-{domain}" if domain != "localhost" else "staging.localhost"
            else:
                staging_default = f"staging-{domain}"
                ans = input(f"Enter staging domain [default: {staging_default}]: ").strip()
                staging_domain = ans if ans else staging_default

        if domain == "auto":
            allowed_hosts = f"{cloud_suffix},localhost,127.0.0.1" if cloud_suffix else "localhost,127.0.0.1"
        elif domain != "localhost":
            extra = f",{cloud_suffix}" if cloud_suffix else ""
            allowed_hosts = f"{domain},{staging_domain}{extra},127.0.0.1,localhost"
        else:
            allowed_hosts = "localhost,127.0.0.1"

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

        # 4. Cloud-Specific Region & Project ID
        cloud_region = cfg.get(
            "region",
            "us-east-1" if target == "aws" else ("us-central1" if target in ("gcp", "k8s") else "eastus"),
        )
        gcp_project_id = options.get("gcp_project_id") or cfg.get("gcp_project_id") or ""
        if not gcp_project_id:
            default_proj = os.environ.get("GOOGLE_CLOUD_PROJECT") or self._detect_gcp_project_id() or "my-gcp-project"
            if target == "gcp" and not non_interactive:
                ans = input(f"\nGCP Project ID [default: {default_proj}]: ").strip()
                gcp_project_id = ans if ans else default_proj
            else:
                gcp_project_id = default_proj

        # 5. Database & Cache URLs (Supports provisioning PostgreSQL from scratch!)
        provision_db = bool(options.get("provision_db") or cfg.get("provision_db", False))
        db_password = options.get("db_password") or cfg.get("db_password") or ""
        database_url = options.get("database_url") or cfg.get("database_url", "")

        if not database_url and not provision_db and not non_interactive:
            ans = input(
                "\nPostgreSQL DATABASE_URL (type 'create' to provision PostgreSQL 16 from scratch on "
                f"{target.upper()}, or leave blank for embedded SQLite WAL): "
            ).strip()
            if ans.lower() in ("create", "new", "provision"):
                provision_db = True
            else:
                database_url = ans

        if database_url.lower() in ("create", "new", "provision"):
            provision_db = True
            database_url = ""

        if provision_db:
            if not db_password:
                db_password = generate_secure_db_password()
            if target == "gcp":
                database_url = (
                    f"postgres://spotter:{db_password}@/spotter_fuel"
                    f"?host=/cloudsql/{gcp_project_id}:{cloud_region}:fuel-route-optimizer-pg"
                )
                self.stdout.write(
                    self.style.SUCCESS(
                        "  ✓ Configured Terraform to create Cloud SQL for PostgreSQL 16 (fuel-route-optimizer-pg) from scratch."
                    )
                )
            elif target == "aws":
                database_url = f"postgres://spotter:{db_password}@fuel-route-optimizer-pg:5432/spotter_fuel"
                self.stdout.write(
                    self.style.SUCCESS(
                        "  ✓ Configured Terraform to create Amazon RDS PostgreSQL 16 (fuel-route-optimizer-pg) from scratch."
                    )
                )
            elif target == "azure":
                database_url = (
                    f"postgres://spotter:{db_password}@fuel-route-optimizer-pg.postgres.database.azure.com:5432"
                    f"/spotter_fuel?sslmode=require"
                )
                self.stdout.write(
                    self.style.SUCCESS(
                        "  ✓ Configured Terraform to create Azure PostgreSQL Flexible Server 16 (fuel-route-optimizer-pg) from scratch."
                    )
                )
            else:
                database_url = f"postgres://spotter:{db_password}@db:5432/spotter_fuel"
                self.stdout.write(
                    self.style.SUCCESS("  ✓ Configured new PostgreSQL 16 instance with auto-generated credentials.")
                )

        provision_redis = bool(options.get("provision_redis") or cfg.get("provision_redis", False))
        redis_url = options.get("redis_url") or cfg.get("redis_url", "")
        if not redis_url and not provision_redis and not non_interactive:
            ans = input(
                f"Redis REDIS_URL (type 'create' to provision Redis 7 from scratch on {target.upper()}, "
                "or leave blank for local memory cache): "
            ).strip()
            if ans.lower() in ("create", "new", "provision"):
                provision_redis = True
            else:
                redis_url = ans

        if redis_url.lower() in ("create", "new", "provision"):
            provision_redis = True
            redis_url = ""

        if provision_redis:
            if target == "gcp":
                redis_url = "redis://fuel-route-optimizer-redis:6379/0"
                self.stdout.write(
                    self.style.SUCCESS(
                        "  ✓ Configured Terraform to create Google Cloud Memorystore for Redis 7 (fuel-route-optimizer-redis) from scratch."
                    )
                )
            elif target == "aws":
                redis_url = "redis://fuel-optimizer-redis:6379/0"
                self.stdout.write(
                    self.style.SUCCESS(
                        "  ✓ Configured Terraform to create Amazon ElastiCache for Redis 7 (fuel-optimizer-redis) from scratch."
                    )
                )
            elif target == "azure":
                redis_url = "rediss://fuel-route-optimizer-redis.redis.cache.windows.net:6380/0"
                self.stdout.write(
                    self.style.SUCCESS(
                        "  ✓ Configured Terraform to create Azure Cache for Redis (fuel-route-optimizer-redis) from scratch."
                    )
                )
            else:
                redis_url = "redis://redis:6379/0"
                self.stdout.write(
                    self.style.SUCCESS("  ✓ Configured Redis 7 service (redis://redis:6379/0).")
                )

        # 6. Kubernetes Provider Prompt
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

        # 7. Automated DNS Configuration (Skipped if using cloud-generated domain 'auto')
        dns_provider = options.get("dns_provider") or cfg.get("dns_provider", "none")
        dns_target_ip = options.get("dns_target_ip") or cfg.get("dns_target_ip", "")
        route53_zone_id = options.get("route53_zone_id") or cfg.get("route53_zone_id", "")
        nc_user = options.get("namecheap_api_user") or cfg.get("namecheap_api_user", "")
        nc_key = options.get("namecheap_api_key") or cfg.get("namecheap_api_key", "")
        nc_client_ip = options.get("namecheap_client_ip") or cfg.get("namecheap_client_ip", "")
        nc_sandbox = bool(options.get("namecheap_sandbox") or cfg.get("namecheap_sandbox", False))

        if not non_interactive and dns_provider == "none" and domain not in ("localhost", "127.0.0.1", "auto"):
            self.stdout.write("\nAutomated DNS Record Provisioning:")
            self.stdout.write("  [1] none      : None / Manual DNS Record Setup (Default)")
            self.stdout.write("  [2] route53   : AWS Route 53 (Automated A-record creation via API)")
            self.stdout.write("  [3] namecheap : Namecheap DNS (Automated A-record creation via API)")
            ans = input("Select DNS provider [1-3, default: 1]: ").strip()
            dns_map = {
                "1": "none", "none": "none", "": "none",
                "2": "route53", "route53": "route53",
                "3": "namecheap", "namecheap": "namecheap",
            }
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

        # 8. Render Configuration Artifacts
        env_content = self._render_env_production(
            secret_key=secret_key,
            allowed_hosts=allowed_hosts,
            database_url=database_url,
            redis_url=redis_url,
            domain=domain,
            staging_domain=staging_domain,
            db_password=db_password,
        )

        gh_secrets_content = self._render_github_secrets_script(
            secret_key=secret_key,
            allowed_hosts=allowed_hosts,
            database_url=database_url,
            redis_url=redis_url,
            domain=domain,
            staging_domain=staging_domain,
            gcp_project_id=gcp_project_id,
            db_password=db_password,
            provision_db=provision_db,
            provision_redis=provision_redis,
        )

        if dry_run:
            self.stdout.write(self.style.SUCCESS("\n[DRY RUN] Generated .env.production Preview:"))
            redacted_env = re.sub(r"DJANGO_SECRET_KEY=.*", "DJANGO_SECRET_KEY=********[REDACTED]", env_content)
            if db_password:
                redacted_env = redacted_env.replace(db_password, "********[REDACTED_DB_PASS]")
            self.stdout.write(redacted_env)
            if (provision_db or provision_redis) and target in ("gcp", "aws", "azure"):
                self.stdout.write(
                    self.style.SUCCESS(
                        f"\n[DRY RUN] Would generate deploy/terraform/{target}/terraform.tfvars "
                        f"with provision_postgres = {str(provision_db).lower()}, provision_redis = {str(provision_redis).lower()}."
                    )
                )
            if dns_provider != "none" and domain not in ("localhost", "127.0.0.1", "auto"):
                tip = dns_target_ip or DNSManager.detect_public_ip() or "127.0.0.1"
                self.stdout.write(
                    self.style.SUCCESS(
                        f"\n[DRY RUN] Would update {dns_provider.upper()} A-records: {domain}, {staging_domain} -> {tip}"
                    )
                )
            self._print_dns_guide(
                domain, staging_domain, target, provision_db=provision_db, provision_redis=provision_redis
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"\n[DRY RUN] Target selected: {target} "
                    f"(K8s: {k8s_provider}, Provision DB: {provision_db}, Provision Redis: {provision_redis}). Dry run complete."
                )
            )
            return

        # Write .env.production with 0600 permissions
        env_path = settings.BASE_DIR / ".env.production"
        with open(env_path, "w", encoding="utf-8") as f:
            f.write(env_content)
        os.chmod(env_path, 0o600)

        # Write Terraform variables file if target uses Terraform (gcp, aws, azure)
        if target in ("gcp", "aws", "azure"):
            tfvars_path = settings.BASE_DIR / "deploy" / "terraform" / target / "terraform.tfvars"
            tfvars_path.parent.mkdir(parents=True, exist_ok=True)
            tfvars_content = self._render_terraform_tfvars(
                target=target,
                gcp_project_id=gcp_project_id,
                cloud_region=cloud_region,
                secret_key=secret_key,
                allowed_hosts=allowed_hosts,
                provision_db=provision_db,
                db_password=db_password,
                database_url=database_url,
                provision_redis=provision_redis,
                redis_url=redis_url,
            )
            with open(tfvars_path, "w", encoding="utf-8") as f:
                f.write(tfvars_content)
            os.chmod(tfvars_path, 0o600)
            self.stdout.write(
                self.style.SUCCESS(f"[1.5/3] Generated Terraform variables at {tfvars_path} (mode 0600).")
            )

        # Write deploy helper & deployment state manifest (for ./bin/spotter-destroy)
        deploy_dir = settings.BASE_DIR / "deploy" / "generated"
        deploy_dir.mkdir(parents=True, exist_ok=True)
        gh_script_path = deploy_dir / "set_github_secrets.sh"
        with open(gh_script_path, "w", encoding="utf-8") as f:
            f.write(gh_secrets_content)
        os.chmod(gh_script_path, 0o755)

        state_path = deploy_dir / "deployment_state.json"
        state_payload = {
            "target": target,
            "k8s_provider": k8s_provider,
            "cloud_region": cloud_region,
            "domain": domain,
            "staging_domain": staging_domain,
            "provision_db": provision_db,
            "provision_redis": provision_redis,
            "dns_provider": dns_provider,
            "dns_target_ip": dns_target_ip,
            "route53_zone_id": route53_zone_id,
            "namecheap_api_user": nc_user,
            "namecheap_client_ip": nc_client_ip or "127.0.0.1",
            "namecheap_sandbox": nc_sandbox,
        }
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(state_payload, f, indent=2)

        if target == "k8s":
            if k8s_provider == "gcp-gke":
                gke_tfvars_path = settings.BASE_DIR / "deploy" / "terraform" / "gcp-gke" / "terraform.tfvars"
                gke_tfvars_path.parent.mkdir(parents=True, exist_ok=True)
                gke_tfvars_path.write_text(
                    f'# Generated by manage.py deploy_wizard for target: k8s (gcp-gke)\n'
                    f'project_id = "{gcp_project_id}"\n'
                    f'region     = "{cloud_region}"\n',
                    encoding="utf-8",
                )
                os.chmod(gke_tfvars_path, 0o600)

            k8s_secrets_path = deploy_dir / "k8s-secrets.yaml"
            k8s_secrets_content = self._render_k8s_secrets_manifest(
                secret_key=secret_key,
                allowed_hosts=allowed_hosts,
                database_url=database_url,
                redis_url=redis_url,
                db_password=db_password or "spotter-k8s-db-password",
                domain=domain,
            )
            with open(k8s_secrets_path, "w", encoding="utf-8") as f:
                f.write(k8s_secrets_content)
            os.chmod(k8s_secrets_path, 0o600)

            k8s_deploy_script_path = deploy_dir / "deploy_k8s.sh"
            k8s_deploy_script_content = self._render_k8s_deploy_script(
                k8s_provider=k8s_provider,
                gcp_project_id=gcp_project_id,
                cloud_region=cloud_region,
            )
            with open(k8s_deploy_script_path, "w", encoding="utf-8") as f:
                f.write(k8s_deploy_script_content)
            os.chmod(k8s_deploy_script_path, 0o755)
            self.stdout.write(
                self.style.SUCCESS(
                    f"[2.2/3] Generated Kubernetes Secret manifest ({k8s_secrets_path}, mode 0600) "
                    f"and deploy script ({k8s_deploy_script_path})."
                )
            )
            if domain != "auto":
                self._patch_k8s_domains(domain, staging_domain)
                self.stdout.write(
                    self.style.SUCCESS("[2.5/3] Patched Kubernetes Ingress & Certificate manifests with domain.")
                )

        if dns_provider != "none" and domain not in ("localhost", "127.0.0.1", "auto"):
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
            sync_domains = [d for d in [domain, staging_domain] if d and d not in ("localhost", "127.0.0.1", "auto")]
            results = dns_mgr.sync_domains(sync_domains, target_ip=tip)
            for res in results:
                if res.success:
                    self.stdout.write(self.style.SUCCESS(f"  ✓ {res.domain} -> {res.target_ip} ({res.message})"))
                else:
                    self.stdout.write(self.style.WARNING(f"  ⚠ {res.domain} -> {res.message}"))

        self.stdout.write(self.style.SUCCESS(f"\n[1/3] Successfully wrote {env_path} (mode 0600)."))
        self.stdout.write(self.style.SUCCESS(f"[2/3] Generated GitHub Actions secret helper at {gh_script_path}."))

        # 9. Pre-flight verification
        self.stdout.write("\n[3/3] Running production pre-flight checks...")
        from apps.stations.models import FuelStation
        station_count = FuelStation.objects.filter(is_active_us=True).count()
        self.stdout.write(f"  * Active US Fuel Stations: {station_count} / 6626")

        from apps.routing.models import USPlaceGazetteer
        gaz_count = USPlaceGazetteer.objects.count()
        self.stdout.write(f"  * US Gazetteer Entries: {gaz_count}")

        self._print_dns_guide(
            domain, staging_domain, target, provision_db=provision_db, provision_redis=provision_redis
        )

        if target in ("gcp", "aws", "azure"):
            deploy_cmd_hint = f"terraform -chdir=deploy/terraform/{target} init && terraform -chdir=deploy/terraform/{target} apply"
        elif target == "k8s":
            deploy_cmd_hint = (
                f"./deploy/generated/deploy_k8s.sh "
                f"(or: kubectl apply -k deploy/k8s/overlays/{k8s_provider} && kubectl apply -n production -f deploy/generated/k8s-secrets.yaml)"
            )
        else:
            deploy_cmd_hint = "docker compose -f docker-compose.prod.yml up -d"
        workflow_map = {
            "gcp": "cd-gcp.yml",
            "aws": "cd-aws.yml",
            "azure": "cd-azure.yml",
            "k8s": "cd-k8s.yml",
            "docker-vps": "cd-vps.yml",
            "paas": "cd-paas.yml",
        }
        wf_name = workflow_map.get(target, "ci.yml")
        self.stdout.write(
            self.style.SUCCESS(
                f"\nDeployment configuration for target '{target}' is ready!\n"
                f"Note: Generated files (.env.production, terraform.tfvars) contain live secrets and are git-ignored (mode 0600) so secrets are never committed to Git.\n\n"
                f"Next Steps — Choose how to deploy:\n"
                f"  [Option A - Direct Local Deploy] : {deploy_cmd_hint}\n"
                f"  [Option B - GitHub Actions CI/CD]: ./deploy/generated/set_github_secrets.sh && gh workflow run {wf_name}\n\n"
                f"Tip: After your demonstration, run './bin/spotter-destroy' to tear down all resources and DNS records ($0/hr cost)."
            )
        )

    def _detect_gcp_project_id(self) -> str:
        """Attempt to detect the active GCP project from gcloud config."""
        import subprocess
        try:
            res = subprocess.run(
                ["gcloud", "config", "get-value", "project"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            val = res.stdout.strip()
            if res.returncode == 0 and val and val != "(unset)":
                return val
        except Exception:
            pass
        return ""

    def _print_dns_guide(
        self,
        domain: str,
        staging_domain: str,
        target: str,
        provision_db: bool = False,
        provision_redis: bool = False,
    ) -> None:
        self.stdout.write("\n" + "=" * 76)
        self.stdout.write(self.style.MIGRATE_HEADING("                  DNS & DOMAIN CONFIGURATION GUIDE"))
        self.stdout.write("=" * 76)
        if domain == "auto":
            suffix = CLOUD_DOMAIN_SUFFIXES.get(target, "")
            self.stdout.write(f"Using Cloud-Generated Public HTTPS Domain ({target.upper()}):")
            self.stdout.write(f"  * Allowed Domain Suffixes : {suffix}")
            self.stdout.write("  * Custom DNS Setup        : Not required (Cloud provider provisions HTTPS URL automatically)")
            if target == "gcp":
                self.stdout.write("  * Retrieve Cloud Run URL  : terraform -chdir=deploy/terraform/gcp output -raw url")
            elif target == "aws":
                self.stdout.write("  * Retrieve App Runner URL : terraform -chdir=deploy/terraform/aws output -raw service_url")
            elif target == "azure":
                self.stdout.write("  * Retrieve Container FQDN : terraform -chdir=deploy/terraform/azure output -raw fqdn")
        else:
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
        if provision_db:
            self.stdout.write(f"\nManaged PostgreSQL 16 Provisioning : ENABLED (from scratch on {target.upper()})")
        if provision_redis:
            self.stdout.write(f"Managed Redis 7 Cache Provisioning : ENABLED (from scratch on {target.upper()})")
        self.stdout.write("=" * 76 + "\n")

    def _render_terraform_tfvars(
        self,
        target: str,
        gcp_project_id: str,
        cloud_region: str,
        secret_key: str,
        allowed_hosts: str,
        provision_db: bool,
        db_password: str,
        database_url: str,
        provision_redis: bool = False,
        redis_url: str = "",
    ) -> str:
        lines = [
            f"# Generated by manage.py deploy_wizard for target: {target}",
        ]
        if target == "gcp":
            lines.append(f'project_id         = "{gcp_project_id}"')
            lines.append(f'region             = "{cloud_region}"')
        elif target == "aws":
            lines.append(f'aws_region         = "{cloud_region}"')
        elif target == "azure":
            lines.append(f'location           = "{cloud_region}"')

        lines.extend([
            f'django_secret_key  = "{secret_key}"',
            f'allowed_hosts      = "{allowed_hosts}"',
            f"provision_postgres = {'true' if provision_db else 'false'}",
            f'db_password        = "{db_password}"',
            f'database_url       = "{database_url if not provision_db else ""}"',
            f"provision_redis    = {'true' if provision_redis else 'false'}",
            f'redis_url          = "{redis_url if not provision_redis else ""}"',
            "",
        ])
        return "\n".join(lines)

    def _patch_k8s_domains(self, domain: str, staging_domain: str) -> None:
        """Patch Kubernetes overlay manifests with user-specified domains."""
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

        for overlay_name in ("aws-eks", "azure-aks", "generic"):
            ing_path = settings.BASE_DIR / "deploy" / "k8s" / "overlays" / overlay_name / "ingress_patch.yaml"
            if ing_path.exists():
                content = ing_path.read_text(encoding="utf-8")
                updated = re.sub(r"-\s*host:\s*\S+", f"- host: {domain}", content)
                ing_path.write_text(updated, encoding="utf-8")

    def _render_k8s_secrets_manifest(
        self,
        secret_key: str,
        allowed_hosts: str,
        database_url: str,
        redis_url: str,
        db_password: str,
        domain: str,
    ) -> str:
        ssl_redirect = "False" if domain in ("localhost", "127.0.0.1", "auto") else "True"
        extra_wildcard = ",*" if domain in ("localhost", "127.0.0.1", "auto") else ""
        hosts_with_svc = f"{allowed_hosts},fuel-route-optimizer{extra_wildcard}"
        return f"""apiVersion: v1
kind: ConfigMap
metadata:
  name: fuel-optimizer-config
  namespace: production
  labels:
    app.kubernetes.io/name: fuel-route-optimizer
data:
  DJANGO_SETTINGS_MODULE: "config.settings.production"
  ALLOWED_HOSTS: "{hosts_with_svc}"
  SECURE_SSL_REDIRECT: "{ssl_redirect}"
  SECURE_HSTS_SECONDS: "31536000"
  EXTERNAL_CONNECT_TIMEOUT_SEC: "2.0"
  EXTERNAL_READ_TIMEOUT_SEC: "4.0"
---
apiVersion: v1
kind: Secret
metadata:
  name: fuel-optimizer-secrets
  namespace: production
  labels:
    app.kubernetes.io/name: fuel-route-optimizer
type: Opaque
stringData:
  DJANGO_SECRET_KEY: "{secret_key}"
  POSTGRES_DB: "spotter_fuel"
  POSTGRES_USER: "spotter"
  POSTGRES_PASSWORD: "{db_password}"
  DATABASE_URL: "{database_url}"
  REDIS_URL: "{redis_url}"
"""

    def _render_k8s_deploy_script(
        self,
        k8s_provider: str,
        gcp_project_id: str = "",
        cloud_region: str = "us-central1",
    ) -> str:
        proj = gcp_project_id or "aldianfazrihady"
        return f"""#!/usr/bin/env bash
# Generated by manage.py deploy_wizard for Kubernetes ({k8s_provider})
set -e

CTX="$(kubectl config current-context 2>/dev/null || true)"
echo "Active Kubernetes context: ${{CTX:-unknown}}"

if [ -n "$CTX" ] && ([[ "$CTX" == *"minikube"* ]] || [[ "$CTX" == *"kind"* ]] || (command -v minikube >/dev/null 2>&1 && minikube profile list 2>/dev/null | grep -q "$CTX")); then
  echo "=== [0/3] Preparing container image for local Kubernetes cluster ($CTX) ==="
  if ! docker image inspect ghcr.io/aldian/fuel-route-optimizer:latest >/dev/null 2>&1; then
    docker build -t ghcr.io/aldian/fuel-route-optimizer:latest .
  fi
  if [[ "$CTX" == *"kind"* ]]; then
    CLUSTER_NAME="${{CTX#kind-}}"
    kind load docker-image ghcr.io/aldian/fuel-route-optimizer:latest --name "${{CLUSTER_NAME:-kind}}"
  else
    minikube image load --overwrite=true ghcr.io/aldian/fuel-route-optimizer:latest -p "$CTX"
  fi
elif [ "{k8s_provider}" = "gcp-gke" ]; then
  echo "=== [0a/3] Ensuring GKE cluster (fuel-route-optimizer-cluster) exists in GCP project {proj} ({cloud_region}-a) ==="
  if ! gcloud container clusters describe fuel-route-optimizer-cluster --zone "{cloud_region}-a" --project "{proj}" >/dev/null 2>&1; then
    terraform -chdir=deploy/terraform/gcp-gke init
    terraform -chdir=deploy/terraform/gcp-gke apply -auto-approve
  fi
  gcloud container clusters get-credentials fuel-route-optimizer-cluster --zone "{cloud_region}-a" --project "{proj}"

  echo "=== [0b/3] Building & pushing container image to Artifact Registry ({cloud_region}-docker.pkg.dev/{proj}/fuel-route-optimizer/app:latest) ==="
  gcloud auth configure-docker {cloud_region}-docker.pkg.dev --quiet
  docker build -t {cloud_region}-docker.pkg.dev/{proj}/fuel-route-optimizer/app:latest .
  docker push {cloud_region}-docker.pkg.dev/{proj}/fuel-route-optimizer/app:latest
fi

echo "=== [1/3] Applying Kustomize overlay: deploy/k8s/overlays/{k8s_provider} ==="
kubectl apply -k "deploy/k8s/overlays/{k8s_provider}"

echo "=== [2/3] Applying production Secret & ConfigMap ==="
kubectl apply -n production -f "deploy/generated/k8s-secrets.yaml"

echo "=== [3/3] Waiting for database, Redis, and API rollout in namespace 'production' ==="
kubectl rollout status statefulset/db -n production --timeout=300s || true
kubectl rollout status deployment/redis -n production --timeout=120s || true
kubectl rollout restart deployment/fuel-route-optimizer -n production
kubectl rollout status deployment/fuel-route-optimizer -n production --timeout=300s

EXTERNAL_IP=""
for i in $(seq 1 24); do
  EXTERNAL_IP="$(kubectl get svc fuel-route-optimizer -n production -o jsonpath='{{.status.loadBalancer.ingress[0].ip}}' 2>/dev/null || true)"
  if [ -n "$EXTERNAL_IP" ]; then
    break
  fi
  sleep 5
done

echo ""
echo "✓ Kubernetes deployment is live in namespace 'production'!"
echo "  Check pods   : kubectl get pods -n production"
if [ -n "$EXTERNAL_IP" ]; then
  echo "  External URL : http://$EXTERNAL_IP/api/docs/"
  echo "  Route Map    : http://$EXTERNAL_IP/api/v1/route/map/?start=New+York,+NY&finish=Los+Angeles,+CA"
  echo "  SRE Console  : http://$EXTERNAL_IP/api/v1/sre/dashboard/"
else
  echo "  Port-forward : kubectl port-forward -n production svc/fuel-route-optimizer 8080:80"
fi
"""

    def _render_env_production(
        self,
        secret_key: str,
        allowed_hosts: str,
        database_url: str,
        redis_url: str,
        domain: str,
        staging_domain: str = "",
        db_password: str = "",
    ) -> str:
        extra_pg = f"\nPOSTGRES_PASSWORD={db_password}" if db_password else ""
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
DATABASE_URL={database_url}{extra_pg}
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
        gcp_project_id: str = "",
        db_password: str = "",
        provision_db: bool = False,
        provision_redis: bool = False,
    ) -> str:
        return f"""#!/usr/bin/env bash
# Helper script to set GitHub Actions Secrets via gh CLI
# Usage: ./deploy/generated/set_github_secrets.sh

set -e

echo "Setting GitHub Actions Secrets..."
gh secret set DJANGO_SECRET_KEY --body "{secret_key}"
gh secret set ALLOWED_HOSTS --body "{allowed_hosts}"
gh secret set PROVISION_POSTGRES --body "{'true' if provision_db else 'false'}"
gh secret set PROVISION_REDIS --body "{'true' if provision_redis else 'false'}"
if [ -n "{gcp_project_id}" ]; then
  gh secret set GCP_PROJECT_ID --body "{gcp_project_id}"
fi
if [ -n "{db_password}" ]; then
  gh secret set DB_PASSWORD --body "{db_password}"
fi
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

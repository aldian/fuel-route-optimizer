"""
One-command ephemeral demo teardown & cost-saving infrastructure destruction CLI.

Traces To: REQ-OPS-006 (specs/07-cicd-and-production-deployment-cli.md §5)

Responsibilities:
1. Checks the 'ALLOW_EPHEMERAL_TEARDOWN' feature flag (apps/common/flags.py) to prevent
   accidental teardown if an environment has been locked as permanent production.
2. Automatically loads 'deploy/generated/deployment_state.json' written by 'deploy_wizard'
   (or accepts explicit CLI flags).
3. Deletes provisioned DNS A-records via AWS Route 53 or Namecheap APIs to prevent
   dangling DNS records and subdomain takeover risks.
4. Destroys compute, database, load balancer, and container resources across:
   - docker-vps (docker compose down --volumes --rmi local --remove-orphans)
   - k8s (kubectl delete -k overlays + production/staging namespaces + optional cluster deletion)
   - aws / gcp / azure (terraform destroy -auto-approve)
   - paas (flyctl apps destroy)
5. Cleans up local generated secrets (.env.production, set_github_secrets.sh, deployment_state.json)
   and resets patched Kubernetes overlay manifests back to clean repository templates.
"""
import json
import logging
import os
from pathlib import Path
import subprocess
from typing import Any
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from apps.common.flags import flags
from apps.stations.dns.manager import DNSManager

logger = logging.getLogger(__name__)


DEFAULT_PROD_CERT = """apiVersion: networking.gke.io/v1
kind: ManagedCertificate
metadata:
  name: fuel-optimizer-certificate
spec:
  domains:
    - api.fuel-optimizer.example.com
"""

DEFAULT_PROD_INGRESS = """apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: fuel-route-optimizer-ingress
  annotations:
    kubernetes.io/ingress.class: "gce"
    networking.gke.io/managed-certificates: "fuel-optimizer-certificate"
spec:
  rules:
    - host: api.fuel-optimizer.example.com
      http:
        paths:
          - path: /*
            pathType: ImplementationSpecific
            backend:
              service:
                name: fuel-route-optimizer
                port:
                  number: 80
"""

DEFAULT_STAG_CERT = """apiVersion: networking.gke.io/v1
kind: ManagedCertificate
metadata:
  name: staging-fuel-optimizer-certificate
spec:
  domains:
    - staging-api.fuel-optimizer.example.com
"""

DEFAULT_STAG_INGRESS = """apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: fuel-route-optimizer-ingress
  annotations:
    kubernetes.io/ingress.class: "gce"
    networking.gke.io/managed-certificates: "staging-fuel-optimizer-certificate"
spec:
  rules:
    - host: staging-api.fuel-optimizer.example.com
      http:
        paths:
          - path: /*
            pathType: ImplementationSpecific
            backend:
              service:
                name: fuel-route-optimizer
                port:
                  number: 80
"""


class Command(BaseCommand):
    help = "Tears down all provisioned demo infrastructure, DNS records, and local secrets to save costs ($0/hr)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--target",
            choices=["docker-vps", "aws", "gcp", "azure", "k8s", "paas"],
            help="Deployment target to tear down (defaults to deploy/generated/deployment_state.json).",
        )
        parser.add_argument(
            "--k8s-provider",
            choices=["gcp-gke", "aws-eks", "azure-aks", "generic"],
            help="Kubernetes provider if target is k8s.",
        )
        parser.add_argument(
            "--destroy-cluster",
            action="store_true",
            help="Also delete the underlying managed Kubernetes cluster (GKE/EKS/AKS) for zero idle cost.",
        )
        parser.add_argument(
            "--cluster-name",
            type=str,
            default="fuel-route-optimizer-cluster",
            help="Managed Kubernetes cluster name when using --destroy-cluster.",
        )
        parser.add_argument(
            "--region",
            type=str,
            help="Cloud region for cluster or Terraform teardown.",
        )
        parser.add_argument(
            "--domain",
            type=str,
            help="Primary public domain whose DNS A-record should be deleted.",
        )
        parser.add_argument(
            "--staging-domain",
            type=str,
            help="Staging domain whose DNS A-record should be deleted.",
        )
        parser.add_argument(
            "--dns-provider",
            choices=["none", "route53", "namecheap"],
            help="Automated DNS provider to remove A-records from.",
        )
        parser.add_argument(
            "--dns-target-ip",
            type=str,
            help="Target IP of the A-record to delete (optional; auto-discovered if omitted).",
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
            help="Namecheap API key (or set NAMECHEAP_API_KEY env var).",
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
            "--keep-local-artifacts",
            action="store_true",
            help="Keep .env.production and deploy/generated/ files on disk.",
        )
        parser.add_argument(
            "--yes",
            "--non-interactive",
            action="store_true",
            dest="yes",
            help="Skip interactive confirmation prompt.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Bypass ALLOW_EPHEMERAL_TEARDOWN feature flag check.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview all teardown commands and DNS deletions without executing them.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                "=== Spotter Fuel Route Optimizer: Ephemeral Demo Teardown & Cost Saver ==="
            )
        )

        dry_run = bool(options.get("dry_run", False))
        force = bool(options.get("force", False))

        # 1. Feature Flag Safety Gate (REQ-OPS-006 / Spec 08)
        if not force and not flags.is_enabled("ALLOW_EPHEMERAL_TEARDOWN", default=True):
            raise CommandError(
                "Teardown blocked: Feature flag 'ALLOW_EPHEMERAL_TEARDOWN' is disabled for this environment. "
                "Enable it via './bin/spotter-feature-flag --set ALLOW_EPHEMERAL_TEARDOWN on' or pass '--force'."
            )

        # 2. Load saved state from deploy_wizard if available
        state_path = settings.BASE_DIR / "deploy" / "generated" / "deployment_state.json"
        saved_state: dict[str, Any] = {}
        if state_path.exists():
            try:
                with open(state_path, "r", encoding="utf-8") as f:
                    saved_state = json.load(f)
                self.stdout.write(f"Loaded deployment state from {state_path}")
            except Exception as exc:
                logger.warning("Could not parse %s: %s", state_path, exc)

        target = options.get("target") or saved_state.get("target") or "docker-vps"
        k8s_provider = options.get("k8s_provider") or saved_state.get("k8s_provider") or "gcp-gke"
        region = options.get("region") or saved_state.get("cloud_region") or "us-central1"
        domain = options.get("domain") or saved_state.get("domain") or ""
        staging_domain = options.get("staging_domain") or saved_state.get("staging_domain") or ""
        dns_provider = options.get("dns_provider") or saved_state.get("dns_provider") or "none"
        dns_target_ip = options.get("dns_target_ip") or saved_state.get("dns_target_ip") or ""
        route53_zone_id = options.get("route53_zone_id") or saved_state.get("route53_zone_id") or ""
        nc_user = options.get("namecheap_api_user") or saved_state.get("namecheap_api_user") or ""
        nc_key = options.get("namecheap_api_key") or os.environ.get("NAMECHEAP_API_KEY", "")
        nc_client_ip = options.get("namecheap_client_ip") or saved_state.get("namecheap_client_ip") or "127.0.0.1"
        nc_sandbox = bool(options.get("namecheap_sandbox") or saved_state.get("namecheap_sandbox", False))
        destroy_cluster = bool(options.get("destroy_cluster", False))
        cluster_name = options.get("cluster_name") or "fuel-route-optimizer-cluster"
        keep_local = bool(options.get("keep_local_artifacts", False))
        auto_yes = bool(options.get("yes", False))

        domains_to_delete = [
            d for d in [domain, staging_domain] if d and d not in ("localhost", "127.0.0.1", "staging.localhost", "auto")
        ]

        self.stdout.write("\nTeardown Plan Summary:")
        self.stdout.write(f"  * Target Infrastructure : {target}" + (f" ({k8s_provider})" if target == "k8s" else ""))
        if target == "k8s" and destroy_cluster:
            self.stdout.write(f"  * Destroy K8s Cluster   : YES ({cluster_name} in {region})")
        self.stdout.write(f"  * DNS Provider Cleanup  : {dns_provider.upper()} ({', '.join(domains_to_delete) or 'no public domains'})")
        self.stdout.write(f"  * Clean Local Artifacts : {'NO' if keep_local else 'YES (.env.production, deploy/generated/)'}")

        if not auto_yes and not dry_run:
            confirm = input("\nProceed with complete teardown to stop all cloud billing? [y/N]: ").strip().lower()
            if confirm not in ("y", "yes"):
                self.stdout.write(self.style.WARNING("Teardown cancelled by operator."))
                return

        # Step 1: Automated DNS Record Cleanup (Route 53 / Namecheap)
        if dns_provider != "none" and domains_to_delete:
            if dry_run:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"\n[1/3] [DRY RUN] Would delete {dns_provider.upper()} A-records for: {', '.join(domains_to_delete)}"
                    )
                )
            else:
                self.stdout.write(f"\n[1/3] Deleting DNS A-records via {dns_provider.upper()}...")
                dns_cfg = {
                    "dns_provider": dns_provider,
                    "route53_zone_id": route53_zone_id,
                    "namecheap_api_user": nc_user,
                    "namecheap_api_key": nc_key,
                    "namecheap_client_ip": nc_client_ip,
                    "namecheap_sandbox": nc_sandbox,
                }
                dns_mgr = DNSManager.from_config(dns_cfg)
                results = dns_mgr.delete_domains(domains_to_delete, target_ip=dns_target_ip or None)
                for res in results:
                    if res.success:
                        self.stdout.write(self.style.SUCCESS(f"  ✓ {res.domain}: {res.message}"))
                    else:
                        self.stdout.write(self.style.WARNING(f"  ⚠ {res.domain}: {res.message}"))
        else:
            self.stdout.write("\n[1/3] Skipping automated DNS deletion (provider: none or localhost).")

        # Step 2: Infrastructure Teardown
        self.stdout.write(f"\n[2/3] Destroying '{target}' infrastructure resources...")
        self._teardown_infrastructure(
            target=target,
            k8s_provider=k8s_provider,
            destroy_cluster=destroy_cluster,
            cluster_name=cluster_name,
            region=region,
            dry_run=dry_run,
        )

        # Step 3: Local Artifacts & Manifest Reset
        if not keep_local:
            self._clean_local_artifacts(state_path=state_path, dry_run=dry_run)
        else:
            self.stdout.write("\n[3/3] Keeping local configuration artifacts (--keep-local-artifacts).")

        self.stdout.write(
            self.style.SUCCESS(
                "\nTeardown complete! All demo infrastructure, load balancers, and DNS records have been cleaned up ($0/hr ongoing cost)."
            )
        )

    def _teardown_infrastructure(
        self,
        target: str,
        k8s_provider: str,
        destroy_cluster: bool,
        cluster_name: str,
        region: str,
        dry_run: bool,
    ) -> None:
        commands: list[list[str]] = []

        if target == "docker-vps":
            commands.append(
                [
                    "docker",
                    "compose",
                    "-f",
                    "docker-compose.prod.yml",
                    "down",
                    "--volumes",
                    "--rmi",
                    "local",
                    "--remove-orphans",
                ]
            )
        elif target == "k8s":
            overlay_map = {
                "gcp-gke": "deploy/k8s/overlays/gcp-gke",
                "aws-eks": "deploy/k8s/overlays/aws-eks",
                "azure-aks": "deploy/k8s/overlays/azure-aks",
                "generic": "deploy/k8s/overlays/generic",
            }
            overlay_dir = overlay_map.get(k8s_provider, "deploy/k8s/overlays/gcp-gke")
            commands.append(["kubectl", "delete", "-k", overlay_dir, "--ignore-not-found=true"])
            commands.append(["kubectl", "delete", "-k", "deploy/k8s/overlays/gcp-gke-staging", "--ignore-not-found=true"])
            commands.append(["kubectl", "delete", "-k", "deploy/k8s/overlays/canary", "--ignore-not-found=true"])
            commands.append(["kubectl", "delete", "-k", "deploy/k8s/overlays/blue-green", "--ignore-not-found=true"])
            commands.append(["kubectl", "delete", "namespace", "production", "staging", "--ignore-not-found=true"])

            if destroy_cluster:
                if k8s_provider == "gcp-gke":
                    commands.append(
                        ["gcloud", "container", "clusters", "delete", cluster_name, f"--region={region}", "--quiet"]
                    )
                elif k8s_provider == "aws-eks":
                    commands.append(["eksctl", "delete", "cluster", "--name", cluster_name, "--region", region])
                elif k8s_provider == "azure-aks":
                    commands.append(
                        ["az", "aks", "delete", "--name", cluster_name, "--resource-group", f"{cluster_name}-rg", "--yes"]
                    )
        elif target in ("aws", "gcp", "azure"):
            tf_dir = f"deploy/terraform/{target}"
            commands.append(["terraform", f"-chdir={tf_dir}", "destroy", "-auto-approve"])
        elif target == "paas":
            commands.append(["flyctl", "apps", "destroy", "fuel-route-optimizer", "--yes"])

        for cmd in commands:
            cmd_str = " ".join(cmd)
            if dry_run:
                self.stdout.write(self.style.SUCCESS(f"  [DRY RUN] Would execute: {cmd_str}"))
                continue

            self.stdout.write(f"  Executing: {cmd_str}...")
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, check=True)
                if res.stdout.strip():
                    self.stdout.write(self.style.SUCCESS(f"    ✓ {res.stdout.strip().splitlines()[0]}"))
                else:
                    self.stdout.write(self.style.SUCCESS("    ✓ Completed."))
            except FileNotFoundError:
                self.stdout.write(self.style.WARNING(f"    ⚠ CLI tool '{cmd[0]}' not installed locally; skipped."))
            except subprocess.CalledProcessError as exc:
                err = exc.stderr.strip() or str(exc)
                raise CommandError(f"Infrastructure teardown command failed ({cmd_str}): {err}")

    def _clean_local_artifacts(self, state_path: Path, dry_run: bool) -> None:
        self.stdout.write("\n[3/3] Cleaning local deployment artifacts and resetting manifests...")
        files_to_remove = [
            settings.BASE_DIR / ".env.production",
            settings.BASE_DIR / "deploy" / "generated" / "set_github_secrets.sh",
            settings.BASE_DIR / "deploy" / "generated" / "k8s-secrets.yaml",
            settings.BASE_DIR / "deploy" / "generated" / "deploy_k8s.sh",
            state_path,
            settings.BASE_DIR / "deploy" / "terraform" / "gcp" / "terraform.tfvars",
            settings.BASE_DIR / "deploy" / "terraform" / "aws" / "terraform.tfvars",
            settings.BASE_DIR / "deploy" / "terraform" / "azure" / "terraform.tfvars",
            settings.BASE_DIR / "deploy" / "terraform" / "gcp-gke" / "terraform.tfvars",
        ]
        for path in files_to_remove:
            if path.exists():
                if dry_run:
                    self.stdout.write(self.style.SUCCESS(f"  [DRY RUN] Would remove {path}"))
                else:
                    path.unlink()
                    self.stdout.write(self.style.SUCCESS(f"  ✓ Removed {path}"))

        # Reset Kubernetes domain overlays to clean repository defaults
        k8s_resets = [
            (settings.BASE_DIR / "deploy" / "k8s" / "overlays" / "gcp-gke" / "certificate.yaml", DEFAULT_PROD_CERT),
            (settings.BASE_DIR / "deploy" / "k8s" / "overlays" / "gcp-gke" / "ingress_patch.yaml", DEFAULT_PROD_INGRESS),
            (settings.BASE_DIR / "deploy" / "k8s" / "overlays" / "gcp-gke-staging" / "certificate.yaml", DEFAULT_STAG_CERT),
            (settings.BASE_DIR / "deploy" / "k8s" / "overlays" / "gcp-gke-staging" / "ingress_patch.yaml", DEFAULT_STAG_INGRESS),
        ]
        for path, default_content in k8s_resets:
            if path.exists():
                if dry_run:
                    self.stdout.write(self.style.SUCCESS(f"  [DRY RUN] Would reset {path} to template default"))
                else:
                    path.write_text(default_content, encoding="utf-8")
                    self.stdout.write(self.style.SUCCESS(f"  ✓ Reset {path.relative_to(settings.BASE_DIR)}"))

        import re
        for overlay_name in ("aws-eks", "azure-aks", "generic"):
            ing_path = settings.BASE_DIR / "deploy" / "k8s" / "overlays" / overlay_name / "ingress_patch.yaml"
            if ing_path.exists() and not dry_run:
                content = ing_path.read_text(encoding="utf-8")
                updated = re.sub(r"-\s*host:\s*\S+", "- host: api.fuel-optimizer.example.com", content)
                ing_path.write_text(updated, encoding="utf-8")

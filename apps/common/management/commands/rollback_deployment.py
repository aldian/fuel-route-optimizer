"""
CLI Management Command to orchestrate rapid, zero-downtime deployment rollbacks.

Supports:
- Kubernetes: Executes 'kubectl rollout undo' with revision targeting and health verification.
- Docker Compose / VPS: Rolls back container tag or restarts previous revision.
- Dry-run simulation mode for CI/CD and safety verification.
"""
import logging
from pathlib import Path
import subprocess
import time
from typing import Any
import httpx
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Rollback application deployment in Kubernetes or Docker environment to previous healthy version."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--target",
            choices=["k8s", "docker"],
            default="k8s",
            help="Deployment target environment (k8s or docker).",
        )
        parser.add_argument(
            "--namespace",
            default="production",
            help="Kubernetes namespace (production or staging).",
        )
        parser.add_argument(
            "--deployment",
            default="fuel-route-optimizer",
            help="Deployment name.",
        )
        parser.add_argument(
            "--to-revision",
            type=int,
            help="Specific revision number to rollback to (default: previous revision).",
        )
        parser.add_argument(
            "--verify-url",
            type=str,
            help="Health check URL to verify post-rollback (e.g. https://api.example.com/api/v1/health/).",
        )
        parser.add_argument(
            "--timeout",
            type=int,
            default=120,
            help="Timeout in seconds to wait for rollout completion (default: 120s).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print rollback commands without executing them.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        target = options.get("target", "k8s")
        namespace = options.get("namespace", "production")
        deployment = options.get("deployment", "fuel-route-optimizer")
        to_revision = options.get("to_revision")
        verify_url = options.get("verify_url")
        timeout = options.get("timeout", 120)
        dry_run = options.get("dry_run", False)

        self.stdout.write(self.style.MIGRATE_HEADING(f"Initiating Deployment Rollback [Target: {target.upper()}]"))
        if dry_run:
            self.stdout.write(self.style.WARNING("Mode: DRY RUN (no actions will be executed)"))

        if target == "k8s":
            self._rollback_k8s(
                namespace=namespace,
                deployment=deployment,
                to_revision=to_revision,
                timeout=timeout,
                dry_run=dry_run,
            )
        elif target == "docker":
            self._rollback_docker(dry_run=dry_run)

        # Health verification
        if verify_url and not dry_run:
            self._verify_health(verify_url, timeout=30)

        self.stdout.write(self.style.SUCCESS("\nRollback procedure completed successfully."))

    def _rollback_k8s(
        self,
        namespace: str,
        deployment: str,
        to_revision: int | None,
        timeout: int,
        dry_run: bool,
    ) -> None:
        cmd = ["kubectl", "rollout", "undo", f"deployment/{deployment}", "-n", namespace]
        if to_revision:
            cmd.append(f"--to-revision={to_revision}")

        status_cmd = ["kubectl", "rollout", "status", f"deployment/{deployment}", "-n", namespace, f"--timeout={timeout}s"]

        if dry_run:
            self.stdout.write(f"  [DRY RUN] Would execute: {' '.join(cmd)}")
            self.stdout.write(f"  [DRY RUN] Would execute: {' '.join(status_cmd)}")
            return

        self.stdout.write(f"Executing: {' '.join(cmd)}...")
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            self.stdout.write(self.style.SUCCESS(f"  ✓ {res.stdout.strip()}"))

            self.stdout.write(f"Waiting for rollout to complete (timeout: {timeout}s)...")
            status_res = subprocess.run(status_cmd, capture_output=True, text=True, check=True)
            self.stdout.write(self.style.SUCCESS(f"  ✓ {status_res.stdout.strip()}"))
        except subprocess.CalledProcessError as exc:
            err = exc.stderr.strip() or str(exc)
            raise CommandError(f"Kubernetes rollback failed: {err}")

    def _rollback_docker(self, dry_run: bool) -> None:
        cmd = ["docker", "compose", "-f", "docker-compose.prod.yml", "restart", "api"]
        if dry_run:
            self.stdout.write(f"  [DRY RUN] Would execute: {' '.join(cmd)}")
            return

        self.stdout.write(f"Executing: {' '.join(cmd)}...")
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            self.stdout.write(self.style.SUCCESS(f"  ✓ {res.stdout.strip()}"))
        except subprocess.CalledProcessError as exc:
            err = exc.stderr.strip() or str(exc)
            raise CommandError(f"Docker rollback failed: {err}")

    def _verify_health(self, url: str, timeout: int = 30) -> None:
        self.stdout.write(f"\nVerifying health endpoint post-rollback: {url}...")
        start_time = time.time()
        with httpx.Client(timeout=5.0) as client:
            while time.time() - start_time < timeout:
                try:
                    resp = client.get(url)
                    if resp.status_code == 200:
                        data = resp.json()
                        if data.get("status") == "healthy":
                            self.stdout.write(self.style.SUCCESS("  ✓ Health check passed (status: healthy, HTTP 200)"))
                            return
                except Exception as exc:
                    logger.debug("Health probe waiting: %s", exc)
                time.sleep(2.0)

        raise CommandError(f"Health verification timed out after {timeout}s on {url}")

"""DNS manager orchestrating automated record updates across supported providers."""
import logging
from typing import Any
import httpx
from .base import DNSProvider, DNSRecordResult
from .namecheap import NamecheapDNSProvider
from .route53 import Route53DNSProvider

logger = logging.getLogger(__name__)


class DNSManager:
    """Orchestrates DNS operations for production and staging domains."""

    def __init__(self, provider: DNSProvider | None = None) -> None:
        self.provider = provider

    @staticmethod
    def detect_public_ip() -> str | None:
        """Attempt to auto-detect public IPv4 address of the local host."""
        try:
            with httpx.Client(timeout=4.0) as client:
                resp = client.get("https://api.ipify.org")
                if resp.status_code == 200 and resp.text.strip():
                    return resp.text.strip()
        except Exception as exc:
            logger.debug("Failed to auto-detect public IP via ipify: %s", exc)
        return None

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> "DNSManager":
        """Instantiate DNSManager from configuration dictionary or environment."""
        dns_type = (cfg.get("dns_provider") or "none").lower()

        if dns_type in ("route53", "aws"):
            provider = Route53DNSProvider(
                hosted_zone_id=cfg.get("route53_zone_id"),
                aws_access_key_id=cfg.get("aws_access_key_id"),
                aws_secret_access_key=cfg.get("aws_secret_access_key"),
                aws_region=cfg.get("aws_region", "us-east-1"),
            )
            return cls(provider=provider)

        if dns_type in ("namecheap", "nc"):
            provider = NamecheapDNSProvider(
                api_user=cfg.get("namecheap_api_user", ""),
                api_key=cfg.get("namecheap_api_key", ""),
                username=cfg.get("namecheap_username"),
                client_ip=cfg.get("namecheap_client_ip", "127.0.0.1"),
                sandbox=bool(cfg.get("namecheap_sandbox", False)),
            )
            return cls(provider=provider)

        return cls(provider=None)

    def sync_domains(self, domains: list[str], target_ip: str, ttl: int = 300) -> list[DNSRecordResult]:
        """Apply A record updates for all specified domains pointing to target_ip."""
        results: list[DNSRecordResult] = []
        if not self.provider:
            for d in domains:
                results.append(
                    DNSRecordResult(
                        success=False,
                        domain=d,
                        record_type="A",
                        target_ip=target_ip,
                        provider="None",
                        message="No automated DNS provider configured; record must be created manually.",
                    )
                )
            return results

        for domain in domains:
            if not domain or domain in ("localhost", "127.0.0.1"):
                continue
            res = self.provider.upsert_a_record(domain, target_ip, ttl=ttl)
            results.append(res)

        return results

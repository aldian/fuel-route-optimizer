"""Abstract base class and domain parsing utilities for DNS providers."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
import re


@dataclass(slots=True, frozen=True)
class DNSRecordResult:
    """Result of a DNS record change operation."""

    success: bool
    domain: str
    record_type: str
    target_ip: str
    provider: str
    message: str


def extract_domain_parts(fqdn: str) -> tuple[str, str, str]:
    """
    Parse a fully qualified domain name into (subdomain, sld, tld).
    
    Examples:
      'api.example.com' -> ('api', 'example', 'com')
      'example.com'     -> ('@', 'example', 'com')
      'staging.sub.example.com' -> ('staging.sub', 'example', 'com')
    """
    cleaned = fqdn.strip().rstrip(".").lower()
    parts = cleaned.split(".")
    if len(parts) < 2:
        return ("@", cleaned, "")
    if len(parts) == 2:
        return ("@", parts[0], parts[1])
    
    # Common two-part TLDs (e.g. co.uk, com.au)
    two_part_tlds = {"co.uk", "com.au", "co.nz", "co.jp", "com.br", "org.uk", "gov.uk"}
    last_two = f"{parts[-2]}.{parts[-1]}"
    if last_two in two_part_tlds and len(parts) >= 3:
        tld = last_two
        sld = parts[-3]
        subdomain = ".".join(parts[:-3]) if len(parts) > 3 else "@"
    else:
        tld = parts[-1]
        sld = parts[-2]
        subdomain = ".".join(parts[:-2])

    return (subdomain if subdomain else "@", sld, tld)


class DNSProvider(ABC):
    """Abstract interface for automated DNS providers."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Name of the DNS provider."""
        ...

    @abstractmethod
    def upsert_a_record(self, domain: str, target_ip: str, ttl: int = 300) -> DNSRecordResult:
        """Create or update an A record for the given domain pointing to target_ip."""
        ...

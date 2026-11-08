"""DNS automation providers for automatic domain record management."""
from .base import DNSProvider, DNSRecordResult
from .manager import DNSManager
from .namecheap import NamecheapDNSProvider
from .route53 import Route53DNSProvider

__all__ = [
    "DNSProvider",
    "DNSRecordResult",
    "DNSManager",
    "Route53DNSProvider",
    "NamecheapDNSProvider",
]

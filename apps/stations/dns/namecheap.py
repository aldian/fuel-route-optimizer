"""Namecheap automated DNS provider via Namecheap XML API."""
import logging
from typing import Any
import xml.etree.ElementTree as ET
import httpx
from .base import DNSProvider, DNSRecordResult, extract_domain_parts

logger = logging.getLogger(__name__)


class NamecheapDNSProvider(DNSProvider):
    """Manages DNS records using the Namecheap API."""

    PROD_ENDPOINT = "https://api.namecheap.com/xml.response"
    SANDBOX_ENDPOINT = "https://api.sandbox.namecheap.com/xml.response"

    def __init__(
        self,
        api_user: str,
        api_key: str,
        username: str | None = None,
        client_ip: str = "127.0.0.1",
        sandbox: bool = False,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.api_user = api_user
        self.api_key = api_key
        self.username = username or api_user
        self.client_ip = client_ip
        self.sandbox = sandbox
        self.endpoint = self.SANDBOX_ENDPOINT if sandbox else self.PROD_ENDPOINT
        self._client = http_client or httpx.Client(timeout=15.0)

    @property
    def provider_name(self) -> str:
        return "Namecheap"

    def _base_params(self, command: str, sld: str, tld: str) -> dict[str, str]:
        return {
            "ApiUser": self.api_user,
            "ApiKey": self.api_key,
            "UserName": self.username,
            "ClientIp": self.client_ip,
            "Command": command,
            "SLD": sld,
            "TLD": tld,
        }

    def _get_existing_hosts(self, sld: str, tld: str) -> list[dict[str, str]]:
        """Fetch existing DNS host records for SLD.TLD to avoid overwriting them."""
        params = self._base_params("namecheap.domains.dns.getHosts", sld, tld)
        resp = self._client.get(self.endpoint, params=params)
        resp.raise_for_status()

        root = ET.fromstring(resp.text)
        status = root.attrib.get("Status", "").upper()
        if status != "OK":
            errors = [elem.text for elem in root.findall(".//{*}Error")]
            raise RuntimeError(f"Namecheap getHosts failed: {'; '.join(filter(None, errors)) or 'Unknown error'}")

        hosts: list[dict[str, str]] = []
        for host in root.findall(".//{*}host"):
            name = host.attrib.get("Name", "@")
            rec_type = host.attrib.get("Type", "A")
            address = host.attrib.get("Address", "")
            ttl = host.attrib.get("TTL", "300")
            mx_pref = host.attrib.get("MXPref", "10")
            hosts.append({
                "HostName": name,
                "RecordType": rec_type,
                "Address": address,
                "TTL": ttl,
                "MXPref": mx_pref,
            })
        return hosts

    def upsert_a_record(self, domain: str, target_ip: str, ttl: int = 300) -> DNSRecordResult:
        """Upsert an A record while safely preserving all existing domain host records."""
        clean_domain = domain.strip().rstrip(".")
        subdomain, sld, tld = extract_domain_parts(clean_domain)

        try:
            existing_hosts = self._get_existing_hosts(sld, tld)

            # Check if matching record exists to update, or append new one
            updated = False
            for host in existing_hosts:
                if host["HostName"].lower() == subdomain.lower() and host["RecordType"].upper() == "A":
                    host["Address"] = target_ip
                    host["TTL"] = str(ttl)
                    updated = True
                    break

            if not updated:
                existing_hosts.append({
                    "HostName": subdomain,
                    "RecordType": "A",
                    "Address": target_ip,
                    "TTL": str(ttl),
                    "MXPref": "10",
                })

            # Build setHosts payload
            params = self._base_params("namecheap.domains.dns.setHosts", sld, tld)
            for i, host in enumerate(existing_hosts, start=1):
                params[f"HostName{i}"] = host["HostName"]
                params[f"RecordType{i}"] = host["RecordType"]
                params[f"Address{i}"] = host["Address"]
                params[f"TTL{i}"] = str(host.get("TTL", ttl))
                if host.get("RecordType") == "MX":
                    params[f"MXPref{i}"] = str(host.get("MXPref", 10))

            resp = self._client.post(self.endpoint, data=params)
            resp.raise_for_status()

            root = ET.fromstring(resp.text)
            status = root.attrib.get("Status", "").upper()
            if status != "OK":
                errors = [elem.text for elem in root.findall(".//{*}Error")]
                err_msg = "; ".join(filter(None, errors)) or "Unknown error"
                raise RuntimeError(f"Namecheap setHosts failed: {err_msg}")

            msg = f"Successfully upserted Namecheap A record for '{subdomain}.{sld}.{tld}' -> '{target_ip}'."
            logger.info(msg)
            return DNSRecordResult(
                success=True,
                domain=clean_domain,
                record_type="A",
                target_ip=target_ip,
                provider=self.provider_name,
                message=msg,
            )
        except Exception as exc:
            msg = f"Failed to upsert Namecheap record for '{clean_domain}': {exc}"
            logger.error(msg)
            return DNSRecordResult(
                success=False,
                domain=clean_domain,
                record_type="A",
                target_ip=target_ip,
                provider=self.provider_name,
                message=msg,
            )

    def delete_a_record(self, domain: str, target_ip: str | None = None, ttl: int = 300) -> DNSRecordResult:
        """Delete an A record in Namecheap while preserving all other existing host records."""
        clean_domain = domain.strip().rstrip(".")
        subdomain, sld, tld = extract_domain_parts(clean_domain)

        try:
            existing_hosts = self._get_existing_hosts(sld, tld)
            remaining_hosts: list[dict[str, str]] = []
            removed_ip = target_ip or ""
            removed = False

            for host in existing_hosts:
                is_match = (
                    host["HostName"].lower() == subdomain.lower()
                    and host["RecordType"].upper() == "A"
                    and (not target_ip or host["Address"] == target_ip)
                )
                if is_match:
                    removed = True
                    removed_ip = host.get("Address", removed_ip)
                else:
                    remaining_hosts.append(host)

            if not removed:
                msg = f"Namecheap A record for '{subdomain}.{sld}.{tld}' already absent."
                logger.info(msg)
                return DNSRecordResult(
                    success=True,
                    domain=clean_domain,
                    record_type="A",
                    target_ip=removed_ip,
                    provider=self.provider_name,
                    message=msg,
                )

            params = self._base_params("namecheap.domains.dns.setHosts", sld, tld)
            for i, host in enumerate(remaining_hosts, start=1):
                params[f"HostName{i}"] = host["HostName"]
                params[f"RecordType{i}"] = host["RecordType"]
                params[f"Address{i}"] = host["Address"]
                params[f"TTL{i}"] = str(host.get("TTL", ttl))
                if host.get("RecordType") == "MX":
                    params[f"MXPref{i}"] = str(host.get("MXPref", 10))

            resp = self._client.post(self.endpoint, data=params)
            resp.raise_for_status()

            root = ET.fromstring(resp.text)
            status = root.attrib.get("Status", "").upper()
            if status != "OK":
                errors = [elem.text for elem in root.findall(".//{*}Error")]
                err_msg = "; ".join(filter(None, errors)) or "Unknown error"
                raise RuntimeError(f"Namecheap setHosts failed: {err_msg}")

            msg = f"Successfully deleted Namecheap A record for '{subdomain}.{sld}.{tld}'."
            logger.info(msg)
            return DNSRecordResult(
                success=True,
                domain=clean_domain,
                record_type="A",
                target_ip=removed_ip,
                provider=self.provider_name,
                message=msg,
            )
        except Exception as exc:
            msg = f"Failed to delete Namecheap record for '{clean_domain}': {exc}"
            logger.error(msg)
            return DNSRecordResult(
                success=False,
                domain=clean_domain,
                record_type="A",
                target_ip=target_ip or "",
                provider=self.provider_name,
                message=msg,
            )

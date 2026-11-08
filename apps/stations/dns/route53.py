"""AWS Route 53 automated DNS provider."""
import logging
from typing import Any
from .base import DNSProvider, DNSRecordResult, extract_domain_parts

logger = logging.getLogger(__name__)


class Route53DNSProvider(DNSProvider):
    """Manages DNS records in AWS Route 53."""

    def __init__(
        self,
        hosted_zone_id: str | None = None,
        aws_access_key_id: str | None = None,
        aws_secret_access_key: str | None = None,
        aws_region: str = "us-east-1",
        boto3_client: Any | None = None,
    ) -> None:
        self.hosted_zone_id = hosted_zone_id
        self.aws_access_key_id = aws_access_key_id
        self.aws_secret_access_key = aws_secret_access_key
        self.aws_region = aws_region
        self._client = boto3_client

    @property
    def provider_name(self) -> str:
        return "AWS Route 53"

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client

        try:
            import boto3
        except ImportError as exc:
            raise ImportError(
                "The 'boto3' package is required for Route 53 automation. "
                "Install it with 'pip install boto3'."
            ) from exc

        kwargs: dict[str, Any] = {"region_name": self.aws_region}
        if self.aws_access_key_id and self.aws_secret_access_key:
            kwargs["aws_access_key_id"] = self.aws_access_key_id
            kwargs["aws_secret_access_key"] = self.aws_secret_access_key

        self._client = boto3.client("route53", **kwargs)
        return self._client

    def _find_hosted_zone_id(self, domain: str) -> str:
        """Find the hosted zone ID corresponding to the root domain."""
        if self.hosted_zone_id:
            return self.hosted_zone_id

        client = self._get_client()
        _, sld, tld = extract_domain_parts(domain)
        root_domain = f"{sld}.{tld}".strip(".")

        response = client.list_hosted_zones_by_name(DNSName=root_domain, MaxItems="5")
        for zone in response.get("HostedZones", []):
            zone_name = zone.get("Name", "").rstrip(".")
            if zone_name == root_domain:
                zone_id = zone.get("Id", "")
                # Format: '/hostedzone/Z123456' -> 'Z123456'
                return zone_id.split("/")[-1]

        raise ValueError(f"Could not find a Route 53 hosted zone matching root domain '{root_domain}'.")

    def upsert_a_record(self, domain: str, target_ip: str, ttl: int = 300) -> DNSRecordResult:
        """Create or update an A record in Route 53."""
        clean_domain = domain.strip().rstrip(".")
        fqdn = f"{clean_domain}."

        try:
            client = self._get_client()
            zone_id = self._find_hosted_zone_id(clean_domain)

            change_batch = {
                "Comment": "Automated DNS record update by Spotter Deploy Wizard",
                "Changes": [
                    {
                        "Action": "UPSERT",
                        "ResourceRecordSet": {
                            "Name": fqdn,
                            "Type": "A",
                            "TTL": ttl,
                            "ResourceRecords": [{"Value": target_ip}],
                        },
                    }
                ],
            }

            resp = client.change_resource_record_sets(
                HostedZoneId=zone_id,
                ChangeBatch=change_batch,
            )
            change_id = resp.get("ChangeInfo", {}).get("Id", "unknown")
            msg = f"Successfully submitted UPSERT to Route 53 zone '{zone_id}' (Change ID: {change_id})."
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
            msg = f"Failed to upsert Route 53 record for '{clean_domain}': {exc}"
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
        """Delete an A record from Route 53 (idempotent if record does not exist)."""
        clean_domain = domain.strip().rstrip(".")
        fqdn = f"{clean_domain}."

        try:
            client = self._get_client()
            zone_id = self._find_hosted_zone_id(clean_domain)

            records_to_delete: list[dict[str, str]] = []
            record_ttl = ttl

            if target_ip:
                records_to_delete = [{"Value": target_ip}]
            else:
                resp_list = client.list_resource_record_sets(
                    HostedZoneId=zone_id,
                    StartRecordName=fqdn,
                    StartRecordType="A",
                    MaxItems="5",
                )
                for rset in resp_list.get("ResourceRecordSets", []):
                    if rset.get("Name", "").rstrip(".") == clean_domain and rset.get("Type") == "A":
                        records_to_delete = rset.get("ResourceRecords", [])
                        record_ttl = int(rset.get("TTL", ttl))
                        break

            if not records_to_delete:
                msg = f"Route 53 A record for '{clean_domain}' already absent in zone '{zone_id}'."
                logger.info(msg)
                return DNSRecordResult(
                    success=True,
                    domain=clean_domain,
                    record_type="A",
                    target_ip=target_ip or "",
                    provider=self.provider_name,
                    message=msg,
                )

            resolved_ip = records_to_delete[0].get("Value", target_ip or "")
            change_batch = {
                "Comment": "Automated DNS record deletion by Spotter Destroy CLI",
                "Changes": [
                    {
                        "Action": "DELETE",
                        "ResourceRecordSet": {
                            "Name": fqdn,
                            "Type": "A",
                            "TTL": record_ttl,
                            "ResourceRecords": records_to_delete,
                        },
                    }
                ],
            }

            resp = client.change_resource_record_sets(
                HostedZoneId=zone_id,
                ChangeBatch=change_batch,
            )
            change_id = resp.get("ChangeInfo", {}).get("Id", "unknown")
            msg = f"Successfully submitted DELETE to Route 53 zone '{zone_id}' (Change ID: {change_id})."
            logger.info(msg)
            return DNSRecordResult(
                success=True,
                domain=clean_domain,
                record_type="A",
                target_ip=resolved_ip,
                provider=self.provider_name,
                message=msg,
            )
        except Exception as exc:
            msg = f"Failed to delete Route 53 record for '{clean_domain}': {exc}"
            logger.error(msg)
            return DNSRecordResult(
                success=False,
                domain=clean_domain,
                record_type="A",
                target_ip=target_ip or "",
                provider=self.provider_name,
                message=msg,
            )

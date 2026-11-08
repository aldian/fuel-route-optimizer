"""Unit tests for automated DNS providers and orchestration."""
from typing import Any
from unittest.mock import MagicMock, patch
import httpx
import pytest

from apps.stations.dns.base import DNSProvider, DNSRecordResult, extract_domain_parts
from apps.stations.dns.manager import DNSManager
from apps.stations.dns.namecheap import NamecheapDNSProvider
from apps.stations.dns.route53 import Route53DNSProvider


# ============================================================================
# 1. Base Utilities & Domain Parsing
# ============================================================================

def test_extract_domain_parts_standard() -> None:
    sub, sld, tld = extract_domain_parts("api.example.com")
    assert sub == "api"
    assert sld == "example"
    assert tld == "com"


def test_extract_domain_parts_apex() -> None:
    sub, sld, tld = extract_domain_parts("example.com")
    assert sub == "@"
    assert sld == "example"
    assert tld == "com"


def test_extract_domain_parts_nested_subdomains() -> None:
    sub, sld, tld = extract_domain_parts("staging.api.fuel.example.org")
    assert sub == "staging.api.fuel"
    assert sld == "example"
    assert tld == "org"


def test_extract_domain_parts_multi_part_tld() -> None:
    sub, sld, tld = extract_domain_parts("api.example.co.uk")
    assert sub == "api"
    assert sld == "example"
    assert tld == "co.uk"

    apex_sub, apex_sld, apex_tld = extract_domain_parts("example.co.uk")
    assert apex_sub == "@"
    assert apex_sld == "example"
    assert apex_tld == "co.uk"


def test_extract_domain_parts_single_or_empty() -> None:
    sub, sld, tld = extract_domain_parts("localhost")
    assert sub == "@"
    assert sld == "localhost"
    assert tld == ""


def test_extract_domain_parts_cleaning() -> None:
    sub, sld, tld = extract_domain_parts("  API.EXAMPLE.COM. ")
    assert sub == "api"
    assert sld == "example"
    assert tld == "com"


def test_dns_provider_abstract_instantiation() -> None:
    with pytest.raises(TypeError):
        DNSProvider()  # type: ignore[abstract]


# ============================================================================
# 2. AWS Route 53 Provider Tests
# ============================================================================

def test_route53_provider_name_and_client_reuse() -> None:
    mock_client = MagicMock()
    provider = Route53DNSProvider(hosted_zone_id="Z12345", boto3_client=mock_client)
    assert provider.provider_name == "AWS Route 53"
    assert provider._get_client() is mock_client


def test_route53_get_client_instantiation() -> None:
    with patch("boto3.client") as mock_boto:
        mock_instance = MagicMock()
        mock_boto.return_value = mock_instance
        provider = Route53DNSProvider(
            aws_access_key_id="test_key",
            aws_secret_access_key="test_secret",
            aws_region="us-west-2",
        )
        client = provider._get_client()
        assert client is mock_instance
        mock_boto.assert_called_once_with(
            "route53",
            region_name="us-west-2",
            aws_access_key_id="test_key",
            aws_secret_access_key="test_secret",
        )


def test_route53_get_client_import_error() -> None:
    provider = Route53DNSProvider()
    with patch.dict("sys.modules", {"boto3": None}):
        with pytest.raises(ImportError, match="boto3"):
            provider._get_client()


def test_route53_find_hosted_zone_id_explicit() -> None:
    provider = Route53DNSProvider(hosted_zone_id="ZEXPLICIT")
    assert provider._find_hosted_zone_id("api.example.com") == "ZEXPLICIT"


def test_route53_find_hosted_zone_id_auto_discovery() -> None:
    mock_client = MagicMock()
    mock_client.list_hosted_zones_by_name.return_value = {
        "HostedZones": [
            {"Name": "other.org.", "Id": "/hostedzone/Z00000"},
            {"Name": "example.com.", "Id": "/hostedzone/Z987654"},
        ]
    }
    provider = Route53DNSProvider(boto3_client=mock_client)
    zone_id = provider._find_hosted_zone_id("api.example.com")
    assert zone_id == "Z987654"
    mock_client.list_hosted_zones_by_name.assert_called_once_with(
        DNSName="example.com", MaxItems="5"
    )


def test_route53_find_hosted_zone_id_not_found_raises() -> None:
    mock_client = MagicMock()
    mock_client.list_hosted_zones_by_name.return_value = {"HostedZones": []}
    provider = Route53DNSProvider(boto3_client=mock_client)
    with pytest.raises(ValueError, match="Could not find a Route 53 hosted zone"):
        provider._find_hosted_zone_id("api.nonexistent.com")


def test_route53_upsert_a_record_success() -> None:
    mock_client = MagicMock()
    mock_client.change_resource_record_sets.return_value = {
        "ChangeInfo": {"Id": "/change/C123456789", "Status": "PENDING"}
    }
    provider = Route53DNSProvider(hosted_zone_id="Z111222", boto3_client=mock_client)

    result = provider.upsert_a_record("api.example.com", "203.0.113.10", ttl=600)
    assert result.success is True
    assert result.domain == "api.example.com"
    assert result.target_ip == "203.0.113.10"
    assert result.provider == "AWS Route 53"
    assert "C123456789" in result.message

    mock_client.change_resource_record_sets.assert_called_once_with(
        HostedZoneId="Z111222",
        ChangeBatch={
            "Comment": "Automated DNS record update by Spotter Deploy Wizard",
            "Changes": [
                {
                    "Action": "UPSERT",
                    "ResourceRecordSet": {
                        "Name": "api.example.com.",
                        "Type": "A",
                        "TTL": 600,
                        "ResourceRecords": [{"Value": "203.0.113.10"}],
                    },
                }
            ],
        },
    )


def test_route53_upsert_a_record_failure() -> None:
    mock_client = MagicMock()
    mock_client.change_resource_record_sets.side_effect = RuntimeError("AWS API Throttled")
    provider = Route53DNSProvider(hosted_zone_id="Z111222", boto3_client=mock_client)

    result = provider.upsert_a_record("api.example.com", "203.0.113.10")
    assert result.success is False
    assert "Failed to upsert Route 53 record" in result.message
    assert "AWS API Throttled" in result.message


# ============================================================================
# 3. Namecheap Provider Tests
# ============================================================================

def test_namecheap_provider_properties_and_params() -> None:
    nc = NamecheapDNSProvider(
        api_user="testuser",
        api_key="secretkey",
        client_ip="198.51.100.5",
        sandbox=True,
    )
    assert nc.provider_name == "Namecheap"
    assert nc.endpoint == NamecheapDNSProvider.SANDBOX_ENDPOINT

    params = nc._base_params("testCmd", "example", "com")
    assert params["ApiUser"] == "testuser"
    assert params["ApiKey"] == "secretkey"
    assert params["UserName"] == "testuser"
    assert params["ClientIp"] == "198.51.100.5"
    assert params["Command"] == "testCmd"
    assert params["SLD"] == "example"
    assert params["TLD"] == "com"


def test_namecheap_get_existing_hosts_success() -> None:
    xml_response = """<?xml version="1.0" encoding="utf-8"?>
    <ApiResponse Status="OK" xmlns="http://api.namecheap.com/xml.response">
      <CommandResponse Type="namecheap.domains.dns.getHosts">
        <DomainDNSGetHostsResult Domain="example.com" EmailType="MX">
          <host HostId="1" Name="@" Type="A" Address="1.1.1.1" TTL="300" />
          <host HostId="2" Name="mail" Type="MX" Address="mail.example.com" TTL="1800" MXPref="10" />
        </DomainDNSGetHostsResult>
      </CommandResponse>
    </ApiResponse>"""

    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = xml_response
    mock_resp.raise_for_status.return_value = None
    mock_client.get.return_value = mock_resp

    nc = NamecheapDNSProvider(
        api_user="user", api_key="key", http_client=mock_client
    )
    hosts = nc._get_existing_hosts("example", "com")
    assert len(hosts) == 2
    assert hosts[0]["HostName"] == "@"
    assert hosts[0]["RecordType"] == "A"
    assert hosts[1]["RecordType"] == "MX"
    assert hosts[1]["MXPref"] == "10"


def test_namecheap_get_existing_hosts_error() -> None:
    xml_error = """<?xml version="1.0" encoding="utf-8"?>
    <ApiResponse Status="ERROR" xmlns="http://api.namecheap.com/xml.response">
      <Errors>
        <Error Number="1011150">Parameter ClientIP is invalid or whitelisted IP mismatch</Error>
      </Errors>
    </ApiResponse>"""

    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = xml_error
    mock_resp.raise_for_status.return_value = None
    mock_client.get.return_value = mock_resp

    nc = NamecheapDNSProvider(
        api_user="user", api_key="key", http_client=mock_client
    )
    with pytest.raises(RuntimeError, match="Parameter ClientIP is invalid"):
        nc._get_existing_hosts("example", "com")


def test_namecheap_upsert_a_record_update_existing() -> None:
    get_hosts_xml = """<?xml version="1.0" encoding="utf-8"?>
    <ApiResponse Status="OK" xmlns="http://api.namecheap.com/xml.response">
      <CommandResponse Type="namecheap.domains.dns.getHosts">
        <DomainDNSGetHostsResult Domain="example.com">
          <host HostId="1" Name="api" Type="A" Address="10.0.0.1" TTL="300" />
          <host HostId="2" Name="@" Type="A" Address="10.0.0.2" TTL="300" />
        </DomainDNSGetHostsResult>
      </CommandResponse>
    </ApiResponse>"""

    set_hosts_ok = """<?xml version="1.0" encoding="utf-8"?>
    <ApiResponse Status="OK" xmlns="http://api.namecheap.com/xml.response">
      <CommandResponse Type="namecheap.domains.dns.setHosts">
        <DomainDNSSetHostsResult Domain="example.com" IsSuccess="true" />
      </CommandResponse>
    </ApiResponse>"""

    mock_client = MagicMock()
    mock_get_resp = MagicMock(text=get_hosts_xml)
    mock_post_resp = MagicMock(text=set_hosts_ok)
    mock_client.get.return_value = mock_get_resp
    mock_client.post.return_value = mock_post_resp

    nc = NamecheapDNSProvider(
        api_user="u", api_key="k", http_client=mock_client
    )
    res = nc.upsert_a_record("api.example.com", "203.0.113.25", ttl=300)

    assert res.success is True
    assert res.domain == "api.example.com"
    assert res.target_ip == "203.0.113.25"
    assert res.provider == "Namecheap"

    # Verify posted payload updated api record while preserving @ record
    posted_data = mock_client.post.call_args[1]["data"]
    assert posted_data["HostName1"] == "api"
    assert posted_data["Address1"] == "203.0.113.25"
    assert posted_data["HostName2"] == "@"
    assert posted_data["Address2"] == "10.0.0.2"


def test_namecheap_upsert_a_record_append_new_and_failure_response() -> None:
    get_hosts_xml = """<?xml version="1.0" encoding="utf-8"?>
    <ApiResponse Status="OK" xmlns="http://api.namecheap.com/xml.response">
      <CommandResponse Type="namecheap.domains.dns.getHosts">
        <DomainDNSGetHostsResult Domain="example.com">
          <host HostId="1" Name="@" Type="A" Address="10.0.0.2" TTL="300" />
        </DomainDNSGetHostsResult>
      </CommandResponse>
    </ApiResponse>"""

    set_hosts_fail = """<?xml version="1.0" encoding="utf-8"?>
    <ApiResponse Status="ERROR" xmlns="http://api.namecheap.com/xml.response">
      <Errors>
        <Error Number="201">Authentication failed</Error>
      </Errors>
    </ApiResponse>"""

    mock_client = MagicMock()
    mock_client.get.return_value = MagicMock(text=get_hosts_xml)
    mock_client.post.return_value = MagicMock(text=set_hosts_fail)

    nc = NamecheapDNSProvider(
        api_user="u", api_key="k", http_client=mock_client
    )
    res = nc.upsert_a_record("newsub.example.com", "203.0.113.50")

    assert res.success is False
    assert "Authentication failed" in res.message


# ============================================================================
# 4. DNS Manager Tests
# ============================================================================

def test_dns_manager_detect_public_ip_success() -> None:
    with patch("httpx.Client") as mock_client_cls:
        mock_instance = MagicMock()
        mock_instance.get.return_value = MagicMock(status_code=200, text="198.51.100.99\n")
        mock_client_cls.return_value.__enter__.return_value = mock_instance

        ip = DNSManager.detect_public_ip()
        assert ip == "198.51.100.99"


def test_dns_manager_detect_public_ip_failure() -> None:
    with patch("httpx.Client") as mock_client_cls:
        mock_instance = MagicMock()
        mock_instance.get.side_effect = httpx.ConnectError("Network unreachable")
        mock_client_cls.return_value.__enter__.return_value = mock_instance

        ip = DNSManager.detect_public_ip()
        assert ip is None


def test_dns_manager_from_config_route53() -> None:
    cfg = {
        "dns_provider": "route53",
        "route53_zone_id": "Z12345",
        "aws_access_key_id": "AKIA...",
        "aws_secret_access_key": "secret...",
        "aws_region": "us-east-1",
    }
    mgr = DNSManager.from_config(cfg)
    assert isinstance(mgr.provider, Route53DNSProvider)
    assert mgr.provider.hosted_zone_id == "Z12345"


def test_dns_manager_from_config_namecheap() -> None:
    cfg = {
        "dns_provider": "namecheap",
        "namecheap_api_user": "myuser",
        "namecheap_api_key": "mykey",
        "namecheap_client_ip": "1.2.3.4",
        "namecheap_sandbox": True,
    }
    mgr = DNSManager.from_config(cfg)
    assert isinstance(mgr.provider, NamecheapDNSProvider)
    assert mgr.provider.api_user == "myuser"
    assert mgr.provider.sandbox is True


def test_dns_manager_from_config_none() -> None:
    mgr = DNSManager.from_config({"dns_provider": "none"})
    assert mgr.provider is None


def test_dns_manager_sync_domains_no_provider() -> None:
    mgr = DNSManager(provider=None)
    results = mgr.sync_domains(["api.example.com", "staging.example.com"], target_ip="1.2.3.4")
    assert len(results) == 2
    assert all(r.success is False for r in results)
    assert "No automated DNS provider configured" in results[0].message


def test_dns_manager_sync_domains_with_provider() -> None:
    mock_provider = MagicMock(spec=DNSProvider)
    mock_provider.upsert_a_record.return_value = DNSRecordResult(
        success=True,
        domain="api.example.com",
        record_type="A",
        target_ip="1.2.3.4",
        provider="MockDNS",
        message="OK",
    )

    mgr = DNSManager(provider=mock_provider)
    # Include localhost to verify it gets filtered out
    results = mgr.sync_domains(["localhost", "api.example.com", "127.0.0.1"], target_ip="1.2.3.4")

    assert len(results) == 1
    assert results[0].domain == "api.example.com"
    assert results[0].success is True
    mock_provider.upsert_a_record.assert_called_once_with("api.example.com", "1.2.3.4", ttl=300)

"""SSRF host policy tiers.

"public_only" is the internet only; "no_local" also allows private networks
(self-hosted Confluence/Jira images) but never loopback, link-local or metadata.
"""

import ipaddress

import pytest

from app.utils.url_fetcher import (
    NO_LOCAL,
    PUBLIC_ONLY,
    FetchError,
    check_url_host_without_dns,
    ip_is_blocked,
)


@pytest.mark.parametrize(
    "address",
    [
        "169.254.169.254",        # AWS/GCP/Azure metadata
        "fd00:ec2::254",          # AWS IPv6 metadata (a ULA, which no_local otherwise allows)
        "100.100.100.200",        # Alibaba metadata
        "64:ff9b::a9fe:a9fe",     # 169.254.169.254 via NAT64
        "127.0.0.1",
        "::1",
        "0.0.0.0",
        "169.254.10.1",
    ],
)
def test_never_reachable_under_any_policy(address: str) -> None:
    ip = ipaddress.ip_address(address)
    assert ip_is_blocked(ip, PUBLIC_ONLY)
    assert ip_is_blocked(ip, NO_LOCAL)


@pytest.mark.parametrize("address", ["10.1.2.3", "172.16.0.5", "192.168.1.10", "fd12:3456::1"])
def test_private_networks_only_under_no_local(address: str) -> None:
    ip = ipaddress.ip_address(address)
    assert ip_is_blocked(ip, PUBLIC_ONLY)
    assert not ip_is_blocked(ip, NO_LOCAL)


def test_public_addresses_are_allowed() -> None:
    ip = ipaddress.ip_address("93.184.216.34")
    assert not ip_is_blocked(ip, PUBLIC_ONLY)
    assert not ip_is_blocked(ip, NO_LOCAL)


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:8529/_api/collection",
        "http://[::1]/x.png",
        "http://localhost:6379/",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://api.localhost/x.png",
        "file:///etc/passwd",
        "gopher://10.0.0.1/",
        "http:///no-host",
    ],
)
def test_check_without_dns_refuses(url: str) -> None:
    with pytest.raises(FetchError):
        check_url_host_without_dns(url, NO_LOCAL)


def test_check_without_dns_allows_private_hosts_only_under_no_local() -> None:
    check_url_host_without_dns("http://10.0.0.8/wiki/logo.png", NO_LOCAL)
    check_url_host_without_dns("http://confluence.local/logo.png", NO_LOCAL)
    with pytest.raises(FetchError):
        check_url_host_without_dns("http://10.0.0.8/wiki/logo.png", PUBLIC_ONLY)
    with pytest.raises(FetchError):
        check_url_host_without_dns("http://confluence.local/logo.png", PUBLIC_ONLY)


def test_check_without_dns_does_not_resolve_hostnames() -> None:
    # Hostnames are vetted at connect time by the resolver; this check must not block on DNS.
    check_url_host_without_dns("https://does-not-exist.invalid/a.png", NO_LOCAL)

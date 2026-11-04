"""SSRF address rules with and without ``allow_private``.

The default is the public internet only; ``allow_private`` also reaches private networks
(self-hosted servers) but never loopback, link-local or metadata.
"""

import ipaddress

import pytest

from app.utils.url_fetcher import (
    FetchError,
    _ip_is_blocked,
    check_http_url_without_resolving,
)


@pytest.mark.parametrize(
    "address",
    [
        "169.254.169.254",        # AWS/GCP/Azure metadata
        "fd00:ec2::254",          # AWS IPv6 metadata (a ULA, which allow_private otherwise allows)
        "100.100.100.200",        # Alibaba metadata
        "64:ff9b::a9fe:a9fe",     # 169.254.169.254 via NAT64
        "127.0.0.1",
        "::1",
        "0.0.0.0",
        "169.254.10.1",
    ],
)
def test_never_reachable_even_with_allow_private(address: str) -> None:
    ip = ipaddress.ip_address(address)
    assert _ip_is_blocked(ip)
    assert _ip_is_blocked(ip, allow_private=True)


@pytest.mark.parametrize("address", ["10.1.2.3", "172.16.0.5", "192.168.1.10", "fd12:3456::1"])
def test_private_networks_only_with_allow_private(address: str) -> None:
    ip = ipaddress.ip_address(address)
    assert _ip_is_blocked(ip)
    assert not _ip_is_blocked(ip, allow_private=True)


def test_public_addresses_are_allowed() -> None:
    ip = ipaddress.ip_address("93.184.216.34")
    assert not _ip_is_blocked(ip)
    assert not _ip_is_blocked(ip, allow_private=True)


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
        check_http_url_without_resolving(url, allow_private=True)


def test_check_without_dns_allows_private_hosts_only_with_allow_private() -> None:
    check_http_url_without_resolving("http://10.0.0.8/wiki/logo.png", allow_private=True)
    check_http_url_without_resolving("http://confluence.local/logo.png", allow_private=True)
    with pytest.raises(FetchError):
        check_http_url_without_resolving("http://10.0.0.8/wiki/logo.png")
    with pytest.raises(FetchError):
        check_http_url_without_resolving("http://confluence.local/logo.png")


def test_check_without_dns_does_not_resolve_hostnames() -> None:
    # Hostnames are vetted at connect time by the resolver; this check must not block on DNS.
    check_http_url_without_resolving("https://does-not-exist.invalid/a.png", allow_private=True)

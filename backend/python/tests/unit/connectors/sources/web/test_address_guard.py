"""Unit tests for app.connectors.sources.web.address_guard."""

import ipaddress
import socket
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.sources.web.address_guard import (
    GuardedResolver,
    UnsafeAddressError,
    create_guarded_session,
    is_unsafe_url,
    resolve_target,
)

PUBLIC = ipaddress.ip_address("93.184.215.14")


def _answers(*addresses: ipaddress.IPv4Address | ipaddress.IPv6Address) -> list[tuple]:
    return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (str(ip), 0)) for ip in addresses]


@pytest.fixture
def dns(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Resolve every host to PUBLIC, or to what the test sets on the returned mock."""
    resolve = MagicMock(return_value=_answers(PUBLIC))
    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    return resolve


class TestResolveTarget:
    async def test_public_host_is_pinned_to_its_address(self, dns) -> None:
        target = await resolve_target("https://example.com/page")
        assert target is not None
        assert (target.host, target.port, target.pinned_address) == ("example.com", 443, PUBLIC)

    @pytest.mark.parametrize("url", [
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:8088/",
        "http://[::1]/",
        "http://10.1.2.3/",
        "http://100.100.100.200/",
        "http://localhost/",
        "file:///etc/passwd",
        "gopher://example.com/",
        "http://example.com:notaport/",
    ])
    async def test_refuses_non_public_or_non_http_urls(self, dns, url) -> None:
        with pytest.raises(UnsafeAddressError):
            await resolve_target(url)

    async def test_refuses_a_host_with_any_private_address(self, dns) -> None:
        dns.return_value = _answers(PUBLIC, ipaddress.ip_address("192.168.0.10"))
        with pytest.raises(UnsafeAddressError):
            await resolve_target("http://rebind.example/")

    async def test_a_host_that_does_not_resolve_is_not_refused(self, dns) -> None:
        dns.side_effect = socket.gaierror("no such host")
        assert await resolve_target("http://nowhere.example/") is None
        assert await is_unsafe_url("http://nowhere.example/") is False


class TestGuardedResolver:
    async def test_refuses_a_private_answer(self, monkeypatch) -> None:
        resolver = GuardedResolver()
        answer = [{"hostname": "x", "host": "10.0.0.1", "port": 80, "family": socket.AF_INET, "proto": 0, "flags": 0}]
        monkeypatch.setattr(resolver._resolver, "resolve", AsyncMock(return_value=answer))
        with pytest.raises(UnsafeAddressError):
            await resolver.resolve("x", 80)
        await resolver.close()

    async def test_passes_a_public_answer_through(self, monkeypatch) -> None:
        resolver = GuardedResolver()
        answer = [{"hostname": "x", "host": str(PUBLIC), "port": 80, "family": socket.AF_INET, "proto": 0, "flags": 0}]
        monkeypatch.setattr(resolver._resolver, "resolve", AsyncMock(return_value=answer))
        assert await resolver.resolve("x", 80) == answer
        await resolver.close()


class TestGuardedSession:
    @pytest.mark.parametrize("url", ["http://127.0.0.1:9/", "http://169.254.169.254/", "http://localhost:9/"])
    async def test_refuses_before_connecting(self, url) -> None:
        async with create_guarded_session() as session:
            with pytest.raises(UnsafeAddressError):
                async with session.get(url):
                    pass

"""SSRF guard for MCP connections and OAuth token requests (`app.agents.mcp.url_guard`)."""
from __future__ import annotations

import socket
from contextlib import contextmanager
from typing import TYPE_CHECKING
from unittest.mock import patch

import httpx
import pytest

from app.agents.mcp import url_guard
from app.agents.mcp.errors import MCPConnectionError, MCPUrlBlockedError
from app.agents.mcp.url_guard import (
    GuardedTransport,
    MCPUrlPolicy,
    PerOriginGuardedTransport,
    assert_mcp_url_allowed,
    check_mcp_url,
    check_mcp_url_at_save,
    private_network_allowed,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

_PUBLIC_IP = "93.184.216.34"


def _resolves_to(*addresses: str) -> "Callable[..., list]":
    def _getaddrinfo(host: str, *_args: object, **_kwargs: object) -> list:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0)) for address in addresses]
    return _getaddrinfo


class _Recorder:
    """Inner transport: records what actually went to the network and answers 200."""

    def __init__(self, responses: "dict[str, httpx.Response] | None" = None) -> None:
        self.requests: list[httpx.Request] = []
        self._responses = responses or {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._responses.get(request.url.path, httpx.Response(200, json={"ok": True}))


@pytest.fixture(autouse=True)
def _no_env_proxy() -> "Iterator[None]":
    with patch.object(url_guard.urllib.request, "getproxies", return_value={}):
        yield


class TestPrivateNetworkAllowed:
    def test_allowed_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(url_guard.PRIVATE_NETWORK_ENV, raising=False)
        assert private_network_allowed() is True

    # Only "true" keeps it on, as with every PipesHub switch; a typo fails closed.
    @pytest.mark.parametrize("value", ["false", "0", "no", "OFF", "disabled", "ture"])
    def test_can_be_turned_off(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv(url_guard.PRIVATE_NETWORK_ENV, value)
        assert private_network_allowed() is False

    def test_true_in_any_case_keeps_it_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(url_guard.PRIVATE_NETWORK_ENV, " TRUE ")
        assert private_network_allowed() is True


class TestPersonalInstancesArePublicOnly:
    def test_record_and_connection_config_are_both_public_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.agents.mcp.service import instance_config_from_dict

        monkeypatch.setenv("MCP_ALLOW_PRIVATE_NETWORK_URLS", "true")
        record = {
            "_id": "i1", "orgId": "o1", "createdBy": "u1", "name": "mine", "scope": "personal",
            "transport": "streamable_http", "authMode": "none", "url": "https://mcp.example.com",
        }
        assert private_network_allowed(record) is False
        assert private_network_allowed(instance_config_from_dict(record)) is False
        assert private_network_allowed({**record, "scope": None}) is True


class TestAssertMcpUrlAllowed:
    async def test_public_url_is_allowed_and_resolved(self) -> None:
        with patch("socket.getaddrinfo", _resolves_to(_PUBLIC_IP)):
            target = await assert_mcp_url_allowed("https://mcp.example.com/mcp", allow_private=False)
        assert str(target.pinned_address) == _PUBLIC_IP

    @pytest.mark.parametrize("url", ["ftp://mcp.example.com/", "http://127.0.0.1:8080/mcp", "http://169.254.169.254/"])
    async def test_blocked_urls_raise_a_connection_error(self, url: str) -> None:
        with pytest.raises(MCPConnectionError):
            await assert_mcp_url_allowed(url, allow_private=True)

    async def test_private_address_depends_on_the_policy(self) -> None:
        with patch("socket.getaddrinfo", _resolves_to("10.1.2.3")):
            await assert_mcp_url_allowed("https://mcp.internal/", allow_private=True)
            with pytest.raises(MCPUrlBlockedError, match="public"):
                await assert_mcp_url_allowed("https://mcp.internal/", allow_private=False)

    async def test_error_message_never_names_the_resolved_address(self) -> None:
        with patch("socket.getaddrinfo", _resolves_to("127.0.0.1")), pytest.raises(MCPUrlBlockedError) as exc:
            await assert_mcp_url_allowed("https://rebind.example.com/", allow_private=True)
        assert "127.0.0.1" not in str(exc.value)


class TestMappedMetadataIsRefused:
    @pytest.mark.parametrize("allow_private", [True, False])
    async def test_mapped_cloud_metadata_is_refused(self, allow_private: bool) -> None:
        with pytest.raises(MCPUrlBlockedError):
            await assert_mcp_url_allowed("https://[::ffff:168.63.129.16]/mcp", allow_private=allow_private)


class TestCheckMcpUrlAtSave:
    def test_does_not_resolve(self) -> None:
        with patch("socket.getaddrinfo", side_effect=AssertionError("no DNS at save time")):
            check_mcp_url_at_save("https://not-yet-resolvable.example/mcp", allow_private=True)

    @pytest.mark.parametrize("url", ["http://localhost:3000/mcp", "http://[::1]/mcp", "http://169.254.169.254/"])
    def test_rejects_literal_loopback_and_metadata(self, url: str) -> None:
        with pytest.raises(MCPUrlBlockedError):
            check_mcp_url_at_save(url, allow_private=True)


class TestGuardedTransport:
    async def test_request_is_pinned_to_the_validated_address(self) -> None:
        recorder = _Recorder()
        transport = GuardedTransport(
            "https://mcp.example.com/mcp", allow_private=False, inner=httpx.MockTransport(recorder),
        )
        with patch("socket.getaddrinfo", _resolves_to(_PUBLIC_IP)):
            async with httpx.AsyncClient(transport=transport) as client:
                await client.post("https://mcp.example.com/mcp", json={})

        sent = recorder.requests[0]
        assert sent.url.host == _PUBLIC_IP
        assert sent.headers["host"] == "mcp.example.com"
        assert sent.extensions["sni_hostname"] == "mcp.example.com"

    async def test_resolves_once_for_many_requests(self) -> None:
        transport = GuardedTransport(
            "https://mcp.example.com/mcp", allow_private=False, inner=httpx.MockTransport(_Recorder()),
        )
        lookups = []

        def _getaddrinfo(*args: object, **kwargs: object) -> list:
            lookups.append(args)
            return _resolves_to(_PUBLIC_IP)(*args, **kwargs)

        with patch("socket.getaddrinfo", _getaddrinfo):
            async with httpx.AsyncClient(transport=transport) as client:
                for _ in range(3):
                    await client.post("https://mcp.example.com/mcp", json={})

        assert len(lookups) == 1

    async def test_request_to_another_origin_never_reaches_the_network(self) -> None:
        recorder = _Recorder()
        transport = GuardedTransport(
            "https://mcp.example.com/mcp", allow_private=False, inner=httpx.MockTransport(recorder),
        )
        with patch("socket.getaddrinfo", _resolves_to(_PUBLIC_IP)):
            async with httpx.AsyncClient(transport=transport) as client:
                with pytest.raises(MCPUrlBlockedError, match="different host"):
                    await client.get("https://evil.example.net/steal")

        assert recorder.requests == []

    async def test_host_that_resolves_to_loopback_is_refused(self) -> None:
        recorder = _Recorder()
        transport = GuardedTransport(
            "https://rebind.example.com/", allow_private=True, inner=httpx.MockTransport(recorder),
        )
        with patch("socket.getaddrinfo", _resolves_to("127.0.0.1")):
            async with httpx.AsyncClient(transport=transport) as client:
                with pytest.raises(MCPUrlBlockedError):
                    await client.get("https://rebind.example.com/")

        assert recorder.requests == []

    async def test_through_an_env_proxy_the_request_still_goes_to_the_checked_address(self) -> None:
        """Left to resolve the name, the proxy could connect wherever it points by then."""
        with patch.object(url_guard.urllib.request, "getproxies", return_value={"https": "http://proxy:3128"}), \
             patch.object(url_guard.urllib.request, "proxy_bypass", return_value=False):
            recorder = _Recorder()
            transport = GuardedTransport(
                "https://mcp.example.com/mcp", allow_private=False, inner=httpx.MockTransport(recorder),
            )
            assert transport._pin is True
            with patch("socket.getaddrinfo", _resolves_to(_PUBLIC_IP)):
                async with httpx.AsyncClient(transport=transport) as client:
                    await client.post("https://mcp.example.com/mcp", json={})
            # The name later points inward: the next look-up is checked like the first.
            transport._resolved_at = 0.0
            with patch("socket.getaddrinfo", _resolves_to("169.254.169.254")):
                async with httpx.AsyncClient(transport=transport) as client:
                    with pytest.raises(MCPUrlBlockedError):
                        await client.post("https://mcp.example.com/mcp", json={})

        sent = recorder.requests[0]
        assert sent.url.host == _PUBLIC_IP
        assert sent.headers["Host"] == "mcp.example.com"
        assert len(recorder.requests) == 1


def _dns_down(*_args: object, **_kwargs: object) -> list:
    raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")


class TestBehindAnEgressProxy:
    """Pods that reach the internet only through `HTTPS_PROXY` can't resolve public names;
    the proxy does. What can be checked without DNS still is."""

    @pytest.fixture
    def proxied(self) -> "Iterator[None]":
        with patch.object(url_guard.urllib.request, "getproxies", return_value={"https": "http://proxy:3128"}), \
             patch.object(url_guard.urllib.request, "proxy_bypass", side_effect=lambda host: host == "direct.example"):
            yield

    @pytest.mark.usefixtures("proxied")
    async def test_a_name_only_the_proxy_can_resolve_is_refused_unless_listed(self) -> None:
        recorder = _Recorder()
        transport = GuardedTransport("https://mcp.example.com/mcp", allow_private=False, inner=httpx.MockTransport(recorder))
        with patch("socket.getaddrinfo", _dns_down):
            async with httpx.AsyncClient(transport=transport) as client:
                with pytest.raises(url_guard.ProxyNameRefusedError, match=url_guard.PROXY_ALLOWED_HOSTS_ENV):
                    await client.post("https://mcp.example.com/mcp", json={})

        assert recorder.requests == []

    @pytest.mark.usefixtures("proxied")
    @pytest.mark.parametrize("listed", ["mcp.example.com", "*.example.com", "other.test, MCP.EXAMPLE.COM."])
    async def test_a_listed_name_goes_to_the_proxy_by_name(self, monkeypatch: pytest.MonkeyPatch, listed: str) -> None:
        monkeypatch.setenv(url_guard.PROXY_ALLOWED_HOSTS_ENV, listed)
        recorder = _Recorder()
        transport = GuardedTransport("https://mcp.example.com/mcp", allow_private=False, inner=httpx.MockTransport(recorder))
        assert transport._pin is False
        with patch("socket.getaddrinfo", _dns_down):
            async with httpx.AsyncClient(transport=transport) as client:
                await client.post("https://mcp.example.com/mcp", json={})

        assert recorder.requests[0].url.host == "mcp.example.com"

    @pytest.mark.usefixtures("proxied")
    async def test_a_listed_name_is_checked_again_after_the_ttl(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(url_guard.PROXY_ALLOWED_HOSTS_ENV, "mcp.example.com")
        transport = GuardedTransport(
            "https://mcp.example.com/mcp", allow_private=False, inner=httpx.MockTransport(_Recorder()),
        )
        with patch("socket.getaddrinfo", _resolves_to(_PUBLIC_IP)):
            async with httpx.AsyncClient(transport=transport) as client:
                await client.post("https://mcp.example.com/mcp", json={})
        transport._name_checked_at = 0.0
        with patch("socket.getaddrinfo", _resolves_to("169.254.169.254")):
            async with httpx.AsyncClient(transport=transport) as client:
                with pytest.raises(MCPUrlBlockedError):
                    await client.post("https://mcp.example.com/mcp", json={})

    @pytest.mark.usefixtures("proxied")
    async def test_a_host_the_proxy_skips_is_pinned_directly(self) -> None:
        recorder = _Recorder()
        transport = GuardedTransport("https://direct.example/mcp", allow_private=False, inner=httpx.MockTransport(recorder))
        assert transport._proxied is False
        with patch("socket.getaddrinfo", _resolves_to(_PUBLIC_IP)):
            async with httpx.AsyncClient(transport=transport) as client:
                await client.post("https://direct.example/mcp", json={})

        assert recorder.requests[0].url.host == _PUBLIC_IP

    @pytest.mark.usefixtures("proxied")
    @pytest.mark.parametrize("url", ["https://10.0.0.5/mcp", "https://localhost/mcp", "https://[::ffff:168.63.129.16]/mcp"])
    async def test_what_needs_no_dns_is_still_refused(self, url: str) -> None:
        with patch("socket.getaddrinfo", _dns_down), pytest.raises(MCPUrlBlockedError):
            await check_mcp_url(url, allow_private=False)

    @pytest.mark.usefixtures("proxied")
    async def test_a_name_that_resolves_is_still_checked(self) -> None:
        with patch("socket.getaddrinfo", _resolves_to("169.254.169.254")), pytest.raises(MCPUrlBlockedError):
            await check_mcp_url("https://mcp.example.com/mcp", allow_private=True)

    @pytest.mark.usefixtures("proxied")
    async def test_a_host_the_proxy_skips_must_resolve(self) -> None:
        with patch("socket.getaddrinfo", _dns_down), pytest.raises(MCPUrlBlockedError):
            await check_mcp_url("https://direct.example/mcp", allow_private=False)

    async def test_without_a_proxy_an_unresolvable_name_is_refused(self) -> None:
        with patch("socket.getaddrinfo", _dns_down), pytest.raises(MCPUrlBlockedError):
            await check_mcp_url("https://mcp.example.com/mcp", allow_private=False)

    @pytest.mark.usefixtures("proxied")
    async def test_oauth_discovery_through_the_proxy_needs_a_listed_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.agents.mcp.dcr import assert_discovery_target_allowed

        url = "https://auth.example.com/.well-known/oauth-authorization-server"
        with patch("socket.getaddrinfo", _dns_down):
            with pytest.raises(Exception, match=url_guard.PROXY_ALLOWED_HOSTS_ENV):
                await assert_discovery_target_allowed(url, allow_private=False)
            monkeypatch.setenv(url_guard.PROXY_ALLOWED_HOSTS_ENV, "auth.example.com")
            await assert_discovery_target_allowed(url, allow_private=False)


class TestTlsThroughAProxy:
    """httpcore's tunnel names the TLS server after the URL host, which the guard replaced
    with the checked address; the certificate must still be checked against the name."""

    def test_an_address_is_renamed_to_the_host_for_tls(self) -> None:
        import ssl

        context = url_guard.proxy_tls_context("https://mcp.example.com/mcp")
        renamed = context.wrap_bio(ssl.MemoryBIO(), ssl.MemoryBIO(), server_hostname=_PUBLIC_IP)
        # anyio hands the name over as bytes.
        from_bytes = context.wrap_bio(ssl.MemoryBIO(), ssl.MemoryBIO(), server_hostname=_PUBLIC_IP.encode())
        assert from_bytes.server_hostname == "mcp.example.com"
        named = context.wrap_bio(ssl.MemoryBIO(), ssl.MemoryBIO(), server_hostname="other.example")
        assert renamed.server_hostname == "mcp.example.com"
        assert named.server_hostname == "other.example"
        assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname

    def test_an_international_name_is_sent_in_its_ascii_form(self) -> None:
        import ssl

        context = url_guard.proxy_tls_context("https://bücher.example/mcp")
        assert context.wrap_bio(ssl.MemoryBIO(), ssl.MemoryBIO(), server_hostname=_PUBLIC_IP).server_hostname == "xn--bcher-kva.example"

    async def test_a_real_client_tunnels_to_the_address_and_names_the_host_in_tls(self) -> None:
        """A local stand-in proxy records the CONNECT line and the TLS ClientHello that follows."""
        import asyncio

        seen: dict[str, object] = {}

        async def proxy(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            seen["connect"] = (await reader.readline()).decode()
            while (await reader.readline()) not in (b"\r\n", b""):
                pass
            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            await writer.drain()
            seen["sni"] = _client_hello_sni(await reader.read(4096))
            writer.close()

        server = await asyncio.start_server(proxy, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        real_getaddrinfo = socket.getaddrinfo

        def getaddrinfo(host: str, *args: object, **kwargs: object) -> list:
            if host == "mcp.example.com":
                return _resolves_to(_PUBLIC_IP)(host)
            return real_getaddrinfo(host, *args, **kwargs)

        with patch.object(url_guard.urllib.request, "getproxies", return_value={"https": f"http://127.0.0.1:{port}"}), \
             patch.object(url_guard.urllib.request, "proxy_bypass", return_value=False), \
             patch("socket.getaddrinfo", getaddrinfo):
            transport = GuardedTransport("https://mcp.example.com/mcp", allow_private=False)
            async with httpx.AsyncClient(transport=transport, timeout=5) as client:
                with pytest.raises(httpx.HTTPError):
                    await client.post("https://mcp.example.com/mcp", json={})
        server.close()
        await server.wait_closed()

        assert seen["connect"] == f"CONNECT {_PUBLIC_IP}:443 HTTP/1.1\r\n"
        assert seen["sni"] == "mcp.example.com"

    async def test_a_refused_connect_moves_on_to_the_next_address_and_ipv6_is_bracketed(self) -> None:
        """A dual-stack name whose IPv6 address the proxy can't reach: the proxy answers 502 to
        that CONNECT, and the request goes on to the IPv4 address."""
        import asyncio

        connects: list[str] = []

        async def proxy(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            line = (await reader.readline()).decode()
            connects.append(line)
            while (await reader.readline()) not in (b"\r\n", b""):
                pass
            writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n" if "[" in line else b"HTTP/1.1 200 Connection established\r\n\r\n")
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(proxy, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        with _through_local_proxy(port, _DUAL_STACK):
            transport = GuardedTransport("https://mcp.example.com/mcp", allow_private=False)
            async with httpx.AsyncClient(transport=transport, timeout=5) as client:
                # The stand-in proxy closes the tunnel instead of speaking TLS.
                with pytest.raises(httpx.HTTPError) as raised:
                    await client.post("https://mcp.example.com/mcp", json={})
        server.close()
        await server.wait_closed()

        assert not isinstance(raised.value, httpx.ProxyError)
        assert connects == [f"CONNECT [{_PUBLIC_IPV6}]:443 HTTP/1.1\r\n", f"CONNECT {_PUBLIC_IP}:443 HTTP/1.1\r\n"]

    async def test_a_plain_http_request_names_an_ipv6_address_in_brackets(self) -> None:
        import asyncio

        lines: list[str] = []

        async def proxy(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            lines.append((await reader.readline()).decode())
            while (await reader.readline()) not in (b"\r\n", b""):
                pass
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(proxy, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        with _through_local_proxy(port, _DUAL_STACK[:1]):
            transport = GuardedTransport("http://mcp.example.com:8080/mcp", allow_private=False)
            async with httpx.AsyncClient(transport=transport, timeout=5) as client:
                response = await client.get("http://mcp.example.com:8080/mcp")
        server.close()
        await server.wait_closed()

        assert response.status_code == 200
        assert lines == [f"GET http://[{_PUBLIC_IPV6}]:8080/mcp HTTP/1.1\r\n"]

    @pytest.mark.parametrize(("method", "lines", "status"), [("GET", 2, 200), ("HEAD", 2, 200), ("POST", 1, 502)])
    async def test_a_plain_http_proxys_502_moves_on_only_for_a_request_safe_to_repeat(
        self, method: str, lines: int, status: int,
    ) -> None:
        """A plain-http request isn't tunnelled: the proxy answers 502 when it can't reach the
        address. A POST stays on that answer, since the proxy may have forwarded its body."""
        import asyncio

        sent: list[str] = []

        async def proxy(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            line = (await reader.readline()).decode()
            sent.append(line)
            while (await reader.readline()) not in (b"\r\n", b""):
                pass
            writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n" if "[" in line else b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(proxy, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        with _through_local_proxy(port, _DUAL_STACK):
            transport = GuardedTransport("http://mcp.example.com:8080/mcp", allow_private=False)
            async with httpx.AsyncClient(transport=transport, timeout=5) as client:
                response = await client.request(method, "http://mcp.example.com:8080/mcp")
        server.close()
        await server.wait_closed()

        assert response.status_code == status
        assert [line.split()[1] for line in sent] == [
            f"http://[{_PUBLIC_IPV6}]:8080/mcp", f"http://{_PUBLIC_IP}:8080/mcp",
        ][:lines]

    @pytest.mark.parametrize("url", ["https://mcp.example.com/mcp", "http://mcp.example.com/mcp"])
    async def test_a_502_is_returned_as_it_is_off_the_plain_http_proxied_path(self, url: str) -> None:
        """Over https the 502 came through the tunnel from the server itself; without a proxy it
        came from the server too. Neither is a proxy saying it couldn't reach the address."""
        attempts: list[str] = []

        def _handler(request: httpx.Request) -> httpx.Response:
            attempts.append(request.url.host)
            return httpx.Response(502)

        proxied = url.startswith("https")
        proxies = {"https": "http://proxy:3128"} if proxied else {}
        with patch.object(url_guard.urllib.request, "getproxies", return_value=proxies), \
             patch.object(url_guard.urllib.request, "proxy_bypass", return_value=False), \
             patch("socket.getaddrinfo", _resolves_to(_PUBLIC_IP, "93.184.216.35")):
            transport = GuardedTransport(url, allow_private=False, inner=httpx.MockTransport(_handler))
            assert transport._proxied is proxied
            async with httpx.AsyncClient(transport=transport) as client:
                response = await client.get(url)

        assert response.status_code == 502
        assert attempts == [_PUBLIC_IP]


_PUBLIC_IPV6 = "2606:2800:220:1:248:1893:25c8:1946"
_DUAL_STACK = [
    (socket.AF_INET6, socket.SOCK_STREAM, 6, "", (_PUBLIC_IPV6, 0, 0, 0)),
    (socket.AF_INET, socket.SOCK_STREAM, 6, "", (_PUBLIC_IP, 0)),
]


@contextmanager
def _through_local_proxy(port: int, answers: list) -> "Iterator[None]":
    """mcp.example.com resolves to `answers`, and https and http go through 127.0.0.1:`port`."""
    real_getaddrinfo = socket.getaddrinfo

    def getaddrinfo(host: str, *args: object, **kwargs: object) -> list:
        return answers if host == "mcp.example.com" else real_getaddrinfo(host, *args, **kwargs)

    proxies = {"https": f"http://127.0.0.1:{port}", "http": f"http://127.0.0.1:{port}"}
    with patch.object(url_guard.urllib.request, "getproxies", return_value=proxies), \
         patch.object(url_guard.urllib.request, "proxy_bypass", return_value=False), \
         patch("socket.getaddrinfo", getaddrinfo):
        yield


def _client_hello_sni(data: bytes) -> str | None:
    """The server name in a TLS ClientHello record, read field by field."""
    i = 5 + 4 + 2 + 32  # record header, handshake header, version, random
    i += 1 + data[i]  # session id
    i += 2 + int.from_bytes(data[i:i + 2], "big")  # cipher suites
    i += 1 + data[i]  # compression methods
    end = i + 2 + int.from_bytes(data[i:i + 2], "big")
    i += 2
    while i < end:
        kind, size = int.from_bytes(data[i:i + 2], "big"), int.from_bytes(data[i + 2:i + 4], "big")
        if kind == 0:  # server_name: list length, name type, name length, name
            return data[i + 9:i + 9 + int.from_bytes(data[i + 7:i + 9], "big")].decode()
        i += 4 + size
    return None


class TestMCPUrlPolicy:
    @pytest.fixture(autouse=True)
    def _private_networks_allowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(url_guard.PRIVATE_NETWORK_ENV, raising=False)

    def test_only_hosts_the_instance_names_may_be_private(self) -> None:
        policy = MCPUrlPolicy.for_instance({
            "url": "http://MCP.internal:8080/mcp", "tokenUrl": "https://sso.internal/token",
            "authorizationUrl": "https://login.internal/authorize",
        })
        assert policy.private_ok("http://mcp.internal/.well-known/oauth-protected-resource")
        assert policy.private_ok("https://sso.internal:9443/token")
        assert policy.private_ok("https://login.internal/authorize")
        assert not policy.private_ok("http://10.0.3.7:8091/register")

    def test_a_personal_instance_is_never_private(self) -> None:
        policy = MCPUrlPolicy.for_instance({"url": "http://mcp.internal/mcp", "scope": "personal"})
        assert not policy.private_ok("http://mcp.internal/mcp")

    def test_the_deployment_setting_turns_it_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(url_guard.PRIVATE_NETWORK_ENV, "false")
        assert not MCPUrlPolicy.for_instance({"url": "http://mcp.internal/mcp"}).private_ok("http://mcp.internal/mcp")

    def test_no_instance_is_public_only(self) -> None:
        assert not MCPUrlPolicy.for_instance(None).private_ok("http://mcp.internal/mcp")


class TestPerOriginGuardedTransport:
    async def test_each_origin_is_checked_and_pinned(self) -> None:
        recorder = _Recorder()
        transport = PerOriginGuardedTransport(MCPUrlPolicy(allow_private=False), inner=httpx.MockTransport(recorder))
        with patch("socket.getaddrinfo", _resolves_to(_PUBLIC_IP)):
            async with httpx.AsyncClient(transport=transport) as client:
                await client.get("https://mcp.example.com/.well-known/oauth-protected-resource")
                await client.get("https://auth.example.org/.well-known/oauth-authorization-server")

        assert [r.headers["host"] for r in recorder.requests] == ["mcp.example.com", "auth.example.org"]
        assert all(r.url.host == _PUBLIC_IP for r in recorder.requests)

    async def test_a_trusted_origin_may_be_private_and_another_may_not(self) -> None:
        recorder = _Recorder()
        policy = MCPUrlPolicy(allow_private=True, trusted_hosts=frozenset({"mcp.internal"}))
        transport = PerOriginGuardedTransport(policy, inner=httpx.MockTransport(recorder))
        with patch("socket.getaddrinfo", _resolves_to("10.0.0.5")):
            async with httpx.AsyncClient(transport=transport) as client:
                await client.get("http://mcp.internal/.well-known/oauth-protected-resource")
                with pytest.raises(MCPUrlBlockedError):
                    await client.get("http://auth.internal/.well-known/oauth-authorization-server")

        assert [r.headers["host"] for r in recorder.requests] == ["mcp.internal"]

    async def test_an_origin_that_resolves_privately_is_refused_for_public_only(self) -> None:
        recorder = _Recorder()
        transport = PerOriginGuardedTransport(MCPUrlPolicy(allow_private=False), inner=httpx.MockTransport(recorder))
        with patch("socket.getaddrinfo", _resolves_to("10.0.0.5")):
            async with httpx.AsyncClient(transport=transport) as client:
                with pytest.raises(MCPUrlBlockedError):
                    await client.get("https://rebind.example.com/.well-known/oauth-authorization-server")

        assert recorder.requests == []


class TestAddressFailover:
    async def test_moves_to_the_next_validated_address_when_connect_fails(self) -> None:
        attempts: list[str] = []

        def _handler(request: httpx.Request) -> httpx.Response:
            attempts.append(request.url.host)
            if request.url.host == "2001:db8::1":
                raise httpx.ConnectError("no route to host", request=request)
            return httpx.Response(200)

        transport = GuardedTransport(
            "https://mcp.example.com/mcp", allow_private=False, inner=httpx.MockTransport(_handler),
        )
        infos = [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:db8::1", 0, 0, 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (_PUBLIC_IP, 0)),
        ]
        with patch("socket.getaddrinfo", return_value=infos), \
             patch("app.utils.url_fetcher._ip_is_blocked", return_value=False):
            async with httpx.AsyncClient(transport=transport) as client:
                response = await client.get("https://mcp.example.com/mcp")

        assert response.status_code == 200
        assert attempts == ["2001:db8::1", _PUBLIC_IP]

    async def test_a_connect_the_proxy_refuses_moves_on_to_the_next_address(self) -> None:
        attempts: list[str] = []

        def _handler(request: httpx.Request) -> httpx.Response:
            attempts.append(request.url.host)
            if request.url.host == _PUBLIC_IP:
                raise httpx.ProxyError("502 Bad Gateway", request=request)
            return httpx.Response(200)

        transport = GuardedTransport(
            "https://mcp.example.com/mcp", allow_private=False, inner=httpx.MockTransport(_handler),
        )
        with patch("socket.getaddrinfo", _resolves_to(_PUBLIC_IP, "93.184.216.35")):
            async with httpx.AsyncClient(transport=transport) as client:
                response = await client.get("https://mcp.example.com/mcp")

        assert response.status_code == 200
        assert attempts == [_PUBLIC_IP, "93.184.216.35"]

    async def test_a_response_error_is_not_retried(self) -> None:
        attempts: list[str] = []

        def _handler(request: httpx.Request) -> httpx.Response:
            attempts.append(request.url.host)
            raise httpx.ReadTimeout("slow", request=request)

        transport = GuardedTransport(
            "https://mcp.example.com/mcp", allow_private=False, inner=httpx.MockTransport(_handler),
        )
        with patch("socket.getaddrinfo", _resolves_to(_PUBLIC_IP, "93.184.216.35")):
            async with httpx.AsyncClient(transport=transport) as client:
                with pytest.raises(httpx.ReadTimeout):
                    await client.get("https://mcp.example.com/mcp")

        assert attempts == [_PUBLIC_IP]

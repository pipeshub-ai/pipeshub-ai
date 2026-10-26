"""SSRF guard for MCP connections and OAuth token requests (`app.agents.mcp.url_guard`)."""
from __future__ import annotations

import socket
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

    async def test_env_proxy_is_used_without_pinning_but_still_checked(self) -> None:
        with patch.object(url_guard.urllib.request, "getproxies", return_value={"https": "http://proxy:3128"}), \
             patch.object(url_guard.urllib.request, "proxy_bypass", return_value=False):
            recorder = _Recorder()
            transport = GuardedTransport(
                "https://mcp.example.com/mcp", allow_private=False, inner=httpx.MockTransport(recorder),
            )
            assert transport._pin is False
            with patch("socket.getaddrinfo", _resolves_to(_PUBLIC_IP)):
                async with httpx.AsyncClient(transport=transport) as client:
                    await client.post("https://mcp.example.com/mcp", json={})
            with patch("socket.getaddrinfo", _resolves_to("169.254.169.254")):
                blocked = GuardedTransport(
                    "https://mcp.example.com/mcp", allow_private=False, inner=httpx.MockTransport(_Recorder()),
                )
                async with httpx.AsyncClient(transport=blocked) as client:
                    with pytest.raises(MCPUrlBlockedError):
                        await client.post("https://mcp.example.com/mcp", json={})

        assert recorder.requests[0].url.host == "mcp.example.com"


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
    async def test_a_name_only_the_proxy_can_resolve_goes_out_through_it(self) -> None:
        recorder = _Recorder()
        transport = GuardedTransport("https://mcp.example.com/mcp", allow_private=False, inner=httpx.MockTransport(recorder))
        with patch("socket.getaddrinfo", _dns_down):
            async with httpx.AsyncClient(transport=transport) as client:
                await client.post("https://mcp.example.com/mcp", json={})

        assert recorder.requests[0].url.host == "mcp.example.com"

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
    async def test_oauth_discovery_is_not_blocked_by_local_dns(self) -> None:
        from app.agents.mcp.dcr import assert_discovery_target_allowed

        with patch("socket.getaddrinfo", _dns_down):
            await assert_discovery_target_allowed("https://auth.example.com/.well-known/oauth-authorization-server", allow_private=False)


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

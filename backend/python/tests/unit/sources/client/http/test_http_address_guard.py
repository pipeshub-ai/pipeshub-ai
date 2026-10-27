"""The address guard every HTTPClient sends through (`app.sources.client.http.address_guard`)."""
from __future__ import annotations

import socket
from typing import TYPE_CHECKING
from unittest.mock import patch

import httpx
import pytest
import requests
from requests.adapters import HTTPAdapter

from app.agents.mcp import url_guard
from app.sources.client.http.address_guard import (
    AddressGuardAdapter,
    AddressGuardTransport,
    AddressNotAllowedError,
)
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.http.http_resilient_transport import ResilientHTTPTransport
from app.sources.client.resilience import ResiliencePolicy
from app.utils.url_fetcher import PRIVATE_ADDRESS_SWITCH_ENV

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

_PUBLIC_IP = "93.184.216.34"


def _dns(answers: dict[str, list[str]]) -> "Callable[..., list]":
    """getaddrinfo that answers each lookup of a name with the next of its addresses."""
    calls: dict[str, int] = {}

    def _getaddrinfo(host: str, *_args: object, **_kwargs: object) -> list:
        n = calls.get(host, 0)
        calls[host] = n + 1
        address = answers[host][min(n, len(answers[host]) - 1)]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0))]

    _getaddrinfo.calls = calls  # type: ignore[attr-defined]
    return _getaddrinfo


@pytest.fixture(autouse=True)
def _no_env_proxy() -> "Iterator[None]":
    with patch.object(url_guard.urllib.request, "getproxies", return_value={}):
        yield


def _client(
    seen: list[httpx.Request],
    responses: "dict[str, httpx.Response] | None" = None,
    *,
    allow_private: "bool | None" = False,
) -> httpx.AsyncClient:
    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return (responses or {}).get(request.url.path, httpx.Response(200, json={"ok": True}))

    transport = AddressGuardTransport(allow_private=allow_private, inner=httpx.MockTransport(answer))
    return httpx.AsyncClient(transport=transport, follow_redirects=True)


class TestDeploymentRule:
    """A connector's server: on the deployment's network is fine, this server and metadata never."""

    async def test_a_private_server_is_reached_while_the_deployment_allows_it(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv(PRIVATE_ADDRESS_SWITCH_ENV, raising=False)
        seen: list[httpx.Request] = []
        with patch("socket.getaddrinfo", _dns({"jira.corp": ["10.0.0.5"]})):
            response = await _client(seen, allow_private=None).get("https://jira.corp/rest/api/2/myself")

        assert response.status_code == 200
        assert seen[0].url.host == "10.0.0.5"

    @pytest.mark.parametrize(
        ("host", "address"),
        [
            ("sneaky.example", "127.0.0.1"),
            ("sneaky.example", "::1"),
            ("sneaky.example", "169.254.169.254"),
            ("sneaky.example", "168.63.129.16"),
            ("sneaky.example", "0.0.0.0"),
            ("sneaky.example", "::ffff:169.254.169.254"),
            # Loopback carried inside IPv6: mapped, 6to4, and Teredo (client 127.0.0.1, stored inverted).
            ("sneaky.example", "::ffff:127.0.0.1"),
            ("sneaky.example", "2002:7f00:1::1"),
            ("sneaky.example", "2001:0:808:808::80ff:fffe"),
            ("127.0.0.1", "127.0.0.1"),
            ("169.254.169.254", "169.254.169.254"),
            ("localhost", "127.0.0.1"),
            ("metadata.google.internal", "169.254.169.254"),
        ],
    )
    async def test_this_server_and_metadata_are_never_reached(
        self, monkeypatch: pytest.MonkeyPatch, host: str, address: str,
    ) -> None:
        monkeypatch.delenv(PRIVATE_ADDRESS_SWITCH_ENV, raising=False)
        seen: list[httpx.Request] = []
        with patch("socket.getaddrinfo", _dns({host: [address]})), pytest.raises(AddressNotAllowedError):
            await _client(seen, allow_private=None).get(f"http://{host}:8088/ocs/v1.php/cloud/capabilities")
        assert seen == []

    async def test_a_private_server_is_refused_while_the_deployment_blocks_them(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(PRIVATE_ADDRESS_SWITCH_ENV, "true")
        seen: list[httpx.Request] = []
        with patch("socket.getaddrinfo", _dns({"jira.corp": ["10.0.0.5"]})), pytest.raises(
            AddressNotAllowedError, match=PRIVATE_ADDRESS_SWITCH_ENV,
        ):
            await _client(seen, allow_private=None).get("https://jira.corp/rest/api/2/myself")
        assert seen == []

    async def test_a_redirect_to_this_server_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(PRIVATE_ADDRESS_SWITCH_ENV, raising=False)
        seen: list[httpx.Request] = []
        redirect = httpx.Response(302, headers={"location": "http://127.0.0.1:8088/admin"})
        with patch("socket.getaddrinfo", _dns({"jira.corp": ["10.0.0.5"]})), pytest.raises(AddressNotAllowedError):
            await _client(seen, {"/go": redirect}, allow_private=None).get("https://jira.corp/go")
        assert [r.url.host for r in seen] == ["10.0.0.5"]

    async def test_a_name_is_checked_again_once_its_answer_is_old(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # DNS rebinding after the re-lookup interval: the new answer is checked like the first.
        monkeypatch.delenv(PRIVATE_ADDRESS_SWITCH_ENV, raising=False)
        seen: list[httpx.Request] = []
        now = [1000.0]
        monkeypatch.setattr(url_guard.time, "monotonic", lambda: now[0])
        dns = _dns({"wiki.example": [_PUBLIC_IP, "169.254.169.254"]})
        with patch("socket.getaddrinfo", dns):
            client = _client(seen, allow_private=None)
            await client.get("https://wiki.example/a")
            now[0] += url_guard._RESOLVE_TTL_SECONDS + 1
            with pytest.raises(AddressNotAllowedError):
                await client.get("https://wiki.example/b")

        assert [r.url.host for r in seen] == [_PUBLIC_IP]
        assert dns.calls["wiki.example"] == 2  # type: ignore[attr-defined]


class TestPublicOnly:
    async def test_it_connects_to_the_address_it_checked(self) -> None:
        seen: list[httpx.Request] = []
        with patch("socket.getaddrinfo", _dns({"me.atlassian.net": [_PUBLIC_IP]})):
            response = await _client(seen).get("https://me.atlassian.net/rest/api/3/myself")

        assert response.status_code == 200
        assert seen[0].url.host == _PUBLIC_IP
        assert seen[0].headers["host"] == "me.atlassian.net"
        assert seen[0].extensions["sni_hostname"] == "me.atlassian.net"

    async def test_a_private_address_is_refused(self) -> None:
        seen: list[httpx.Request] = []
        with patch("socket.getaddrinfo", _dns({"jira.corp": ["10.0.0.5"]})), pytest.raises(AddressNotAllowedError):
            await _client(seen).get("https://jira.corp/rest/api/3/myself")
        assert seen == []

    async def test_a_name_that_turns_private_later_still_reaches_the_address_checked(self) -> None:
        # DNS rebinding: public for the check, private for the connection. The name is looked
        # up once per client and every request goes to that address.
        seen: list[httpx.Request] = []
        dns = _dns({"evil.example": [_PUBLIC_IP, "127.0.0.1"]})
        with patch("socket.getaddrinfo", dns):
            client = _client(seen)
            await client.get("https://evil.example/a")
            await client.get("https://evil.example/b")

        assert [r.url.host for r in seen] == [_PUBLIC_IP, _PUBLIC_IP]
        assert dns.calls["evil.example"] == 1  # type: ignore[attr-defined]

    async def test_a_redirect_to_a_private_address_is_refused(self) -> None:
        seen: list[httpx.Request] = []
        redirect = httpx.Response(302, headers={"location": "https://inside.corp/admin"})
        dns = _dns({"me.atlassian.net": [_PUBLIC_IP], "inside.corp": ["169.254.169.254"]})
        with patch("socket.getaddrinfo", dns), pytest.raises(AddressNotAllowedError):
            await _client(seen, {"/go": redirect}).get("https://me.atlassian.net/go")
        assert [r.url.host for r in seen] == [_PUBLIC_IP]


class TestHTTPClient:
    async def test_every_client_goes_through_the_guard(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(PRIVATE_ADDRESS_SWITCH_ENV, raising=False)
        httpx_client = await HTTPClient("token")._ensure_client()

        assert isinstance(httpx_client._transport, AddressGuardTransport)
        assert httpx_client._transport._allow_private is True
        # httpx's own environment proxy would route around the guard.
        assert httpx_client.trust_env is False

    async def test_it_follows_the_deployment_switch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(PRIVATE_ADDRESS_SWITCH_ENV, "true")
        httpx_client = await HTTPClient("token")._ensure_client()
        assert httpx_client._transport._allow_private is False

    async def test_public_only_once_asked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(PRIVATE_ADDRESS_SWITCH_ENV, raising=False)
        client = HTTPClient("token")
        client.require_public_addresses()
        httpx_client = await client._ensure_client()
        assert httpx_client._transport._allow_private is False

    async def test_a_loopback_server_is_never_sent_the_request(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(PRIVATE_ADDRESS_SWITCH_ENV, raising=False)
        client = HTTPClient("token")
        with pytest.raises(AddressNotAllowedError):
            await client.execute(HTTPRequest(method="GET", url="http://127.0.0.1:1/rest/api/2/myself"))

    async def test_a_resilient_client_keeps_its_policy_and_the_environment_proxy(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv(PRIVATE_ADDRESS_SWITCH_ENV, raising=False)
        policy = ResiliencePolicy()
        transport = AddressGuardTransport(resilience=policy)
        request = httpx.Request("GET", "https://jira.example.com/rest")
        with patch.object(url_guard.urllib.request, "getproxies", return_value={"https": "http://proxy.corp:3128"}):
            guard = transport._guard_for(request)

        assert isinstance(guard._inner, ResilientHTTPTransport)
        assert guard._inner._policy is policy
        assert guard._inner._pool._proxy_url is not None

    async def test_asking_after_the_first_request_is_refused(self) -> None:
        client = HTTPClient("token")
        await client._ensure_client()
        with pytest.raises(RuntimeError):
            client.require_public_addresses()

    async def test_a_subclass_that_skips_init_is_guarded_but_not_public_only(self) -> None:
        class Bare(HTTPClient):
            def __init__(self) -> None:  # deliberately skips HTTPClient.__init__
                self.client = None
                self.timeout = 5.0
                self.follow_redirects = True
                self.headers = {}
                self.resilience = None

        httpx_client = await Bare()._ensure_client()
        assert isinstance(httpx_client._transport, AddressGuardTransport)
        assert httpx_client._transport._allow_private is not False


class TestRequestsAdapter:
    """The same guard on a requests session (GitLab's)."""

    @staticmethod
    def _session(allow_private: "bool | None" = None) -> "tuple[requests.Session, list[tuple[str, str]]]":
        sent: list[tuple[str, str]] = []

        class Recording(AddressGuardAdapter):
            def send(self, request, *args, **kwargs):  # noqa: ANN202
                with patch.object(HTTPAdapter, "send", lambda _s, req, *_a, **_k: _ok(req)):
                    return super().send(request, *args, **kwargs)

        def _ok(req: "requests.PreparedRequest") -> requests.Response:
            adapter.add_headers(req)
            conn_host, _ = adapter.build_connection_pool_key_attributes(req, True)
            sent.append((conn_host["host"], req.headers.get("Host", "")))
            response = requests.Response()
            response.status_code = 200
            response.request = req
            return response

        adapter = Recording(allow_private=allow_private)
        session = requests.Session()
        session.trust_env = False
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session, sent

    def test_it_connects_to_the_address_it_checked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(PRIVATE_ADDRESS_SWITCH_ENV, raising=False)
        session, sent = self._session()
        with patch("socket.getaddrinfo", _dns({"gitlab.corp": ["10.0.0.7"]})):
            assert session.get("https://gitlab.corp/api/v4/version").status_code == 200
        assert sent == [("10.0.0.7", "gitlab.corp")]

    @pytest.mark.parametrize(
        "address", ["127.0.0.1", "169.254.169.254", "fd00:ec2::254", "2002:7f00:1::1", "2001:0:808:808::80ff:fffe"],
    )
    def test_this_server_and_metadata_are_refused(self, monkeypatch: pytest.MonkeyPatch, address: str) -> None:
        monkeypatch.delenv(PRIVATE_ADDRESS_SWITCH_ENV, raising=False)
        session, sent = self._session()
        with patch("socket.getaddrinfo", _dns({"gitlab.example": [address]})), pytest.raises(AddressNotAllowedError):
            session.get("https://gitlab.example/api/v4/version")
        assert sent == []

    def test_a_private_server_is_refused_while_the_deployment_blocks_them(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(PRIVATE_ADDRESS_SWITCH_ENV, "true")
        session, sent = self._session()
        with patch("socket.getaddrinfo", _dns({"gitlab.corp": ["10.0.0.7"]})), pytest.raises(AddressNotAllowedError):
            session.get("https://gitlab.corp/api/v4/version")
        assert sent == []

    def test_a_name_that_turns_private_later_still_reaches_the_address_checked(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv(PRIVATE_ADDRESS_SWITCH_ENV, raising=False)
        session, sent = self._session()
        dns = _dns({"evil.example": [_PUBLIC_IP, "127.0.0.1"]})
        with patch("socket.getaddrinfo", dns):
            session.get("https://evil.example/a")
            session.get("https://evil.example/b")
        assert [host for host, _ in sent] == [_PUBLIC_IP, _PUBLIC_IP]
        assert dns.calls["evil.example"] == 1  # type: ignore[attr-defined]

    def test_an_unresolvable_name_is_a_connection_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(PRIVATE_ADDRESS_SWITCH_ENV, raising=False)
        session, sent = self._session()

        def nxdomain(*_a: object, **_k: object) -> list:
            raise socket.gaierror("Name or service not known")

        with patch("socket.getaddrinfo", nxdomain), pytest.raises(requests.ConnectionError):
            session.get("https://nowhere.example/a")
        assert sent == []

    def test_gitlab_sessions_are_guarded(self) -> None:
        from app.sources.client.gitlab.gitlab import _secure_session

        session = _secure_session()
        assert isinstance(session.get_adapter("https://gitlab.example.com/"), AddressGuardAdapter)
        assert isinstance(session.get_adapter("http://gitlab.example.com/"), AddressGuardAdapter)

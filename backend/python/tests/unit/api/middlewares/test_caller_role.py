"""Tests for app.api.middlewares.caller_role."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.api.middlewares.caller_role import (
    CALLER_ROLE_PATH,
    CallerRole,
    CallerRoleCache,
    CallerRoleStatus,
    fetch_caller_role,
    normalize_auth_role,
)
from app.config.constants.service import DefaultEndpoints

_NODE = "http://nodejs:3000"
_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _config_service(endpoints: dict | None = None) -> AsyncMock:
    config_service = AsyncMock()
    config_service.get_config = AsyncMock(
        return_value={"nodejs": {"endpoint": _NODE}} if endpoints is None else endpoints
    )
    return config_service


def _request(headers: dict) -> MagicMock:
    request = MagicMock()
    request.headers = headers
    return request


def _node(handler):
    """Route the module's outbound httpx client through a MockTransport."""
    return patch(
        "app.api.middlewares.caller_role.httpx.AsyncClient",
        side_effect=lambda **kwargs: _REAL_ASYNC_CLIENT(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )


_BEARER = {"authorization": "Bearer caller-token"}


class TestFetchCallerRole:
    @pytest.mark.parametrize("role", ["admin", "member"])
    async def test_returns_nodes_role(self, role):
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"role": role})

        with _node(handler):
            result = await fetch_caller_role(_request(_BEARER), _config_service())

        assert result == CallerRole(CallerRoleStatus.VALID, role)
        assert result.is_admin is (role == "admin")
        assert str(seen[0].url) == f"{_NODE}{CALLER_ROLE_PATH}"

    async def test_unknown_role_value_is_member(self):
        with _node(lambda _: httpx.Response(200, json={"role": "superadmin"})):
            result = await fetch_caller_role(_request(_BEARER), _config_service())
        assert result == CallerRole(CallerRoleStatus.VALID, "member")

    async def test_401_means_the_token_is_rejected(self):
        """Revoked/expired OAuth or PAT tokens and deleted users come back as 401."""
        with _node(lambda _: httpx.Response(401, json={"message": "revoked"})):
            result = await fetch_caller_role(_request(_BEARER), _config_service())
        assert result.status is CallerRoleStatus.REJECTED
        assert result.is_admin is False

    @pytest.mark.parametrize("status", [400, 403, 404, 500, 503])
    async def test_other_statuses_fail_closed(self, status):
        with _node(lambda _: httpx.Response(status)):
            result = await fetch_caller_role(_request(_BEARER), _config_service())
        assert result == CallerRole(CallerRoleStatus.UNKNOWN)
        assert result.is_admin is False

    async def test_network_error_fails_closed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        with _node(handler):
            result = await fetch_caller_role(_request(_BEARER), _config_service())
        assert result == CallerRole(CallerRoleStatus.UNKNOWN)

    async def test_non_json_body_fails_closed(self):
        with _node(lambda _: httpx.Response(200, text="<html>")):
            result = await fetch_caller_role(_request(_BEARER), _config_service())
        assert result == CallerRole(CallerRoleStatus.UNKNOWN)

    async def test_forwards_only_the_authorization_header(self):
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"role": "member"})

        headers = {
            "Authorization": "Bearer caller-token",
            "X-Organization-Id": "org-1",
            "Cookie": "refreshToken=long-lived",
            "X-Is-Admin": "true",
            "Host": "attacker.example",
        }
        with _node(handler):
            await fetch_caller_role(_request(headers), _config_service())

        sent = seen[0].headers
        assert sent["authorization"] == "Bearer caller-token"
        assert "cookie" not in sent
        assert "x-organization-id" not in sent
        assert "x-is-admin" not in sent
        assert sent["host"] == "nodejs:3000"

    @pytest.mark.parametrize(
        "headers",
        [{}, {"Cookie": "refreshToken=long-lived"}],
        ids=["no-headers", "cookie-only"],
    )
    async def test_without_a_bearer_token_node_is_not_called(self, headers):
        handler = MagicMock()
        with _node(handler):
            result = await fetch_caller_role(_request(headers), _config_service())
        assert result == CallerRole(CallerRoleStatus.UNKNOWN)
        handler.assert_not_called()

    async def test_falls_back_to_default_endpoint_when_config_is_unreadable(self):
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"role": "member"})

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(side_effect=RuntimeError("etcd down"))
        with _node(handler):
            await fetch_caller_role(_request(_BEARER), config_service)

        default = DefaultEndpoints.NODEJS_ENDPOINT.value.rstrip("/")
        assert str(seen[0].url) == f"{default}{CALLER_ROLE_PATH}"

    async def test_trailing_slash_in_configured_endpoint(self):
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"role": "member"})

        with _node(handler):
            await fetch_caller_role(
                _request(_BEARER), _config_service({"nodejs": {"endpoint": f"{_NODE}/"}})
            )
        assert str(seen[0].url) == f"{_NODE}{CALLER_ROLE_PATH}"


class TestNormalizeAuthRole:
    def test_admin_variants(self):
        assert normalize_auth_role(" Admin ") == "admin"

    @pytest.mark.parametrize("value", [None, "", "member", "superadmin", True, 1])
    def test_everything_else_is_member(self, value):
        assert normalize_auth_role(value) == "member"


_LIVE = CallerRole(CallerRoleStatus.VALID, "member")
_ENDED = CallerRole(CallerRoleStatus.REJECTED)
_UNSURE = CallerRole(CallerRoleStatus.UNKNOWN)


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class TestCallerRoleCache:
    async def test_answer_is_reused_until_it_expires(self):
        clock = _Clock()
        cache = CallerRoleCache(ttl_seconds=2.0, max_entries=10, clock=clock)
        lookup = AsyncMock(side_effect=[_LIVE, _ENDED])

        assert await cache.get("tok", lookup) == _LIVE
        clock.now += 1.9
        assert await cache.get("tok", lookup) == _LIVE
        clock.now += 0.2
        # The session ended meanwhile; once the reused answer lapses, that shows.
        assert await cache.get("tok", lookup) == _ENDED
        assert lookup.await_count == 2

    async def test_unknown_is_asked_again(self):
        cache = CallerRoleCache(ttl_seconds=30.0, max_entries=10)
        lookup = AsyncMock(side_effect=[_UNSURE, _LIVE])

        assert await cache.get("tok", lookup) == _UNSURE
        assert await cache.get("tok", lookup) == _LIVE

    async def test_tokens_do_not_share_answers(self):
        cache = CallerRoleCache(ttl_seconds=30.0, max_entries=10)
        await cache.get("live", AsyncMock(return_value=_LIVE))
        assert await cache.get("ended", AsyncMock(return_value=_ENDED)) == _ENDED

    async def test_oldest_answer_is_dropped_past_the_limit(self):
        cache = CallerRoleCache(ttl_seconds=30.0, max_entries=2)
        for token in ("a", "b", "c"):
            await cache.get(token, AsyncMock(return_value=_LIVE))

        lookup = AsyncMock(return_value=_LIVE)
        await cache.get("c", lookup)
        lookup.assert_not_awaited()
        await cache.get("a", lookup)
        lookup.assert_awaited_once()

    async def test_token_is_not_kept_in_the_clear(self):
        cache = CallerRoleCache(ttl_seconds=30.0, max_entries=10)
        await cache.get("secret-token", AsyncMock(return_value=_LIVE))
        assert "secret-token" not in repr(cache.__dict__)

    async def test_a_cancelled_waiter_leaves_the_shared_lookup_running(self):
        cache = CallerRoleCache(ttl_seconds=30.0, max_entries=10)
        release = asyncio.Event()

        async def slow_lookup() -> CallerRole:
            await release.wait()
            return _LIVE

        first = asyncio.ensure_future(cache.get("tok", slow_lookup))
        second = asyncio.ensure_future(cache.get("tok", AsyncMock(return_value=_ENDED)))
        await asyncio.sleep(0)
        first.cancel()
        release.set()

        assert await second == _LIVE
        with pytest.raises(asyncio.CancelledError):
            await first

    async def test_a_failed_lookup_is_not_left_in_flight(self):
        cache = CallerRoleCache(ttl_seconds=30.0, max_entries=10)
        with pytest.raises(RuntimeError):
            await cache.get("tok", AsyncMock(side_effect=RuntimeError("boom")))
        assert await cache.get("tok", AsyncMock(return_value=_LIVE)) == _LIVE

"""The connector OAuth callback when our own store fails after the checks, and when two callbacks carry one code.

The real ``handle_oauth_callback`` over HTTP, the real ``ConnectorRegistry`` on a recording graph, the real
``OAuthProvider`` and the real ``ConfigurationService`` (so a read that does not ask the store to raise is caught) on an
in-memory key-value store that copies like Redis or etcd and fails on chosen calls by count. Only the provider's code
exchange is a fake. Concurrent callbacks are ordered with events, never with sleeps.
"""
from __future__ import annotations

import asyncio
import copy
import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI, Request

from app.api.middlewares.request_log import install_request_logging
from app.config.configuration_service import ConfigurationService
from app.config.providers.in_memory_store import InMemoryKeyValueStore
from app.connectors.api import router as routes
from app.connectors.core.base.token_service import oauth_service
from app.connectors.core.registry.connector_registry import ConnectorRegistry
from tests.support.log_capture import failure_line
from tests.unit.connectors.api.test_oauth_callback_logging import (
    CONFIG_PATH,
    CONNECTOR_ID,
    PATH,
    _state,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

LOGGER = "app.test.oauth_callback_store"
EVENT = "http.request.end"
OLD = {"access_token": "at-old", "refresh_token": "rt-old"}
NEW = {"access_token": "at-new", "refresh_token": "rt-new"}
CLAIMS = "/services/oauth-code-claims/"


class FlakyKV(InMemoryKeyValueStore):
    """Copies on read and write. ``fail_reads`` are the 1-based reads of CONFIG_PATH that raise (1: the route's,
    2: ``handle_callback``'s); the writes that save NEW, or make a claim, raise when asked."""

    def __init__(self) -> None:
        super().__init__(logging.getLogger(LOGGER))
        self.reads = 0
        self.fail_reads: set[int] = set()
        self.fail_credentials_save = False
        self.fail_claim = False

    async def get_key(self, key: str, *, raise_on_error: bool = False) -> Any:  # noqa: ANN401
        if key == CONFIG_PATH:
            self.reads += 1
            if self.reads in self.fail_reads:
                raise ConnectionError("kv down")
        return copy.deepcopy(await super().get_key(key))

    async def create_key(self, key: str, value: Any, overwrite: bool = True, ttl: int | None = None) -> bool:  # noqa: ANN401
        saves_new = isinstance(value, dict) and (value.get("credentials") or {}).get("access_token") == NEW["access_token"]
        if (self.fail_credentials_save and saves_new) or (self.fail_claim and key.startswith(CLAIMS)):
            raise ConnectionError("kv down")
        return await super().create_key(key, copy.deepcopy(value), overwrite, ttl)

    def saved(self) -> dict[str, Any]:
        return self.store[CONFIG_PATH].value


class RecordingGraph:
    def __init__(self) -> None:
        self.doc = {"_key": CONNECTOR_ID, "type": "Custom OAuth", "scope": "team", "createdBy": "u1", "orgId": "o1",
                    "authType": "OAUTH", "isAuthenticated": True, "updatedAtTimestamp": 1}
        self.flags: list[bool] = []
        self.fail_writes = False

    async def get_document(self, key: str, _collection: str, transaction: object = None) -> dict[str, Any] | None:
        return dict(self.doc) if key == self.doc["_key"] else None

    async def update_node(self, key: str, _collection: str, updates: dict[str, Any], transaction: object = None) -> None:
        if self.fail_writes:
            raise ConnectionError("graph down")
        self.flags.append(updates["isAuthenticated"])
        self.doc.update(updates)


class _Container:
    def __init__(self, config: ConfigurationService, graph: RecordingGraph) -> None:
        self._config, self._graph = config, graph

    def logger(self) -> logging.Logger:
        return logging.getLogger(LOGGER)

    def config_service(self) -> ConfigurationService:
        return self._config

    async def data_store(self) -> MagicMock:
        return MagicMock(graph_provider=self._graph)


class World:
    def __init__(self, client: httpx.AsyncClient, kv: FlakyKV, graph: RecordingGraph,
                 config: ConfigurationService) -> None:
        self.client, self.kv, self.graph, self.config = client, kv, graph, config

    async def callback(self) -> dict[str, Any]:
        response = await self.client.get(
            PATH, params={"base_url": "https://app.example", "code": "auth-code", "state": _state()})
        assert response.status_code == 200
        return response.json()


@pytest.fixture
async def world(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> AsyncIterator[World]:
    caplog.set_level(logging.INFO, logger="app")
    monkeypatch.setenv("SECRET_KEY", "oauth-callback-store-test-key-of-32-chars")
    monkeypatch.setattr(routes, "_get_settings_base_path", AsyncMock(return_value="/settings/connectors"))
    monkeypatch.setattr(routes, "check_beta_connector_access", AsyncMock())
    refresh = MagicMock(get_token_refresh_service=MagicMock(return_value=AsyncMock()))
    monkeypatch.setattr("app.connectors.core.base.token_service.startup_service.startup_service", refresh)
    kv, graph = FlakyKV(), RecordingGraph()
    config = ConfigurationService(logging.getLogger(LOGGER), kv)
    await kv.create_key(CONFIG_PATH, {
        "auth": {"clientId": "cid-123456789", "clientSecret": "cs-secret", "redirectUri": "https://app.example/cb",
                 "authorizeUrl": "https://idp.example/authorize", "tokenUrl": "https://idp.example/token"},
        "oauth": {"state": "orig", "used_codes": []},
        "credentials": OLD,  # signed in before: this callback is a re-authentication
    })
    container = _Container(config, graph)
    registry = ConnectorRegistry(container)
    registry._connectors["Custom OAuth"] = {"appGroup": "Custom", "config": {}}
    app = FastAPI()

    @app.middleware("http")
    async def authenticate(request: Request, call_next: Callable[..., Any]) -> Any:  # noqa: ANN401
        request.state.user = {"userId": "u1", "orgId": "o1", "role": "admin"}
        return await call_next(request)

    install_request_logging(app)
    app.container = container
    app.state.connector_registry = registry
    app.state.graph_provider = AsyncMock()
    app.add_api_route(PATH, routes.handle_oauth_callback, methods=["GET"])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield World(client, kv, graph, config)
    await config.close()


def _exchange(monkeypatch: pytest.MonkeyPatch, answer: Callable[[int], Any]) -> list[int]:
    """Replace the provider's code exchange with ``answer(n)`` for the n-th exchange (0-based); returns the calls."""
    calls: list[int] = []

    async def exchange(_self: oauth_service.OAuthProvider, code: str, state: str | None = None,
                       code_verifier: str | None = None) -> oauth_service.OAuthToken:
        calls.append(len(calls))
        return await answer(calls[-1])

    monkeypatch.setattr(oauth_service.OAuthProvider, "exchange_code_for_token", exchange)
    return calls


async def _new_token(_n: int) -> oauth_service.OAuthToken:
    return oauth_service.OAuthToken.from_dict(NEW)


def _invalid_grant() -> oauth_service.OAuthTokenRequestError:
    return oauth_service.OAuthTokenRequestError("Token request failed with status 400", status=400,
                                                error_code="invalid_grant")


def _raising_read(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    read = world.config.get_config

    async def failing(key: str, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        if key == CONFIG_PATH:
            raise ConnectionError("kv down")
        return await read(key, *args, **kwargs)

    monkeypatch.setattr(world.config, "get_config", failing)


OUTAGES: dict[str, Callable[[World, pytest.MonkeyPatch], None]] = {
    "config-read-raises": _raising_read,
    "route-config-read-fails": lambda w, _m: w.kv.fail_reads.add(1),
    "callback-config-read-fails": lambda w, _m: w.kv.fail_reads.add(2),
    "code-claim-fails": lambda w, _m: setattr(w.kv, "fail_claim", True),
    "credentials-save-fails": lambda w, _m: setattr(w.kv, "fail_credentials_save", True),
    "graph-success-write-fails": lambda w, _m: setattr(w.graph, "fail_writes", True),
}


@pytest.mark.parametrize("case", list(OUTAGES))
async def test_an_outage_of_our_store_after_the_checks_is_temporarily_unavailable_and_changes_nothing(
    world: World, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, case: str,
) -> None:
    _exchange(monkeypatch, _new_token)
    OUTAGES[case](world, monkeypatch)

    body = await world.callback()

    assert (body["success"], body["error"]) == (False, "temporarily_unavailable")
    assert "try again" in body["error_message"]
    assert body["redirect_url"].endswith("oauth_error=temporarily_unavailable")
    assert (world.graph.flags, world.graph.doc["isAuthenticated"]) == ([], True)
    fields = failure_line(caplog.records, EVENT, "unavailable", logging.WARNING)
    assert (fields["reason"], fields["connector_id"], fields["error_type"]) == (
        "temporarily_unavailable", CONNECTOR_ID, "OAuthStoreUnavailableError")
    saved = world.kv.saved()
    if case == "graph-success-write-fails":  # the credentials were saved before the graph failed
        assert (saved["credentials"]["access_token"], saved["oauth"]) == ("at-new", {"used_codes": ["auth-code"]})
    else:
        assert (saved["credentials"], saved["oauth"]) == (OLD, {"state": "orig", "used_codes": []})


async def test_after_a_graph_outage_the_same_callback_completes_the_sign_in(
    world: World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _exchange(monkeypatch, _new_token)
    world.graph.fail_writes = True
    assert (await world.callback())["error"] == "temporarily_unavailable"
    world.graph.fail_writes = False

    body = await world.callback()

    assert body["success"] is True
    assert world.graph.flags == [True]
    assert calls == [0]  # the saved token was reused, not exchanged again


async def test_a_reload_after_a_failed_token_save_is_told_to_start_again_and_changes_nothing(
    world: World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _exchange(monkeypatch, _new_token)
    world.kv.fail_credentials_save = True
    assert (await world.callback())["error"] == "temporarily_unavailable"
    world.kv.fail_credentials_save = False

    body = await world.callback()  # the code is spent at the provider, and its claim still stands

    assert (body["success"], body["error"]) == (False, "server_error")
    assert "start a new sign-in" in body["error_message"] and "another window" not in body["error_message"]
    assert calls == [0]
    assert world.graph.flags == []
    assert (world.kv.saved()["credentials"], world.kv.saved()["oauth"]) == (OLD, {"state": "orig", "used_codes": []})


async def test_a_genuine_provider_refusal_still_signs_the_connector_out(
    world: World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def refuse(_n: int) -> oauth_service.OAuthToken:
        raise _invalid_grant()

    _exchange(monkeypatch, refuse)

    body = await world.callback()

    assert (body["success"], body["error"]) == (False, "server_error")
    assert world.graph.flags == [False]
    assert world.kv.saved()["oauth"]["used_codes"] == ["auth-code"]


async def _concurrently(world: World, first: Callable[[], Any], second: Callable[[], Any],
                        monkeypatch: pytest.MonkeyPatch) -> tuple[list[dict[str, Any]], list[int]]:
    """Two callbacks with one code. The first exchange waits until the other callback reached its exchange or
    answered; a second exchange (only possible without the claim) waits until a callback answered. These pin the
    ordering, not atomicity: the in-memory store never yields mid-claim, so that is the store's create-once, pinned
    by test_oauth_service.py's claim test."""
    other_moved, someone_answered = asyncio.Event(), asyncio.Event()

    async def answer(n: int) -> oauth_service.OAuthToken:
        if n == 0:
            await other_moved.wait()
            return await first()
        other_moved.set()
        await someone_answered.wait()
        return await second()

    calls = _exchange(monkeypatch, answer)

    async def call() -> dict[str, Any]:
        body = await world.callback()
        other_moved.set()
        someone_answered.set()
        return body

    bodies = await asyncio.wait_for(asyncio.gather(call(), call()), timeout=20)  # a deadlock guard, not a timing
    return list(bodies), calls


async def _refused() -> oauth_service.OAuthToken:
    raise _invalid_grant()


async def test_two_callbacks_with_one_code_keep_the_winners_credentials(
    world: World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def accepted() -> oauth_service.OAuthToken:
        return oauth_service.OAuthToken.from_dict(NEW)

    bodies, calls = await _concurrently(world, accepted, _refused, monkeypatch)

    assert calls == [0]
    (loser,) = [b for b in bodies if not b["success"]]
    assert loser["error"] == "server_error" and "already submitted" in loser["error_message"]
    assert world.graph.flags == [True]
    saved = world.kv.saved()
    assert (saved["credentials"]["access_token"], saved["oauth"]) == ("at-new", {"used_codes": ["auth-code"]})


async def test_two_callbacks_with_one_code_whose_exchange_is_refused_sign_out_once(
    world: World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bodies, calls = await _concurrently(world, _refused, _refused, monkeypatch)

    assert calls == [0]
    assert [b["success"] for b in bodies] == [False, False]
    assert world.graph.flags == [False]
    assert world.kv.saved()["credentials"] == OLD

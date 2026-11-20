"""The connector OAuth callback's failures are on its one ``http.request.end`` line, text-free (docs/logging.md, "Routes").

The real ``handle_oauth_callback`` on an app with ``install_request_logging``, driven over HTTP, with the real
``OAuthProvider`` and aiohttp. The token endpoint is a ``RefusingTokenEndpoint`` for the refusals, aiohttp itself for an
unusable token URL, and a local aiohttp server for the 200 answers whose text comes from aiohttp or ``json()``.
"""
from __future__ import annotations

import base64
import json
import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import httpx
import pytest
from aiohttp import web
from fastapi import FastAPI, HTTPException, Request

from app.api.middlewares.request_log import install_request_logging
from app.connectors.api import router as routes
from app.connectors.core.base.token_service import oauth_service
from app.utils.logger import log_record_fields
from tests.support.in_memory_config import InMemoryConfigService
from tests.support.log_capture import assert_not_logged, failure_line
from tests.support.refusing_token_endpoint import RefusingTokenEndpoint

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

EVENT = "http.request.end"
PATH = "/api/v1/connectors/oauth/callback"
CONNECTOR_ID = "c1"
CONFIG_PATH = routes._get_config_path_for_instance(CONNECTOR_ID)
# Built at run time: a traceback prints source lines, so a literal would show in a stack.
QUERY = "-".join(("tenant=QRY", "MARKER"))
BODY = "-".join(("BODY", "MARKER"))
STATE_TEXT = "-".join(("STATE", "MARKER"))
UID = "-".join(("UID", "MARKER"))
ACCOUNT = "dbid:" + "-".join(("ACCOUNT", "MARKER"))
TEAM = "dbtid:" + "-".join(("TEAM", "MARKER"))
SCOPE = "files.read " + "-".join(("SCOPE", "MARKER"))
TOKEN_TYPE = "-".join(("TYPE", "MARKER"))
EMAIL_UID = "person." + "-".join(("EMAIL", "MARKER")) + "@example.com"
ROUTE_LOGGER = "app.test.connector_service"


class _Container:
    def __init__(self, store: InMemoryConfigService) -> None:
        self._store = store

    def logger(self) -> logging.Logger:
        return logging.getLogger(ROUTE_LOGGER)

    def config_service(self) -> InMemoryConfigService:
        return self._store


@pytest.fixture
def store() -> InMemoryConfigService:
    store = InMemoryConfigService()
    store.values[CONFIG_PATH] = {
        "auth": {"clientId": "cid-123456789", "clientSecret": "cs-secret", "redirectUri": "https://app.example/cb",
                 "authorizeUrl": "https://idp.example/authorize", "tokenUrl": f"https://idp.example/token?{QUERY}"},
        "oauth": {"state": "orig", "used_codes": []},
    }
    return store


@pytest.fixture
def registry() -> MagicMock:
    return MagicMock(
        get_connector_instance=AsyncMock(return_value={
            "_key": CONNECTOR_ID, "type": "Custom OAuth", "scope": "team", "createdBy": "u1", "authType": "OAUTH"}),
        update_connector_instance=AsyncMock(return_value=True),
    )


@pytest.fixture
async def client(
    store: InMemoryConfigService, registry: MagicMock, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> AsyncIterator[httpx.AsyncClient]:
    caplog.set_level(logging.DEBUG, logger="app")  # personal data stays off every level, DEBUG included
    monkeypatch.setattr(routes, "_get_settings_base_path", AsyncMock(return_value="/settings/connectors"))
    monkeypatch.setattr(routes, "check_beta_connector_access", AsyncMock())
    # The local token endpoint is on loopback, which the SSRF guard always refuses.
    monkeypatch.setattr(oauth_service, "guarded_session_kwargs", lambda **_: {})
    refresh = MagicMock(get_token_refresh_service=MagicMock(return_value=AsyncMock()))
    monkeypatch.setattr("app.connectors.core.base.token_service.startup_service.startup_service", refresh)
    app = FastAPI()

    @app.middleware("http")
    async def authenticate(request: Request, call_next: Any) -> Any:  # noqa: ANN401
        request.state.user = {"userId": request.headers.get("x-user", "u1"), "orgId": "o1",
                              "role": request.headers.get("x-role", "admin")}
        return await call_next(request)

    install_request_logging(app)
    app.container = _Container(store)
    app.state.connector_registry = registry
    app.state.graph_provider = AsyncMock()
    app.add_api_route(PATH, routes.handle_oauth_callback, methods=["GET"])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


def _state(payload: object = None) -> str:
    payload = {"state": "orig", "connector_id": CONNECTOR_ID} if payload is None else payload
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


def _loud(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name.startswith("app") and r.levelno >= logging.WARNING]


async def _callback(client: httpx.AsyncClient, **params: str) -> dict[str, Any]:
    response = await client.get(PATH, params={"base_url": "https://app.example", **params})
    assert response.status_code == 200
    return response.json()


def _token_url(store: InMemoryConfigService, url: str) -> None:
    store.values[CONFIG_PATH]["auth"]["tokenUrl"] = url


@pytest.fixture
async def local_endpoint(store: InMemoryConfigService) -> AsyncIterator[Callable[[Callable[[], web.Response]], None]]:
    """Serves the token URL from a local aiohttp server answering ``answer()`` (a Response is sent once: one per request)."""
    runners: list[web.AppRunner] = []

    async def serve(answer: Callable[[], web.Response]) -> None:
        async def token(_: web.Request) -> web.Response:
            return answer()

        app = web.Application()
        app.router.add_post("/token", token)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        runners.append(runner)
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = runner.addresses[0][1]
        _token_url(store, f"http://127.0.0.1:{port}/token?{QUERY}")

    yield serve
    for runner in runners:
        await runner.cleanup()


def _refusing(status: int, body: str, *, raises: Callable[[], BaseException] | None = None) -> Callable[..., Any]:
    async def arrange(monkeypatch: pytest.MonkeyPatch, _store: InMemoryConfigService, _serve: Any) -> None:  # noqa: ANN401
        endpoint = RefusingTokenEndpoint(status, body, raises=raises() if raises else None)
        monkeypatch.setattr(oauth_service, "ClientSession", endpoint.client_session)
    return arrange


def _saved_url(url: str) -> Callable[..., Any]:
    async def arrange(_monkeypatch: pytest.MonkeyPatch, store: InMemoryConfigService, _serve: Any) -> None:  # noqa: ANN401
        _token_url(store, url)
    return arrange


def _answering(answer: Callable[[], web.Response]) -> Callable[..., Any]:
    async def arrange(_monkeypatch: pytest.MonkeyPatch, _store: InMemoryConfigService, serve: Any) -> None:  # noqa: ANN401
        await serve(answer)
    return arrange


def _connect_error() -> aiohttp.ClientConnectorError:
    return aiohttp.ClientConnectorError(
        SimpleNamespace(host="idp.example", port=443, ssl=True), OSError(111, "Connection refused"))


# (arrange, error_class, level)
EXCHANGE_FAILURES = {
    "schemeless-token-url": (lambda: _saved_url(f"idp.example/token?{QUERY}"), "internal", logging.ERROR),
    "ftp-token-url": (lambda: _saved_url(f"ftp://idp.example/token?{QUERY}"), "internal", logging.ERROR),
    "400-invalid-grant": (lambda: _refusing(400, json.dumps({"error": "invalid_grant", "error_description": BODY})),
                          "auth_expired", logging.WARNING),
    "401-invalid-client": (lambda: _refusing(401, json.dumps({"error": "invalid_client", "error_description": BODY})),
                           "misconfigured", logging.ERROR),
    "endpoint-unreachable": (lambda: _refusing(0, "", raises=_connect_error), "unavailable", logging.WARNING),
    "200-slack-ok-false": (lambda: _answering(lambda: web.json_response({"ok": False, "error": "invalid_code"})),
                           "internal", logging.ERROR),
    "200-form-error": (lambda: _answering(lambda: web.Response(
        text=f"error=bad_verification_code&error_description={BODY}", content_type="application/x-www-form-urlencoded")),
        "internal", logging.ERROR),
    "200-html": (lambda: _answering(lambda: web.Response(text=f"<html>{BODY}</html>", content_type="text/html")),
                 "internal", logging.ERROR),
    "200-bad-created-at": (lambda: _answering(lambda: web.json_response(
        {"access_token": "at-1", "token_type": "bearer", "created_at": BODY})), "internal", logging.ERROR),
}


@pytest.mark.parametrize("case", list(EXCHANGE_FAILURES))
async def test_an_exchange_failure_is_one_text_free_line_on_the_request(
    client: httpx.AsyncClient, store: InMemoryConfigService, registry: MagicMock, local_endpoint: Any,  # noqa: ANN401
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, case: str,
) -> None:
    arrange, cls, level = EXCHANGE_FAILURES[case]
    await arrange()(monkeypatch, store, local_endpoint)

    body = await _callback(client, code="auth-code", state=_state())

    assert (body["success"], body["error"]) == (False, "server_error")  # the body is unchanged
    assert body["redirect_url"] == "https://app.example/connectors/oauth/callback?oauth_error=server_error"
    assert QUERY not in body["error_message"] and BODY not in body["error_message"]
    fields = failure_line(caplog.records, EVENT, cls, level, QUERY, BODY)
    assert (fields["status"], fields["reason"]) == (200, "server_error")
    assert (fields["connector_id"], fields["connector_type"]) == (CONNECTOR_ID, "CustomOAuth")
    assert "has_refresh_token" not in fields
    assert fields["error_type"]
    (end,) = [r for r in caplog.records if r.getMessage() == EVENT]
    assert _loud(caplog) == [end]  # the route writes no line of its own
    assert_not_logged(caplog.records, QUERY, BODY)
    if level >= logging.ERROR:
        assert "router.py" in end.exc_text and "<message omitted>" in end.exc_text  # frames kept, text dropped
    registry.update_connector_instance.assert_awaited_once()
    assert registry.update_connector_instance.await_args.kwargs["updates"]["isAuthenticated"] is False


async def test_a_refused_grant_still_gets_its_friendly_message(
    client: httpx.AsyncClient, store: InMemoryConfigService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _refusing(400, json.dumps({"error": "invalid_grant", "error_description": BODY}))(monkeypatch, store, None)

    body = await _callback(client, code="auth-code", state=_state())

    # extract_oauth_error_message still reads the exception's text; only the line goes without it.
    assert body["error_message"].endswith("Please update it in the admin settings.")


# (error=, error_class, level, oauth_error)
PROVIDER_ERRORS = {
    "access-denied": ("access_denied", "cancelled", logging.INFO, "access_denied"),
    "server-error": ("server_error", "upstream_5xx", logging.WARNING, "server_error"),
    "hostile-value": (f"access_denied<script>{BODY}", "forbidden", logging.WARNING, "other"),
    "hostile-newline-url": (f"access_denied\nhttps://evil.example/cb?q={BODY}", "forbidden", logging.WARNING, "other"),
}


@pytest.mark.parametrize("case", list(PROVIDER_ERRORS))
async def test_a_provider_error_is_its_spec_code_on_the_line_and_never_error(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture, case: str,
) -> None:
    error, cls, level, oauth_error = PROVIDER_ERRORS[case]

    body = await _callback(client, error=error, state=_state())

    assert (body["success"], body["error"]) == (False, error)  # the body still echoes it, as before
    fields = failure_line(caplog.records, EVENT, cls, level, BODY)
    assert (fields["status"], fields["reason"], fields["oauth_error"]) == (200, "provider_error", oauth_error)
    assert "error_type" not in fields
    assert all(r.levelno < logging.ERROR for r in caplog.records)
    assert_not_logged(caplog.records, BODY)


async def test_an_unknown_connector_in_the_state_is_not_found_without_its_id(
    client: httpx.AsyncClient, registry: MagicMock, caplog: pytest.LogCaptureFixture,
) -> None:
    registry.get_connector_instance.return_value = None

    body = await _callback(client, code="auth-code", state=_state({"state": "orig", "connector_id": STATE_TEXT}))

    assert (body["success"], body["error"]) == (False, "instance_not_found")
    fields = failure_line(caplog.records, EVENT, "not_found", logging.WARNING, STATE_TEXT)
    assert (fields["status"], fields["reason"]) == (200, "instance_not_found")
    assert "connector_id" not in fields
    (end,) = [r for r in caplog.records if r.getMessage() == EVENT]
    assert _loud(caplog) == [end]
    assert_not_logged(caplog.records, STATE_TEXT)


async def test_a_state_of_the_wrong_shape_is_internal_and_text_free(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture,
) -> None:
    # A known limitation: decodable JSON without the keys is a TypeError, not invalid_state.
    body = await _callback(client, code="auth-code", state=_state([STATE_TEXT]))

    assert (body["success"], body["error"]) == (False, "server_error")
    failure_line(caplog.records, EVENT, "internal", logging.ERROR, STATE_TEXT)
    assert_not_logged(caplog.records, STATE_TEXT)


def _personal_not_creator(registry: MagicMock, _monkeypatch: pytest.MonkeyPatch) -> None:
    registry.get_connector_instance.return_value = {
        "_key": CONNECTOR_ID, "type": "Custom OAuth", "scope": "personal", "createdBy": "u1", "authType": "OAUTH"}


def _beta_refused(_registry: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes, "check_beta_connector_access", AsyncMock(side_effect=HTTPException(403, "beta")))


# Each refusal comes before the connector is bound: its id stays off the line.
REFUSALS = {
    "team-connector-member": lambda _registry, _monkeypatch: None,
    "personal-not-creator": _personal_not_creator,
    "beta-refused": _beta_refused,
}


@pytest.mark.parametrize("case", list(REFUSALS))
async def test_a_refusal_raised_inside_the_callback_is_forbidden_on_the_line(
    client: httpx.AsyncClient, registry: MagicMock, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    case: str,
) -> None:
    REFUSALS[case](registry, monkeypatch)

    response = await client.get(PATH, params={"code": "auth-code", "state": _state()},
                                headers={"x-role": "member", "x-user": "u2"})

    assert response.json()["error"] == "server_error"
    fields = failure_line(caplog.records, EVENT, "forbidden", logging.WARNING)
    assert (fields["reason"], fields["error_type"]) == ("server_error", "HTTPException")
    assert "connector_id" not in fields and "connector_type" not in fields


# (token answer, markers that must stay off the logs, has_refresh_token)
SIGN_INS = {
    "dropbox": ({"access_token": "at-1", "refresh_token": "rt-1", "token_type": TOKEN_TYPE, "expires_in": 14400,
                 "scope": SCOPE, "uid": UID, "account_id": ACCOUNT, "team_id": TEAM},
                (UID, ACCOUNT, TEAM, SCOPE, TOKEN_TYPE), True),
    "custom-idp-email-uid": ({"access_token": "at-1", "token_type": "bearer", "expires_in": 3600, "uid": EMAIL_UID},
                             (EMAIL_UID,), False),
    "team-id-only": ({"access_token": "at-1", "expires_in": 3600, "team_id": TEAM}, (TEAM,), False),
    "scope-only": ({"access_token": "at-1", "refresh_token": "rt-1", "scope": SCOPE}, (SCOPE,), True),
}


def _success_line(caplog: pytest.LogCaptureFixture) -> dict[str, object]:
    (end,) = [r for r in caplog.records if r.getMessage() == EVENT]
    assert end.levelno == logging.INFO
    fields = log_record_fields(end)
    assert (fields["outcome"], fields["connector_id"], fields["connector_type"]) == ("success", CONNECTOR_ID, "CustomOAuth")
    assert _loud(caplog) == []
    return fields


@pytest.mark.parametrize("case", list(SIGN_INS))
async def test_a_successful_sign_in_is_one_success_line_without_the_providers_token_fields(
    client: httpx.AsyncClient, local_endpoint: Any, caplog: pytest.LogCaptureFixture, case: str,  # noqa: ANN401
) -> None:
    answer, markers, has_refresh_token = SIGN_INS[case]
    await local_endpoint(lambda: web.json_response(answer))

    body = await _callback(client, code="auth-code", state=_state())

    assert body["success"] is True
    assert _success_line(caplog)["has_refresh_token"] is has_refresh_token
    # The ids are bound for the route's own lines too, not only put on the request line.
    (redirect,) = [r for r in caplog.records if r.getMessage().startswith("Callback redirect URI")]
    assert log_record_fields(redirect)["connector_id"] == CONNECTOR_ID
    assert_not_logged(caplog.records, *markers)


async def test_a_repeated_callback_reuses_the_saved_token_without_logging_its_fields(
    client: httpx.AsyncClient, store: InMemoryConfigService, caplog: pytest.LogCaptureFixture,
) -> None:
    # A browser refresh: the code was used and the saved credentials are answered again, with no exchange.
    store.values[CONFIG_PATH]["oauth"]["used_codes"] = ["auth-code"]
    store.values[CONFIG_PATH]["credentials"] = {
        "access_token": "at-1", "refresh_token": "rt-1", "scope": SCOPE, "uid": UID, "account_id": ACCOUNT, "team_id": TEAM}

    body = await _callback(client, code="auth-code", state=_state())

    assert body["success"] is True
    assert _success_line(caplog)["has_refresh_token"] is True
    assert_not_logged(caplog.records, UID, ACCOUNT, TEAM, SCOPE)


def _no_oauth_config(store: InMemoryConfigService, _monkeypatch: pytest.MonkeyPatch) -> None:
    del store.values[CONFIG_PATH]["auth"]


def _flow_config_refused(_store: InMemoryConfigService, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes, "_build_oauth_flow_config", AsyncMock(side_effect=HTTPException(500, "no app")))


def _no_token(_store: InMemoryConfigService, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(oauth_service.OAuthProvider, "handle_callback", AsyncMock(return_value=None))


# (arrange, the body's error, error_class, level): answered 200 after the bind, so a failure line that names the connector
BOUND_FAILURES = {
    "config-not-found": (_no_oauth_config, "config_not_found", "misconfigured", logging.ERROR),
    "oauth-config-fetch-error": (_flow_config_refused, "oauth_config_fetch_error", "misconfigured", logging.ERROR),
    "invalid-token": (_no_token, "invalid_token", "internal", logging.ERROR),
}


@pytest.mark.parametrize("case", list(BOUND_FAILURES))
async def test_a_failure_answered_after_the_checks_is_a_failure_line_naming_the_connector(
    client: httpx.AsyncClient, store: InMemoryConfigService, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture, case: str,
) -> None:
    arrange, error, cls, level = BOUND_FAILURES[case]
    arrange(store, monkeypatch)

    body = await _callback(client, code="auth-code", state=_state())

    assert (body["success"], body["error"]) == (False, error)
    fields = failure_line(caplog.records, EVENT, cls, level)
    assert (fields["status"], fields["reason"], fields["connector_id"]) == (200, error, CONNECTOR_ID)
    assert "has_refresh_token" not in fields
    assert [r for r in caplog.records if r.name == ROUTE_LOGGER and r.levelno >= logging.WARNING] == []  # no line of its own


async def test_a_crash_after_the_exchange_is_a_failure_line_that_keeps_the_refresh_token_flag(
    client: httpx.AsyncClient, registry: MagicMock, local_endpoint: Any, caplog: pytest.LogCaptureFixture,  # noqa: ANN401
) -> None:
    await local_endpoint(lambda: web.json_response({"access_token": "at-1", "refresh_token": "rt-1", "uid": UID}))
    registry.update_connector_instance.side_effect = [RuntimeError("store down"), True]

    body = await _callback(client, code="auth-code", state=_state())

    assert (body["success"], body["error"]) == (False, "server_error")
    fields = failure_line(caplog.records, EVENT, "internal", logging.ERROR, UID)
    assert (fields["reason"], fields["connector_id"], fields["has_refresh_token"]) == ("server_error", CONNECTOR_ID, True)
    assert registry.update_connector_instance.await_args.kwargs["updates"]["isAuthenticated"] is False
    assert_not_logged(caplog.records, UID)

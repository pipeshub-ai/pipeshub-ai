"""Toolset route failures are on the request's one ``http.request.end`` line (docs/logging.md, "Tool calls and MCP").

The real toolsets router on an app with ``install_request_logging``, driven over HTTP. Each test asserts every
WARNING+ record the ``app.*`` loggers wrote, so a second line for the same failure fails it, and the response
the client got, which this change must not alter.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest
from dependency_injector import providers
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from app.agents.tools.factories.base import ToolsetAuthError
from app.api.middlewares.request_log import install_request_logging
from app.api.routes import toolsets as routes
from app.connectors.core.base.token_service import oauth_service
from app.containers.connector import ConnectorAppContainer
from app.utils.logger import log_record_fields
from tests.support.in_memory_config import InMemoryConfigService
from tests.support.refusing_token_endpoint import RefusingTokenEndpoint
from tests.unit.api.routes.test_toolsets import OAUTH_APP_SAVE_FAILED
from tests.unit.api.routes.test_toolsets_full_coverage import _make_oauth_registry

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

ORG, USER = "65f0a1b2c3d4e5f6a7b8c9d0", "65f0a1b2c3d4e5f6a7b8c9d1"
PREFIX = "/api/v1/toolsets"
EVENT = "http.request.end"
OAUTH_APPS_PATH = routes._get_toolset_oauth_config_path("jira")
INSTANCES_PATH = routes._get_instances_path(ORG)
USER_AUTH_PATH = routes._get_user_auth_path("i1", USER)
# Built at run time: a traceback prints source lines, so a literal would show in a stack.
SENTINEL = "-".join(("SENTINEL", "provider")) + " alice@example.com"


class _Store(InMemoryConfigService):
    """The config store, with chosen keys failing: a write answered False, a read or listing raising."""

    def __init__(self) -> None:
        super().__init__()
        self.failing_writes: set[str] = set()
        self.failing_reads: dict[str, BaseException] = {}
        self.listing_fails: BaseException | None = None

    async def get_config(self, key: str, default: Any = None, use_cache: bool = True, **kw: Any) -> Any:  # noqa: ANN401
        if key in self.failing_reads:
            raise self.failing_reads[key]
        return await super().get_config(key, default, use_cache, **kw)

    async def set_config(self, key: str, value: Any, **kw: Any) -> bool:  # noqa: ANN401
        return False if key in self.failing_writes else await super().set_config(key, value, **kw)

    async def list_keys_in_directory(self, directory: str) -> list[str]:
        if self.listing_fails is not None:
            raise self.listing_fails
        return await super().list_keys_in_directory(directory)


@pytest.fixture
def store() -> _Store:
    store = _Store()
    store.values[INSTANCES_PATH] = [{
        "_id": "i1", "orgId": ORG, "instanceName": "Jira", "toolsetType": "jira", "authType": "OAUTH",
        "oauthConfigId": "oc-1",
    }]
    store.values[OAUTH_APPS_PATH] = [{
        "_id": "oc-1", "orgId": ORG, "oauthInstanceName": "Jira app", "toolsetType": "jira",
        "config": {"clientId": "cid", "clientSecret": "csec"},
    }]
    store.values[USER_AUTH_PATH] = {"oauth": {"state": "orig-state"}}
    return store


@pytest.fixture
def graph() -> MagicMock:
    return MagicMock(check_toolset_instance_in_use=AsyncMock(return_value=[]))


@pytest.fixture
def notifications() -> MagicMock:
    return MagicMock(publish_notification=AsyncMock())


# Kept before the fixture replaces it, for the tests of the setup check itself.
_VALIDATE_SETUP = routes._validate_toolset_oauth_setup


@pytest.fixture
def client(store: _Store, graph: MagicMock, notifications: MagicMock, monkeypatch: pytest.MonkeyPatch,
           caplog: pytest.LogCaptureFixture) -> Iterator[TestClient]:
    caplog.set_level(logging.INFO, logger="app")
    monkeypatch.setattr(routes, "_check_user_is_admin", AsyncMock(return_value=True))
    # The factory's own setup check calls the provider's API; it has its own tests.
    monkeypatch.setattr(routes, "_validate_toolset_oauth_setup", AsyncMock(return_value=None))
    container = ConnectorAppContainer()
    container.config_service.override(providers.Object(store))
    container.connector_notification_service.override(providers.Object(notifications))
    container.wire(modules=[routes])
    app = FastAPI()
    app.state.toolset_registry = _make_oauth_registry("jira")
    app.state.graph_provider = graph

    @app.middleware("http")
    async def authenticate(request: Request, call_next: Any) -> Any:  # noqa: ANN401
        request.state.user = {"userId": USER, "orgId": ORG}
        return await call_next(request)

    install_request_logging(app)
    app.include_router(routes.router)
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        container.unwire()


def _end(caplog: pytest.LogCaptureFixture) -> tuple[logging.LogRecord, dict[str, object]]:
    (record,) = [r for r in caplog.records if r.getMessage() == EVENT]
    return record, log_record_fields(record)


def _loud(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name.startswith("app.") and r.levelno >= logging.WARNING]


def _text(record: logging.LogRecord) -> str:
    return " ".join((record.getMessage(), str(log_record_fields(record)), record.exc_text or ""))


# --- pattern A: the route raises, the request's line logs it once ----------------------------------------------------

def test_a_graph_outage_on_delete_is_one_warning_with_the_toolset_id(
    client: TestClient, graph: MagicMock, caplog: pytest.LogCaptureFixture,
) -> None:
    graph.check_toolset_instance_in_use.side_effect = ConnectionError("graph down")

    response = client.delete(f"{PREFIX}/instances/i1")

    assert response.status_code == 500
    assert response.json() == {"detail": "Cannot delete toolset instance: Unable to verify if it's in use by agents. "
                                         "Please try again or contact support."}
    record, fields = _end(caplog)
    assert _loud(caplog) == [record]
    assert (record.levelno, fields["status"], fields["error_class"], fields["toolset_id"]) == (
        logging.WARNING, 500, "unavailable", "i1")
    assert not [r for r in caplog.records if r.name == routes.__name__]


def test_a_failed_credentials_save_on_authenticate_is_one_error_with_the_toolset_id(
    client: TestClient, store: _Store, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    store.values[INSTANCES_PATH][0].update(authType="API_TOKEN", oauthConfigId=None)
    monkeypatch.setattr(routes, "_declared_auth_fields", lambda *_: {"apiToken"})
    store.failing_writes.add(USER_AUTH_PATH)

    response = client.post(f"{PREFIX}/instances/i1/authenticate", json={"auth": {"apiToken": "tok"}})

    assert (response.status_code, response.json()) == (500, {"detail": "Failed to save credentials."})
    record, fields = _end(caplog)
    assert [r for r in _loud(caplog) if not r.name.startswith("app.config")] == [record]
    assert (record.levelno, fields["error_class"], fields["cause_type"], fields["toolset_id"]) == (
        logging.ERROR, "internal", "RuntimeError", "i1")
    assert record.exc_text  # our bug (Q6): the stack, once
    assert not [r for r in caplog.records if r.name == routes.__name__]


_SAVE_APP = {"clientId": "cid", "clientSecret": "csec"}


@pytest.mark.parametrize(("method", "path", "body"), [
    ("POST", "/instances", {"instanceName": "Jira 2", "toolsetType": "jira", "authType": "OAUTH",
                            "authConfig": _SAVE_APP}),
    ("PUT", "/instances/i1", {"authConfig": _SAVE_APP}),
    ("PUT", "/oauth-configs/jira/oc-1", {"authConfig": _SAVE_APP}),
], ids=["create-instance", "update-instance", "update-oauth-app"])
def test_a_failed_oauth_app_save_is_one_error_naming_the_store(
    client: TestClient, store: _Store, caplog: pytest.LogCaptureFixture, method: str, path: str, body: dict,
) -> None:
    store.failing_writes.add(OAUTH_APPS_PATH)

    response = client.request(method, f"{PREFIX}{path}", json=body)

    assert (response.status_code, response.json()) == (500, {"detail": OAUTH_APP_SAVE_FAILED})
    record, fields = _end(caplog)
    assert _loud(caplog) == [record]
    assert (record.levelno, fields["error_class"], fields["cause_type"]) == (logging.ERROR, "internal", "RuntimeError")
    assert "The config store did not save" in str(fields["error_message"])
    assert record.exc_text  # our bug (Q6): the stack, once


# --- pattern C: the OAuth callback answers 200 and marks the failure -------------------------------------------------

def _state(instance_id: str = "i1", user_id: str = USER, *, is_agent: bool = False) -> str:
    return routes._encode_state_with_instance("orig-state", instance_id, user_id, is_agent=is_agent)


def _token_endpoint(status: int, body: str = "", *,
                    raises: Callable[[], BaseException] | None = None) -> Callable[..., None]:
    def arrange(monkeypatch: pytest.MonkeyPatch, _store: _Store) -> None:
        endpoint = RefusingTokenEndpoint(status, body, raises=raises() if raises else None)
        monkeypatch.setattr(oauth_service, "ClientSession", endpoint.client_session)
    return arrange


def _instances_unreadable(_monkeypatch: pytest.MonkeyPatch, store: _Store) -> None:
    store.failing_reads[INSTANCES_PATH] = ConnectionError("store down")


def _no_oauth_app_linked(_monkeypatch: pytest.MonkeyPatch, store: _Store) -> None:
    del store.values[INSTANCES_PATH][0]["oauthConfigId"]


def _no_access_token(monkeypatch: pytest.MonkeyPatch, _store: _Store) -> None:
    monkeypatch.setattr(oauth_service.OAuthProvider, "handle_callback", AsyncMock(return_value=SimpleNamespace(
        access_token="")))


def _agent_check(status: int) -> Callable[..., None]:
    def arrange(monkeypatch: pytest.MonkeyPatch, _store: _Store) -> None:
        monkeypatch.setattr(routes, "_require_agent_edit_access", AsyncMock(side_effect=HTTPException(status, "no")))
    return arrange


def _nothing(_monkeypatch: pytest.MonkeyPatch, _store: _Store) -> None:
    return None


def _connect_error() -> aiohttp.ClientConnectorError:
    """A fresh one per case: a logged exception carries the level it was logged at."""
    return aiohttp.ClientConnectorError(
        SimpleNamespace(host="auth.example.com", port=443, ssl=True), OSError(111, "Connection refused"))
_INVALID_GRANT = '{"error":"invalid_grant","error_description":"code expired for ' + SENTINEL + '"}'
_CODE = {"code": "auth-code"}

# (arrange, query, level, error_class, reason the line and the body's "error" share, oauth_error, toolset_id, agent_id)
CALLBACK_CASES = {
    "token-invalid-grant": (_token_endpoint(400, _INVALID_GRANT), {**_CODE, "state": _state()},
                            logging.WARNING, "auth_expired", "server_error", None, "i1", None),
    "token-503": (_token_endpoint(503, SENTINEL), {**_CODE, "state": _state()},
                  logging.WARNING, "unavailable", "server_error", None, "i1", None),
    "token-500": (_token_endpoint(500, SENTINEL), {**_CODE, "state": _state()},
                  logging.WARNING, "upstream_5xx", "server_error", None, "i1", None),
    "token-endpoint-down": (_token_endpoint(0, raises=_connect_error), {**_CODE, "state": _state()},
                            logging.WARNING, "unavailable", "server_error", None, "i1", None),
    "instances-unreadable": (_instances_unreadable, {**_CODE, "state": _state()},
                             logging.WARNING, "unavailable", "auth_failed", None, "i1", None),
    "other-users-state": (_nothing, {**_CODE, "state": _state(user_id="65f0a1b2c3d4e5f6a7b8c9ff")},
                          logging.WARNING, "forbidden", "auth_failed", None, "i1", None),
    "deleted-instance": (_nothing, {**_CODE, "state": _state(instance_id="gone")},
                         logging.WARNING, "not_found", "auth_failed", None, "gone", None),
    "undecodable-state": (_nothing, {**_CODE, "state": "not-a-state"},
                          logging.WARNING, "bad_request", "OAuthConfigError", None, None, None),
    "no-oauth-app-linked": (_no_oauth_app_linked, {**_CODE, "state": _state()},
                            logging.ERROR, "misconfigured", "OAuthConfigError", None, "i1", None),
    "no-access-token": (_no_access_token, {**_CODE, "state": _state()},
                        logging.WARNING, "upstream_5xx", "OAuthConfigError", None, "i1", None),
    "provider-access-denied": (_nothing, {"error": "access_denied", "state": _state()},
                               logging.INFO, "cancelled", "provider_error", "access_denied", None, None),
    "provider-server-error": (_nothing, {"error": "server_error", "state": _state()},
                              logging.WARNING, "upstream_5xx", "provider_error", "server_error", None, None),
    "provider-invalid-request": (_nothing, {"error": "invalid_request", "state": _state()},
                                 logging.WARNING, "bad_request", "provider_error", "invalid_request", None, None),
    "provider-unknown-code": (_nothing, {"error": SENTINEL, "state": _state()},
                              logging.WARNING, "forbidden", "provider_error", "other", None, None),
    "missing-code": (_nothing, {"state": _state()},
                     logging.WARNING, "bad_request", "missing_parameters", None, None, None),
    "agent-no-edit-access": (_agent_check(404), {**_CODE, "state": _state(user_id="agent-1", is_agent=True)},
                             logging.WARNING, "forbidden", "agent_permission_denied", None, "i1", "agent-1"),
    "agent-sign-in-expired": (_agent_check(401), {**_CODE, "state": _state(user_id="agent-1", is_agent=True)},
                              logging.WARNING, "auth_expired", "agent_auth_error", None, "i1", "agent-1"),
}
_NO_EXCEPTION = {"provider-access-denied", "provider-server-error", "provider-invalid-request",
                 "provider-unknown-code", "missing-code"}


@pytest.mark.parametrize("case", list(CALLBACK_CASES))
def test_each_toolset_callback_failure_is_one_line_with_its_class_and_no_provider_text(
    client: TestClient, store: _Store, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, case: str,
) -> None:
    arrange, query, level, cls, reason, oauth_error, toolset_id, agent_id = CALLBACK_CASES[case]
    arrange(monkeypatch, store)

    response = client.get(f"{PREFIX}/oauth/callback", params=query)

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert body["error"] == (query["error"] if "error" in query else reason)  # the body is unchanged
    record, fields = _end(caplog)
    assert _loud(caplog) == ([record] if level >= logging.WARNING else [])
    assert (record.levelno, fields["status"], fields["outcome"], fields["error_class"], fields["reason"]) == (
        level, 200, "failure", cls, reason)
    assert (fields.get("oauth_error"), fields.get("toolset_id"), fields.get("agent_id")) == (
        oauth_error, toolset_id, agent_id)
    assert ("error_type" in fields) is (case not in _NO_EXCEPTION)
    assert "error_message" not in fields
    for logged in (r for r in caplog.records if r.name.startswith("app.")):
        assert "SENTINEL" not in _text(logged) and "alice@" not in _text(logged) and "://" not in _text(logged)


# --- pattern D: a step the route swallows ----------------------------------------------------------------------------

def test_a_swallowed_user_count_is_its_own_line_and_the_request_succeeds(
    client: TestClient, store: _Store, caplog: pytest.LogCaptureFixture,
) -> None:
    store.listing_fails = ConnectionError("store down")

    response = client.get(f"{PREFIX}/instances/i1")

    assert response.status_code == 200
    assert response.json()["instance"]["authenticatedUserCount"] == 0
    (step,) = _loud(caplog)
    fields = log_record_fields(step)
    assert (step.getMessage(), step.levelno, fields["op"], fields["error_class"], fields["toolset_id"]) == (
        "toolset.route.failed", logging.WARNING, "count_authenticated_users", "unavailable", "i1")
    end, end_fields = _end(caplog)
    assert (end.levelno, end_fields["outcome"], end_fields["toolset_id"]) == (logging.INFO, "success", "i1")


# --- the setup check after a sign-in ---------------------------------------------------------------------------------

def _factory_raising(error: BaseException) -> MagicMock:
    return MagicMock(test_connection=AsyncMock(side_effect=error))


@pytest.mark.parametrize(("check_error", "notify_error", "body_error", "loud"), [
    # A factory refusing the setup: the callback answers toolset_setup_error and the line is the failure.
    (lambda: ToolsetAuthError(SENTINEL, title="t"), None, "toolset_setup_error",
     [(EVENT, logging.ERROR, "misconfigured", None)]),
    # ... and its notification can't be sent: a swallowed step of its own, without the provider's text.
    (lambda: ToolsetAuthError(SENTINEL, title="t"), lambda: ConnectionError(SENTINEL), "toolset_setup_error",
     [("toolset.route.failed", logging.WARNING, "unavailable", "notify_setup_error"),
      (EVENT, logging.ERROR, "misconfigured", None)]),
    # The check itself breaks: logged, and the sign-in still completes.
    (lambda: RuntimeError(SENTINEL), None, None, [("toolset.route.failed", logging.ERROR, "internal", "validate_setup")]),
], ids=["setup-refused", "setup-refused-notify-fails", "setup-check-breaks"])
def test_the_setup_check_after_a_sign_in_logs_once_without_the_providers_text(
    client: TestClient, notifications: MagicMock, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    check_error: Callable[[], BaseException], notify_error: Callable[[], BaseException] | None,
    body_error: str | None, loud: list[tuple],
) -> None:
    from app.agents.tools.factories.registry import ClientFactoryRegistry

    monkeypatch.setattr(routes, "_validate_toolset_oauth_setup", _VALIDATE_SETUP)
    monkeypatch.setattr(oauth_service.OAuthProvider, "handle_callback", AsyncMock(
        return_value=SimpleNamespace(access_token="at")))
    monkeypatch.setattr(ClientFactoryRegistry, "get_factory", MagicMock(return_value=_factory_raising(check_error())))
    monkeypatch.setattr("app.connectors.core.base.token_service.startup_service.startup_service",
                        MagicMock(get_toolset_token_refresh_service=MagicMock(return_value=None)))
    if notify_error is not None:
        notifications.publish_notification.side_effect = notify_error()

    response = client.get(f"{PREFIX}/oauth/callback", params={**_CODE, "state": _state()})

    assert response.status_code == 200
    assert response.json().get("error") == body_error and response.json()["success"] is (body_error is None)
    lines = [(r.getMessage(), r.levelno, log_record_fields(r)) for r in _loud(caplog)]
    assert [(event, level, f["error_class"], f.get("op")) for event, level, f in lines] == loud
    for event, _, fields in lines:
        assert "error_message" not in fields and fields["toolset_id"] == "i1"
        if event == EVENT:
            assert (fields["reason"], fields["outcome"], "error_type" in fields) == (
                "toolset_setup_error", "failure", False)
    _, end = _end(caplog)
    assert end["outcome"] == ("success" if body_error is None else "failure")
    for logged in (r for r in caplog.records if r.name.startswith("app.")):
        assert "SENTINEL" not in _text(logged) and "alice@" not in _text(logged)

"""What the MCP token refresh service logs: one background refresh of one MCP sign-in, from the logs alone.

The real service, the real choke point (`app.agents.mcp.token_refresh`) and the real token client run against a
scripted token endpoint (tests/support/mcp_oauth_fakes.py) and an in-memory config store. Records are captured
from every logger at DEBUG.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.constants.mcp_server_constants import (
    get_mcp_credentials_path,
    get_mcp_instance_path,
    get_mcp_shared_dcr_client_path,
)
from app.agents.mcp.models import OAuthTokens
from app.agents.mcp.oauth_client import MCPRefreshTokenInvalidError
from app.connectors.core.base.token_service import (
    mcp_token_refresh_service as svc_module,
)
from app.core import org_context as oc
from app.utils import credential_refresh_log as CR
from app.utils import logging_fields
from app.utils import request_context as rc
from app.utils.structured_logging import DefaultLoggingProvider
from tests.support.mcp_oauth_fakes import json_response, mock_async_client
from tests.support.refresh_log_fakes import (
    SEEDED_RT,
    SEEDED_SECRET,
    TOKEN_URL,
    assert_no_secrets,
    events,
    fields,
)
from tests.unit.api.routes.mcp_route_fakes import FakeConfigService

if TYPE_CHECKING:
    from collections.abc import Iterator

INST, INST2 = "inst-1", "inst-2"
OWNER = "65f0a1b2c3d4e5f6a7b8c9d1"
ORG, ORG2 = "65f0a1b2c3d4e5f6a7b8c9d0", "65f0a1b2c3d4e5f6a7b8c9d2"
PATH = get_mcp_credentials_path(INST, OWNER)
INVALID_GRANT = (400, {"error": "invalid_grant", "error_description": f"refresh token {SEEDED_RT} is not active"})
OK = (200, {"access_token": "at-new", "refresh_token": "rt-new", "expires_in": 3600})
SKIPPED = "credential_refresh.skipped"
REMOVING = "Removing an MCP credential whose server no longer exists"


def _record(*, due: bool = True, org: str | None = ORG) -> dict[str, Any]:
    created = datetime.now(timezone.utc) - timedelta(seconds=7200 if due else 0)
    record: dict[str, Any] = {"isAuthenticated": True, "oauthTokens": {
        "accessToken": "at-old", "refreshToken": SEEDED_RT, "tokenUrl": f"{TOKEN_URL}?tenant={SEEDED_RT}",
        "expiresIn": 3600, "createdAt": created.isoformat(),
    }}
    if org:
        record["orgId"] = org
    return record


def _seed(store: FakeConfigService, path: str = PATH, record: dict | None = None, instance: str = INST) -> None:
    store.data[get_mcp_instance_path(instance)] = {"_id": instance}
    store.data[get_mcp_shared_dcr_client_path(instance)] = {"clientId": "client-1", "clientSecret": SEEDED_SECRET}
    store.data[path] = record if record is not None else _record()


def _at_least(level: int, caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.levelno >= level]


class Endpoint:
    def __init__(self) -> None:
        self.answers: list[tuple[int, dict]] = []

    async def post(self, *_: object, **__: object) -> MagicMock:
        return json_response(*self.answers.pop(0))


@pytest.fixture(autouse=True)
def _raw_ids_fresh_gate_no_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.utils.logger.get_provider", lambda: DefaultLoggingProvider())
    monkeypatch.setattr(logging_fields, "_log_once_last", {})
    monkeypatch.setattr(rc, "_service_suffix", "")
    monkeypatch.setattr(svc_module, "MIN_IMMEDIATE_RECHECK_DELAY_SECONDS", 0)
    monkeypatch.setattr(svc_module, "REFRESH_COOLDOWN_SECONDS", 0)


@pytest.fixture
def endpoint() -> Iterator[Endpoint]:
    ep = Endpoint()
    inner = MagicMock()
    inner.post = AsyncMock(side_effect=ep.post)
    with mock_async_client(inner):
        yield ep


@pytest.fixture
def store() -> FakeConfigService:
    return FakeConfigService()


@pytest.fixture
async def service(store: FakeConfigService, caplog: pytest.LogCaptureFixture) -> Iterator[svc_module.MCPTokenRefreshService]:
    caplog.set_level(logging.DEBUG)
    for name in ("connector_service", "connector_service.refresh"):  # a level another test left on them must not hide a line
        caplog.set_level(logging.DEBUG, logger=name)
    service = svc_module.MCPTokenRefreshService(store)  # type: ignore[arg-type]
    service._running = True
    yield service
    await service.stop()


def _due_after_the_scan(service: svc_module.MCPTokenRefreshService) -> None:
    """The scan sees the token 1 s before its refresh time, so it arms a timer, which then fires at once."""
    real = service._calculate_refresh_delay
    first = [1.0]

    def delay(token: OAuthTokens) -> tuple[float, datetime]:
        seconds, at = real(token)
        return (first.pop() if first else seconds), at

    service._calculate_refresh_delay = delay  # type: ignore[method-assign]


class TestHealthy:
    async def test_scan_refresh_is_one_end_and_one_schedule_with_the_records_org(
        self, service: svc_module.MCPTokenRefreshService, store: FakeConfigService, endpoint: Endpoint,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        endpoint.answers = [OK]
        _seed(store)

        await service._refresh_all_tokens()

        [end] = events(caplog, CR.EVENT_END)
        assert end.levelno == logging.INFO
        assert fields(end).items() >= {
            "outcome": "success", "refresh_kind": "mcp", "trigger": "scan", "mcp_server_id": INST, "owner_id": OWNER,
            "org_id": ORG, "ctx_kind": "job",
        }.items()
        assert "user_id" not in fields(end)
        assert [r.getMessage() for r in _at_least(logging.INFO, caplog)] == [CR.EVENT_END, CR.EVENT_SCHEDULED]
        [scheduled] = events(caplog, CR.EVENT_SCHEDULED)
        assert fields(scheduled).items() >= {"trigger": "scan", "mcp_server_id": INST, "org_id": ORG}.items()
        assert fields(scheduled)["next_refresh_at"].endswith("Z")

    async def test_timer_firing_is_its_own_root_scheduled_by_the_scan(
        self, service: svc_module.MCPTokenRefreshService, store: FakeConfigService, endpoint: Endpoint,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        endpoint.answers = [OK]
        _seed(store)
        _due_after_the_scan(service)

        await service._refresh_all_tokens()
        [armed] = events(caplog, CR.EVENT_SCHEDULED)
        await asyncio.wait_for(service._refresh_tasks[PATH], 5)

        [end] = events(caplog, CR.EVENT_END)
        assert fields(end).items() >= {
            "outcome": "success", "trigger": "scheduled", "mcp_server_id": INST, "owner_id": OWNER, "org_id": ORG,
            "ctx_kind": "job", "scheduled_by": armed.request_id,
        }.items()
        assert end.request_id.startswith("sys-credential_refresh-")
        assert fields(armed)["trigger"] == "scan"

    async def test_oauth_callback_schedule_is_on_demand_and_its_timer_drops_the_user(
        self, service: svc_module.MCPTokenRefreshService, store: FakeConfigService, endpoint: Endpoint,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        endpoint.answers = [OK]
        _seed(store)
        root = rc.set_context("req-callback")
        try:
            with oc.bind(oc.OrgContext(ORG, OWNER, "user", "jwt", "req-callback")):
                await service.schedule_token_refresh(PATH, OAuthTokens.model_validate(_record()["oauthTokens"]))
        finally:
            rc.reset_context(root)
        await asyncio.wait_for(service._refresh_tasks[PATH], 5)

        armed = events(caplog, CR.EVENT_SCHEDULED)[0]
        assert fields(armed).items() >= {
            "trigger": "on_demand", "refresh_kind": "mcp", "mcp_server_id": INST, "owner_id": OWNER, "org_id": ORG,
        }.items()
        [end] = events(caplog, CR.EVENT_END)
        assert fields(end).items() >= {"trigger": "scheduled", "org_id": ORG, "ctx_kind": "job", "scheduled_by": "req-callback"}.items()
        assert "user_id" not in fields(end)

    async def test_two_orgs_in_one_scan_do_not_leak_into_each_other_or_the_scan(
        self, service: svc_module.MCPTokenRefreshService, store: FakeConfigService, caplog: pytest.LogCaptureFixture,
    ) -> None:
        _seed(store, record=_record(due=False))
        _seed(store, get_mcp_credentials_path(INST2, "u2"), _record(due=False, org=ORG2), INST2)
        _seed(store, get_mcp_credentials_path(INST2, "u3"), _record(due=False, org=None), INST2)

        await service._refresh_all_tokens()

        assert [(fields(r)["owner_id"], fields(r).get("org_id")) for r in events(caplog, CR.EVENT_SCHEDULED)] == [
            (OWNER, ORG), ("u2", ORG2), ("u3", None),
        ]
        assert oc.peek() is None

    async def test_an_agent_sign_in_is_named_by_its_agent_key_not_as_a_user(
        self, service: svc_module.MCPTokenRefreshService, store: FakeConfigService, endpoint: Endpoint,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        endpoint.answers = [OK]
        _seed(store, get_mcp_credentials_path(INST, "agent-key-1"))

        await service._refresh_all_tokens()

        [end] = events(caplog, CR.EVENT_END)
        assert fields(end)["owner_id"] == "agent-key-1"
        assert "user_id" not in fields(end)

    async def test_an_orphan_credential_is_removed_with_its_ids_not_its_path(
        self, service: svc_module.MCPTokenRefreshService, store: FakeConfigService, caplog: pytest.LogCaptureFixture,
    ) -> None:
        store.data[PATH] = _record()

        await service._refresh_all_tokens()

        [line] = [r for r in caplog.records if r.getMessage() == REMOVING]
        assert line.levelno == logging.INFO
        assert fields(line).items() >= {"mcp_server_id": INST, "owner_id": OWNER, "trigger": "scan"}.items()
        assert PATH not in store.data


class TestRejectedGrant:
    async def test_three_scans_strike_then_deauthenticate(
        self, service: svc_module.MCPTokenRefreshService, store: FakeConfigService, endpoint: Endpoint,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        endpoint.answers = [INVALID_GRANT] * 3
        _seed(store)

        for _ in range(svc_module.MAX_REFRESH_TOKEN_INVALID_FAILURES):
            await service._refresh_all_tokens()

        assert [r.levelno for r in events(caplog, CR.EVENT_END)] == [logging.WARNING] * 3
        assert [(r.levelno, fields(r)["strike"], fields(r)["error_class"], fields(r)["org_id"])
                for r in events(caplog, CR.EVENT_STRIKE)] == [(logging.INFO, f"{n}/3", "auth_expired", ORG) for n in (1, 2, 3)]
        [deauth] = events(caplog, CR.EVENT_DEAUTHENTICATED)
        assert deauth.levelno == logging.WARNING
        assert fields(deauth).items() >= {
            "reason": "refresh_token_invalid", "strikes": 3, "path": "background", "mcp_server_id": INST, "org_id": ORG,
        }.items()
        assert store.data[PATH]["isAuthenticated"] is False
        assert _at_least(logging.ERROR, caplog) == []
        assert_no_secrets(caplog)

    @pytest.mark.parametrize(("case", "reason"), [("record gone", "not_found"), ("not saved", "not_updated")])
    async def test_a_deauth_that_did_not_happen_is_an_error(
        self, service: svc_module.MCPTokenRefreshService, store: FakeConfigService, caplog: pytest.LogCaptureFixture,
        case: str, reason: str,
    ) -> None:
        if case == "not saved":
            _seed(store)
            store.set_config = AsyncMock(return_value=False)  # type: ignore[method-assign]
        service._invalid_refresh_failures[PATH] = 2

        await service._handle_refresh_token_invalid(PATH, MCPRefreshTokenInvalidError("x", error_code="invalid_grant", status=400))

        [line] = events(caplog, CR.EVENT_DEAUTHENTICATE_FAILED)
        assert (line.levelno, fields(line)["reason"]) == (logging.ERROR, reason)
        assert events(caplog, CR.EVENT_DEAUTHENTICATED) == []

    async def test_a_deauth_write_that_raises_is_an_error_with_a_stack_and_no_provider_text(
        self, service: svc_module.MCPTokenRefreshService, store: FakeConfigService, endpoint: Endpoint,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        endpoint.answers = [INVALID_GRANT]
        _seed(store)
        store.set_config = AsyncMock(side_effect=ConnectionError("store down"))  # type: ignore[method-assign]
        service._invalid_refresh_failures[PATH] = 2

        await service._refresh_all_tokens()

        [line] = events(caplog, CR.EVENT_DEAUTHENTICATE_FAILED)
        assert (line.levelno, fields(line)["error_type"]) == (logging.ERROR, "ConnectionError")
        assert line.exc_text
        assert fields(line)["org_id"] == ORG
        assert_no_secrets(caplog)

    async def test_a_bug_in_the_strike_handler_is_internal_with_the_org(
        self, service: svc_module.MCPTokenRefreshService, store: FakeConfigService, endpoint: Endpoint,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # Raised inside `except MCPRefreshTokenInvalidError`, the KeyError would be chained to the rejection and classed by it.
        endpoint.answers = [INVALID_GRANT]
        _seed(store)
        service._handle_refresh_token_invalid = AsyncMock(side_effect=KeyError("bug"))  # type: ignore[method-assign]

        await service._refresh_all_tokens()

        [line] = events(caplog, CR.EVENT_CHECK_FAILED)
        assert (line.levelno, fields(line)["error_class"], fields(line)["error_type"]) == (logging.ERROR, "internal", "KeyError")
        assert "reason" not in fields(line)
        assert (fields(line)["mcp_server_id"], fields(line)["org_id"]) == (INST, ORG)
        assert_no_secrets(caplog)


class TestOtherFailures:
    async def test_an_outage_on_a_scan_is_one_warning_end_and_a_debug_check_failure(
        self, service: svc_module.MCPTokenRefreshService, store: FakeConfigService, endpoint: Endpoint,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        endpoint.answers = [(503, {"detail": f"down, try {SEEDED_RT} later"})]
        _seed(store)

        await service._refresh_all_tokens()

        [end] = events(caplog, CR.EVENT_END)
        assert (end.levelno, fields(end)["error_class"], fields(end)["org_id"]) == (logging.WARNING, "unavailable", ORG)
        assert [r.levelno for r in events(caplog, CR.EVENT_CHECK_FAILED)] == [logging.DEBUG]
        assert _at_least(logging.ERROR, caplog) == []
        assert_no_secrets(caplog)

    async def test_a_held_local_lock_is_one_skipped_warning(
        self, service: svc_module.MCPTokenRefreshService, store: FakeConfigService, endpoint: Endpoint,
        caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _seed(store)
        monkeypatch.setattr(svc_module, "LOCK_TIMEOUT_SECONDS", 0)
        await service._get_credential_lock(PATH).acquire()  # another refresh of this sign-in holds it

        await service._refresh_all_tokens()

        [line] = _at_least(logging.WARNING, caplog)
        assert (line.getMessage(), line.levelno) == (SKIPPED, logging.WARNING)
        assert fields(line).items() >= {"outcome": "skipped", "reason": "lock_wait_timeout", "mcp_server_id": INST}.items()
        assert events(caplog, CR.EVENT_END) == []

    async def test_a_record_that_cannot_be_read_is_one_check_failure_and_no_end(
        self, service: svc_module.MCPTokenRefreshService, store: FakeConfigService, caplog: pytest.LogCaptureFixture,
    ) -> None:
        _seed(store)
        real = store.get_config

        async def unreadable(key: str, *args: object, **kwargs: object) -> object:
            if key == PATH:
                raise ConnectionError("store down")
            return await real(key, *args, **kwargs)

        store.get_config = unreadable  # type: ignore[method-assign]

        await service._refresh_all_tokens()

        assert events(caplog, CR.EVENT_END) == []
        [line] = events(caplog, CR.EVENT_CHECK_FAILED)
        assert (fields(line)["error_type"], fields(line)["mcp_server_id"]) == ("ConnectionError", INST)
        assert "org_id" not in fields(line)

    @pytest.mark.parametrize("due", [True, False], ids=["after-a-refresh", "not-due"])
    async def test_a_scheduling_failure_is_one_full_check_failure_with_the_org(
        self, service: svc_module.MCPTokenRefreshService, store: FakeConfigService, endpoint: Endpoint,
        caplog: pytest.LogCaptureFixture, due: bool,
    ) -> None:
        endpoint.answers = [OK]
        _seed(store, record=_record(due=due))
        service.schedule_token_refresh = AsyncMock(side_effect=RuntimeError("timer"))  # type: ignore[method-assign]

        await service._refresh_all_tokens()

        assert [fields(r)["outcome"] for r in events(caplog, CR.EVENT_END)] == (["success"] if due else [])
        [line] = events(caplog, CR.EVENT_CHECK_FAILED)
        assert (line.levelno, fields(line)["error_class"], fields(line)["org_id"]) == (logging.ERROR, "internal", ORG)
        assert_no_secrets(caplog)

    async def test_a_timer_whose_refresh_raises_logs_on_the_timers_root(
        self, service: svc_module.MCPTokenRefreshService, caplog: pytest.LogCaptureFixture,
    ) -> None:
        service._refresh_credential = AsyncMock(side_effect=KeyError("bug"))  # type: ignore[method-assign]
        root = rc.set_context("sys-armed-by")
        try:
            await service._delayed_refresh(PATH, 0)
        finally:
            rc.reset_context(root)

        [line] = events(caplog, CR.EVENT_CHECK_FAILED)
        assert line.request_id.startswith("sys-credential_refresh-")
        assert fields(line).items() >= {
            "scheduled_by": "sys-armed-by", "trigger": "scheduled", "mcp_server_id": INST, "error_class": "internal",
        }.items()

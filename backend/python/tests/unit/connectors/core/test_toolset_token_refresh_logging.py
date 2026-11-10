"""What the toolset token refresh service logs: one refresh of one toolset sign-in, from the logs alone.

The real OAuthProvider and the real OAuth app resolver run against a scripted token endpoint
(tests/support/refresh_log_fakes.py). Records are captured from every logger at DEBUG, so a line
written under another logger name (or by the resolver) still counts.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.registry.toolset_registry import get_toolset_registry
from app.connectors.core.base.token_service import oauth_service
from app.connectors.core.base.token_service import toolset_token_refresh_service as ttrs
from app.connectors.core.base.token_service.oauth_service import OAuthToken
from app.connectors.core.base.token_service.token_refresh_service import (
    CredentialSaveError,
)
from app.core import org_context as oc
from app.utils import credential_refresh_log as CR
from app.utils import logging_fields
from app.utils import request_context as rc
from app.utils.logger import JsonFormatter
from app.utils.structured_logging import DefaultLoggingProvider
from tests.support.refresh_log_fakes import (
    INVALID_GRANT,
    REDIRECT_URI,
    SEEDED_RT,
    SEEDED_SECRET,
    TOKEN_URL,
    FakeConfigStore,
    FakeTokenEndpoint,
    assert_no_secrets,
    events,
    fields,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

LOGGER = "connector_service.refresh"
EVENT_SKIPPED = "credential_refresh.skipped"  # a literal: on main the helper has no such name, and the file must still collect
TYPE = "qatool"  # not in the toolset registry, so every OAuth field comes from the stored app
ORG = "o1"
INST = "11111111-1111-4111-8111-111111111111"  # the scan skips an instance id that is not a UUID
INST2 = "22222222-2222-4222-8222-222222222222"
OWNER = "u1"
PATH = f"/services/toolsets/{INST}/{OWNER}"
APPS = f"/services/oauths/toolsets/{TYPE}"
INSTANCES = "/services/toolset-instances"
APP_ID = "app-1"
SAVE_FAILED = "Saving the refreshed credentials failed"


def _token(*, due: bool = True) -> dict:
    created = datetime.now() - timedelta(seconds=7200 if due else 0)  # noqa: DTZ005 - OAuthToken is naive
    return {"access_token": "at-old", "refresh_token": SEEDED_RT, "expires_in": 3600, "created_at": created.isoformat()}


def _record(*, due: bool = True, app: str | None = APP_ID, org: str | None = ORG, instance: str = INST) -> dict:
    record = {"isAuthenticated": True, "toolsetType": TYPE, "instanceId": instance, "auth": {"type": "OAUTH"},
              "credentials": _token(due=due)}
    if app:
        record["oauthConfigId"] = app
    if org:
        record["orgId"] = org
    return record


def _app(**overrides: object) -> dict:
    config = {"clientId": "client-1", "clientSecret": SEEDED_SECRET, "authorizeUrl": "https://idp.example/authorize",
              "tokenUrl": TOKEN_URL, "redirectUri": REDIRECT_URI, "scopes": ["read"]}
    config = {k: v for k, v in {**config, **overrides}.items() if v is not None}
    return {"_id": APP_ID, "orgId": ORG, "config": config}


def _seed(config: FakeConfigStore, record: dict | None = None, path: str = PATH) -> None:
    config.store[APPS] = [_app()]
    config.store[path] = record if record is not None else _record()


def _at_least(level: int, caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.levelno >= level]


def _from_resolver(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.filename == "toolset_resolvers.py"]


@pytest.fixture(autouse=True)
def _raw_identity_fresh_gate_and_no_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.utils.logger.get_provider", lambda: DefaultLoggingProvider())
    monkeypatch.setattr(logging_fields, "_log_once_last", {})
    monkeypatch.setattr(ttrs, "INITIAL_RETRY_DELAY", 0)
    # Importing any *_main module sets a process-wide suffix on request_id (not on scheduled_by, a bare root).
    monkeypatch.setattr(rc, "_service_suffix", "")
    # A due token's timer fires at once instead of after the 1 s re-check and the 10 s cooldown.
    monkeypatch.setattr(ttrs, "MIN_IMMEDIATE_RECHECK_DELAY", 0)
    monkeypatch.setattr(ttrs, "REFRESH_COOLDOWN", 0)


@pytest.fixture
def endpoint(monkeypatch: pytest.MonkeyPatch) -> FakeTokenEndpoint:
    ep = FakeTokenEndpoint()
    monkeypatch.setattr(oauth_service, "ClientSession", ep.client_session)
    return ep


@pytest.fixture
def config() -> FakeConfigStore:
    return FakeConfigStore()


@pytest.fixture
async def service(config: FakeConfigStore, caplog: pytest.LogCaptureFixture) -> Iterator[ttrs.ToolsetTokenRefreshService]:
    get_toolset_registry()  # its one-time INFO line is not the refresh's
    caplog.set_level(logging.DEBUG)
    for name in ("connector_service", LOGGER):  # a level another test left on the parent must not hide a line
        caplog.set_level(logging.DEBUG, logger=name)
    svc = ttrs.ToolsetTokenRefreshService(config)  # type: ignore[arg-type]
    svc._running = True
    yield svc
    await svc.stop()


def _due_after_the_scan(svc: ttrs.ToolsetTokenRefreshService) -> None:
    """The scan sees the token 1 s before its refresh time, so it arms a timer; after that it is due, so the timer fires at once."""
    real = svc._calculate_refresh_delay
    first = [1.0]

    def delay(token: OAuthToken) -> tuple[float, datetime]:
        seconds, at = real(token)
        return (first.pop() if first else seconds), at

    svc._calculate_refresh_delay = delay  # type: ignore[method-assign]


class TestHealthyRefresh:
    async def test_scan_refresh_is_one_end_and_one_schedule_naming_the_sign_in(
        self, service: ttrs.ToolsetTokenRefreshService, config: FakeConfigStore, endpoint: FakeTokenEndpoint,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        endpoint.answers = ["ok"]
        _seed(config)

        await service._refresh_all_tokens()

        [end] = events(caplog, CR.EVENT_END)
        assert end.levelno == logging.INFO
        assert fields(end).items() >= {
            "outcome": "success", "refresh_kind": "toolset", "trigger": "scan", "toolset_id": INST, "owner_id": OWNER,
            "provider": TYPE, "org_id": ORG, "ctx_kind": "job", "oauth_app": "linked",
        }.items()
        assert isinstance(fields(end)["duration_ms"], int)
        assert "user_id" not in fields(end)
        # One refresh is two INFO lines, from any logger: its end and the next schedule.
        assert [r.getMessage() for r in _at_least(logging.INFO, caplog)] == [CR.EVENT_END, CR.EVENT_SCHEDULED]
        assert config.store[PATH]["credentials"]["refresh_token"] == "rt-new-1"

    async def test_scan_rearming_a_live_timer_adds_no_info_line(
        self, service: ttrs.ToolsetTokenRefreshService, config: FakeConfigStore, caplog: pytest.LogCaptureFixture,
    ) -> None:
        _seed(config, _record(due=False))
        config.store["/services/toolsets/not-an-instance"] = {}

        await service._refresh_all_tokens()
        caplog.clear()
        await service._refresh_all_tokens()

        assert _at_least(logging.INFO, caplog) == []

    async def test_a_new_timer_is_one_info_schedule_line(
        self, service: ttrs.ToolsetTokenRefreshService, config: FakeConfigStore, caplog: pytest.LogCaptureFixture,
    ) -> None:
        _seed(config, _record(due=False))

        await service._refresh_all_tokens()

        [line] = _at_least(logging.INFO, caplog)
        assert line.getMessage() == CR.EVENT_SCHEDULED
        assert fields(line).items() >= {"trigger": "scan", "toolset_id": INST, "org_id": ORG}.items()
        assert fields(line)["next_refresh_at"].endswith("Z")
        assert 0 < fields(line)["delay_s"] <= 3000

    async def test_timer_firing_is_its_own_root_scheduled_by_the_scan(
        self, service: ttrs.ToolsetTokenRefreshService, config: FakeConfigStore, endpoint: FakeTokenEndpoint,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        endpoint.answers = ["ok"]
        _seed(config)
        _due_after_the_scan(service)

        await service._refresh_all_tokens()
        [armed] = events(caplog, CR.EVENT_SCHEDULED)
        await asyncio.wait_for(service._refresh_tasks[PATH], 5)

        [end] = events(caplog, CR.EVENT_END)
        assert fields(end).items() >= {
            "outcome": "success", "trigger": "scheduled", "toolset_id": INST, "owner_id": OWNER, "org_id": ORG,
            "ctx_kind": "job", "scheduled_by": armed.request_id,
        }.items()
        assert end.request_id.startswith("sys-credential_refresh-")
        assert end.request_id != armed.request_id
        assert fields(armed)["trigger"] == "scan"

    async def test_oauth_callback_schedule_is_on_demand_and_its_timer_drops_the_user(
        self, service: ttrs.ToolsetTokenRefreshService, config: FakeConfigStore, endpoint: FakeTokenEndpoint,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        endpoint.answers = ["ok"]
        _seed(config)
        due = OAuthToken.from_dict(_token())
        root = rc.set_context("req-callback")
        try:
            with oc.bind(oc.OrgContext(ORG, "u-request", "user", "jwt", "req-callback")):
                await service.schedule_token_refresh(PATH, TYPE, due)
        finally:
            rc.reset_context(root)
        await asyncio.wait_for(service._refresh_tasks[PATH], 5)

        armed, rearmed = events(caplog, CR.EVENT_SCHEDULED)
        assert fields(armed).items() >= {
            "trigger": "on_demand", "refresh_kind": "toolset", "toolset_id": INST, "owner_id": OWNER, "org_id": ORG,
        }.items()
        [end] = events(caplog, CR.EVENT_END)
        assert fields(end).items() >= {
            "trigger": "scheduled", "org_id": ORG, "ctx_kind": "job", "scheduled_by": "req-callback",
        }.items()
        assert "user_id" not in fields(end)
        assert fields(rearmed)["trigger"] == "scheduled"

    async def test_two_orgs_in_one_scan_do_not_leak_into_each_other_or_the_scan(
        self, service: ttrs.ToolsetTokenRefreshService, config: FakeConfigStore, caplog: pytest.LogCaptureFixture,
    ) -> None:
        _seed(config, _record(due=False))
        config.store[f"/services/toolsets/{INST2}/u2"] = _record(due=False, org="o2", instance=INST2)
        config.store[f"/services/toolsets/{INST2}/u3"] = _record(due=False, org=None, instance=INST2)

        await service._refresh_all_tokens()

        assert [(fields(r)["owner_id"], fields(r).get("org_id")) for r in events(caplog, CR.EVENT_SCHEDULED)] == [
            (OWNER, ORG), ("u2", "o2"), ("u3", None),
        ]
        [summary] = [r for r in caplog.records if "Toolset scan summary" in r.getMessage()]
        assert "org_id" not in fields(summary)
        assert "refresh_kind" not in fields(summary)
        assert oc.peek() is None

    async def test_an_agent_sign_in_is_named_by_its_agent_key_not_as_a_user(
        self, service: ttrs.ToolsetTokenRefreshService, config: FakeConfigStore, endpoint: FakeTokenEndpoint,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        endpoint.answers = ["ok"]
        _seed(config, path=f"/services/toolsets/{INST}/agent-key-1")

        await service._refresh_all_tokens()

        [end] = events(caplog, CR.EVENT_END)
        assert fields(end)["owner_id"] == "agent-key-1"
        assert "user_id" not in fields(end)

    async def test_unlinked_sign_in_whose_instance_supplies_the_app_says_so_and_the_resolver_is_silent(
        self, service: ttrs.ToolsetTokenRefreshService, config: FakeConfigStore, endpoint: FakeTokenEndpoint,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        endpoint.answers = ["ok"]
        _seed(config, _record(app=None))
        config.store[INSTANCES] = [{"_id": INST, "oauthConfigId": APP_ID, "orgId": ORG}]

        await service._refresh_all_tokens()

        [end] = events(caplog, CR.EVENT_END)
        assert (fields(end)["outcome"], fields(end)["oauth_app"]) == ("success", "unlinked")
        assert _from_resolver(caplog) == []
        assert [r.getMessage() for r in _at_least(logging.INFO, caplog)] == [CR.EVENT_END, CR.EVENT_SCHEDULED]


class TestFailedRefresh:
    @pytest.mark.parametrize(("answer", "cls", "status", "error_type"), [
        ((503, f"upstream overloaded, retry ({SEEDED_RT})"), "unavailable", 503, "OAuthTokenRequestError"),
        ((502, "bad gateway"), "upstream_5xx", 502, "OAuthTokenRequestError"),
        ((429, "slow down"), "rate_limited", 429, "OAuthTokenRequestError"),
        ((403, "blocked by WAF"), "forbidden", 403, "Exception"),  # the bare 403 wrapper oauth_service raises
        (ConnectionRefusedError("refused"), "unavailable", None, "ConnectionRefusedError"),
        (TimeoutError(), "timeout", None, "TimeoutError"),
    ])
    async def test_endpoint_failure_on_a_scan_is_one_classified_warning(
        self, service: ttrs.ToolsetTokenRefreshService, config: FakeConfigStore, endpoint: FakeTokenEndpoint,
        caplog: pytest.LogCaptureFixture, answer: object, cls: str, status: int | None, error_type: str,
    ) -> None:
        endpoint.answers = [answer]
        _seed(config)

        await service._refresh_all_tokens()

        [end] = events(caplog, CR.EVENT_END)
        assert end.levelno == logging.WARNING
        assert end.exc_info is None
        assert fields(end).items() >= {
            "outcome": "failure", "error_class": cls, "error_type": error_type, "trigger": "scan", "toolset_id": INST,
            "org_id": ORG,
        }.items()
        assert fields(end).get("http_status") == status
        rendered = json.loads(JsonFormatter().format(end))
        assert (rendered["error_class"], rendered.get("http_status")) == (cls, status)
        assert [r.levelno for r in events(caplog, CR.EVENT_CHECK_FAILED)] == [logging.DEBUG]
        assert _at_least(logging.ERROR, caplog) == []
        assert not [r for r in caplog.records if "Traceback" in r.getMessage()]
        assert_no_secrets(caplog)

    async def test_revoked_grant_three_scans_strike_then_deauthenticate(
        self, service: ttrs.ToolsetTokenRefreshService, config: FakeConfigStore, endpoint: FakeTokenEndpoint,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        endpoint.answers = [INVALID_GRANT] * 3
        _seed(config)

        for _ in range(ttrs.MAX_REFRESH_TOKEN_INVALID_FAILURES):
            await service._refresh_all_tokens()

        ends = events(caplog, CR.EVENT_END)
        assert [r.levelno for r in ends] == [logging.WARNING] * 3
        assert {(f["error_class"], f["reason"], f["oauth_error"], f["http_status"]) for f in map(fields, ends)} == {
            ("auth_expired", "invalid_grant", "invalid_grant", 400),
        }
        assert [
            (r.levelno, fields(r)["strike"], fields(r)["counted"], fields(r)["error_class"], fields(r)["toolset_id"])
            for r in events(caplog, CR.EVENT_STRIKE)
        ] == [(logging.INFO, f"{n}/3", True, "auth_expired", INST) for n in (1, 2, 3)]
        [deauth] = events(caplog, CR.EVENT_DEAUTHENTICATED)
        assert deauth.levelno == logging.WARNING
        assert fields(deauth).items() >= {
            "reason": "refresh_token_invalid", "strikes": 3, "toolset_id": INST, "owner_id": OWNER, "org_id": ORG,
            "trigger": "scan",
        }.items()
        assert config.store[PATH]["isAuthenticated"] is False
        assert _at_least(logging.ERROR, caplog) == []
        assert_no_secrets(caplog)

    async def test_a_bug_in_the_deauth_on_the_third_strike_is_internal_not_an_expired_grant(
        self, service: ttrs.ToolsetTokenRefreshService, config: FakeConfigStore, endpoint: FakeTokenEndpoint,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # Review F1: raised inside `except RefreshTokenInvalidError`, the KeyError is chained to the rejection and classed by it.
        endpoint.answers = [INVALID_GRANT] * 3
        _seed(config)
        service._mark_toolset_unauthenticated = AsyncMock(side_effect=KeyError("bug"))  # type: ignore[method-assign]

        for _ in range(ttrs.MAX_REFRESH_TOKEN_INVALID_FAILURES):
            await service._refresh_all_tokens()

        [line] = events(caplog, CR.EVENT_CHECK_FAILED)
        assert (line.levelno, fields(line)["error_class"], fields(line)["error_type"]) == (logging.ERROR, "internal", "KeyError")
        assert "reason" not in fields(line)
        assert fields(line)["toolset_id"] == INST
        assert_no_secrets(caplog)

    async def test_deleted_oauth_app_is_one_error_an_hour_and_the_resolver_is_silent(
        self, service: ttrs.ToolsetTokenRefreshService, config: FakeConfigStore, caplog: pytest.LogCaptureFixture,
    ) -> None:
        _seed(config, _record(app="app-deleted"))

        await service._refresh_all_tokens()
        await service._refresh_all_tokens()

        [error] = _at_least(logging.ERROR, caplog)
        assert error.getMessage() == CR.EVENT_END
        assert fields(error).items() >= {
            "error_class": "misconfigured", "reason": "config_missing", "oauth_app": "linked", "toolset_id": INST,
        }.items()
        assert error.exc_text
        assert [r.levelno for r in events(caplog, CR.EVENT_END)] == [logging.ERROR, logging.DEBUG]
        assert _from_resolver(caplog) == []
        assert [r.getMessage() for r in _at_least(logging.WARNING, caplog)] == [CR.EVENT_END]

        # Positive control: once the hour is up it is an ERROR again.
        for key in logging_fields._log_once_last:
            logging_fields._log_once_last[key] -= CR.MISCONFIGURED_REPEAT_S + 1
        await service._refresh_all_tokens()
        assert [r.levelno for r in events(caplog, CR.EVENT_END)] == [logging.ERROR, logging.DEBUG, logging.ERROR]

    async def test_legacy_sign_in_without_credentials_keeps_the_resolver_reason_as_the_cause(
        self, service: ttrs.ToolsetTokenRefreshService, config: FakeConfigStore, caplog: pytest.LogCaptureFixture,
    ) -> None:
        _seed(config, _record(app=None))

        await service._refresh_all_tokens()

        [error] = _at_least(logging.ERROR, caplog)
        assert error.getMessage() == CR.EVENT_END
        assert fields(error).items() >= {"reason": "config_missing", "oauth_app": "unlinked"}.items()
        cause = error.exc_info[1].__cause__
        assert type(cause) is ValueError
        assert str(cause).startswith("No oauthConfigId found")
        [fallback] = [r for r in caplog.records if r.getMessage() == "Central OAuth app not loaded; trying the legacy credentials"]
        assert (fallback.levelno, fields(fallback)["error_type"]) == (logging.DEBUG, "ValueError")
        assert _from_resolver(caplog) == []
        assert [r.getMessage() for r in _at_least(logging.INFO, caplog)] == [CR.EVENT_END]

    @pytest.mark.parametrize("break_it", [
        pytest.param(lambda c: c.store.pop(PATH), id="no-config"),
        pytest.param(lambda c: c.store[PATH]["auth"].update(type="API_TOKEN"), id="api-token-type"),
        pytest.param(lambda c: c.store.update({APPS: [_app(tokenUrl=None)]}), id="no-token-url"),
        pytest.param(lambda c: c.store.update({APPS: [_app(clientSecret=None)]}), id="app-without-secret"),
        pytest.param(lambda c: c.store.update({APPS: []}), id="app-deleted"),
        pytest.param(lambda c: c.store[PATH].pop("oauthConfigId"), id="legacy-without-credentials"),
    ])
    async def test_each_config_error_is_config_missing_and_still_a_value_error(
        self, service: ttrs.ToolsetTokenRefreshService, config: FakeConfigStore, caplog: pytest.LogCaptureFixture,
        break_it: object,
    ) -> None:
        _seed(config)
        break_it(config)  # type: ignore[operator]

        with pytest.raises(ValueError):  # what callers and the older tests catch
            await service._perform_token_refresh(PATH, TYPE, SEEDED_RT)

        [end] = events(caplog, CR.EVENT_END)
        assert (end.levelno, fields(end)["error_class"], fields(end)["reason"]) == (logging.ERROR, "misconfigured", "config_missing")
        assert fields(end)["toolset_id"] == INST

    @pytest.mark.parametrize(("app", "oauth_app"), [(APP_ID, "linked"), (None, "unlinked")], ids=["linked", "legacy-fallback"])
    async def test_a_resolver_bug_is_internal_not_a_missing_config(
        self, service: ttrs.ToolsetTokenRefreshService, config: FakeConfigStore, caplog: pytest.LogCaptureFixture,
        app: str | None, oauth_app: str,
    ) -> None:
        # Review F3: the resolver wraps any error as `ValueError(...) from e`; only a ValueError of its own is a config verdict.
        # Unlinked, the instance supplies the app, so the bug reaches the legacy fallback's raise (implementation review C).
        _seed(config, _record(app=app))
        config.store[INSTANCES] = [{"_id": INST, "oauthConfigId": APP_ID, "orgId": ORG}]
        config.store[APPS] = ["not-an-app"]  # the resolver's lookup raises AttributeError

        with pytest.raises(ValueError):
            await service._perform_token_refresh(PATH, TYPE, SEEDED_RT)

        [end] = events(caplog, CR.EVENT_END)
        assert (end.levelno, fields(end)["error_class"], fields(end)["oauth_app"]) == (logging.ERROR, "internal", oauth_app)
        assert "reason" not in fields(end)

    async def test_save_failure_is_an_internal_error_after_a_warning_per_try(
        self, service: ttrs.ToolsetTokenRefreshService, config: FakeConfigStore, endpoint: FakeTokenEndpoint,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        endpoint.answers = ["ok"]
        _seed(config)
        config.save_result = False

        with pytest.raises(CredentialSaveError, match="^Failed to save refreshed credentials"):
            await service._perform_token_refresh(PATH, TYPE, SEEDED_RT)

        [end] = events(caplog, CR.EVENT_END)
        assert (end.levelno, bool(end.exc_text)) == (logging.ERROR, True)
        assert fields(end).items() >= {"error_class": "internal", "reason": "save_failed"}.items()
        # Every failed try warns, the last one too (main warned only before a retry).
        retries = [r for r in caplog.records if r.getMessage() == SAVE_FAILED]
        assert [(r.levelno, fields(r)["attempt"]) for r in retries] == [(logging.WARNING, n) for n in (1, 2, 3)]

    async def test_save_that_raises_warns_with_the_error_type(
        self, service: ttrs.ToolsetTokenRefreshService, config: FakeConfigStore, endpoint: FakeTokenEndpoint,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        endpoint.answers = ["ok"]
        _seed(config)
        # The provider's own best-effort write goes through; every one of the service's verified saves raises.
        config.set_config = AsyncMock(side_effect=[True] + [ConnectionError("store down")] * 3)  # type: ignore[method-assign]

        with pytest.raises(CredentialSaveError):
            await service._perform_token_refresh(PATH, TYPE, SEEDED_RT)

        retries = [r for r in caplog.records if r.getMessage() == SAVE_FAILED]
        assert [(fields(r)["attempt"], fields(r)["error_type"]) for r in retries] == [(n, "ConnectionError") for n in (1, 2, 3)]

    async def test_recursion_error_is_one_error_line_without_a_stack(
        self, service: ttrs.ToolsetTokenRefreshService, config: FakeConfigStore, endpoint: FakeTokenEndpoint,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        endpoint.answers = [RecursionError("maximum recursion depth exceeded")]
        _seed(config)

        await service._refresh_all_tokens()

        [error] = _at_least(logging.ERROR, caplog)
        assert error.getMessage() == CR.EVENT_END
        assert error.exc_info is None
        assert fields(error)["reason"] == "recursion"
        assert [r.levelno for r in events(caplog, CR.EVENT_CHECK_FAILED)] == [logging.DEBUG]

    async def test_lock_wait_timeout_is_one_skipped_warning(
        self, service: ttrs.ToolsetTokenRefreshService, config: FakeConfigStore, endpoint: FakeTokenEndpoint,
        caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _seed(config)
        monkeypatch.setattr(ttrs, "LOCK_TIMEOUT", 0)
        await service._get_toolset_lock(PATH).acquire()  # another refresh of this sign-in holds it

        await service._refresh_all_tokens()

        [line] = _at_least(logging.WARNING, caplog)
        assert (line.getMessage(), line.levelno) == (EVENT_SKIPPED, logging.WARNING)
        assert fields(line).items() >= {
            "outcome": "skipped", "reason": "lock_wait_timeout", "toolset_id": INST, "trigger": "scan",
        }.items()
        assert events(caplog, CR.EVENT_END) == []
        assert endpoint.sent == []

    async def test_immediate_refresh_failure_returns_false_with_one_end(
        self, service: ttrs.ToolsetTokenRefreshService, config: FakeConfigStore, endpoint: FakeTokenEndpoint,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        endpoint.answers = [(503, f"down ({SEEDED_RT})"), INVALID_GRANT]
        _seed(config)
        token = OAuthToken.from_dict(_token())

        assert await service._refresh_token_immediately(PATH, TYPE, token) == (None, False)
        assert await service._refresh_token_immediately(PATH, TYPE, token) == (None, False)

        assert [fields(r)["error_class"] for r in events(caplog, CR.EVENT_END)] == ["unavailable", "auth_expired"]
        assert [r.levelno for r in events(caplog, CR.EVENT_CHECK_FAILED)] == [logging.DEBUG]
        [strike] = events(caplog, CR.EVENT_STRIKE)
        assert fields(strike)["strike"] == "1/3"
        assert_no_secrets(caplog)


class TestOneEndPerAttempt:
    async def test_token_load_failure_is_a_check_failure_not_an_end(
        self, service: ttrs.ToolsetTokenRefreshService, config: FakeConfigStore, caplog: pytest.LogCaptureFixture,
    ) -> None:
        _seed(config)
        service._load_token_from_config = AsyncMock(side_effect=ConnectionError("config store down"))  # type: ignore[method-assign]

        await service._refresh_all_tokens()

        assert events(caplog, CR.EVENT_START) == []
        assert events(caplog, CR.EVENT_END) == []
        [line] = events(caplog, CR.EVENT_CHECK_FAILED)
        assert line.levelno == logging.WARNING
        assert fields(line).items() >= {"error_class": "unavailable", "toolset_id": INST, "trigger": "scan", "org_id": ORG}.items()

    async def test_failure_after_a_successful_refresh_is_one_success_and_one_check_failure(
        self, service: ttrs.ToolsetTokenRefreshService, config: FakeConfigStore, endpoint: FakeTokenEndpoint,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        endpoint.answers = ["ok"]
        _seed(config)
        service.schedule_token_refresh = AsyncMock(side_effect=KeyError("refresh_time"))  # type: ignore[method-assign]

        await service._refresh_all_tokens()

        assert [fields(r)["outcome"] for r in events(caplog, CR.EVENT_END)] == ["success"]
        [line] = events(caplog, CR.EVENT_CHECK_FAILED)
        assert (line.levelno, fields(line)["error_class"], bool(line.exc_text)) == (logging.ERROR, "internal", True)

    async def test_record_read_failure_on_the_scan_names_the_sign_in(
        self, service: ttrs.ToolsetTokenRefreshService, config: FakeConfigStore, caplog: pytest.LogCaptureFixture,
    ) -> None:
        _seed(config)
        config.read_error = ConnectionError("config store down")

        await service._refresh_all_tokens()

        [line] = events(caplog, CR.EVENT_CHECK_FAILED)
        assert line.levelno == logging.WARNING
        assert fields(line).items() >= {"error_class": "unavailable", "toolset_id": INST, "owner_id": OWNER, "trigger": "scan"}.items()
        assert events(caplog, CR.EVENT_END) == []


class TestTimerFailures:
    async def test_failure_escaping_the_refresh_keeps_the_timer_root(
        self, service: ttrs.ToolsetTokenRefreshService, caplog: pytest.LogCaptureFixture,
    ) -> None:
        # Review F4: caught after fresh_root exits, the line had the arming root and no scheduled_by.
        service._refresh_toolset_token = AsyncMock(side_effect=KeyError("bug"))  # type: ignore[method-assign]
        armed_by = rc.set_context("req-arming")
        try:
            await service._delayed_refresh(PATH, TYPE, 0)
        finally:
            rc.reset_context(armed_by)

        [line] = events(caplog, CR.EVENT_CHECK_FAILED)
        assert line.levelno == logging.ERROR
        assert fields(line).items() >= {
            "toolset_id": INST, "trigger": "scheduled", "error_class": "internal", "scheduled_by": "req-arming",
        }.items()
        assert line.request_id.startswith("sys-credential_refresh-")

    async def test_timer_whose_root_cannot_be_bound_logs_one_failure_and_does_not_raise(
        self, service: ttrs.ToolsetTokenRefreshService, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setattr(ttrs, "fresh_root", MagicMock(side_effect=RuntimeError("context broken")), raising=False)

        await service._delayed_refresh(PATH, TYPE, 0)

        [line] = events(caplog, CR.EVENT_CHECK_FAILED)
        assert line.levelno == logging.ERROR
        assert fields(line).items() >= {"toolset_id": INST, "trigger": "scheduled", "error_type": "RuntimeError"}.items()


class TestDeauthentication:
    async def test_write_that_changed_nothing_is_an_error(
        self, service: ttrs.ToolsetTokenRefreshService, config: FakeConfigStore, caplog: pytest.LogCaptureFixture,
    ) -> None:
        _seed(config)
        config.save_result = False

        await service._mark_toolset_unauthenticated(PATH)

        [line] = events(caplog, CR.EVENT_DEAUTHENTICATE_FAILED)
        assert (line.levelno, fields(line)["outcome"], fields(line)["reason"]) == (logging.ERROR, "failure", "not_updated")
        assert events(caplog, CR.EVENT_DEAUTHENTICATED) == []

    async def test_write_that_raised_is_an_error_with_a_stack(
        self, service: ttrs.ToolsetTokenRefreshService, config: FakeConfigStore, caplog: pytest.LogCaptureFixture,
    ) -> None:
        _seed(config)
        config.set_config = AsyncMock(side_effect=RuntimeError("store down"))  # type: ignore[method-assign]

        await service._mark_toolset_unauthenticated(PATH)

        [line] = events(caplog, CR.EVENT_DEAUTHENTICATE_FAILED)
        assert (line.levelno, fields(line)["error_class"], bool(line.exc_text)) == (logging.ERROR, "internal", True)
        assert events(caplog, CR.EVENT_DEAUTHENTICATED) == []

    async def test_record_gone_is_an_error_not_silence(
        self, service: ttrs.ToolsetTokenRefreshService, caplog: pytest.LogCaptureFixture,
    ) -> None:
        # Review F6: the strike counter is already reset, so without this line `strike 3/3` is the last word.
        await service._mark_toolset_unauthenticated(PATH)

        [line] = events(caplog, CR.EVENT_DEAUTHENTICATE_FAILED)
        assert (line.levelno, fields(line)["outcome"], fields(line)["reason"]) == (logging.ERROR, "failure", "not_found")

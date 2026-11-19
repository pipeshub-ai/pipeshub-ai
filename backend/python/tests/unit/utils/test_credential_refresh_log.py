"""The shared credential refresh log lines (app.utils.credential_refresh_log)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import pytest

from app.connectors.core.base.token_service.oauth_service import (
    OAuthTokenRequestError,
    RefreshConfigError,
    RefreshTokenInvalidError,
)
from app.connectors.core.base.token_service.token_refresh_service import (
    CredentialSaveError,
)
from app.core import org_context as oc
from app.utils import credential_refresh_log as CR
from app.utils import logging_fields
from app.utils.logger import TextFormatter, log_record_fields
from app.utils.logging_fields import current_log_fields, log_fields
from app.utils.request_context import get_context, reset_context, set_context
from app.utils.structured_logging import DefaultLoggingProvider

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

LOGGER = "test.credential_refresh"
SEEDED_RT = "rt-SEEDED-0123456789abcdef"
SEEDED_SECRET = "cs-SEEDED-fedcba9876543210"


@pytest.fixture(autouse=True)
def _raw_identity_and_fresh_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.utils.logger.get_provider", lambda: DefaultLoggingProvider())
    monkeypatch.setattr(logging_fields, "_log_once_last", {})


class _Records:
    """caplog.records of the running test phase (the list is replaced between phases)."""

    def __init__(self, caplog: pytest.LogCaptureFixture) -> None:
        self._caplog = caplog

    def __iter__(self) -> Iterator[logging.LogRecord]:
        return iter(self._caplog.records)

    def __getitem__(self, i: int) -> logging.LogRecord:
        return self._caplog.records[i]

    def __len__(self) -> int:
        return len(self._caplog.records)


@pytest.fixture
def records(caplog: pytest.LogCaptureFixture) -> _Records:
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    return _Records(caplog)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    now = [1000.0]
    monkeypatch.setattr(logging_fields.time, "monotonic", lambda: now[0])
    return now


def _logger() -> logging.Logger:
    return logging.getLogger(LOGGER)


def _ends(records: _Records) -> list[logging.LogRecord]:
    return [r for r in records if r.getMessage() == CR.EVENT_END]


def _raised(outer: BaseException, cause: BaseException | None = None) -> BaseException:
    try:
        try:
            if cause is not None:
                raise cause
        except BaseException as inner:
            raise outer from inner
        raise outer
    except BaseException as e:
        return e


def _provider_error(status: int, code: str | None = None) -> OAuthTokenRequestError:
    body = f'{{"error": "{code}", "refresh_token": "{SEEDED_RT}"}}' if code else f'upstream said no ({SEEDED_RT})'
    return OAuthTokenRequestError(
        f"OAuth token request failed with status {status}. Token URL: https://idp.example/token?client_secret="
        f"{SEEDED_SECRET}, Response: {body}",
        status=status, error_code=code,
    )


async def _attempt(exc: BaseException | None = None, *, once_key: str = "connector:c1") -> BaseException | None:
    try:
        async with CR.refresh_attempt(_logger(), once_key=once_key):
            if exc is not None:
                raise exc
    except BaseException as e:  # the test inspects what was re-raised
        return e
    return None


class TestRefreshAttempt:
    async def test_success_writes_a_debug_start_and_one_info_end(self, records: _Records) -> None:
        assert await _attempt() is None

        assert [(r.getMessage(), r.levelno) for r in records] == [
            (CR.EVENT_START, logging.DEBUG), (CR.EVENT_END, logging.INFO),
        ]
        fields = log_record_fields(records[1])
        assert fields["outcome"] == "success"
        assert isinstance(fields["duration_ms"], int)
        assert "reason" not in fields

    async def test_adopted_success_says_why_no_request_was_sent(self, records: _Records) -> None:
        async with CR.refresh_attempt(_logger(), once_key="connector:c1") as attempt:
            attempt.reason = "adopted"

        [end] = _ends(records)
        assert log_record_fields(end)["reason"] == "adopted"

    async def test_attempt_fields_are_on_its_end_either_way(self, records: _Records) -> None:
        async with CR.refresh_attempt(_logger(), once_key="toolset:t1") as attempt:
            attempt.fields["oauth_app"] = "unlinked"
        with pytest.raises(TimeoutError):
            async with CR.refresh_attempt(_logger(), once_key="toolset:t1") as attempt:
                attempt.fields["oauth_app"] = "linked"
                raise TimeoutError

        assert [(log_record_fields(r)["outcome"], log_record_fields(r)["oauth_app"]) for r in _ends(records)] == [
            ("success", "unlinked"), ("failure", "linked"),
        ]

    async def test_an_attempt_field_cannot_take_an_end_line_key(self, records: _Records) -> None:
        with pytest.raises(TimeoutError):
            async with CR.refresh_attempt(_logger(), once_key="toolset:t1") as attempt:
                for key in ("reason", "duration_ms", "outcome"):
                    with pytest.raises(ValueError, match=repr(key)):
                        attempt.fields[key] = "x"
                    with pytest.raises(ValueError, match=repr(key)):
                        attempt.fields.update({key: "x"})
                raise TimeoutError

        [end] = _ends(records)  # the attempt's own error still ends it
        assert (log_record_fields(end)["error_class"], log_record_fields(end)["outcome"]) == ("timeout", "failure")
        assert isinstance(log_record_fields(end)["duration_ms"], int)

    @pytest.mark.parametrize(("exc", "cls", "status"), [
        (_provider_error(503), "unavailable", 503),
        (_provider_error(502), "upstream_5xx", 502),
        (_provider_error(429), "rate_limited", 429),
        (_raised(Exception("Token refresh failed with 403 Forbidden"), _provider_error(403)), "forbidden", 403),
        (TimeoutError(), "timeout", None),
        (ConnectionRefusedError("refused"), "unavailable", None),
    ])
    async def test_upstream_failure_is_one_classified_warning_without_a_stack(
        self, records: _Records, exc: BaseException, cls: str, status: int | None,
    ) -> None:
        assert await _attempt(exc) is exc

        [end] = _ends(records)
        fields = log_record_fields(end)
        assert end.levelno == logging.WARNING
        assert end.exc_info is None
        assert end.exc_text is None
        assert fields["outcome"] == "failure"
        assert fields["error_class"] == cls
        assert fields["error_type"] == type(exc).__name__
        assert fields.get("http_status") == status
        # The text of an upstream answer is left out; local errors keep theirs.
        assert ("error_message" in fields) is (status is None)

    async def test_timeout_is_no_longer_an_empty_message(self, records: _Records) -> None:
        await _attempt(TimeoutError())

        text = TextFormatter().format(_ends(records)[0])
        assert "error_class=timeout" in text
        assert "error_type=TimeoutError" in text

    async def test_revoked_grant_is_auth_expired_with_the_oauth_code(self, records: _Records) -> None:
        exc = _raised(RefreshTokenInvalidError("Refresh token rejected by provider"), _provider_error(400, "invalid_grant"))

        await _attempt(exc)

        [end] = _ends(records)
        fields = log_record_fields(end)
        assert end.levelno == logging.WARNING
        assert (fields["error_class"], fields["reason"], fields["oauth_error"], fields["http_status"]) == (
            "auth_expired", "invalid_grant", "invalid_grant", 400,
        )

    async def test_revoked_grant_said_only_in_prose_is_still_auth_expired(self, records: _Records) -> None:
        # A 400 with no `error` code is bad_request to classify_error; the tag says what it is.
        exc = _raised(RefreshTokenInvalidError("refresh_token is invalid"), _provider_error(400))

        await _attempt(exc)

        assert log_record_fields(_ends(records)[0])["error_class"] == "auth_expired"

    async def test_rejected_oauth_client_stays_misconfigured_and_is_an_error(self, records: _Records) -> None:
        exc = _raised(RefreshTokenInvalidError("rejected"), _provider_error(401, "invalid_client"))

        await _attempt(exc)

        [end] = _ends(records)
        assert end.levelno == logging.ERROR
        assert log_record_fields(end)["error_class"] == "misconfigured"
        assert "reason" not in log_record_fields(end)  # not invalid_grant: the grant was never judged

    async def test_save_failure_is_an_internal_error_with_one_stack(self, records: _Records) -> None:
        exc = _raised(CredentialSaveError("Could not save refreshed credentials"))

        await _attempt(exc)

        [end] = _ends(records)
        fields = log_record_fields(end)
        assert end.levelno == logging.ERROR
        assert end.exc_text
        assert (fields["error_class"], fields["reason"]) == ("internal", "save_failed")

    async def test_cancelled_attempt_ends_at_info_and_propagates(self, records: _Records) -> None:
        exc = asyncio.CancelledError()

        assert await _attempt(exc) is exc

        [end] = _ends(records)
        assert end.levelno == logging.INFO
        assert log_record_fields(end)["error_class"] == "cancelled"

    async def test_recursion_error_is_one_error_without_stack_or_text(self, records: _Records) -> None:
        exc = _raised(RecursionError("maximum recursion depth exceeded"))

        await _attempt(exc)
        CR.log_check_failed(_logger(), exc, once_key="connector:c1")

        [end] = _ends(records)
        fields = log_record_fields(end)
        assert end.levelno == logging.ERROR
        assert end.exc_info is None
        assert end.exc_text is None
        assert "error_message" not in fields
        assert (fields["error_class"], fields["reason"], fields["error_type"]) == ("internal", "recursion", "RecursionError")
        [outer] = [r for r in records if r.getMessage() == CR.EVENT_CHECK_FAILED]
        assert outer.levelno == logging.DEBUG

    async def test_no_token_or_secret_reaches_a_record(self, records: _Records) -> None:
        await _attempt(_raised(RefreshTokenInvalidError(f"rejected. {_provider_error(400, 'invalid_grant')}"),
                               _provider_error(400, "invalid_grant")))
        await _attempt(_provider_error(503))
        await _attempt(_raised(RefreshTokenInvalidError(f"rejected. {_provider_error(401, 'invalid_client')}"),
                               _provider_error(401, "invalid_client")))

        assert [r.levelno for r in _ends(records)] == [logging.WARNING, logging.WARNING, logging.ERROR]
        assert _ends(records)[-1].exc_text
        for record in records:
            text = TextFormatter().format(record) + repr(log_record_fields(record))
            assert SEEDED_RT not in text
            assert SEEDED_SECRET not in text
            assert "idp.example" not in text


class TestMisconfiguredOncePerHour:
    async def test_error_once_then_debug_until_the_hour_is_up(
        self, records: _Records, clock: list[float],
    ) -> None:
        def levels() -> list[int]:
            return [r.levelno for r in records if r.getMessage() in (CR.EVENT_END, CR.EVENT_CHECK_FAILED)]

        for _ in range(2):
            exc = await _attempt(RefreshConfigError("No config found for connector c1"))
            CR.log_check_failed(_logger(), exc, once_key="connector:c1")
        assert levels() == [logging.ERROR, logging.DEBUG, logging.DEBUG, logging.DEBUG]
        assert log_record_fields(_ends(records)[0])["error_class"] == "misconfigured"
        assert _ends(records)[0].exc_text

        clock[0] += CR.MISCONFIGURED_REPEAT_S
        await _attempt(RefreshConfigError("No config found for connector c1"))
        assert levels()[-1] == logging.ERROR

    async def test_the_gate_is_per_credential(self, records: _Records, clock: list[float]) -> None:
        await _attempt(RefreshConfigError("missing"), once_key="connector:c1")
        await _attempt(RefreshConfigError("missing"), once_key="connector:c2")

        assert [r.levelno for r in _ends(records)] == [logging.ERROR, logging.ERROR]

    async def test_outer_handler_stays_quiet_when_the_repeat_was_not_emitted(
        self, caplog: pytest.LogCaptureFixture, clock: list[float],
    ) -> None:
        # At INFO the DEBUG repeat is not emitted, so log_failure cannot mark it; the gate keeps the outer line down too.
        caplog.set_level(logging.INFO, logger=LOGGER)
        await _attempt(RefreshConfigError("missing"))
        exc = await _attempt(RefreshConfigError("missing"))
        CR.log_check_failed(_logger(), exc, once_key="connector:c1")

        assert [r.levelno for r in caplog.records] == [logging.ERROR]


class TestCheckFailed:
    async def test_failure_already_ended_drops_to_debug(self, records: _Records) -> None:
        exc = await _attempt(_provider_error(503))

        CR.log_check_failed(_logger(), exc, once_key="connector:c1")

        [outer] = [r for r in records if r.getMessage() == CR.EVENT_CHECK_FAILED]
        assert outer.levelno == logging.DEBUG

    def test_failure_outside_an_attempt_is_logged_in_full_and_writes_no_end(self, records: _Records) -> None:
        CR.log_check_failed(_logger(), _raised(KeyError("credentials")), once_key="connector:c1")

        assert not _ends(records)
        [line] = records
        assert line.levelno == logging.ERROR
        assert line.exc_text
        assert log_record_fields(line)["error_class"] == "internal"
        assert log_record_fields(line)["error_message"] == "KeyError: 'credentials'"

    def test_recursion_outside_an_attempt_is_an_error(self, records: _Records) -> None:
        CR.log_check_failed(_logger(), RecursionError(), once_key="connector:c1")

        [line] = records
        assert line.levelno == logging.ERROR
        assert line.exc_info is None


def _line(records: _Records) -> dict[str, object]:
    _logger().info("x")
    return log_record_fields(records[-1])


class TestLogRefreshFailure:
    def _while_handling_a_rejection(self) -> BaseException:
        rejection = _raised(RefreshTokenInvalidError(f"rejected. {_provider_error(400, 'invalid_grant')}"),
                            _provider_error(400, "invalid_grant"))
        try:
            try:
                raise rejection
            except RefreshTokenInvalidError:
                raise RuntimeError("graph down")  # noqa: B904 - the implicit context is the case under test
        except RuntimeError as e:
            return e

    def test_text_from_the_token_endpoint_never_reaches_the_line_or_stack(self, records: _Records) -> None:
        CR.log_refresh_failure(_logger(), "x.failed", self._while_handling_a_rejection(), level=logging.ERROR)

        [line] = records
        text = TextFormatter().format(line) + repr(log_record_fields(line))
        assert line.exc_text
        assert "error_message" not in log_record_fields(line)
        for seeded in (SEEDED_RT, SEEDED_SECRET, "idp.example"):
            assert seeded not in text
        assert "RuntimeError: <message omitted>" in line.exc_text

    @pytest.mark.parametrize("make", [
        lambda: _provider_error(503), lambda: RefreshConfigError("missing"),
        lambda: RecursionError("maximum recursion depth exceeded"),
    ], ids=["upstream", "misconfigured", "recursion"])
    async def test_a_caller_line_for_what_the_attempt_ended_is_debug(
        self, caplog: pytest.LogCaptureFixture, clock: list[float], make: Callable[[], BaseException],
    ) -> None:
        # At INFO, as in production: the second attempt's misconfigured end is a DEBUG line that is not emitted.
        caplog.set_level(logging.INFO, logger=LOGGER)
        await _attempt(make())
        exc = await _attempt(make())
        caplog.clear()

        CR.log_refresh_failure(_logger(), CR.EVENT_CHECK_FAILED, exc)  # what a refresh_now caller does

        assert caplog.records == []

    async def test_a_caller_asking_for_a_higher_level_still_gets_it(self, records: _Records) -> None:
        exc = await _attempt(_provider_error(503))  # the end: WARNING

        CR.log_refresh_failure(_logger(), CR.EVENT_CHECK_FAILED, exc, level=logging.ERROR)
        CR.log_refresh_failure(_logger(), CR.EVENT_CHECK_FAILED, exc, level=logging.ERROR)

        assert [r.levelno for r in records if r.getMessage() == CR.EVENT_CHECK_FAILED] == [logging.ERROR, logging.DEBUG]

    def test_never_raises_and_falls_back_to_a_text_free_line(self, records: _Records) -> None:
        class Exotic(Exception):
            def __getattribute__(self, name: str) -> object:
                if name in ("status", "status_code", "error_code", "refresh_reason"):
                    raise RuntimeError("attribute access exploded")
                return super().__getattribute__(name)

        CR.log_refresh_failure(_logger(), "x.failed", Exotic(SEEDED_RT))

        [line] = records
        assert (line.getMessage(), line.levelno, line.error_type) == ("x.failed", logging.ERROR, "Exotic")
        assert SEEDED_RT not in TextFormatter().format(line)


class TestRefreshScope:
    def test_binds_kind_trigger_and_ids(self, records: _Records) -> None:
        with CR.refresh_scope(CR.Kind.CONNECTOR, trigger=CR.Trigger.SCAN, connector_id="c1", provider="jira"):
            fields = _line(records)

        assert (fields["refresh_kind"], fields["trigger"], fields["connector_id"], fields["provider"]) == (
            "connector", "scan", "c1", "jira",
        )
        assert "trigger" not in current_log_fields()

    def test_no_trigger_keeps_the_bound_one_else_on_demand(self, records: _Records) -> None:
        with CR.refresh_scope(CR.Kind.CONNECTOR, connector_id="c1"):
            assert _line(records)["trigger"] == "on_demand"
        with CR.refresh_scope(CR.Kind.CONNECTOR, trigger=CR.Trigger.SCHEDULED), CR.refresh_scope(CR.Kind.CONNECTOR):
            assert _line(records)["trigger"] == "scheduled"

    def test_a_sync_trigger_is_not_taken_for_a_refresh_trigger(self, records: _Records) -> None:
        with log_fields(trigger="boot"), CR.refresh_scope(CR.Kind.CONNECTOR, connector_id="c1"):
            assert _line(records)["trigger"] == "on_demand"

    def test_binds_the_org_as_a_job_when_none_is_bound(self, records: _Records) -> None:
        with CR.refresh_scope(CR.Kind.CONNECTOR, org_id="o1"):
            fields = _line(records)
            assert oc.current_org_id() == "o1"

        assert (fields["org_id"], fields["ctx_kind"], fields["ctx_source"]) == ("o1", "job", "job:credential_refresh")
        assert oc.peek() is None

    def test_an_ambient_org_wins(self, records: _Records) -> None:
        user = oc.OrgContext("o1", "u1", "user", "jwt", "-")
        with oc.bind(user), CR.refresh_scope(CR.Kind.CONNECTOR, org_id="o1"):
            assert oc.peek() is user
        with oc.bind(user), CR.refresh_scope(CR.Kind.CONNECTOR, org_id="o2"):  # never OrgMismatch
            assert oc.peek() is user

    def test_a_service_token_context_counts_as_bound(self) -> None:
        service = oc.OrgContext(oc.SYSTEM_ORG_ID, None, "service", "scoped_jwt", "-")
        with oc.bind(service), CR.refresh_scope(CR.Kind.CONNECTOR, org_id="o1"):
            assert oc.peek() is service

    def test_inside_a_system_scope_binds_the_org(self) -> None:
        with oc.system("scan"), CR.refresh_scope(CR.Kind.CONNECTOR, org_id="o1"):
            assert oc.current_org_id() == "o1"

    @pytest.mark.parametrize("org", [None, ""])
    def test_no_org_binds_nothing(self, org: str | None) -> None:
        with CR.refresh_scope(CR.Kind.CONNECTOR, org_id=org):
            assert oc.peek() is None


class TestFreshRoot:
    def test_new_root_names_the_one_that_armed_it_and_drops_the_user(self, records: _Records) -> None:
        token = set_context("req-armed")
        try:
            with oc.bind(oc.OrgContext("o1", "u1", "user", "jwt", "req-armed")), CR.fresh_root():
                root = get_context().root_id
                fields = _line(records)
            assert get_context().root_id == "req-armed"
        finally:
            reset_context(token)

        assert root.startswith("sys-credential_refresh-")
        assert fields["scheduled_by"] == "req-armed"
        assert fields["org_id"] == "o1"
        assert "user_id" not in fields
        assert fields["ctx_kind"] == "job"

    def test_without_an_org_binds_none(self) -> None:
        with CR.fresh_root():
            assert oc.peek() is None
            assert get_context().root_id.startswith("sys-credential_refresh-")
            assert "scheduled_by" not in current_log_fields()

    def test_a_service_token_org_is_not_rebound(self) -> None:
        service = oc.OrgContext(oc.SYSTEM_ORG_ID, None, "service", "scoped_jwt", "-")
        with oc.bind(service), log_fields(connector_id="c1"), CR.fresh_root():
            assert oc.peek() is service


class TestSmallLines:
    @pytest.mark.parametrize(("doc", "org"), [
        ({"orgId": "o1"}, "o1"), ({"orgId": ""}, None), ({}, None), (None, None), ([("orgId", "o1")], None),
    ])
    def test_org_of(self, doc: object, org: str | None) -> None:
        assert CR.org_of(doc) == org

    @pytest.mark.parametrize(("changed", "level"), [(True, logging.INFO), (False, logging.DEBUG)])
    def test_scheduled_is_info_only_for_a_new_time(
        self, records: _Records, changed: bool, level: int,
    ) -> None:
        at = datetime(2026, 11, 6, 9, 30, 5, tzinfo=timezone.utc)

        CR.log_scheduled(_logger(), next_refresh_at=at, delay_s=59.6, changed=changed)

        [line] = records
        assert line.levelno == level
        assert log_record_fields(line).items() >= {"next_refresh_at": "2026-11-06T09:30:05Z", "delay_s": 60}.items()

    def test_strike_and_deauthenticated(self, records: _Records) -> None:
        rejected_client = _raised(RefreshTokenInvalidError(f"rejected. {_provider_error(401, 'invalid_client')}"),
                                  _provider_error(401, "invalid_client"))
        CR.log_strike(_logger(), strike=2, max_strikes=3, cause=rejected_client)
        CR.log_deauthenticated(_logger(), reason="refresh_token_invalid", strikes=3)

        strike, deauth = records
        fields = log_record_fields(strike)
        assert (strike.levelno, fields["strike"], fields["counted"], fields["error_class"], fields["oauth_error"]) == (
            logging.INFO, "2/3", True, "misconfigured", "invalid_client",
        )
        assert "error_message" not in fields
        assert SEEDED_RT not in TextFormatter().format(strike)
        assert deauth.levelno == logging.WARNING
        assert log_record_fields(deauth)["reason"] == "refresh_token_invalid"
        assert "path" not in log_record_fields(deauth)

    def test_skipped_is_a_warning_with_the_bound_ids(self, records: _Records) -> None:
        with CR.refresh_scope(CR.Kind.TOOLSET, trigger=CR.Trigger.SCAN, toolset_id="t1", owner_id="agent-key-1"):
            CR.log_skipped(_logger(), reason="lock_wait_timeout")

        [line] = records
        assert (line.getMessage(), line.levelno) == ("credential_refresh.skipped", logging.WARNING)
        assert log_record_fields(line).items() >= {
            "outcome": "skipped", "reason": "lock_wait_timeout", "refresh_kind": "toolset", "trigger": "scan",
            "toolset_id": "t1", "owner_id": "agent-key-1",
        }.items()

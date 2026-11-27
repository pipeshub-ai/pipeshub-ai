"""What a failed run tells its owner, and when dead credentials switch a connector off."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.core.base.connector import failure_notices as fn
from app.connectors.core.base.token_service.oauth_service import (
    RefreshTokenInvalidError,
)
from app.connectors.core.base.token_service.startup_service import startup_service
from app.connectors.core.base.token_service.token_refresh_service import (
    SWITCHED_OFF_NOTICE_WINDOW_SECONDS,
    TokenRefreshService,
)
from app.services.notification.types import NotificationSeverity, NotificationType

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.asyncio


class Connector:
    def __init__(self, run: Callable[[Connector], object]) -> None:
        self.run_state = fn.SyncRunState()
        self.connector_id = "c1"
        self.connector_name = SimpleNamespace(value="GITHUB")
        self.logger = logging.getLogger("test.failure_notices")
        self.notify = AsyncMock()
        self.config_service = MagicMock(get_config=AsyncMock(return_value={}))
        self._run = run

    def _notification_title(self, event: str) -> str:
        return f"Eng: {event}"

    @fn.reports_failed_sync("GitHub")
    async def run_sync(self) -> None:
        self._run(self)


@pytest.fixture(autouse=True)
def refresher(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    service = MagicMock(note_access_token_rejected=AsyncMock(return_value=False), refresh_now=AsyncMock())
    monkeypatch.setattr(startup_service, "get_token_refresh_service", lambda: service)
    return service


def sent(connector: Connector) -> list[tuple[NotificationType, str]]:
    return [(c.kwargs["type"], c.kwargs["title"]) for c in connector.notify.await_args_list]


def _raise(error: Exception) -> Callable[[Connector], None]:
    def run(_c: Connector) -> None:
        raise error
    return run


async def test_a_clean_run_sends_nothing_and_clears_the_rejections(refresher: MagicMock) -> None:
    c = Connector(lambda _c: None)
    await c.run_sync()
    assert sent(c) == []
    refresher.note_access_token_accepted.assert_called_once_with("c1")


async def test_a_failure_sends_one_sync_error_and_re_raises_it() -> None:
    c = Connector(_raise(RuntimeError("boom")))
    with pytest.raises(RuntimeError, match="boom"):
        await c.run_sync()
    assert sent(c) == [(NotificationType.CONNECTOR_SYNC_ERROR, "Eng: sync failed")]


async def test_credentials_rejected_then_swallowed_by_a_step_still_fail_the_run() -> None:
    c = Connector(lambda c: c.run_state.reject("reconnect it"))
    with pytest.raises(fn.ReconnectRequiredError):
        await c.run_sync()
    assert sent(c) == [(NotificationType.CONNECTOR_AUTH_ERROR, "Eng: needs to be reconnected")]


async def test_the_rejection_is_reported_not_the_error_it_caused() -> None:
    def run(c: Connector) -> None:
        c.run_state.reject("reconnect it")
        raise RuntimeError("every configured org failed")

    c = Connector(run)
    with pytest.raises(fn.ReconnectRequiredError):
        await c.run_sync()
    assert [t for t, _ in sent(c)] == [NotificationType.CONNECTOR_AUTH_ERROR]


async def test_the_rejection_that_switches_the_connector_off_sends_no_second_notice(refresher: MagicMock) -> None:
    refresher.note_access_token_rejected.return_value = True
    c = Connector(lambda c: c.run_state.reject("reconnect it", unrefreshable_token="t"))
    with pytest.raises(fn.ReconnectRequiredError):
        await c.run_sync()
    assert sent(c) == []


async def test_a_token_that_can_be_refreshed_is_not_counted(refresher: MagicMock) -> None:
    c = Connector(lambda c: c.run_state.reject("reconnect it"))
    with pytest.raises(fn.ReconnectRequiredError):
        await c.run_sync()
    refresher.note_access_token_rejected.assert_not_awaited()


async def test_a_rate_limited_run_and_one_already_reported_send_nothing() -> None:
    def limited(c: Connector) -> None:
        c.run_state.rate_limited = True
        raise RuntimeError("429")

    told = RuntimeError("told")
    told._notification_sent = True
    for run in (limited, _raise(told)):
        c = Connector(run)
        with pytest.raises(RuntimeError):
            await c.run_sync()
        assert sent(c) == []


@pytest.mark.parametrize("fails", [True, False])
async def test_problems_are_one_warning_whether_or_not_the_run_fails(fails: bool) -> None:
    def run(c: Connector) -> None:
        c.run_state.note("repo a")
        c.run_state.note("repo a")
        c.run_state.note("repo b")
        if fails:
            raise fn.SyncIncompleteError("incomplete")

    c = Connector(run)
    if fails:
        with pytest.raises(fn.SyncIncompleteError):
            await c.run_sync()
    else:
        await c.run_sync()
    [call] = c.notify.await_args_list
    assert call.kwargs["severity"] is NotificationSeverity.WARNING
    assert call.kwargs["message"].count("repo a") == 1 and "repo b" in call.kwargs["message"]


async def test_one_run_does_not_inherit_the_last_runs_state() -> None:
    runs = iter([lambda c: c.run_state.reject("reconnect it"), lambda c: None])
    c = Connector(lambda c: next(runs)(c))
    with pytest.raises(fn.ReconnectRequiredError):
        await c.run_sync()
    await c.run_sync()


async def test_a_notice_that_cannot_be_sent_does_not_hide_the_failure() -> None:
    c = Connector(_raise(RuntimeError("boom")))
    c.notify.side_effect = ConnectionError("broker down")
    with pytest.raises(RuntimeError, match="boom"):
        await c.run_sync()


async def test_the_same_failure_reads_the_same_every_run() -> None:
    first = fn.stable_reason("request 8f14e45fceea167a at 1764070000123 failed")
    assert first == fn.stable_reason("request c9f0f895fb98ab91 at 1764073600456 failed")
    assert "failed" in first and "deadline" in fn.stable_reason("deadline exceeded")


@pytest.mark.parametrize(("config", "why"), [
    ({"auth": {"authType": "API_TOKEN"}}, "entered by hand"),
    ({"auth": {"authType": "OAUTH"}, "credentials": {"access_token": "a"}}, "no refresh token"),
])
async def test_a_sign_in_that_cannot_be_renewed_says_why(config: dict[str, Any], why: str) -> None:
    c = Connector(None)
    c.config_service.get_config.return_value = config
    assert why in (await fn.renew_access_token(c) or "")


async def test_renewing_tells_a_dead_refresh_token_from_a_failure_that_may_pass(refresher: MagicMock) -> None:
    c = Connector(None)
    c.config_service.get_config.return_value = {
        "auth": {"authType": "OAUTH"}, "credentials": {"access_token": "a", "refresh_token": "r"},
    }
    assert await fn.renew_access_token(c) is None
    refresher.refresh_now.side_effect = RefreshTokenInvalidError("revoked")
    assert "also rejected" in (await fn.renew_access_token(c) or "")
    refresher.refresh_now.side_effect = ConnectionError("timeout")
    with pytest.raises(ConnectionError):
        await fn.renew_access_token(c)


APP = {"orgId": "o1", "type": "GitHub Teams", "name": "Eng", "scope": "team", "createdBy": "u1", "lastSyncedBy": "u2"}


def _service(app: dict[str, Any] = APP) -> tuple[TokenRefreshService, AsyncMock, AsyncMock]:
    graph = AsyncMock()
    graph.get_document = AsyncMock(return_value=app)
    graph.update_node = AsyncMock(return_value=True)
    producer = AsyncMock()
    producer.send_message = AsyncMock(return_value=True)
    return TokenRefreshService(MagicMock(), graph, producer), graph, producer


def _notices(producer: AsyncMock) -> list[dict[str, Any]]:
    return [c.kwargs["message"] for c in producer.send_message.await_args_list if c.kwargs["topic"] == "notification"]


async def test_the_third_rejection_in_a_row_switches_off_and_tells_creator_and_last_syncer() -> None:
    service, graph, producer = _service()
    error = fn.ReconnectRequiredError("401")
    assert [await service.note_access_token_rejected("c1", "tok", error) for _ in range(3)] == [False, False, True]
    assert graph.update_node.await_args.args[2]["isAuthenticated"] is False
    [notice] = _notices(producer)
    assert notice["recipientUserIds"] == ["u1", "u2"] and notice["type"] == "CONNECTOR_AUTH_ERROR"
    assert "tok" not in str(notice["title"])


async def test_a_new_token_or_a_good_run_starts_the_count_again() -> None:
    service, graph, _ = _service()
    error = fn.ReconnectRequiredError("401")
    for token in ("a", "a", "b", "b"):
        assert not await service.note_access_token_rejected("c1", token, error)
    service.note_access_token_accepted("c1")
    assert not await service.note_access_token_rejected("c1", "b", error)
    graph.update_node.assert_not_awaited()


async def test_a_dead_refresh_token_tells_the_owner_once_per_person() -> None:
    service, _, producer = _service({**APP, "lastSyncedBy": "u1"})
    for _ in range(3):
        await service._handle_refresh_token_invalid("c1", RefreshTokenInvalidError("revoked"))
    [notice] = _notices(producer)
    assert notice["recipientUserIds"] == ["u1"]


async def test_a_notice_that_fails_still_leaves_the_connector_off() -> None:
    service, graph, producer = _service()
    producer.send_message.side_effect = [None, ConnectionError("broker down")]
    await service._mark_connector_unauthenticated("c1")
    graph.update_node.assert_awaited_once()


async def test_switched_off_without_a_notice_leaves_the_run_to_tell_the_owner() -> None:
    service, graph, producer = _service()
    producer.send_message.side_effect = ConnectionError("broker down")
    results = [await service.note_access_token_rejected("c1", "tok", fn.ReconnectRequiredError("401")) for _ in range(3)]
    assert results == [False, False, False]
    assert graph.update_node.await_args.args[2]["isAuthenticated"] is False


async def test_a_run_whose_refresh_token_switched_the_connector_off_sends_no_second_notice(refresher: MagicMock) -> None:
    refresher.told_switched_off_recently = MagicMock(return_value=True)
    c = Connector(lambda c: c.run_state.reject("reconnect it"))
    with pytest.raises(fn.ReconnectRequiredError):
        await c.run_sync()
    assert sent(c) == []


async def test_an_incomplete_run_with_no_reason_noted_still_tells_the_owner() -> None:
    c = Connector(_raise(fn.SyncIncompleteError("removing repositories that left the scope: store down")))
    with pytest.raises(fn.SyncIncompleteError):
        await c.run_sync()
    assert sent(c) == [(NotificationType.CONNECTOR_WARNING, "Eng: sync finished with problems")]
    assert "store down" in c.notify.await_args.kwargs["message"]


async def test_a_rate_limited_run_sends_no_problem_notice() -> None:
    def limited_and_incomplete(c: Connector) -> None:
        c.run_state.rate_limited = True
        c.run_state.note("project a did not sync")
        raise fn.SyncIncompleteError("429")

    def limited_but_finished(c: Connector) -> None:
        c.run_state.rate_limited = True
        c.run_state.note("members of acme could not be listed")

    c = Connector(limited_and_incomplete)
    with pytest.raises(fn.SyncIncompleteError):
        await c.run_sync()
    finished = Connector(limited_but_finished)
    await finished.run_sync()
    assert sent(c) == sent(finished) == []


async def test_the_service_remembers_telling_the_owner_until_a_good_run() -> None:
    service, _, _ = _service()
    for _ in range(3):
        await service._handle_refresh_token_invalid("c1", RefreshTokenInvalidError("revoked"))
    assert service.told_switched_off_recently("c1") is True
    service.note_access_token_accepted("c1")
    assert service.told_switched_off_recently("c1") is False


async def test_a_switch_off_told_long_ago_or_to_nobody_leaves_the_run_to_tell_the_owner() -> None:
    service, _, _ = _service()
    for _ in range(3):
        await service._handle_refresh_token_invalid("c1", RefreshTokenInvalidError("revoked"))
    service._told_switched_off["c1"] -= SWITCHED_OFF_NOTICE_WINDOW_SECONDS + 1
    assert service.told_switched_off_recently("c1") is False

    untold, _, producer = _service()
    producer.send_message.side_effect = ConnectionError("broker down")
    for _ in range(3):
        await untold._handle_refresh_token_invalid("c2", RefreshTokenInvalidError("revoked"))
    assert untold.told_switched_off_recently("c2") is False


async def test_the_owner_is_told_even_when_switching_off_stops_the_sync_doing_it() -> None:
    service, graph, producer = _service()
    write_started, release = asyncio.Event(), asyncio.Event()

    async def slow_write(*_args: object) -> bool:
        write_started.set()
        await release.wait()
        return True

    graph.update_node = AsyncMock(side_effect=slow_write)
    sync = asyncio.create_task(service._mark_connector_unauthenticated("c1"))
    await write_started.wait()
    sync.cancel()  # what the appDisabled consumer does to a running sync
    with pytest.raises(asyncio.CancelledError):
        await sync
    release.set()
    await asyncio.gather(*service._switch_off_tasks)
    assert len(_notices(producer)) == 1
    assert service.told_switched_off_recently("c1") is True


async def test_the_owner_is_told_even_when_the_sync_is_stopped_while_the_event_is_published() -> None:
    """The consumer can act on appDisabled before the broker acknowledges sending it."""
    service, _graph, producer = _service()
    published, release = asyncio.Event(), asyncio.Event()
    send = producer.send_message

    async def slow_send(*args: object, **kwargs: object) -> object:
        result = await send(*args, **kwargs)
        if kwargs.get("topic") == "entity-events":
            published.set()
            await release.wait()
        return result

    producer.send_message = AsyncMock(side_effect=slow_send)
    sync = asyncio.create_task(service._mark_connector_unauthenticated("c1"))
    await published.wait()
    sync.cancel()  # what the appDisabled consumer does to a running sync
    with pytest.raises(asyncio.CancelledError):
        await sync
    release.set()
    await asyncio.gather(*service._switch_off_tasks)
    assert len(_notices(producer)) == 1
    assert service.told_switched_off_recently("c1") is True

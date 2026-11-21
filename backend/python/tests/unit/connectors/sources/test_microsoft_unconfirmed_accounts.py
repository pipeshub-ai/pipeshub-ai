"""Outlook, OneDrive and SharePoint: a stored account the user listing does not vouch for is read by id.

A cut or failed listing no longer keeps every stored account: each one it leaves out is read (gone or disabled
loses the connector, found enabled keeps it), at most ``lookups_per_sync()`` per sync, and an account no read
can confirm loses it after ``UNCONFIRMED_SYNCS_BEFORE_CLOSE`` syncs in a row.
"""

import contextlib
import logging
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from msgraph.generated.models.o_data_errors.o_data_error import ODataError

from app.config.constants.arangodb import Connectors
from app.connectors.sources.microsoft.common import account_check
from app.connectors.sources.microsoft.common.account_check import (
    ACCOUNT_CHECK_KEY,
    LOOKUPS_ENV,
    UNCONFIRMED_SYNCS_BEFORE_CLOSE,
    lookups_per_sync,
)
from app.connectors.sources.microsoft.common.msgraph_client import UserListing
from app.connectors.sources.microsoft.onedrive.connector import OneDriveConnector
from app.connectors.sources.microsoft.outlook.connector import OutlookConnector
from app.connectors.sources.microsoft.sharepoint_online.connector import (
    SharePointConnector,
)
from app.models.entities import AppUser
from app.services.notification.types import NotificationOutcome

CONNECTORS = ("outlook", "onedrive", "sharepoint")


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from app.connectors.sources.microsoft.common import msgraph_client

    monkeypatch.setattr(msgraph_client, "asyncio", SimpleNamespace(sleep=AsyncMock()))


class _SyncPoint:
    """The connector's user sync point, kept in memory across syncs."""

    def __init__(self) -> None:
        self.data: dict[str, dict[str, Any]] = {}

    async def read_sync_point(self, key: str) -> dict[str, Any]:
        return dict(self.data.get(key, {}))

    async def update_sync_point(self, key: str, sync_point_data: dict[str, Any]) -> None:
        self.data[key] = dict(sync_point_data)


def _odata(code: int) -> ODataError:
    e = ODataError()
    e.response_status_code = code
    return e


class World:
    """One connector over a store of linked accounts and a directory that answers per account id."""

    def __init__(self, name: str, stored_ids: list[str]) -> None:
        self.name = name
        self.stored = {sid: AppUser(app_name=Connectors.OUTLOOK, connector_id="c1", source_user_id=sid,
                                    email=f"{sid}@x.com", full_name=sid) for sid in stored_ids}
        self.status: dict[str, object] = {}          # id -> "gone" | "disabled" | "present" | an exception
        self.reads: list[str] = []
        proc = MagicMock()
        proc.org_id = "org"
        proc.on_new_app_users = AsyncMock()
        proc.on_new_user_groups = AsyncMock()
        proc.get_all_active_users = AsyncMock(return_value=[])
        proc.get_app_user_links = AsyncMock(side_effect=lambda *a, **k: list(self.stored.values()))

        async def withdraw(_cid: str, users: list[AppUser], **kwargs: object) -> None:
            for u in users:
                self.stored.pop(u.source_user_id)

        proc.withdraw_app_user_access = AsyncMock(side_effect=withdraw)
        cls = {"outlook": OutlookConnector, "onedrive": OneDriveConnector, "sharepoint": SharePointConnector}[name]
        self.c = cls(logging.getLogger("t"), proc, MagicMock(), MagicMock(), "c1", "team", "creator")
        self.c.user_sync_point = _SyncPoint()
        self.c.notify = AsyncMock()
        self.c.notify_and_wait = AsyncMock(return_value=NotificationOutcome.SENT)
        if name == "outlook":
            self.c.external_users_client = MagicMock()
            self.c.external_users_client.client.users.by_user_id.side_effect = self._outlook_read
        else:
            self.c.msgraph_client = MagicMock()
            self.c.msgraph_client.get_user_status = AsyncMock(side_effect=self._status)

    def _answer(self, sid: str) -> object:
        self.reads.append(sid)
        return self.status.get(sid, "present")

    async def _status(self, sid: str) -> str:
        a = self._answer(sid)
        if isinstance(a, Exception):
            raise a
        return a

    def _outlook_read(self, sid: str) -> MagicMock:
        async def get(*_a: object, **_k: object) -> MagicMock:
            a = self._answer(sid)
            if isinstance(a, Exception):
                raise a
            if a == "gone":
                raise _odata(404)
            return MagicMock(account_enabled=a == "present")
        return MagicMock(get=get)

    def list(self, enabled: list[str], *, complete: bool, disabled: tuple[str, ...] = ()) -> None:
        """What the next user listing answers: these accounts, and whether it is complete."""
        if self.name == "outlook":
            def g(uid: str, on: bool) -> MagicMock:
                u = MagicMock()
                u.id, u.display_name, u.mail, u.user_principal_name = uid, uid, f"{uid}@x.com", None
                u.given_name = u.surname = None
                u.account_enabled, u.user_type = on, "Member"
                return u
            users = [g(s, True) for s in enabled] + [g(s, False) for s in disabled]
            data = MagicMock(value=users, odata_next_link=None, odata_count=len(users) + (0 if complete else 7))
            self.c.external_users_client.users_user_list_user = AsyncMock(return_value=MagicMock(success=True, data=data))
        else:
            listing = UserListing([AppUser(app_name=Connectors.ONEDRIVE, connector_id="c1", source_user_id=s, email=f"{s}@x.com",
                                           full_name=s, is_active=s not in disabled) for s in [*enabled, *disabled]])
            listing.complete = complete
            self.c.msgraph_client.get_all_users = AsyncMock(return_value=listing)

    def fail_listing(self) -> None:
        """The listing answers nothing: the API refuses it."""
        if self.name == "outlook":
            self.c.external_users_client.users_user_list_user = AsyncMock(
                return_value=MagicMock(success=False, data=None, error="Server error (status 500)"))
        else:
            self.c.msgraph_client.get_all_users = AsyncMock(side_effect=_odata(500))

    async def sync(self) -> None:
        with contextlib.suppress(Exception):     # a failed listing still stops the user sync; what it withdrew is judged
            await self.c._sync_users()


def _closing_notices(w: World) -> list[object]:
    return [c for c in w.c.notify_and_wait.await_args_list if "could not confirm" in c.kwargs.get("title", "")]


@pytest.fixture(params=CONNECTORS)
def world(request: pytest.FixtureRequest) -> Callable[[list[str]], World]:
    return lambda stored: World(request.param, stored)


@pytest.mark.asyncio
async def test_a_cut_listing_reads_the_accounts_it_left_out_and_withdraws_the_deleted_one(world: Callable[[list[str]], World]) -> None:
    w = world(["a", "b", "gone"])
    w.status["gone"] = "gone"
    w.list(["a"], complete=False)
    await w.sync()
    assert set(w.stored) == {"a", "b"}
    assert set(w.reads) == {"b", "gone"}          # the listed account is not read


@pytest.mark.asyncio
async def test_a_cut_listing_keeps_an_account_a_read_finds_enabled(world: Callable[[list[str]], World]) -> None:
    w = world(["a", "b"])
    w.list(["a"], complete=False)
    await w.sync()
    assert set(w.stored) == {"a", "b"}
    w.c.data_entities_processor.withdraw_app_user_access.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_cut_listing_withdraws_an_account_a_read_finds_disabled(world: Callable[[list[str]], World]) -> None:
    w = world(["a", "off"])
    w.status["off"] = "disabled"
    w.list(["a"], complete=False)
    await w.sync()
    assert set(w.stored) == {"a"}


@pytest.mark.asyncio
async def test_a_failed_listing_still_reads_every_stored_account(world: Callable[[list[str]], World]) -> None:
    w = world(["a", "gone", "off"])
    w.status.update(gone="gone", off="disabled")
    w.fail_listing()
    await w.sync()
    assert set(w.stored) == {"a"}


@pytest.mark.asyncio
async def test_an_account_no_read_confirms_loses_access_at_the_bound_and_the_owner_is_told(world: Callable[[list[str]], World]) -> None:
    w = world(["a", "x"])
    w.status["x"] = _odata(500)
    w.fail_listing()
    for _ in range(UNCONFIRMED_SYNCS_BEFORE_CLOSE - 1):
        await w.sync()
        assert set(w.stored) == {"a", "x"}
    assert not _closing_notices(w)
    await w.sync()
    assert set(w.stored) == {"a"}  # "a" was read and found every time
    assert len(_closing_notices(w)) == 1


@pytest.mark.asyncio
async def test_a_confirming_read_starts_the_count_again(world: Callable[[list[str]], World]) -> None:
    w = world(["a", "x"])
    w.list(["a"], complete=True)
    w.status["x"] = _odata(500)
    for _ in range(UNCONFIRMED_SYNCS_BEFORE_CLOSE - 1):
        await w.sync()
    w.status["x"] = "present"
    await w.sync()                                  # one good read
    w.status["x"] = _odata(500)
    for _ in range(UNCONFIRMED_SYNCS_BEFORE_CLOSE - 1):
        await w.sync()
    assert set(w.stored) == {"a", "x"}
    await w.sync()
    assert set(w.stored) == {"a"}


@pytest.mark.asyncio
async def test_a_listing_that_shows_the_account_again_starts_the_count_again(world: Callable[[list[str]], World]) -> None:
    w = world(["a", "x"])
    w.status["x"] = _odata(500)
    w.list(["a"], complete=True)
    for _ in range(UNCONFIRMED_SYNCS_BEFORE_CLOSE - 1):
        await w.sync()
    w.list(["a", "x"], complete=True)
    await w.sync()
    w.list(["a"], complete=True)
    for _ in range(UNCONFIRMED_SYNCS_BEFORE_CLOSE - 1):
        await w.sync()
    assert set(w.stored) == {"a", "x"}


@pytest.mark.asyncio
async def test_reads_are_capped_per_sync_and_every_account_is_reached(world: Callable[[list[str]], World],
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(LOOKUPS_ENV, "2")
    w = world(["a", "b", "c", "d", "e"])
    w.status.update(c="gone", e="gone")
    w.list(["a"], complete=False)
    await w.sync()
    assert len(set(w.reads)) == 2                   # a 404 is read three times (read_user_status), still one account
    await w.sync()
    assert set(w.reads) == {"b", "c", "d", "e"}     # b..e read in turn: c and e were gone
    assert set(w.stored) == {"a", "b", "d"}


@pytest.mark.asyncio
async def test_an_account_counting_towards_the_bound_is_read_every_sync_beside_the_others(world: Callable[[list[str]], World],
                                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(LOOKUPS_ENV, "2")
    w = world(["a", "b", "c", "x"])
    w.status["x"] = _odata(500)
    w.c.user_sync_point.data[ACCOUNT_CHECK_KEY] = {"streaks": '{"x": 1}', "cursor": ""}
    w.list(["a"], complete=False)
    for _ in range(UNCONFIRMED_SYNCS_BEFORE_CLOSE - 1):
        await w.sync()
    assert w.reads.count("x") == UNCONFIRMED_SYNCS_BEFORE_CLOSE - 1     # half the reads: closed at the bound
    assert set(w.reads) - {"x"} == {"b", "c"}                          # the other half: the rest in turn
    assert "x" not in w.stored


@pytest.mark.asyncio
async def test_accounts_whose_reads_keep_failing_do_not_starve_the_others(world: Callable[[list[str]], World],
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(LOOKUPS_ENV, "2")
    w = world(["a", "x", "y", "gone"])
    w.status.update(x=RuntimeError("connection reset"), y=RuntimeError("connection reset"), gone="gone")
    w.c.user_sync_point.data[ACCOUNT_CHECK_KEY] = {"streaks": '{"x": 1, "y": 1}', "cursor": ""}
    w.list(["a"], complete=False)
    await w.sync()
    assert "gone" not in w.stored and {"x", "y"} <= set(w.stored)      # transport failures are not counted


@pytest.mark.asyncio
async def test_two_syncs_that_close_different_accounts_each_tell_the_owner(world: Callable[[list[str]], World]) -> None:
    w = world(["a", "x", "y"])
    w.status["x"] = _odata(500)
    w.list(["a", "y"], complete=True)
    for _ in range(UNCONFIRMED_SYNCS_BEFORE_CLOSE):
        await w.sync()
    w.status["y"] = _odata(500)
    w.list(["a"], complete=True)
    for _ in range(UNCONFIRMED_SYNCS_BEFORE_CLOSE):
        await w.sync()
    notices = _closing_notices(w)
    assert set(w.stored) == {"a"} and len(notices) == 2
    assert all(n.kwargs["suppress_repeats"] is False for n in notices)


@pytest.mark.asyncio
async def test_a_notice_that_was_not_delivered_is_sent_by_the_next_sync(world: Callable[[list[str]], World]) -> None:
    w = world(["a", "x"])
    w.status["x"] = _odata(500)
    w.list(["a"], complete=True)
    w.c.notify_and_wait.return_value = NotificationOutcome.FAILED
    for _ in range(UNCONFIRMED_SYNCS_BEFORE_CLOSE):
        await w.sync()
    assert "x" not in w.stored and len(_closing_notices(w)) == 1
    assert w.c.user_sync_point.data[ACCOUNT_CHECK_KEY]["pending_notice"] == '["x"]'   # saved before the withdrawal
    w.c.notify_and_wait.return_value = NotificationOutcome.SENT
    await w.sync()
    await w.sync()
    assert len(_closing_notices(w)) == 2                              # sent once more, then no longer owed
    assert w.c.user_sync_point.data[ACCOUNT_CHECK_KEY]["pending_notice"] == "[]"


@pytest.mark.asyncio
async def test_a_close_waits_while_its_notice_cannot_be_saved(world: Callable[[list[str]], World]) -> None:
    w = world(["a", "x", "gone"])
    w.status.update(x=_odata(500), gone="gone")
    w.list(["a"], complete=True)
    for _ in range(UNCONFIRMED_SYNCS_BEFORE_CLOSE - 1):
        await w.sync()
    saved = w.c.user_sync_point.update_sync_point
    w.c.user_sync_point.update_sync_point = AsyncMock(side_effect=RuntimeError("store down"))
    w.c.notify_and_wait.return_value = NotificationOutcome.FAILED
    await w.sync()
    assert set(w.stored) == {"a", "x"} and not _closing_notices(w)   # the deleted account still goes; x waits
    w.c.user_sync_point.update_sync_point = saved
    await w.sync()
    assert set(w.stored) == {"a"} and len(_closing_notices(w)) == 1
    assert w.c.user_sync_point.data[ACCOUNT_CHECK_KEY]["pending_notice"] == '["x"]'


@pytest.mark.asyncio
async def test_without_the_saved_count_no_account_is_closed(world: Callable[[list[str]], World]) -> None:
    w = world(["a", "x"])
    w.status["x"] = _odata(500)
    w.c.user_sync_point.read_sync_point = AsyncMock(side_effect=RuntimeError("store down"))
    w.list(["a"], complete=True)
    for _ in range(UNCONFIRMED_SYNCS_BEFORE_CLOSE + 1):
        await w.sync()
    assert set(w.stored) == {"a", "x"}


@pytest.mark.asyncio
async def test_a_read_that_never_reached_microsoft_is_not_counted(world: Callable[[list[str]], World]) -> None:
    w = world(["a", "x"])
    w.status["x"] = RuntimeError("connection reset")
    w.list(["a"], complete=True)
    for _ in range(UNCONFIRMED_SYNCS_BEFORE_CLOSE + 1):
        await w.sync()
    assert set(w.stored) == {"a", "x"}


@pytest.mark.parametrize("raw", ["1", "0", "-1", "many"])
def test_the_read_cap_must_be_a_positive_whole_number(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv(LOOKUPS_ENV, raw)
    with pytest.raises(ValueError, match=LOOKUPS_ENV):
        lookups_per_sync()


def test_the_read_cap_defaults_to_a_bounded_number(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(LOOKUPS_ENV, raising=False)
    assert lookups_per_sync() == account_check.DEFAULT_LOOKUPS_PER_SYNC

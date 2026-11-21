"""Outlook and SharePoint: a group whose member read Microsoft refuses keeps its stored members for at most
``UNCONFIRMED_SYNCS_BEFORE_CLOSE`` syncs in a row, then is written with no members.

Before, the stored members were kept for as long as the refusal lasted, so a person removed from the group kept
reading its mail, posts and files with no bound.
"""

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from msgraph.generated.models.o_data_errors.o_data_error import ODataError

from app.connectors.core.registry.filters import FilterCollection
from app.connectors.sources.microsoft.common.account_check import (
    GROUP_CHECK_KEY,
    UNCONFIRMED_SYNCS_BEFORE_CLOSE,
)
from app.connectors.sources.microsoft.outlook.connector import OutlookConnector
from app.connectors.sources.microsoft.sharepoint_online.connector import (
    SharePointConnector,
)

N = UNCONFIRMED_SYNCS_BEFORE_CLOSE


class _SyncPoint:
    def __init__(self) -> None:
        self.data: dict[str, dict[str, Any]] = {}
        self.fail_reads = False

    async def read_sync_point(self, key: str) -> dict[str, Any]:
        if self.fail_reads:
            raise RuntimeError("store down")
        return dict(self.data.get(key, {}))

    async def update_sync_point(self, key: str, sync_point_data: dict[str, Any]) -> None:
        self.data[key] = dict(sync_point_data)


def _odata(code: int) -> ODataError:
    e = ODataError()
    e.response_status_code = code
    return e


def _member(email: str) -> MagicMock:
    m = MagicMock()
    m.mail = m.user_principal_name = email
    m.display_name = email
    m.id = email
    m.odata_type = "#microsoft.graph.user"
    return m


def _group(gid: str) -> MagicMock:
    g = MagicMock()
    g.id = gid
    g.display_name = gid
    g.description = None
    g.additional_data = {}
    g.mail = f"{gid}@x.com"
    g.mail_nickname = gid
    g.created_date_time = None
    return g


def _processor() -> MagicMock:
    proc = MagicMock()
    proc.org_id = "org"
    proc.on_new_user_groups = AsyncMock()
    proc.on_new_record_groups = AsyncMock()
    proc.note_kept_user_group = AsyncMock()
    return proc


def _written(proc: MagicMock, gid: str) -> list[list[str]]:
    """The member emails of every write of group ``gid``, in order."""
    out = []
    for call in proc.on_new_user_groups.await_args_list:
        for group, users in call.args[0]:
            if group.source_user_group_id == gid:
                out.append([u.email for u in users])
    return out


class Outlook:
    def __init__(self) -> None:
        self.proc = _processor()
        self.c = OutlookConnector(logging.getLogger("t"), self.proc, MagicMock(), MagicMock(), "c1", "team", "creator")
        self.c.user_sync_point = _SyncPoint()
        self.c.notify = AsyncMock()
        self.c.sync_filters = FilterCollection()
        self.c._skipped = {"mailboxes": 0, "folders": 0, "groups": 0, "mails": 0}
        self.c._full_sync_incomplete = False
        self.c._user_cache = {"ann@x.com": "u-ann"}
        self.c._get_all_microsoft_365_groups = AsyncMock(return_value=[_group("g1")])
        self.c._drop_missing_groups = AsyncMock()
        self.c._transform_group_to_record_group = MagicMock(return_value=None)
        self.c.external_users_client = MagicMock()
        self.answer: object = [_member("ann@x.com")]
        page = MagicMock()
        page.odata_next_link = None

        async def get(*_a: object, **_k: object) -> MagicMock:
            if isinstance(self.answer, BaseException):
                raise self.answer
            page.value = self.answer
            return page

        self.c.external_users_client.client.groups.by_group_id.return_value.transitive_members.get = get

    async def sync(self) -> None:
        await self.c._sync_user_groups()


class SharePoint:
    def __init__(self) -> None:
        self.proc = _processor()
        self.c = SharePointConnector(logging.getLogger("t"), self.proc, MagicMock(), MagicMock(), "c1", "team", "creator")
        self.c.user_sync_point = _SyncPoint()
        self.c._group_grace.sync_point = self.c.user_sync_point
        self.c.notify = AsyncMock()
        self.c.msgraph_client = MagicMock()
        self.c.msgraph_client.get_all_user_groups = AsyncMock(return_value=[_group("g1")])
        self.c.msgraph_client.get_groups_delta_response = AsyncMock(
            return_value={"groups": [_group("g1")], "delta_link": "https://graph/next-delta"})
        self.c._containing_groups = AsyncMock(return_value=[])
        self.answer: object = [{"id": "u-ann", "email": "ann@x.com", "name": "Ann"}]

        async def members(group_id: str, is_owner: bool = False) -> list[dict]:
            if isinstance(self.answer, BaseException):
                raise self.answer
            return self.answer

        self.c._fetch_graph_group_members = members
        self.applied: list[bool] = []
        self.full = False

    async def sync(self) -> None:
        g = self.c._group_grace
        await g.load()
        if self.full:
            await self.c._perform_initial_full_sync()
        else:
            self.applied.append(await self.c._perform_delta_sync("https://graph/delta", "k"))
        await g.finish()


WORLDS = {"outlook": Outlook, "sharepoint": SharePoint}


@pytest.fixture(params=["outlook", "sharepoint-delta", "sharepoint-full"])
def world(request: pytest.FixtureRequest) -> Outlook | SharePoint:
    w = WORLDS[request.param.split("-")[0]]()
    if request.param == "sharepoint-full":
        w.full = True
    return w


@pytest.mark.asyncio
async def test_a_lasting_refusal_withdraws_the_stored_members_at_the_bound_and_tells_the_owner_once(
        world: Outlook | SharePoint, caplog: pytest.LogCaptureFixture) -> None:
    await world.sync()
    assert _written(world.proc, "g1") == [["ann@x.com"]]
    world.answer = _odata(403)
    for _ in range(N - 1):
        await world.sync()
    assert _written(world.proc, "g1") == [["ann@x.com"]], "kept within the grace"
    world.c.notify.assert_not_awaited()
    with caplog.at_level(logging.ERROR):
        await world.sync()
    assert _written(world.proc, "g1")[-1] == [], "the stored members lose the group's access at the bound"
    assert any("unverified past the grace" in r.getMessage() and r.levelno == logging.ERROR for r in caplog.records)
    world.c.notify.assert_awaited_once()
    await world.sync()
    assert _written(world.proc, "g1")[-1] == [], "stays closed while the refusal lasts"
    world.c.notify.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_read_after_the_close_writes_the_members_back(world: Outlook | SharePoint) -> None:
    world.answer = _odata(403)
    for _ in range(N):
        await world.sync()
    assert _written(world.proc, "g1")[-1] == []
    world.answer = [_member("ann@x.com")] if isinstance(world, Outlook) else [{"id": "u-ann", "email": "ann@x.com", "name": "Ann"}]
    await world.sync()
    assert _written(world.proc, "g1")[-1] == ["ann@x.com"]
    assert world.c.user_sync_point.data[GROUP_CHECK_KEY]["streaks"] == "{}"


@pytest.mark.asyncio
async def test_a_successful_read_starts_the_count_again(world: Outlook | SharePoint) -> None:
    ok = world.answer
    for _ in range(3):
        world.answer = _odata(503)
        for _ in range(N - 1):
            await world.sync()
        world.answer = ok
        await world.sync()
    assert [] not in _written(world.proc, "g1")
    world.c.notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_read_that_never_reached_microsoft_keeps_and_is_not_counted(world: Outlook | SharePoint) -> None:
    world.answer = ConnectionError("transport closed")
    for _ in range(N + 2):
        await world.sync()
    assert [] not in _written(world.proc, "g1")
    world.c.notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_unreadable_count_closes_no_group(world: Outlook | SharePoint) -> None:
    world.c.user_sync_point.fail_reads = True
    world.answer = _odata(403)
    for _ in range(N + 2):
        await world.sync()
    assert [] not in _written(world.proc, "g1")


@pytest.mark.asyncio
async def test_sharepoint_keeps_the_change_page_held_after_the_close() -> None:
    w = SharePoint()
    w.answer = _odata(403)
    for _ in range(N + 1):
        await w.sync()
    assert w.applied == [False] * (N + 1), "a closed group is read again on every sync until its members are"
    assert _written(w.proc, "g1")[-1] == []


@pytest.mark.asyncio
async def test_sharepoint_counts_a_group_read_twice_in_one_sync_once() -> None:
    w = SharePoint()
    w.answer = _odata(403)
    g = w.c._group_grace
    for _ in range(N - 1):
        await g.load()
        await w.c._perform_delta_sync("https://graph/delta", "k")
        await w.c._perform_initial_full_sync()
        await g.finish()
    assert [] not in _written(w.proc, "g1")


@pytest.mark.asyncio
async def test_sharepoint_site_group_drops_an_entra_group_refused_past_the_grace_and_keeps_its_other_members() -> None:
    """A SharePoint group holding an Entra group (its claim) flattens the Entra group's people into its own members,
    so the same bound applies there; the SharePoint group's own users stay."""
    w = SharePoint()
    gid = "abcdefab-1234-5678-9012-abcdefabcdef"
    principals = [{"LoginName": f"c:0o.c|federateddirectoryclaimprovider|{gid}", "PrincipalType": 4},
                  {"LoginName": "i:0#.f|membership|bob@x.com", "Email": "bob@x.com", "Id": 7, "Title": "Bob", "PrincipalType": 1}]
    g = w.c._group_grace

    async def members() -> list[str]:
        await g.load()
        try:
            return sorted(u.email for u in await w.c._site_group_member_users("s1", "5", principals))
        finally:
            await g.finish()

    assert await members() == ["ann@x.com", "bob@x.com"]
    w.answer = _odata(403)
    for _ in range(N - 1):
        with pytest.raises(ODataError):
            await members()  # the SharePoint group keeps its stored members within the grace
    assert await members() == ["bob@x.com"]
    w.c.notify.assert_awaited_once()
    w.answer = [{"id": "u-ann", "email": "ann@x.com", "name": "Ann"}]
    assert await members() == ["ann@x.com", "bob@x.com"]


@pytest.mark.asyncio
async def test_outlook_keeps_within_the_grace_through_note_kept() -> None:
    w = Outlook()
    w.answer = _odata(404)
    await w.sync()
    w.proc.note_kept_user_group.assert_awaited_once_with("c1", "g1")
    assert w.c._full_sync_incomplete

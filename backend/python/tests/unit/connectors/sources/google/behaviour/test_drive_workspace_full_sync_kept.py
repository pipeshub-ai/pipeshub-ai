"""A full sync the runner starts keeps the links of the groups and shared drives the connector keeps as stored.

A stored user group or shared drive the run neither writes nor notes kept has every edge unlinked once the full sync
ends: a group its members and the grants made to it, a shared drive its members' access and its records' links. One
the directory or Drive no longer has is removed.
"""

from dataclasses import dataclass
from typing import Any

import pytest
from google_behaviour_fakes import ApiRequest, google_error
from test_drive_workspace_sync_behaviour import (  # noqa: F401
    ALICE,
    BOB,
    CONNECTOR_ID,
    Workspace,
    no_pacing_delays,
    ws,
)

from app.connectors.core.base.connector.connector_service import IncompleteSyncError
from app.connectors.sources.google.common.connector_google_exceptions import (
    DriveSyncIncomplete,
)

KEPT = "eng@example.com"
CAROL = "carol@example.com"
NESTED = "eng-oncall@example.com"
GONE = "gone@example.com"
ADMIN_API = "/admin/directory/v1"
DRIVE_API = "/drive/v3"


@dataclass
class Unlinked:
    """After a full sync: the stored nodes the runner unlinks (never written nor noted kept), or that were removed."""

    user_groups: set[str]
    record_groups: set[str]


async def _runner_full_sync(ws: Workspace) -> Unlinked:  # noqa: F811
    """A full sync the runner starts from no sync points, ended as the runner ends it. A user group is seen when
    written or noted kept; a record group when written, noted kept or holding a record the run wrote."""
    records = ws.records
    before_groups, before_drives = set(records.user_groups), set(records.record_groups)
    written_groups: set[str] = set()
    written_drives: set[str] = set()
    new_user_groups, new_record_groups, new_records = (
        records.on_new_user_groups, records.on_new_record_groups, records.on_new_records,
    )

    async def on_new_user_groups(groups: list[tuple[Any, list[Any]]], **kwargs: bool) -> None:
        written_groups.update(g.source_user_group_id for g, _ in groups)
        return await new_user_groups(groups, **kwargs)

    async def on_new_record_groups(groups: list[tuple[Any, list[Any]]]) -> None:
        written_drives.update(g.external_group_id for g, _ in groups)
        return await new_record_groups(groups)

    async def on_new_records(batch: list[tuple[Any, list[Any]]], **kwargs: bool) -> None:
        written_drives.update(r.external_record_group_id for r, _ in batch if r.external_record_group_id)
        return await new_records(batch, **kwargs)

    records.on_new_user_groups = on_new_user_groups  # type: ignore[method-assign]
    records.on_new_record_groups = on_new_record_groups  # type: ignore[method-assign]
    records.on_new_records = on_new_records  # type: ignore[method-assign]
    records.kept_user_groups.clear()
    records.kept_record_groups.clear()
    ws.sync_points.sync_points.clear()
    records.begin_full_sync_run(CONNECTOR_ID)
    connector = await ws.connector_()
    try:
        await connector.run_sync()
    except (IncompleteSyncError, DriveSyncIncomplete):
        pass
    finally:
        records.on_new_user_groups = new_user_groups  # type: ignore[method-assign]
        records.on_new_record_groups = new_record_groups  # type: ignore[method-assign]
        records.on_new_records = new_records  # type: ignore[method-assign]
    if not connector.full_sync_coverage().complete:
        records.full_sync_seen = None
        return Unlinked(
            before_groups - set(records.user_groups), before_drives - set(records.record_groups)
        )
    records.unlink_missed_by_full_sync()
    return Unlinked(
        before_groups - written_groups - set(records.kept_user_groups),
        before_drives - written_drives - set(records.kept_record_groups),
    )


def _groups_listed_without(ws: Workspace, group_email: str) -> None:  # noqa: F811
    """The directory's group listing leaves this group out; the group still exists."""
    real = ws.world._admin_groups

    def listing(req: ApiRequest) -> object:
        page = real(req)
        if isinstance(page, dict):
            page = {**page, "groups": [g for g in page.get("groups", []) if g["email"] != group_email]}
        return page

    ws.http.route("GET", f"{ADMIN_API}/groups", listing)


def _drives_listed_without(ws: Workspace, drive_id: str) -> None:  # noqa: F811
    """The admin's shared-drive listing leaves this drive out; the drive still exists, and its members list it."""
    real = ws.world._drives_list

    def listing(req: ApiRequest) -> object:
        page = real(req)
        if isinstance(page, dict) and req.query.get("useDomainAdminAccess") == "true":
            page = {**page, "drives": [d for d in page.get("drives", []) if d["id"] != drive_id]}
        return page

    ws.http.route("GET", f"{DRIVE_API}/drives", listing)


async def _two_groups_synced(ws: Workspace) -> None:  # noqa: F811
    ws.world.add_group(KEPT, [BOB])
    ws.world.add_group(GONE, [BOB])
    ws.world.add_item("f1", "plan.txt", parent="root-alice", owner=ALICE, perms=[
        {"type": "group", "role": "reader", "emailAddress": KEPT},
        {"type": "group", "role": "reader", "emailAddress": GONE},
    ])
    await ws.sync()
    assert {KEPT, GONE} <= set(ws.records.user_groups)
    del ws.world.groups[GONE]
    ws.world.files["f1"].perms = [p for p in ws.world.files["f1"].perms if p.get("emailAddress") != GONE]


async def test_a_group_left_out_of_the_listing_that_still_has_members_keeps_its_links(ws: Workspace) -> None:  # noqa: F811
    await _two_groups_synced(ws)
    _groups_listed_without(ws, KEPT)

    unlinked = await _runner_full_sync(ws)

    assert KEPT in ws.records.user_groups and KEPT not in ws.records.deleted_groups
    assert unlinked.user_groups & {KEPT} == set(), "members.list still answers: the group exists"
    assert unlinked.user_groups & {GONE} == {GONE}
    assert _members(ws, KEPT) == {BOB}, "written with the members the directory lists"
    assert KEPT not in ws.records.kept_user_groups, "written this run, so not noted kept"


async def _group_with_a_leaver_synced(ws: Workspace) -> None:  # noqa: F811
    """KEPT holds BOB directly and CAROL through a nested group, and grants f1; then BOB leaves it at the source
    and the listing leaves KEPT out."""
    ws.world.add_user(CAROL)
    ws.world.add_group(NESTED, [CAROL])
    ws.world.add_group(KEPT, [BOB, NESTED])
    ws.world.add_item("f1", "plan.txt", parent="root-alice", owner=ALICE, perms=[
        {"type": "group", "role": "reader", "emailAddress": KEPT},
    ])
    await ws.sync()
    assert _members(ws, KEPT) == {BOB, CAROL}
    ws.world.groups[KEPT]["members"] = [NESTED]
    _groups_listed_without(ws, KEPT)


async def test_a_user_who_left_a_group_left_out_of_the_listing_loses_its_access(ws: Workspace) -> None:  # noqa: F811
    await _group_with_a_leaver_synced(ws)

    unlinked = await _runner_full_sync(ws)

    assert _members(ws, KEPT) == {CAROL}, "the membership the directory lists now, nested members included"
    assert not _reaches_through_a_group(ws, BOB, "f1"), "the user removed at the source loses the group's access"
    assert _reaches_through_a_group(ws, CAROL, "f1"), "the remaining member keeps it"
    assert (KEPT, "GROUP", "READ") in ws.grants("f1")
    assert KEPT not in ws.records.kept_user_groups
    assert unlinked.user_groups & {KEPT} == set()


async def test_a_group_left_out_of_the_listing_whose_members_are_read_in_part_keeps_the_stored_members(
    ws: Workspace  # noqa: F811
) -> None:
    await _group_with_a_leaver_synced(ws)

    def members(req: ApiRequest) -> object:
        if req.query.get("pageToken"):
            return google_error(500, "backendError")
        return {"kind": "admin#directory#members", "nextPageToken": "1", "members": [
            {"id": "ext-x", "email": CAROL, "type": "USER", "role": "MEMBER"},
        ]}

    ws.http.route("GET", f"{ADMIN_API}/groups/{KEPT}/members", members)

    unlinked = await _runner_full_sync(ws)

    assert _members(ws, KEPT) == {BOB, CAROL}, "a partial read is not the membership"
    assert ws.records.kept_user_groups == [KEPT]
    assert unlinked.user_groups & {KEPT} == set()


async def test_a_group_with_members_that_cannot_be_written_is_kept_and_the_run_is_incomplete(
    ws: Workspace, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    await _group_with_a_leaver_synced(ws)
    connector = await ws.connector_()
    real = ws.records.on_new_user_groups

    async def on_new_user_groups(groups: list[tuple[Any, list[Any]]], **kwargs: bool) -> None:
        if any(g.source_user_group_id == KEPT for g, _ in groups):
            raise RuntimeError("graph unavailable")
        await real(groups, **kwargs)

    monkeypatch.setattr(ws.records, "on_new_user_groups", on_new_user_groups)

    await connector._remove_deleted_groups({NESTED})

    assert _members(ws, KEPT) == {BOB, CAROL}
    assert ws.records.kept_user_groups == [KEPT]
    assert not connector.full_sync_coverage().complete


async def test_a_group_left_out_of_the_listing_whose_members_cannot_be_read_keeps_its_links(ws: Workspace) -> None:  # noqa: F811
    await _two_groups_synced(ws)
    _groups_listed_without(ws, KEPT)
    ws.http.fail("GET", f"{ADMIN_API}/groups/{KEPT}/members", 500, "backendError")

    unlinked = await _runner_full_sync(ws)

    assert KEPT not in ws.records.deleted_groups
    assert unlinked.user_groups & {KEPT} == set(), "unconfirmed: the group is kept"
    assert unlinked.user_groups & {GONE} == {GONE}
    assert _members(ws, KEPT) == {BOB}, "a failed read is not an empty group"


def _members(ws: Workspace, group_email: str) -> set[str]:  # noqa: F811
    return {m.email for m in ws.records.user_groups[group_email][1]}


def _reaches_through_a_group(ws: Workspace, email: str, external_id: str) -> bool:  # noqa: F811
    """Whether a group granted on the item has the person among its stored members."""
    return any(
        entity == "GROUP" and group in ws.records.user_groups and email in _members(ws, group)
        for group, entity, _ in ws.grants(external_id)
    )


def _members_answer(ws: Workspace, group_email: str, answer: dict[str, Any]) -> None:  # noqa: F811
    """members.list answers this body for the group."""
    ws.http.route("GET", f"{ADMIN_API}/groups/{group_email}/members", lambda req: answer)


async def test_a_group_left_out_of_the_listing_that_has_no_members_left_loses_its_members(ws: Workspace) -> None:  # noqa: F811
    await _two_groups_synced(ws)
    assert _reaches_through_a_group(ws, BOB, "f1")
    _groups_listed_without(ws, KEPT)
    ws.world.groups[KEPT]["members"] = []

    unlinked = await _runner_full_sync(ws)

    assert KEPT in ws.records.user_groups and KEPT not in ws.records.deleted_groups
    assert _members(ws, KEPT) == set(), "members.list answers the group is empty"
    assert not _reaches_through_a_group(ws, BOB, "f1"), "the former member loses the group's access"
    assert (KEPT, "GROUP", "READ") in ws.grants("f1"), "the grant to the group itself stays"
    assert KEPT not in ws.records.kept_user_groups
    assert unlinked.user_groups & {KEPT} == set(), "written this run: not unlinked"
    assert unlinked.user_groups & {GONE} == {GONE}


async def test_an_empty_members_answer_without_its_members_key_empties_the_group(ws: Workspace) -> None:  # noqa: F811
    await _two_groups_synced(ws)
    _groups_listed_without(ws, KEPT)
    # The directory leaves an empty list out of its answer.
    _members_answer(ws, KEPT, {"kind": "admin#directory#members", "etag": "e1"})

    await _runner_full_sync(ws)

    assert _members(ws, KEPT) == set()
    assert KEPT not in ws.records.kept_user_groups


@pytest.mark.parametrize("answer", [
    {"etag": "e1"},
    {"kind": "admin#directory#members", "members": None},
    {"kind": "admin#directory#members", "members": [], "nextPageToken": "1"},
], ids=["no-kind-no-members", "members-null", "empty-page-with-more"])
async def test_a_members_answer_that_is_not_conclusively_empty_keeps_the_stored_members(
    ws: Workspace, answer: dict[str, Any]  # noqa: F811
) -> None:
    await _two_groups_synced(ws)
    _groups_listed_without(ws, KEPT)
    _members_answer(ws, KEPT, answer)

    unlinked = await _runner_full_sync(ws)

    assert _members(ws, KEPT) == {BOB}, "unknown is not empty"
    assert _reaches_through_a_group(ws, BOB, "f1")
    assert ws.records.kept_user_groups == [KEPT]
    assert unlinked.user_groups & {KEPT} == set()


async def test_an_emptied_group_that_cannot_be_written_is_kept_and_the_run_is_incomplete(
    ws: Workspace, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    await _two_groups_synced(ws)
    _groups_listed_without(ws, KEPT)
    ws.world.groups[KEPT]["members"] = []
    connector = await ws.connector_()
    real = ws.records.on_new_user_groups

    async def on_new_user_groups(groups: list[tuple[Any, list[Any]]], **kwargs: bool) -> None:
        if any(g.source_user_group_id == KEPT for g, _ in groups):
            raise RuntimeError("graph unavailable")
        await real(groups, **kwargs)

    monkeypatch.setattr(ws.records, "on_new_user_groups", on_new_user_groups)

    await connector._remove_deleted_groups(set())

    assert _members(ws, KEPT) == {BOB}
    assert ws.records.kept_user_groups == [KEPT]
    assert not connector.full_sync_coverage().complete


async def test_a_group_left_out_of_the_listing_whose_lookup_fails_keeps_its_links(ws: Workspace) -> None:  # noqa: F811
    await _two_groups_synced(ws)
    _groups_listed_without(ws, KEPT)
    ws.world.unlistable_groups[KEPT] = 404
    ws.http.fail("GET", f"{ADMIN_API}/groups/{KEPT}", 500, "backendError")

    unlinked = await _runner_full_sync(ws)

    assert KEPT not in ws.records.deleted_groups
    assert unlinked.user_groups & {KEPT} == set(), "groups.get could not confirm it gone: the group is kept"
    assert unlinked.user_groups & {GONE} == {GONE}


async def test_a_group_left_out_of_the_listing_that_groups_get_still_finds_keeps_its_links(ws: Workspace) -> None:  # noqa: F811
    await _two_groups_synced(ws)
    _groups_listed_without(ws, KEPT)
    ws.world.unlistable_groups[KEPT] = 404

    unlinked = await _runner_full_sync(ws)

    assert KEPT not in ws.records.deleted_groups
    assert unlinked.user_groups & {KEPT} == set(), "groups.get still finds it: not deleted"
    assert unlinked.user_groups & {GONE} == {GONE}


async def test_a_group_the_directory_confirms_deleted_is_not_noted_kept(ws: Workspace) -> None:  # noqa: F811
    ws.world.add_group(GONE, [BOB])
    ws.world.add_item("f1", "plan.txt", parent="root-alice", owner=ALICE, perms=[
        {"type": "group", "role": "reader", "emailAddress": GONE},
    ])
    await ws.sync()
    assert GONE in ws.records.user_groups
    del ws.world.groups[GONE]
    ws.world.files["f1"].perms = []

    unlinked = await _runner_full_sync(ws)

    assert GONE in ws.records.deleted_groups
    assert GONE not in ws.records.kept_user_groups
    assert unlinked.user_groups & {GONE} == {GONE}
    assert not {g for g in ws.grants("f1") if g[0] == GONE}


async def test_a_kept_group_leaves_the_full_sync_complete(ws: Workspace) -> None:  # noqa: F811
    await _two_groups_synced(ws)
    _groups_listed_without(ws, KEPT)
    ws.http.fail("GET", f"{ADMIN_API}/groups/{KEPT}/members", 500, "backendError")
    ws.records.kept_user_groups.clear()
    ws.sync_points.sync_points.clear()
    ws.records.begin_full_sync_run(CONNECTOR_ID)
    connector = await ws.connector_()

    await connector.run_sync()

    assert connector.full_sync_coverage().complete
    assert ws.records.kept_user_groups == [KEPT]


async def _two_drives_synced(ws: Workspace) -> None:  # noqa: F811
    ws.world.add_drive("sd-kept", "Board", {ALICE: "organizer", BOB: "reader"})
    ws.world.add_drive("sd-gone", "Old", {ALICE: "organizer"})
    await ws.sync()
    assert {"sd-kept", "sd-gone"} <= set(ws.records.record_groups)
    del ws.world.drives["sd-gone"]


async def test_a_shared_drive_left_out_of_the_listing_that_still_exists_keeps_its_links(ws: Workspace) -> None:  # noqa: F811
    await _two_drives_synced(ws)
    _drives_listed_without(ws, "sd-kept")

    unlinked = await _runner_full_sync(ws)

    assert "sd-kept" in ws.records.record_groups
    assert ws.records.kept_record_groups == ["sd-kept"], "noted once, though two members list it"
    assert unlinked.record_groups & {"sd-kept"} == set(), "its members still list it: not deleted"
    assert unlinked.record_groups & {"sd-gone"} == {"sd-gone"}


async def test_a_shared_drive_left_out_of_the_listing_whose_lookup_fails_keeps_its_links(ws: Workspace) -> None:  # noqa: F811
    await _two_drives_synced(ws)
    _drives_listed_without(ws, "sd-kept")
    ws.http.fail("GET", f"{DRIVE_API}/drives/sd-kept", 500, "backendError")

    unlinked = await _runner_full_sync(ws)

    assert "sd-kept" in ws.records.record_groups
    assert ws.records.kept_record_groups == ["sd-kept"]
    assert unlinked.record_groups & {"sd-kept"} == set(), "its members still list it: the drive is kept"
    assert unlinked.record_groups & {"sd-gone"} == {"sd-gone"}


async def _full_sync(ws: Workspace) -> tuple[Any, bool]:  # noqa: F811
    """A full sync from no sync points, as the runner starts one: the connector and whether it ended incomplete."""
    ws.records.kept_record_groups.clear()
    ws.sync_points.sync_points.clear()
    ws.records.begin_full_sync_run(CONNECTOR_ID)
    connector = await ws.connector_()
    try:
        await connector.run_sync()
    except (IncompleteSyncError, DriveSyncIncomplete):
        return connector, True
    return connector, False


async def test_a_kept_shared_drive_leaves_the_full_sync_complete(ws: Workspace) -> None:  # noqa: F811
    await _two_drives_synced(ws)
    _drives_listed_without(ws, "sd-kept")

    connector, incomplete = await _full_sync(ws)

    assert not incomplete and connector.full_sync_coverage().complete
    assert ws.records.kept_record_groups == ["sd-kept"]


async def test_a_shared_drive_the_admin_listing_has_is_not_noted_kept(ws: Workspace) -> None:  # noqa: F811
    await _two_drives_synced(ws)

    await _full_sync(ws)

    assert ws.records.kept_record_groups == []


async def test_a_shared_drive_confirmed_deleted_is_not_noted_kept(ws: Workspace) -> None:  # noqa: F811
    ws.world.add_drive("sd-gone", "Old", {ALICE: "organizer"})
    await ws.sync()
    del ws.world.drives["sd-gone"]

    unlinked = await _runner_full_sync(ws)

    assert ws.records.kept_record_groups == []
    assert unlinked.record_groups & {"sd-gone"} == {"sd-gone"}


async def test_a_kept_shared_drive_outside_the_drive_filter_is_not_noted_kept(ws: Workspace) -> None:  # noqa: F811
    await _two_drives_synced(ws)
    _drives_listed_without(ws, "sd-kept")
    ws.filters(drive_ids={"operator": "not_in", "type": "list", "value": ["sd-kept"]})

    await _full_sync(ws)

    assert ws.records.kept_record_groups == []


async def test_a_kept_shared_drive_that_cannot_be_noted_leaves_the_run_incomplete(
    ws: Workspace, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    ws.world.add_drive("sd-kept", "Board", {ALICE: "organizer"})
    await ws.sync()
    _drives_listed_without(ws, "sd-kept")
    ws.world.add_item("sd-new", "agenda.txt", parent="sd-kept")
    calls: list[str] = []

    async def note_kept_record_group(connector_id: str, external_group_id: str) -> None:
        calls.append(external_group_id)
        raise RuntimeError("graph unavailable")

    monkeypatch.setattr(ws.records, "note_kept_record_group", note_kept_record_group)

    connector, incomplete = await _full_sync(ws)

    assert incomplete and not connector.full_sync_coverage().complete
    assert calls == ["sd-kept"]
    assert "sd-new" in ws.records.records, "its only member still syncs the drive after the failed note"


async def test_a_shared_drive_left_out_of_the_listing_in_two_full_syncs_is_noted_kept_in_each(ws: Workspace) -> None:  # noqa: F811
    await _two_drives_synced(ws)
    _drives_listed_without(ws, "sd-kept")
    await _runner_full_sync(ws)

    unlinked = await _runner_full_sync(ws)

    assert ws.records.kept_record_groups == ["sd-kept"]
    assert unlinked.record_groups & {"sd-kept"} == set(), "the same connector notes it again in the next run"


async def test_the_kept_user_groups_are_noted_once_per_run(ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    await _two_groups_synced(ws)
    connector = await ws.connector_()
    notes: list[list[str]] = []

    def note_kept_user_groups(connector_id: str, groups: list[Any]) -> None:
        notes.append([g.source_user_group_id for g in groups])

    monkeypatch.setattr(ws.records, "note_kept_user_groups", note_kept_user_groups)
    ws.world.unlistable_groups[KEPT] = 404  # members.list cannot find it, groups.get still does: kept

    await connector._remove_deleted_groups(set())
    ws.world.groups.pop(KEPT)
    await connector._remove_deleted_groups(set())

    assert notes == [[KEPT], []]

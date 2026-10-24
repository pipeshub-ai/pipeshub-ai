# pyright: ignore-file

"""Confluence changes between syncs, against the connectors simulator.

Each scenario changes the simulated site the way a Confluence user would (restrictions,
groups, attachments, the trash, moves, comments) and checks what the next sync made of it in
PipesHub: the graph, the vectors, the streamed page and the notifications.

An incremental sync re-reads every page modified in the 24 hours before its checkpoint, so a
change to a new page is found whatever else the connector does. The changes that the
connector must find some other way (restrictions, attachments, moves: none of them gives the
page a new version) are therefore made on the pack space's own pages and folders, which are
months old, and put back in ``finally``: restrictions removed, attachments the test added
deleted, moved pages moved back. Only pages and folders without any restriction, on them or
above them, are picked, so removing every restriction restores them exactly. One scenario
(3b) edits a pack page; that page keeps a new version with the same body.

What age does not matter for (the trash, comments, streaming) uses a space the module creates
and deletes; a second such space is the target of the move to another space. Each scenario
uses objects no other scenario touches, so they do not depend on each other's order. Every
sync also checks the notifications it sent (none for a clean sync), when the test ends.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator

import pytest
import pytest_asyncio
import requests

from connectors.confluence import confluence_sim as sim
from connectors.confluence.constants import connector_base_url
from helper.graph_provider import GraphProviderProtocol
from helper.graph_provider_utils import wait_for_sync_completion
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

logger = logging.getLogger("confluence-sim-changes")

INDEXED_TIMEOUT_SEC = int(os.getenv("INTEGRATION_INDEXING_WAIT_SEC", "300"))
RESTRICTED, STRICT = "RESTRICTED", "STRICT"


def _rule(record: Any) -> str:
    return str(getattr(record.access_rule, "value", record.access_rule))


@dataclass
class World:
    client: PipeshubClient
    graph: GraphProviderProtocol
    connector_id: str
    name: str
    inbox: sim.Inbox
    me: str
    team_group: str
    spaces: dict[str, dict[str, Any]]
    ids: dict[str, str] = field(default_factory=dict)
    # Where each picked pack page sits, to move it back.
    pack_parents: dict[str, str] = field(default_factory=dict)
    # Notification mismatches of the running test, checked when it ends so a wrong notice does
    # not hide what the scenario's own checks find.
    notice_problems: list[str] = field(default_factory=list)

    async def record(self, key_or_id: str) -> Any:
        return await self.graph.get_record_by_external_id(self.connector_id, self.ids.get(key_or_id, key_or_id))

    async def attachment(self, att_id: str) -> Any:
        return await sim.find_record(self.graph, self.connector_id, sim.attachment_external_ids(att_id))

    async def sync(self, context: str, *, notices: int = 0, full: bool = False) -> list[dict[str, Any]]:
        """One sync, waited out; returns the notifications it sent, which must number ``notices``."""
        await sim.run_sync(self.client, self.graph, self.connector_id, full=full)
        new = await self.inbox.arrived(notices)
        problem = sim.Inbox.mismatch(new, notices, context)
        if problem:
            self.notice_problems.append(problem)
        return new


def _seed(space_a: dict[str, Any], tag: str) -> dict[str, str]:
    """The new content the scenarios change, made before the first sync. Titles are unique per run."""
    a = str(space_a["id"])
    ids: dict[str, str] = {}

    def page(key: str, text: str = "", parent: str | None = None) -> str:
        ids[key] = sim.create_page(a, f"PipesHub IT sim {key} {tag}", text or f"{key} page of run {tag}", parent)
        return ids[key]

    ids["trash_folder"] = sim.create_folder(a, f"PipesHub IT sim trash_folder {tag}")
    page("in_trash_folder", parent=ids["trash_folder"])
    ids["att_trash"] = sim.upload(page("trash_page"), f"trash-{tag}.txt", b"goes to the trash with its page",
                                  "text/plain")
    page("footer_page")
    page("inline_page", text=f"Quarterly figures of run {tag} are settled here in plain words.")

    # A page whose comments are in place before its first sync: two levels of footer replies and
    # an inline thread, for streaming.
    page("stream_page", text=f"Streaming body of run {tag} with an anchor phrase for the inline thread.")
    root = sim.footer_comment(f"stream footer root {tag}", page_id=ids["stream_page"])
    reply = sim.footer_comment(f"stream footer reply {tag}", parent_id=root["id"])
    deeper = sim.footer_comment(f"stream footer reply to reply {tag}", parent_id=reply["id"])
    inline = sim.inline_comment(f"stream inline root {tag}", page_id=ids["stream_page"], selection="anchor phrase")
    inline_reply = sim.inline_comment(f"stream inline reply {tag}", parent_id=inline["id"])
    ids.update(c_root=str(root["id"]), c_reply=str(reply["id"]), c_deeper=str(deeper["id"]),
               c_inline=str(inline["id"]), c_inline_reply=str(inline_reply["id"]))
    return ids


# Pages other suites name: the homepage and the first page under it (conftest's hierarchy page).
_PACK_PAGES_IN_USE = 2


def _has_restriction(content_id: str) -> bool:
    return any(r["restrictions"]["user"]["size"] or r["restrictions"]["group"]["size"]
               for r in sim.ok("GET", f"/wiki/rest/api/content/{content_id}/restriction")["results"])


def _pick_pack(space_id: str, space_key: str) -> tuple[dict[str, str], dict[str, str]]:
    """Old pack pages and folders without any restriction, on them or above them, one per role;
    and where each picked page sits, to move it back to."""
    pages: list[dict[str, Any]] = []
    path = f"/wiki/api/v2/spaces/{space_id}/pages?limit=250"
    while path:
        body = sim.ok("GET", path)
        pages += body["results"]
        path = (body.get("_links") or {}).get("next")
    pages.sort(key=lambda p: int(p["id"]))
    parent = {str(p["id"]): str(p.get("parentId") or "") for p in pages}
    folders = sorted(str(f["id"]) for f in sim.ok(
        "GET", f"/wiki/rest/api/content/search?cql=space%3D{space_key}%20and%20type%3Dfolder&limit=50")["results"])
    restricted = {i for i in [*parent, *folders] if _has_restriction(i)}

    def free(i: str) -> bool:
        for _ in range(50):
            if not i:
                return True
            if i in restricted:
                return False
            i = parent.get(i, "")
        return False

    kids: dict[str, list[str]] = {}
    for i, p in parent.items():
        kids.setdefault(p, []).append(i)
    candidates = [str(p["id"]) for p in pages[_PACK_PAGES_IN_USE:] if free(str(p["id"]))]
    used: set[str] = set()

    def take(*, with_kids: bool = False, under_page: bool = False) -> str:
        for i in candidates:
            family = {i, *kids.get(i, []), parent[i]}
            if i in used or used & family or bool(kids.get(i)) != with_kids or (
                    under_page and parent[i] not in parent):
                continue
            used.update(family)
            return i
        raise AssertionError("the pack space has too few unrestricted pages for these scenarios")

    ids: dict[str, str] = {}
    ids["restrict_page"] = take(with_kids=True)
    ids["restrict_child"] = kids[ids["restrict_page"]][0]
    ids["space_move_page"] = take(with_kids=True, under_page=True)
    ids["space_move_child"] = kids[ids["space_move_page"]][0]
    for key in ("group_page", "audit_page", "unreadable_page", "add_att_page", "version_att_page",
                "att_limit_page", "att_edit_page", "move_target"):
        ids[key] = take()
    ids["moved_page"] = take(under_page=True)
    free_folders = [f for f in folders if f not in restricted and kids.get(f)]
    assert len(free_folders) >= 2, f"the pack space needs two unrestricted folders with pages, has {free_folders}"
    ids["restrict_folder"], ids["audit_folder"] = free_folders[:2]
    ids["in_restrict_folder"] = kids[ids["restrict_folder"]][0]
    return ids, {k: parent[v] for k, v in ids.items() if v in parent}


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def world(pipeshub_client: PipeshubClient, graph_provider: GraphProviderProtocol) -> AsyncGenerator[World, None]:
    sim.require_simulator()
    tag = uuid.uuid4().hex[:6].upper()
    keys = {"a": f"ITA{tag}", "b": f"ITB{tag}"}
    pack_key = os.environ["CONFLUENCE_TEST_SPACE_KEY"].strip()
    spaces: dict[str, dict[str, Any]] = {}
    connector_id = None
    try:
        spaces["pack"] = sim.ok("GET", f"/wiki/api/v2/spaces?keys={pack_key}")["results"][0]
        for which, key in keys.items():
            spaces[which] = sim.create_space(key, f"PipesHub IT sim {which} {tag}")
        ids = _seed(spaces["a"], tag)
        pack_ids, pack_parents = _pick_pack(str(spaces["pack"]["id"]), pack_key)
        ids.update(pack_ids)
        me = sim.current_account_id()
        team_group = next(g["id"] for g in sim.ok("GET", "/wiki/rest/api/group?limit=200")["results"]
                          if g["name"].startswith("team-"))

        name = f"confluence-sim-{tag.lower()}"
        instance = pipeshub_client.create_connector(
            connector_type="Confluence", instance_name=name, scope="team", auth_type="API_TOKEN",
            config={
                "auth": {"authType": "API_TOKEN", "baseUrl": connector_base_url(),
                         "email": os.getenv("CONFLUENCE_TEST_EMAIL"),
                         "apiToken": os.getenv("CONFLUENCE_TEST_API_TOKEN")},
                "filters": {"sync": {"values": {"space_keys": {
                    "operator": "in", "type": "list", "value": [pack_key, *keys.values()]}}}},
            },
        )
        connector_id = instance.connector_id
        assert connector_id, "Connector must have a valid ID"
        inbox = sim.Inbox(pipeshub_client.base_url, name, email=os.getenv("PIPESHUB_TEST_USER_EMAIL"),
                          password=os.getenv("PIPESHUB_TEST_USER_PASSWORD"))
        w = World(pipeshub_client, graph_provider, connector_id, name, inbox, me, team_group, spaces, ids,
                  pack_parents)

        pipeshub_client.toggle_sync(connector_id, enable=True)
        await wait_for_sync_completion(pipeshub_client, graph_provider, connector_id, timeout=sim.SYNC_TIMEOUT_SEC)

        async def all_synced() -> bool:
            for key, external_id in ids.items():
                if key.startswith("c_"):
                    continue
                found = await (w.attachment(external_id) if key.startswith("att_") else w.record(external_id))
                if found is None:
                    return False
            return True
        await sim.poll(all_synced, "every seeded page, folder and attachment synced")
        await inbox.expect(0, "first full sync of the module's spaces")
        yield w
    finally:
        if connector_id:
            try:
                pipeshub_client.toggle_sync(connector_id, enable=False)
                pipeshub_client.delete_connector(connector_id)
                pipeshub_client.wait(25)
                await graph_provider.assert_all_records_cleaned(
                    connector_id, timeout=int(os.getenv("INTEGRATION_GRAPH_CLEANUP_TIMEOUT", "300")))
            except Exception as e:  # noqa: BLE001 - teardown must not mask the result
                logger.warning("TEARDOWN: delete/clean failed for %s: %s", connector_id, e)
        for key in keys.values():
            try:
                sim.delete_space(key)
            except Exception as e:  # noqa: BLE001 - try both spaces
                logger.warning("TEARDOWN: could not delete space %s: %s", key, e)
        sim.ok("DELETE", "/_sim/faults")


pytestmark = [pytest.mark.integration, pytest.mark.confluence, pytest.mark.asyncio(loop_scope="session")]


@pytest.fixture(autouse=True)
def _each_sync_sent_the_expected_notices(world: World):
    """9: a clean sync notifies nobody, and a sync with a problem sends exactly one notice."""
    world.notice_problems.clear()
    yield
    assert not world.notice_problems, "\n".join(world.notice_problems)


# ---- 1, 13: restrictions ---------------------------------------------------------------------------------

async def _assert_restricted(w: World, key: str, restricted: bool, context: str) -> None:
    rec = await w.record(key)
    assert rec is not None, f"{context}: {key} is gone"
    has_grant = await w.graph.group_has_record_permission(w.connector_id, w.team_group, w.ids[key])
    if restricted:
        assert _rule(rec) == RESTRICTED and has_grant, (
            f"{context}: {key} should be read-restricted to the team group; rule={_rule(rec)}, group grant={has_grant}")
    else:
        assert _rule(rec) != RESTRICTED and not has_grant, (
            f"{context}: {key} should be open again; rule={_rule(rec)}, group grant={has_grant}")


@pytest.mark.parametrize("key", ["restrict_page", "restrict_folder"])
async def test_read_restriction_added_then_removed(world: World, key: str) -> None:
    """1a/1b (page) and 1c/1d (folder): a read restriction reaches the record, and its removal opens it again."""
    w, content_id = world, world.ids[key]
    try:
        sim.restrict(content_id, users=[w.me], groups=[w.team_group])
        await w.sync(f"{key} restricted")
        await _assert_restricted(w, key, True, f"{key} after the restriction")
        child = "restrict_child" if key == "restrict_page" else "in_restrict_folder"
        child_rec = await w.record(child)
        assert child_rec is not None and child_rec.parent_external_record_id == content_id, (
            f"{child} should still sit under {key}")

        sim.unrestrict(content_id)
        await w.sync(f"{key} unrestricted")
        await _assert_restricted(w, key, False, f"{key} after the restriction was removed")
    finally:
        sim.unrestrict(content_id)


async def test_restrictions_applied_while_the_audit_log_is_refused(world: World) -> None:
    """13a/13b: without the audit log, restriction changes on a folder and a page still reach the records.

    The refused audit log is reported once, not on every sync while it lasts.
    """
    w = world
    audit_refused = {"match": {"path_regex": "^/wiki/rest/api/audit"}, "effect": {"type": "status", "code": 403}}
    try:
        with sim.faults(audit_refused):
            for key in ("audit_folder", "audit_page"):
                sim.restrict(w.ids[key], users=[w.me], groups=[w.team_group])
            notices = await w.sync("restrictions with the audit log refused", notices=1)
            assert any("audit log (HTTP 403)" in n["message"] for n in notices), "the refused audit log was not reported"
            assert any(f["effect"] == "status" for f in sim.faults_fired()), "the audit log was never asked for"
            for key in ("audit_folder", "audit_page"):
                await _assert_restricted(w, key, True, f"{key} with the audit log refused")

            for key in ("audit_folder", "audit_page"):
                sim.unrestrict(w.ids[key])
            await w.sync("restrictions removed, audit log still refused", notices=0)
            for key in ("audit_folder", "audit_page"):
                await _assert_restricted(w, key, False, f"{key} unrestricted with the audit log refused")
        await w.sync("audit log readable again")
    finally:
        for key in ("audit_folder", "audit_page"):
            sim.unrestrict(w.ids[key])


async def test_unreadable_restriction_keeps_the_page_and_is_read_next_sync(world: World) -> None:
    """Edge: a restriction that cannot be read is reported, and the next sync applies it."""
    w, page_id = world, world.ids["unreadable_page"]
    unreadable = {"match": {"method": "GET", "path_regex": f"^/wiki/rest/api/content/{page_id}/restriction"},
                  "effect": {"type": "status", "code": 500}}
    try:
        sim.restrict(page_id, users=[w.me], groups=[w.team_group])
        with sim.faults(unreadable):
            notices = await w.sync("a page whose restriction cannot be read", notices=1)
        assert await w.record("unreadable_page") is not None, "the page was dropped instead of kept"
        assert any("restriction" in n["message"].lower() for n in notices), "the unreadable restriction was not reported"
        await w.sync("the restriction is readable again")
        await _assert_restricted(w, "unreadable_page", True, "after the restriction became readable")
    finally:
        sim.unrestrict(page_id)


# ---- 2: groups -------------------------------------------------------------------------------------------

async def test_group_restriction_synced_then_group_deleted(world: World) -> None:
    """2a: a page restricted to a new group grants that group; 2b: deleting the group takes the grant away."""
    w, page_id = world, world.ids["group_page"]
    member = sim.other_active_account({w.me})
    group_id = sim.create_group(f"it-sim-{uuid.uuid4().hex[:8]}", [member["account_id"]])
    try:
        sim.restrict(page_id, users=[w.me], groups=[group_id])
        await w.sync("page restricted to a new group")
        assert await w.graph.get_user_group_by_external_id(w.connector_id, group_id) is not None, "group not synced"
        assert await w.graph.group_has_record_permission(w.connector_id, group_id, page_id), "group has no grant"

        sim.delete_group(group_id)
        await w.sync("group deleted")
        assert await w.graph.get_user_group_by_external_id(w.connector_id, group_id) is None, (
            "the deleted group is still stored, so its former members keep its grants")
        assert not await w.graph.group_has_record_permission(w.connector_id, group_id, page_id)
        assert await w.record("group_page") is not None
    finally:
        sim.unrestrict(page_id)
        sim.delete_group(group_id)


# ---- 3, 10: attachments of pack pages ----------------------------------------------------------------------

async def _add_attachment(w: World, key: str, label: str) -> str:
    """A file of this run on a pack page, synced; the page gets no new version from it."""
    page_id = w.ids[key]
    version = sim.page_version(page_id)
    att = sim.upload(page_id, f"it-{label}-{uuid.uuid4().hex[:6]}.txt", f"{label} version one".encode(), "text/plain")
    assert sim.page_version(page_id) == version, "the simulator gave the page a new version; the scenario is void"
    await w.sync(f"{label}: attachment added")
    return att


async def test_attachment_added_to_unchanged_page(world: World) -> None:
    """10a: an upload adds no page version; the attachment search still brings it in."""
    w, page_id = world, world.ids["add_att_page"]
    att = None
    try:
        att = await _add_attachment(w, "add_att_page", "added")
        rec = await w.attachment(att)
        assert rec is not None, "the new attachment of an unchanged page was not synced"
        assert rec.parent_external_record_id == page_id, f"attachment parent {rec.parent_external_record_id!r}"
    finally:
        if att:
            sim.delete_attachment(att)


async def test_attachment_new_version_on_unchanged_page(world: World) -> None:
    """10b: a new version of a stored attachment is synced though its page did not change."""
    w, page_id = world, world.ids["version_att_page"]
    att = None
    try:
        att = await _add_attachment(w, "version_att_page", "versioned")
        before = await w.attachment(att)
        assert before is not None, "the attachment was not synced in the first place"
        version = sim.page_version(page_id)
        name = next(a["title"] for a in sim.attachments(page_id) if a["id"] == att)
        sim.upload(page_id, name, b"versioned version two, a little longer", "text/plain", new_version=True)
        assert sim.page_version(page_id) == version
        await w.sync("attachment re-uploaded on an unchanged page")
        after = await w.attachment(att)
        assert after is not None
        assert (after.external_revision_id, after.version) != (before.external_revision_id, before.version), (
            f"attachment unchanged in the graph: revision {after.external_revision_id!r}, version {after.version}")
    finally:
        if att:
            sim.delete_attachment(att)


async def test_attachment_deleted_without_page_edit_stays_until_full_sync(world: World) -> None:
    """3: known limit. Confluence's search does not list deleted attachments, so one deleted from an
    unchanged page stays until its page changes or a full sync runs; the full sync removes it."""
    w = world
    att = None
    try:
        att = await _add_attachment(w, "att_limit_page", "limit")
        assert await w.attachment(att) is not None, "the attachment was not synced in the first place"
        sim.delete_attachment(att)
        await w.sync("attachment deleted, page unchanged")
        assert await w.attachment(att) is not None, (
            "the deleted attachment is gone after an incremental sync: the documented limit no longer holds, "
            "update this test")
        await w.sync("full sync after the attachment was deleted", full=True)
        assert await w.attachment(att) is None, "a full sync should remove the deleted attachment"
        assert await w.record("att_limit_page") is not None
    finally:
        if att:
            sim.delete_attachment(att)


async def test_attachment_removed_when_its_page_is_edited(world: World) -> None:
    """3b: the deleted attachment goes once its page has a new version (the page keeps that version)."""
    w = world
    att = None
    try:
        att = await _add_attachment(w, "att_edit_page", "edited")
        assert await w.attachment(att) is not None, "the attachment was not synced in the first place"
        sim.delete_attachment(att)
        sim.edit_page(w.ids["att_edit_page"])
        await w.sync("attachment deleted, page edited")
        assert await w.attachment(att) is None, "the deleted attachment is still stored after its page changed"
        assert await w.record("att_edit_page") is not None
    finally:
        if att:
            sim.delete_attachment(att)


# ---- 4, 5, 11: trash, restore, moves ---------------------------------------------------------------------

async def test_folder_trashed(world: World) -> None:
    """4: a trashed folder's record goes; its page stays, under the folder's old parent."""
    w = world
    sim.ok("DELETE", f"/wiki/api/v2/folders/{w.ids['trash_folder']}")
    await w.sync("folder trashed")
    assert await w.record("trash_folder") is None, "the trashed folder is still stored"
    child = await w.record("in_trash_folder")
    assert child is not None, "the trashed folder's page was removed with it"
    expected_parent = str(sim.page(w.ids["in_trash_folder"]).get("parentId") or "")
    assert (child.parent_external_record_id or "") == expected_parent, (
        f"page parent {child.parent_external_record_id!r}, Confluence says {expected_parent!r}")


async def test_page_trashed_then_restored(world: World) -> None:
    """5a: a trashed page and its attachment go; 5b: restoring the page brings both back."""
    w, page_id, att = world, world.ids["trash_page"], world.ids["att_trash"]
    sim.trash_page(page_id)
    await w.sync("page trashed")
    assert await w.record("trash_page") is None, "the trashed page is still stored"
    assert await w.attachment(att) is None, "the trashed page's attachment is still stored"

    sim.restore_page(page_id)
    await w.sync("page restored")
    assert await w.record("trash_page") is not None, "the restored page did not come back"
    assert await w.attachment(att) is not None, "the restored page's attachment did not come back"


async def test_page_moved_to_another_space(world: World) -> None:
    """5c: a pack page moved to another synced space follows, with its child, under the new space's homepage."""
    w, page_id = world, world.ids["space_move_page"]
    space_b = w.spaces["b"]
    try:
        sim.move_page(page_id, str(space_b["homepageId"]))
        await w.sync("page moved to the other space")
        rec, child = await w.record("space_move_page"), await w.record("space_move_child")
        assert rec is not None and child is not None
        assert rec.external_record_group_id == str(space_b["id"]), f"page still in space {rec.external_record_group_id}"
        assert rec.parent_external_record_id == str(space_b["homepageId"]), f"parent {rec.parent_external_record_id!r}"
        assert child.external_record_group_id == str(space_b["id"]), "the child page stayed in the old space"
    finally:
        sim.move_page(page_id, w.pack_parents["space_move_page"])


async def test_page_moved_without_version_change(world: World) -> None:
    """11: the v1 move adds no page version; the record still gets its new parent."""
    w, page_id, target = world, world.ids["moved_page"], world.ids["move_target"]
    try:
        version = sim.page_version(page_id)
        sim.move_page(page_id, target)
        assert sim.page_version(page_id) == version, "the simulator gave the page a new version; the scenario is void"
        await w.sync("page moved under another page")
        rec = await w.record("moved_page")
        assert rec is not None and rec.parent_external_record_id == target, (
            f"parent {getattr(rec, 'parent_external_record_id', None)!r}, expected {target}")
    finally:
        sim.move_page(page_id, w.pack_parents["moved_page"])


# ---- 6, 7, 8b: comments ----------------------------------------------------------------------------------

async def _vector_texts(w: World, key: str, vector_store: Any) -> str:
    rec = await w.record(key)
    if rec is None or not rec.virtual_record_id:
        return ""
    return "\n".join(await vector_store.content_texts(rec.virtual_record_id))


async def _wait_for_text(w: World, key: str, vector_store: Any, present: str, absent: str | None = None) -> None:
    async def check() -> bool:
        text = await _vector_texts(w, key, vector_store)
        return present in text and (absent is None or absent not in text)
    await sim.poll(check, f"{key} indexed with {present!r}" + (f" and without {absent!r}" if absent else ""),
                   timeout=INDEXED_TIMEOUT_SEC, interval=5)


async def test_comments_added_and_edited_reindex_their_page(world: World, vector_store: Any) -> None:
    """6a: a new footer or inline comment re-indexes its page; 6b: an edited comment does too.

    A comment changes neither the page's version nor its last-modified time.
    """
    w = world
    tag = uuid.uuid4().hex[:8]
    footer_text, inline_text, edited_text = f"footerroot{tag}", f"inlineroot{tag}", f"footeredited{tag}"
    versions = {k: sim.page_version(w.ids[k]) for k in ("footer_page", "inline_page")}
    footer = sim.footer_comment(footer_text, page_id=w.ids["footer_page"])
    sim.inline_comment(inline_text, page_id=w.ids["inline_page"], selection="figures")
    assert versions == {k: sim.page_version(w.ids[k]) for k in versions}
    await w.sync("comments added")
    await _wait_for_text(w, "footer_page", vector_store, footer_text)
    await _wait_for_text(w, "inline_page", vector_store, inline_text)

    sim.edit_footer_comment(footer, edited_text)
    await w.sync("comment edited")
    await _wait_for_text(w, "footer_page", vector_store, edited_text, absent=footer_text)


def _stream_blocks(w: World, record_id: str) -> tuple[bytes, dict[str, Any]]:
    resp = w.client.stream_record(record_id)
    raw = b"".join(resp.iter_content(chunk_size=65536))
    assert raw.lstrip().startswith(b"{"), f"page did not stream as a blocks document: {raw[:120]!r}"
    return raw, json.loads(raw)


async def test_streamed_page_carries_its_comment_threads(world: World) -> None:
    """7: footer replies (replies to replies too) sit in their root's thread; the inline thread is on its block."""
    w = world
    rec = await w.record("stream_page")
    raw, doc = _stream_blocks(w, rec.id)
    groups, blocks = doc.get("block_groups") or [], doc.get("blocks") or []
    by_index = {g["index"]: g for g in groups}
    comment_groups = {str(g.get("source_group_id")): g for g in groups
                      if str(g.get("sub_type") or "").lower() == "comment"}
    root = w.ids["c_root"]
    for key in ("c_root", "c_reply", "c_deeper"):
        group = comment_groups.get(w.ids[key])
        assert group is not None, f"footer comment {key} missing from the streamed page"
        thread = by_index.get(group.get("parent_index"), {})
        assert str(thread.get("source_group_id")) == f"thread_footer_{root}", (
            f"{key} sits in thread {thread.get('source_group_id')!r}, not in its root's thread")
        assert any(b.get("parent_index") == group["index"] for b in blocks), f"comment group {key} has no text"
    inline_threads = [t for b in blocks for t in (b.get("comments") or [])]
    assert len(inline_threads) == 1 and len(inline_threads[0]) == 2, (
        f"expected one inline thread of two comments on the page's blocks, got {[len(t) for t in inline_threads]}")
    for text in ("stream footer reply to reply", "stream inline reply"):
        assert text.encode() in raw, f"{text!r} missing from the streamed page"


@pytest.mark.parametrize("kind", ["footer", "inline"])
async def test_streaming_fails_when_comments_cannot_be_read(world: World, kind: str) -> None:
    """8b: a page whose comments cannot be read fails to stream, rather than streaming without them."""
    w = world
    page_id = w.ids["stream_page"]
    rec = await w.record("stream_page")
    broken = {"match": {"path_regex": f"/pages/{page_id}/{kind}-comments$"}, "effect": {"type": "status", "code": 503}}
    with sim.faults(broken):
        with pytest.raises(requests.HTTPError):
            w.client.stream_record(rec.id).raise_for_status()
        assert sim.faults_fired(), f"the {kind} comments were never read"
    _stream_blocks(w, rec.id)  # and streams again once they can be read

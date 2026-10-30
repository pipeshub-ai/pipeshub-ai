# pyright: ignore-file

"""Dropbox team and personal connector ITs against the Dropbox simulator (design: DESIGN.md here).

Team, one connector for the class, in order:
  DBX-T-01  first sync: home, shared-folder, team-folder and nested files have records; owners and members open
            them, others do not (team folders need no extra Dropbox permission)
  DBX-T-02  incremental: a new file, an edit, a rename, a move into a new folder, a delete
  DBX-T-04  a suspended member loses everything
  DBX-T-05  a listing Dropbox fails: the run is not a success and nothing is removed; the next run catches up
  DBX-T-06  Dropbox resets the cursors: everything is listed again and a file deleted meanwhile goes
  DBX-T-07  a file streams Dropbox's bytes
  DBX-T-08  the refresh token is revoked: the run fails and nothing is removed
Personal:
  DBX-P-01  the connector's creator opens the files of the connected account, even when that account carries a
            colleague's email; the colleague and anyone else cannot
  DBX-P-02  incremental: a new file, a delete, a file replaced at the same path
  DBX-P-03  the account's file and a file from a folder shared with it stream Dropbox's bytes
  DBX-P-04  under "extensions in [txt]", a file renamed to .md leaves on the next incremental sync (M7)
Connector deletion removes its data: each fixture's teardown.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from connectors.dropbox_sim.dropbox_it import access as _access
from connectors.dropbox_sim.dropbox_it import apply_filters
from connectors.dropbox_sim.dropbox_it import outcome as _outcome
from connectors.dropbox_sim.dropbox_it import record as _record
from connectors.dropbox_sim.dropbox_sim import extension_filter, fault
from connectors.google_drive_workspace.drive_workspace_test_utils import sync_and_wait  # type: ignore[import-not-found]
from helper.graph_provider import GraphProviderProtocol  # type: ignore[import-not-found]
from helper.graph_provider_utils import wait_for_sync_completion  # type: ignore[import-not-found]
from helper.record_access import wait_for_record_access  # type: ignore[import-not-found]
from helper.second_user import (  # type: ignore[import-not-found]
    create_second_user,
    delete_second_user,
    log_in_existing_user,
    log_out_existing_user,
)
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

pytestmark = [
    pytest.mark.integration,
    pytest.mark.dropbox_sim,
    pytest.mark.asyncio(loop_scope="session"),
]


class TestDropboxTeam:
    @pytest.mark.order(0)
    async def test_dbx_t_01_first_sync_and_access(self, dropbox_team: dict[str, Any], graph_provider: GraphProviderProtocol,
                                                  pipeshub_client: PipeshubClient) -> None:
        sim, cid = dropbox_team["sim"], dropbox_team["connector_id"]
        records = {key: await _record(graph_provider, cid, sim.item_id(key), key)
                   for key in ("alice-notes", "alice-q3", "bob-todo", "p-spec", "d-logo", "s-plan")}
        _access(pipeshub_client, dropbox_team, "alice", records["alice-q3"].id, True, "her own file")
        _access(pipeshub_client, dropbox_team, "bob", records["alice-q3"].id, False, "alice's private file")
        _access(pipeshub_client, dropbox_team, "bob", records["alice-notes"].id, True, "a file alice shared with him")
        _access(pipeshub_client, dropbox_team, "carol", records["p-spec"].id, True, "a team folder file (Engineering)")
        _access(pipeshub_client, dropbox_team, "dan", records["p-spec"].id, False, "a team folder he is not in")
        _access(pipeshub_client, dropbox_team, "dan", records["s-plan"].id, True, "the nested folder he belongs to")
        _access(pipeshub_client, dropbox_team, "carol", records["s-plan"].id, False, "a nested folder that does not inherit")
        assert _outcome(pipeshub_client, cid) in ("", "succeeded")
        assert not sim.problems(), f"requests the simulator does not model or answers off the spec: {sim.problems()}"

    @pytest.mark.order(1)
    async def test_dbx_t_02_incremental(self, dropbox_team: dict[str, Any], graph_provider: GraphProviderProtocol,
                                        pipeshub_client: PipeshubClient) -> None:
        sim, cid = dropbox_team["sim"], dropbox_team["connector_id"]
        sim.ops({"op": "add_file", "in": "home:bob", "key": "bob-new", "name": "new.txt"},
                {"op": "edit", "item": "alice-q3"},
                {"op": "rename", "item": "p-spec", "name": "spec-final.md"},
                {"op": "add_folder", "in": "home:bob", "key": "bob-archive", "name": "Archive"},
                {"op": "move", "item": "bob-todo", "to": "bob-archive"},
                {"op": "delete", "item": "d-logo"})
        await sync_and_wait(pipeshub_client, graph_provider, cid)
        await _record(graph_provider, cid, sim.item_id("bob-new"), "the new file")
        await _record(graph_provider, cid, sim.item_id("d-logo"), "the deleted file", present=False)
        spec = await _record(graph_provider, cid, sim.item_id("p-spec"), "the renamed file")
        assert spec.record_name == "spec-final.md"
        todo = await _record(graph_provider, cid, sim.item_id("bob-todo"), "the moved file")
        assert todo.parent_external_record_id == sim.item_id("bob-archive"), "the moved file hangs under its new folder"
        _access(pipeshub_client, dropbox_team, "bob", todo.id, True, "his file moved into a new folder")

    @pytest.mark.order(2)
    async def test_dbx_t_04_suspended_member(self, dropbox_team: dict[str, Any], graph_provider: GraphProviderProtocol,
                                             pipeshub_client: PipeshubClient) -> None:
        sim, cid = dropbox_team["sim"], dropbox_team["connector_id"]
        spec = await graph_provider.get_record_by_external_id(cid, sim.item_id("p-spec"))
        sim.ops({"op": "set_member_status", "member": "carol", "status": "suspended"})
        await sync_and_wait(pipeshub_client, graph_provider, cid)
        _access(pipeshub_client, dropbox_team, "carol", spec.id, False, "a team folder file while suspended")
        sim.ops({"op": "set_member_status", "member": "carol", "status": "active"})
        await sync_and_wait(pipeshub_client, graph_provider, cid)
        _access(pipeshub_client, dropbox_team, "carol", spec.id, True, "a team folder file once active again")

    @pytest.mark.order(3)
    async def test_dbx_t_05_failed_listing(self, dropbox_team: dict[str, Any], graph_provider: GraphProviderProtocol,
                                           pipeshub_client: PipeshubClient) -> None:
        sim, cid = dropbox_team["sim"], dropbox_team["connector_id"]
        before = await graph_provider.count_records(cid)
        sim.ops({"op": "add_file", "in": "home:alice", "key": "alice-late", "name": "late.txt"})
        sim.faults(fault("files/list_folder", member="alice"))
        pipeshub_client.resync_connector(cid, full_sync=True)
        await wait_for_sync_completion(pipeshub_client, graph_provider, cid, timeout=300)
        fired = sim.fault_log()
        sim.clear_faults()
        assert fired, "the injected fault fired"
        assert _outcome(pipeshub_client, cid) != "succeeded", "a sync that could not read alice's Dropbox is not a success"
        await graph_provider.assert_record_count_unchanged(cid, before)
        await sync_and_wait(pipeshub_client, graph_provider, cid)
        await _record(graph_provider, cid, sim.item_id("alice-late"), "the file added during the outage")

    @pytest.mark.order(4)
    async def test_dbx_t_06_cursor_reset(self, dropbox_team: dict[str, Any], graph_provider: GraphProviderProtocol,
                                         pipeshub_client: PipeshubClient) -> None:
        sim, cid = dropbox_team["sim"], dropbox_team["connector_id"]
        sim.ops({"op": "delete", "item": "alice-notes"}, {"op": "add_file", "in": "home:alice", "key": "alice-after",
                                                          "name": "after-reset.txt"})
        sim.expire_cursors("list_folder")
        await sync_and_wait(pipeshub_client, graph_provider, cid)
        await _record(graph_provider, cid, sim.item_id("alice-after"), "a file added while the cursor was dead")
        await _record(graph_provider, cid, sim.item_id("alice-notes"), "a file deleted while the cursor was dead",
                      present=False)
        assert _outcome(pipeshub_client, cid) in ("", "succeeded")

    @pytest.mark.order(5)
    async def test_dbx_t_07_streaming(self, dropbox_team: dict[str, Any], graph_provider: GraphProviderProtocol,
                                      pipeshub_client: PipeshubClient) -> None:
        sim, cid = dropbox_team["sim"], dropbox_team["connector_id"]
        record = await _record(graph_provider, cid, sim.item_id("alice-q3"), "q3.md")
        resp = pipeshub_client.stream_record(record.id)
        assert resp.status_code == 200, resp.text[:300]
        assert resp.content == sim.content("alice-q3"), "the stream is Dropbox's bytes"

    @pytest.mark.order(6)
    async def test_dbx_t_08_revoked_refresh_token(self, dropbox_team: dict[str, Any],
                                                  graph_provider: GraphProviderProtocol,
                                                  pipeshub_client: PipeshubClient) -> None:
        sim, cid = dropbox_team["sim"], dropbox_team["connector_id"]
        before = await graph_provider.count_records(cid)
        sim.revoke_refresh_token()
        pipeshub_client.resync_connector(cid, full_sync=False)
        await wait_for_sync_completion(pipeshub_client, graph_provider, cid, timeout=300)
        await graph_provider.assert_record_count_unchanged(cid, before)
        assert _outcome(pipeshub_client, cid) != "succeeded", "a sync Dropbox refused does not report success"


class TestDropboxPersonal:
    @pytest.mark.order(7)
    async def test_dbx_p_01_only_the_creator_opens_it(self, dropbox_personal: dict[str, Any],
                                                      graph_provider: GraphProviderProtocol,
                                                      pipeshub_client: PipeshubClient) -> None:
        sim, cid = dropbox_personal["sim"], dropbox_personal["connector_id"]
        one = await _record(graph_provider, cid, sim.item_id("me-one"), "one.txt")
        await _record(graph_provider, cid, sim.item_id("other-shared"), "a file from a folder shared with the account")
        await _record(graph_provider, cid, sim.item_id("other-private"), "someone else's private file", present=False)
        admin = SimpleNamespace(base_url=pipeshub_client.base_url, timeout=30, headers=pipeshub_client._headers())
        wait_for_record_access(admin, one.id, expect_access=True, description="the creator opens the account's file")
        colleague = log_in_existing_user(pipeshub_client, dropbox_personal["colleague"]["id"],
                                         dropbox_personal["colleague"]["email"])
        other = create_second_user(pipeshub_client)
        try:
            wait_for_record_access(colleague, one.id, expect_access=False,
                                   description="the colleague whose email the account carries")
            wait_for_record_access(other, one.id, expect_access=False, description="anyone else")
        finally:
            log_out_existing_user(pipeshub_client, colleague)
            delete_second_user(pipeshub_client, other)
        assert not sim.problems(), f"requests the simulator does not model or answers off the spec: {sim.problems()}"

    @pytest.mark.order(8)
    async def test_dbx_p_02_incremental(self, dropbox_personal: dict[str, Any], graph_provider: GraphProviderProtocol,
                                        pipeshub_client: PipeshubClient) -> None:
        sim, cid = dropbox_personal["sim"], dropbox_personal["connector_id"]
        sim.ops({"op": "add_file", "in": "me-dir", "key": "me-three", "name": "three.txt"},
                {"op": "delete", "item": "me-one"},
                {"op": "replace", "item": "me-two", "new_key": "me-two-new"})
        await sync_and_wait(pipeshub_client, graph_provider, cid)
        await _record(graph_provider, cid, sim.item_id("me-three"), "the new file")
        await _record(graph_provider, cid, sim.item_id("me-one"), "the deleted file", present=False)
        await _record(graph_provider, cid, sim.item_id("me-two-new"), "the file made in place of another")
        await _record(graph_provider, cid, sim.item_id("me-two"), "the replaced file", present=False)

    @pytest.mark.order(9)
    async def test_dbx_p_03_streaming(self, dropbox_personal: dict[str, Any], graph_provider: GraphProviderProtocol,
                                      pipeshub_client: PipeshubClient) -> None:
        sim, cid = dropbox_personal["sim"], dropbox_personal["connector_id"]
        for key in ("me-three", "other-shared"):
            rec = await _record(graph_provider, cid, sim.item_id(key), key)
            resp = pipeshub_client.stream_record(rec.id)
            assert resp.status_code == 200, f"{key}: {resp.text[:300]}"
            assert resp.content == sim.content(key), f"{key} streams Dropbox's bytes"

    @pytest.mark.order(10)
    async def test_dbx_p_04_renamed_out_of_the_filter(self, dropbox_personal: dict[str, Any],
                                                      graph_provider: GraphProviderProtocol,
                                                      pipeshub_client: PipeshubClient) -> None:
        sim, cid = dropbox_personal["sim"], dropbox_personal["connector_id"]
        await apply_filters(pipeshub_client, graph_provider, dropbox_personal, sync_values=extension_filter(["txt"]))
        await _record(graph_provider, cid, sim.item_id("me-two-new"), "two.md, outside the filter", present=False)
        await _record(graph_provider, cid, sim.item_id("me-three"), "three.txt, inside the filter")
        sim.ops({"op": "rename", "item": "me-three", "name": "three.md"})
        await sync_and_wait(pipeshub_client, graph_provider, cid)
        await _record(graph_provider, cid, sim.item_id("me-three"), "a file renamed out of the filter", present=False)
        await _record(graph_provider, cid, sim.item_id("other-shared"), "shared.txt, still inside")

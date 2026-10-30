# pyright: ignore-file

"""Dropbox team: items over their life, and their content, against the Dropbox simulator (design: DESIGN.md here).

The tenant is ``lifecycle_scenario``: the team of ``team_scenario`` (Projects team folder, Design shared folder)
plus, in alice's home, trash-me.txt and falconry.txt (both with known text), mover.txt, and Doomed/x.txt with
Doomed/Inner/y.txt; and a second team folder, Archive, with minutes.txt. Indexing is on, so deletes and edits are
checked in the vector store too.

One connector, in order (each test leaves Dropbox as the next expects):
  DBX-L-01  a deleted file leaves the graph and the vector store
  DBX-L-02  the file is restored at Dropbox: it is back, indexed again, and its readers open it
  DBX-L-03  a folder deleted with its subtree: every descendant leaves, siblings stay
  DBX-L-04  a file moved from a home into a team folder keeps its record and gains the folder's members; moved
            back, it loses them
  DBX-L-05  a team folder is archived (Dropbox: nobody reads it through the member views): its files leave;
            restored, they come back
  DBX-L-06  an edit replaces the vectors (new text in, old text out) and the stream serves the new bytes
  DBX-L-07  "Index Shared Items" off: a new shared file syncs but is not indexed; a new private file is
  DBX-L-08  Dropbox refuses the refresh token: a stream fails with an error, never a 401 (which signs the PipesHub
            user out) and never a body
"""

from __future__ import annotations

from typing import Any

import pytest

from connectors.dropbox_sim.dropbox_it import (
    apply_filters,
    assert_access_matches_source,
    assert_no_simulator_problems,
    assert_succeeded,
    record,
    records,
    sync,
)
from connectors.dropbox_sim.dropbox_sim import lifecycle_scenario
from helper.graph_provider import GraphProviderProtocol  # type: ignore[import-not-found]
from helper.graph_provider_utils import async_poll_until  # type: ignore[import-not-found]
from helper.indexing_wait import wait_until_record_indexing_completed  # type: ignore[import-not-found]
from helper.vector_store import VectorStoreProbe  # type: ignore[import-not-found]
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

pytestmark = [
    pytest.mark.integration,
    pytest.mark.dropbox_sim,
    pytest.mark.asyncio(loop_scope="session"),
]

INDEX_TIMEOUT_SEC = 600
SHARED_OFF = {"shared": {"operator": "is", "type": "boolean", "value": False}}


async def _indexed(graph: GraphProviderProtocol, client: PipeshubClient, ctx: dict[str, Any], key: str) -> Any:
    return await wait_until_record_indexing_completed(graph, ctx["connector_id"], ctx["sim"].item_id(key),
                                                      timeout=INDEX_TIMEOUT_SEC, pipeshub_client=client,
                                                      description=f"{key} indexed")


async def _vectors_hold(vector_store: VectorStoreProbe, virtual_record_id: str, present: str,
                        absent: str | None = None) -> None:
    async def check() -> bool | None:
        joined = "\n".join(await vector_store.content_texts(virtual_record_id))
        return (present in joined and (absent is None or absent not in joined)) or None

    await async_poll_until(check, timeout=INDEX_TIMEOUT_SEC, interval=5,
                           description=f"vectors of {virtual_record_id} hold {present!r}"
                                       + (f" and not {absent!r}" if absent else ""))


class TestDropboxTeamLifecycle:
    TENANT = staticmethod(lifecycle_scenario)
    AUTO_INDEX = True

    @pytest.mark.order(0)
    async def test_dbx_l_01_deleted_file_leaves_graph_and_vectors(self, dropbox_team: dict[str, Any],
                                                                  graph_provider: GraphProviderProtocol,
                                                                  pipeshub_client: PipeshubClient,
                                                                  vector_store: VectorStoreProbe) -> None:
        sim, cid = dropbox_team["sim"], dropbox_team["connector_id"]
        rec = await _indexed(graph_provider, pipeshub_client, dropbox_team, "l-trash")
        assert rec.virtual_record_id, "an indexed record has a virtual record id"
        await vector_store.assert_embeddings_present(rec.virtual_record_id)
        sim.ops({"op": "delete", "item": "l-trash"})
        await sync(pipeshub_client, graph_provider, dropbox_team)
        await record(graph_provider, cid, sim.item_id("l-trash"), "the deleted file", present=False)
        await vector_store.assert_embeddings_gone(rec.virtual_record_id)

    @pytest.mark.order(1)
    async def test_dbx_l_02_restored_file_comes_back(self, dropbox_team: dict[str, Any],
                                                     graph_provider: GraphProviderProtocol,
                                                     pipeshub_client: PipeshubClient) -> None:
        dropbox_team["sim"].ops({"op": "restore", "item": "l-trash"})
        await sync(pipeshub_client, graph_provider, dropbox_team)
        await _indexed(graph_provider, pipeshub_client, dropbox_team, "l-trash")
        await assert_access_matches_source(pipeshub_client, graph_provider, dropbox_team, ["l-trash"],
                                           "after the restore")

    @pytest.mark.order(2)
    async def test_dbx_l_03_folder_deleted_with_its_subtree(self, dropbox_team: dict[str, Any],
                                                            graph_provider: GraphProviderProtocol,
                                                            pipeshub_client: PipeshubClient) -> None:
        dropbox_team["sim"].ops({"op": "delete", "item": "l-doomed"})
        await sync(pipeshub_client, graph_provider, dropbox_team)
        await records(graph_provider, dropbox_team, ["l-doomed", "l-doomed-sub", "l-doomed-a", "l-doomed-b"],
                      present=False)
        await records(graph_provider, dropbox_team, ["alice-plans", "alice-q3", "l-mover"])

    @pytest.mark.order(3)
    async def test_dbx_l_04_move_into_a_team_folder_and_back(self, dropbox_team: dict[str, Any],
                                                             graph_provider: GraphProviderProtocol,
                                                             pipeshub_client: PipeshubClient) -> None:
        sim = dropbox_team["sim"]
        before = (await records(graph_provider, dropbox_team, ["l-mover"]))["l-mover"]
        sim.ops({"op": "move", "item": "l-mover", "to": "projects"})
        await sync(pipeshub_client, graph_provider, dropbox_team)
        moved = (await records(graph_provider, dropbox_team, ["l-mover"]))["l-mover"]
        assert moved.id == before.id, "Dropbox keeps a moved file's id, so PipesHub keeps its record"
        await assert_access_matches_source(pipeshub_client, graph_provider, dropbox_team, ["l-mover"],
                                           "after the move into Projects")
        sim.ops({"op": "move", "item": "l-mover", "to": "home:alice"})
        await sync(pipeshub_client, graph_provider, dropbox_team)
        await assert_access_matches_source(pipeshub_client, graph_provider, dropbox_team, ["l-mover"],
                                           "after the move back home")

    @pytest.mark.order(4)
    async def test_dbx_l_05_team_folder_archived_then_restored(self, dropbox_team: dict[str, Any],
                                                               graph_provider: GraphProviderProtocol,
                                                               pipeshub_client: PipeshubClient) -> None:
        sim, cid = dropbox_team["sim"], dropbox_team["connector_id"]
        sim.ops({"op": "set_team_folder_status", "folder": "archive", "status": "archived"})
        await sync(pipeshub_client, graph_provider, dropbox_team)
        assert not sim.readers("ar-file"), "the simulator lets nobody read an archived team folder"
        await assert_access_matches_source(pipeshub_client, graph_provider, dropbox_team, ["ar-file", "p-spec"],
                                           "after Archive was archived")
        sim.ops({"op": "set_team_folder_status", "folder": "archive", "status": "active"})
        await sync(pipeshub_client, graph_provider, dropbox_team)
        await assert_access_matches_source(pipeshub_client, graph_provider, dropbox_team, ["ar-file"],
                                           "after Archive was restored")
        assert_succeeded(pipeshub_client, cid, "the sync after the restore")

    @pytest.mark.order(5)
    async def test_dbx_l_06_edit_replaces_vectors_and_stream(self, dropbox_team: dict[str, Any],
                                                             graph_provider: GraphProviderProtocol,
                                                             pipeshub_client: PipeshubClient,
                                                             vector_store: VectorStoreProbe) -> None:
        sim, cid = dropbox_team["sim"], dropbox_team["connector_id"]
        before = await _indexed(graph_provider, pipeshub_client, dropbox_team, "c-text")
        await _vectors_hold(vector_store, before.virtual_record_id, "Merlin hood")
        sim.ops({"op": "edit", "item": "c-text", "text": "Gyrfalcon jesses are oiled every week in the mews."})
        await sync(pipeshub_client, graph_provider, dropbox_team)

        async def new_revision() -> bool | None:
            rec = await graph_provider.get_record_by_external_id(cid, sim.item_id("c-text"))
            return (rec is not None and rec.external_revision_id != before.external_revision_id) or None

        await async_poll_until(new_revision, timeout=180, interval=3, description="falconry.txt's new revision")
        rec = await _indexed(graph_provider, pipeshub_client, dropbox_team, "c-text")
        await _vectors_hold(vector_store, rec.virtual_record_id, "Gyrfalcon jesses", absent="Merlin hood")
        resp = pipeshub_client.stream_record(rec.id)
        assert resp.status_code == 200, resp.text[:300]
        assert resp.content == sim.content("c-text"), "the stream is the edited bytes"

    @pytest.mark.order(6)
    async def test_dbx_l_07_shared_items_not_indexed(self, dropbox_team: dict[str, Any],
                                                     graph_provider: GraphProviderProtocol,
                                                     pipeshub_client: PipeshubClient) -> None:
        sim, cid = dropbox_team["sim"], dropbox_team["connector_id"]
        await apply_filters(pipeshub_client, graph_provider, dropbox_team, indexing_values=SHARED_OFF, auto_index=True)
        sim.ops({"op": "add_file", "in": "projects", "key": "i-shared", "name": "team-plan.txt"},
                {"op": "add_file", "in": "home:alice", "key": "i-private", "name": "mine.txt"})
        await sync(pipeshub_client, graph_provider, dropbox_team)

        async def not_indexed() -> bool | None:
            rec = await graph_provider.get_record_by_external_id(cid, sim.item_id("i-shared"))
            return (rec is not None and rec.indexing_status == "AUTO_INDEX_OFF") or None

        await async_poll_until(not_indexed, timeout=INDEX_TIMEOUT_SEC, interval=5,
                               description="the shared file synced and left unindexed")
        await _indexed(graph_provider, pipeshub_client, dropbox_team, "i-private")

    @pytest.mark.order(7)
    async def test_dbx_l_08_stream_after_dropbox_refuses_the_token(self, dropbox_team: dict[str, Any],
                                                                   graph_provider: GraphProviderProtocol,
                                                                   pipeshub_client: PipeshubClient) -> None:
        sim, cid = dropbox_team["sim"], dropbox_team["connector_id"]
        rec = await record(graph_provider, cid, sim.item_id("alice-q3"), "q3.md")
        assert_no_simulator_problems(sim)
        sim.revoke_refresh_token()
        sim.expire_access_tokens()
        # stream_record raises on any error status; this needs the error answer itself.
        resp = pipeshub_client.request("GET", f"/api/v1/knowledgeBase/stream/record/{rec.id}")
        assert resp.status_code != 401, "a refused Dropbox token must not sign the PipesHub user out"
        assert resp.status_code >= 400, f"a stream Dropbox refused answered {resp.status_code}"
        assert resp.content != sim.content("alice-q3")

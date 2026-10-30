# pyright: ignore-file

"""Dropbox deletes with the trash on, against the Dropbox simulator (design: DESIGN.md here).

With the Labs setting "Move Deleted Records to the Trash" (``ENABLE_SOFT_DELETE``) on, a record Dropbox deletes
must be hidden from everyone (nobody opens or finds it, its vectors are gone) and come back when Dropbox restores
the file. The setting applies to the whole deployment. Connector suites run one module at a time on a single
worker (the ``serial`` xdist group in ``conftest.py``), so nothing else runs while it is on, and the module turns
it back off when it ends.

  DBX-TR-01  a file deleted at Dropbox is hidden: no record anyone can open, no vectors
  DBX-TR-02  Dropbox restores it: it is back, indexed, and its readers open it again
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from connectors.dropbox_sim.dropbox_it import access, assert_access_matches_source, record, sync
from connectors.dropbox_sim.dropbox_sim import lifecycle_scenario
from helper.graph_provider import GraphProviderProtocol  # type: ignore[import-not-found]
from helper.indexing_wait import wait_until_record_indexing_completed  # type: ignore[import-not-found]
from helper.vector_rebuild import read_platform_settings, write_platform_settings  # type: ignore[import-not-found]
from helper.vector_store import VectorStoreProbe  # type: ignore[import-not-found]
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

pytestmark = [
    pytest.mark.integration,
    pytest.mark.dropbox_sim,
    pytest.mark.asyncio(loop_scope="session"),
]

TRASH_FLAG = "ENABLE_SOFT_DELETE"
INDEX_TIMEOUT_SEC = 600


@pytest.fixture(scope="class")
def trash_on(pipeshub_client: PipeshubClient) -> Iterator[None]:
    before = read_platform_settings(pipeshub_client)
    write_platform_settings(pipeshub_client, before.with_flag(TRASH_FLAG, True))
    try:
        yield
    finally:
        write_platform_settings(pipeshub_client, before)


@pytest.mark.usefixtures("trash_on")
class TestDropboxTeamTrash:
    TENANT = staticmethod(lifecycle_scenario)
    AUTO_INDEX = True

    @pytest.mark.order(0)
    async def test_dbx_tr_01_deleted_file_is_hidden(self, dropbox_team: dict[str, Any],
                                                    graph_provider: GraphProviderProtocol,
                                                    pipeshub_client: PipeshubClient,
                                                    vector_store: VectorStoreProbe) -> None:
        sim, cid = dropbox_team["sim"], dropbox_team["connector_id"]
        rec = await wait_until_record_indexing_completed(graph_provider, cid, sim.item_id("l-trash"),
                                                         timeout=INDEX_TIMEOUT_SEC, pipeshub_client=pipeshub_client,
                                                         description="trash-me.txt indexed")
        await vector_store.assert_embeddings_present(rec.virtual_record_id)
        sim.ops({"op": "delete", "item": "l-trash"})
        await sync(pipeshub_client, graph_provider, dropbox_team)
        await record(graph_provider, cid, sim.item_id("l-trash"), "the deleted file", present=False)
        await vector_store.assert_embeddings_gone(rec.virtual_record_id)
        access(pipeshub_client, dropbox_team, "alice", rec.id, False, "her file once Dropbox deleted it")

    @pytest.mark.order(1)
    async def test_dbx_tr_02_restored_file_comes_back(self, dropbox_team: dict[str, Any],
                                                      graph_provider: GraphProviderProtocol,
                                                      pipeshub_client: PipeshubClient) -> None:
        sim, cid = dropbox_team["sim"], dropbox_team["connector_id"]
        sim.ops({"op": "restore", "item": "l-trash"})
        await sync(pipeshub_client, graph_provider, dropbox_team)
        await record(graph_provider, cid, sim.item_id("l-trash"), "the restored file, out of the trash")
        await wait_until_record_indexing_completed(graph_provider, cid, sim.item_id("l-trash"),
                                                   timeout=INDEX_TIMEOUT_SEC, pipeshub_client=pipeshub_client,
                                                   description="trash-me.txt indexed again")
        await assert_access_matches_source(pipeshub_client, graph_provider, dropbox_team, ["l-trash"],
                                           "after the restore")

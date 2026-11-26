# pyright: ignore-file

"""
BookStack on the simulator – what leaves PipesHub, and what comes back
======================================================================

  TC-BSSIM-DEL-001  A deleted page, chapter and book disappear with what is under them
  TC-BSSIM-DEL-002  A page restored from the recycle bin comes back [F-RESTORE]
  TC-BSSIM-DEL-003  A book restored from the recycle bin comes back with its pages [F-RESTORE]
  TC-BSSIM-DEL-004  A book deleted and purged between syncs disappears with its pages [F-PURGED-GROUPS]
  TC-BSSIM-DEL-005  A page moved into a book the filter excludes disappears [F-MOVED-OUT-OF-SCOPE]
"""

import pytest
from connectors.bookstack.sim_instances import plain_instance
from connectors.bookstack.sim_world import BookStackWorld, succeeded


@pytest.mark.integration
@pytest.mark.bookstack_sim
@pytest.mark.asyncio(loop_scope="session")
class TestBookStackSimDeletion:

    async def test_tc_bssim_del_001_deletes(self, bookstack_world: BookStackWorld) -> None:
        w = bookstack_world
        assert succeeded(await w.start(plain_instance()))
        w.ops({"op": "delete", "id": "welcome"}, {"op": "delete", "id": "policies"}, {"op": "delete", "id": "engineering"})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after a page, a chapter and a book were deleted")

    async def test_tc_bssim_del_002_restored_page(self, bookstack_world: BookStackWorld) -> None:
        w = bookstack_world
        assert succeeded(await w.start(plain_instance()))
        w.ops({"op": "delete", "id": "welcome"})
        await w.sync()
        w.ops({"op": "restore", "id": "welcome"})
        await w.sync()
        await w.assert_matches_source("after Welcome was restored")

    async def test_tc_bssim_del_003_restored_book(self, bookstack_world: BookStackWorld) -> None:
        w = bookstack_world
        assert succeeded(await w.start(plain_instance()))
        w.ops({"op": "delete", "id": "handbook"})
        await w.sync()
        w.ops({"op": "restore", "id": "handbook"})
        await w.sync()
        await w.assert_matches_source("after the Handbook was restored")

    async def test_tc_bssim_del_004_purged_book(self, bookstack_world: BookStackWorld) -> None:
        w = bookstack_world
        assert succeeded(await w.start(plain_instance()))
        w.ops({"op": "delete", "id": "engineering"}, {"op": "destroy", "id": "engineering"})
        await w.sync()
        await w.assert_matches_source("after Engineering was deleted and purged")

    async def test_tc_bssim_del_005_moved_out_of_scope(self, bookstack_world: BookStackWorld) -> None:
        w = bookstack_world
        await w.start(plain_instance(), enable=False)
        finance = w.key("finance")
        w.client.update_connector_filters_sync_config(w.connector_id, filters={
            "sync": {"values": {"book_ids": {"operator": "not_in", "value": [str(finance)], "type": "list"}}}})
        assert succeeded(await w.enable())
        w.ops({"op": "update_page", "id": "welcome", "data": {"book_id": "@finance"}})
        await w.sync()
        await w.assert_matches_source("after Welcome moved into the excluded Finance book", scope_books_out={finance})

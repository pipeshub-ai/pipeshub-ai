# pyright: ignore-file

"""
BookStack on the simulator – what reaches PipesHub, and when
=============================================================

  TC-BSSIM-SYNC-001  First sync matches BookStack (plain instance)
  TC-BSSIM-SYNC-002  New, edited and renamed pages arrive on the next sync, the rename keeping its record
  TC-BSSIM-SYNC-003  Changes on an instance west of UTC (APP_TIMEZONE=America/New_York) arrive [F-TIMEZONE]
  TC-BSSIM-SYNC-004  More changes than one audit-log page arrive [F-AUDIT-PAGING]
  TC-BSSIM-SYNC-005  A page moved with BookStack's Move action follows its new book's access [F-MOVES]
  TC-BSSIM-SYNC-006  A chapter moved on the book sort screen takes its pages and their access [F-MOVES]
  TC-BSSIM-SYNC-007  A first sync at BookStack's default 180 requests a minute loses nothing [F-RATE-LIMIT]
  TC-BSSIM-SYNC-008  Shelves are not synced, whatever is done to them; only books, chapters and pages [C-NO-SHELVES]
"""

import pytest
from connectors.bookstack.sim_instances import DENY_ALL, plain_instance, small_instance
from connectors.bookstack.sim_world import BookStackWorld, succeeded


@pytest.mark.integration
@pytest.mark.bookstack_sim
@pytest.mark.asyncio(loop_scope="session")
class TestBookStackSimSync:

    async def test_tc_bssim_sync_001_first_sync(self, bookstack_world: BookStackWorld) -> None:
        w = bookstack_world
        assert succeeded(await w.start(plain_instance()))
        await w.assert_matches_source("after the first sync")

    async def test_tc_bssim_sync_002_incremental_changes(self, bookstack_world: BookStackWorld) -> None:
        w = bookstack_world
        assert succeeded(await w.start(plain_instance()))
        before = {r["externalRecordId"]: r["id"] for r in await w.records()}
        w.ops({"op": "create_page", "key": "new", "data": {"book_id": "@handbook", "name": "New starter"}},
              {"op": "update_page", "id": "welcome", "data": {"markdown": "Second edition."}},
              {"op": "update_page", "id": "runbook", "data": {"name": "Runbook v2"}})
        assert succeeded(await w.sync())
        after = {r["externalRecordId"]: r for r in await w.records()}
        runbook = f"page/{w.key('runbook')}"
        assert after[runbook]["id"] == before[runbook] and after[runbook]["recordName"] == "Runbook v2"
        await w.assert_matches_source("after new, edited and renamed pages")

    async def test_tc_bssim_sync_003_instance_west_of_utc(self, bookstack_world: BookStackWorld) -> None:
        w = bookstack_world
        assert succeeded(await w.start(plain_instance(), behaviours={"app_timezone": "America/New_York"}))
        w.ops({"op": "create_page", "key": "tz", "data": {"book_id": "@handbook", "name": "Time zones"}},
              {"op": "delete", "id": "expenses"})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after changes on an instance in New York time")

    async def test_tc_bssim_sync_004_more_than_one_audit_page(self, bookstack_world: BookStackWorld) -> None:
        w = bookstack_world
        assert succeeded(await w.start(small_instance(pages=3)))
        w.ops(*[{"op": "create_page", "key": f"n{i}", "data": {"book_id": "@b0", "name": f"Note {i}"}}
                for i in range(130)])
        assert succeeded(await w.sync())
        await w.assert_matches_source("after 130 changes")

    async def test_tc_bssim_sync_005_ui_page_move(self, bookstack_world: BookStackWorld) -> None:
        w = bookstack_world
        assert succeeded(await w.start(plain_instance()))
        w.ops({"op": "move_page", "id": "welcome", "data": {"book_id": "@finance"}})
        await w.sync()
        await w.assert_matches_source("after Welcome was moved into the Finance book")

    async def test_tc_bssim_sync_006_sorted_chapter(self, bookstack_world: BookStackWorld) -> None:
        w = bookstack_world
        assert succeeded(await w.start(plain_instance()))
        w.ops({"op": "sort_chapter", "id": "policies", "data": {"book_id": "@finance"}})
        await w.sync()
        await w.assert_matches_source("after Policies was sorted into the Finance book")

    async def test_tc_bssim_sync_007_default_rate_limit(self, bookstack_world: BookStackWorld) -> None:
        w = bookstack_world
        assert succeeded(await w.start(small_instance(pages=120, books=2), behaviours={"requests_per_minute": 180}))
        await w.assert_matches_source("after a first sync at 180 requests a minute")

    async def test_tc_bssim_sync_008_shelves_are_not_synced(self, bookstack_world: BookStackWorld) -> None:
        w = bookstack_world
        assert succeeded(await w.start(plain_instance()))
        await w.assert_matches_source("after the first sync")
        w.ops({"op": "create_shelf", "key": "team", "data": {"name": "Team", "books": ["@handbook"]}},
              {"op": "update_shelf", "id": "company", "data": {"name": "Company wide", "books": ["@finance"]}},
              {"op": "set_permissions", "kind": "bookshelf", "id": "company",
               "data": {"role_permissions": [{"role_id": "@role:finance", "view": True, "create": False,
                                              "update": False, "delete": False}],
                        "fallback_permissions": {"inheriting": False, **DENY_ALL}}})
        assert succeeded(await w.sync())
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after shelves were created, changed and restricted, and a full sync")

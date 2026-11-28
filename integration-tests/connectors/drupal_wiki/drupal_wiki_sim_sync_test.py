# pyright: ignore-file

"""
Drupal Wiki on the simulator – what a sync stores, and what an incremental sync picks up
=========================================================================================

After each sync the simulator's oracle says which spaces, pages and attachments a correct sync holds, where each sits
(space, parent page) and who may read it (sim_world.py); every record must be there once, filed there, with the wiki's
own name, revision and web address.

  TC-DWSIM-SYNC-001  First sync of the seed wiki: every page and attachment, its space, parent, name, revision (the
                     wiki's lastModified), web address (/node/<id>) and source time equal the wiki's
  TC-DWSIM-SYNC-002  Page-size boundary: a space of 99, 100, 101 and 250 pages, and a page with 100 and 101 attachments,
                     are held in full, each once
  TC-DWSIM-SYNC-003  A sync with nothing changed creates, removes and re-versions nothing
  TC-DWSIM-SYNC-004  Incremental: a page created (at the top and under a page), edited, renamed, re-parented, moved to
                     another space (the move changes lastModified); an attachment added and updated
  TC-DWSIM-SYNC-005  The one-second strict window: pages stamped in the same second as the newest page already synced,
                     across the boundary of a window of pages, are all picked up
  TC-DWSIM-SYNC-006  A change between two pages of a listing (the wiki deletes a page and moves another after the first
                     window is served): the sync does not fail, and the next clean sync matches the wiki
  TC-DWSIM-SYNC-007  A page created in a space the sync had no page of, and in a space created after the first sync
  TC-DWSIM-SYNC-008  An attachment added under a page whose lastModified does not move (the handbook does not say
                     that it must) [#6082: attachments of pages a sync does not write are re-listed]
"""

import pytest
from connectors.drupal_wiki.sim_instances import (
    ALICE,
    ENG_SPACE,
    HANDBOOK_SPACE,
    LEADERSHIP_SPACE,
    PRODUCT_SPACE,
    TWO_FILES_PAGE,
)
from connectors.drupal_wiki.sim_world import CONNECTOR_URL, DrupalWorld, gap, succeeded


def _bulk(w: DrupalWorld, space: int, count: int) -> None:
    w.sim.call("POST", "/_sim/bulk", {"spaceId": space, "count": count})


@pytest.mark.integration
@pytest.mark.drupal_wiki_sim
@pytest.mark.asyncio(loop_scope="session")
class TestDrupalWikiSimSync:

    async def test_tc_dwsim_sync_001_first_sync_of_the_seed_wiki(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        assert succeeded(await w.start())
        await w.assert_matches_source("after the first sync")
        expected = w.sim.expected(w.scope)
        records = {r["externalRecordId"]: r for r in await w.records()}
        for pid, page in expected["pages"].items():
            rec = records[f"page:{pid}"]
            assert rec["recordName"] == page["title"], f"page {pid} is named {rec['recordName']!r}"
            assert rec["externalRevisionId"] == str(page["lastModified"]), f"page {pid}: revision"
            assert rec["webUrl"] == f"{CONNECTOR_URL}/node/{pid}", f"page {pid}: web address {rec['webUrl']}"
        for aid, att in expected["attachments"].items():
            rec = records[f"attachment:{aid}"]
            assert rec["externalRevisionId"] == str(att["lastModified"]), f"attachment {aid}: revision"
            assert rec["webUrl"] == f"{CONNECTOR_URL}/node/{att['page']}", f"attachment {aid}: web address"

    @pytest.mark.parametrize("count", [99, 100, 101, 250])
    async def test_tc_dwsim_sync_002_page_size_boundary_of_pages(self, drupal_world: DrupalWorld, count: int) -> None:
        w = drupal_world
        assert await w.start(enable=False) is None
        space, = (r["space"] for r in w.mutate(("space_create", {"name": f"Bulk {count}"})))
        _bulk(w, space, count)
        assert succeeded(await w.enable())
        await w.assert_matches_source(f"after the first sync of a space of {count} pages")
        assert sum(1 for r in await w.records(parents=False) if r["externalGroupId"] == str(space)) == count

    @pytest.mark.parametrize("count", [100, 101])
    async def test_tc_dwsim_sync_002_page_size_boundary_of_attachments(self, drupal_world: DrupalWorld,
                                                                      count: int) -> None:
        w = drupal_world
        assert await w.start(enable=False) is None
        w.mutate(*[("attachment_add", {"page": TWO_FILES_PAGE, "file_name": f"bulk-{i:03}.txt", "content": f"file {i}"})
                   for i in range(count - 2)])
        assert succeeded(await w.enable())
        await w.assert_matches_source(f"after the first sync of a page with {count} attachments")

    async def test_tc_dwsim_sync_003_nothing_changed(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        assert succeeded(await w.start())
        before = {r["externalRecordId"]: (r["id"], r["version"], r["externalRevisionId"])
                  for r in await w.records(parents=False)}
        assert succeeded(await w.sync())
        assert succeeded(await w.sync(full=True))
        after = {r["externalRecordId"]: (r["id"], r["version"], r["externalRevisionId"])
                 for r in await w.records(parents=False)}
        assert after == before, f"a sync with nothing changed changed {sorted(set(after.items()) ^ set(before.items()))[:6]}"
        await w.assert_matches_source("after syncs with nothing changed")

    async def test_tc_dwsim_sync_004_incremental_changes_of_every_object(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        assert succeeded(await w.start(knobs={"bumps_last_modified": ["edit", "attach", "move", "reparent"]}))
        before = {r["externalRecordId"]: r for r in await w.records(parents=False)}
        top, = (r["page"] for r in w.mutate(("page_create", {"space": ENG_SPACE, "title": "A new top page"})))
        child, = (r["page"] for r in w.mutate(("page_create", {"space": ENG_SPACE, "title": "A new child",
                                                               "parent": top})))
        w.mutate(("attachment_add", {"page": child, "file_name": "new.txt", "media_type": "text/plain"}),
                 ("page_edit", {"id": 104, "body_extra": "<p>edited</p>"}),
                 ("page_rename", {"id": 106, "title": "Team Dashboard (renamed)"}),
                 ("page_reparent", {"id": 107, "parent": 101}),
                 ("page_move", {"id": 303, "space": HANDBOOK_SPACE}),
                 ("attachment_update", {"id": 5002}))
        assert succeeded(await w.sync())
        await w.assert_matches_source("after pages were created, edited, renamed, re-parented and moved")
        after = {r["externalRecordId"]: r for r in await w.records(parents=False)}
        assert after["page:106"]["recordName"] == "Team Dashboard (renamed)"
        assert after["page:104"]["externalRevisionId"] != before["page:104"]["externalRevisionId"]
        assert after["page:104"]["id"] == before["page:104"]["id"], "an edited page got a new record"
        assert after["attachment:5002"]["externalRevisionId"] != before["attachment:5002"]["externalRevisionId"]
        assert after["page:303"]["externalGroupId"] == str(HANDBOOK_SPACE)
        assert set(after) - set(before) >= {f"page:{top}", f"page:{child}"}

    async def test_tc_dwsim_sync_005_one_second_window(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        assert succeeded(await w.start(knobs={"max_page_size": 3}))
        newest = max(p["lastModified"] for p in w.sim.expected()["pages"].values() if p["space"] == ENG_SPACE)
        # Written after the sync, stamped in the second of the newest page it saw: a strict ``>`` on that second
        # would never list them, so the connector stands one second back.
        w.mutate(*[("page_create", {"space": ENG_SPACE, "title": f"Same second {i}", "at": newest}) for i in range(5)])
        assert succeeded(await w.sync())
        await w.assert_matches_source("after five pages stamped in the second of the newest page already synced")
        w.mutate(*[("page_create", {"space": ENG_SPACE, "title": f"Same second again {i}", "at": newest})
                   for i in range(4)])
        assert succeeded(await w.sync())
        await w.assert_matches_source("after four more in that second")

    async def test_tc_dwsim_sync_006_a_change_lands_between_two_pages(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        assert succeeded(await w.start(knobs={"max_page_size": 3}))
        await w.assert_matches_source("after the first sync")
        w.mutate(("page_edit", {"id": 101, "body_extra": "<p>so the space lists again</p>"}),
                 ("page_edit", {"id": 109, "body_extra": "<p>so the space lists again</p>"}))
        # Once the first window of Engineering is served, a page is deleted and another moves away.
        w.triggers({"match": {"method": "GET", "path_regex": "/scope/api/page$", "query_regex": "space=1"},
                    "after_nth": 1,
                    "mutations": [{"op": "page_delete", "args": {"id": 102}},
                                  {"op": "page_move", "args": {"id": 106, "space": PRODUCT_SPACE}}]})
        outcome = await w.sync()
        w.sim.call("DELETE", "/_sim/drupalwiki/triggers")
        assert succeeded(outcome), "a change between two pages of a listing failed the sync"
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the next sync following a change between two pages",
                                      known={"STALE": gap("#5799", records=["page:102"]),
                                             "LEAK:stale-record": gap("#5799", records=["page:102"]),
                                             "PARENT": gap("#5799", records=["page:103", "page:106"])})

    async def test_tc_dwsim_sync_007_first_page_of_a_space_and_a_new_space(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        assert succeeded(await w.start())
        empty, = (r["space"] for r in w.mutate(("space_create", {"name": "Created later"})))
        assert succeeded(await w.sync())
        await w.assert_matches_source("after a space with no page was created")
        w.mutate(("page_create", {"space": empty, "title": "First page"}),
                 ("page_create", {"space": LEADERSHIP_SPACE, "title": "Another private page"}),
                 ("member", {"space": LEADERSHIP_SPACE, "principal_type": "user", "principal_id": ALICE}))
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the first page of that space and a role on Leadership")

    async def test_tc_dwsim_sync_008_an_attachment_added_under_a_page_whose_lastmodified_stays(
            self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        assert succeeded(await w.start(knobs={"bumps_last_modified": ["edit"]}))
        late = w.mutate(("attachment_add", {"page": TWO_FILES_PAGE, "file_name": "late.txt",
                                            "content": "added quietly"}))[0]["attachment"]
        assert succeeded(await w.sync())
        await w.assert_matches_source("after an attachment was added under a page whose lastModified stayed",
                                      known={"MISSING": gap("#6082", records=[f"attachment:{late}"])})

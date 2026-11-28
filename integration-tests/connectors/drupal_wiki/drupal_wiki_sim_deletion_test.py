# pyright: ignore-file

"""
Drupal Wiki on the simulator – what is gone at the source, and a full sync
===========================================================================

A page, attachment or space deleted in the wiki must stop being readable in PipesHub; a full sync removes whatever it
read to be gone, and keeps what it could not read. Where an incremental sync does not see a deletion yet, the test
names the open pull request that makes it see it, and fails once that gap no longer shows.

  TC-DWSIM-DEL-001  Pages and an attachment deleted while the connector is disabled: the sync after enabling it still
                    holds them [#5799]; a full sync removes them
  TC-DWSIM-DEL-002  A full sync whose page listing of one space fails persistently removes nothing and does not report
                    success; the next clean full sync matches the wiki
  TC-DWSIM-DEL-003  A space deleted with its pages: the incremental sync still holds them [#5799]; a full sync removes
                    pages and space
  TC-DWSIM-DEL-004  A page sent to the trash from the UI (the trash extension on; hidden, or listed in the trash
                    space), then restored with lastModified unchanged
  TC-DWSIM-DEL-005  An attachment deleted while its page's lastModified stays: still held by an incremental sync
                    [#6082], removed by a full sync
  TC-DWSIM-DEL-006  A page deleted whose sub-pages stay (they move one level up)
"""

import pytest
from connectors.drupal_wiki.sim_instances import HANDBOOK_SPACE, ROOT_PAGE
from connectors.drupal_wiki.sim_world import SEED_SPECIAL_SPACES, DrupalWorld, gap, succeeded

PAGE_LIST = r"^/api/rest/scope/api/page$"


def gone(pr: str, *records: str) -> dict:
    """Records of what the wiki no longer has: stored, and read by people, until a full sync unlinks them."""
    return {"STALE": gap(pr, records=records), "LEAK:stale-record": gap(pr, records=records)}



@pytest.mark.integration
@pytest.mark.drupal_wiki_sim
@pytest.mark.asyncio(loop_scope="session")
class TestDrupalWikiSimDeletion:

    async def test_tc_dwsim_del_001_gone_while_the_connector_was_disabled(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        assert succeeded(await w.start())
        w.client.toggle_sync(w.connector_id, enable=False)
        await w._wait_idle()
        w.mutate(("page_delete", {"id": 109}), ("page_delete", {"id": 303}))
        assert succeeded(await w.enable())
        await w.assert_matches_source("after enabling the connector with pages deleted meanwhile", known=gone("#5799", "page:109", "attachment:5003", "page:303"))
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after a full sync")

    async def test_tc_dwsim_del_002_full_sync_that_could_not_read_a_space_keeps_it(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        assert succeeded(await w.start())
        before = await w.holdings()
        w.mutate(("page_delete", {"id": 109}))
        w.faults({"match": {"path_regex": PAGE_LIST}, "effect": {"type": "status", "code": 503},
                  "trigger": {"max_times": 500}})
        outcome = await w.sync(full=True)
        w.clear_faults()
        assert not succeeded(outcome), "a full sync that could not list any page reported success"
        await w.assert_kept_not_overshared(before, "after a full sync that could not list the pages")
        held = await w.holdings()
        assert held["records"] >= before["records"], f"a full sync that read nothing removed {before['records'] - held['records']}"
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after the clean full sync")

    async def test_tc_dwsim_del_003_space_deleted(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        assert succeeded(await w.start())
        w.mutate(("space_delete", {"id": HANDBOOK_SPACE}))
        assert succeeded(await w.sync())
        await w.assert_matches_source("after a space was deleted",
                                      known={**gone("#5799", "page:401", "page:402", "attachment:5005"),
                                             # the deleted space is still a record group until a full sync
                                             "STALE-GROUP": gap("#5799", names=[*SEED_SPECIAL_SPACES, "Company Handbook"])})
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after a full sync")

    @pytest.mark.parametrize("visibility", ["hidden", "trash_space"])
    async def test_tc_dwsim_del_004_page_in_the_trash_and_restored(self, drupal_world: DrupalWorld,
                                                                   visibility: str) -> None:
        w = drupal_world
        assert succeeded(await w.start(knobs={"trash_extension": True, "trashed_visibility": visibility}))
        w.mutate(("page_delete", {"id": 402, "mode": "ui"}), ("page_delete", {"id": 202, "mode": "ui"}))
        assert succeeded(await w.sync())
        # Listed in the trash space, the page is written under it, where nobody reads it; hidden, it is not seen at all.
        gaps = (gone("#5799", "page:402", "page:202") if visibility == "hidden"
                else {"STALE": gap("#5799", records=["page:402", "page:202"])})
        await w.assert_matches_source(f"after pages went to the trash ({visibility})", known=gaps)
        assert succeeded(await w.sync(full=True))
        # In the trash space (a synced space whatever its type) the pages stay, under the trash's grants.
        await w.assert_matches_source("after a full sync", known=None if visibility == "hidden" else {
            "STALE": gap("#5799", records=["page:402", "page:202"], spaces=["91"])})
        w.mutate(("page_restore", {"id": 202}))
        assert succeeded(await w.sync())
        # Hidden: the full sync had unlinked the page, and nothing lists it again. In the trash space: it stays filed there.
        restored = ({"DENIED:open-space": gap("#5799", records=["page:202"])} if visibility == "hidden" else
                    {"DENIED:open-space": gap("#5799", records=["page:202"]),
                     "PARENT": gap("#5799", records=["page:202"]),
                     "STALE": gap("#5799", records=["page:402"], spaces=["91"])})
        await w.assert_matches_source("after the page was restored (its lastModified did not change)", known=restored)
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after a full sync", known=None if visibility == "hidden" else {
            "STALE": gap("#5799", records=["page:402"], spaces=["91"])})

    async def test_tc_dwsim_del_005_attachment_deleted_under_an_unchanged_page(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        assert succeeded(await w.start(knobs={"bumps_last_modified": ["edit"]}))
        w.mutate(("attachment_delete", {"id": 5000}))
        assert succeeded(await w.sync())
        await w.assert_matches_source("after an attachment was deleted under a page whose lastModified stayed",
                                      known=gone("#6082", "attachment:5000"))
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after a full sync")

    async def test_tc_dwsim_del_006_page_deleted_and_its_children_stay(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        assert succeeded(await w.start())
        w.mutate(("page_delete", {"id": ROOT_PAGE}), ("page_edit", {"id": 102, "body_extra": "<p>moved up</p>"}))
        assert succeeded(await w.sync())
        await w.assert_matches_source("after a page was deleted and its child edited", known=gone("#5799", "page:101"))
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after a full sync")

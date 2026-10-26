# pyright: ignore-file

"""
SharePoint Online on the simulator – deletions and change tracking
==================================================================

What is deleted in SharePoint must stop opening in PipesHub, including what Graph's delta never reports
(libraries, sites, pages, the contents of a deleted folder), and including deletions that happen while the
connector's delta token is broken or expired. The finding each test proves is in brackets; those fail on
upstream main and pass with the audit's fixes.

  TC-SPSIM-DEL-001  A deleted file disappears
  TC-SPSIM-DEL-002  A deleted folder takes its files and subfolders [F-FOLDER-DELETE-CHILDREN]
  TC-SPSIM-DEL-003  A deleted library disappears [F-CONTAINER-DELETE]
  TC-SPSIM-DEL-004  A deleted site disappears [F-CONTAINER-DELETE]
  TC-SPSIM-DEL-005  A deleted page disappears [F-PAGE-DELETE]
  TC-SPSIM-DEL-006  After Graph answers 410 for every delta token, the resync applies deletions made
                    meanwhile and keeps additions [F-DELTA-RESET-LOSES-DELETES]
  TC-SPSIM-DEL-007  A delta read that fails keeps the token, so the next sync still applies the deletion
                    [F-DELTA-RESET-LOSES-DELETES]
  TC-SPSIM-DEL-008  A library listing that comes back empty removes nothing
"""

import logging

import pytest
from connectors.sharepoint.sim_tenants import base_tenant, small_tenant
from connectors.sharepoint.sim_world import (
    QUIET,
    SharePointWorld,
    empty_listing,
    fault,
)

logger = logging.getLogger("sharepoint-sim-deletion-test")

# Sources disagree on whether Graph's delta sends a tombstone for each child of a deleted folder; without
# them only the folder is reported, which is what the old code could not handle.
CHILDREN_NOT_REPORTED = {**QUIET, "delta_deleted_folder_reports_children": False}


@pytest.mark.integration
@pytest.mark.sharepoint_sim
@pytest.mark.asyncio(loop_scope="session")
class TestSharePointSimDeletion:

    async def test_tc_spsim_del_001_deleted_file_disappears(self, sharepoint_world: SharePointWorld) -> None:
        w = sharepoint_world
        await w.start(base_tenant(), behaviours=QUIET)
        w.ops({"op": "delete", "item": "eng-design"})
        await w.sync()
        await w.assert_matches_source("after the file is deleted")

    async def test_tc_spsim_del_002_deleted_folder_takes_children(self, sharepoint_world: SharePointWorld) -> None:
        w = sharepoint_world
        await w.start(base_tenant(), behaviours=CHILDREN_NOT_REPORTED)
        w.ops({"op": "delete", "item": "hr-salaries"})
        await w.sync()
        await w.assert_matches_source("after the Salaries folder is deleted")

    async def test_tc_spsim_del_003_deleted_library_disappears(self, sharepoint_world: SharePointWorld) -> None:
        w = sharepoint_world
        await w.start(base_tenant(), behaviours=QUIET)
        w.ops({"op": "delete_library", "library": "eng-archive"})
        await w.sync()
        await w.assert_matches_source("after the Archive library is deleted")

    async def test_tc_spsim_del_004_deleted_site_disappears(self, sharepoint_world: SharePointWorld) -> None:
        w = sharepoint_world
        await w.start(base_tenant(), behaviours=QUIET)
        w.ops({"op": "delete_site", "site": "fin"})
        await w.sync()
        await w.assert_matches_source("after the Finance site is deleted")

    async def test_tc_spsim_del_005_deleted_page_disappears(self, sharepoint_world: SharePointWorld) -> None:
        w = sharepoint_world
        await w.start(base_tenant(), behaviours=QUIET)
        w.ops({"op": "delete", "item": "eng-page"})
        await w.sync()
        await w.assert_matches_source("after the page is deleted")

    async def test_tc_spsim_del_006_expired_delta_token_resyncs_and_removes_deleted(
        self, sharepoint_world: SharePointWorld,
    ) -> None:
        w = sharepoint_world
        await w.start(base_tenant(), behaviours=QUIET)
        w.ops({"op": "delete", "item": "hr-handbook"}, {"op": "delete", "item": "eng-restricted"},
              {"op": "add_file", "parent": "eng-specs", "name": "after.docx", "key": "eng-after"})
        w.expire_delta_tokens()
        # Not asserted: the group delta's token expires too, and the fix records that part as skipped.
        await w.sync()
        await w.assert_matches_source("after Graph refused every delta token")

    async def test_tc_spsim_del_007_delta_error_keeps_deletions(self, sharepoint_world: SharePointWorld) -> None:
        w = sharepoint_world
        await w.start(small_tenant(), behaviours=QUIET)
        w.ops({"op": "delete", "item": "b"})
        w.faults(fault("/root/delta", 502, max_times=50))
        await w.sync()
        w.clear_faults()
        await w.sync()
        await w.assert_matches_source("after a failed delta read and a clean sync")

    async def test_tc_spsim_del_008_empty_library_listing_removes_nothing(self, sharepoint_world: SharePointWorld) -> None:
        w = sharepoint_world
        await w.start(base_tenant(), behaviours=QUIET)
        w.faults(empty_listing(r"/sites/[^/]+/drives$"))
        await w.sync()
        w.clear_faults()
        await w.assert_matches_source("after a sync whose library listings came back empty")

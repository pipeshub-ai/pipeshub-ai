# pyright: ignore-file

"""
SharePoint Online on the simulator – sync, filters and streaming
================================================================

Each test owns a simulated tenant (sim_tenants.py), syncs it with a connector of its own, and compares what
every person in the tenant can open in PipesHub with what the simulator's oracle says they may read
(sim_world.py). Unlike the live suite next to it, these tests change the source. Findings in brackets fail
on upstream main and pass with the audit's fixes; the rest guard behaviour found sound.

  TC-SPSIM-SYNC-001  First sync matches the source; no page is stored twice [F-PAGES-AS-FILES]
  TC-SPSIM-SYNC-002  An unchanged incremental sync changes nothing
  TC-SPSIM-SYNC-003  Incremental sync picks up added, edited, renamed and moved files, and the folder
                     tree is the source's [F-ROOT-PARENT]
  TC-SPSIM-SYNC-004  A full sync converges on the source
  TC-SPSIM-FLT-001   Narrowing the site filter removes the other sites; widening brings them back
  TC-SPSIM-FLT-002   A library filter syncs that library only
  TC-SPSIM-FLT-003   Narrowing the extension filter removes the other files
  TC-SPSIM-FLT-004   Narrowing the library filter after a sync removes the other libraries' files
  TC-SPSIM-STR-001   Files and pages stream what the source holds; a file gone from the source does not
"""

import logging
from typing import Any

import pytest
from connectors.sharepoint.sim_tenants import base_tenant, small_tenant
from connectors.sharepoint.sim_world import (
    QUIET,
    SharePointWorld,
    succeeded,
)

logger = logging.getLogger("sharepoint-sim-sync-test")


def _sites_filter(site_ids: list[str]) -> dict[str, Any]:
    return {"sync": {"values": {"site_ids": {"operator": "in", "value": site_ids, "type": "list"}}}}


@pytest.mark.integration
@pytest.mark.sharepoint_sim
@pytest.mark.asyncio(loop_scope="session")
class TestSharePointSimSync:

    async def test_tc_spsim_sync_001_first_sync_matches_source(self, sharepoint_world: SharePointWorld) -> None:
        """TC-SPSIM-SYNC-001: with the Site Pages library listed among a site's drives. A live tenant did not
        list it, but the documents allow it, and listed is the case that stored every page twice."""
        w = sharepoint_world
        outcome = await w.start(base_tenant(), behaviours={"drives_include_site_pages": True})
        assert succeeded(outcome), f"first sync reported {outcome}"
        await w.assert_matches_source("after the first sync")

    async def test_tc_spsim_sync_002_unchanged_sync_changes_nothing(self, sharepoint_world: SharePointWorld) -> None:
        w = sharepoint_world
        await w.start(small_tenant(), behaviours=QUIET)
        before = {r["id"]: r["version"] for r in await w.records()}
        outcome = await w.sync()
        assert succeeded(outcome), f"unchanged sync reported {outcome}"
        after = {r["id"]: r["version"] for r in await w.records()}
        assert before.keys() == after.keys(), (
            f"records appeared or went on an unchanged sync: added {sorted(after.keys() - before.keys())}, "
            f"gone {sorted(before.keys() - after.keys())}"
        )
        moved = sorted(rid for rid in before if before[rid] != after[rid])
        assert not moved, f"record versions moved on an unchanged sync: {moved}"
        await w.assert_matches_source("after an unchanged sync")

    async def test_tc_spsim_sync_003_incremental_picks_up_changes(self, sharepoint_world: SharePointWorld) -> None:
        w = sharepoint_world
        await w.start(base_tenant(), behaviours=QUIET)
        before = await w.record_of("fin-budget")
        design_before = await w.record_of("eng-design")
        w.ops({"op": "add_file", "parent": "hr-policies", "name": "leave.pdf", "key": "hr-leave"},
              {"op": "edit_file", "item": "eng-design", "size": 9000},
              {"op": "rename", "item": "fin-budget", "name": "budget-2027.xlsx"},
              {"op": "add_folder", "parent": "intranet-docs", "name": "Archive", "key": "intranet-arch"},
              {"op": "move", "item": "intranet-news", "parent": "intranet-arch"})
        outcome = await w.sync()
        assert succeeded(outcome), f"incremental sync reported {outcome}"
        await w.assert_matches_source("after the incremental sync")
        after = await w.record_of("fin-budget")
        assert before is not None and after is not None and after.id == before.id, (
            "a rename must keep the record, not replace it"
        )
        assert after.record_name == "budget-2027.xlsx", f"the rename was not applied: {after.record_name!r}"
        design_after = await w.record_of("eng-design")
        # The stored record, not a stream: streaming reads the source at that moment, edited or not.
        assert design_before is not None and design_after is not None
        assert design_after.external_revision_id != design_before.external_revision_id, (
            "the edit was not applied: the stored record still has the old revision"
        )
        await w.assert_hierarchy_matches_source("parents after the move")

    async def test_tc_spsim_sync_004_full_sync_converges(self, sharepoint_world: SharePointWorld) -> None:
        w = sharepoint_world
        await w.start(base_tenant(), behaviours=QUIET)
        deleted = await w.record_of("fin-budget")
        assert deleted is not None
        w.ops({"op": "add_file", "parent": "eng-specs", "name": "api.md", "key": "eng-api"},
              {"op": "delete", "item": "fin-budget"})
        outcome = await w.sync(full=True)
        assert succeeded(outcome), f"full sync reported {outcome}"
        # assert_matches_source reports a stale record only when someone can still open it.
        active = {r["id"] for r in await w.records() if not r.get("isDeleted") and not r.get("isPlaceholder")}
        assert deleted.id not in active, "the full sync left the deleted file's record active"
        await w.assert_matches_source("after the full sync")

    async def test_tc_spsim_flt_001_site_filter_narrowed_then_widened(self, sharepoint_world: SharePointWorld) -> None:
        w = sharepoint_world
        await w.start(base_tenant(), behaviours=QUIET)
        eng = w.key("eng")
        await w.set_filters(_sites_filter([eng]))
        w.scope_sites = {eng}
        await w.assert_matches_source("after narrowing to one site")
        await w.set_filters({"sync": {"values": {}}})
        w.scope_sites = None
        await w.assert_matches_source("after widening again")

    async def test_tc_spsim_flt_002_library_filter(self, sharepoint_world: SharePointWorld) -> None:
        w = sharepoint_world
        library = "contoso.sharepoint.com/sites/eng/shared documents"
        await w.start(base_tenant(), behaviours=QUIET, filters={
            "sync": {"values": {"drive_ids": {"operator": "in", "value": [library], "type": "list"}}}})
        w.scope_libraries = {w.library("eng-docs")["id"]}
        await w.assert_matches_source("with one library selected")

    async def test_tc_spsim_flt_003_extension_filter_narrowed(self, sharepoint_world: SharePointWorld) -> None:
        w = sharepoint_world
        await w.start(base_tenant(), behaviours=QUIET)
        await w.set_filters({"sync": {"values": {
            "file_extensions": {"operator": "in", "value": ["pdf"], "type": "multiselect"}}}})
        w.scope_extensions = {"pdf"}
        await w.assert_matches_source("after narrowing to pdf")

    async def test_tc_spsim_flt_004_library_filter_narrowed(self, sharepoint_world: SharePointWorld) -> None:
        w = sharepoint_world
        await w.start(base_tenant(), behaviours=QUIET)
        library = "contoso.sharepoint.com/sites/eng/shared documents"
        await w.set_filters({"sync": {"values": {
            "drive_ids": {"operator": "in", "value": [library], "type": "list"}}}})
        w.scope_libraries = {w.library("eng-docs")["id"]}
        await w.assert_matches_source("after narrowing to one library")

    async def test_tc_spsim_str_001_files_and_pages_stream(self, sharepoint_world: SharePointWorld) -> None:
        w = sharepoint_world
        await w.start(base_tenant(), behaviours=QUIET)
        for key in ("eng-design", "hr-handbook", "intranet-news"):
            record = await w.record_of(key)
            assert record is not None, f"{key} has no record"
            status, body = w.stream(record.id, w.reader_of(key))
            want = w.sim.content(w.key(key))
            assert status == 200 and body == want, (
                f"{key} streamed HTTP {status}, {len(body)} bytes; the source holds {len(want)}"
            )
        page = await w.record_of("eng-page")
        assert page is not None, "the page has no record"
        status, body = w.stream(page.id, w.reader_of("eng-page"))
        assert status == 200 and b"Engineering news" in body, f"the page streamed HTTP {status}: {body[:200]!r}"

        design, reader = await w.record_of("eng-design"), w.reader_of("eng-design")
        w.ops({"op": "delete", "item": "eng-design"})
        status, _ = w.stream(design.id, reader)
        assert status == 404, f"a file gone from the source streamed HTTP {status}, not 404"

# pyright: ignore-file

"""
ServiceNow on the simulator – what reaches PipesHub, and who may open it
========================================================================

  TC-SNSIM-SYNC-001  The first sync matches the instance: every published article search shows (one record per
                     number) and its files, each opened by exactly its ServiceNow readers, across the 14
                     knowledge bases' access patterns
  TC-SNSIM-SYNC-002  A batch of instance changes (edits, new versions, new, retired and deleted articles, files
                     added and removed, criteria and memberships changed) arrives on the next sync
  TC-SNSIM-SYNC-003  A new published version keeps the article's record
  TC-SNSIM-SYNC-004  A full sync after changes leaves the same state as the incremental runs
  TC-SNSIM-SYNC-005  A knowledge base filter syncs only that knowledge base; clearing it brings the rest back
"""

import pytest
from connectors.servicenow.servicenow_helpers import kb_filter
from connectors.servicenow.sim_world import ServiceNowWorld, succeeded

# Every kind clock/advance knows but user deactivation, which has its own test (access file).
CHANGES = {"article_edit": 2, "article_version": 2, "article_create": 2, "article_retire": 1, "article_delete": 1,
           "attachment_add": 2, "attachment_delete": 1, "criterion_change": 1, "kb_criteria_change": 1,
           "member_add": 2, "member_remove": 1}


@pytest.mark.integration
@pytest.mark.servicenow_sim
@pytest.mark.asyncio(loop_scope="session")
class TestServiceNowSimSync:

    async def test_tc_snsim_sync_001_first_sync(self, servicenow_world: ServiceNowWorld) -> None:
        w = servicenow_world
        assert succeeded(await w.start())
        await w.assert_matches_source("after the first sync")

    async def test_tc_snsim_sync_002_instance_changes(self, servicenow_world: ServiceNowWorld) -> None:
        w = servicenow_world
        assert succeeded(await w.start())
        w.sim.advance(counts=CHANGES)
        assert succeeded(await w.sync())
        await w.assert_matches_source("after a batch of instance changes")

    async def test_tc_snsim_sync_003_new_version_keeps_record(self, servicenow_world: ServiceNowWorld) -> None:
        w = servicenow_world
        assert succeeded(await w.start())
        article = sorted(w.sim.oracle()["articles"], key=lambda a: a["number"])[0]
        before = {w.key_of(r): r["id"] for r in await w.records()}
        w.sim.mutate({"op": "article_version", "args": {"sys_id": article["sys_id"]}})
        assert succeeded(await w.sync())
        after = {w.key_of(r): r["id"] for r in await w.records()}
        assert after.get(article["number"]) == before[article["number"]], (
            f"{article['number']}: a new version got a new record ({before[article['number']]} -> "
            f"{after.get(article['number'])})")
        await w.assert_matches_source("after a new version was published")

    async def test_tc_snsim_sync_004_full_sync_after_changes(self, servicenow_world: ServiceNowWorld) -> None:
        w = servicenow_world
        assert succeeded(await w.start())
        w.sim.advance(counts=CHANGES)
        assert succeeded(await w.sync())
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after a full sync")

    async def test_tc_snsim_sync_005_knowledge_base_filter(self, servicenow_world: ServiceNowWorld) -> None:
        w = servicenow_world
        w.sim.reset()
        kb = sorted(a["kb"] for a in w.sim.oracle()["articles"])[0]
        assert succeeded(await w.start(filters=kb_filter([kb])))
        await w.assert_matches_source("with one knowledge base in scope", kbs_in_scope={kb})
        assert succeeded(await w.restart(filters={"sync": {"values": {}}}))
        await w.assert_matches_source("after the filter was cleared")

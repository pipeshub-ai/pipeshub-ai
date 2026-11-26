# pyright: ignore-file

"""
Jira Data Center on the simulator – changes after the first sync
================================================================

Each test changes the simulated Jira with the simulator's change engine (``clock/advance``: the same writes Jira makes,
with ``updated`` moved where Jira moves it and the audit log written for a deletion), runs the next sync and checks
every person reads exactly what the oracle says. Several kinds of change are made together, so one run covers them.

  TC-JDSIM-INC-001  Issues created, edited, transitioned and assigned
  TC-JDSIM-INC-002  Issues re-parented and moved to another project (the record is kept, its project changes)
  TC-JDSIM-INC-003  Issues deleted (the audit log names them); a full sync agrees
  TC-JDSIM-INC-004  Attachments added and removed (a removed file's record goes)
  TC-JDSIM-INC-005  Comments added, edited and deleted
  TC-JDSIM-INC-006  A project created and a project renamed
  TC-JDSIM-INC-007  A deleted issue and a new one in the same run; the new one is a new record
  TC-JDSIM-INC-008  The sync account's own run after a change of security level, grant and role actor made by the engine
"""

import pytest
from connectors.jira_data_center.sim_world import JiraDcWorld, succeeded


@pytest.mark.integration
@pytest.mark.jira_dc_sim
@pytest.mark.asyncio(loop_scope="session")
class TestJiraDcSimIncremental:

    async def _changed(self, w: JiraDcWorld, label: str, **counts: int) -> None:
        assert succeeded(await w.start())
        await w.assert_matches_source("after the first sync")
        result = w.sim.advance(**counts)
        assert not result["shortfall"], f"the engine could not make every change: {result['shortfall']}"
        assert succeeded(await w.sync())
        await w.assert_matches_source(f"after {label}")

    async def test_tc_jdsim_inc_001_create_edit_transition_assign(self, jira_dc_world: JiraDcWorld) -> None:
        await self._changed(jira_dc_world, "issues were created, edited, transitioned and assigned",
                            issue_create=3, issue_edit=3, transition=2, assign=2)

    async def test_tc_jdsim_inc_002_reparent_and_move(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        before = {r["externalRecordId"]: r["id"] for r in (await self._first(w))}
        w.sim.advance(reparent=2, move=2)
        assert succeeded(await w.sync())
        await w.assert_matches_source("after issues were re-parented and moved")
        after = {r["externalRecordId"]: r["id"] for r in await w.records()}
        kept = {ext for ext in before if not ext.startswith("attachment_")} & set(after)
        assert all(before[e] == after[e] for e in kept), "a moved or re-parented issue got a new record"

    async def _first(self, w: JiraDcWorld) -> list[dict]:
        assert succeeded(await w.start())
        await w.assert_matches_source("after the first sync")
        return await w.records()

    async def test_tc_jdsim_inc_003_delete(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        await self._changed(w, "issues were deleted", delete=3)
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after a full sync following the deletions")

    async def test_tc_jdsim_inc_004_attachments(self, jira_dc_world: JiraDcWorld) -> None:
        await self._changed(jira_dc_world, "attachments were added and removed", attachment_add=3, attachment_remove=3)

    async def test_tc_jdsim_inc_005_comments(self, jira_dc_world: JiraDcWorld) -> None:
        await self._changed(jira_dc_world, "comments were added, edited and deleted",
                            comment_add=3, comment_edit=2, comment_delete=2)

    async def test_tc_jdsim_inc_006_project_created_and_renamed(self, jira_dc_world: JiraDcWorld) -> None:
        await self._changed(jira_dc_world, "a project was created and another renamed", project_create=1,
                            project_rename=1)

    async def test_tc_jdsim_inc_007_delete_and_create_together(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        before = {r["externalRecordId"] for r in await self._first(w)}
        w.sim.advance(delete=2, issue_create=2)
        assert succeeded(await w.sync())
        await w.assert_matches_source("after two issues were deleted and two created")
        after = {r["externalRecordId"] for r in await w.records()}
        assert len(after - before) >= 2

    async def test_tc_jdsim_inc_008_engine_access_changes(self, jira_dc_world: JiraDcWorld) -> None:
        await self._changed(jira_dc_world, "grants, role actors, memberships and security levels changed",
                            grant_add=1, role_actor_add=2, group_member_add=3, security_set=2, user_deactivate=1)

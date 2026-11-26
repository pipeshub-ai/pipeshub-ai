# pyright: ignore-file

"""
Jira Cloud on the simulator: projects, deletion and full syncs
==============================================================

  TC-JCSIM-PROJ-001  A project renamed (its key and records stay)
  TC-JCSIM-PROJ-002  A project re-keyed (the issues keep their records; the old keys still resolve)
  TC-JCSIM-PROJ-003  A project trashed, then restored
  TC-JCSIM-PROJ-004  A project archived, then restored
  TC-JCSIM-PROJ-005  A project deleted for good
  TC-JCSIM-PROJ-006  A project created with issues, and an issue moved into it
  TC-JCSIM-PROJ-007  A project's permission scheme replaced by another
  TC-JCSIM-DEL-001   Issues, files and comments gone at the source while the connector is off: a full sync removes them
  TC-JCSIM-DEL-002   A full sync that could not read a project (the sync account lost it) keeps nothing it cannot prove
                     is still there outside what Jira stopped showing; it returns when the project does
  TC-JCSIM-DEL-003   A full sync on an unchanged site changes nothing
  TC-JCSIM-DEL-004   A deleted parent: its children keep their records
"""

import pytest
from connectors.jira.cloud_sim_world import (  # noqa: F401 - fixtures of the jira_cloud_sim suite
    JiraCloudWorld,
    jira_cloud_sim,
    jira_cloud_sim_people,
    jira_cloud_world,
    succeeded,
)


@pytest.mark.integration
@pytest.mark.jira_cloud_sim
@pytest.mark.asyncio(loop_scope="session")
class TestJiraCloudSimProjects:

    async def _first(self, w: JiraCloudWorld) -> str:
        assert succeeded(await w.start())
        await w.assert_matches_source("after the first sync")
        return w.big_project(3)

    async def test_tc_jcsim_proj_001_renamed(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        key = await self._first(w)
        before = {r["externalRecordId"]: r["id"] for r in await w.records()}
        w.sim.rest("PUT", f"/project/{key}", {"name": "A new name for the project"})
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after the project was renamed")
        assert {r["externalRecordId"]: r["id"] for r in await w.records()} == before

    async def test_tc_jcsim_proj_002_rekeyed(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        key = await self._first(w)
        w.sim.rest("PUT", f"/project/{key}", {"key": "RENM"})
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after the project was re-keyed")
        assert any(t["key"].startswith("RENM-") for t in w.sim.expected()["ticket_index"].values())

    async def test_tc_jcsim_proj_003_trashed_and_restored(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        key = await self._first(w)
        w.sim.rest("DELETE", f"/project/{key}")
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after the project went to the trash")
        w.sim.rest("POST", f"/project/{key}/restore")
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after the project was restored")

    async def test_tc_jcsim_proj_004_archived_and_restored(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        key = await self._first(w)
        w.sim.rest("POST", f"/project/{key}/archive")
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after the project was archived")
        w.sim.rest("POST", f"/project/{key}/restore")
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after the project was restored")

    async def test_tc_jcsim_proj_005_deleted_for_good(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        key = await self._first(w)
        w.sim.rest("DELETE", f"/project/{key}", enableUndo="false")
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after the project was deleted for good")

    async def test_tc_jcsim_proj_006_created_with_issues_and_a_move_into_it(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        key = await self._first(w)
        lead = w.sim.users()[0]["account_id"]
        w.sim.rest("POST", "/project", {"key": "FRSH", "name": "Fresh", "projectTypeKey": "software", "leadAccountId": lead})
        w.sim.rest("POST", "/issue", {"fields": {"project": {"key": "FRSH"}, "summary": "In the fresh project",
                                                "issuetype": {"name": "Task"}}}, ok=(400,))
        w.sim.advance(move=2)
        assert succeeded(await w.sync())
        await w.assert_matches_source("after a project was created and issues moved")
        assert key

    async def test_tc_jcsim_proj_007_permission_scheme_replaced(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        a, b = w.projects()[0], w.projects()[1]
        scheme_b = w.sim.rest("GET", f"/project/{b}/permissionscheme")["id"]
        w.sim.rest("PUT", f"/project/{a}/permissionscheme", {"id": scheme_b})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after a project took another project's permission scheme")


@pytest.mark.integration
@pytest.mark.jira_cloud_sim
@pytest.mark.asyncio(loop_scope="session")
class TestJiraCloudSimDeletion:

    async def test_tc_jcsim_del_001_gone_while_off(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        w.disable()
        await w._wait_idle()
        w.sim.advance(delete=3, attachment_remove=2, comment_delete=2)
        assert succeeded(await w.enable())
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after a full sync of a site that lost things while the connector was off")

    async def test_tc_jcsim_del_002_project_lost_and_back(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        key = w.big_project(3)
        w.mutate({"op": "project", "project": key, "sa_browse": False})
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after the sync account lost a project")
        w.mutate({"op": "project", "project": key, "sa_browse": True})
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after the sync account got the project back")

    async def test_tc_jcsim_del_003_full_sync_changes_nothing(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        before = {r["externalRecordId"]: r["id"] for r in await w.records()}
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after a full sync of an unchanged site")
        assert {r["externalRecordId"]: r["id"] for r in await w.records()} == before

    async def test_tc_jcsim_del_004_parent_deleted(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        index = w.sim.expected()["ticket_index"]
        parent = next(t["parent_id"] for t in index.values() if t["parent_id"] and str(t["parent_id"]) in index)
        w.sim.rest("DELETE", f"/issue/{index[str(parent)]['key']}", deleteSubtasks="false", ok=(400,))
        assert succeeded(await w.sync())
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after a parent issue was deleted")

# pyright: ignore-file

"""
Jira Cloud on the simulator: changes after the first sync
=========================================================

Each test changes the simulated Jira (the change engine `clock/advance`, or Jira's own write API as a person would),
runs the next sync and checks every person reads exactly what the oracle says.

  TC-JCSIM-INC-001  Issues created, edited, transitioned and assigned
  TC-JCSIM-INC-002  Issues re-parented and moved to another project (the record is kept, its project changes)
  TC-JCSIM-INC-003  Issues deleted (the audit log names them); a full sync agrees
  TC-JCSIM-INC-004  Attachments added and removed
  TC-JCSIM-INC-005  Comments added, edited and deleted
  TC-JCSIM-INC-006  Issue links added and removed
  TC-JCSIM-INC-007  The same kinds of change made through the write API: create, edit, transition, assign, comment, attach, delete
  TC-JCSIM-INC-008  A project created and a project renamed
  TC-JCSIM-INC-009  A deleted issue and a new one in the same run; the new one is a new record
  TC-JCSIM-INC-010  A restricted comment added, widened to everyone and removed
"""

import pytest
from connectors.jira.cloud_sim_world import (  # noqa: F401 - fixtures of the jira_cloud_sim suite
    CREATOR_EMAIL,
    SYNC_ACCOUNT,
    JiraCloudWorld,
    jira_cloud_sim,
    jira_cloud_sim_people,
    jira_cloud_world,
    succeeded,
)


@pytest.mark.integration
@pytest.mark.jira_cloud_sim
@pytest.mark.asyncio(loop_scope="session")
class TestJiraCloudSimIncremental:

    async def _first(self, w: JiraCloudWorld) -> list[dict]:
        assert succeeded(await w.start())
        await w.assert_matches_source("after the first sync")
        return await w.records()

    async def _changed(self, w: JiraCloudWorld, label: str, **counts: int) -> None:
        await self._first(w)
        result = w.sim.advance(**counts)
        assert not result["shortfall"], f"the engine could not make every change: {result['shortfall']}"
        assert succeeded(await w.sync())
        await w.assert_matches_source(f"after {label}")

    async def test_tc_jcsim_inc_001_create_edit_transition_assign(self, jira_cloud_world: JiraCloudWorld) -> None:
        await self._changed(jira_cloud_world, "issues were created, edited, transitioned and assigned",
                            issue_create=3, issue_edit=3, transition=2, assign=2)

    async def test_tc_jcsim_inc_002_reparent_and_move(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        before = {r["externalRecordId"]: r["id"] for r in await self._first(w)}
        w.sim.advance(reparent=2, move=2)
        assert succeeded(await w.sync())
        await w.assert_matches_source("after issues were re-parented and moved")
        after = {r["externalRecordId"]: r["id"] for r in await w.records()}
        kept = {e for e in before if not e.startswith(("attachment_", "comment_"))} & set(after)
        assert all(before[e] == after[e] for e in kept), "a moved or re-parented issue got a new record"

    async def test_tc_jcsim_inc_003_delete(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        await self._changed(w, "issues were deleted", delete=3)
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after a full sync")

    async def test_tc_jcsim_inc_004_attachments(self, jira_cloud_world: JiraCloudWorld) -> None:
        await self._changed(jira_cloud_world, "attachments were added and removed", attachment_add=3, attachment_remove=2)

    async def test_tc_jcsim_inc_005_comments(self, jira_cloud_world: JiraCloudWorld) -> None:
        await self._changed(jira_cloud_world, "comments were added, edited and deleted",
                            comment_add=3, comment_edit=2, comment_delete=2)

    async def test_tc_jcsim_inc_006_links(self, jira_cloud_world: JiraCloudWorld) -> None:
        await self._changed(jira_cloud_world, "links were added and removed", link_add=3, link_remove=2)

    async def test_tc_jcsim_inc_007_through_the_write_api(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        await self._first(w)
        key = w.big_project()
        issues = w.issue_keys(key)
        created = w.sim.rest("POST", "/issue", {"fields": {"project": {"key": key}, "summary": "Made through the API",
                                                          "issuetype": {"name": "Task"}}})
        w.sim.rest("PUT", f"/issue/{issues[0]}", {"fields": {"summary": "Edited through the API"}})
        transitions = w.sim.rest("GET", f"/issue/{issues[1]}/transitions")["transitions"]
        w.sim.rest("POST", f"/issue/{issues[1]}/transitions", {"transition": {"id": transitions[0]["id"]}})
        person = w.sim.users()[0]
        w.sim.rest("PUT", f"/issue/{issues[2]}/assignee", {"accountId": person["account_id"]})
        w.sim.rest("POST", f"/issue/{issues[3]}/comment", {"body": {"type": "doc", "version": 1, "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "A comment from the test"}]}]}})
        w.sim.upload(issues[4], "from-the-test.txt", b"the file the test attached\n")
        w.sim.rest("DELETE", f"/issue/{issues[5]}")
        assert succeeded(await w.sync())
        await w.assert_matches_source("after changes made through the write API")
        names = {r["recordName"] for r in await w.records()}
        assert any(n.endswith("Made through the API") for n in names) and "from-the-test.txt" in names, sorted(names)
        assert created["key"] in {t["key"] for t in w.sim.expected()["ticket_index"].values()}

    async def test_tc_jcsim_inc_008_project_created_and_renamed(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        await self._first(w)
        lead = w.sim.users()[0]["account_id"]
        w.sim.rest("POST", "/project", {"key": "NEWP", "name": "A new project", "projectTypeKey": "software",
                                        "leadAccountId": lead})
        w.sim.rest("POST", "/issue", {"fields": {"project": {"key": "NEWP"}, "summary": "First issue of NEWP",
                                                "issuetype": {"name": "Task"}}}, ok=(400,))
        w.sim.rest("PUT", f"/project/{w.projects()[0]}", {"name": "Renamed by the test"})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after a project was created and another renamed")

    async def test_tc_jcsim_inc_009_delete_and_create_together(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        before = {r["externalRecordId"]: r["id"] for r in await self._first(w)}
        w.sim.advance(delete=1, issue_create=1)
        assert succeeded(await w.sync())
        await w.assert_matches_source("after an issue was deleted and one created")
        after = {r["externalRecordId"]: r["id"] for r in await w.records()}
        assert set(after) - set(before), "the new issue has no record"

    async def test_tc_jcsim_inc_010_restricted_comment(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        await self._first(w)
        key = w.issue_keys(w.projects()[0])[0]
        doc = {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Only for admins"}]}]}
        c = w.sim.rest("POST", f"/issue/{key}/comment", {"body": doc, "visibility": {"type": "role", "value": "Administrators"}})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after a restricted comment was added")
        w.sim.rest("PUT", f"/issue/{key}/comment/{c['id']}", {"body": doc, "visibility": None})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the comment was opened to everyone")
        w.sim.rest("POST", f"/issue/{key}/comment", {"body": doc, "visibility": {"type": "role", "value": "Administrators"}})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after another restricted comment")

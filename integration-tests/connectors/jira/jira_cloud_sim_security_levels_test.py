# pyright: ignore-file

"""
Jira Cloud on the simulator: issue security levels
==================================================

  TC-JCSIM-LVL-001  A level renamed, with its holder changed
  TC-JCSIM-LVL-002  A level removed: its issues move to another level or to none
  TC-JCSIM-LVL-003  The project's scheme swapped for another with its own holders
  TC-JCSIM-LVL-004  A level on an issue with sub-tasks and on an epic with children, then cleared
  TC-JCSIM-LVL-005  An issue created at a level while the levels cannot be read: nobody reads it who Jira does not show it to
  TC-JCSIM-LVL-006  Every holder kind of a level at the first run with the levels refused, then read
  TC-JCSIM-LVL-008  The project's scheme removed (Jira answers 404 for it): the issues are open to the project again
  TC-JCSIM-LVL-007  A level's holders read through a group, the group's members changed while the group list is refused
"""

from typing import Any

import pytest
from connectors.jira.cloud_sim_access import API, PAGE_EMPTY, PERSISTENT, actual, oracle, refused, rule, within_bounds
from connectors.jira.cloud_sim_world import (  # noqa: F401 - fixtures of the jira_cloud_sim suite
    CREATOR_EMAIL,
    JiraCloudWorld,
    jira_cloud_sim,
    jira_cloud_sim_people,
    jira_cloud_world,
    succeeded,
)


async def leveled(w: JiraCloudWorld) -> dict[str, Any]:
    """A project with a level scheme, an issue and two people who read it, as the site is built."""
    w.sim.reset()
    project, levels = w.leveled_project()
    scheme = w.sim.rest("GET", f"/project/{project}/issuesecuritylevelscheme")
    issues = w.issue_keys(project)
    tid = next(t for t, v in w.sim.expected()["ticket_index"].items() if v["key"] == issues[0])
    inside = [e for e in sorted(w.readers_now()[tid]) if e != CREATOR_EMAIL and e in w.emails()]
    assert len(inside) >= 2, "an issue of the leveled project is read by fewer than two people"
    return {"project": project, "levels": levels, "scheme": scheme, "issue": issues[0], "issues": issues,
            "who": inside[0], "keep": inside[1]}


def holders(c: dict[str, Any], *people: str, present: bool = True) -> list[dict[str, Any]]:
    return [{"op": "level_member", "project": c["project"], "level": c["levels"][0]["name"], "holder": "user",
             "parameter": p, "present": present} for p in people]


@pytest.mark.integration
@pytest.mark.jira_cloud_sim
@pytest.mark.asyncio(loop_scope="session")
class TestJiraCloudSimLevels:

    async def _begin(self, w: JiraCloudWorld) -> dict[str, Any]:
        c = await leveled(w)
        assert succeeded(await w.start(reset=False, prepare=[
            {"op": "security", "issue": c["issue"], "level": c["levels"][0]["name"]}, *holders(c, c["who"], c["keep"])]))
        await w.assert_matches_source("after the first sync")
        return c

    async def test_tc_jcsim_lvl_001_level_renamed(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        c = await self._begin(w)
        level = c["levels"][0]
        w.sim.rest("PUT", f"/issuesecurityschemes/{c['scheme']['id']}/level/{level['id']}", {"name": "Renamed level"})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the level was renamed")
        w.mutate({"op": "level_member", "project": c["project"], "level": "Renamed level", "holder": "user",
                  "parameter": c["who"], "present": False})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after a holder left the renamed level")
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after a full run")

    @pytest.mark.parametrize("move_to", ["another", "none"])
    async def test_tc_jcsim_lvl_002_level_removed(self, jira_cloud_world: JiraCloudWorld, move_to: str) -> None:
        w = jira_cloud_world
        c = await self._begin(w)
        scheme_id = c["scheme"]["id"]
        level = c["levels"][0]
        if move_to == "another" and len(c["levels"]) < 2:
            pytest.fail("the project's scheme has a single level")
        extra = {"replaceWith": c["levels"][1]["id"]} if move_to == "another" else {}
        w.sim.rest("DELETE", f"/issuesecurityschemes/{scheme_id}/level/{level['id']}", **extra)
        assert succeeded(await w.sync())
        await w.assert_matches_source(f"after the level was removed, its issues moved to {move_to}")
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after a full run")

    async def test_tc_jcsim_lvl_003_scheme_swapped(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        c = await self._begin(w)
        keep_id = w.sim.access(c["keep"])["account_id"]
        sync_id = w.sim.rest("GET", "/myself")["accountId"]
        made = w.sim.rest("POST", "/issuesecurityschemes", {"name": "Swapped", "levels": [
            {"name": "Only keep", "isDefault": True, "members": [{"type": "user", "parameter": keep_id},
                                                                 {"type": "user", "parameter": sync_id}]}]})
        new = w.sim.rest("GET", f"/issuesecurityschemes/{made['id']}")
        project_id = w.sim.rest("GET", f"/project/{c['project']}")["id"]
        w.sim.rest("PUT", "/issuesecurityschemes/project", {
            "projectId": project_id, "schemeId": made["id"],
            "oldToNewSecurityLevelMappings": [{"oldLevelId": lv["id"], "newLevelId": new["levels"][0]["id"]}
                                              for lv in c["levels"]]})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the project took another level scheme")
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after a full run")

    async def test_tc_jcsim_lvl_004_subtasks_and_children(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        w.sim.reset()
        project, levels = w.leveled_project()
        parents = []
        for key in w.issue_keys(project):
            kids = w.sim.rest("POST", "/search/jql", {"jql": f'parent = "{key}"', "fields": ["id"], "maxResults": 5})["issues"]
            if kids:
                parents.append((key, len(kids)))
        assert parents, f"no issue of {project} has children"
        assert succeeded(await w.start(reset=False))
        level = levels[0]["name"]
        for key, _n in parents[:3]:
            w.mutate({"op": "security", "issue": key, "level": level})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after parents with children were put under a level")
        w.mutate({"op": "security", "issue": parents[0][0], "level": None})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the level of one parent was cleared")
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after a full run")

    @pytest.mark.parametrize("kind", ["403", "empty"])
    async def test_tc_jcsim_lvl_005_new_issue_while_levels_unreadable(self, jira_cloud_world: JiraCloudWorld, kind: str) -> None:
        w = jira_cloud_world
        c = await self._begin(w)
        before = oracle(w)
        created = w.sim.rest("POST", "/issue", {"fields": {
            "project": {"key": c["project"]}, "summary": "A new secret", "issuetype": {"name": "Task"},
            "security": {"id": c["levels"][0]["id"]}}})
        assert created["key"]
        after = oracle(w)
        w.faults(rule(kind, API + r"/(project/[^/]+/issuesecuritylevelscheme|issuesecurityschemes/level/member)$",
                      {"levels": []}, max_times=PERSISTENT))
        outcome = await w.sync(full=True)
        w.clear_faults()
        within_bounds(before, after, await actual(w), f"new leveled issue, levels refused ({kind}, run {outcome})",
                      losses_ok=kind in ("403", "empty"))
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after the levels could be read")

    @pytest.mark.parametrize("holder", ["group", "projectRole", "projectLead", "reporter", "assignee"])
    async def test_tc_jcsim_lvl_006_holder_kinds_levels_refused_first(self, jira_cloud_world: JiraCloudWorld, holder: str) -> None:
        w = jira_cloud_world
        c = await leveled(w)
        level, issue = c["levels"][0]["name"], c["issue"]
        prep: list[dict[str, Any]] = [{"op": "security", "issue": issue, "level": level}]
        if holder == "group":
            prep += [{"op": "group_create", "group": "lvl-g", "members": [c["who"]]},
                     {"op": "level_member", "project": c["project"], "level": level, "holder": "group", "parameter": "lvl-g"}]
        elif holder == "projectRole":
            prep += [{"op": "role_actor", "project": c["project"], "role": "Developers", "kind": "user", "who": c["who"]},
                     {"op": "level_member", "project": c["project"], "level": level, "holder": "projectRole",
                      "parameter": "Developers"}]
        elif holder == "projectLead":
            prep += [{"op": "project", "project": c["project"], "lead": c["who"]},
                     {"op": "level_member", "project": c["project"], "level": level, "holder": "projectLead"}]
        else:
            prep += [{"op": "issue", "issue": issue, holder: c["who"]},
                     {"op": "level_member", "project": c["project"], "level": level, "holder": holder}]
        await w.start(reset=False, prepare=prep, enable=False)
        await refused(w, [], rule("403", API + r"/issuesecurityschemes/level/member$", None, max_times=PERSISTENT),
                      f"{holder} holder, level members refused at the first run", full=True,
                      before={e: set() for e in w.people}, first=True)

    async def test_tc_jcsim_lvl_007_group_holder_group_read_refused(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        c = await leveled(w)
        level = c["levels"][0]["name"]
        assert succeeded(await w.start(reset=False, prepare=[
            {"op": "security", "issue": c["issue"], "level": level},
            {"op": "group_create", "group": "lvl-g", "members": [c["who"], c["keep"]]},
            {"op": "level_member", "project": c["project"], "level": level, "holder": "group", "parameter": "lvl-g"}]))
        await w.assert_matches_source("after the first sync")
        for i, kind in enumerate(("403", "500", "malformed")):
            await refused(w, [{"op": "group_member", "group": "lvl-g", "user": c["who"], "member": i % 2 == 1}],
                          rule(kind, API + r"/group/(bulk|member)$", PAGE_EMPTY, max_times=PERSISTENT),
                          f"level group members, group read {kind}", full=i % 2 == 0)

    async def test_tc_jcsim_lvl_008_scheme_removed(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        c = await self._begin(w)
        project_id = w.sim.rest("GET", f"/project/{c['project']}")["id"]
        w.sim.rest("PUT", "/issuesecurityschemes/project", {"projectId": project_id, "schemeId": None})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the project's level scheme was removed")
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after a full run")

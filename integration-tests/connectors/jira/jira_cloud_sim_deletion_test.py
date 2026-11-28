# pyright: ignore-file

"""
Jira Cloud on the simulator: every way an issue's deletion is found, and what a failed read leaves
==================================================================================================

  TC-JCSIM-DELX-001  Deleted issues (with sub-tasks, files, comments) are removed by the audit log on an incremental run
  TC-JCSIM-DELX-002  More than one page of audit records; issues made and deleted between two runs were never synced
  TC-JCSIM-DELX-003  The audit log refused for lack of permission (403): nothing is removed, the next clean run removes
  TC-JCSIM-DELX-004  The audit log refused as on a Free plan: deletions are found by listing, and the notice is sent once
  TC-JCSIM-DELX-005  Free plan, the issue listing fails (token repeated, dropped, expired, 5xx, cut body): nothing removed
  TC-JCSIM-DELX-006  Free plan, an issue moved to another project is not removed as gone
  TC-JCSIM-DELX-007  The read that confirms a deletion fails (GET issue): the record stays, the next run removes it
  TC-JCSIM-DELX-008  The audit log fails on its second page (every kind): the window is read again next run
  TC-JCSIM-DELX-009  A deleted issue's parent keeps its other children; a deleted epic's children stay
"""

import pytest
from connectors.jira.cloud_sim_sync import (
    end_faults,
    AUDIT_RX,
    ISSUE_ONE_RX,
    PERSISTENT,
    SEARCH_RX,
    clean,
    free_plan_audit,
    ids_of,
    make_issues,
    run_case,
)
from connectors.jira.cloud_sim_world import (  # noqa: F401 - fixtures of the jira_cloud_sim suite
    JiraCloudWorld,
    drop,
    fault,
    jira_cloud_sim,
    jira_cloud_sim_people,
    jira_cloud_world,
    malformed,
    page_token,
    succeeded,
)


def _delete(w: JiraCloudWorld, count: int) -> set[str]:
    """Delete ``count`` issues at the source; returns every external id that goes with them."""
    held = ids_of(w)
    w.sim.advance(delete=count)  # a deleted issue takes its sub-tasks with it
    return held - ids_of(w)


@pytest.mark.integration
@pytest.mark.jira_cloud_sim
@pytest.mark.asyncio(loop_scope="session")
class TestJiraCloudSimDeletionPaths:

    async def test_tc_jcsim_delx_001_audit_log(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        _delete(w, 4)
        await clean(w, "after issues were deleted at the source")
        assert w.sim.paths_called().get("GET /rest/api/3/auditing/record", 0) >= 1, "the audit log was never read"

    async def test_tc_jcsim_delx_002_two_audit_pages_and_never_synced_issues(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        project = w.big_project()
        made = make_issues(w, project, 505, "Made and deleted")
        for key in made:
            w.sim.rest("DELETE", f"/issue/{key}")
        gone = _delete(w, 2)
        w.sim.call("POST", "/_sim/calls/reset")
        await clean(w, "after 505 issues were made and deleted between two runs")
        assert w.sim.paths_called().get("GET /rest/api/3/auditing/record", 0) >= 2, "the audit log was read in one page"
        assert gone

    async def test_tc_jcsim_delx_003_audit_log_without_permission(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        gone = _delete(w, 2)
        outcome, _ = await run_case(w, fault(AUDIT_RX, 403, max_times=PERSISTENT), "audit log 403", gone=gone)
        assert outcome is not None

    async def test_tc_jcsim_delx_004_free_plan_lists_ids(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        w.faults(free_plan_audit(max_times=PERSISTENT))
        _delete(w, 3)
        assert succeeded(await w.sync()), "a Free-plan site's run did not succeed"
        await w.assert_matches_source("deletions found by comparing ids")
        _delete(w, 2)
        assert succeeded(await w.sync())  # the notice marker is read: it is sent once
        await w.assert_matches_source("deletions found by comparing ids, second run")
        end_faults(w)
        await clean(w, "after the audit log answered again")

    @pytest.mark.parametrize("mode", ["repeat", "drop", "expire", "short"])
    async def test_tc_jcsim_delx_005_free_plan_listing_fails_paged(self, jira_cloud_world: JiraCloudWorld, mode: str) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        w.sim.call("POST", "/_sim/calls/reset")
        assert succeeded(await w.sync())
        searches = w.sim.paths_called().get("POST /rest/api/3/search/jql", 0)
        gone = _delete(w, 2)
        # the run's own searches come first; the id listings after them fail
        await run_case(w, [free_plan_audit(max_times=PERSISTENT), page_token(SEARCH_RX, mode, after_n=searches)],
                       f"free plan, listing {mode}", gone=gone, fires=False)

    @pytest.mark.parametrize("kind", ["500", "drop", "malformed", "empty"])
    async def test_tc_jcsim_delx_005b_free_plan_listing_fails_whole(self, jira_cloud_world: JiraCloudWorld, kind: str) -> None:
        from connectors.jira.cloud_sim_sync import shaped

        w = jira_cloud_world
        assert succeeded(await w.start())
        w.sim.call("POST", "/_sim/calls/reset")
        assert succeeded(await w.sync())
        searches = w.sim.paths_called().get("POST /rest/api/3/search/jql", 0)
        gone = _delete(w, 2)
        later = {"after_n": searches, "max_times": PERSISTENT}
        rule = {"500": lambda: fault(SEARCH_RX, 500, **later), "drop": lambda: drop(SEARCH_RX, **later),
                "malformed": lambda: malformed(SEARCH_RX, **later),
                "empty": lambda: shaped(SEARCH_RX, {"path": "$", "value": {}}, **later)}[kind]()
        await run_case(w, [free_plan_audit(max_times=PERSISTENT), rule], f"free plan, listing {kind}", gone=gone, fires=False)

    async def test_tc_jcsim_delx_006_free_plan_moved_issue_is_not_gone(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        w.faults(free_plan_audit(max_times=PERSISTENT))
        w.sim.advance(move=3)
        assert succeeded(await w.sync())
        end_faults(w)
        await w.assert_matches_source("after issues moved to another project, on a Free plan")

    async def test_tc_jcsim_delx_007_confirming_read_fails(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        gone = _delete(w, 2)
        for name, rule in (("503", fault(ISSUE_ONE_RX, 503, max_times=PERSISTENT)),
                           ("drop", drop(ISSUE_ONE_RX, max_times=PERSISTENT))):
            before = await w.holdings()
            w.faults(rule)
            await w.sync()
            assert end_faults(w)
            await w.assert_holdings_kept(before, f"a run that could not confirm a deletion ({name})", allow_gains=True)
        assert gone
        await clean(w, "after Jira confirmed the deletions again")

    @pytest.mark.parametrize("kind", ["502"])
    async def test_tc_jcsim_delx_008_audit_second_page_fails(self, jira_cloud_world: JiraCloudWorld, kind: str) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        project = w.big_project()
        for key in make_issues(w, project, 505, "Made and deleted"):
            w.sim.rest("DELETE", f"/issue/{key}")
        gone = _delete(w, 2)
        rule = {"502": lambda: fault(AUDIT_RX, 502, after_n=1, max_times=PERSISTENT),
                "drop": lambda: drop(AUDIT_RX, after_n=1, max_times=PERSISTENT),
                "malformed": lambda: malformed(AUDIT_RX, after_n=1, max_times=PERSISTENT)}[kind]()
        await run_case(w, rule, f"audit log page two {kind}", gone=gone)

    async def test_tc_jcsim_delx_009_children_of_a_deleted_parent(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        index = w.sim.expected()["ticket_index"]
        parents = {str(t["parent_id"]): [i for i, c in index.items() if str(c["parent_id"]) == str(t["parent_id"])]
                   for t in index.values() if t["parent_id"] and str(t["parent_id"]) in index}
        parent, children = next((p, c) for p, c in parents.items() if len(c) >= 1)
        w.sim.rest("DELETE", f"/issue/{index[parent]['key']}", deleteSubtasks="false", ok=(400,))
        await clean(w, "after a parent was deleted (incremental)")
        assert children, "the parent had no child"

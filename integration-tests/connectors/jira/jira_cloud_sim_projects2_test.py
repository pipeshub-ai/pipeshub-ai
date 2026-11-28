# pyright: ignore-file

"""
Jira Cloud on the simulator: projects leaving, entering and being selected
==========================================================================

  TC-JCSIM-PRJ2-001  More than one page of projects (60 more), with no filter, an "in" filter and a "not in" filter
  TC-JCSIM-PRJ2-002  A project trashed, archived, deleted, hidden from the account: removed by an incremental run; back
                     when restored
  TC-JCSIM-PRJ2-003  The check that a missing project is gone fails (GET project 5xx, dropped, malformed): kept
  TC-JCSIM-PRJ2-004  The account sees no project at all: nothing is removed
  TC-JCSIM-PRJ2-005  A selected project re-keyed (follows under the new key), deleted, archived, unknown; the lookup refused
  TC-JCSIM-PRJ2-006  A project taken in and out of the filter, "in" and "not in"
  TC-JCSIM-PRJ2-007  The project listing fails first page and mid-listing (every kind)
  TC-JCSIM-PRJ2-008  A project removed while the connector is off
"""

import pytest
from connectors.jira.cloud_sim_sync import (
    end_faults,
    PERSISTENT,
    PROJECT_ONE_RX,
    PROJECT_SEARCH_RX,
    clean,
    ids_of,
    make_issues,
    make_projects,
    run_case,
    shaped,
)
from connectors.jira.cloud_sim_world import (  # noqa: F401 - fixtures of the jira_cloud_sim suite
    JiraCloudWorld,
    drop,
    fault,
    jira_cloud_sim,
    jira_cloud_sim_people,
    jira_cloud_world,
    malformed,
    project_filter,
    succeeded,
)


@pytest.mark.integration
@pytest.mark.jira_cloud_sim
@pytest.mark.asyncio(loop_scope="session")
class TestJiraCloudSimProjectPaths:

    @pytest.mark.parametrize("mode", ["none", "in", "not_in"])
    async def test_tc_jcsim_prj2_001_many_projects(self, jira_cloud_world: JiraCloudWorld, mode: str) -> None:
        w = jira_cloud_world
        w.sim.reset()
        extra = make_projects(w, 60)
        make_issues(w, extra[0], 2)
        keys = w.projects()
        filters = {"none": None, "in": project_filter("in", [*extra[:3], keys[0]]),
                   "not_in": project_filter("not_in", extra[:30])}[mode]
        assert succeeded(await w.start(filters=filters, reset=False))
        await w.assert_matches_source(f"with {len(extra) + 12} projects, filter {mode}")
        pages = w.sim.paths_called().get("GET /rest/api/3/project/search", 0)
        assert pages >= (2 if mode != "in" else 1), f"the project search was read in {pages} page(s)"

    @pytest.mark.parametrize("how", [
        "trash", "archive", "delete",
        pytest.param("hide", marks=pytest.mark.xfail(strict=True, reason=(
            "the sync account is a Jira admin: it keeps seeing the project in the project listing (GET /project/search lists the "
            "projects a user with Administer Jira sees) but no longer sees its issues, and the connector, which removes a project "
            "only when it is not found, keeps the stored issues of it; nobody gains, the records go stale"))),
    ])
    async def test_tc_jcsim_prj2_002_project_leaves_and_returns(self, jira_cloud_world: JiraCloudWorld, how: str) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        key = w.big_project(3)
        gone = ids_of(w, project=key)
        if how == "trash":
            w.sim.rest("DELETE", f"/project/{key}")
        elif how == "archive":
            w.sim.rest("POST", f"/project/{key}/archive")
        elif how == "delete":
            w.sim.rest("DELETE", f"/project/{key}", enableUndo="false")
        else:
            w.mutate({"op": "project", "project": key, "sa_browse": False})
        assert succeeded(await w.sync())  # incremental: the project check, not a full sync, removes it
        await w.assert_matches_source(f"after the project was {how}d, by an incremental run")
        records = {r["externalRecordId"] for r in await w.records()}
        assert not (gone & records), f"records of the {how}d project stayed: {sorted(gone & records)[:5]}"
        if how == "delete":
            return
        if how == "hide":
            w.mutate({"op": "project", "project": key, "sa_browse": True})
        else:
            w.sim.rest("POST", f"/project/{key}/restore")
        await clean(w, f"after the {how}d project came back")
        assert gone <= {r["externalRecordId"] for r in await w.records()}, "the restored project's records did not return"

    @pytest.mark.parametrize("kind", ["503", "drop", "malformed", "empty"])
    async def test_tc_jcsim_prj2_003_project_check_fails(self, jira_cloud_world: JiraCloudWorld, kind: str) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        key = w.big_project(3)
        gone = ids_of(w, project=key)
        w.sim.rest("DELETE", f"/project/{key}")
        rule = {"503": lambda: fault(PROJECT_ONE_RX, 503, max_times=PERSISTENT),
                "drop": lambda: drop(PROJECT_ONE_RX, max_times=PERSISTENT),
                "malformed": lambda: malformed(PROJECT_ONE_RX, max_times=PERSISTENT),
                "empty": lambda: shaped(PROJECT_ONE_RX, {"path": "$", "value": []}, max_times=PERSISTENT)}[kind]()
        before = await w.holdings()
        w.faults(rule)
        await w.sync()
        end_faults(w)
        after = await w.holdings()
        # SAFETY: a project Jira did not say was gone is kept with its records
        assert (gone & before["records"]) <= after["records"], \
            f"{kind}: the records of a project Jira did not confirm gone were removed"
        await clean(w, f"after the project check answered again ({kind})")

    async def test_tc_jcsim_prj2_004_account_sees_no_project(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        before = await w.holdings()
        keys = w.projects()
        for key in keys:
            w.mutate({"op": "project", "project": key, "sa_browse": False})
        await w.sync()
        after = await w.holdings()
        # SAFETY: no record is removed; the people's access may be withdrawn (fails closed), never widened
        assert before["records"] <= after["records"], "records were removed when the account saw no project"
        assert all(after[who] <= before[who] for who in before if who != "records"), "someone gained access"
        for key in keys:
            w.mutate({"op": "project", "project": key, "sa_browse": True})
        await clean(w, "after the account got every project back")

    async def test_tc_jcsim_prj2_005_selected_project_changes(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        w.sim.reset()
        a, b, c, d = w.projects()[:4]
        assert succeeded(await w.start(filters=project_filter("in", [a, b, c, d, "NOSUCH"]), reset=False))
        await w.assert_matches_source("selected keys with one unknown")
        w.sim.rest("PUT", f"/project/{a}", {"key": "RKEY"})            # re-keyed: followed under the new key
        w.sim.rest("DELETE", f"/project/{b}", enableUndo="false")      # deleted
        w.sim.rest("POST", f"/project/{c}/archive")                    # archived
        w.scope.projects_in = (w.scope.projects_in or set()) | {"RKEY"}  # the connector follows a re-keyed project
        await clean(w, "after a selected project was re-keyed, one deleted and one archived")
        # the lookup of a former key is refused: the run keeps what it holds and the next one equals Jira
        before = await w.holdings()
        w.faults(fault(PROJECT_ONE_RX, 503, max_times=PERSISTENT))
        await w.sync()
        end_faults(w)
        await w.assert_holdings_kept(before, "a run that could not look up the selected projects", allow_gains=True)
        await clean(w, "after the lookups answered again")

    @pytest.mark.parametrize("operator", ["in", "not_in"])
    async def test_tc_jcsim_prj2_006_in_and_out_of_the_filter(self, jira_cloud_world: JiraCloudWorld, operator: str) -> None:
        w = jira_cloud_world
        w.sim.reset()
        a, b, c = w.projects()[:3]
        assert succeeded(await w.start(filters=project_filter(operator, [a]), reset=False))
        await w.assert_matches_source(f"project filter {operator} {a}")
        assert succeeded(await w.restart(project_filter(operator, [a, b])))
        await w.assert_matches_source(f"project filter {operator} {a},{b}")
        assert succeeded(await w.restart(project_filter(operator, [c])))
        await w.assert_matches_source(f"project filter {operator} {c}")
        assert succeeded(await w.restart({"sync": {"values": {}}}))
        await w.assert_matches_source("filter cleared")

    @pytest.mark.parametrize("kind", ["401", "403", "404", "429", "500", "drop", "malformed", "empty"])
    @pytest.mark.parametrize("where", ["first", "later"])
    async def test_tc_jcsim_prj2_007_project_listing_fails(self, jira_cloud_world: JiraCloudWorld, kind: str, where: str) -> None:
        w = jira_cloud_world
        w.sim.reset()
        make_projects(w, 60)
        assert succeeded(await w.start(reset=False))
        trig = {"max_times": PERSISTENT, **({"after_n": 1} if where == "later" else {})}
        rule = {"401": lambda: fault(PROJECT_SEARCH_RX, 401, **trig), "403": lambda: fault(PROJECT_SEARCH_RX, 403, **trig),
                "404": lambda: fault(PROJECT_SEARCH_RX, 404, **trig), "429": lambda: fault(PROJECT_SEARCH_RX, 429, **trig),
                "500": lambda: fault(PROJECT_SEARCH_RX, 500, **trig), "drop": lambda: drop(PROJECT_SEARCH_RX, **trig),
                "malformed": lambda: malformed(PROJECT_SEARCH_RX, **trig),
                "empty": lambda: shaped(PROJECT_SEARCH_RX, {"path": "$", "value": {"values": []}}, **trig)}[kind]()
        w.sim.advance(issue_edit=2)
        await run_case(w, rule, f"project search {kind} {where}")

    async def test_tc_jcsim_prj2_008_removed_while_off(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        key = w.big_project(3)
        gone = ids_of(w, project=key)
        w.disable()
        await w._wait_idle()
        w.sim.rest("DELETE", f"/project/{key}")
        assert succeeded(await w.enable())
        await w.assert_matches_source("after a project went to the trash while the connector was off")
        assert not (gone & {r["externalRecordId"] for r in await w.records()})

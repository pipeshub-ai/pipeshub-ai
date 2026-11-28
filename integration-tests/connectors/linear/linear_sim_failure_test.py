# pyright: ignore-file

"""
Linear on the simulator: the source fails
=========================================

Every GraphQL operation a sync sends (SYNC_OPERATIONS in linear_sim_sync_test.py) is failed, a transient and a lasting way.
A transient fault is one answer (503, or 429 RATELIMITED, which the connector waits out); a lasting one is a 403 that the
connector must not retry, or a 503 that outlives its retries. Faults name the operation (the simulator's ``op-faults``: all
GraphQL requests are POST /graphql, so the core's path rules cannot tell them apart). SAFETY, never under xfail: the fault
fired; no record the source still has was lost; nobody reads what Linear does not show them; the next clean sync equals
the source.

  TC-LNSIM-FAIL-001  One 503 on every operation a sync sends: retried, the run succeeds and equals the source [I]
  TC-LNSIM-FAIL-002  One 429 RATELIMITED on the listings: waited out, the run succeeds and equals the source [I]
  TC-LNSIM-FAIL-003  A lasting 403 on each operation in turn (a change waiting at the source for that operation to read):
                     nothing the source still has is lost, nobody gains, the next clean sync equals the source [I]
  TC-LNSIM-FAIL-004  OUTCOME: a run whose issue listing was refused is not reported as succeeded [I] (strict xfail)
  TC-LNSIM-FAIL-005  A 503 that outlives the retries (teams, issues, projects, the id listings): same safety [I]
  TC-LNSIM-FAIL-006  Broken cursors (more pages and no cursor, a repeated cursor, a refused cursor) on the issue listing:
                     nothing lost, next clean sync equals the source [H, I]
  TC-LNSIM-FAIL-007  A dropped connection mid-sync: retried, the run succeeds and equals the source [I]
  TC-LNSIM-FAIL-008  The connection test: refused (403) the connector cannot be turned on and stores nothing; one 503 is
                     retried and it can [A, I]
  TC-LNSIM-FAIL-009  A 403 on the second page of the issue listing: nothing lost, next clean sync equals the source [H, I]
  TC-LNSIM-FAIL-010  The file host failing (503, then 403): streaming a file fails loudly, the record stays, and streams
                     again once the host answers [J, I]
  TC-LNSIM-FAIL-011  The nested member listing of a team with more than ten members failing: nobody is granted from a
                     partial list [E, I]
"""

import pytest
from connectors.linear.linear_sim_sync_test import SYNC_OPERATIONS
from connectors.linear.sim_instances import many, small, workspace
from connectors.linear.sim_world import REFUSED, LinearWorld, op_fault, succeeded

LISTINGS = ["users", "teams", "issues", "Projects", "issueIds"]
# What waiting at the source makes each operation read (applied before the faulted full sync).
CHANGES = {
    "viewer": [{"op": "edit_issue", "issue": "eng-2", "title": "viewer"}],
    "users": [{"op": "add_user", "key": "newbie", "name": "Newbie Nu"}, {"op": "add_member", "team": "OPS", "user": "newbie"}],
    "teams": [{"op": "add_team", "key": "NT", "name": "Fresh team", "members": ["sync", "alice"]}],
    "issues": [{"op": "add_issue", "team": "OPS", "title": "waiting for issues", "creator": "bob"}],
    "issue": [{"op": "add_issue", "team": "ENG", "title": "child waiting", "parent": "eng-2", "creator": "bob"}],
    "Attachments": [{"op": "add_attachment", "issue": "eng-2", "title": "waiting", "url": "https://x.example/w"}],
    "Documents": [{"op": "add_document", "issue": "eng-2", "title": "waiting doc", "content": "text", "creator": "alice"}],
    "Projects": [{"op": "add_project_update", "project": "p1", "user": "alice", "body": "waiting"},
                 {"op": "edit_project", "project": "p1", "content": "edited, waiting"}],
    "Project": [{"op": "edit_project", "project": "p3", "content": "edited, waiting for Project"}],
    "TrashedIssues": [{"op": "trash_issue", "issue": "eng-4"}],
    "projectIds": [{"op": "delete_project", "project": "p4"}],
    "issueIds": [{"op": "delete_issue", "issue": "web-1"}],
    "attachmentIds": [{"op": "delete_attachment", "attachment": "a2"}],
    "documentIds": [{"op": "delete_document", "document": "pd1"}],
}


@pytest.mark.integration
@pytest.mark.linear_sim
@pytest.mark.asyncio(loop_scope="session")
class TestLinearSimFailure:

    async def test_tc_lnsim_fail_001_one_503_on_every_operation(self, linear_world: LinearWorld) -> None:
        w = linear_world
        w.load(workspace())
        w.create_connector()
        w.faults(*[op_fault(op, 503, first_n=1) for op in sorted(SYNC_OPERATIONS - {"organization", "document"})])
        assert succeeded(await w.enable()), "one 503 on each operation failed the run"
        log = w.sim.call("GET", "/_sim/linear/op-faults")["log"]
        fired = {e["operation"] for e in log}
        silent = SYNC_OPERATIONS - {"organization", "document"} - fired
        assert not silent, f"faults that never fired: {sorted(silent)}"
        w.clear_faults()
        await w.assert_matches_source("after one 503 on every operation")

    async def test_tc_lnsim_fail_002_rate_limited_listings_are_waited_out(self, linear_world: LinearWorld) -> None:
        w = linear_world
        w.load(workspace())
        w.create_connector()
        w.faults(*[op_fault(op, 429, first_n=1) for op in LISTINGS])
        assert succeeded(await w.enable()), "a 429 RATELIMITED was not waited out"
        assert w.clear_faults() >= len(LISTINGS)
        await w.assert_matches_source("after a 429 on the listings")

    @pytest.mark.parametrize("mode", ["lasting-403"])
    async def test_tc_lnsim_fail_003_a_lasting_403_on_each_operation(self, linear_world: LinearWorld, mode: str) -> None:
        w = linear_world
        assert succeeded(await w.start(workspace()))
        silent = []
        for op in [o for o in sorted(SYNC_OPERATIONS) if o not in ("organization", "document")]:
            before = await w.records()
            w.ops(*CHANGES[op])
            w.faults(op_fault(op, 403))
            await w.sync(full=True)
            if not w.fired():
                silent.append(op)
            w.clear_faults() if w.fired() else w.sim.clear_faults()
            await w.assert_nothing_live_lost(before, f"after a 403 on {op}")
            assert succeeded(await w.sync()), f"the clean sync after a 403 on {op} did not succeed"
            await w.assert_matches_source(f"the clean sync after a 403 on {op}")
        assert not silent, f"the fault never fired for {silent}: the case proves nothing for them"

    @pytest.mark.xfail(strict=True, reason="a team whose issues could not be read does not fail the run: "
                                           "connector.py:817-829 run_sync reports it (notification) and returns")
    async def test_tc_lnsim_fail_004_outcome_of_a_refused_issue_listing(self, linear_world: LinearWorld) -> None:
        w = linear_world
        assert succeeded(await w.start(small()))
        w.ops({"op": "add_issue", "team": "OPS", "title": "waiting", "creator": "bob"})
        w.faults(op_fault("issues", 403))
        outcome = await w.sync()
        w.clear_faults()
        assert not succeeded(outcome), "a run that could not read the issues was reported as succeeded"

    async def test_tc_lnsim_fail_005_a_503_that_outlives_the_retries(self, linear_world: LinearWorld) -> None:
        w = linear_world
        assert succeeded(await w.start(workspace()))
        for op in ("teams", "issues", "Project", "issueIds", "users"):
            before = await w.records()
            w.ops(*CHANGES[op])
            w.faults(op_fault(op, 503, max_times=4))  # the request and its three retries
            await w.sync(full=True)
            w.clear_faults()
            await w.assert_nothing_live_lost(before, f"after a lasting 503 on {op}")
            assert succeeded(await w.sync()), f"the clean sync after a 503 on {op} did not succeed"
            await w.assert_matches_source(f"the clean sync after a 503 on {op}")

    @pytest.mark.parametrize("mode", ["drop", "repeat", "expire"])
    async def test_tc_lnsim_fail_006_broken_cursors(self, linear_world: LinearWorld, mode: str) -> None:
        w = linear_world
        assert succeeded(await w.start(many(130, other=6)))
        before = await w.records()
        w.ops({"op": "add_issue", "team": "BIG", "title": "waiting for the listing", "creator": "alice"})
        w.faults(core=({"match": {"path_regex": "^/graphql$"}, "effect": {"type": "page_token", "mode": mode}},))
        await w.sync(full=True)
        w.clear_faults()
        await w.assert_nothing_live_lost(before, f"after a {mode} cursor")
        assert succeeded(await w.sync())
        await w.assert_matches_source(f"the clean sync after a {mode} cursor")

    async def test_tc_lnsim_fail_007_a_dropped_connection_is_retried(self, linear_world: LinearWorld) -> None:
        w = linear_world
        w.load(workspace())
        w.create_connector()
        w.faults(core=({"match": {"path_regex": "^/graphql$"}, "trigger": {"after_n": 6, "max_times": 2},
                        "effect": {"type": "reset"}},))
        assert succeeded(await w.enable()), "two dropped connections failed the run"
        assert w.clear_faults() == 2
        await w.assert_matches_source("after two dropped connections")

    async def test_tc_lnsim_fail_008_the_connection_test(self, linear_world: LinearWorld) -> None:
        w = linear_world
        w.load(small())
        w.create_connector()
        w.faults(op_fault("organization", 403))
        assert await w.enable() == REFUSED, "a connector whose connection test was refused was turned on"
        w.clear_faults()
        assert not await w.records(), "a refused connector stored records"
        w.faults(op_fault("organization", 503, first_n=1))
        assert succeeded(await w.enable()), "one 503 on the connection test stopped the connector"
        w.clear_faults()
        await w.assert_matches_source("after the connection test recovered")

    async def test_tc_lnsim_fail_009_a_page_of_the_issue_listing_refused(self, linear_world: LinearWorld) -> None:
        w = linear_world
        assert succeeded(await w.start(many(130, other=6)))
        before = await w.records()
        w.ops({"op": "add_issue", "team": "BIG", "title": "waiting for page two", "creator": "alice"},
              {"op": "edit_issue", "issue": "i3", "title": "edited, waiting"})
        w.faults(op_fault("issues", 403, after_n=1, max_times=1))
        await w.sync(full=True)
        w.clear_faults()
        await w.assert_nothing_live_lost(before, "after the second page was refused")
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("the clean sync after the second page was refused")

    async def test_tc_lnsim_fail_010_the_file_host_fails(self, linear_world: LinearWorld) -> None:
        w = linear_world
        assert succeeded(await w.start(small()))
        x = w.sim.expected()
        upload = x["uploads"][0]
        owner = w.ident("issues", "e1")
        ext = f"{upload['url']}#parent={owner}"
        good = w.upload_bytes("spec")
        for status in (503, 403):
            w.faults(core=({"match": {"path_regex": r"^/[0-9a-f-]{36}/[0-9a-f-]{36}/spec\.pdf$"},
                            "effect": {"type": "status", "code": status}},))
            with pytest.raises(Exception):  # noqa: B017, PT011 - the HTTP error of the stream
                await w.stream(ext)
            w.clear_faults()
            assert await w.record_for(ext), f"a {status} on the file host removed the record"
        assert await w.stream(ext) == good, "the file did not stream the source's bytes once the host answered"

    async def test_tc_lnsim_fail_011_member_pages_of_a_big_team(self, linear_world: LinearWorld) -> None:
        w = linear_world
        account = small()
        account["users"] += [{"key": f"m{i}", "name": f"Member {i}"} for i in range(14)]
        account["teams"][0]["members"] = ["sync", "alice", *[f"m{i}" for i in range(14)]]
        # The same people must have PipesHub accounts for the comparison to name them: only the grant of the team matters.
        assert succeeded(await w.start(account))
        assert w.sim.operations().get("teamMembers", 0) >= 1, "a team of 16 was read without the nested member listing"
        before = await w.records()
        w.ops({"op": "add_member", "team": "ENG", "user": "bob"})
        w.faults(op_fault("teamMembers", 403))
        await w.sync(full=True)
        w.clear_faults()
        await w.assert_nothing_live_lost(before, "after the member listing was refused")
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after the member listing recovered")

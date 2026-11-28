# pyright: ignore-file

"""
GitHub Teams on the simulator: the first sync, an unchanged sync, paging
========================================================================

The connector syncs one repository (issues, pull requests, code files) of an organisation the simulator holds. After each
sync PipesHub is compared with GitHub's own rules for every person and every record (``GithubWorld.differences``).

  TC-GHSIM-SYNC-001  First sync equals the oracle: issues with comments, sub-issue, blocker, pull request, files and
                     folders, each under its record group, with GitHub's web address [B]
  TC-GHSIM-SYNC-002  A sub-issue carries its parent; the blocking link is read for a blocker only; links made later reach
                     PipesHub [B, D]
  TC-GHSIM-SYNC-003  A blocker whose blocked issue is closed still links it (strict xfail: the dependency read is gated on
                     GitHub's open count) [B]
  TC-GHSIM-SYNC-004  An unchanged sync creates, removes and re-versions nothing; a full sync after it rebuilds the same
                     state [C, F]
  TC-GHSIM-SYNC-005  After a restart of the connector the issue listing is incremental (it names ``since``) [C]
  TC-GHSIM-SYNC-006  An issue edited between two pages of the listing is read (150 issues, two pages) [H]
  TC-GHSIM-SYNC-007  Past GitHub's page limit the listing goes on through the cursor [H]
  TC-GHSIM-SYNC-008  An edit in the same second as the checkpoint is read [H]
  TC-GHSIM-SYNC-009  A listing that fails on its third request resumes on the next run; the end state equals the source [H, I]
  TC-GHSIM-SYNC-010  Indexing reaches a terminal state for every record: none failed, none left running [B]
"""

import pytest
from connectors.github_teams.sim_instances import acme, issues, rich
from connectors.github_teams.sim_world import (
    GROUPS,
    GithubWorld,
    fault,
    indexed,
    succeeded,
)


@pytest.mark.integration
@pytest.mark.github_teams_sim
@pytest.mark.asyncio(loop_scope="session")
class TestGithubSimSync:

    async def test_tc_ghsim_sync_001_first_sync_equals_the_oracle(self, github_world: GithubWorld) -> None:
        w = github_world
        assert succeeded(await w.start(acme(), ops=rich()))
        await w.assert_matches_source("first sync")
        rid = w.repo_id()
        records = {r["externalRecordId"]: r for r in await w.records()}
        names = {r["recordName"] for r in records.values() if r["recordType"] == "TICKET"}
        assert names == {"Login fails", "Epic", "Task", "Blocked", "Blocker"}, names
        assert {r["recordName"] for r in records.values() if r["recordType"] == "PULL_REQUEST"} == {"Fix login"}
        code = {r["externalRecordId"] for r in records.values() if r["recordType"] == "CODE_FILE"}
        assert f"/{rid}/blob/docs/a/b/deep.txt" in code and f"/{rid}/blob/Makefile" in code, sorted(code)
        assert f"/{rid}/tree/docs/a/b" in records, "no folder record for a nested directory"
        for ext, r in records.items():
            assert r["externalGroupId"] == f"{rid}-{GROUPS[r['recordType']]}", (r["recordType"], ext, r["externalGroupId"])
        assert records[f"{rid}/issues/1"]["webUrl"] == "https://github.com/acme/widgets/issues/1"
        assert records[f"{rid}/pull/6"]["webUrl"] == "https://github.com/acme/widgets/pull/6"
        text = await w.stream_text(f"{rid}/issues/1")
        assert "Steps: click login" in text and "seen it too" in text and "same here" in text, text[:400]

    async def test_tc_ghsim_sync_002_sub_issues_and_blocking_links(self, github_world: GithubWorld) -> None:
        w = github_world
        assert succeeded(await w.start(acme(), ops=rich()))
        rid, since = w.repo_id(), w.sim.total_calls()
        g = w.graph
        task = await g.get_record_parent_external_id(w.connector_id, f"{rid}/issues/3")
        assert task == f"{rid}/issues/2", f"the sub-issue's parent is {task}"
        assert f"{rid}/issues/4" in await g.get_record_outgoing_relations(w.connector_id, f"{rid}/issues/5", "BLOCKS")
        asked = [c["path"] for c in w.sim.calls() if c["path"].endswith("/dependencies/blocking")]
        assert asked == ["/repos/acme/widgets/issues/5/dependencies/blocking"], \
            f"the dependency read must be asked for the blocker alone: {asked}"
        # links made between syncs reach PipesHub on the next run
        w.ops({"op": "set_parent", "issue": "blocked", "parent": "epic"}, {"op": "set_parent", "issue": "task", "parent": None})
        assert succeeded(await w.sync())
        assert await g.get_record_parent_external_id(w.connector_id, f"{rid}/issues/4") == f"{rid}/issues/2"
        assert not await g.get_record_parent_external_id(w.connector_id, f"{rid}/issues/3"), "a parent removed at GitHub stayed"
        await w.assert_matches_source("after the links changed")

    @pytest.mark.xfail(strict=True, reason="issues.py _related_from_dependencies reads dependencies only when GitHub's "
                                           "open blocking count is positive; a link to a closed issue is never read")
    async def test_tc_ghsim_sync_003_blocking_a_closed_issue_is_a_link(self, github_world: GithubWorld) -> None:
        w = github_world
        ops = [{"op": "add_issue", "repo": "widgets", "title": "Done", "key": "done", "state": "closed"},
               {"op": "add_issue", "repo": "widgets", "title": "Blocker", "key": "blocker", "blocking": ["done"]}]
        assert succeeded(await w.start(acme(), ops=ops))
        rid = w.repo_id()
        targets = await w.graph.get_record_outgoing_relations(w.connector_id, f"{rid}/issues/2", "BLOCKS")
        assert f"{rid}/issues/1" in targets, f"the blocker links {targets}, not the closed issue it blocks"

    async def test_tc_ghsim_sync_004_unchanged_sync_changes_nothing(self, github_world: GithubWorld) -> None:
        w = github_world
        assert succeeded(await w.start(acme(), ops=rich()))
        before = {r["externalRecordId"]: (r["id"], r["version"]) for r in await w.records()}
        grants = await w.holdings()
        assert succeeded(await w.sync(advance=False))
        after = {r["externalRecordId"]: (r["id"], r["version"]) for r in await w.records()}
        assert after == before, f"an unchanged sync created, removed or re-versioned: {set(after.items()) ^ set(before.items())}"
        await w.assert_holdings_kept(grants, "after an unchanged sync")
        assert succeeded(await w.sync(full=True))
        again = {r["externalRecordId"] for r in await w.records()}
        assert again == set(before), f"a full sync changed the records: {again ^ set(before)}"
        await w.assert_matches_source("after a full sync")

    async def test_tc_ghsim_sync_005_restart_keeps_the_checkpoint(self, github_world: GithubWorld) -> None:
        w = github_world
        assert succeeded(await w.start(acme(), ops=rich()))
        w.client.toggle_sync(w.connector_id, enable=False)
        await w._wait_idle()
        position = w.sim.total_calls()
        assert succeeded(await w.enable())
        listing = [c for c in w.sim.calls(position) if c["path"] == "/repos/acme/widgets/issues"]
        assert listing and all("since" in c["query"] for c in listing), f"the listing after a restart was not incremental: {listing}"
        await w.assert_matches_source("after the restart")

    async def test_tc_ghsim_sync_006_issue_edited_between_two_pages(self, github_world: GithubWorld) -> None:
        w = github_world
        assert succeeded(await w.start(acme(), ops=issues(120)))
        w.ops(*[{"op": "edit_issue", "issue": f"issue{n}", "title": f"Issue {n:03} v2"} for n in range(120)])
        # between the first and second page request, someone edits an issue the first page returned
        w.defer("/repos/acme/widgets/issues", [{"op": "edit_issue", "issue": "issue0", "title": "Issue 000 v3"}], after=2)
        for _ in range(2):
            assert succeeded(await w.sync())
        titles = sorted(r["recordName"] for r in await w.records() if r["recordType"] == "TICKET")
        assert titles == sorted(["Issue 000 v3"] + [f"Issue {n:03} v2" for n in range(1, 120)]), titles[:5]
        await w.assert_matches_source("after the edit between two pages")

    async def test_tc_ghsim_sync_007_past_the_page_limit_the_cursor_is_used(self, github_world: GithubWorld) -> None:
        w = github_world
        ops = issues(110) + [{"op": "add_pull", "repo": "widgets", "title": f"PR {n:03}"} for n in range(25)]
        assert succeeded(await w.start(acme(), ops=ops, behaviours={"issues_page_limit": 150}))
        assert len([r for r in await w.records() if r["recordType"] == "TICKET"]) == 110
        await w.assert_matches_source("past the page limit")
        w.ops({"op": "add_issue", "repo": "widgets", "title": "After the checkpoint"})
        assert succeeded(await w.sync())
        assert "After the checkpoint" in {r["recordName"] for r in await w.records()}
        await w.assert_matches_source("incremental past the page limit")

    async def test_tc_ghsim_sync_008_edit_in_the_second_of_the_checkpoint(self, github_world: GithubWorld) -> None:
        w = github_world
        assert succeeded(await w.start(acme(), ops=[{"op": "add_issue", "repo": "widgets", "title": "A", "key": "a"},
                                                    {"op": "add_issue", "repo": "widgets", "title": "B", "key": "b"}]))
        w.ops({"op": "edit_issue", "issue": "a", "title": "A v2"})
        assert succeeded(await w.sync(advance=False))
        assert {r["recordName"] for r in await w.records() if r["recordType"] == "TICKET"} == {"A v2", "B"}
        w.ops({"op": "edit_issue", "issue": "b", "title": "B v2"}, {"op": "edit_issue", "issue": "a", "title": "A v3"})
        assert succeeded(await w.sync(advance=False))
        assert {r["recordName"] for r in await w.records() if r["recordType"] == "TICKET"} == {"A v3", "B v2"}

    async def test_tc_ghsim_sync_009_interrupted_listing_resumes(self, github_world: GithubWorld) -> None:
        w = github_world
        await w.start(acme(), ops=issues(130), enable=False)
        w.faults(fault(r"/repos/acme/widgets/issues$", 503, after_n=1))  # the second page of the listing and on
        before = await w.holdings()
        assert not succeeded(await w.enable()), "a listing that failed on its second page reported success"
        w.clear_faults()
        await w.assert_holdings_kept(before, "after a listing cut in the middle", allow_gains=True)
        assert succeeded(await w.sync())
        assert len([r for r in await w.records() if r["recordType"] == "TICKET"]) == 130
        await w.assert_matches_source("after the listing resumed")

    async def test_tc_ghsim_sync_010_indexing_reaches_a_terminal_state(self, github_world: GithubWorld) -> None:
        w = github_world
        assert succeeded(await w.start(acme(), ops=rich()))
        records = await indexed(w)
        stuck = [(r["recordName"], r["indexingStatus"]) for r in records if r["indexingStatus"] in
                 ("NOT_STARTED", "QUEUED", "IN_PROGRESS", None)]
        failed = [(r["recordName"], r["indexingStatus"]) for r in records if str(r["indexingStatus"]).startswith("FAILED")]
        assert not stuck and not failed, f"indexing: left running {stuck[:5]}, failed {failed[:5]}"

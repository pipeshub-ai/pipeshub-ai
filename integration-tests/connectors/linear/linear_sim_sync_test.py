# pyright: ignore-file

"""
Linear on the simulator: first sync, unchanged sync, paging
===========================================================

The connector reads one workspace through Linear's GraphQL API (the simulator, behind the TLS front) and the comparison
asks, for every person and every record, whether PipesHub's own permission query agrees with Linear's rules.

  TC-LNSIM-SYNC-001  First sync of a workspace with every team visibility, sub-teams, a guest, a suspended person, issues,
                     sub-issues, links, documents, projects, files, trash and archive equals the oracle for every
                     person, and every record is indexed [B, E, K]
  TC-LNSIM-SYNC-002  Every ticket is named "[IDENTIFIER] title", filed under its team, every team is a record group with its
                     key and parent, and every web URL points at the workspace [B]
  TC-LNSIM-SYNC-003  A sync with nothing new creates, removes and re-versions nothing [C]
  TC-LNSIM-SYNC-004  A full sync of an unchanged workspace keeps every record's id and version [C]
  TC-LNSIM-SYNC-005  Someone with a PipesHub account and no Linear account, and a suspended person, read nothing; a guest
                     reads only the issues of the teams they belong to [E]
  TC-LNSIM-SYNC-006  A listing that spans several pages (the page limit lowered to 2) is read in full [H]
  TC-LNSIM-SYNC-007  130 issues in one team (three pages at the connector's page size) and a second team: equals the oracle [H]
  TC-LNSIM-SYNC-008  The sync reads the operations the fault matrix lists, and no others (a new call must be added to the
                     fault cases) [I, O]
  TC-LNSIM-SYNC-009  A workspace with no issues at all: the sync succeeds, holds the teams and nothing else [B]
  TC-LNSIM-SYNC-010  Names a person could use to inject markup or a path (angle brackets, quotes, slashes, dots) are stored
                     as plain text and never change the record's place [N]
"""

import asyncio
import time

import pytest
from connectors.linear.sim_instances import OUTSIDER, email_of, small, workspace
from connectors.linear.sim_world import LinearWorld, succeeded

# The GraphQL operations a first sync of the workspace sends (the simulator names each by the operation the connector
# sends). Every one has a transient and a lasting fault case in linear_sim_failure_test.py.
SYNC_OPERATIONS = {
    "organization", "viewer", "users", "teams", "issues", "issue", "document", "Attachments", "Documents", "Projects",
    "Project", "TrashedIssues", "projectIds", "issueIds", "attachmentIds", "documentIds",
}


@pytest.mark.integration
@pytest.mark.linear_sim
@pytest.mark.asyncio(loop_scope="session")
class TestLinearSimSync:

    async def test_tc_lnsim_sync_001_first_sync_equals_the_oracle(self, linear_world: LinearWorld) -> None:
        w = linear_world
        assert succeeded(await w.start(workspace()))
        await w.assert_matches_source("first sync")
        problems = w.source_problems()
        assert not problems, problems
        # What was stored is indexed (the stack's models are fake, so indexing finishes; an empty file is EMPTY).
        deadline = time.monotonic() + 120
        while True:
            pending = [r["recordName"] for r in await w.records() if r["indexingStatus"] not in ("COMPLETED", "EMPTY")]
            if not pending or time.monotonic() > deadline:
                break
            await asyncio.sleep(3)
        assert not pending, f"records never finished indexing: {pending[:8]}"

    async def test_tc_lnsim_sync_002_names_parents_and_urls(self, linear_world: LinearWorld) -> None:
        w = linear_world
        assert succeeded(await w.start(workspace()))
        x = w.sim.expected()
        records = {r["externalRecordId"]: r for r in await w.records()}
        for issue in x["issues"]:
            if issue["trashed"] or issue["archived"] or not issue["visible_to_viewer"]:
                continue
            record = records[issue["id"]]
            title = next(i for i in w.scenario["issues"] if i["key"] == issue["key"])["title"]
            assert record["recordName"] == f"[{issue['identifier']}] {title}", record
            team = next(t for t in x["teams"] if t["key"] == issue["team"])
            assert record["externalGroupId"] == team["id"], (issue["identifier"], record["externalGroupId"])
            assert record["webUrl"].startswith(f"https://linear.app/{x['org']['url_key']}/"), record["webUrl"]
        for team in x["teams"]:
            if not team["visible_to_viewer"]:
                continue
            group = await w.graph.get_record_group_by_external_id(w.connector_id, team["id"])
            assert group is not None, f"team {team['key']} has no record group"
            assert group.name == team["name"] and group.short_name == team["linear_key"], (group.name, group.short_name)
            parent = next((t["id"] for t in x["teams"] if t["key"] == team["parent"]), None) if team["parent"] else None
            assert (group.parent_external_group_id or None) == parent, (team["key"], group.parent_external_group_id)

    async def test_tc_lnsim_sync_003_a_sync_with_nothing_new_changes_nothing(self, linear_world: LinearWorld) -> None:
        w = linear_world
        assert succeeded(await w.start(workspace()))
        before = {r["externalRecordId"]: (r["id"], r["version"]) for r in await w.records()}
        grants = await w.holdings()
        assert succeeded(await w.sync())
        after = {r["externalRecordId"]: (r["id"], r["version"]) for r in await w.records()}
        assert after == before, {k: (before.get(k), after.get(k)) for k in set(before) | set(after) if before.get(k) != after.get(k)}
        await w.assert_holdings_kept(grants, "after a sync with nothing new")
        await w.assert_matches_source("after a sync with nothing new")

    async def test_tc_lnsim_sync_004_full_sync_keeps_ids_and_versions(self, linear_world: LinearWorld) -> None:
        w = linear_world
        assert succeeded(await w.start(workspace()))
        before = {r["externalRecordId"]: (r["id"], r["version"]) for r in await w.records()}
        assert succeeded(await w.sync(full=True))
        after = {r["externalRecordId"]: (r["id"], r["version"]) for r in await w.records()}
        assert after == before, {k: (before.get(k), after.get(k)) for k in set(before) | set(after) if before.get(k) != after.get(k)}
        await w.assert_matches_source("after a full sync of an unchanged workspace")

    async def test_tc_lnsim_sync_005_who_reads_nothing(self, linear_world: LinearWorld) -> None:
        w = linear_world
        assert succeeded(await w.start(workspace()))
        held = await w.holdings()
        assert held["records"], "the sync held nothing"
        assert not held[OUTSIDER], f"a PipesHub user with no Linear account reads {sorted(held[OUTSIDER])}"
        assert not held[email_of("sam")], "a suspended person reads records"
        x = w.sim.expected()
        guest_teams = {t["key"] for t in x["teams"] if email_of("gail") in t["members"]}
        by_id = {i["id"]: i for i in x["issues"]}
        for ext in held[email_of("gail")]:
            if ext in by_id:
                assert by_id[ext]["team"] in guest_teams, f"a guest reads {by_id[ext]['identifier']} outside their teams"
        assert any(ext in by_id for ext in held[email_of("gail")]), "the guest reads none of their teams' issues"

    async def test_tc_lnsim_sync_006_small_pages(self, linear_world: LinearWorld) -> None:
        w = linear_world
        assert succeeded(await w.start(small(), behaviours={"max_page": 2, "over_page_limit": "clamp"}))
        await w.assert_matches_source("every listing in pages of two")
        # The connector asked for more than two a page: the listings really were cut and continued.
        assert w.sim.operations()["issues"] >= 3, w.sim.operations()

    async def test_tc_lnsim_sync_007_three_pages_and_two_teams(self, linear_world: LinearWorld) -> None:
        from connectors.linear.sim_instances import many
        w = linear_world
        assert succeeded(await w.start(many(130, other=8)))
        await w.assert_matches_source("130 issues in one team")
        assert w.sim.operations()["issues"] >= 3

    async def test_tc_lnsim_sync_008_the_operations_a_sync_sends(self, linear_world: LinearWorld) -> None:
        w = linear_world
        assert succeeded(await w.start(workspace()))
        sent = set(w.sim.operations())
        assert sent == SYNC_OPERATIONS, f"unlisted: {sorted(sent - SYNC_OPERATIONS)}, never sent: {sorted(SYNC_OPERATIONS - sent)}"

    async def test_tc_lnsim_sync_009_an_empty_workspace(self, linear_world: LinearWorld) -> None:
        w = linear_world
        empty = small()
        empty["issues"], empty["projects"], empty["uploads"] = [], [], []
        assert succeeded(await w.start(empty))
        assert not await w.records()
        await w.assert_matches_source("a workspace with teams and no issues", allow_empty=True)

    async def test_tc_lnsim_sync_010_names_are_plain_text(self, linear_world: LinearWorld) -> None:
        w = linear_world
        account = small()
        account["issues"] += [
            {"key": "x1", "team": "ENG", "title": "<img src=x onerror=alert(1)>", "creator": "alice"},
            {"key": "x2", "team": "ENG", "title": "../../etc/passwd ' OR 1=1 --", "creator": "alice"},
            {"key": "x3", "team": "OPS", "title": "Q: \"quotes\" & <b>bold</b> \\ back\\slash", "creator": "bob"}]
        assert succeeded(await w.start(account))
        await w.assert_matches_source("issues with hostile titles")
        names = {r["recordName"] for r in await w.records()}
        for key in ("x1", "x2", "x3"):
            issue = next(i for i in account["issues"] if i["key"] == key)
            ident = next(i["identifier"] for i in w.sim.expected()["issues"] if i["key"] == key)
            assert f"[{ident}] {issue['title']}" in names, names

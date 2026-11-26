# pyright: ignore-file

"""
Linear on the simulator: changes between syncs
==============================================

A first sync, a change at the source, a second sync: PipesHub must equal the source after each one, for every person.

  TC-LNSIM-CHG-001  Every kind of edit: an issue edited, a comment, a link, a document, a new issue and its sub-issue, a
                    project update, milestone, link and text, an edited document, a deleted link, a trashed issue, a deleted
                    issue, a trashed project [D]
  TC-LNSIM-CHG-002  An issue trashed, restored, then deleted for good [D, F]
  TC-LNSIM-CHG-003  An issue moved into a private team, out of it, and back (sub-issues follow) [D, E]
  TC-LNSIM-CHG-004  Members added to and removed from a private team, a team made private and public again, a guest added to
                    a team: nobody gains or loses a record the source does not give or take [E]
  TC-LNSIM-CHG-005  A person suspended, one reinstated, one made a guest [E]
  TC-LNSIM-CHG-006  A restricted team made private and restricted again [E]
  TC-LNSIM-CHG-007  A team added (with a restricted sub-team under a private one), a team archived, one re-keyed, one deleted [D, E]
  TC-LNSIM-CHG-008  A project: a team added to and removed from it, its lead team changed, archived, unarchived, trashed,
                    restored (its documents, links and files follow) [D, E]
  TC-LNSIM-CHG-009  An issue deleted and another created between two pages of the listing: the sync does not fail and the
                    next one picks up the rest [H]
  TC-LNSIM-CHG-010  An issue re-parented and renamed, a comment edited and deleted, a label changed: names and parents follow [D]
  TC-LNSIM-CHG-011  A document deleted and one moved off its issue's list; a project's milestone renamed [D]
  TC-LNSIM-CHG-012  An issue's text edited (version moves), then edited back: the record's version only moves forward [D]
  TC-LNSIM-CHG-013  Changes that touch a record's readers and not its update time (a private team's member removed, a
                    project's team removed with touch off) are still seen by the next sync [E]
"""

import pytest
from connectors.linear.sim_instances import small, many, workspace
from connectors.linear.sim_world import LinearWorld, succeeded


async def synced(w: LinearWorld, label: str, *, full: bool = False) -> None:
    assert succeeded(await w.sync(full=full)), f"{label}: the sync did not succeed"
    await w.assert_matches_source(label)


@pytest.mark.integration
@pytest.mark.linear_sim
@pytest.mark.asyncio(loop_scope="session")
class TestLinearSimChanges:

    async def test_tc_lnsim_chg_001_every_kind_of_edit(self, linear_world: LinearWorld) -> None:
        w = linear_world
        assert succeeded(await w.start(workspace()))
        await w.assert_matches_source("first sync")
        w.sim.call("POST", "/_sim/linear/stats/reset")
        w.ops(
            {"op": "edit_issue", "issue": "eng-2", "title": "Renamed importer", "description": "new text"},
            {"op": "add_comment", "issue": "eng-1", "user": "bob", "body": "late comment {{upload:spec}}"},
            {"op": "add_attachment", "issue": "eng-2", "title": "Fresh link", "url": "https://x.example/1"},
            {"op": "add_document", "issue": "eng-2", "title": "Fresh doc", "content": "text", "creator": "alice"},
            {"op": "add_issue", "key": "ops-2", "team": "OPS", "title": "Brand new", "creator": "bob",
             "comments": [{"user": "bob", "body": "first"}]},
            {"op": "add_issue", "key": "ops-sub", "team": "OPS", "title": "Sub of new", "parent": "ops-2"},
            {"op": "add_project_update", "project": "p1", "user": "alice", "body": "At risk", "health": "atRisk"},
            {"op": "edit_project", "project": "p1", "content": "new plan"},
            {"op": "add_milestone", "project": "p1", "name": "RC"},
            {"op": "add_link", "project": "p1", "label": "Doc", "url": "https://x.example/doc"},
            {"op": "edit_document", "document": "d1", "content": "edited"},
            {"op": "delete_attachment", "attachment": "a2"},
            {"op": "trash_issue", "issue": "eng-4"},
            {"op": "delete_issue", "issue": "web-1"},
            {"op": "trash_project", "project": "p3"})
        await synced(w, "after every kind of edit")
        assert b"late comment" in await w.stream(w.ident("issues", "eng-1")), "a comment added later is not in the issue's text"
        # Nothing changed since: the next sync leaves everything as it is.
        before = {r["externalRecordId"]: (r["id"], r["version"]) for r in await w.records()}
        await synced(w, "with nothing new")
        assert {r["externalRecordId"]: (r["id"], r["version"]) for r in await w.records()} == before

    async def test_tc_lnsim_chg_002_trash_restore_delete(self, linear_world: LinearWorld) -> None:
        w = linear_world
        assert succeeded(await w.start(workspace()))
        w.ops({"op": "trash_issue", "issue": "eng-4"}, {"op": "trash_issue", "issue": "eng-2"})
        await synced(w, "two issues trashed")
        w.ops({"op": "restore_issue", "issue": "eng-4"})
        await synced(w, "one restored")
        w.ops({"op": "delete_issue", "issue": "eng-4"}, {"op": "delete_issue", "issue": "eng-2"})
        await synced(w, "both deleted for good")

    async def test_tc_lnsim_chg_003_moves_between_teams(self, linear_world: LinearWorld) -> None:
        w = linear_world
        assert succeeded(await w.start(workspace()))
        w.ops({"op": "move_issue", "issue": "eng-1", "team": "SEC"})
        await synced(w, "moved into a private team")
        w.ops({"op": "move_issue", "issue": "sec-1", "team": "OPS"})
        await synced(w, "moved out of a private team")
        w.ops({"op": "move_issue", "issue": "eng-1", "team": "ENG"}, {"op": "trash_issue", "issue": "eng-1"},
              {"op": "restore_issue", "issue": "eng-1"})
        await synced(w, "moved back, trashed and restored")

    async def test_tc_lnsim_chg_004_membership_and_privacy(self, linear_world: LinearWorld) -> None:
        w = linear_world
        assert succeeded(await w.start(workspace()))
        w.ops({"op": "add_member", "team": "SEC", "user": "bob"}, {"op": "remove_member", "team": "SEC", "user": "alice"},
              {"op": "add_member", "team": "PORTAL", "user": "carol"})
        await synced(w, "private team members changed")
        w.ops({"op": "set_visibility", "team": "PORTAL", "visibility": "private"})
        await synced(w, "a public team became private")
        w.ops({"op": "set_visibility", "team": "PORTAL", "visibility": "public"}, {"op": "add_member", "team": "ENG", "user": "gail"})
        await synced(w, "private team became public, a guest was added to a team")

    async def test_tc_lnsim_chg_005_people_suspended_and_reinstated(self, linear_world: LinearWorld) -> None:
        w = linear_world
        assert succeeded(await w.start(workspace()))
        w.ops({"op": "deactivate_user", "user": "rita"}, {"op": "reactivate_user", "user": "sam"},
              {"op": "set_user", "user": "carol", "guest": True})
        await synced(w, "a person suspended, one reinstated, one made a guest")
        w.ops({"op": "reactivate_user", "user": "rita"}, {"op": "set_user", "user": "carol", "guest": False})
        await synced(w, "and back")

    async def test_tc_lnsim_chg_006_restricted_and_private(self, linear_world: LinearWorld) -> None:
        w = linear_world
        assert succeeded(await w.start(workspace()))
        w.ops({"op": "set_visibility", "team": "SECOPS", "visibility": "private"})
        await synced(w, "restricted team became private")
        w.ops({"op": "set_visibility", "team": "SECOPS", "visibility": "restricted"})
        await synced(w, "private team became restricted again")

    async def test_tc_lnsim_chg_007_teams_come_and_go(self, linear_world: LinearWorld) -> None:
        w = linear_world
        assert succeeded(await w.start(workspace()))
        w.ops({"op": "add_team", "key": "NEW", "name": "New team", "members": ["sync", "alice"]},
              {"op": "add_issue", "team": "NEW", "title": "In the new team", "creator": "alice"},
              {"op": "add_team", "key": "SECSUB", "visibility": "restricted", "parent": "SEC", "members": ["sync", "bob"]},
              {"op": "add_issue", "team": "SECSUB", "title": "Under security", "creator": "bob"})
        await synced(w, "teams added")
        w.ops({"op": "archive_team", "team": "PORTAL"}, {"op": "rename_team_key", "team": "ENG", "new_key": "ENGX"})
        await synced(w, "a team archived, another re-keyed")
        w.ops({"op": "delete_team", "team": "OPS"})
        await synced(w, "a team deleted for good")

    async def test_tc_lnsim_chg_008_project_changes(self, linear_world: LinearWorld) -> None:
        w = linear_world
        assert succeeded(await w.start(workspace()))
        w.ops({"op": "set_project_teams", "project": "p1", "teams": ["ENG", "OPS", "PORTAL"]})
        await synced(w, "a team added to a project")
        w.ops({"op": "set_project_teams", "project": "p1", "teams": ["ENG"]})
        await synced(w, "teams removed from a project")
        w.ops({"op": "archive_project", "project": "p1"})
        await synced(w, "a project archived")
        w.ops({"op": "unarchive_project", "project": "p1"}, {"op": "trash_project", "project": "p3"})
        await synced(w, "unarchived, another trashed")
        w.ops({"op": "restore_project", "project": "p3"}, {"op": "set_project_teams", "project": "p4", "teams": ["SEC"]})
        await synced(w, "restored, another moved to the private team")

    async def test_tc_lnsim_chg_009_changes_between_pages(self, linear_world: LinearWorld) -> None:
        w = linear_world
        account = many(130, other=10)
        w.load(account)
        w.create_connector()
        w.defer("issues", [{"op": "delete_issue", "issue": "i129"},
                           {"op": "add_issue", "team": "BIG", "title": "created mid-listing", "key": "late",
                            "creator": "alice"}], after=2)
        assert succeeded(await w.enable()), "a change between two pages failed the sync"
        assert w.deferred_applied(), "the change never landed: the listing did not reach a second page"
        await synced(w, "the next sync picks up what the first missed")

    async def test_tc_lnsim_chg_010_reparent_rename_comments(self, linear_world: LinearWorld) -> None:
        w = linear_world
        assert succeeded(await w.start(workspace()))
        w.ops({"op": "set_issue_parent", "issue": "eng-3", "parent": "eng-2"},
              {"op": "edit_issue", "issue": "eng-4", "title": "Done thing, renamed"},
              {"op": "edit_comment", "comment": "c2", "body": "second thread, edited"},
              {"op": "delete_comment", "comment": "c1r"})
        await synced(w, "re-parented, renamed, comments edited and deleted")
        record = await w.record_for(w.ident("issues", "eng-4"))
        assert record["recordName"].endswith("Done thing, renamed"), record

    async def test_tc_lnsim_chg_011_documents_and_milestones(self, linear_world: LinearWorld) -> None:
        w = linear_world
        assert succeeded(await w.start(workspace()))
        w.ops({"op": "delete_document", "document": "d1"}, {"op": "edit_milestone", "project": "p1", "milestone": "m1", "name": "Beta 2"},
              {"op": "trash_document", "document": "pd1"})
        await synced(w, "documents deleted and trashed, a milestone renamed")

    async def test_tc_lnsim_chg_012_versions_move_forward(self, linear_world: LinearWorld) -> None:
        w = linear_world
        assert succeeded(await w.start(small()))
        key = w.ident("issues", "e2")
        v0 = (await w.record_for(key))["version"]
        w.ops({"op": "edit_issue", "issue": "e2", "description": "changed once"})
        await synced(w, "edited")
        v1 = (await w.record_for(key))["version"]
        assert v1 > v0, (v0, v1)
        w.ops({"op": "edit_issue", "issue": "e2", "description": ""})
        await synced(w, "edited back")
        assert (await w.record_for(key))["version"] >= v1

    async def test_tc_lnsim_chg_013_readers_change_without_an_update_time(self, linear_world: LinearWorld) -> None:
        w = linear_world
        assert succeeded(await w.start(workspace()))
        w.ops({"op": "remove_member", "team": "ENGHR", "user": "carol"})
        await synced(w, "a private team's member removed")
        w.ops({"op": "set_project_teams", "project": "p1", "teams": ["ENG"], "touch": False})
        await synced(w, "a project's team removed without touching the project")

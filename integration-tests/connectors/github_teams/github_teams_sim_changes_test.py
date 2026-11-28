# pyright: ignore-file

"""
GitHub Teams on the simulator: changes between syncs, deletions, branches
=========================================================================

Each case syncs, changes the world, syncs again, and compares PipesHub with GitHub's rules.

  TC-GHSIM-CHG-001  Issue created, edited, renamed, closed, commented [D]
  TC-GHSIM-CHG-002  Pull request edited, a review and a comment added, then merged [D]
  TC-GHSIM-CHG-003  Files added, modified, renamed and deleted by commits [D]
  TC-GHSIM-CHG-004  A file deleted in a commit; a force push that drops most of the files [D, F]
  TC-GHSIM-CHG-005  An issue deleted at GitHub is removed [D, F]
  TC-GHSIM-CHG-006  An issue transferred to another repository leaves this one [D, F]
  TC-GHSIM-CHG-007  A deleted repository: every record is removed [F]
  TC-GHSIM-CHG-008  The sync account leaves the organisation: the repository's records are removed [F]
  TC-GHSIM-CHG-009  Another repository selected: the old one's records go, the new one's arrive [F, G]
  TC-GHSIM-CHG-010  A renamed repository keeps every record (the filter still names the old name) [D]
  TC-GHSIM-CHG-011  Issues turned off remove the tickets, turned on bring them back [D, F]
  TC-GHSIM-CHG-012  A chosen branch is indexed; switching branch updates in place and back again [G]
  TC-GHSIM-CHG-013  A chosen branch deleted at GitHub: the run fails and the records stay [G, I]
  TC-GHSIM-CHG-014  The default branch moved to a small branch [D]
  TC-GHSIM-CHG-015  A comment added, edited and deleted shows in the streamed issue [D]
  TC-GHSIM-CHG-016  A repository deleted and restored: records come back [D]
"""

import pytest
from connectors.github_teams.sim_instances import acme, rich
from connectors.github_teams.sim_world import (
    GithubWorld,
    branch_filter,
    repo_filter,
    succeeded,
    with_filters,
)


async def names(w: GithubWorld, kind: str) -> set[str]:
    return {r["recordName"] for r in await w.records() if r["recordType"] == kind}


@pytest.mark.integration
@pytest.mark.github_teams_sim
@pytest.mark.asyncio(loop_scope="session")
class TestGithubSimChanges:

    async def test_tc_ghsim_chg_001_issue_changes(self, github_world: GithubWorld) -> None:
        w = github_world
        assert succeeded(await w.start(acme(), ops=[{"op": "add_issue", "repo": "widgets", "title": "First", "key": "first"}]))
        w.ops({"op": "edit_issue", "issue": "first", "title": "First (renamed)", "state": "closed", "state_reason": "completed"},
              {"op": "add_issue", "repo": "widgets", "title": "Second", "key": "second"},
              {"op": "comment", "issue": "second", "user": "alice", "body": "a late comment"})
        assert succeeded(await w.sync())
        assert await names(w, "TICKET") == {"First (renamed)", "Second"}
        assert "a late comment" in await w.stream_text(f"{w.repo_id()}/issues/2")
        await w.assert_matches_source("after the issue changes")

    async def test_tc_ghsim_chg_002_pull_request_changes(self, github_world: GithubWorld) -> None:
        w = github_world
        ops = [{"op": "add_pull", "repo": "widgets", "title": "Feature", "changes": {"src/f.py": "f = 1\n"}, "key": "feature"}]
        assert succeeded(await w.start(acme(), ops=ops))
        w.ops({"op": "edit_pull", "pull": "feature", "title": "Feature v2"},
              {"op": "add_review", "pull": "feature", "user": "alice", "state": "APPROVED", "body": "ship it"},
              {"op": "comment", "issue": "feature", "user": "bob", "body": "conversation"})
        assert succeeded(await w.sync())
        assert await names(w, "PULL_REQUEST") == {"Feature v2"}
        text = await w.stream_text(f"{w.repo_id()}/pull/1")
        assert "ship it" in text and "conversation" in text, text[:400]
        w.ops({"op": "merge_pull", "pull": "feature"})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the merge")

    async def test_tc_ghsim_chg_003_file_changes(self, github_world: GithubWorld) -> None:
        w = github_world
        assert succeeded(await w.start(acme(), ops=[{"op": "commit", "repo": "widgets", "changes": {
            "lib/old_name.py": "same content\n", "lib/gone.py": "bye\n"}}]))
        rid = w.repo_id()
        w.ops({"op": "commit", "repo": "widgets", "changes": {
            "src/app.py": "print('v2')\n", "docs/new.md": "new\n", "lib/new_name.py": "same content\n",
            "lib/old_name.py": None, "lib/gone.py": None}})
        assert succeeded(await w.sync())
        code = {r["externalRecordId"] for r in await w.records() if r["recordType"] == "CODE_FILE"}
        assert f"/{rid}/blob/docs/new.md" in code and f"/{rid}/blob/lib/new_name.py" in code, sorted(code)
        assert f"/{rid}/blob/lib/old_name.py" not in code and f"/{rid}/blob/lib/gone.py" not in code, sorted(code)
        assert await w.stream(f"/{rid}/blob/src/app.py") == b"print('v2')\n"
        await w.assert_matches_source("after the commits")

    async def test_tc_ghsim_chg_004_deleted_file_and_force_push(self, github_world: GithubWorld) -> None:
        w = github_world
        assert succeeded(await w.start(acme(), ops=[{"op": "commit", "repo": "widgets",
                                                     "changes": {f"lib/m{n}.py": str(n) for n in range(12)}}]))
        w.ops({"op": "commit", "repo": "widgets", "changes": {"lib/m0.py": None}})
        assert succeeded(await w.sync())
        await w.assert_matches_source("a file deleted in a normal commit")
        w.ops({"op": "commit", "repo": "widgets", "changes": {"README.md": "# slim\n"}, "replace": True, "parent": None})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after a force push that dropped most of the files")

    async def test_tc_ghsim_chg_005_deleted_issue_is_removed(self, github_world: GithubWorld) -> None:
        w = github_world
        ops = [{"op": "add_issue", "repo": "widgets", "title": "Keep"}, {"op": "add_issue", "repo": "widgets", "title": "Delete me", "key": "gone"}]
        assert succeeded(await w.start(acme(), ops=ops))
        w.ops({"op": "delete_issue", "issue": "gone"})
        assert succeeded(await w.sync())
        assert await names(w, "TICKET") == {"Keep"}
        await w.assert_matches_source("after the issue was deleted")

    async def test_tc_ghsim_chg_006_transferred_issue_leaves(self, github_world: GithubWorld) -> None:
        w = github_world
        assert succeeded(await w.start(acme(repos=2), ops=[{"op": "add_issue", "repo": "widgets", "title": "CVE details", "key": "cve"}]))
        w.ops({"op": "transfer_issue", "issue": "cve", "to": "gadgets"})
        assert succeeded(await w.sync())
        assert not await names(w, "TICKET"), "an issue transferred out of the repository is still synced"
        await w.assert_matches_source("after the transfer", allow_empty=False)

    async def test_tc_ghsim_chg_007_deleted_repository_is_removed(self, github_world: GithubWorld) -> None:
        w = github_world
        assert succeeded(await w.start(acme(), ops=rich()))
        w.ops({"op": "delete_repo", "repo": "widgets"})
        assert succeeded(await w.sync())
        assert not await w.records(), "records of a deleted repository are still live"

    async def test_tc_ghsim_chg_008_sync_account_leaves_the_organisation(self, github_world: GithubWorld) -> None:
        w = github_world
        assert succeeded(await w.start(acme(), ops=rich()))
        w.ops({"op": "set_member", "org": "acme", "user": "admin", "role": None})
        await w.sync()
        assert not await w.records(), "the sync account lost access and its records are still live"

    async def test_tc_ghsim_chg_009_another_repository_selected(self, github_world: GithubWorld) -> None:
        w = github_world
        ops = [{"op": "add_issue", "repo": "widgets", "title": "Old repo issue"}, {"op": "add_issue", "repo": "gadgets", "title": "New repo issue"}]
        assert succeeded(await w.start(acme(repos=2), ops=ops))
        assert succeeded(await w.restart(repo_filter("acme/gadgets")))
        assert await names(w, "TICKET") == {"New repo issue"}
        await w.assert_matches_source("after another repository was selected")

    async def test_tc_ghsim_chg_010_renamed_repository(self, github_world: GithubWorld) -> None:
        w = github_world
        assert succeeded(await w.start(acme(), ops=rich()))
        before = {r["externalRecordId"] for r in await w.records()}
        w.ops({"op": "rename_repo", "repo": "widgets", "name": "widgets-2"})
        assert succeeded(await w.sync())  # the filter still names acme/widgets: GitHub answers 301
        assert {r["externalRecordId"] for r in await w.records()} == before, "a renamed repository changed the records"

    async def test_tc_ghsim_chg_011_issues_turned_off_and_on(self, github_world: GithubWorld) -> None:
        w = github_world
        ops = [{"op": "add_issue", "repo": "widgets", "title": "Issue"}, {"op": "add_pull", "repo": "widgets", "title": "PR"}]
        assert succeeded(await w.start(acme(), ops=ops))
        w.ops({"op": "set_repo", "repo": "widgets", "has_issues": False})
        assert succeeded(await w.sync())
        assert not await names(w, "TICKET")
        await w.assert_matches_source("issues off")
        w.ops({"op": "set_repo", "repo": "widgets", "has_issues": True})
        assert succeeded(await w.sync())
        assert await names(w, "TICKET") == {"Issue"}
        await w.assert_matches_source("issues on again")

    async def test_tc_ghsim_chg_012_chosen_branch(self, github_world: GithubWorld) -> None:
        w = github_world
        ops = [{"op": "create_branch", "repo": "widgets", "branch": "release"},
               {"op": "commit", "repo": "widgets", "branch": "release", "changes": {
                   "src/app.py": "print('release')\n", "src/release_only.py": "x = 1\n", "README.md": None}}]
        assert succeeded(await w.start(acme(), ops=ops))
        rid = w.repo_id()
        app_id = (await w.record_for(f"/{rid}/blob/src/app.py"))["id"]
        flt = with_filters(repo_filter("acme/widgets"), branch=branch_filter("release"))
        assert succeeded(await w.restart(flt))
        files = {r["externalRecordId"] for r in await w.records() if r["recordType"] == "CODE_FILE"}
        assert files == {f"/{rid}/blob/src/app.py", f"/{rid}/blob/src/release_only.py"}, sorted(files)
        assert (await w.record_for(f"/{rid}/blob/src/app.py"))["id"] == app_id, "switching branch made a new record"
        assert await w.stream(f"/{rid}/blob/src/app.py") == b"print('release')\n"
        await w.assert_matches_source("on the branch")
        assert succeeded(await w.restart(repo_filter("acme/widgets")))
        assert await w.stream(f"/{rid}/blob/src/app.py") == b"print('hi')\n"
        await w.assert_matches_source("back on the default branch")

    async def test_tc_ghsim_chg_013_missing_branch_fails_and_keeps_records(self, github_world: GithubWorld) -> None:
        w = github_world
        ops = [{"op": "create_branch", "repo": "widgets", "branch": "release"},
               {"op": "commit", "repo": "widgets", "branch": "release", "changes": {"src/release_only.py": "x = 1\n"}}]
        flt = with_filters(repo_filter("acme/widgets"), branch=branch_filter("release"))
        assert succeeded(await w.start(acme(), ops=ops, filters=flt, branch="release"))
        before = await w.holdings()
        w.ops({"op": "delete_branch", "repo": "widgets", "branch": "release"},
              {"op": "commit", "repo": "widgets", "changes": {"src/main_only.py": "y = 2\n"}})
        assert not succeeded(await w.sync()), "a run whose branch is gone reported success"
        await w.assert_holdings_kept(before, "after the chosen branch was deleted")

    async def test_tc_ghsim_chg_014_default_branch_moved(self, github_world: GithubWorld) -> None:
        w = github_world
        assert succeeded(await w.start(acme(), ops=[{"op": "commit", "repo": "widgets",
                                                     "changes": {f"lib/m{n}.py": str(n) for n in range(12)}}]))
        w.ops({"op": "commit", "repo": "widgets", "branch": "slim", "changes": {"README.md": "# slim\n"}, "replace": True, "parent": None},
              {"op": "set_repo", "repo": "widgets", "default_branch": "slim"})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the default branch moved")

    async def test_tc_ghsim_chg_015_comments_added_edited_deleted(self, github_world: GithubWorld) -> None:
        w = github_world
        ops = [{"op": "add_issue", "repo": "widgets", "title": "Talk", "key": "talk",
                "comments": [{"user": "alice", "body": "first words", "key": "c1"}, {"user": "bob", "body": "doomed", "key": "c2"}]}]
        assert succeeded(await w.start(acme(), ops=ops))
        ext = f"{w.repo_id()}/issues/1"
        w.ops({"op": "edit_comment", "issue": "talk", "comment": "c1", "body": "edited words"},
              {"op": "delete_comment", "issue": "talk", "comment": "c2"},
              {"op": "comment", "issue": "talk", "user": "alice", "body": "brand new"})
        assert succeeded(await w.sync())
        text = await w.stream_text(ext)
        assert "edited words" in text and "brand new" in text and "first words" not in text and "doomed" not in text, text[:500]

    async def test_tc_ghsim_chg_016_repository_deleted_and_restored(self, github_world: GithubWorld) -> None:
        w = github_world
        assert succeeded(await w.start(acme(), ops=rich()))
        w.ops({"op": "delete_repo", "repo": "widgets"})
        assert succeeded(await w.sync())
        assert not await w.records()
        w.ops({"op": "restore_repo", "repo": "widgets"})
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after the repository came back")

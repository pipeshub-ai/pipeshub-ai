# pyright: ignore-file

"""
GitLab on the simulator – what changes at the source, and what a full sync does about it
=========================================================================================

Create, edit, rename, move, merge, delete and restore every kind of record; the project renamed or deleted; a change
while the connector is disabled; a full sync that removes what is gone and keeps what GitLab still has. After each run
PipesHub equals the oracle for every person. Live twins: TC-INCR-ISSUE-001, TC-INCR-MR-001, TC-INCR-CODE-001.
Harness scenarios: incremental, deletion, changes_while_listing.

  TC-GLSIM-INC-001  a new issue, an edited title, a new comment, a new merge request, a commit: one run equals GitLab
  TC-GLSIM-INC-002  an edited issue and merge request keep their record and take version + 1; no second record
  TC-GLSIM-INC-003  code: a new file, an edit, a deletion, a move to another folder, an emptied folder, in one run
  TC-GLSIM-INC-004  a merge request merged, and a new one closed
  TC-GLSIM-INC-005  an issue closed and reopened: the state follows, the record stays one
  TC-GLSIM-INC-006  a new upload in a description and a new one in an internal note (no record)
  TC-GLSIM-INC-007  a comment edited and a comment deleted: the streamed issue follows
  TC-GLSIM-DEL-001  an issue, a merge request, a file and a folder deleted at the source: gone after the run
  TC-GLSIM-DEL-002  the project deleted: nothing of it is readable afterwards
  TC-GLSIM-DEL-003  the project renamed (the old path answers 301): the run still equals GitLab
  TC-GLSIM-DEL-004  changes while the connector is disabled (an edit, a new issue, a commit) arrive when it is enabled
  TC-GLSIM-DEL-005  a full sync of an unchanged project: nothing is lost, nobody loses access
  TC-GLSIM-DEL-006  a full sync removes what was deleted while the connector was disabled
  TC-GLSIM-DEL-007  a full sync refused the issues (403: the feature is off) takes them as gone; nobody gains; a clean full sync
                    brings them back
  TC-GLSIM-DEL-008  the sync account loses its own access: nobody gains, and the run with access back equals GitLab
  TC-GLSIM-DEL-009  an issue deleted in the interval in which another was created is removed (the count of what was created
                    since is added to the last count)
  TC-GLSIM-DEL-010  an issue deleted while the connector is disabled is removed when it is enabled
"""

import pytest

from connectors.gitlab.sim_instances import base_instance
from connectors.gitlab.sim_world import (
    GitLabWorld,
    fault,
    norm,
    succeeded,
)


async def versions(w: GitLabWorld) -> dict[str, tuple[str, int]]:
    return {norm(str(r["externalRecordId"])): (r["id"], r["version"]) for r in await w.records()}


@pytest.mark.integration
@pytest.mark.gitlab_sim
@pytest.mark.asyncio(loop_scope="session")
class TestGitLabSimChange:

    async def _started(self, w: GitLabWorld, **kw) -> None:
        assert succeeded(await w.start(base_instance(), **kw))
        await w.assert_matches_source("the first sync")

    async def test_tc_glsim_inc_001_create_edit_comment_commit(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        await self._started(w)
        w.ops({"op": "add_issue", "project": "widgets", "title": "Beta", "author": "dev", "description": "[shot]({{upload:shot}})"},
              {"op": "set_issue", "issue": "open", "title": "Open issue renamed"},
              {"op": "add_note", "issue": "open", "author": "dev", "body": "a brand new comment"},
              {"op": "add_mr", "project": "widgets", "title": "Second MR", "author": "dev"},
              {"op": "commit", "project": "widgets", "files": {"src/extra.py": "y = 2\n", "README.md": "# widgets v2\n"}})
        assert succeeded(await w.sync())
        await w.assert_matches_source("an incremental run")
        names = {r["recordName"] for r in await w.records()}
        assert {"Beta", "Open issue renamed", "Second MR", "extra.py"} <= names, sorted(names)
        text = w.stream((await w.record_for(str(w.key("open"))))["id"]).decode(errors="replace")
        assert "a brand new comment" in text, "the new comment is not in the streamed issue"

    async def test_tc_glsim_inc_002_edits_keep_the_record_and_bump_the_version(self, gitlab_world: GitLabWorld, graph_provider) -> None:
        w = gitlab_world
        await self._started(w)
        keys = (str(w.key("open")), str(w.key("mr1")))
        before = {k: await graph_provider.get_record_by_external_id(w.connector_id, k) for k in keys}
        others = {k: v for k, v in (await versions(w)).items() if k not in keys}
        w.ops({"op": "set_issue", "issue": "open", "title": "Open issue v2"}, {"op": "set_mr", "mr": "mr1", "title": "Add feature v2"})
        assert succeeded(await w.sync())
        for k in keys:
            after = await graph_provider.get_record_by_external_id(w.connector_id, k)
            assert after.id == before[k].id, f"{k} got a second record"
            assert after.version == before[k].version + 1, f"{k}: version {before[k].version} -> {after.version}"
            assert after.external_revision_id != before[k].external_revision_id, f"{k}: the revision did not move"
        assert {k: v for k, v in (await versions(w)).items() if k not in keys} == others, "a record nobody edited changed"
        await w.assert_matches_source("after the edits")

    async def test_tc_glsim_inc_003_code_changes(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        await self._started(w)
        w.ops({"op": "commit", "project": "widgets", "files": {
            "src/new_module.py": "z = 3\n", "src/app.py": "print('changed')\n", "README.md": None,
            "src/util/helpers.py": None, "src/util2/moved.py": "def h():\n    return 1\n"}})
        assert succeeded(await w.sync())
        await w.assert_matches_source("new, edited, deleted, moved files and an emptied folder")
        names = {r["recordName"] for r in await w.records()}
        assert "new_module.py" in names and "moved.py" in names and "helpers.py" not in names and "README.md" not in names

    async def test_tc_glsim_inc_004_merge_request_lifecycle(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        await self._started(w)
        w.ops({"op": "merge_mr", "mr": "mr1"})
        assert succeeded(await w.sync())
        await w.assert_matches_source("a merged merge request")
        record = await w.record_for(str(w.key("mr1")))
        assert record and record["recordName"] == "Add feature"
        w.ops({"op": "add_mr", "project": "widgets", "title": "Closed one", "author": "dev", "key": "mr2"},
              {"op": "set_mr", "mr": "mr2", "state": "closed"})
        assert succeeded(await w.sync())
        await w.assert_matches_source("a closed merge request")

    async def test_tc_glsim_inc_005_issue_closed_and_reopened(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        await self._started(w)
        w.ops({"op": "set_issue", "issue": "open", "state": "closed"})
        assert succeeded(await w.sync())
        await w.assert_matches_source("an issue closed")
        w.ops({"op": "set_issue", "issue": "open", "state": "opened"})
        assert succeeded(await w.sync())
        await w.assert_matches_source("an issue reopened")
        assert len([r for r in await w.records() if r["recordName"] == "Open issue"]) == 1

    async def test_tc_glsim_inc_006_new_uploads(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        await self._started(w)
        w.ops({"op": "add_upload", "project": "widgets", "key": "pub2", "filename": "pub2.txt", "text": "public two"},
              {"op": "add_upload", "project": "widgets", "key": "int2", "filename": "int2.txt", "text": "internal two"},
              {"op": "add_issue", "project": "widgets", "title": "With upload", "author": "dev", "description": "[p]({{upload:pub2}})",
               "notes": [{"author": "rita", "body": "[i]({{upload:int2}})", "internal": True}]})
        assert succeeded(await w.sync())
        await w.assert_matches_source("a public and an internal-only upload")
        assert any("pub2.txt" in str(r["externalRecordId"]) for r in await w.records())
        assert not any("int2.txt" in str(r["externalRecordId"]) for r in await w.records())

    async def test_tc_glsim_inc_007_comment_edited_and_deleted(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        await self._started(w)
        issue = await w.record_for(str(w.key("open")))
        assert "a public comment" in w.stream(issue["id"]).decode(errors="replace")
        w.ops({"op": "edit_note", "issue": "open", "note": "pub", "body": "an edited comment"})
        assert succeeded(await w.sync())
        text = w.stream(issue["id"]).decode(errors="replace")
        assert "an edited comment" in text and "a public comment" not in text, "the edited comment is not what is streamed"
        w.ops({"op": "delete_note", "issue": "open", "note": "pub"})
        assert succeeded(await w.sync())
        assert "an edited comment" not in w.stream(issue["id"]).decode(errors="replace"), "a deleted comment is still streamed"

    # ---- deletion ---------------------------------------------------------------------------------------

    async def test_tc_glsim_del_001_deleted_at_the_source(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        await self._started(w)
        w.ops({"op": "delete_issue", "issue": "closed"}, {"op": "delete_mr", "mr": "mr1"},
              {"op": "commit", "project": "widgets", "files": {"src/util/helpers.py": None}})
        assert succeeded(await w.sync())
        await w.assert_matches_source("an issue, a merge request, a file and its emptied folder deleted")
        names = {r["recordName"] for r in await w.records()}
        assert not names & {"Closed issue", "Add feature", "helpers.py"}, sorted(names)

    async def test_tc_glsim_del_002_project_deleted(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        await self._started(w)
        w.ops({"op": "delete_project", "project": "widgets"})
        await w.sync()
        held = await w.holdings()
        readable = {who: v for who, v in held.items() if who != "records" and v}
        assert not readable, f"people still read a deleted project's records: { {k: len(v) for k, v in readable.items()} }"

    async def test_tc_glsim_del_003_project_renamed(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        await self._started(w)
        w.ops({"op": "rename_project", "project": "widgets", "path": "gadgets"})
        assert succeeded(await w.sync()), "the run after the rename did not succeed"
        w.scope.projects = ["acme/gadgets"]
        await w.assert_matches_source("after the project was renamed (the old path answers 301)")

    async def test_tc_glsim_del_004_changes_while_disabled(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        await self._started(w)
        await w.disable()
        w.ops({"op": "set_issue", "issue": "open", "title": "Open issue while off"},
              {"op": "add_issue", "project": "widgets", "title": "Added while off", "author": "dev"},
              {"op": "commit", "project": "widgets", "files": {"src/off.py": "z = 0\n"}})
        w.ops({"op": "advance", "seconds": 180})
        assert succeeded(await w.enable())
        await w.assert_matches_source("after the connector was enabled again")

    async def test_tc_glsim_del_009_a_deletion_is_not_hidden_by_a_creation(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        await self._started(w)
        w.ops({"op": "delete_issue", "issue": "closed"}, {"op": "add_issue", "project": "widgets", "title": "Added", "author": "dev"})
        assert succeeded(await w.sync())
        await w.assert_matches_source("a deletion in the interval in which an issue was created")

    async def test_tc_glsim_del_010_a_deletion_while_disabled(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        await self._started(w)
        await w.disable()
        w.ops({"op": "delete_issue", "issue": "closed"})
        w.ops({"op": "advance", "seconds": 180})
        assert succeeded(await w.enable())
        await w.assert_matches_source("a deletion made while the connector was disabled")

    async def test_tc_glsim_del_005_full_sync_of_an_unchanged_project(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        await self._started(w)
        held = await w.holdings()
        assert succeeded(await w.sync(full=True))
        await w.assert_holdings_kept(held, "after a full sync of an unchanged project")
        await w.assert_matches_source("after a full sync")

    async def test_tc_glsim_del_006_full_sync_removes_what_is_gone(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        await self._started(w)
        await w.disable()
        w.ops({"op": "delete_issue", "issue": "closed"}, {"op": "delete_mr", "mr": "mr1"},
              {"op": "commit", "project": "widgets", "files": {"src/app.py": None}})
        w.ops({"op": "advance", "seconds": 180})
        assert succeeded(await w.enable())
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after a full sync of what was deleted while disabled")

    async def test_tc_glsim_del_007_full_sync_refused_the_issues(self, gitlab_world: GitLabWorld) -> None:
        # GitLab answers 403 for the issues of a project that has the feature off, so a full sync that is refused the
        # listing takes the issues as gone: they leave, for everybody, and nobody gains anything. Once GitLab
        # lists them again a full sync brings them all back.
        w = gitlab_world
        await self._started(w)
        held = await w.holdings()
        w.faults(fault(r"^/api/v4/projects/\d+/issues$", 403))
        await w.sync(full=True)
        w.clear_faults(every_rule=True)
        after = await w.holdings()
        gains = {who: sorted(after[who] - held[who]) for who in held if who != "records" and after[who] - held[who]}
        assert not gains, f"somebody gained access: {gains}"
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after a clean full sync")

    async def test_tc_glsim_del_008_project_unreadable_to_the_sync_account(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        await self._started(w)
        held = await w.holdings()
        # The sync account reads the project as an owner of the group; without that it reads nothing.
        w.ops({"op": "set_member", "group": "acme", "user": "admin", "level": None})
        await w.sync()
        after = await w.holdings()
        gains = {who: sorted(after[who] - held[who]) for who in held if who != "records" and after[who] - held[who]}
        assert not gains, f"somebody gained access when the sync account lost its own: {gains}"
        w.ops({"op": "set_member", "group": "acme", "user": "admin", "level": "owner"})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the sync account has its access back")

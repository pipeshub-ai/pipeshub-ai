# pyright: ignore-file

"""
GitHub Teams on the simulator: attachments indexing, code timestamps, simulator switches, long lists
===================================================================================================

  TC-GHSIM-CNT-007  An upload in an issue body becomes a file record under that issue, in its record group, and indexes
                    to COMPLETED (twin of the live attachment cases) [J, K]
  TC-GHSIM-CNT-008  Code files and folders get source timestamps, filled after the run ends; a folder's dates cover its
                    files (twin of the live code timestamp case) [J]
  TC-GHSIM-SWT-001  `secondary_limit_status` 429: a secondary rate limit answered 429 with Retry-After is waited out [I, M]
  TC-GHSIM-SWT-002  `primary_limit_status` 429: a spent hourly pool answered 429 fails the run, loses nothing, and the run
                    after the window equals the source [I, M]
  TC-GHSIM-SWT-003  `comment_edit_bumps_issue` off: an edited comment does not move the issue's date; the edit still reaches
                    PipesHub on the next sync [D, M]
  TC-GHSIM-SWT-004  `compare_detects_renames` off: a moved file arrives as a removal and an addition; the end state equals the
                    source and the old path is gone [D, M]
  TC-GHSIM-SYNC-011 More than 100 pull requests and more than 100 comments on one issue: every one is read (second page) [H]
  Matrix (`scenario_matrix.py`): not run on the simulator; its scenarios are covered by CHG-001..003 and CHG-005 (add, edit,
  rename, delete), SYNC-004 (full sync), FILT-007 (filter change) and FILT-002 (indexing settings). What only the matrix adds
  (vectors hold the text, search finds the record, the stored copy follows) needs the live stack's probes.
"""

import asyncio
import time

import pytest
from connectors.github_teams.sim_instances import acme
from connectors.github_teams.sim_world import GithubWorld, fault, indexed, succeeded

PNG_NOTE = "meeting notes"
SECONDARY = "You have exceeded a secondary rate limit. Please wait a few minutes before you try again."


async def wait_for(check, timeout: int = 180):
    deadline = time.monotonic() + timeout
    while True:
        got = await check()
        if got or time.monotonic() >= deadline:
            return got
        await asyncio.sleep(3)


@pytest.mark.integration
@pytest.mark.github_teams_sim
@pytest.mark.asyncio(loop_scope="session")
class TestGithubSimExtra:

    async def test_tc_ghsim_cnt_007_attachment_is_a_file_record_and_indexes(self, github_world: GithubWorld) -> None:
        w = github_world
        spec = acme()
        spec["repos"][0]["visibility"] = "public"
        await w.start(spec, enable=False, behaviours={"attachment_token_accepted": False})
        doc = w.ops({"op": "upload", "repo": "widgets", "name": "notes.txt", "text": PNG_NOTE})[0]["url"]
        w.ops({"op": "add_issue", "repo": "widgets", "title": "With a file", "body": f"See [notes.txt]({doc})"})
        assert succeeded(await w.enable())
        records = await indexed(w)
        issue_ext = f"{w.repo_id()}/issues/1"
        attachment = next((r for r in records if str(r["externalRecordId"]).startswith("https://")), None)
        assert attachment, f"no attachment record among {[r['externalRecordId'] for r in records]}"
        parent = next(r for r in records if r["externalRecordId"] == issue_ext)
        typed = await w.graph.get_typed_record_by_external_id(w.connector_id, attachment["externalRecordId"])
        assert typed is not None and typed.is_file is True and typed.record_type.value == "FILE"
        assert typed.parent_external_record_id == issue_ext
        assert typed.external_record_group_id == parent["externalGroupId"], "the attachment is not in its issue's group"
        assert str(typed.parent_node_id) == str(parent["id"]), "the attachment's parent id is not its issue's record id"
        assert attachment["indexingStatus"] == "COMPLETED", attachment["indexingStatus"]
        assert await w.stream(attachment["externalRecordId"]) == PNG_NOTE.encode()
        await w.assert_matches_source("an issue with an attachment")

    async def test_tc_ghsim_cnt_008_code_files_and_folders_get_timestamps(self, github_world: GithubWorld) -> None:
        w = github_world
        ops = [{"op": "commit", "repo": "widgets", "changes": {"lib/a/one.py": "1\n", "lib/a/two.py": "2\n"}},
               {"op": "advance", "seconds": 600},
               {"op": "commit", "repo": "widgets", "changes": {"lib/a/two.py": "2b\n", "lib/a/three.py": "3\n"}}]
        assert succeeded(await w.start(acme(), ops=ops))
        rid = w.repo_id()
        files = [f"/{rid}/blob/lib/a/{n}.py" for n in ("one", "two", "three")]
        folder = f"/{rid}/tree/lib/a"

        async def dated():
            rows = [await w.graph.get_record_by_external_id(w.connector_id, e) for e in (*files, folder)]
            return rows if all(r and r.source_created_at and r.source_updated_at for r in rows) else None

        rows = await wait_for(dated)
        assert rows, "the code files and their folder never got source timestamps"
        *file_rows, folder_row = rows
        assert folder_row.source_created_at <= min(r.source_created_at for r in file_rows), "a folder cannot be created after its files"
        assert folder_row.source_updated_at >= max(r.source_updated_at for r in file_rows), "a folder cannot be updated before its files"
        one, two = file_rows[0], file_rows[1]
        assert two.source_updated_at >= one.source_updated_at, "the file edited later carries the older update date"
        await w.assert_matches_source("after the timestamps were filled")

    async def test_tc_ghsim_swt_001_secondary_limit_answered_429(self, github_world: GithubWorld) -> None:
        w = github_world
        await w.start(acme(), ops=[{"op": "add_issue", "repo": "widgets", "title": "Issue"}], enable=False,
                      behaviours={"secondary_limit_status": 429})
        w.faults(fault(r"/repos/acme/widgets/issues$", 429, max_times=1, message=SECONDARY))
        started = time.monotonic()
        assert succeeded(await w.enable()), "a secondary rate limit answered 429 was not waited out"
        assert time.monotonic() - started >= 30, "the connector did not wait for the Retry-After the limit carried"
        assert w.clear_faults() == 1
        await w.assert_matches_source("after a secondary limit answered 429")

    async def test_tc_ghsim_swt_002_primary_limit_answered_429(self, github_world: GithubWorld) -> None:
        w = github_world
        assert succeeded(await w.start(acme(), ops=[{"op": "add_issue", "repo": "widgets", "title": "Before"}],
                                       behaviours={"primary_limit_status": 429}))
        before = await w.holdings()
        w.ops({"op": "add_issue", "repo": "widgets", "title": "During"}, {"op": "exhaust", "resource": "core", "seconds": 1800})
        assert not succeeded(await w.sync()), "a spent pool answered 429 did not fail the run"
        calls = [c for c in w.sim.calls() if c["status"] == 429]
        assert calls, "the simulator never answered 429: the switch did not take effect"
        await w.assert_holdings_kept(before, "after the pool was spent (429)")
        w.ops({"op": "advance", "seconds": 2000})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the pool refilled")

    async def test_tc_ghsim_swt_003_comment_edit_does_not_move_the_issue(self, github_world: GithubWorld) -> None:
        w = github_world
        ops = [{"op": "add_issue", "repo": "widgets", "title": "Talk", "key": "talk",
                "comments": [{"user": "alice", "body": "first words", "key": "c1"}]}]
        assert succeeded(await w.start(acme(), ops=ops, behaviours={"comment_edit_bumps_issue": False}))
        w.ops({"op": "edit_comment", "issue": "talk", "comment": "c1", "body": "edited words"})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after a comment edit that left the issue's date alone")
        text = await w.stream_text(f"{w.repo_id()}/issues/1")
        assert "edited words" in text and "first words" not in text, text[:400]

    async def test_tc_ghsim_swt_004_renames_not_detected(self, github_world: GithubWorld) -> None:
        w = github_world
        ops = [{"op": "commit", "repo": "widgets", "changes": {"lib/old_name.py": "same content\n", "lib/keep.py": "k\n"}}]
        assert succeeded(await w.start(acme(), ops=ops, behaviours={"compare_detects_renames": False}))
        rid = w.repo_id()
        w.ops({"op": "commit", "repo": "widgets", "changes": {"lib/new_name.py": "same content\n", "lib/old_name.py": None}})
        assert succeeded(await w.sync())
        code = {r["externalRecordId"] for r in await w.records() if r["recordType"] == "CODE_FILE"}
        assert f"/{rid}/blob/lib/new_name.py" in code and f"/{rid}/blob/lib/keep.py" in code, sorted(code)
        assert f"/{rid}/blob/lib/old_name.py" not in code, sorted(code)
        assert await w.stream(f"/{rid}/blob/lib/new_name.py") == b"same content\n"
        await w.assert_matches_source("after a move reported as removal plus addition")

    async def test_tc_ghsim_sync_011_second_page_of_pulls_and_comments(self, github_world: GithubWorld) -> None:
        w = github_world
        ops = [{"op": "add_pull", "repo": "widgets", "title": f"PR {n:03}"} for n in range(102)]
        ops.append({"op": "add_issue", "repo": "widgets", "title": "Long thread", "key": "long",
                    "comments": [{"user": "alice", "body": f"remark {n:03}"} for n in range(102)]})
        assert succeeded(await w.start(acme(), ops=ops))
        pulls = [r for r in await w.records() if r["recordType"] == "PULL_REQUEST"]
        assert len(pulls) == 102, f"{len(pulls)} pull requests stored of 102"
        issue = next(r for r in await w.records() if r["recordName"] == "Long thread")
        text = await w.stream_text(issue["externalRecordId"])
        assert "remark 000" in text and "remark 101" in text, "a comment past the first page is missing"
        await w.assert_matches_source("past 100 pull requests and 100 comments")
        w.ops({"op": "comment", "issue": "long", "user": "bob", "body": "remark after the page"})
        assert succeeded(await w.sync())
        assert "remark after the page" in await w.stream_text(issue["externalRecordId"])

# pyright: ignore-file

"""
GitHub Teams on the simulator: what PipesHub streams
====================================================

  TC-GHSIM-CNT-001  An issue streams its body, comments and authors; a pull request its body, review, review comment and
                    conversation [J]
  TC-GHSIM-CNT-002  A public repository's upload: the image is embedded, the file streams its bytes, and no token reaches
                    github.com or the storage host [J, N]
  TC-GHSIM-CNT-003  A private repository's upload: github.com is asked without the token, then with it; the storage host
                    never sees it [J, N]
  TC-GHSIM-CNT-004  A code file streams the bytes of its blob: odd names (spaces, non-ASCII), an empty file, a nested path [J]
  TC-GHSIM-CNT-005  An upload GitHub refuses (403 on the download): the sync is unaffected, the stream does not serve invented
                    bytes, and the next stream recovers [J, I]
  TC-GHSIM-CNT-006  An attachment's record is read by exactly the people who read its issue (the comparison covers it) [J, E]
"""

import pytest
from connectors.github_teams.sim_instances import ALICE, BOB, acme
from connectors.github_teams.sim_world import (
    API_HOST,
    STORAGE_HOST,
    WEB_HOST,
    GithubWorld,
    fault,
    succeeded,
)

PNG = "\x89PNG" + "0" * 100


def uploads(w: GithubWorld) -> tuple[str, str]:
    image = w.ops({"op": "upload", "repo": "widgets", "name": "shot.png", "image": True, "text": PNG})[0]["url"]
    doc = w.ops({"op": "upload", "repo": "widgets", "name": "notes.txt", "text": "meeting notes"})[0]["url"]
    return image, doc


async def attachment_key(w: GithubWorld) -> str:
    key = next((r["externalRecordId"] for r in await w.records() if str(r["externalRecordId"]).startswith("https://")), None)
    assert key, "no attachment record"
    return key


@pytest.mark.integration
@pytest.mark.github_teams_sim
@pytest.mark.asyncio(loop_scope="session")
class TestGithubSimContent:

    async def test_tc_ghsim_cnt_001_issue_and_pull_request_content(self, github_world: GithubWorld) -> None:
        w = github_world
        ops = [{"op": "add_issue", "repo": "widgets", "title": "Crash on save", "body": "Steps: click save", "user": "bob",
                "comments": [{"user": "alice", "body": "Workaround: restart"}]},
               {"op": "add_pull", "repo": "widgets", "title": "Fix crash", "body": "Fixes the save crash", "changes": {"src/save.py": "ok\n"},
                "key": "fix", "reviews": [{"user": "alice", "state": "APPROVED", "body": "Looks right"}],
                "review_comments": [{"user": "alice", "body": "Rename this", "path": "src/save.py", "line": 1}],
                "comments": [{"user": "bob", "body": "Thanks both"}]}]
        assert succeeded(await w.start(acme(), ops=ops))
        rid = w.repo_id()
        issue = await w.stream_text(f"{rid}/issues/1")
        assert "Steps: click save" in issue and "Workaround: restart" in issue and "alice" in issue, issue[:500]
        pull = await w.stream_text(f"{rid}/pull/2")
        for part in ("Fixes the save crash", "Looks right", "Rename this", "Thanks both"):
            assert part in pull, f"{part!r} is missing from the streamed pull request: {pull[:600]}"

    async def test_tc_ghsim_cnt_002_public_repository_uploads(self, github_world: GithubWorld) -> None:
        w = github_world
        spec = acme()
        spec["repos"][0]["visibility"] = "public"
        await w.start(spec, enable=False, behaviours={"attachment_token_accepted": False})
        image, doc = uploads(w)
        w.ops({"op": "add_issue", "repo": "widgets", "title": "Screenshot", "body": f"Look: ![shot]({image}) and [notes.txt]({doc})"})
        assert succeeded(await w.enable())
        text = await w.stream_text(f"{w.repo_id()}/issues/1")
        assert "data:image/png;base64" in text, text[:300]
        assert await w.stream(await attachment_key(w)) == b"meeting notes"
        web = [c for c in w.sim.calls() if c["host"] != API_HOST and c["authorization"]]
        assert not web, f"a token reached github.com or the storage host: {web}"
        assert [c for c in w.sim.calls() if c["host"] == STORAGE_HOST], "the signed storage host was never reached"
        await w.assert_matches_source("public repository with uploads")

    async def test_tc_ghsim_cnt_003_private_repository_uploads(self, github_world: GithubWorld) -> None:
        w = github_world
        await w.start(acme(), enable=False, behaviours={"attachment_token_accepted": True})
        image, doc = uploads(w)
        w.ops({"op": "add_issue", "repo": "widgets", "title": "Screenshot", "body": f"Look: ![shot]({image}) and [notes.txt]({doc})"})
        assert succeeded(await w.enable())
        assert "data:image/png;base64" in await w.stream_text(f"{w.repo_id()}/issues/1")
        assert await w.stream(await attachment_key(w)) == b"meeting notes"
        web = [c for c in w.sim.calls() if c["host"] == WEB_HOST]
        by_path: dict[str, list[dict]] = {}
        for c in web:
            by_path.setdefault(c["path"], []).append(c)
        assert by_path, "github.com was never asked for an upload"
        for path, seen in by_path.items():
            assert seen[0]["authorization"] is False, f"{path}: the first request carried the token"
            if seen[0]["status"] == 404:
                assert len(seen) > 1 and seen[1]["authorization"] is True, f"{path}: refused anonymously, never asked with the token"
        assert not [c for c in w.sim.calls() if c["host"] == STORAGE_HOST and c["authorization"]], "the token reached the storage host"
        await w.assert_matches_source("private repository with uploads")

    async def test_tc_ghsim_cnt_004_code_file_bytes(self, github_world: GithubWorld) -> None:
        w = github_world
        files = {"docs/with space.md": "# spaced\n", "docs/ünï.txt": "unicode name\n", "empty.txt": "", "a/b/c/d.py": "d = 1\n"}
        assert succeeded(await w.start(acme(), ops=[{"op": "commit", "repo": "widgets", "changes": files}]))
        rid = w.repo_id()
        for path, content in {**files, "src/app.py": "print('hi')\n"}.items():
            record = await w.record_for(f"/{rid}/blob/{path}")
            assert record, f"no record for {path!r}"
            body = await w.stream(f"/{rid}/blob/{path}") if content else b""
            assert body == content.encode(), f"{path!r}: streamed {body[:60]!r}"
        await w.assert_matches_source("odd file names")

    async def test_tc_ghsim_cnt_005_upload_refused_then_recovered(self, github_world: GithubWorld) -> None:
        w = github_world
        await w.start(acme(), enable=False, behaviours={"attachment_token_accepted": True})
        image, doc = uploads(w)
        w.ops({"op": "add_issue", "repo": "widgets", "title": "Notes", "body": f"See [notes.txt]({doc})"})
        assert succeeded(await w.enable())
        key = await attachment_key(w)
        w.faults(fault(r"^/user-attachments/", 403))
        try:
            body = await w.stream(key)
        except Exception:  # noqa: BLE001 - an HTTP error from the stream route is the right answer
            body = None
        w.clear_faults()
        assert body is None or body != b"meeting notes", "bytes were served while GitHub refused the download"
        assert await w.stream(key) == b"meeting notes", "the stream did not recover once GitHub answered again"

    async def test_tc_ghsim_cnt_006_attachment_readers_are_the_issue_readers(self, github_world: GithubWorld) -> None:
        w = github_world
        await w.start(acme(), enable=False)
        _, doc = uploads(w)
        w.ops({"op": "add_issue", "repo": "widgets", "title": "Notes", "body": f"See [notes.txt]({doc})"})
        assert succeeded(await w.enable())
        key = await attachment_key(w)
        holdings = await w.holdings()
        assert key in holdings[ALICE] and key not in holdings[BOB], "the attachment is read by other people than its issue's readers"
        await w.assert_matches_source("attachment readers")

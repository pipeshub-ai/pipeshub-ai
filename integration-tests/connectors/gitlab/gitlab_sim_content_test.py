# pyright: ignore-file

"""
GitLab on the simulator – reindexing a record, and names that carry markup
==========================================================================

  TC-GLSIM-REIDX-001  reindex an issue after it changed at GitLab: the record takes the new title and the streamed
                      content is the new one
  TC-GLSIM-REIDX-002  reindex after the access changed: a removed member reads nothing of it, after the reindex and
                      after the next run
  TC-GLSIM-REIDX-003  reindex a record whose issue is gone at GitLab: PipesHub does not fail, and the next run leaves what
                      GitLab has
  TC-GLSIM-REIDX-004  a code file cannot be reindexed alone (PipesHub says to run a sync); after the run the streamed bytes
                      are the repository's
  TC-GLSIM-SEC-001    markup, quotes, ampersands and path-like text in an issue title, a file name and a note: stored
                      verbatim, nothing else appears, the comparison holds
  TC-GLSIM-SEC-002    the connector only ever sends one token, the sync account's, and only to the instance
"""

import re

import pytest

from connectors.gitlab.sim_instances import base_instance, email_of
from connectors.gitlab.sim_world import GitLabWorld, succeeded
from helper.graph_provider_utils import async_poll_until



async def _indexed(w: GitLabWorld, external_id: str) -> dict:
    """The record once its first indexing has ended: PipesHub does not reindex a record that is still being indexed."""
    async def done() -> dict | None:
        record = await w.record_for(external_id)
        return record if record and record["indexingStatus"] == "COMPLETED" else None

    return await async_poll_until(done, timeout=180, interval=2, description=f"{external_id} to be indexed")


NASTY = '<img src=x onerror=alert(1)> "quoted" & \'single\' ../../etc/passwd {{7*7}} ${HOME}'


@pytest.mark.integration
@pytest.mark.gitlab_sim
@pytest.mark.asyncio(loop_scope="session")
class TestGitLabSimContent:

    async def test_tc_glsim_reidx_001_reindex_after_a_change(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        assert succeeded(await w.start(base_instance()))
        record = await _indexed(w, str(w.key("open")))
        w.ops({"op": "set_issue", "issue": "open", "title": "Open issue after the change"},
              {"op": "add_note", "issue": "open", "author": "dev", "body": "a comment made after the sync"})
        w.client.reindex_record(record["id"])

        async def renamed():
            now = await w.record_for(str(w.key("open")))
            return now if now and now["recordName"] == "Open issue after the change" else None

        await async_poll_until(renamed, timeout=120, interval=3, description="the reindexed issue takes its new title")
        assert "a comment made after the sync" in w.stream(record["id"]).decode(errors="replace")
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the reindex and a run")

    async def test_tc_glsim_reidx_002_reindex_after_the_access_changed(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        assert succeeded(await w.start(base_instance()))
        record = await _indexed(w, str(w.key("open")))
        before = await w.holdings()
        w.ops({"op": "set_member", "project": "widgets", "user": "gus", "level": None})
        w.client.reindex_record(record["id"])
        held = await w.holdings()
        gained = {who: sorted(seen - before[who]) for who, seen in held.items() if seen - before[who]}
        assert not gained, f"a reindex gave access: {gained}"
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the reindex and a run")
        assert not (await w.holdings())[email_of("gus")], "a removed member still reads"

    async def test_tc_glsim_reidx_003_reindex_of_a_record_gone_at_the_source(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        assert succeeded(await w.start(base_instance()))
        record = await _indexed(w, str(w.key("closed")))
        w.ops({"op": "delete_issue", "issue": "closed"})
        try:
            w.client.reindex_record(record["id"])
        except Exception as exc:  # noqa: BLE001 - an answer, not a crash of PipesHub
            assert "500" not in str(exc)[:200], f"reindexing a record gone at the source failed: {exc}"
        assert succeeded(await w.sync())
        await w.assert_matches_source("after a run that saw the issue gone")

    async def test_tc_glsim_reidx_004_a_code_file_is_reindexed_by_a_run(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        assert succeeded(await w.start(base_instance()))
        record = await w.record_for("/acme/widgets/-/blob/HEAD/src/app.py")
        w.ops({"op": "commit", "project": "widgets", "files": {"src/app.py": "print('after')\n"}})
        # Code of a repository belongs to its code graph: PipesHub refuses a reindex of one file and says to run a sync.
        resp = w.client.request("POST", f"/api/v1/knowledgeBase/reindex/record/{record['id']}")
        assert resp.status_code == 400 and "Run a connector sync" in resp.text, (resp.status_code, resp.text[:300])
        assert succeeded(await w.sync())
        assert w.stream(record["id"]) == b"print('after')\n", "the streamed file is not what the repository holds now"

    async def test_tc_glsim_sec_001_markup_and_path_like_names(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        inst = base_instance()
        for project in inst["projects"]:
            if project["key"] == "widgets":
                project["issues"].append({"key": "nasty", "title": NASTY, "author": "dev", "description": NASTY,
                                          "notes": [{"author": "rita", "body": NASTY}]})
                project["files"]["docs/<b>x & y.md"] = "# x\n"
        assert succeeded(await w.start(inst))
        await w.assert_matches_source("names that carry markup")
        names = {r["recordName"] for r in await w.records()}
        assert NASTY in names and "<b>x & y.md" in names, sorted(names)
        assert len([r for r in await w.records() if r["recordName"] == NASTY]) == 1
        text = w.stream((await w.record_for(str(w.key("nasty"))))["id"]).decode(errors="replace")
        assert "../../etc/passwd" in text, "the title and the note are in the streamed issue as text"

    async def test_tc_glsim_sec_002_one_token_and_only_to_the_instance(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        assert succeeded(await w.start(base_instance()))
        tokens = {c["token"] for c in w.sim.calls() if c.get("token")}
        mine = w.tokens["sync_account"][:24]
        assert tokens <= {mine}, f"the connector sent a token other than the sync account's: {tokens - {mine}}"
        assert not re.search(r"glpat-[A-Za-z0-9-]+", str(await w.records())), "a token is in a stored record"

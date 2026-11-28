# pyright: ignore-file

"""
GitLab on the simulator: sync points and indexing
=================================================

  TC-GLSIM-CKPT-001  One sync point per data group of a project (work items, merge requests, code repository), each with a
                     time; the code point is the branch head, and after a commit and a sync both move on (twin of the live
                     sync point case) [C, D]
  TC-GLSIM-IDX-001   An issue, a merge request and a code file all leave the indexing pipeline: COMPLETED, none left running
                     or failed (twin of the live indexing case) [B, K]
  Not covered: OAuth refresh and the authorization-code exchange. The connector's token and authorize addresses are the
  fixed https://gitlab.com/oauth/... (connector.py, GITLAB_CLOUD_URL), so they reach the simulator only through a TLS front
  aliasing gitlab.com, which this suite does not build.
"""

import asyncio
import time

import pytest
from connectors.gitlab.gitlab_test_utils import branch_head_sha
from connectors.gitlab.sim_instances import base_instance
from connectors.gitlab.sim_world import GitLabWorld, succeeded

BUSY = {"NOT_STARTED", "QUEUED", "IN_PROGRESS", None}


@pytest.mark.integration
@pytest.mark.gitlab_sim
@pytest.mark.asyncio(loop_scope="session")
class TestGitLabSimExtra:

    async def _points(self, w: GitLabWorld, graph_provider) -> dict:
        project = w.key("widgets")
        out = {}
        for kind in ("work-items", "merge-requests", "code-repository"):
            out[kind] = await graph_provider.get_sync_point(w.connector_id, f"GITLAB/{project}-{kind}/")
        return out

    async def test_tc_glsim_ckpt_001_sync_points(self, gitlab_world: GitLabWorld, graph_provider) -> None:
        w = gitlab_world
        assert succeeded(await w.start(base_instance()))
        head = await branch_head_sha(w.rest(), "acme/widgets", "main")
        assert head, "the simulator named no head for the branch"
        points = await self._points(w, graph_provider)
        for kind in ("work-items", "merge-requests"):
            assert points[kind], f"no sync point stored for {kind}"
            assert points[kind].get("last_sync_time"), f"{kind} has no last_sync_time"
        assert points["code-repository"], "no sync point stored for the code repository"
        assert points["code-repository"].get("last_commit_sha") == head, (
            f"code checkpoint {points['code-repository'].get('last_commit_sha')!r}, branch head {head!r}")
        w.ops({"op": "commit", "project": "widgets", "files": {"src/new_file.py": "z = 3\n"}},
              {"op": "add_issue", "project": "widgets", "title": "After the checkpoint", "author": "dev"})
        assert succeeded(await w.sync())
        new_head = await branch_head_sha(w.rest(), "acme/widgets", "main")
        assert new_head != head
        later = await self._points(w, graph_provider)
        assert later["code-repository"].get("last_commit_sha") == new_head, "the code checkpoint did not follow the branch"
        assert later["work-items"]["last_sync_time"] > points["work-items"]["last_sync_time"], "the issue checkpoint did not move"
        await w.assert_matches_source("after the checkpoints moved")

    async def test_tc_glsim_idx_001_indexing_reaches_a_terminal_state(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        assert succeeded(await w.start(base_instance()))
        deadline = time.monotonic() + 240
        while True:
            records = await w.records()
            busy = [r for r in records if r["indexingStatus"] in BUSY]
            if not busy or time.monotonic() >= deadline:
                break
            await asyncio.sleep(3)
        assert not busy, f"still being indexed: {[(r['recordName'], r['indexingStatus']) for r in busy][:6]}"
        by_kind = {}
        for r in records:
            kind = "code" if "/-/blob/" in str(r["externalRecordId"]) else r["recordType"]
            by_kind.setdefault(kind, []).append(r)
        for kind in ("TICKET", "PULL_REQUEST", "code"):
            rows = by_kind.get(kind)
            assert rows, f"no {kind} record among {sorted(by_kind)}"
            assert any(r["indexingStatus"] == "COMPLETED" for r in rows), \
                [(r["recordName"], r["indexingStatus"]) for r in rows][:6]
        failed = [(r["recordName"], r["indexingStatus"]) for r in records if str(r["indexingStatus"]).startswith("FAILED")]
        assert not failed, f"records that failed to index: {failed[:6]}"
        off = [r["recordName"] for r in records if r["indexingStatus"] == "AUTO_INDEX_OFF"]
        assert not off, f"switched off for indexing under default filters: {off[:6]}"

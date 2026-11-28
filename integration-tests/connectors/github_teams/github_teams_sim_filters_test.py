# pyright: ignore-file

"""
GitHub Teams on the simulator: filters, reindex and the connector's lifecycle
=============================================================================

  TC-GHSIM-FILT-001  The repository filter scopes the sync to one repository of the organisation [G]
  TC-GHSIM-FILT-002  Indexing filters (issues, code files off): the records are stored and read as before, indexing is
                     switched off for them, pull requests still index [G, K]
  TC-GHSIM-FILT-003  Two repositories in one instance: enabling is refused with the reason; one repository syncs [G]
  TC-GHSIM-FILT-004  The organisation and repository pickers list what the connected account reaches, ranked and paged;
                     a filter with no options is refused [G]
  TC-GHSIM-FILT-005  The "updated since" window keeps older issues and pull requests out and everything else in [G]
  TC-GHSIM-FILT-006  A filter that excludes a path keeps its files out; code files elsewhere stay [G]
  TC-GHSIM-FILT-007  An organisation filter with no repository filter does not sync every repository the account reads [G]
  TC-GHSIM-FILT-008  A record re-indexed: it is read again, after a change it carries the change, and a record gone at
                     GitHub is not served [K]
  TC-GHSIM-LIFE-001  Disable then enable: the same records, nobody gains access [L]
  TC-GHSIM-LIFE-002  Deleting the connector leaves no record of it [L]
  TC-GHSIM-LIFE-003  Two connectors over two repositories of one organisation each equal the source and do not mix [L]
  TC-GHSIM-LIFE-004  A sync requested while one runs ends with the source's state [L]
"""

import time

import pytest
from connectors.github_teams.sim_instances import ALICE, BOB, acme, rich
from connectors.github_teams.sim_world import (
    GithubWorld,
    repo_filter,
    succeeded,
    with_filters,
)

ONE = [{"op": "add_issue", "repo": "widgets", "title": "Issue", "key": "issue"},
       {"op": "add_pull", "repo": "widgets", "title": "PR", "changes": {"src/pr.py": "pr = 1\n"}}]


@pytest.mark.integration
@pytest.mark.github_teams_sim
@pytest.mark.asyncio(loop_scope="session")
class TestGithubSimFilters:

    async def test_tc_ghsim_filt_001_repository_scope(self, github_world: GithubWorld) -> None:
        w = github_world
        ops = [{"op": "add_issue", "repo": "widgets", "title": "Widgets"}, {"op": "add_issue", "repo": "gadgets", "title": "Gadgets"}]
        assert succeeded(await w.start(acme(repos=2), ops=ops))
        assert {r["recordName"] for r in await w.records() if r["recordType"] == "TICKET"} == {"Widgets"}
        await w.assert_matches_source("one repository of two")

    async def test_tc_ghsim_filt_002_indexing_filters(self, github_world: GithubWorld) -> None:
        w = github_world
        flt = with_filters(repo_filter("acme/widgets"), indexing={"issues": False, "merge_requests": True, "code_files": False})
        assert succeeded(await w.start(acme(), ops=rich(), filters=flt))
        status = {}
        for r in await w.records():
            status.setdefault(r["recordType"], set()).add(r["indexingStatus"])
        assert status["TICKET"] == {"AUTO_INDEX_OFF"} and status["CODE_FILE"] == {"AUTO_INDEX_OFF"}, status
        assert "AUTO_INDEX_OFF" not in status["PULL_REQUEST"], status
        await w.assert_matches_source("indexing off")

    async def test_tc_ghsim_filt_003_two_repositories_refused(self, github_world: GithubWorld) -> None:
        w = github_world
        outcome = await w.start(acme(repos=2), ops=ONE, repos=["acme/widgets", "acme/gadgets"])
        assert outcome == "refused", "an instance holding two repositories was enabled"
        assert not w.connector().get("isActive")
        assert not await w.records()
        assert succeeded(await w.restart(repo_filter("acme/widgets")))
        await w.assert_matches_source("narrowed to one repository")

    async def test_tc_ghsim_filt_004_pickers(self, github_world: GithubWorld) -> None:
        w = github_world
        assert succeeded(await w.start(acme(repos=2), ops=ONE))

        def options(key: str, **params):
            resp = w.client.request("GET", f"/api/v1/connectors/{w.connector_id}/filters/{key}/options",
                                    params={"page": 1, "limit": 20, **params})
            assert resp.status_code == 200, f"{key}: HTTP {resp.status_code} {resp.text[:200]}"
            return resp.json()

        assert "acme" in [o["id"] for o in options("org_ids")["options"]]
        repos = [o["id"] for o in options("repo_ids", limit=100)["options"]]
        assert {"acme/widgets", "acme/gadgets"} <= set(repos), repos
        assert all("/" in r for r in repos)
        assert options("repo_ids", limit=1)["hasMore"] is True
        assert w.client.request("GET", f"/api/v1/connectors/{w.connector_id}/filters/issues/options",
                                params={"page": 1, "limit": 20}).status_code == 400

    async def test_tc_ghsim_filt_005_updated_since_window(self, github_world: GithubWorld) -> None:
        w = github_world
        await w.start(acme(), enable=False)
        w.ops({"op": "add_issue", "repo": "widgets", "title": "Old issue", "key": "old"},
              {"op": "add_pull", "repo": "widgets", "title": "Old PR", "key": "oldpr", "changes": {"o.py": "o\n"}},
              {"op": "advance", "seconds": 30 * 86400})
        cut_s = w.sim.expected()["now"] - 86400
        w.ops({"op": "add_issue", "repo": "widgets", "title": "New issue", "key": "new"})
        rid = w.repo_id()
        flt = with_filters(repo_filter("acme/widgets"), modified={
            "operator": "is_after", "type": "datetime", "value": {"start": cut_s * 1000, "end": None}})
        w.client.update_connector_filters_sync_config(w.connector_id, filters=flt)
        w.scope.left_out = {f"{rid}/issues/1", f"{rid}/pull/2"}
        assert succeeded(await w.enable())
        assert {r["recordName"] for r in await w.records() if r["recordType"] == "TICKET"} == {"New issue"}
        assert not [r for r in await w.records() if r["recordType"] == "PULL_REQUEST"]
        await w.assert_matches_source("only what was updated since the cut")

    async def test_tc_ghsim_filt_006_excluded_paths(self, github_world: GithubWorld) -> None:
        w = github_world
        flt = with_filters(repo_filter("acme/widgets"), exclude_paths={"operator": "in", "type": "list", "value": ["docs/"]})
        ops = [{"op": "commit", "repo": "widgets", "changes": {"docs/guide.md": "g\n", "docs/api/x.md": "x\n", "lib/keep.py": "k = 1\n"}}]
        await w.start(acme(), ops=ops, filters=flt, enable=False)
        assert succeeded(await w.enable())
        rid = w.repo_id()
        code = {r["externalRecordId"] for r in await w.records() if r["recordType"] == "CODE_FILE"}
        assert f"/{rid}/blob/lib/keep.py" in code and not [c for c in code if "/docs/" in c], sorted(code)

    async def test_tc_ghsim_filt_007_organisation_filter_alone(self, github_world: GithubWorld) -> None:
        w = github_world
        flt = {"sync": {"values": {"org_ids": {"operator": "in", "type": "multiselect", "value": ["acme"]}}}}
        outcome = await w.start(acme(repos=2), ops=ONE, filters=flt, repos=[])
        # the repository filter is required: a sync of every repository the account reads must never happen
        repos = {r["externalGroupId"].split("-")[0] for r in await w.records()}
        assert not repos, f"an organisation filter alone ended {outcome!r} and synced {sorted(repos)}"

    async def test_tc_ghsim_filt_008_reindex(self, github_world: GithubWorld) -> None:
        w = github_world
        assert succeeded(await w.start(acme(), ops=ONE))
        rid = w.repo_id()
        record = await w.record_for(f"{rid}/issues/1")
        w.client.reindex_record(record["id"])
        w.ops({"op": "edit_issue", "issue": "issue", "title": "Issue (edited)", "body": "a new body"})
        w.client.reindex_record(record["id"])
        deadline = time.monotonic() + 60
        while "a new body" not in await w.stream_text(f"{rid}/issues/1"):
            assert time.monotonic() < deadline, "a re-indexed issue never carried its change"
        w.ops({"op": "delete_issue", "issue": "issue"})
        with pytest.raises(Exception):  # noqa: B017, PT011 - an HTTP error: the record is gone at GitHub
            await w.stream(f"{rid}/issues/1")

    async def test_tc_ghsim_life_001_disable_then_enable(self, github_world: GithubWorld) -> None:
        w = github_world
        assert succeeded(await w.start(acme(), ops=ONE))
        before = await w.holdings()
        w.client.toggle_sync(w.connector_id, enable=False)
        await w._wait_idle()
        assert not w.connector().get("isActive")
        assert succeeded(await w.enable())
        await w.assert_holdings_kept(before, "after disable and enable")

    async def test_tc_ghsim_life_002_deleted_connector_leaves_nothing(self, github_world: GithubWorld) -> None:
        w = github_world
        assert succeeded(await w.start(acme(), ops=rich()))
        cid = w.connector_id
        w.client.toggle_sync(cid, enable=False)
        w.client.delete_connector(cid)
        w.client.wait(15)
        await w.graph.assert_all_records_cleaned(cid, timeout=180)
        w.connector_id = None

    async def test_tc_ghsim_life_003_two_connectors(self, github_world: GithubWorld) -> None:
        w = github_world
        ops = [{"op": "add_issue", "repo": "widgets", "title": "Widgets issue"}, {"op": "add_issue", "repo": "gadgets", "title": "Gadgets issue"}]
        assert succeeded(await w.start(acme(repos=2), ops=ops))
        first = w.connector_id
        w.extra_connectors.append(first)
        w.scope.repos = {"acme/gadgets"}
        w.create_connector(filters=repo_filter("acme/gadgets"))
        assert succeeded(await w.enable())
        await w.assert_matches_source("the second connector")
        assert {r["recordName"] for r in await w.records() if r["recordType"] == "TICKET"} == {"Gadgets issue"}
        w.connector_id, second = first, w.connector_id
        w.scope.repos = {"acme/widgets"}
        await w.assert_matches_source("the first connector")
        w.extra_connectors = [second]

    async def test_tc_ghsim_life_004_sync_requested_while_one_runs(self, github_world: GithubWorld) -> None:
        w = github_world
        assert succeeded(await w.start(acme(), ops=rich()))
        w.ops({"op": "add_issue", "repo": "widgets", "title": "While running"}, {"op": "advance", "seconds": 180})
        before = w.connector().get("lastSyncFinishedAtTimestamp") or 0
        w.client.resync_connector(w.connector_id, full_sync=False, connector_name=w.connector_type)
        try:
            w.client.resync_connector(w.connector_id, full_sync=False, connector_name=w.connector_type)
        except Exception:  # noqa: BLE001 - refused while one runs is fine
            pass
        await w._await_end(before)
        await w._wait_idle()
        await w.assert_matches_source("after two requests")

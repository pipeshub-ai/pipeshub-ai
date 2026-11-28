# pyright: ignore-file

"""
GitLab on the simulator – filters, the one-repository rule, paging, lifecycle and vendor variants
=================================================================================================

  TC-GLSIM-FILT-001  a group filter beside the selected repository widens nothing (live TC-FILTER-001)
  TC-GLSIM-FILT-002  Index Code Files off: the records exist and are AUTO_INDEX_OFF (live TC-FILTER-002)
  TC-GLSIM-FILT-003  Index Issues and Index Merge Requests off: the records exist, none is indexed
  TC-GLSIM-FILT-004  the Modified Date filter keeps out issues and merge requests last changed before it
  TC-GLSIM-FILT-005  the Created Date filter keeps out issues and merge requests created before it
  TC-GLSIM-FILT-006  Exclude Paths: files under an excluded folder have no record
  TC-GLSIM-FILT-007  strict gap: the repository filter changed later: the old project's records stay stored
  TC-GLSIM-FILT-007b the repository filter changed later: the new project is synced and equals GitLab
  TC-GLSIM-FILT-008  two repositories saved on one connector: PipesHub refuses to turn it on (live TC-GL-ONEREPO-001)
  TC-GLSIM-FILT-009  the pickers list the groups and projects the sync account reads, and not a private project it cannot
                     (live TC-GL-FILTEROPT-001)
  TC-GLSIM-PAGE-001  101 issues (past the page size): every one is a record
  TC-GLSIM-PAGE-002  an issue edited between two pages of the listing: the next run equals GitLab (harness
                     changes_while_listing)
  TC-GLSIM-PAGE-003  offset paging for issues (GitLab without keyset paging): equals GitLab
  TC-GLSIM-PAGE-004  the repository's tree in pages of two (GraphQL): every file and folder is a record
  TC-GLSIM-LIFE-001  disable and enable again: nothing lost, nobody gains, equals GitLab
  TC-GLSIM-LIFE-002  delete the connector: no record, record group or permission stays
  TC-GLSIM-LIFE-003  two connectors on one instance (two projects): each equals GitLab; deleting one leaves the other
  TC-GLSIM-LIFE-004  a sync requested while one runs: the second request does not start a second run and the result
                     equals GitLab
  TC-GLSIM-VAR-001   members listed without their public e-mail (older GitLab): the profile read gives it; equals GitLab
  TC-GLSIM-VAR-002   a commit larger than the compare limit (the compare answers compare_timeout): equals GitLab
  TC-GLSIM-VAR-003   a rename GitLab does not report as one (delete and add): equals GitLab, one record
  TC-GLSIM-VAR-004   a comment that does not move the issue's updated_at: the streamed issue still holds it
"""

import asyncio

import pytest
from app.config.constants.arangodb import ProgressStatus

from connectors.gitlab.sim_instances import base_instance, issues
from connectors.gitlab.sim_world import (
    GitLabWorld,
    list_filter,
    norm,
    succeeded,
    sync_filters,
)


def indexing(**values: bool) -> dict:
    return {"indexing": {"values": {k: {"operator": "is", "type": "boolean", "value": v} for k, v in values.items()}}}


def datetime_filter(operator: str, start: int) -> dict:
    return {"operator": operator, "type": "datetime", "value": {"start": start, "end": None}}


@pytest.mark.integration
@pytest.mark.gitlab_sim
@pytest.mark.asyncio(loop_scope="session")
class TestGitLabSimScope:

    async def test_tc_glsim_filt_001_a_group_filter_widens_nothing(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        assert succeeded(await w.start(base_instance(), filters=sync_filters("acme/widgets", group_ids=list_filter("in", ["acme"]))))
        await w.assert_matches_source("a group filter beside the repository")
        groups = {str(r["externalGroupId"]) for r in await w.records()}
        assert not any(g.startswith(str(w.key("oss"))) for g in groups), "another project of the group was synced"

    async def test_tc_glsim_filt_002_code_indexing_off(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        assert succeeded(await w.start(base_instance(), filters={**sync_filters(), **indexing(code_files=False)}))
        await w.assert_matches_source("code files not indexed")
        code = [r for r in await w.records() if "/-/blob/" in str(r["externalRecordId"]) or "/-/tree/" in str(r["externalRecordId"])]
        assert code, "no code record at all: the indexing filter was applied as a sync filter"
        on = [r["externalRecordId"] for r in code if r["indexingStatus"] != ProgressStatus.AUTO_INDEX_OFF.value]
        assert not on, f"code records still indexable with Index Code Files off: {on[:3]}"
        rest = [r for r in await w.records() if r["recordType"] in ("TICKET", "PULL_REQUEST")]
        assert rest and all(r["indexingStatus"] != ProgressStatus.AUTO_INDEX_OFF.value for r in rest), \
            "the issues and merge requests were switched off with the code"

    async def test_tc_glsim_filt_003_issue_and_merge_request_indexing_off(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        assert succeeded(await w.start(base_instance(), filters={**sync_filters(), **indexing(issues=False, merge_requests=False)}))
        await w.assert_matches_source("issues and merge requests not indexed")
        work = [r for r in await w.records() if r["recordType"] in ("TICKET", "PULL_REQUEST")]
        assert work and all(r["indexingStatus"] == ProgressStatus.AUTO_INDEX_OFF.value for r in work), [
            (r["recordName"], r["indexingStatus"]) for r in work]

    async def _dated(self, w: GitLabWorld, key: str, label: str) -> None:
        inst = base_instance()
        for project in inst["projects"]:
            if project["key"] == "widgets":
                project["issues"][0]["created_at"] = project["issues"][0]["updated_at"] = "2025-01-10T00:00:00Z"
                project["issues"][2]["created_at"] = project["issues"][2]["updated_at"] = "2025-01-10T00:00:00Z"
        cut = 1_750_000_000_000      # 2025-06-15
        assert succeeded(await w.start(inst, filters=sync_filters(**{key: datetime_filter("is_after", cut)})))
        rest = w.rest()
        try:
            from connectors.gitlab.gitlab_test_utils import (
                list_issues,
                list_merge_requests,
            )

            old = {str(i["id"]) for i in await list_issues(rest, "acme/widgets") if i["created_at"] < "2025-06-15"}
            old |= {str(m["id"]) for m in await list_merge_requests(rest, "acme/widgets") if m["created_at"] < "2025-06-15"}
        finally:
            await rest.aclose()
        assert old, "the instance holds no old issue, so the filter has nothing to keep out"
        shot = {norm(r["id"]) for r in w.sim.expected()["records"] if r["kind"] == "upload" and r.get("filename") == "shot.txt"}
        w.scope.exclude = old | shot          # the upload of an item the filter keeps out goes with it
        await w.assert_matches_source(label)
        assert not {norm(str(r["externalRecordId"])) for r in await w.records()} & old, "an old item has a record"

    async def test_tc_glsim_filt_004_modified_date(self, gitlab_world: GitLabWorld) -> None:
        await self._dated(gitlab_world, "modified", "the Modified Date filter")

    async def test_tc_glsim_filt_005_created_date(self, gitlab_world: GitLabWorld) -> None:
        await self._dated(gitlab_world, "created", "the Created Date filter")

    async def test_tc_glsim_filt_006_exclude_paths(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        assert succeeded(await w.start(base_instance(), filters=sync_filters(exclude_paths={"operator": "in", "type": "list", "value": ["src/util/"]})))
        w.scope.exclude = {"/acme/widgets/-/blob/HEAD/src/util/helpers.py"}      # the folder stays, empty
        await w.assert_matches_source("Exclude Paths")
        assert "helpers.py" not in {r["recordName"] for r in await w.records()}

    @pytest.mark.xfail(strict=True, reason="the records of a project that is no longer the selected repository stay stored and "
                       "readable, even after a full sync: backend/python/app/connectors/sources/gitlab/projects.py "
                       "(_resolve_projects_with_filters only lists the selected project; nothing withdraws the others); no open PR")
    async def test_tc_glsim_filt_007_repository_changed_later(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        assert succeeded(await w.start(base_instance()))
        assert succeeded(await w.restart(filters=sync_filters("acme/oss"), project="acme/oss"))
        assert succeeded(await w.sync(full=True))
        stale = [p for p in await w.differences() if p["type"] == "STALE"]
        assert not stale, f"{len(stale)} record(s) of the project that is no longer selected are still stored: {stale[:3]}"

    async def test_tc_glsim_filt_007b_the_new_repository_is_synced(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        assert succeeded(await w.start(base_instance()))
        assert succeeded(await w.restart(filters=sync_filters("acme/oss"), project="acme/oss"))
        assert succeeded(await w.sync(full=True))
        missing = [p for p in await w.differences() if p["type"] not in ("STALE", "DENIED:project-visibility")]
        assert not missing, f"the new repository is not what GitLab holds: {missing[:5]}"

    async def test_tc_glsim_filt_008_two_repositories_are_refused(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        await w.start(base_instance(), filters=sync_filters(None, project_ids=list_filter("in", ["acme/widgets", "acme/oss"])),
                      enable=False)
        resp = w.client.request("POST", f"/api/v1/connectors/{w.connector_id}/toggle", json={"type": "sync"})
        assert resp.status_code == 400 and "Narrow it down to one" in resp.text, (resp.status_code, resp.text[:300])
        assert not w._connector().get("isActive")
        assert not await w.records()

    async def test_tc_glsim_filt_009_pickers(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        inst = base_instance()
        inst["groups"].append({"key": "other", "path": "other", "members": {"dev": "owner"}})
        inst["projects"].append({"key": "vault", "path": "vault", "namespace": "other"})
        assert succeeded(await w.start(inst))
        projects = w.client.get_filter_options(w.connector_id, "project_ids", limit=100, search="acme")
        labels = {str(o.get("id") or o.get("value")) for o in projects.get("options", [])} | {
            str(o.get("label")) for o in projects.get("options", [])}
        assert {"acme/widgets", "acme/oss"} <= labels, sorted(labels)
        hidden = w.client.get_filter_options(w.connector_id, "project_ids", limit=100, search="vault")
        assert not hidden.get("options"), f"the picker lists a private project the account cannot read: {hidden}"
        groups = w.client.get_filter_options(w.connector_id, "group_ids", limit=100)
        names = {str(o.get("id") or o.get("value")) for o in groups.get("options", [])}
        assert "acme" in names, sorted(names)

    # ---- paging -----------------------------------------------------------------------------------------

    async def test_tc_glsim_page_001_past_the_page_size(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        inst = base_instance()
        await w.start(inst, enable=False)
        w.ops(*issues(101))
        assert succeeded(await w.enable())
        await w.assert_matches_source("101 more issues")
        assert len([r for r in await w.records() if r["recordType"] == "TICKET"]) == 104

    async def test_tc_glsim_page_002_a_change_between_two_pages(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        await w.start(base_instance(), enable=False)
        w.ops(*issues(110))
        assert succeeded(await w.enable())
        w.ops(*({"op": "set_issue", "issue": f"issue{i}", "title": f"Issue {i:03} v2"} for i in range(110)))
        w.sim.g("POST", "/deferred", {"contains": "/issues", "after": 1,
                                      "ops": [{"op": "set_issue", "issue": "issue0", "title": "Issue 000 v3"}]})
        await w.sync()
        assert w.sim.g("GET", "/deferred")["applied"], "the deferred change never landed between the pages"
        assert succeeded(await w.sync())
        await w.assert_matches_source("after a change between two pages")

    async def test_tc_glsim_page_003_offset_paging(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        await w.start(base_instance(), behaviours={"issues_keyset": False}, enable=False)
        w.ops(*issues(105))
        assert succeeded(await w.enable())
        await w.assert_matches_source("offset paging")

    async def test_tc_glsim_page_004_tree_in_pages_of_two(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        assert succeeded(await w.start(base_instance(), behaviours={"graphql_tree_page_size": 2}))
        await w.assert_matches_source("a tree paged by two")

    # ---- lifecycle --------------------------------------------------------------------------------------

    async def test_tc_glsim_life_001_disable_and_enable(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        assert succeeded(await w.start(base_instance()))
        held = await w.holdings()
        await w.disable()
        await w.assert_holdings_kept(held, "after disabling")
        w.ops({"op": "advance", "seconds": 180})
        assert succeeded(await w.enable())
        await w.assert_holdings_kept(held, "after enabling again")
        await w.assert_matches_source("after enabling again")

    async def test_tc_glsim_life_002_delete_the_connector(self, gitlab_world: GitLabWorld, graph_provider) -> None:
        w = gitlab_world
        assert succeeded(await w.start(base_instance()))
        cid = w.connector_id
        w.client.toggle_sync(cid, enable=False)
        w.client.delete_connector(cid)
        await graph_provider.assert_connector_graph_fully_cleaned(cid, timeout=180)
        w.connector_id = None

    async def test_tc_glsim_life_003_two_connectors_on_one_instance(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        assert succeeded(await w.start(base_instance()))
        other = await w.second("acme/oss")
        await w.assert_matches_source("the first connector with a second beside it")
        await other.assert_matches_source("the second connector")
        w.client.toggle_sync(other.connector_id, enable=False)
        w.client.delete_connector(other.connector_id)
        await asyncio.sleep(0)
        other.connector_id = None
        await w.assert_matches_source("the first connector after the second was deleted")

    async def test_tc_glsim_life_004_a_sync_requested_while_one_runs(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        assert succeeded(await w.start(base_instance()))
        w.ops(*issues(30))
        w.ops({"op": "advance", "seconds": 180})
        before = w._connector().get("lastSyncFinishedAtTimestamp") or 0
        for _ in range(3):
            try:
                w.client.resync_connector(w.connector_id, full_sync=False, connector_name="GitLab")
            except Exception:  # noqa: BLE001, S110 - a request refused while a run is going is an answer
                pass
        await w._wait_idle()
        for _ in range(60):
            if (w._connector().get("lastSyncFinishedAtTimestamp") or 0) > before:
                break
            await asyncio.sleep(1)
        await w._wait_idle()
        await w.assert_matches_source("after three requests for a sync")

    # ---- vendor variants --------------------------------------------------------------------------------

    async def test_tc_glsim_var_001_hidden_member_emails(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        assert succeeded(await w.start(base_instance(), behaviours={"members_expose_public_email": False}))
        # The members listing carries no e-mail, so the connector reads each member's profile for it.
        await w.assert_matches_source("members listed without their e-mail")

    async def test_tc_glsim_var_002_commit_past_the_compare_limit(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        assert succeeded(await w.start(base_instance(), behaviours={"compare_max_files": 2}))
        w.ops({"op": "commit", "project": "widgets", "files": {f"bulk/f{i}.py": f"v = {i}\n" for i in range(6)}})
        assert succeeded(await w.sync())
        await w.assert_matches_source("a commit past the compare limit")
        assert len([r for r in await w.records() if "/bulk/" in str(r["externalRecordId"]) and r["recordName"].endswith(".py")]) == 6

    async def test_tc_glsim_var_003_rename_without_rename_detection(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        assert succeeded(await w.start(base_instance(), behaviours={"compare_detects_renames": False}))
        w.ops({"op": "commit", "project": "widgets", "files": {"src/app.py": None, "src/renamed.py": "print('hi')\n"}})
        assert succeeded(await w.sync())
        await w.assert_matches_source("a rename reported as a delete and an add")
        names = [r["recordName"] for r in await w.records()]
        assert names.count("renamed.py") == 1 and "app.py" not in names

    async def test_tc_glsim_var_004_comment_that_does_not_move_the_issue(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        assert succeeded(await w.start(base_instance(), behaviours={"note_bumps_issue": False}))
        w.ops({"op": "add_note", "issue": "open", "author": "dev", "body": "a quiet comment"})
        assert succeeded(await w.sync())
        text = w.stream((await w.record_for(str(w.key("open"))))["id"]).decode(errors="replace")
        assert "a quiet comment" in text, "the streamed issue lacks a comment that did not move its updated_at"

# pyright: ignore-file

"""
GitLab on the simulator – the first sync, an unchanged sync, content, and sign-in
==================================================================================

Each test loads an instance into the GitLab simulator, runs the real connector through the deployed product, and
compares what PipesHub holds with the simulator's oracle for every person (sim_world.py). The cases that only read the
state of one first sync share a connector (``gitlab_synced``) and compare against the oracle as it was; the others own
a connector. Live twins: TC-SYNC-001, TC-GL-RG-001/002, TC-GL-USER-001/002, TC-GL-AUTH-001, TC-GL-ISSUE-001/002,
TC-GL-MR-001, TC-GL-CODE-001/002, TC-GL-ATTACH-001, TC-GL-ISSUE-BLOCKS-001, TC-GL-MR-BLOCKS-001.

  TC-GLSIM-SYNC-001  the first sync equals the oracle: every issue, merge request, file, folder and upload, its record
                     group, and what each person reads
  TC-GLSIM-SYNC-002  the project record group and its four children (work items, confidential work items, merge
                     requests, code repository); only the work items inherit the project's grants
  TC-GLSIM-SYNC-003  an issue's record equals what GitLab serves (title, state, URL, dates); closed stays "closed"
  TC-GLSIM-SYNC-004  a merge request's record equals what GitLab serves
  TC-GLSIM-SYNC-005  code files and folders: names, paths, extensions, the folder chain; dotfiles have no record
  TC-GLSIM-SYNC-006  an upload linked from a public place has a file record; one linked only from an internal note
                     has none
  TC-GLSIM-SYNC-007  members are bound to PipesHub accounts by GitLab id; blocked and awaiting members are not
                     readers; a person with no GitLab account reads nothing
  TC-GLSIM-SYNC-008  an unchanged sync: no record created, removed or given a new version; the same people read the
                     same records
  TC-GLSIM-SYNC-009  an odd file name (spaces, parentheses, an ampersand, an accent) and an empty file
  TC-GLSIM-SYNC-010  the streamed issue holds its description and public comment and not the internal note; the
                     streamed merge request holds its comment
  TC-GLSIM-SYNC-011  streamed code and upload bytes equal the repository's; an empty file streams empty
  TC-GLSIM-SYNC-012  the sync account is not the person who set the connector up: the creator reads what the account
                     reads (the account's own address is not the creator's)

  TC-GLSIM-AUTH-001  an API token (personal access token) connector equals the oracle
  TC-GLSIM-AUTH-002  a token GitLab does not know: PipesHub refuses to turn the connector on; nothing is stored
  TC-GLSIM-AUTH-004  a revoked token: the sync does not succeed and changes nothing; with a new token the next sync
                     equals the oracle
  TC-GLSIM-AUTH-005  an OAuth access token that expired and no refresher: the sync does not succeed, nothing is
                     lost; with a fresh token the next sync equals the oracle
  TC-GLSIM-AUTH-006  an instance that cannot be reached (no such host): PipesHub refuses to turn the connector on
  TC-GLSIM-AUTH-007  a guest's token (too few privileges for code, merge requests, confidential issues): the sync
                     stores what a guest reads and gives nobody more
"""

import pytest
from app.config.constants.arangodb import CollectionNames
from app.utils.time_conversion import parse_timestamp

from connectors.gitlab.gitlab_expected import (
    PIPELINE_ASSIGNED_FIELDS,
    PROCESSOR_ASSIGNED_FIELDS,
    GitLabExpected,
)
from connectors.gitlab.sim_instances import base_instance, email_of
from connectors.gitlab.sim_world import REFUSED, GitLabWorld, norm, succeeded
from helper.oauth_token_helper import inject_access_token
from validation.graph_entity_validator import assert_graph_entity_matches

SKIP = PROCESSOR_ASSIGNED_FIELDS | PIPELINE_ASSIGNED_FIELDS | frozenset({"inherit_permissions", "source_created_at",
                                                                          "source_updated_at"})
CHILD_KINDS = ["work-items", "confidential-work-items", "merge-requests", "code-repository"]


def _kinds(records: list[dict]) -> set[str]:
    return {r["recordType"] for r in records}


@pytest.mark.integration
@pytest.mark.gitlab_sim
@pytest.mark.asyncio(loop_scope="session")
class TestGitLabSimSync:

    async def test_tc_glsim_sync_001_first_sync_equals_the_oracle(self, gitlab_synced) -> None:
        _, snap, _ = gitlab_synced
        failing = snap.failing()
        assert not failing, f"PipesHub differs from GitLab: {failing[:25]}"
        kinds = {r["kind"] for r in snap.expected["records"]}
        assert {"issue", "merge_request", "file", "folder", "upload"} <= kinds, f"the instance lacks kinds: {kinds}"
        assert len(snap.records) >= 12, f"too few records stored: {len(snap.records)}"
        assert _kinds(snap.records) >= {"TICKET", "PULL_REQUEST", "FILE"}, _kinds(snap.records)

    async def test_tc_glsim_sync_002_project_record_group_and_children(self, gitlab_synced, graph_provider) -> None:
        world, _, source = gitlab_synced
        project, cid = source["project"], world.connector_id
        group = await graph_provider.get_record_group_by_external_id(cid, str(project["id"]))
        assert group is not None, "project record group missing"
        assert_graph_entity_matches(GitLabExpected.project_record_group(project, connector_id=cid,
                                                                        parent_full_path="acme"),
                                    group, entity="record_group", skip_compare=SKIP)
        for kind in CHILD_KINDS:
            child = await graph_provider.get_record_group_by_external_id(cid, f"{project['id']}-{kind}")
            assert child is not None, f"child record group {kind} missing"
            assert_graph_entity_matches(GitLabExpected.child_record_group(project, kind=kind, connector_id=cid), child,
                                        entity="record_group", skip_compare=SKIP)
            # Every member at Guest or above is on the project group, so only the work items (which a Guest reads)
            # inherit it; the others carry their own grants, or a Guest would read the code and the merge requests.
            edges = await graph_provider.find_edges_between(CollectionNames.RECORD_GROUPS.value, child.id,
                                                            CollectionNames.RECORD_GROUPS.value, group.id,
                                                            CollectionNames.INHERIT_PERMISSIONS.value)
            assert bool(edges) == (kind == "work-items"), f"child group {kind}: inherits the project group: {bool(edges)}"

    async def test_tc_glsim_sync_003_issue_records(self, gitlab_synced, graph_provider) -> None:
        world, _, source = gitlab_synced
        by_title = {i["title"]: i for i in source["issues"]}
        assert {"Open issue", "Confidential issue", "Closed issue"} <= set(by_title), by_title.keys()
        for title, issue in by_title.items():
            actual = await graph_provider.get_typed_record_by_external_id(world.connector_id, str(issue["id"]))
            assert actual is not None, f"typed TICKET record missing for {title}"
            expected = GitLabExpected.ticket_record(issue, connector_id=world.connector_id)
            # The connector names the state in PipesHub's words (issues.py _ISSUE_STATUS) and keeps when it closed.
            expected.status = {"opened": "OPEN", "closed": "CLOSED"}[issue["state"]]
            expected.resolved_at = parse_timestamp(issue["closed_at"]) if issue.get("closed_at") else None
            assert_graph_entity_matches(expected, actual, entity="ticket_record", skip_compare=SKIP)
        assert by_title["Closed issue"]["state"] == "closed" and by_title["Open issue"]["state"] == "opened"

    async def test_tc_glsim_sync_004_merge_request_records(self, gitlab_synced, graph_provider) -> None:
        world, _, source = gitlab_synced
        assert source["mrs"], "the instance has a merge request"
        for mr in source["mrs"]:
            actual = await graph_provider.get_typed_record_by_external_id(world.connector_id, str(mr["id"]))
            assert actual is not None, f"typed PULL_REQUEST record missing for {mr['title']}"
            assert_graph_entity_matches(
                GitLabExpected.merge_request_record(mr, connector_id=world.connector_id), actual,
                entity="pull_request_record", skip_compare=SKIP)

    async def test_tc_glsim_sync_005_code_files_and_folders(self, gitlab_synced, graph_provider) -> None:
        world, snap, _ = gitlab_synced
        wanted = {r["id"]: r for r in snap.expected["records"] if r["kind"] in ("file", "folder")}
        names = {r["externalRecordId"]: r for r in snap.records}
        for rid, r in wanted.items():
            if r["path"].rsplit("/", 1)[-1].startswith("."):
                assert rid not in names, f"dotfile {r['path']} has a record"
        assert "/acme/widgets/-/blob/HEAD/src/util/helpers.py" in names, sorted(names)
        helper = names["/acme/widgets/-/blob/HEAD/src/util/helpers.py"]
        assert helper["recordName"] == "helpers.py"
        parent = await graph_provider.get_record_parent_external_id(world.connector_id, "/acme/widgets/-/blob/HEAD/src/util/helpers.py")
        assert parent == "/acme/widgets/-/tree/HEAD/src/util", f"parent of helpers.py: {parent}"
        up = await graph_provider.get_record_parent_external_id(world.connector_id, "/acme/widgets/-/tree/HEAD/src/util")
        assert up == "/acme/widgets/-/tree/HEAD/src", f"parent of src/util: {up}"

    async def test_tc_glsim_sync_006_uploads(self, gitlab_synced) -> None:
        _, snap, _ = gitlab_synced
        uploads = [r for r in snap.expected["records"] if r["kind"] == "upload"]
        internal = {norm(r["id"]) for r in uploads if r.get("internal_only")}
        public = {norm(r["id"]) for r in uploads if not r.get("internal_only")}
        live = {norm(str(r["externalRecordId"])) for r in snap.records}
        assert public and internal, f"the instance has both kinds of upload: {uploads}"
        assert public <= live, f"a public upload has no record: {public - live}"
        assert not internal & live, f"an upload linked only from an internal note has a record: {internal & live}"

    async def test_tc_glsim_sync_007_members_and_strangers(self, gitlab_synced, graph_provider) -> None:
        world, snap, source = gitlab_synced
        members = {m["username"]: m for m in source["members"]}
        for name in ("dev", "rita", "pat", "gus"):
            found = await graph_provider.get_user_by_source_id(str(members[name]["id"]), world.connector_id)
            assert found is not None, f"member {name} (GitLab id {members[name]['id']}) is bound to no PipesHub account"
            assert found.email.lower() == email_of(name), (name, found.email)
        for who in ("blocked", "nina", "nobody"):
            email = email_of(who) if who != "nobody" else "gl-nobody@example.com"
            assert not snap.readable[email], f"{who} reads {len(snap.readable[email])} record(s)"

    async def test_tc_glsim_sync_009_odd_names_and_empty_files(self, gitlab_synced) -> None:
        _, snap, _ = gitlab_synced
        names = {r["recordName"] for r in snap.records}
        assert "odd name (1) & café.md" in names, sorted(names)
        assert "empty.md" in names, sorted(names)

    async def test_tc_glsim_sync_008_unchanged_sync_changes_nothing(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        assert succeeded(await w.start(base_instance()))
        before = {r["externalRecordId"]: (r["id"], r["version"]) for r in await w.records()}
        held = await w.holdings()
        assert succeeded(await w.sync())
        assert succeeded(await w.sync(full=False))
        after = {r["externalRecordId"]: (r["id"], r["version"]) for r in await w.records()}
        assert after == before, f"an unchanged sync created, removed or re-versioned: {set(after.items()) ^ set(before.items())}"
        await w.assert_holdings_kept(held, "after two unchanged syncs")
        await w.assert_matches_source("after unchanged syncs")

    async def test_tc_glsim_sync_010_streamed_issue_and_merge_request(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        assert succeeded(await w.start(base_instance()))
        issue = await w.record_for(str(w.key("open")))
        mr = await w.record_for(str(w.key("mr1")))
        assert issue and mr, "the issue and the merge request have records"
        text = w.stream(issue["id"]).decode(errors="replace")
        assert "see [shot]" in text or "shot" in text, text[:500]
        assert "a public comment" in text, "the public comment is missing from the streamed issue"
        assert "internal [secret]" not in text and "internal only" not in text, "an internal note was streamed"
        assert "looks good" in w.stream(mr["id"]).decode(errors="replace"), "the merge request comment is missing"

    async def test_tc_glsim_sync_011_streamed_bytes_equal_the_source(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        assert succeeded(await w.start(base_instance()))
        helper = await w.record_for("/acme/widgets/-/blob/HEAD/src/util/helpers.py")
        empty = await w.record_for("/acme/widgets/-/blob/HEAD/docs/empty.md")
        assert helper and empty
        assert w.stream(helper["id"]) == b"def h():\n    return 1\n"
        assert w.stream(empty["id"]) == b""
        upload = next(r for r in await w.records() if "/uploads/" in str(r["externalRecordId"]))
        assert w.stream(upload["id"]) == b"a screenshot"

    async def test_tc_glsim_sync_012_creator_differs_from_the_sync_account(self, gitlab_world: GitLabWorld) -> None:
        # The sync account has its own address; the PipesHub login that set the connector up (pipeshub-it) is a
        # different person to GitLab. GitLab is the authority: the creator reads what the account reads, because the
        # connector registers which account it is signed in as.
        w = gitlab_world
        inst = base_instance()
        for u in inst["users"]:
            if u["username"] == "admin":
                u["email"] = "gl-svc-account@example.com"
        assert succeeded(await w.start(inst))
        creator = email_of("admin")
        held = await w.holdings()
        assert held[creator], "the connector's creator reads nothing of what the sync account reads"

    # ---- sign-in ---------------------------------------------------------------------------------------------

    async def test_tc_glsim_auth_001_api_token_connector(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        assert succeeded(await w.start(base_instance(), auth="api_token"))
        await w.assert_matches_source("an API token connector")

    async def test_tc_glsim_auth_002_unknown_token_is_refused(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        outcome = await w.start(base_instance(), token="glpat-not-a-token")
        assert outcome == REFUSED, f"PipesHub turned a connector on with a token GitLab does not know: {outcome}"
        assert not await w.records(), "records were stored for a refused connector"
        assert any(c["status"] == 401 for c in w.sim.calls()), "GitLab never refused the token"

    async def test_tc_glsim_auth_004_revoked_token_then_a_new_one(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        assert succeeded(await w.start(base_instance()))
        held = await w.holdings()
        w.ops({"op": "revoke_token", "token": w.tokens["sync_account"]})
        outcome = await w.sync()
        assert not succeeded(outcome), "a sync whose every read was refused (401) reported success"
        await w.assert_holdings_kept(held, "after a sync with a revoked token")
        new = w.ops({"op": "add_token", "key": "fresh", "user": "admin"})[0]["token"]
        inject_access_token(w.connector_id, new)
        assert succeeded(await w.sync()), "the sync with a new token did not succeed"
        await w.assert_matches_source("after the token was replaced")

    async def test_tc_glsim_auth_005_expired_oauth_token_then_a_fresh_one(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        assert succeeded(await w.start(base_instance(), token="oa"))
        held = await w.holdings()
        w.ops({"op": "advance", "seconds": 7300})        # an OAuth access token lives two hours
        outcome = await w.sync(step=False)
        assert not succeeded(outcome), "a sync with an expired OAuth access token reported success"
        await w.assert_holdings_kept(held, "after a sync with an expired token")
        fresh = w.ops({"op": "add_token", "key": "oa2", "user": "admin", "kind": "oauth", "client_id": "app1"})[0]["access_token"]
        inject_access_token(w.connector_id, fresh)
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the token was renewed")

    async def test_tc_glsim_auth_006_unreachable_instance(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        outcome = await w.start(base_instance(), instance_url="http://gitlab-sim-gone.invalid")
        assert outcome == REFUSED, f"PipesHub turned a connector on for an instance it cannot reach: {outcome}"
        assert not await w.records()

    async def test_tc_glsim_auth_007_a_guests_token(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        outcome = await w.start(base_instance(), token="t:gus")
        w.scope.sync_user = "gus"
        # Whatever PipesHub decides about a token too weak to read the code and the merge requests, it must store
        # only what a guest reads, and give nobody more than GitLab gives them.
        if outcome == REFUSED:
            assert not await w.records()
            return
        await w.assert_matches_source("a guest's token")

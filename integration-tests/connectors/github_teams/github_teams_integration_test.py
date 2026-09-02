# pyright: ignore-file

"""
GitHub Teams Connector – Integration Tests (pre-provisioned, read-only + self-cleaning mutations)
=================================================================================================

Scope comes from ``GH_TEAMS_TEST_ORG`` + the three repo env vars; fixture *shapes* are
discovered by the conftest rather than pinned, so a re-provisioned org needs no code
change. Only the two frozen blocks snapshots are pinned by number.

Every CI leg, every PR and the nightly cron share ONE GitHub org, and different PRs run
at the same time. The primary and public repos are therefore never written to: the three
mutation tests (orders 15-17) each create a throw-away connector scoped to the *mutation*
repo and assert by external id, so nothing another run does can reach an assertion here.
Code mutations are further confined to ``it/<run_id>/`` — the connector only syncs the
default branch, so concurrent runs share it and only a path namespace keeps them apart.
``README.md`` in this directory is the contract for adding tests.

  order 1  TC-SYNC-001            — full sync baseline + graph self-consistency
  order 2  TC-GH-RG-001           — org/repo/child record-group hierarchy + App edge
  order 3  TC-GH-USER-001         — AppUsers, USER_APP_RELATION, team→app gate edge
  order 4  TC-GH-ISSUE-001        — reference issue TICKET properties
  order 5  TC-GH-ISSUE-002        — hierarchy + BLOCKS relation + entity relations
  order 6  TC-GH-ISSUE-BLOCKS-001 — streamed issue blocks snapshot + attachment record
  order 7  TC-GH-PR-001           — merged PR PULL_REQUEST properties
  order 8  TC-GH-PR-BLOCKS-001    — streamed PR blocks snapshot
  order 9  TC-GH-CODE-001         — code file + folder record properties
  order 10 TC-GH-CODE-HIER-001    — folder PARENT_CHILD chain + folder inventory
  order 11 TC-GH-CODE-TS-001      — code/folder source timestamps (polled)
  order 12 TC-GH-PERM-001         — private repo ACL, role mapping, 2-hop inheritance
  order 13 TC-GH-PERM-002         — public repo ORG grant placement
  order 14 TC-GH-IDX-001          — indexing reaches COMPLETED / AUTO_INDEX_OFF
  order 15 TC-INCR-ISSUE-001      — new issue + sub-issue, then title/comment update
  order 16 TC-INCR-PR-001         — PR update-only: no new record, version += 1
  order 17 TC-INCR-CODE-001       — new/update/rename/move/delete in one commit set
"""

import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.config.constants.arangodb import (  # type: ignore[import-not-found]  # noqa: E402
    CollectionNames,
    ProgressStatus,
)
from app.models.entities import RecordType  # type: ignore[import-not-found]  # noqa: E402
from helper.graph_provider import GraphProviderProtocol  # noqa: E402
from helper.graph_provider_utils import (  # noqa: E402
    wait_for_record_by_external_id,
    wait_for_sync_completion,
    wait_until_graph_condition,
)
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]  # noqa: E402
from validation.graph_entity_validator import (  # noqa: E402
    assert_graph_entity_matches,
    assert_graph_entity_with_edges,
    assert_user_app_edge,
)

from connectors.github_teams.constants import (  # noqa: E402
    ENV_BLOCKS_BOOTSTRAP,
    ENV_OVERSIZED_PATH,
    GH_INDEXING_WAIT_SEC,
    GH_IT_RUN_ID,
    GH_SYNC_WAIT_SEC,
    GH_TIMESTAMP_BACKFILL_WAIT_SEC,
    artifact_title,
    it_path,
)
from connectors.github_teams.github_block_utils import (  # noqa: E402
    ISSUE_BLOCKS_PATH,
    PR_BLOCKS_PATH,
    bootstrap_expected,
    load_expected,
    normalize_blocks_container,
    parse_connector_blocks_via_processor,
)
from connectors.github_teams.github_expected import (  # noqa: E402
    PROCESSOR_ASSIGNED_FIELDS,
    GitHubExpected,
    epoch_ms,
    expected_repo_grant_emails,
)
from connectors.github_teams.github_test_utils import (  # noqa: E402
    FileChange,
    add_comment,
    add_sub_issue,
    blob_sha_for_path,
    close_issue,
    close_pull,
    commit_changes,
    create_issue,
    create_pull,
    dedicated_connector,
    get_branch_head,
    get_issue,
    get_pull,
    list_filter,
    sync_filters,
    tree_dirs,
    update_issue,
    update_pull,
)

logger = logging.getLogger("github-teams-it")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.github_teams,
    pytest.mark.asyncio(loop_scope="session"),
]

# ``created_at``/``updated_at`` are stamped by the processor at write time with
# wall-clock values a test cannot know. Every record comparison skips them.
_SKIP = PROCESSOR_ASSIGNED_FIELDS

# ``RecordGroup.from_arango_base_record_group`` does not hydrate
# ``inherit_permissions``, so a group read back from the graph always reports the
# model default regardless of what was written. Comparing the field would assert
# nothing; the tests assert the real INHERIT_PERMISSIONS edge instead.
_GROUP_SKIP = _SKIP | frozenset({"inherit_permissions"})

# ``extraction_status`` and ``md5_hash`` are written by the indexing pipeline after the
# record lands, so comparing them races the pipeline exactly the way ``indexing_status``
# (already in the validator's default skip set) would.
_RECORD_SKIP = _SKIP | frozenset({"extraction_status", "md5_hash"})

# TicketRecord.to_arango_record WRITES these three, but from_arango_record never reads
# them back, so a record loaded from the graph always reports the model default. They
# are asserted where they are observable instead: the single ASSIGNED_TO edge below
# covers what assignee_source_id exists to make possible.
_TICKET_UNHYDRATED = frozenset({
    "assignee_source_id", "reporter_source_id", "is_email_hidden",
})
_TICKET_SKIP = _RECORD_SKIP | _TICKET_UNHYDRATED

# Code-file source timestamps are filled by the background backfill that outlives the
# sync, so a comparison here races it. TC-GH-CODE-TS-001 polls for them instead.
_CODE_SKIP = _RECORD_SKIP | frozenset({"source_created_at", "source_updated_at"})


async def _group_edge_count(
    graph_provider: GraphProviderProtocol,
    *,
    from_group: Any,
    to_group: Any,
    edge_collection: str,
) -> int:
    edges = await graph_provider.find_edges_between(
        CollectionNames.RECORD_GROUPS.value, from_group.id,
        CollectionNames.RECORD_GROUPS.value, to_group.id,
        edge_collection,
    )
    return len(edges or [])


def _restart_sync(pipeshub_client: PipeshubClient, connector_id: str) -> None:
    """Toggle off/on to trigger an incremental sync.

    ``run_incremental_sync`` is an alias for ``run_sync`` in this connector — the delta
    is entirely checkpoint-driven — so re-entering the sync is all that is needed.
    """
    pipeshub_client.toggle_sync(connector_id, enable=False)
    pipeshub_client.wait(5)
    pipeshub_client.toggle_sync(connector_id, enable=True)
    pipeshub_client.wait(8)


async def _resync(
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
    connector_id: str,
) -> None:
    _restart_sync(pipeshub_client, connector_id)
    await wait_for_sync_completion(
        pipeshub_client, graph_provider, connector_id, timeout=GH_SYNC_WAIT_SEC,
    )


def _mutation_filters(state: dict[str, Any]) -> dict[str, Any]:
    return sync_filters(
        repo_ids=list_filter("in", [state["mutation_repo"]["full_name"]]),
    )


def _connector_name(kind: str) -> str:
    return f"github-teams-{kind}-{GH_IT_RUN_ID}-{uuid.uuid4().hex[:6]}"


# =============================================================================
# TestGitHubTeamsConnector — sync baseline and structure
# =============================================================================


class TestGitHubTeamsConnector:
    """Full-sync baseline, record-group hierarchy, identity."""

    @pytest.mark.order(1)
    async def test_tc_sync_001_full_sync_graph_validation(
        self,
        github_connector: dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-SYNC-001: validate the graph after the fixture's full sync.

        Counts are asserted as *structural invariants* (which hold exactly, whatever
        the fixture contains) plus presence of every record the primary repo should
        have produced. A global exact count would also have to model the public repo's
        contents, which the fixture deliberately does not enumerate — and an exact
        total would be the first thing to break when someone adds a file to a fixture
        repo, without catching any real defect.
        """
        connector_id = github_connector["connector_id"]

        total = await graph_provider.count_records(connector_id, scoped=True)
        by_type = {
            rt: await graph_provider.count_records_by_type(connector_id, rt, scoped=True)
            for rt in (
                RecordType.TICKET.value,
                RecordType.PULL_REQUEST.value,
                RecordType.CODE_FILE.value,
                RecordType.FILE.value,
            )
        }
        assert total > 0, "full sync produced no records"
        assert total == sum(by_type.values()), (
            f"records {total} != sum of known types {by_type} — an unexpected record "
            "type was synced"
        )

        # Every record belongs to exactly one record group and inherits permissions:
        # the connector puts the ACL on the repo group alone and every record inherits
        # into its child group, so a record with a direct PERMISSION edge is a bug.
        rg_edges = await graph_provider.count_record_group_edges(connector_id)
        assert rg_edges == total, (
            f"every record needs one BELONGS_TO→RecordGroup ({rg_edges} != {total})"
        )
        inherit = await graph_provider.count_inherit_permissions_edges(connector_id)
        assert inherit == total, (
            f"every record needs one INHERIT_PERMISSIONS edge ({inherit} != {total})"
        )

        # Primary repo content is all present, by external id.
        primary_id = github_connector["primary_repo"]["id"]
        for issue in github_connector["primary_issues"]:
            external_id = f"{primary_id}/issues/{issue['number']}"
            assert await graph_provider.get_record_by_external_id(connector_id, external_id), (
                f"issue #{issue['number']} missing from the graph ({external_id})"
            )
        for pr in github_connector["primary_pulls"]:
            external_id = f"{primary_id}/pull/{pr['number']}"
            assert await graph_provider.get_record_by_external_id(connector_id, external_id), (
                f"PR #{pr['number']} missing from the graph ({external_id})"
            )

        graph_app = await graph_provider.get_app_metadata_by_connector_id(connector_id)
        assert graph_app is not None, f"apps document missing for connector {connector_id}"
        assert_graph_entity_matches(
            GitHubExpected.app_metadata_for_full_sync_baseline(github_connector),
            graph_app,
            entity="app_metadata",
            skip_compare=frozenset({
                "created_at_timestamp", "updated_at_timestamp", "auth_type", "is_active",
                "is_agent_active", "is_configured", "is_authenticated", "created_by",
                "updated_by", "status", "is_locked",
            }),
        )
        logger.info("TC-SYNC-001 passed: %d records %s", total, by_type)

    @pytest.mark.order(2)
    async def test_tc_gh_rg_001_record_group_hierarchy(
        self,
        github_connector: dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-GH-RG-001: org → repo → three child groups, and the App edge.

        The App-edge count is the load-bearing assertion. The processor creates a
        RecordGroup→App edge only for a group with **no parent**, so only the org group
        gets one. A regression that gives the repo group an App edge (or removes the
        org group's) makes connector stats read zero for an entire sync while every
        record is in fact stored and queryable — which is exactly what happened once.
        """
        connector_id = github_connector["connector_id"]
        primary = github_connector["primary_repo"]

        org_group = await graph_provider.get_record_group_by_external_id(
            connector_id, f"org-{github_connector['org_id']}",
        )
        assert org_group is not None, "org record group missing"
        await assert_graph_entity_with_edges(
            GitHubExpected.org_record_group(
                org_login=github_connector["org"],
                org_id=github_connector["org_id"],
                connector_id=connector_id,
            ),
            org_group, entity="record_group",
            connector_id=connector_id, graph_provider=graph_provider,
            skip_compare=_GROUP_SKIP,
        )

        repo_group = await graph_provider.get_record_group_by_external_id(
            connector_id, str(primary["id"]),
        )
        assert repo_group is not None, "repo record group missing"
        # Fields only: assert_graph_entity_with_edges would additionally demand a
        # belongsTo → App edge, which by design exists ONLY on the parentless org
        # group — the very invariant asserted at the end of this test.
        assert_graph_entity_matches(
            GitHubExpected.repo_record_group(primary, connector_id=connector_id),
            repo_group, entity="record_group", skip_compare=_GROUP_SKIP,
        )
        assert await _group_edge_count(
            graph_provider, from_group=repo_group, to_group=org_group,
            edge_collection=CollectionNames.BELONGS_TO.value,
        ) == 1, "repo group must belong to the org group"
        assert await _group_edge_count(
            graph_provider, from_group=repo_group, to_group=org_group,
            edge_collection=CollectionNames.INHERIT_PERMISSIONS.value,
        ) == 0, (
            "the repo group must NOT inherit from the org group: the org group carries "
            "the union of every repo's grants, so inheriting it would leak each repo to "
            "every other repo's users"
        )

        for kind in ("work-items", "pull-requests", "code-repository"):
            child = await graph_provider.get_record_group_by_external_id(
                connector_id, f"{primary['id']}-{kind}",
            )
            assert child is not None, f"child record group {kind} missing"
            assert_graph_entity_matches(
                GitHubExpected.child_record_group(
                    primary, kind=kind, connector_id=connector_id,
                ),
                child, entity="record_group", skip_compare=_GROUP_SKIP,
            )
            assert await _group_edge_count(
                graph_provider, from_group=child, to_group=repo_group,
                edge_collection=CollectionNames.BELONGS_TO.value,
            ) == 1, f"child group {kind} must belong to the repo group"
            # This edge is what makes a record resolve in two hops: the ACL lives on
            # the repo group alone, and each child inherits it.
            assert await _group_edge_count(
                graph_provider, from_group=child, to_group=repo_group,
                edge_collection=CollectionNames.INHERIT_PERMISSIONS.value,
            ) == 1, (
                f"child group {kind} must inherit permissions from the repo group, or "
                "its records resolve to nobody"
            )

        # One App edge per synced org — not one per group.
        app_edges = await graph_provider.count_app_record_group_edges(connector_id)
        assert app_edges == 1, (
            f"expected exactly 1 RecordGroup→App edge (the parentless org group), got "
            f"{app_edges}. Both fixture repos live in one org, so a different number "
            "means the App edge moved off the org group — connector stats would read 0."
        )
        logger.info("TC-GH-RG-001 passed: hierarchy verified, %d App edge(s)", app_edges)

    @pytest.mark.order(3)
    async def test_tc_gh_user_001_identity(
        self,
        github_connector: dict[str, Any],
        graph_provider: GraphProviderProtocol,
        pipeshub_client: PipeshubClient,
    ) -> None:
        """TC-GH-USER-001: AppUsers keyed by numeric id, plus the team→app gate edge."""
        connector_id = github_connector["connector_id"]
        emails = github_connector["app_user_emails"]
        if not emails:
            pytest.skip(
                "No GitHub principal resolved to a PipesHub identity — the fixture org's "
                "members need emails that match PipesHub users for identity assertions."
            )

        for source_id in sorted(emails):
            user = await graph_provider.get_user_by_source_id(
                source_user_id=source_id, connector_id=connector_id,
            )
            # The lookup itself is the assertion: get_user_by_source_id queries on
            # sourceUserId, so a hit proves the AppUser is keyed by the GitHub numeric
            # id rather than the login. The field is not hydrated onto the model, so
            # reading it back would only ever return None.
            assert user is not None, (
                f"AppUser missing for GitHub id {source_id} — the connector binds "
                "principals by numeric id, which is what permissions, assignees and "
                "reporters all resolve through"
            )
            await assert_user_app_edge(
                source_id, connector_id=connector_id, graph_provider=graph_provider,
            )

        # Bots hold no PipesHub identity and are filtered at discovery.
        bot_ids = {
            str(c["id"]) for c in github_connector["primary_collaborators"]
            if c.get("type") and c["type"] != "User" and c.get("id") is not None
        }
        for bot_id in bot_ids:
            assert await graph_provider.get_user_by_source_id(
                source_user_id=bot_id, connector_id=connector_id,
            ) is None, f"bot account {bot_id} was synced as an AppUser"

        # The coarse gate edge: (Teams all_{org})-[USER_APP_RELATION]->(App). It grants
        # nothing on its own, but the record-access query pre-filters on
        # `connectorId IN user_apps_ids`, so without it a public repo's ORG grant is
        # unreachable for anyone whose GitHub account never resolved to an AppUser.
        gate_edges = await graph_provider.find_edges_between(
            CollectionNames.TEAMS.value, f"all_{pipeshub_client.org_id}",
            CollectionNames.APPS.value, connector_id,
            CollectionNames.USER_APP_RELATION.value,
        )
        assert gate_edges, (
            "missing (Teams all_{org})→(App) USER_APP_RELATION gate edge written by "
            "ensure_team_app_edge at sync start"
        )
        logger.info("TC-GH-USER-001 passed: %d identities, gate edge present", len(emails))


# =============================================================================
# TestGitHubTeamsIssues
# =============================================================================


class TestGitHubTeamsIssues:
    """Issue records, relations, and streamed content."""

    @pytest.mark.order(4)
    async def test_tc_gh_issue_001_ticket_properties(
        self,
        github_connector: dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-GH-ISSUE-001: reference issue has correct TICKET properties + edges."""
        connector_id = github_connector["connector_id"]
        repo_id = github_connector["primary_repo"]["id"]
        issue = github_connector["reference_issue"]
        external_id = f"{repo_id}/issues/{issue['number']}"

        actual = await graph_provider.get_typed_record_by_external_id(connector_id, external_id)
        assert actual is not None, f"typed TICKET record missing for {external_id}"

        expected = GitHubExpected.ticket_record(
            issue, connector_id=connector_id, repo_id=repo_id,
            emails=github_connector["app_user_emails"],
        )
        await assert_graph_entity_with_edges(
            expected, actual, entity="ticket_record",
            connector_id=connector_id, graph_provider=graph_provider,
            skip_compare=_TICKET_SKIP,
        )

        if not issue.get("issue_field_values"):
            logger.info(
                "TC-GH-ISSUE-001: no issue_field_values on #%s — priority mapping not "
                "exercised (org-level issue fields are plan-dependent)", issue["number"],
            )
        logger.info("TC-GH-ISSUE-001 passed: issue #%s validated", issue["number"])

    @pytest.mark.order(5)
    async def test_tc_gh_issue_002_relations(
        self,
        github_connector: dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-GH-ISSUE-002: hierarchy, external related records, and entity relations.

        Three shapes in one case because they all read the same single sync — splitting
        them would triple the fixture surface without adding coverage.
        """
        connector_id = github_connector["connector_id"]
        repo_id = github_connector["primary_repo"]["id"]
        checked = 0

        # (a) Sub-issue hierarchy.
        parent = github_connector["subissue_parent"]
        child = github_connector["subissue_child"]
        if parent and child:
            parent_external = f"{repo_id}/issues/{parent['number']}"
            child_external = f"{repo_id}/issues/{child['number']}"
            child_record = await graph_provider.get_typed_record_by_external_id(
                connector_id, child_external,
            )
            assert child_record is not None, f"sub-issue record missing ({child_external})"
            assert str(child_record.parent_external_record_id) == parent_external, (
                f"sub-issue parent is {child_record.parent_external_record_id!r}, "
                f"expected {parent_external!r}"
            )
            incoming = await graph_provider.get_record_incoming_relations(
                connector_id, child_external, "PARENT_CHILD",
            )
            assert parent_external in incoming, (
                f"PARENT_CHILD {parent_external} → {child_external} missing ({incoming!r})"
            )
            if not child.get("type"):
                child_type = getattr(child_record.type, "value", child_record.type)
                assert child_type == "SUBTASK", (
                    "an issue with a parent and no GitHub issue type must be typed "
                    f"SUBTASK, got {child_record.type!r}"
                )
            checked += 1
        else:
            logger.info("TC-GH-ISSUE-002: no sub-issue pair discovered — hierarchy skipped")

        # (b) External related record: exactly one BLOCKS edge, no inverse.
        blocker = github_connector["blocking_issue"]
        if blocker:
            blocker_external = f"{repo_id}/issues/{blocker['number']}"
            outgoing = await graph_provider.get_record_outgoing_relations(
                connector_id, blocker_external, "BLOCKS",
            )
            assert outgoing, f"no outgoing BLOCKS edge from {blocker_external}"
            for target in outgoing:
                # GitHub reports one dependency from both ends; the connector reads only
                # the blocking side, so a second inverse edge means both ends were read
                # and every link is now duplicated.
                inverse = await graph_provider.get_record_outgoing_relations(
                    connector_id, target, "BLOCKS",
                )
                assert blocker_external not in inverse, (
                    f"inverse BLOCKS edge {target} → {blocker_external} exists; GitHub "
                    "reports one dependency from both ends and only the blocking side "
                    "should be modelled"
                )
            checked += 1
        else:
            logger.info("TC-GH-ISSUE-002: no blocking issue discovered — BLOCKS skipped")

        # (c) Entity relations on the multi-assignee issue.
        multi = github_connector["multi_assignee_issue"]
        emails = github_connector["app_user_emails"]
        if multi:
            multi_external = f"{repo_id}/issues/{multi['number']}"
            record = await graph_provider.get_typed_record_by_external_id(
                connector_id, multi_external,
            )
            assert record is not None, f"record missing for {multi_external}"

            assignees = [a for a in multi["assignees"] if a.get("login")]
            primary_login = assignees[0]["login"]
            assert record.assignee == primary_login, (
                f"assignee must be GitHub's FIRST assignee ({primary_login}), got "
                f"{record.assignee!r} — a joined string matches no user and produces "
                "zero ASSIGNED_TO edges"
            )
            # assignee_source_id is written but never hydrated back (see
            # _TICKET_UNHYDRATED), so the observable consequence is asserted instead:
            # exactly one ASSIGNED_TO edge, built from the primary assignee's email.
            expected_email = emails.get(str(assignees[0]["id"]))
            assert record.assignee_email == expected_email, (
                "assignee_email must be the PRIMARY assignee's or None — never borrowed "
                f"from a co-assignee (expected {expected_email!r}, got "
                f"{record.assignee_email!r})"
            )

            assigned = await graph_provider.get_record_outgoing_entity_relations(
                connector_id, multi_external, "ASSIGNED_TO",
            )
            assert len(assigned) <= 1, (
                f"expected at most one ASSIGNED_TO edge (single-valued assignee_email), "
                f"got {assigned!r}"
            )
            if expected_email:
                assert assigned, "primary assignee resolved to a user but no ASSIGNED_TO edge"

            creator = multi.get("user") or {}
            if emails.get(str(creator.get("id"))):
                for edge_type in ("CREATED_BY", "REPORTED_BY"):
                    related = await graph_provider.get_record_outgoing_entity_relations(
                        connector_id, multi_external, edge_type,
                    )
                    assert related, f"{edge_type} edge missing on {multi_external}"
            checked += 1
        else:
            logger.info("TC-GH-ISSUE-002: no multi-assignee issue — entity relations skipped")

        if checked == 0:
            pytest.skip("None of the three relation shapes exist in the fixture repo")
        logger.info("TC-GH-ISSUE-002 passed: %d relation shape(s)", checked)

    @pytest.mark.order(6)
    async def test_tc_gh_issue_blocks_001_streamed_blocks_and_attachment(
        self,
        github_connector: dict[str, Any],
        graph_provider: GraphProviderProtocol,
        pipeshub_client: PipeshubClient,
    ) -> None:
        """TC-GH-ISSUE-BLOCKS-001: streamed issue blocks vs snapshot, plus the
        attachment FileRecord.

        Comment blocks must be present. The "Index Comments" filter was deleted because
        it stripped every comment from manually-indexed tickets — a regression that
        reinstates that gating would show up here and nowhere else.

        Note this case streams a ticket, which persists newly-discovered attachment
        records as a side effect. It is ordered before anything that counts records.
        """
        connector_id = github_connector["connector_id"]
        repo_id = github_connector["primary_repo"]["id"]
        number = github_connector["blocks_issue_number"]
        external_id = f"{repo_id}/issues/{number}"

        record = await graph_provider.get_record_by_external_id(connector_id, external_id)
        assert record is not None, (
            f"frozen blocks issue #{number} not synced. Set GH_TEAMS_BLOCKS_ISSUE_NUMBER "
            "to an issue that exists in the primary repo."
        )

        resp = pipeshub_client.stream_record(record.id)
        assert resp.status_code == 200, f"stream_record HTTP {resp.status_code}"
        content_type = (resp.headers.get("content-type") or "").lower()
        assert "application/blocks" in content_type, f"unexpected content-type {content_type!r}"

        parsed = await parse_connector_blocks_via_processor(resp.content)
        actual = normalize_blocks_container(parsed)
        if os.getenv(ENV_BLOCKS_BOOTSTRAP) == "1":
            bootstrap_expected(ISSUE_BLOCKS_PATH, actual)
        expected = load_expected(ISSUE_BLOCKS_PATH)
        assert actual == expected, (
            "Parsed issue blocks do not match the expected snapshot. If the fixture "
            f"issue was edited, regenerate with {ENV_BLOCKS_BOOTSTRAP}=1 and review."
        )

        # Attachment FileRecord — non-image only. Images are inlined as base64 and
        # deliberately produce no record.
        attachment_issue = github_connector["attachment_issue"]
        attachment_url = github_connector["attachment_url"]
        if not (attachment_issue and attachment_url):
            logger.info("TC-GH-ISSUE-BLOCKS-001: no non-image attachment — record check skipped")
            logger.info("TC-GH-ISSUE-BLOCKS-001 passed (blocks only)")
            return

        parent_external = f"{repo_id}/issues/{attachment_issue['number']}"
        parent_record = await graph_provider.get_record_by_external_id(
            connector_id, parent_external,
        )
        assert parent_record is not None, f"attachment parent missing ({parent_external})"

        if parent_external != external_id:
            # The attachment hangs off a different issue; stream it so the record exists.
            other = pipeshub_client.stream_record(parent_record.id)
            assert other.status_code == 200

        attachment = await graph_provider.get_typed_record_by_external_id(
            connector_id, attachment_url,
        )
        assert attachment is not None, (
            f"attachment FileRecord missing. Its external id is the raw attachment URL "
            f"verbatim ({attachment_url!r}), not a derived id."
        )
        assert attachment.is_dependent_node is True, "attachment must be a dependent node"
        assert str(attachment.parent_node_id) == str(parent_record.id), (
            "parent_node_id must be the parent issue's true DB id"
        )
        assert attachment.weburl == parent_record.weburl, (
            "attachment weburl must point at the parent issue page (previewable), while "
            "the raw download URL lives in external_record_id"
        )
        logger.info("TC-GH-ISSUE-BLOCKS-001 passed: blocks + attachment validated")


# =============================================================================
# TestGitHubTeamsPullRequests
# =============================================================================


class TestGitHubTeamsPullRequests:

    @pytest.mark.order(7)
    async def test_tc_gh_pr_001_pull_request_properties(
        self,
        github_connector: dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-GH-PR-001: merged PR has correct PULL_REQUEST properties + edges."""
        connector_id = github_connector["connector_id"]
        repo_id = github_connector["primary_repo"]["id"]
        pr = github_connector["merged_pr"]
        if not pr:
            pytest.skip("No merged PR in the primary repo — seed one (see README.md)")

        # Singular "pull", unlike the plural "issues".
        external_id = f"{repo_id}/pull/{pr['number']}"
        actual = await graph_provider.get_typed_record_by_external_id(connector_id, external_id)
        assert actual is not None, f"typed PULL_REQUEST record missing for {external_id}"

        expected = GitHubExpected.pull_request_record(
            pr, connector_id=connector_id, repo_id=repo_id,
            emails=github_connector["app_user_emails"],
        )
        await assert_graph_entity_with_edges(
            expected, actual, entity="pull_request_record",
            connector_id=connector_id, graph_provider=graph_provider,
            skip_compare=_RECORD_SKIP,
        )
        assert str(actual.status) == "DONE", (
            "a merged PR maps to DONE via merged_at; reading `.merged` instead would "
            "both be wrong on the listing payload and force a per-PR fetch"
        )

        logger.info("TC-GH-PR-001 passed: PR #%s validated", pr["number"])

    @pytest.mark.order(8)
    async def test_tc_gh_pr_blocks_001_streamed_blocks(
        self,
        github_connector: dict[str, Any],
        graph_provider: GraphProviderProtocol,
        pipeshub_client: PipeshubClient,
    ) -> None:
        """TC-GH-PR-BLOCKS-001: streamed PR blocks vs snapshot.

        Covers description, commit list, per-file diffs with their inline review
        threads, and conversation comments — the whole PR block builder in one compare.
        """
        connector_id = github_connector["connector_id"]
        repo_id = github_connector["primary_repo"]["id"]
        number = github_connector["blocks_pr_number"]
        external_id = f"{repo_id}/pull/{number}"

        record = await graph_provider.get_record_by_external_id(connector_id, external_id)
        assert record is not None, (
            f"frozen blocks PR #{number} not synced. Set GH_TEAMS_BLOCKS_PR_NUMBER to a "
            "PR that exists in the primary repo."
        )

        resp = pipeshub_client.stream_record(record.id)
        assert resp.status_code == 200, f"stream_record HTTP {resp.status_code}"
        assert "application/blocks" in (resp.headers.get("content-type") or "").lower()

        parsed = await parse_connector_blocks_via_processor(resp.content)
        actual = normalize_blocks_container(parsed)
        if os.getenv(ENV_BLOCKS_BOOTSTRAP) == "1":
            bootstrap_expected(PR_BLOCKS_PATH, actual)
        expected = load_expected(PR_BLOCKS_PATH)
        assert actual == expected, (
            "Parsed PR blocks do not match the expected snapshot. If the fixture PR was "
            f"edited, regenerate with {ENV_BLOCKS_BOOTSTRAP}=1 and review."
        )
        logger.info("TC-GH-PR-BLOCKS-001 passed: PR #%s blocks validated", number)


# =============================================================================
# TestGitHubTeamsCodeFiles
# =============================================================================


class TestGitHubTeamsCodeFiles:

    @pytest.mark.order(9)
    async def test_tc_gh_code_001_code_and_folder_properties(
        self,
        github_connector: dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-GH-CODE-001: CodeFileRecord and folder FileRecord properties."""
        connector_id = github_connector["connector_id"]
        repo = github_connector["primary_repo"]
        tree = github_connector["primary_tree"]
        path = github_connector["nested_code_path"]
        if not path:
            pytest.skip("No nested code path in the primary repo — seed one (see README.md)")

        sha = next(
            (e["sha"] for e in tree if e.get("path") == path and e.get("type") == "blob"), None,
        )
        assert sha, f"no blob sha for {path}"

        external_id = f"/{repo['id']}/blob/{path}"
        actual = await graph_provider.get_typed_record_by_external_id(connector_id, external_id)
        assert actual is not None, f"typed CODE_FILE record missing for {external_id}"
        await assert_graph_entity_with_edges(
            GitHubExpected.code_file_record(
                repo=repo, path=path, sha=sha, connector_id=connector_id,
            ),
            actual, entity="code_file_record",
            connector_id=connector_id, graph_provider=graph_provider,
            skip_compare=_CODE_SKIP,
        )

        # Folder record for the file's immediate parent.
        parent_path = path.rpartition("/")[0]
        folder_external = f"/{repo['id']}/tree/{parent_path}"
        folder = await graph_provider.get_typed_record_by_external_id(
            connector_id, folder_external,
        )
        assert folder is not None, f"folder record missing for {folder_external}"
        assert folder.is_file is False, (
            "folder records must carry is_file=False. A past incident rebuilt records "
            "from bare graph nodes, defaulting is_file to True and flipping every "
            "touched folder into a file."
        )

        # Extensionless files get extension=None, not "".
        ext_path = github_connector["extensionless_code_path"]
        if ext_path:
            ext_record = await graph_provider.get_typed_record_by_external_id(
                connector_id, f"/{repo['id']}/blob/{ext_path}",
            )
            assert ext_record is not None, f"record missing for {ext_path}"
            assert ext_record.extension is None, (
                f"{ext_path} has no extension, so extension must be None, not "
                f"{ext_record.extension!r}"
            )
        logger.info("TC-GH-CODE-001 passed: %s + parent folder validated", path)

    @pytest.mark.order(10)
    async def test_tc_gh_code_hier_001_folder_hierarchy(
        self,
        github_connector: dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-GH-CODE-HIER-001: full PARENT_CHILD chain + folder inventory."""
        connector_id = github_connector["connector_id"]
        repo = github_connector["primary_repo"]
        tree = github_connector["primary_tree"]
        path = github_connector["nested_code_path"]
        if not path or "/" not in path:
            pytest.skip("No nested code path in the primary repo — seed one (see README.md)")

        # Walk file → parent → ... → root, asserting each link.
        current = f"/{repo['id']}/blob/{path}"
        segments = path.split("/")[:-1]
        for depth in range(len(segments), 0, -1):
            expected_parent = f"/{repo['id']}/tree/{'/'.join(segments[:depth])}"
            actual_parent = await graph_provider.get_record_parent_external_id(
                connector_id, current,
            )
            assert actual_parent == expected_parent, (
                f"parent of {current} is {actual_parent!r}, expected {expected_parent!r}"
            )
            incoming = await graph_provider.get_record_incoming_relations(
                connector_id, current, "PARENT_CHILD",
            )
            assert expected_parent in incoming, (
                f"PARENT_CHILD {expected_parent} → {current} missing ({incoming!r})"
            )
            current = expected_parent

        # Top-level folder has no parent.
        root_parent = await graph_provider.get_record_parent_external_id(connector_id, current)
        assert root_parent in (None, ""), (
            f"top-level folder {current} must have no parent, got {root_parent!r}"
        )

        # Folder inventory: one record per distinct directory the synced files imply.
        expected_dirs = tree_dirs(tree)
        folder_count = await graph_provider.count_records_by_type(
            connector_id, RecordType.FILE.value, scoped=True,
        )
        assert folder_count >= len(expected_dirs), (
            f"graph has {folder_count} FILE records but the primary repo alone implies "
            f"{len(expected_dirs)} directories"
        )
        for directory in sorted(expected_dirs):
            assert await graph_provider.get_record_by_external_id(
                connector_id, f"/{repo['id']}/tree/{directory}",
            ), f"folder record missing for directory {directory!r}"
        logger.info("TC-GH-CODE-HIER-001 passed: %d directories", len(expected_dirs))

    @pytest.mark.order(11)
    async def test_tc_gh_code_ts_001_source_timestamps(
        self,
        github_connector: dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-GH-CODE-TS-001: code files and folders carry source timestamps.

        The backfill that fills these is scheduled fire-and-forget AFTER run_sync
        returns, so it is still running when the sync reports finished. This polls for
        arrival and never asserts absence — a snapshot assertion here is a guaranteed
        flake, in either direction.
        """
        connector_id = github_connector["connector_id"]
        repo = github_connector["primary_repo"]
        path = github_connector["nested_code_path"]
        if not path:
            pytest.skip("No nested code path in the primary repo — seed one (see README.md)")

        file_external = f"/{repo['id']}/blob/{path}"
        folder_external = f"/{repo['id']}/tree/{path.rpartition('/')[0]}"

        async def _dated(external_id: str) -> bool:
            record = await graph_provider.get_record_by_external_id(connector_id, external_id)
            return bool(
                record
                and getattr(record, "source_created_at", None)
                and getattr(record, "source_updated_at", None)
            )

        await wait_until_graph_condition(
            connector_id,
            check=lambda: _dated(file_external),
            timeout=GH_TIMESTAMP_BACKFILL_WAIT_SEC,
            description=f"source timestamps on {path}",
        )
        await wait_until_graph_condition(
            connector_id,
            check=lambda: _dated(folder_external),
            timeout=GH_TIMESTAMP_BACKFILL_WAIT_SEC,
            description=f"aggregated timestamps on folder {folder_external}",
        )

        file_record = await graph_provider.get_record_by_external_id(connector_id, file_external)
        folder_record = await graph_provider.get_record_by_external_id(connector_id, folder_external)
        assert folder_record.source_created_at <= file_record.source_created_at, (
            "a folder's created date is the MIN over its children, so it cannot be "
            "later than a file it contains"
        )
        assert folder_record.source_updated_at >= file_record.source_updated_at, (
            "a folder's updated date is the MAX over its children, so it cannot be "
            "earlier than a file it contains"
        )
        logger.info("TC-GH-CODE-TS-001 passed: file and folder timestamps aggregate correctly")


# =============================================================================
# TestGitHubTeamsPermissions
# =============================================================================


class TestGitHubTeamsPermissions:

    @pytest.mark.order(12)
    async def test_tc_gh_perm_001_private_repo_acl(
        self,
        github_connector: dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-GH-PERM-001: private repo — collaborator ACL on the repo group only.

        The ACL lives on the ``{repo.id}`` group alone; the three child groups carry an
        empty ACL and an INHERIT_PERMISSIONS edge, so a record resolves in two hops.
        Writing the same ACL four times would work but quadruples the delete-and-recreate
        cost on every sync.
        """
        connector_id = github_connector["connector_id"]
        repo = github_connector["primary_repo"]
        emails = github_connector["app_user_emails"]

        repo_group_perms = await graph_provider.count_permission_edges_to_record_groups(
            connector_id, str(repo["id"]),
        )
        expected_grants = expected_repo_grant_emails(
            github_connector["primary_collaborators"], emails,
        )
        assert repo_group_perms == len(expected_grants), (
            f"repo group has {repo_group_perms} PERMISSION edge(s), expected "
            f"{len(expected_grants)} ({sorted(expected_grants)}). A principal with no "
            "PipesHub identity has nothing to grant to, a custom repository role maps "
            "to no PermissionType, and grants are deduped per email — all three "
            "legitimately reduce the count."
        )

        repo_group = await graph_provider.get_record_group_by_external_id(
            connector_id, str(repo["id"]),
        )
        assert repo_group is not None
        for kind in ("work-items", "pull-requests", "code-repository"):
            child_perms = await graph_provider.count_permission_edges_to_record_groups(
                connector_id, f"{repo['id']}-{kind}",
            )
            assert child_perms == 0, (
                f"child group {kind} must carry an EMPTY ACL and inherit from the repo "
                f"group, but has {child_perms} PERMISSION edge(s)"
            )
            child_group = await graph_provider.get_record_group_by_external_id(
                connector_id, f"{repo['id']}-{kind}",
            )
            # inherit_permissions is not hydrated on read-back, so assert the edge.
            assert await _group_edge_count(
                graph_provider, from_group=child_group, to_group=repo_group,
                edge_collection=CollectionNames.INHERIT_PERMISSIONS.value,
            ) == 1, f"child group {kind} does not inherit from the repo group"

        # A private repo has no visibility floor: access comes solely from collaborators.
        assert repo.get("visibility") == "private"
        logger.info(
            "TC-GH-PERM-001 passed: %d collaborator grant(s) on the repo group only",
            repo_group_perms,
        )

    @pytest.mark.order(13)
    async def test_tc_gh_perm_002_public_repo_org_grant(
        self,
        github_connector: dict[str, Any],
        graph_provider: GraphProviderProtocol,
        pipeshub_client: PipeshubClient,
    ) -> None:
        """TC-GH-PERM-002: public repo — the visibility-derived ORG grant.

        A public repo is readable by anyone with a GitHub account, so mirroring it as
        readable by the whole PipesHub org matches reality.

        The org group *does* legitimately carry an ORG edge — it accumulates the union
        of every repo's grants. What keeps that union from leaking is that nothing
        inherits FROM it: the repo group deliberately does not, which is what this test
        asserts alongside the grant itself.
        """
        connector_id = github_connector["connector_id"]
        public = github_connector["public_repo"]

        public_group = await graph_provider.get_record_group_by_external_id(
            connector_id, str(public["id"]),
        )
        assert public_group is not None, "public repo record group missing"

        # The ORG grant is materialised as a PERMISSION edge from the organization
        # node to the record group. Counting edges alone would pass on collaborator
        # grants and never notice the visibility-derived one was missing.
        org_edges = await graph_provider.find_edges_between(
            CollectionNames.ORGS.value, pipeshub_client.org_id,
            CollectionNames.RECORD_GROUPS.value, public_group.id,
            CollectionNames.PERMISSION.value,
        )
        assert org_edges, (
            f"public repo {public['full_name']} has no organization → record-group "
            "PERMISSION edge; the visibility-derived Permission(READ, ORG) is missing"
        )
        repo_group_perms = await graph_provider.count_permission_edges_to_record_groups(
            connector_id, str(public["id"]),
        )

        # The org group legitimately carries the union of every repo's grants, which is
        # why nothing may inherit FROM it — the repo group deliberately does not.
        org_group = await graph_provider.get_record_group_by_external_id(
            connector_id, f"org-{github_connector['org_id']}",
        )
        assert org_group is not None
        assert await _group_edge_count(
            graph_provider, from_group=public_group, to_group=org_group,
            edge_collection=CollectionNames.INHERIT_PERMISSIONS.value,
        ) == 0, (
            "the public repo group must not inherit from the org group; the org group "
            "holds the union of every repo's grants in this org"
        )
        logger.info(
            "TC-GH-PERM-002 passed: %d grant(s) on the public repo group",
            repo_group_perms,
        )


# =============================================================================
# TestGitHubTeamsIndexing
# =============================================================================


class TestGitHubTeamsIndexing:

    @pytest.mark.order(14)
    async def test_tc_gh_idx_001_indexing_terminal_state(
        self,
        github_connector: dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-GH-IDX-001: issue, PR and code file reach COMPLETED; oversized file is
        AUTO_INDEX_OFF with a reason."""
        connector_id = github_connector["connector_id"]
        repo = github_connector["primary_repo"]
        repo_id = repo["id"]

        targets = [f"{repo_id}/issues/{github_connector['reference_issue']['number']}"]
        if github_connector["merged_pr"]:
            targets.append(f"{repo_id}/pull/{github_connector['merged_pr']['number']}")
        if github_connector["nested_code_path"]:
            targets.append(f"/{repo_id}/blob/{github_connector['nested_code_path']}")

        async def _completed(external_id: str) -> bool:
            record = await graph_provider.get_record_by_external_id(connector_id, external_id)
            return bool(
                record
                and str(getattr(record, "indexing_status", "")) == ProgressStatus.COMPLETED.value
            )

        for external_id in targets:
            await wait_until_graph_condition(
                connector_id,
                check=lambda eid=external_id: _completed(eid),
                timeout=GH_INDEXING_WAIT_SEC,
                description=f"indexing COMPLETED for {external_id}",
            )

        oversized = os.getenv(ENV_OVERSIZED_PATH)
        if oversized:
            record = await graph_provider.get_record_by_external_id(
                connector_id, f"/{repo_id}/blob/{oversized}",
            )
            assert record is not None, (
                f"oversized file {oversized} must still get a record — it stays visible "
                "and name-searchable, only its content is not indexed"
            )
            assert str(record.indexing_status) == ProgressStatus.AUTO_INDEX_OFF.value
            assert record.reason, "an AUTO_INDEX_OFF oversized file must carry a reason"
        else:
            logger.info(
                "TC-GH-IDX-001: %s unset — oversized-file handling not exercised",
                ENV_OVERSIZED_PATH,
            )
        logger.info("TC-GH-IDX-001 passed: %d record(s) indexed", len(targets))


# =============================================================================
# TestGitHubTeamsIncremental — dedicated connectors, mutation repo
# =============================================================================


class TestGitHubTeamsIncremental:
    """Mutation cases. Each owns its connector and asserts only by external id."""

    @pytest.mark.order(15)
    async def test_tc_incr_issue_001_new_issue_and_update(
        self,
        github_connector: dict[str, Any],
        github_rest: Any,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-INCR-ISSUE-001: a new issue + sub-issue arrive on the next incremental,
        then a title edit bumps the version and the revision."""
        state = github_connector
        org = state["org"]
        repo_name = state["mutation_repo_name"]
        repo_id = state["mutation_repo"]["id"]

        parent_num: int | None = None
        child_num: int | None = None
        async with dedicated_connector(
            pipeshub_client, graph_provider,
            token=state["token"], name=_connector_name("incr-issue"),
            filters=_mutation_filters(state),
        ) as connector_id:
            try:
                parent = await create_issue(
                    github_rest, org, repo_name,
                    title=artifact_title("IncrIssue"), body="Incremental sync test issue.",
                )
                parent_num = parent["number"]
                child = await create_issue(
                    github_rest, org, repo_name,
                    title=artifact_title("SubIssue"), body="Sub-issue of the above.",
                )
                child_num = child["number"]

                sub_issues_supported = True
                try:
                    await add_sub_issue(github_rest, org, repo_name, parent_num, child["id"])
                except Exception as e:
                    sub_issues_supported = False
                    logger.info(
                        "TC-INCR-ISSUE-001: sub-issue link unavailable (%s); hierarchy "
                        "assertion skipped", e,
                    )

                await _resync(pipeshub_client, graph_provider, connector_id)

                parent_external = f"{repo_id}/issues/{parent_num}"
                child_external = f"{repo_id}/issues/{child_num}"
                before = await wait_for_record_by_external_id(
                    graph_provider, connector_id, parent_external,
                    description="TC-INCR-ISSUE-001 new issue (the `since` clock)",
                )
                await wait_for_record_by_external_id(
                    graph_provider, connector_id, child_external,
                    description="TC-INCR-ISSUE-001 second new issue",
                )

                if sub_issues_supported:
                    child_record = await graph_provider.get_record_by_external_id(
                        connector_id, child_external,
                    )
                    assert str(child_record.parent_external_record_id) == parent_external, (
                        "sub-issue link created before the sync must produce a parent "
                        f"reference, got {child_record.parent_external_record_id!r}"
                    )

                # --- update leg ---
                old_version = int(before.version)
                new_title = artifact_title("Edited")
                await update_issue(github_rest, org, repo_name, parent_num, title=new_title)
                await add_comment(
                    github_rest, org, repo_name, parent_num, "Comment added by TC-INCR-ISSUE-001.",
                )
                pipeshub_client.wait(5)
                await _resync(pipeshub_client, graph_provider, connector_id)

                after = await graph_provider.get_record_by_external_id(
                    connector_id, parent_external,
                )
                assert after is not None, "record disappeared after update"
                assert after.version == old_version + 1, (
                    f"expected version {old_version + 1}, got {after.version}"
                )
                assert new_title in (after.record_name or ""), (
                    f"edited title not reflected: {after.record_name!r}"
                )
                live = await get_issue(github_rest, org, repo_name, parent_num)
                assert str(after.external_revision_id) == str(epoch_ms(live["updated_at"])), (
                    "external_revision_id must track the source updated_at in epoch ms"
                )
                logger.info(
                    "TC-INCR-ISSUE-001 passed: version %s → %s", old_version, after.version,
                )
            finally:
                for number in (child_num, parent_num):
                    if number:
                        await close_issue(github_rest, org, repo_name, number)

    @pytest.mark.order(16)
    async def test_tc_incr_pr_001_update_only(
        self,
        github_connector: dict[str, Any],
        github_rest: Any,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-INCR-PR-001: editing a PR's title/body/comments updates the SAME record.

        Exercises the PR sweep, which has no ``since`` parameter — it pages
        updated-desc and stops on either a filtered-out PR *or* a short page. A
        regression that requires both conditions would loop the whole listing every
        sync; one that drops the filtered-out check would miss the update entirely.
        """
        state = github_connector
        org = state["org"]
        repo_name = state["mutation_repo_name"]
        repo_id = state["mutation_repo"]["id"]
        branch = state["mutation_repo"]["default_branch"]

        pr_number: int | None = None
        head_branch = f"it/{GH_IT_RUN_ID}-pr"
        async with dedicated_connector(
            pipeshub_client, graph_provider,
            token=state["token"], name=_connector_name("incr-pr"),
            filters=_mutation_filters(state),
        ) as connector_id:
            try:
                # A PR needs a branch with at least one commit ahead of the base.
                base_sha = await get_branch_head(github_rest, org, repo_name, branch)
                await github_rest.post_json(
                    f"/repos/{org}/{repo_name}/git/refs",
                    {"ref": f"refs/heads/{head_branch}", "sha": base_sha},
                )
                await commit_changes(
                    github_rest, org, repo_name, head_branch,
                    [FileChange.upsert(it_path("pr", "change.txt"), "pr fixture\n")],
                    message="TC-INCR-PR-001 fixture",
                )
                pr = await create_pull(
                    github_rest, org, repo_name,
                    title=artifact_title("IncrPr"), head=head_branch, base=branch,
                    body="Original description.",
                )
                pr_number = pr["number"]

                await _resync(pipeshub_client, graph_provider, connector_id)

                external_id = f"{repo_id}/pull/{pr_number}"
                before = await wait_for_record_by_external_id(
                    graph_provider, connector_id, external_id,
                    description="TC-INCR-PR-001 new PR",
                )
                before_id, before_version = before.id, int(before.version)

                # --- update only: no new PR is opened ---
                new_title = artifact_title("EditedPr")
                await update_pull(
                    github_rest, org, repo_name, pr_number,
                    title=new_title, body="Edited description.",
                )
                await add_comment(
                    github_rest, org, repo_name, pr_number,
                    "Conversation comment from TC-INCR-PR-001.",
                )
                pipeshub_client.wait(5)
                await _resync(pipeshub_client, graph_provider, connector_id)

                after = await graph_provider.get_record_by_external_id(connector_id, external_id)
                assert after is not None, "PR record disappeared after update"
                # The identity check is what proves no second record was created. A
                # count delta cannot say this: concurrent runs open their own PRs in
                # this same mutation repo, so the total moves for reasons unrelated to
                # the edit under test.
                assert after.id == before_id, (
                    "an updated PR must reuse its record, not create a new one "
                    f"({before_id} → {after.id})"
                )
                assert after.version == before_version + 1, (
                    f"expected version {before_version + 1}, got {after.version}"
                )
                assert new_title in (after.record_name or ""), (
                    f"edited title not reflected: {after.record_name!r}"
                )
                live = await get_pull(github_rest, org, repo_name, pr_number)
                assert str(after.external_revision_id) == str(epoch_ms(live["updated_at"])), (
                    "external_revision_id must track the source updated_at in epoch ms"
                )
                logger.info("TC-INCR-PR-001 passed: same record, version %s → %s",
                            before_version, after.version)
            finally:
                if pr_number:
                    await close_pull(github_rest, org, repo_name, pr_number)
                try:
                    await github_rest.request(
                        "DELETE", f"/repos/{org}/{repo_name}/git/refs/heads/{head_branch}",
                    )
                except Exception as e:
                    logger.warning("Could not delete branch %s: %s", head_branch, e)

    @pytest.mark.order(17)
    async def test_tc_incr_code_001_all_deltas(
        self,
        github_connector: dict[str, Any],
        github_rest: Any,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-INCR-CODE-001: new / update / rename / move / delete in one commit set.

        All five in one commit and one resync: a resync costs minutes, and the deltas
        are independent, so splitting them would quadruple the wall clock for no extra
        coverage. Every path is inside ``it/<run_id>/`` — concurrent runs share this
        branch and their files land in the same compare, which is harmless precisely
        because every assertion here is by external id.
        """
        state = github_connector
        org = state["org"]
        repo_name = state["mutation_repo_name"]
        repo = state["mutation_repo"]
        repo_id = repo["id"]
        branch = repo["default_branch"]

        # Paths for this run only.
        keep = it_path("code", "keep.txt")          # updated in place
        renamed_from = it_path("code", "before.txt")  # renamed within its directory
        renamed_to = it_path("code", "after.txt")
        moved_from = it_path("code", "moving.txt")    # moved to another directory
        moved_to = it_path("moved", "moving.txt")
        doomed = it_path("code", "doomed.txt")        # deleted
        fresh = it_path("added", "nested", "new.txt")  # new file in a new folder chain

        async with dedicated_connector(
            pipeshub_client, graph_provider,
            token=state["token"], name=_connector_name("incr-code"),
            filters=_mutation_filters(state), min_records=1,
        ) as connector_id:
            # Artifacts are reaped by the module teardown (reap_own_artifacts), which
            # clears this run's whole namespace whether or not the test succeeded.

            # --- baseline commit + sync ---
            await commit_changes(
                github_rest, org, repo_name, branch,
                [
                    FileChange.upsert(keep, "v1\n"),
                    FileChange.upsert(renamed_from, "rename me\n"),
                    FileChange.upsert(moved_from, "move me\n"),
                    FileChange.upsert(doomed, "delete me\n"),
                ],
                message=f"TC-INCR-CODE-001 baseline ({GH_IT_RUN_ID})",
            )
            await _resync(pipeshub_client, graph_provider, connector_id)

            def blob_id(path: str) -> str:
                return f"/{repo_id}/blob/{path}"

            baseline = {}
            for path in (keep, renamed_from, moved_from, doomed):
                baseline[path] = await wait_for_record_by_external_id(
                    graph_provider, connector_id, blob_id(path),
                    description=f"TC-INCR-CODE-001 baseline {path}",
                )

            # --- one commit carrying all five deltas ---
            rename_content = "rename me\n"   # identical → same blob sha → pure rename
            move_content = "move me\n"
            await commit_changes(
                github_rest, org, repo_name, branch,
                [
                    FileChange.upsert(keep, "v2 modified\n"),
                    FileChange.delete(renamed_from),
                    FileChange.upsert(renamed_to, rename_content),
                    FileChange.delete(moved_from),
                    FileChange.upsert(moved_to, move_content),
                    FileChange.delete(doomed),
                    FileChange.upsert(fresh, "brand new\n"),
                ],
                message=f"TC-INCR-CODE-001 deltas ({GH_IT_RUN_ID})",
            )
            await _resync(pipeshub_client, graph_provider, connector_id)

            # (a) NEW — record plus its folder chain.
            await wait_for_record_by_external_id(
                graph_provider, connector_id, blob_id(fresh),
                description=f"TC-INCR-CODE-001 new file {fresh}",
            )
            for directory in (it_path("added"), it_path("added", "nested")):
                assert await graph_provider.get_record_by_external_id(
                    connector_id, f"/{repo_id}/tree/{directory}",
                ), f"folder record missing for new directory {directory}"

            # (b) UPDATE — same record, new blob sha, version bumped.
            updated = await graph_provider.get_typed_record_by_external_id(
                connector_id, blob_id(keep),
            )
            assert updated is not None
            live_sha = await blob_sha_for_path(github_rest, org, repo_name, keep, branch)
            assert str(updated.file_hash) == str(live_sha), (
                "a modified file must carry the new blob sha"
            )
            assert updated.version == int(baseline[keep].version) + 1, (
                f"modified file version {updated.version} != "
                f"{int(baseline[keep].version) + 1}"
            )

            # (c) RENAME — the DB vertex is reused, so the id survives the new id.
            renamed = await wait_for_record_by_external_id(
                graph_provider, connector_id, blob_id(renamed_to),
                description=f"TC-INCR-CODE-001 renamed file {renamed_to}",
            )
            assert renamed.id == baseline[renamed_from].id, (
                "on_records_moved must reuse the existing vertex so permission and "
                f"parent edges survive ({baseline[renamed_from].id} → {renamed.id})"
            )
            assert renamed.version == int(baseline[renamed_from].version), (
                "a pure rename carries the same blob sha, so content_changed is "
                f"False and the version must NOT bump (got {renamed.version})"
            )
            assert await graph_provider.get_record_by_external_id(
                connector_id, blob_id(renamed_from),
            ) is None, "the old path must no longer resolve after a rename"

            # (d) MOVE — new parent folder edge; the emptied source folder is swept.
            moved = await wait_for_record_by_external_id(
                graph_provider, connector_id, blob_id(moved_to),
                description=f"TC-INCR-CODE-001 moved file {moved_to}",
            )
            assert moved.id == baseline[moved_from].id, "a move must reuse the vertex"
            new_parent = await graph_provider.get_record_parent_external_id(
                connector_id, blob_id(moved_to),
            )
            assert new_parent == f"/{repo_id}/tree/{it_path('moved')}", (
                f"moved file's parent is {new_parent!r}, expected the new directory"
            )

            # (e) DELETE — record gone. The incremental delete path has no valve.
            assert await graph_provider.get_record_by_external_id(
                connector_id, blob_id(doomed),
            ) is None, f"deleted file {doomed} still has a record"

            # (f) Timestamps written by the first sync survived this resync.
            #     Neo4j `SET n += null` deletes a property, so upserting a record
            #     built with None dates silently wiped whatever the backfill filled.
            await wait_until_graph_condition(
                connector_id,
                check=lambda: _has_dates(graph_provider, connector_id, blob_id(keep)),
                timeout=GH_TIMESTAMP_BACKFILL_WAIT_SEC,
                description="source timestamps survived the incremental resync",
            )
            logger.info("TC-INCR-CODE-001 passed: new/update/rename/move/delete verified")


async def _has_dates(
    graph_provider: GraphProviderProtocol, connector_id: str, external_id: str
) -> bool:
    record = await graph_provider.get_record_by_external_id(connector_id, external_id)
    return bool(
        record
        and getattr(record, "source_created_at", None)
        and getattr(record, "source_updated_at", None)
    )

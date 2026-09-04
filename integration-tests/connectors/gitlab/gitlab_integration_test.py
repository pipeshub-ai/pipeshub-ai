# pyright: ignore-file

"""
GitLab Connector – Integration Tests (pre-provisioned, read-only + self-cleaning mutations)
===========================================================================================

Scope comes from ``GITLAB_TEST_GROUP`` + the two project env vars; fixture *shapes*
are discovered by the conftest rather than pinned, so a re-provisioned fixture needs
no code change. Only the two frozen blocks snapshots and the pinned merge request are
addressed by number.

Every CI leg, every PR and the nightly cron share ONE GitLab group, and different PRs
run at the same time. The primary project is therefore never written to: the four
mutation and filter cases (orders 19-23) each create a throw-away connector scoped to
the *mutation* project and assert by external id, so nothing another run does can
reach an assertion here. Code mutations are further confined to ``it/<run_id>/`` —
the connector only syncs the default branch, so concurrent runs share it and only a
path namespace keeps them apart.

  order 1  TC-SYNC-001            — full sync baseline + graph self-consistency
  order 2  TC-GL-RG-001           — the four-way project record-group shape
  order 3  TC-GL-RG-002           — namespace group nodes are flat, and each gets an App edge
  order 4  TC-GL-USER-001         — AppUsers keyed by GitLab numeric id + USER_APP edge
  order 5  TC-GL-USER-002         — pseudo-groups stand in for members with no public_email
  order 6  TC-GL-ISSUE-001        — TICKET properties, and the people fields that are absent
  order 7  TC-GL-ISSUE-002        — issue_type mapping and the raw GitLab state
  order 8  TC-GL-ISSUE-BLOCKS-001 — streamed issue blocks snapshot
  order 9  TC-GL-ATTACH-001       — non-image attachment FileRecord
  order 10 TC-GL-MR-001           — merged MR PULL_REQUEST properties
  order 11 TC-GL-MR-BLOCKS-001    — streamed MR blocks snapshot
  order 12 TC-GL-CODE-001         — CodeFileRecord properties incl. extension-less blob
  order 13 TC-GL-CODE-002         — folder records, PARENT_CHILD chain, dotfile exclusion
  order 14 TC-GL-CODE-TS-001      — blob source timestamps arrive from the backfill
  order 15 TC-GL-PERM-001         — the four-way ACL split by access level
  order 16 TC-GL-PERM-002         — permission type is always OWNER; 2-hop resolution
  order 17 TC-GL-CKPT-001         — sync points for all three per-project data groups
  order 18 TC-GL-IDX-001          — indexing reaches a terminal state
  order 19 TC-INCR-ISSUE-001      — issue create then update: version += 1, new revision
  order 20 TC-INCR-MR-001         — MR update-only: no new record, version += 1
  order 21 TC-INCR-CODE-001       — new/update/rename/move/delete in one commit set
  order 22 TC-FILTER-001          — group_ids scoping expands subgroups and parents the project
  order 23 TC-FILTER-002          — Index Code Files off: records exist, AUTO_INDEX_OFF
"""

import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Optional

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.config.constants.arangodb import (  # type: ignore[import-not-found]  # noqa: E402
    CollectionNames,
    MimeTypes,
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

from connectors.gitlab.constants import (  # noqa: E402
    ENV_BLOCKS_BOOTSTRAP,
    GL_INCR_MR_IID,
    GL_INDEXING_WAIT_SEC,
    GL_IT_RUN_ID,
    GL_STREAM_WAIT_SEC,
    GL_SYNC_WAIT_SEC,
    GL_TIMESTAMP_WAIT_SEC,
    PINNED_MR_COMMENT_MARKER,
    PINNED_MR_FILE,
    PSEUDO_USER_GROUP_PREFIX,
    artifact_title,
    it_path,
)
from connectors.gitlab.gitlab_block_utils import (  # noqa: E402
    ISSUE_BLOCKS_PATH,
    MR_BLOCKS_PATH,
    assert_snapshot_source_unchanged,
    bootstrap_expected,
    load_expected,
    normalize_blocks_container,
    parse_connector_blocks_via_processor,
)
from connectors.gitlab.gitlab_expected import (  # noqa: E402
    CODE_TIMESTAMP_FIELDS,
    PIPELINE_ASSIGNED_FIELDS,
    PROCESSOR_ASSIGNED_FIELDS,
    GitLabExpected,
    folder_id_of,
)
from connectors.gitlab.gitlab_test_utils import (  # noqa: E402
    GitLabRestClient,
    add_note,
    blob_sha_for_path,
    bool_filter,
    branch_head_sha,
    child_groups_for_level,
    commit_actions,
    create_issue,
    dedicated_connector,
    dedupe_members,
    delete_issue,
    delete_note,
    find_code_record,
    get_issue,
    get_merge_request,
    indexing_filters,
    list_filter,
    list_notes,
    sync_filters,
    update_issue,
    update_merge_request,
)

logger = logging.getLogger("gitlab-it")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.gitlab,
    pytest.mark.asyncio(loop_scope="session"),
]

# ``created_at``/``updated_at`` are stamped by the processor at write time with
# wall-clock values a test cannot know. Every comparison skips them.
_SKIP = PROCESSOR_ASSIGNED_FIELDS

# ``RecordGroup.from_arango_base_record_group`` does not hydrate
# ``inherit_permissions``, so a group read back from the graph always reports the model
# default regardless of what was written. Comparing it would assert nothing; the tests
# assert the real INHERIT_PERMISSIONS edge instead.
_GROUP_SKIP = _SKIP | frozenset({"inherit_permissions"})

_RECORD_SKIP = _SKIP | PIPELINE_ASSIGNED_FIELDS

# Blob source timestamps are filled by a backfill that outlives the sync, so comparing
# them here races it. TC-GL-CODE-TS-001 polls for them instead.
_CODE_SKIP = _RECORD_SKIP | CODE_TIMESTAMP_FIELDS

# GitLab grants every principal the same PermissionType. Asserting the constant is the
# point of TC-GL-PERM-002 — the access level decides *which groups* a member reaches,
# never *what they may do* once there.
_GITLAB_PERMISSION_ROLE = "OWNER"


# =============================================================================
# Local helpers
# =============================================================================


def _connector_name(kind: str) -> str:
    return f"gitlab-{kind}-{GL_IT_RUN_ID}-{uuid.uuid4().hex[:6]}"


def _restart_sync(pipeshub_client: PipeshubClient, connector_id: str) -> None:
    """Toggle off/on to trigger an incremental sync.

    The GitLab connector has no separate incremental entry point — the delta is
    entirely checkpoint-driven — so re-entering the sync is all that is needed.
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
        pipeshub_client, graph_provider, connector_id, timeout=GL_SYNC_WAIT_SEC,
    )


async def _group_edge_count(
    graph_provider: GraphProviderProtocol, *,
    from_group: Any, to_group: Any, edge_collection: str,
) -> int:
    edges = await graph_provider.find_edges_between(
        CollectionNames.RECORD_GROUPS.value, from_group.id,
        CollectionNames.RECORD_GROUPS.value, to_group.id,
        edge_collection,
    )
    return len(edges or [])


async def _code_records(
    graph_provider: GraphProviderProtocol, connector_id: str,
) -> list[dict[str, Any]]:
    return await graph_provider.fetch_records_by_type(
        connector_id, RecordType.CODE_FILE.value,
    )


def _record_key(row: dict[str, Any]) -> str:
    """Stable identity of a raw graph record row across both backends.

    The Arango provider returns ``_key``; the Neo4j one returns the node verbatim,
    where the same value is ``id``.
    """
    return str(row.get("_key") or row.get("id") or "")


async def _folder_records(
    graph_provider: GraphProviderProtocol, connector_id: str,
) -> list[dict[str, Any]]:
    """FILE records that are repository folders.

    Attachments are FILE records too, so the ``/-/tree/`` marker in the external id is
    what separates a directory node from an uploaded file.
    """
    rows = await graph_provider.fetch_records_by_type(
        connector_id, RecordType.FILE.value,
    )
    return [r for r in rows if "/-/tree/" in str(r.get("externalRecordId") or "")]


def _mutation_filters(state: dict[str, Any]) -> dict[str, Any]:
    return sync_filters(project_ids=list_filter("in", [state["mutation_path"]]))


async def _await_indexing_terminal(
    graph_provider: GraphProviderProtocol, connector_id: str,
    external_id: str, *, label: str,
) -> str:
    """Poll until a record leaves the non-terminal indexing states, then return it."""
    terminal = {
        ProgressStatus.COMPLETED.value,
        ProgressStatus.FAILED.value,
        ProgressStatus.AUTO_INDEX_OFF.value,
    }
    seen: dict[str, Optional[str]] = {"status": None}

    async def _check() -> bool:
        record = await graph_provider.get_record_by_external_id(connector_id, external_id)
        seen["status"] = getattr(record, "indexing_status", None) if record else None
        return seen["status"] in terminal

    await wait_until_graph_condition(
        connector_id, check=_check, timeout=GL_INDEXING_WAIT_SEC,
        description=f"indexing to settle on {label}",
    )
    return str(seen["status"])


# =============================================================================
# Sync baseline and structure
# =============================================================================


class TestGitLabSyncAndStructure:

    @pytest.mark.order(1)
    async def test_tc_sync_001_full_sync_graph_validation(
        self, gitlab_connector: dict[str, Any], graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-SYNC-001: validate the graph after the fixture's full sync.

        Counts are asserted as *structural invariants* — which hold whatever the
        fixture contains — plus presence, by external id, of every issue, merge
        request and syncable blob the primary project should have produced. An exact
        global total would break the first time someone adds a file to the fixture
        without catching any real defect.
        """
        connector_id = gitlab_connector["connector_id"]
        primary = gitlab_connector["primary"]

        total = await graph_provider.count_records(connector_id, scoped=True)
        by_type = {
            record_type: await graph_provider.count_records_by_type(
                connector_id, record_type, scoped=True,
            )
            for record_type in (
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

        # Every record belongs to exactly one record group and inherits permissions.
        # The ACL lives on the four record groups; a record carrying its own
        # PERMISSION edge would mean the inheritance chain was bypassed.
        rg_edges = await graph_provider.count_record_group_edges(connector_id)
        assert rg_edges == total, (
            f"every record needs one BELONGS_TO→RecordGroup ({rg_edges} != {total})"
        )
        inherit = await graph_provider.count_inherit_permissions_edges(connector_id)
        assert inherit == total, (
            f"every record needs one INHERIT_PERMISSIONS edge ({inherit} != {total})"
        )

        for issue in gitlab_connector["primary_issues"]:
            external_id = str(issue["id"])
            assert await graph_provider.get_record_by_external_id(connector_id, external_id), (
                f"issue #{issue['iid']} missing from the graph (external id {external_id})"
            )
        for mr in gitlab_connector["primary_mrs"]:
            external_id = str(mr["id"])
            assert await graph_provider.get_record_by_external_id(connector_id, external_id), (
                f"MR !{mr['iid']} missing from the graph (external id {external_id})"
            )

        code = await _code_records(graph_provider, connector_id)
        missing = [
            path for path in gitlab_connector["primary_blob_paths"]
            if find_code_record(code, path) is None
        ]
        assert not missing, f"blobs synced from {primary['path_with_namespace']} missing: {missing}"

        graph_app = await graph_provider.get_app_metadata_by_connector_id(connector_id)
        assert graph_app is not None, f"apps document missing for connector {connector_id}"
        assert_graph_entity_matches(
            GitLabExpected.app_metadata(gitlab_connector), graph_app,
            entity="app_metadata",
            skip_compare=frozenset({
                "created_at_timestamp", "updated_at_timestamp", "auth_type", "is_active",
                "is_agent_active", "is_configured", "is_authenticated", "created_by",
                "updated_by", "status", "is_locked", "last_synced_by",
                "vector_membership_backfill_after_key",
            }),
        )
        logger.info("TC-SYNC-001 passed: %d records %s", total, by_type)

    @pytest.mark.order(2)
    async def test_tc_gl_rg_001_project_record_groups(
        self, gitlab_connector: dict[str, Any], graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-GL-RG-001: the project group and its three children.

        The shape that makes GitLab different from every other repository connector:
        work items, merge requests and the code repository are separate ACL holders,
        not passive children of the project. Each child carries its own directly-gated
        grants and deliberately does **not** inherit from the project group — an
        inherit edge there would collapse the four-way split, handing the code
        repository to every project member including Guests.

        The corollary is asserted too, because it is the surprising half: the project
        group's own grants reach no records at all. Nothing inherits from it, so a
        member whose access level earns only the project-level grant (0 or Minimal)
        resolves to nothing.
        """
        connector_id = gitlab_connector["connector_id"]
        primary = gitlab_connector["primary"]

        project_group = await graph_provider.get_record_group_by_external_id(
            connector_id, str(primary["id"]),
        )
        assert project_group is not None, "project record group missing"
        assert_graph_entity_matches(
            GitLabExpected.project_record_group(
                primary, connector_id=connector_id,
                parent_full_path=gitlab_connector["primary_namespace_path"],
            ),
            project_group, entity="record_group", skip_compare=_GROUP_SKIP,
        )

        for kind in ("work-items", "merge-requests", "code-repository"):
            child = await graph_provider.get_record_group_by_external_id(
                connector_id, f"{primary['id']}-{kind}",
            )
            assert child is not None, f"child record group {kind} missing"
            assert_graph_entity_matches(
                GitLabExpected.child_record_group(
                    primary, kind=kind, connector_id=connector_id,
                ),
                child, entity="record_group", skip_compare=_GROUP_SKIP,
            )
            assert await _group_edge_count(
                graph_provider, from_group=child, to_group=project_group,
                edge_collection=CollectionNames.BELONGS_TO.value,
            ) == 1, f"child group {kind} must belong to the project group"
            assert await _group_edge_count(
                graph_provider, from_group=child, to_group=project_group,
                edge_collection=CollectionNames.INHERIT_PERMISSIONS.value,
            ) == 0, (
                f"child group {kind} inherits from the project group. Every project "
                "member holds a project-level grant regardless of access level, so "
                "inheriting it would hand the code repository and merge requests to "
                "Guests — the whole point of the four separate ACLs."
            )
        logger.info("TC-GL-RG-001 passed: project group + 3 non-inheriting children")

    @pytest.mark.order(3)
    async def test_tc_gl_rg_002_namespace_groups_are_flat(
        self, gitlab_connector: dict[str, Any], graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-GL-RG-002: namespace group nodes, and where the App edge lands.

        The App-edge count is the load-bearing assertion. The processor links a
        RecordGroup to the App only when the group has no parent, and GitLab writes
        namespace groups *flat* — a subgroup node is never parented to its parent
        group — so the count equals the number of namespace nodes, not one. A
        regression that moves the App edge onto the project group makes connector
        stats read zero for a whole sync while every record is in fact stored.
        """
        connector_id = gitlab_connector["connector_id"]
        namespace = gitlab_connector["primary_namespace"]
        if not namespace:
            pytest.skip(
                "The primary project sits in a user namespace, so no group record "
                "group is created. Move it under a group to exercise this."
            )

        group_node = await graph_provider.get_record_group_by_external_id(
            connector_id, namespace["full_path"],
        )
        assert group_node is not None, (
            f"namespace record group {namespace['full_path']!r} missing — the project "
            "group's parent points at a node that does not exist"
        )
        assert_graph_entity_matches(
            GitLabExpected.group_record_group(namespace, connector_id=connector_id),
            group_node, entity="record_group", skip_compare=_GROUP_SKIP,
        )
        assert group_node.parent_external_group_id is None, (
            "namespace groups are written flat: a subgroup node carries no parent, "
            f"but {namespace['full_path']!r} reports "
            f"{group_node.parent_external_group_id!r}"
        )

        app_edges = await graph_provider.count_app_record_group_edges(connector_id)
        assert app_edges == 1, (
            f"expected exactly 1 RecordGroup→App edge for a single-namespace sync, got "
            f"{app_edges}. Only a parentless group gets one, and a bare project_ids "
            "filter materialises exactly one namespace node — the project's own. A "
            "different number means the App edge moved, and connector stats read 0."
        )
        logger.info(
            "TC-GL-RG-002 passed: %s is parentless, %d App edge(s)",
            namespace["full_path"], app_edges,
        )


# =============================================================================
# Identity
# =============================================================================


class TestGitLabIdentity:

    @pytest.mark.order(4)
    async def test_tc_gl_user_001_app_users(
        self, gitlab_connector: dict[str, Any], graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-GL-USER-001: AppUsers keyed by GitLab's numeric id, not username.

        The numeric id is what survives a username change, and it is also the key the
        pseudo-group migration looks up — so keying on the login would silently strand
        every pseudo-group permission.
        """
        connector_id = gitlab_connector["connector_id"]
        emails = gitlab_connector["app_user_emails"]
        if not emails:
            pytest.skip(
                "No GitLab member resolved to a PipesHub identity. GitLab only exposes "
                "an address when the member sets public_email, and it must match a "
                "PipesHub user."
            )

        members_by_id = dedupe_members(gitlab_connector["primary_members"])
        assert set(emails) <= {str(mid) for mid in members_by_id}, (
            "an AppUser was created for a GitLab id that is not a project member"
        )
        for source_id, email in sorted(emails.items()):
            user = await graph_provider.get_user_by_source_id(
                source_user_id=source_id, connector_id=connector_id,
            )
            assert user is not None, f"AppUser missing for GitLab id {source_id}"
            assert user.email == email, (
                f"AppUser {source_id} is bound to {user.email!r}, but the connector "
                f"resolved it to {email!r}"
            )
            # The lookup above IS the key assertion: get_user_by_source_id queries on
            # source_user_id, so a hit proves the AppUser is keyed by GitLab's numeric
            # id. Reading the field back would assert nothing — the User model does not
            # hydrate it. Keying on the username instead would strand every permission
            # the pseudo-group migration later tries to move.
            await assert_user_app_edge(
                source_id, connector_id=connector_id, graph_provider=graph_provider,
            )
        logger.info("TC-GL-USER-001 passed: %d AppUser(s) verified", len(emails))

    @pytest.mark.order(5)
    async def test_tc_gl_user_002_pseudo_groups(
        self, gitlab_connector: dict[str, Any], graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-GL-USER-002: a member with no resolvable email becomes a pseudo-group.

        GitLab hides addresses unless a member sets ``public_email``, so a project can
        easily have members PipesHub cannot identify. Dropping their grants would
        silently narrow the ACL; instead the connector parks each one on a group keyed
        by the GitLab id, and migrates it the first sync after the identity appears.
        """
        connector_id = gitlab_connector["connector_id"]
        members_by_id = dedupe_members(gitlab_connector["primary_members"])
        emails = gitlab_connector["app_user_emails"]
        unresolved = [str(mid) for mid in members_by_id if str(mid) not in emails]
        if not unresolved:
            pytest.skip(
                "Every fixture member resolved to a PipesHub identity, so no "
                "pseudo-group is created. Add a member without public_email to cover it."
            )

        for source_id in unresolved:
            group = await graph_provider.get_user_group_by_external_id(
                connector_id, source_id,
            )
            assert group is not None, (
                f"member {source_id} resolved to no AppUser and no pseudo-group — their "
                "grant was dropped, silently narrowing the ACL"
            )
            assert group.name == f"{PSEUDO_USER_GROUP_PREFIX}_{source_id}", (
                f"pseudo-group name is {group.name!r}; the migration on the next sync "
                "looks the group up by external id, so the name is diagnostic only, but "
                "an unexpected shape means a different code path created it"
            )
        logger.info(
            "TC-GL-USER-002 passed: %d pseudo-group(s) hold unresolved members",
            len(unresolved),
        )


# =============================================================================
# Work items
# =============================================================================


class TestGitLabIssues:

    @pytest.mark.order(6)
    async def test_tc_gl_issue_001_ticket_properties(
        self, gitlab_connector: dict[str, Any], graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-GL-ISSUE-001: TicketRecord fields, and the people fields that are absent.

        The absence is asserted deliberately. Every other ticket-bearing connector
        writes CREATED_BY / ASSIGNED_TO entity relations; this one sets no people
        fields on a ticket at all, so a search for "issues assigned to me" returns
        nothing. Pinning the gap means the day someone closes it, this test tells them
        the contract changed rather than quietly passing.
        """
        connector_id = gitlab_connector["connector_id"]
        issue = gitlab_connector["reference_issue"]
        assert issue, "conftest guarantees at least one issue"

        external_id = str(issue["id"])
        actual = await graph_provider.get_typed_record_by_external_id(
            connector_id, external_id,
        )
        assert actual is not None, f"typed TICKET record missing for {external_id}"
        await assert_graph_entity_with_edges(
            GitLabExpected.ticket_record(issue, connector_id=connector_id),
            actual, entity="ticket_record",
            connector_id=connector_id, graph_provider=graph_provider,
            skip_compare=_RECORD_SKIP,
        )

        for edge_type in ("CREATED_BY", "ASSIGNED_TO", "REPORTED_BY"):
            relations = await graph_provider.get_record_outgoing_entity_relations(
                connector_id, external_id, edge_type,
            )
            assert not relations, (
                f"issue #{issue['iid']} has a {edge_type} edge ({relations!r}). The "
                "connector sets no assignee/creator/reporter on a TicketRecord, so an "
                "edge here means the mapping changed — update the expected builder and "
                "this assertion together."
            )
        logger.info("TC-GL-ISSUE-001 passed: issue #%s validated", issue["iid"])

    @pytest.mark.order(7)
    async def test_tc_gl_issue_002_type_and_state_mapping(
        self, gitlab_connector: dict[str, Any], graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-GL-ISSUE-002: ``issue_type`` mapping and the raw GitLab state.

        Only ``incident`` and ``task`` are recognised; everything else — including
        GitLab's newer work-item types — collapses to ISSUE. ``status`` is the raw
        GitLab string, not a normalised Status, which is unique to this connector and
        means a cross-connector status filter cannot match a GitLab ticket.
        """
        connector_id = gitlab_connector["connector_id"]
        checked = 0

        for key, expected_type in (
            ("incident_issue", "INCIDENT"),
            ("task_issue", "TASK"),
            ("reference_issue", "ISSUE"),
        ):
            issue = gitlab_connector.get(key)
            if not issue:
                logger.info("TC-GL-ISSUE-002: no %s in the fixture — skipped", key)
                continue
            record = await graph_provider.get_typed_record_by_external_id(
                connector_id, str(issue["id"]),
            )
            assert record is not None, f"record missing for {key} #{issue['iid']}"
            actual_type = getattr(record.type, "value", record.type)
            assert actual_type == expected_type, (
                f"issue #{issue['iid']} has GitLab issue_type "
                f"{issue.get('issue_type')!r} and should map to {expected_type}, got "
                f"{actual_type!r}"
            )
            assert record.status == issue["state"], (
                f"status must be GitLab's raw state {issue['state']!r}, got "
                f"{record.status!r} — this connector applies no Status normalisation"
            )
            checked += 1

        assert checked, "no typed issue fixture found — re-run the fixture seed"
        logger.info("TC-GL-ISSUE-002 passed: %d issue type(s) verified", checked)

    @pytest.mark.order(8)
    async def test_tc_gl_issue_blocks_001_streamed_blocks(
        self, gitlab_connector: dict[str, Any], graph_provider: GraphProviderProtocol,
        pipeshub_client: PipeshubClient,
    ) -> None:
        """TC-GL-ISSUE-BLOCKS-001: streamed issue blocks vs the frozen snapshot.

        Comments must appear in the output — they are a separate API call the block
        builder makes per issue, and a regression that drops them costs half the
        searchable content of every ticket without changing a single record count.
        """
        _require_stream_access(gitlab_connector)
        connector_id = gitlab_connector["connector_id"]
        issue = gitlab_connector["blocks_issue"]
        external_id = str(issue["id"])

        record = await graph_provider.get_record_by_external_id(connector_id, external_id)
        assert record is not None, (
            f"frozen blocks issue #{issue['iid']} not synced (external id {external_id})"
        )

        payload = _stream_blocks(
            pipeshub_client, record.id, label=f"issue #{issue['iid']}",
        )
        parsed = await parse_connector_blocks_via_processor(payload)
        actual = normalize_blocks_container(parsed)
        meta = {"iid": issue["iid"], "source_updated_ms": _epoch_ms(issue["updated_at"])}
        if os.getenv(ENV_BLOCKS_BOOTSTRAP) == "1":
            bootstrap_expected(ISSUE_BLOCKS_PATH, actual, meta=meta)
        expected, recorded_meta = load_expected(ISSUE_BLOCKS_PATH)
        assert_snapshot_source_unchanged(
            recorded_meta, label=f"issue #{issue['iid']}",
            live_updated_ms=meta["source_updated_ms"],
        )
        assert actual == expected, (
            "Parsed issue blocks do not match the expected snapshot. If the fixture "
            f"issue was edited, regenerate with {ENV_BLOCKS_BOOTSTRAP}=1 and review."
        )

        comment_bodies = [
            note["body"] for note in
            await _notes_for(gitlab_connector, "issues", issue["iid"])
        ]
        serialised = str(expected)
        for body in comment_bodies:
            fragment = body.strip().split("\n")[0][:40]
            assert fragment and fragment in serialised, (
                f"comment {fragment!r} is missing from the issue blocks — the per-issue "
                "comment fetch is not reaching the block builder"
            )
        logger.info(
            "TC-GL-ISSUE-BLOCKS-001 passed: issue #%s blocks + %d comment(s) validated",
            issue["iid"], len(comment_bodies),
        )

    @pytest.mark.order(9)
    async def test_tc_gl_attach_001_attachment_file_record(
        self, gitlab_connector: dict[str, Any], graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-GL-ATTACH-001: a non-image upload becomes its own FileRecord.

        Non-image on purpose: an image is inlined into the parent's blocks as a base64
        data URI and produces no record at all, so an image fixture would make this
        test pass for the wrong reason.

        The external id is the **API** upload URL, not the browser one the markdown
        carries — the record has to be fetchable by the streaming path, and only the
        API form is.
        """
        connector_id = gitlab_connector["connector_id"]
        issue = gitlab_connector["attachment_issue"]
        href = gitlab_connector["attachment_href"]
        if not (issue and href):
            pytest.skip(
                "No non-image upload found on any fixture issue — re-run the fixture "
                "seed, which uploads a .csv into the blocks issue body."
            )

        project_id = gitlab_connector["primary"]["id"]
        instance = gitlab_connector["instance_url"].rstrip("/")
        external_id = f"{instance}/api/v4/projects/{project_id}{href}"

        record = await graph_provider.get_typed_record_by_external_id(
            connector_id, external_id,
        )
        assert record is not None, (
            f"attachment FileRecord missing for {external_id}. The connector builds the "
            "id from the API base plus the /uploads/ href; a mismatch here means the "
            "record exists under an id nothing can fetch."
        )
        assert record.is_file is True
        assert record.weburl == external_id
        assert record.parent_external_record_id == str(issue["id"]), (
            "an attachment hangs off its parent issue; without the parent link it is an "
            "orphan file with the issue's ACL and no context"
        )
        assert record.extension == href.rsplit(".", 1)[-1].lower()
        assert record.mime_type != MimeTypes.FOLDER.value

        parent_relations = await graph_provider.get_record_parent_external_id(
            connector_id, external_id,
        )
        assert parent_relations == str(issue["id"]), (
            f"attachment PARENT_CHILD points at {parent_relations!r}, expected the "
            f"issue {issue['id']}"
        )
        logger.info("TC-GL-ATTACH-001 passed: %s", external_id)


# =============================================================================
# Merge requests
# =============================================================================


class TestGitLabMergeRequests:

    @pytest.mark.order(10)
    async def test_tc_gl_mr_001_pull_request_properties(
        self, gitlab_connector: dict[str, Any], graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-GL-MR-001: PullRequestRecord fields on a merged MR.

        ``status`` and ``mergeable`` are both raw GitLab strings — ``merged`` and
        ``can_be_merged`` — rather than normalised values, so anything downstream that
        matches on a canonical status will not match a GitLab merge request.
        """
        connector_id = gitlab_connector["connector_id"]
        mr = gitlab_connector["merged_mr"]
        if not mr:
            pytest.skip("No merged MR in the primary project — re-run the fixture seed")

        # The listing payload omits merged_by and reviewers; the expected record is
        # built from the full object, so fetch it.
        full = await get_merge_request(
            gitlab_connector["_rest"], gitlab_connector["primary_path"], mr["iid"],
        )
        external_id = str(full["id"])
        actual = await graph_provider.get_typed_record_by_external_id(
            connector_id, external_id,
        )
        assert actual is not None, f"typed PULL_REQUEST record missing for {external_id}"
        await assert_graph_entity_with_edges(
            GitLabExpected.merge_request_record(full, connector_id=connector_id),
            actual, entity="pull_request_record",
            connector_id=connector_id, graph_provider=graph_provider,
            skip_compare=_RECORD_SKIP,
        )
        assert actual.status == "merged", (
            f"a merged MR keeps GitLab's raw state, got {actual.status!r}"
        )
        logger.info("TC-GL-MR-001 passed: MR !%s validated", full["iid"])

    @pytest.mark.order(11)
    async def test_tc_gl_mr_blocks_001_streamed_blocks(
        self, gitlab_connector: dict[str, Any], graph_provider: GraphProviderProtocol,
        pipeshub_client: PipeshubClient,
    ) -> None:
        """TC-GL-MR-BLOCKS-001: streamed MR blocks vs the frozen snapshot.

        Covers description, commit list, per-file diffs and conversation comments —
        the whole MR block builder in one compare.
        """
        _require_stream_access(gitlab_connector)
        connector_id = gitlab_connector["connector_id"]
        mr = gitlab_connector["blocks_mr"]
        external_id = str(mr["id"])

        record = await graph_provider.get_record_by_external_id(connector_id, external_id)
        assert record is not None, (
            f"frozen blocks MR !{mr['iid']} not synced (external id {external_id})"
        )

        payload = _stream_blocks(
            pipeshub_client, record.id, label=f"MR !{mr['iid']}",
        )
        parsed = await parse_connector_blocks_via_processor(payload)
        actual = normalize_blocks_container(parsed)
        meta = {"iid": mr["iid"], "source_updated_ms": _epoch_ms(mr["updated_at"])}
        if os.getenv(ENV_BLOCKS_BOOTSTRAP) == "1":
            bootstrap_expected(MR_BLOCKS_PATH, actual, meta=meta)
        expected, recorded_meta = load_expected(MR_BLOCKS_PATH)
        assert_snapshot_source_unchanged(
            recorded_meta, label=f"MR !{mr['iid']}",
            live_updated_ms=meta["source_updated_ms"],
        )
        assert actual == expected, (
            "Parsed MR blocks do not match the expected snapshot. If the fixture MR was "
            f"edited, regenerate with {ENV_BLOCKS_BOOTSTRAP}=1 and review."
        )
        logger.info("TC-GL-MR-BLOCKS-001 passed: MR !%s blocks validated", mr["iid"])


# =============================================================================
# Code repository
# =============================================================================


class TestGitLabCodeFiles:

    @pytest.mark.order(12)
    async def test_tc_gl_code_001_code_file_properties(
        self, gitlab_connector: dict[str, Any], graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-GL-CODE-001: CodeFileRecord fields, including the extension-less case.

        ``language`` and ``file_role`` are populated here — GitHub leaves both null —
        so a regression that drops them is invisible in record counts and only shows up
        as code search quietly losing its language filter.
        """
        connector_id = gitlab_connector["connector_id"]
        primary = gitlab_connector["primary"]
        tree = gitlab_connector["primary_tree"]
        path = gitlab_connector["nested_code_path"]
        if not path:
            pytest.skip("No nested code path in the primary project — re-run the seed")

        code = await _code_records(graph_provider, connector_id)
        row = find_code_record(code, path)
        assert row is not None, f"no CODE_FILE record for {path}"
        external_id = str(row["externalRecordId"])

        blob_sha = next(
            (e["id"] for e in tree if e.get("path") == path and e.get("type") == "blob"),
            None,
        )
        assert blob_sha, f"no blob sha for {path} in the live tree"

        actual = await graph_provider.get_typed_record_by_external_id(
            connector_id, external_id,
        )
        assert actual is not None, f"typed CODE_FILE record missing for {external_id}"
        await assert_graph_entity_with_edges(
            GitLabExpected.code_file_record(
                project=primary, path=path, blob_sha=blob_sha,
                connector_id=connector_id, external_record_id=external_id,
                weburl=row.get("webUrl") or actual.weburl,
            ),
            actual, entity="code_file_record",
            connector_id=connector_id, graph_provider=graph_provider,
            skip_compare=_CODE_SKIP,
        )

        # The id is a GraphQL webPath. Its ref segment is GitLab's to choose, so the
        # shape is pinned rather than the whole string — but the shape matters: every
        # incremental code path rebuilds this id from the project path, and a change
        # here means updates land on a second, parallel record.
        assert external_id.startswith(f"/{primary['path_with_namespace']}/-/blob/"), (
            f"code-file external id {external_id!r} no longer has the "
            "/{project}/-/blob/{ref}/{path} shape the incremental paths reconstruct"
        )
        assert external_id.endswith(f"/{path}")

        ext_path = gitlab_connector["extensionless_code_path"]
        if ext_path:
            ext_row = find_code_record(code, ext_path)
            assert ext_row is not None, f"no record for extension-less blob {ext_path}"
            ext_record = await graph_provider.get_typed_record_by_external_id(
                connector_id, str(ext_row["externalRecordId"]),
            )
            assert ext_record.extension is None, (
                f"{ext_path} has no extension, so extension must be None, not "
                f"{ext_record.extension!r} — splitting on '.' would hand back the "
                "filename itself"
            )
            assert ext_record.mime_type == MimeTypes.PLAIN_TEXT.value
        logger.info("TC-GL-CODE-001 passed: %s validated", path)

    @pytest.mark.order(13)
    async def test_tc_gl_code_002_folders_and_hierarchy(
        self, gitlab_connector: dict[str, Any], graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-GL-CODE-002: folder records, the PARENT_CHILD chain, and dotfiles.

        A folder that reads back as a file is a real corruption mode, so ``is_file`` and
        the FOLDER mime type are asserted together. The dotfile check pins a filter that
        is easy to lose: the connector drops any blob whose name starts with ``.``, so a
        ``.gitignore`` in the fixture must have no record.
        """
        connector_id = gitlab_connector["connector_id"]
        primary = gitlab_connector["primary"]
        path = gitlab_connector["nested_code_path"]
        if not path or "/" not in path:
            pytest.skip("No nested code path in the primary project — re-run the seed")

        code = await _code_records(graph_provider, connector_id)
        row = find_code_record(code, path)
        assert row is not None, f"no CODE_FILE record for {path}"
        blob_external_id = str(row["externalRecordId"])

        # Walk the chain from the file up to the repository root.
        segments = path.split("/")[:-1]
        child_external_id = blob_external_id
        for depth in range(len(segments), 0, -1):
            folder_external_id = folder_id_of(child_external_id)
            folder = await graph_provider.get_typed_record_by_external_id(
                connector_id, folder_external_id,
            )
            assert folder is not None, (
                f"folder record missing at depth {depth} ({folder_external_id})"
            )
            assert folder.is_file is False, (
                f"{folder_external_id} must carry is_file=False; rebuilding a record "
                "from a bare graph node defaults it to True and flips the folder into "
                "a file"
            )
            assert folder.mime_type == MimeTypes.FOLDER.value
            assert folder.record_name == segments[depth - 1]

            parent_of_child = await graph_provider.get_record_parent_external_id(
                connector_id, child_external_id,
            )
            assert parent_of_child == folder_external_id, (
                f"PARENT_CHILD for {child_external_id} points at {parent_of_child!r}, "
                f"expected {folder_external_id!r}"
            )
            child_external_id = folder_external_id

        root_parent = await graph_provider.get_record_parent_external_id(
            connector_id, child_external_id,
        )
        assert root_parent is None, (
            f"the top-level folder {child_external_id} should have no parent, got "
            f"{root_parent!r}"
        )

        folders = await _folder_records(graph_provider, connector_id)
        assert len(folders) == len(gitlab_connector["primary_dir_paths"]), (
            f"{len(folders)} folder record(s) for "
            f"{len(gitlab_connector['primary_dir_paths'])} directory prefix(es) in the "
            f"live tree ({sorted(gitlab_connector['primary_dir_paths'])})"
        )
        # Read the group id from the typed record: the raw graph node does not carry
        # externalRecordGroupId on either backend, so the raw row would compare "".
        for folder_row in folders:
            typed = await graph_provider.get_typed_record_by_external_id(
                connector_id, str(folder_row["externalRecordId"]),
            )
            assert typed is not None
            assert typed.external_record_group_id == f"{primary['id']}-code-repository", (
                f"folder {folder_row['externalRecordId']} belongs to "
                f"{typed.external_record_group_id!r}, not the code-repository group"
            )

        dotfiles = [
            entry["path"] for entry in gitlab_connector["primary_tree"]
            if entry.get("type") == "blob"
            and entry["path"].rsplit("/", 1)[-1].startswith(".")
        ]
        for dotfile in dotfiles:
            assert find_code_record(code, dotfile) is None, (
                f"{dotfile} is a dotfile and must not be synced, but a record exists"
            )
        logger.info(
            "TC-GL-CODE-002 passed: %d folder(s), %d dotfile(s) correctly excluded",
            len(folders), len(dotfiles),
        )

    @pytest.mark.order(14)
    async def test_tc_gl_code_ts_001_source_timestamps(
        self, gitlab_connector: dict[str, Any], graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-GL-CODE-TS-001: blob source timestamps arrive from the commit backfill.

        The backfill is scheduled at the end of ``run_sync`` and outlives it, so this
        polls rather than snapshots — and never asserts their *absence*, which would be
        a race against a task the test cannot await.
        """
        connector_id = gitlab_connector["connector_id"]
        path = gitlab_connector["nested_code_path"]
        if not path:
            pytest.skip("No nested code path in the primary project — re-run the seed")

        code = await _code_records(graph_provider, connector_id)
        row = find_code_record(code, path)
        assert row is not None, f"no CODE_FILE record for {path}"
        external_id = str(row["externalRecordId"])

        async def _dated() -> bool:
            record = await graph_provider.get_record_by_external_id(connector_id, external_id)
            return bool(
                record
                and getattr(record, "source_created_at", None)
                and getattr(record, "source_updated_at", None)
            )

        await wait_until_graph_condition(
            connector_id, check=_dated, timeout=GL_TIMESTAMP_WAIT_SEC,
            description=f"commit-date backfill on {path}",
        )
        record = await graph_provider.get_record_by_external_id(connector_id, external_id)
        assert record.source_created_at <= record.source_updated_at, (
            "a blob cannot have been last modified before it was created "
            f"({record.source_created_at} > {record.source_updated_at})"
        )
        logger.info("TC-GL-CODE-TS-001 passed: %s carries commit dates", path)


# =============================================================================
# Permissions
# =============================================================================


class TestGitLabPermissions:

    @pytest.mark.order(15)
    async def test_tc_gl_perm_001_four_way_acl_split(
        self, gitlab_connector: dict[str, Any], graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-GL-PERM-001: the ACL split across the four record groups.

        This is the connector's whole authorization model. The project group is granted
        to *every* member unconditionally — the grant is appended before the access
        level is even inspected — while the three children are gated: Guest (10) reaches
        work items only, and level 15 and up reaches all three.

        The ``>= 15`` bound is asserted as-implemented, not as-intended: 15 is Planner,
        a GitLab role that cannot read repository code, and it is granted the code
        repository here. That is captured in the expected mapping rather than argued
        with, so the day the bound moves to 20 this test is what reports it.
        """
        connector_id = gitlab_connector["connector_id"]
        primary = gitlab_connector["primary"]
        members_by_id = dedupe_members(gitlab_connector["primary_members"])

        project_perms = await graph_provider.count_permission_edges_to_record_groups(
            connector_id, str(primary["id"]),
        )
        assert project_perms == len(members_by_id), (
            f"the project group has {project_perms} PERMISSION edge(s) for "
            f"{len(members_by_id)} member(s). Every member gets one — resolvable ones "
            "as a USER grant, the rest parked on a pseudo-group — so a shortfall means "
            "grants were dropped rather than parked."
        )

        expected_child_counts = {"work-items": 0, "merge-requests": 0, "code-repository": 0}
        for member in members_by_id.values():
            for kind in child_groups_for_level(member.get("access_level") or 0):
                expected_child_counts[kind] += 1

        for kind, expected_count in expected_child_counts.items():
            actual = await graph_provider.count_permission_edges_to_record_groups(
                connector_id, f"{primary['id']}-{kind}",
            )
            assert actual == expected_count, (
                f"child group {kind} has {actual} PERMISSION edge(s), expected "
                f"{expected_count} from access levels "
                f"{sorted(m.get('access_level') for m in members_by_id.values())}"
            )
        logger.info(
            "TC-GL-PERM-001 passed: %d project grant(s), children %s",
            project_perms, expected_child_counts,
        )

    @pytest.mark.order(16)
    async def test_tc_gl_perm_002_permission_type_and_resolution(
        self, gitlab_connector: dict[str, Any], graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-GL-PERM-002: every grant is OWNER, and a record resolves in one hop.

        The access level decides which groups a member reaches, never what they may do
        once there — so a Guest who reaches work items holds OWNER on them. That is the
        connector's actual contract; pinning it means a change to per-level permission
        types is reported here rather than discovered in production.

        One hop, not two: nothing inherits from the project group, so a record resolves
        through the ACL on its own child group and the project-level grant contributes
        nothing to reachability.
        """
        connector_id = gitlab_connector["connector_id"]
        primary = gitlab_connector["primary"]
        emails = gitlab_connector["app_user_emails"]
        if not emails:
            pytest.skip("No member resolved to a PipesHub identity — no USER edge to check")

        project_group = await graph_provider.get_record_group_by_external_id(
            connector_id, str(primary["id"]),
        )
        assert project_group is not None

        checked = 0
        for source_id in sorted(emails):
            user = await graph_provider.get_user_by_source_id(
                source_user_id=source_id, connector_id=connector_id,
            )
            assert user is not None, f"AppUser missing for granted member {source_id}"
            edges = await graph_provider.find_edges_between(
                CollectionNames.USERS.value, user.id,
                CollectionNames.RECORD_GROUPS.value, project_group.id,
                CollectionNames.PERMISSION.value,
            )
            assert len(edges) == 1, (
                f"member {source_id} should hold exactly one PERMISSION edge to the "
                f"project group, found {len(edges)}"
            )
            props = edges[0]
            assert props.get("type") == "USER", (
                f"a resolved member's grant must be a USER permission, got "
                f"{props.get('type')!r}"
            )
            assert props.get("role") == _GITLAB_PERMISSION_ROLE, (
                f"member {source_id} holds role {props.get('role')!r}; this connector "
                f"hard-codes {_GITLAB_PERMISSION_ROLE} for every access level"
            )
            checked += 1

        # One hop: the ticket inherits into the work-items group, which holds the ACL.
        issue = gitlab_connector["reference_issue"]
        assert await graph_provider.record_inherits_permissions(
            connector_id, str(issue["id"]),
        ), (
            f"issue #{issue['iid']} does not inherit permissions, so it resolves only "
            "to whoever holds a direct grant on it — which is nobody"
        )
        work_items_grants = await graph_provider.count_permission_edges_to_record_groups(
            connector_id, f"{primary['id']}-work-items",
        )
        assert work_items_grants > 0, (
            "the work-items group carries no grants, so every ticket under it is "
            "unreachable — the project-level grant does not help, because nothing "
            "inherits from the project group"
        )
        logger.info("TC-GL-PERM-002 passed: %d OWNER grant(s) verified", checked)


# =============================================================================
# Checkpoints and indexing
# =============================================================================


class TestGitLabCheckpointsAndIndexing:

    @pytest.mark.order(17)
    async def test_tc_gl_ckpt_001_sync_points(
        self, gitlab_connector: dict[str, Any], graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-GL-CKPT-001: one checkpoint per per-project data group.

        Three independent checkpoints, one per child group, all keyed
        ``GITLAB/{project_id}-{kind}/`` — note the trailing slash, which comes from the
        empty entity id, and that ``Connectors.GITLAB.value`` has no space in it unlike
        the GitHub Teams one. A missing checkpoint is not a visible failure: the next
        sync silently re-walks the whole project instead of the delta.
        """
        connector_id = gitlab_connector["connector_id"]
        project_id = gitlab_connector["primary"]["id"]

        for kind in ("work-items", "merge-requests"):
            key = f"GITLAB/{project_id}-{kind}/"
            point = await graph_provider.get_sync_point(connector_id, key)
            assert point, f"no sync point stored for {key}"
            assert point.get("last_sync_time"), (
                f"{key} has no last_sync_time; the next sync re-walks every "
                f"{kind.replace('-', ' ')} instead of the delta"
            )

        code_key = f"GITLAB/{project_id}-code-repository/"
        code_point = await graph_provider.get_sync_point(connector_id, code_key)
        assert code_point, f"no sync point stored for {code_key}"
        head = await branch_head_sha(
            gitlab_connector["_rest"], gitlab_connector["primary_path"],
            gitlab_connector["primary_branch"],
        )
        assert code_point.get("last_commit_sha") == head, (
            f"code checkpoint is {code_point.get('last_commit_sha')!r} but the branch "
            f"HEAD is {head!r}; the next incremental sync would compare from the wrong "
            "commit"
        )
        logger.info("TC-GL-CKPT-001 passed: 3 checkpoints, code at %s", head[:8])

    @pytest.mark.order(18)
    async def test_tc_gl_idx_001_indexing_reaches_terminal_state(
        self, gitlab_connector: dict[str, Any], graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-GL-IDX-001: an issue, an MR and a blob all leave the pipeline.

        FAILED counts as terminal here on purpose. Whether a given document parses is
        the indexing pipeline's business; what this suite owns is that the connector
        handed each record over and the record did not sit forever in NOT_STARTED,
        which is what a dropped Kafka publish looks like.
        """
        connector_id = gitlab_connector["connector_id"]
        targets: list[tuple[str, str]] = [
            (str(gitlab_connector["reference_issue"]["id"]), "reference issue"),
        ]
        if gitlab_connector["merged_mr"]:
            targets.append((str(gitlab_connector["merged_mr"]["id"]), "merged MR"))

        path = gitlab_connector["nested_code_path"]
        if path:
            code = await _code_records(graph_provider, connector_id)
            row = find_code_record(code, path)
            if row:
                targets.append((str(row["externalRecordId"]), f"code file {path}"))

        for external_id, label in targets:
            status = await _await_indexing_terminal(
                graph_provider, connector_id, external_id, label=label,
            )
            assert status != ProgressStatus.AUTO_INDEX_OFF.value, (
                f"{label} is AUTO_INDEX_OFF with default filters. The likely cause is "
                "enable_manual_sync being set, which disables EVERY indexing filter at "
                "once rather than just manual scheduling."
            )
            logger.info("TC-GL-IDX-001: %s -> %s", label, status)
        logger.info("TC-GL-IDX-001 passed: %d record(s) reached a terminal state", len(targets))


# =============================================================================
# Incremental sync — dedicated connectors on the mutation project
# =============================================================================


class TestGitLabIncremental:

    @pytest.mark.order(19)
    async def test_tc_incr_issue_001_create_then_update(
        self, gitlab_connector: dict[str, Any], gitlab_rest: GitLabRestClient,
        pipeshub_client: PipeshubClient, graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-INCR-ISSUE-001: a new issue is picked up, then an edit bumps the version.

        The ``updated_after`` watermark is inclusive, so the boundary issue is
        re-fetched on every sync. That is an idempotent upsert, not a defect — which is
        why this asserts on version and revision rather than on "how many issues did
        this sync touch".
        """
        project = gitlab_connector["mutation_path"]
        title = artifact_title("Issue")
        created: dict[str, Any] | None = None

        async with dedicated_connector(
            pipeshub_client, graph_provider,
            token=gitlab_connector["token"], name=_connector_name("incr-issue"),
            instance_url=gitlab_connector["instance_url"],
            filters=_mutation_filters(gitlab_connector),
        ) as connector_id:
            try:
                created = await create_issue(
                    gitlab_rest, project, title=title,
                    description="Created by TC-INCR-ISSUE-001.",
                )
                await _resync(pipeshub_client, graph_provider, connector_id)

                external_id = str(created["id"])
                record = await wait_for_record_by_external_id(
                    graph_provider, connector_id, external_id,
                    timeout=GL_SYNC_WAIT_SEC, description="new issue",
                )
                assert record.record_name == title
                first_id, first_version = record.id, record.version

                new_title = f"{title} (edited)"
                await update_issue(gitlab_rest, project, created["iid"], title=new_title)
                await add_note(
                    gitlab_rest, project, "issues", created["iid"],
                    "TC-INCR-ISSUE-001 follow-up comment.",
                )
                await _resync(pipeshub_client, graph_provider, connector_id)

                updated = await get_issue(gitlab_rest, project, created["iid"])
                after = await graph_provider.get_record_by_external_id(
                    connector_id, external_id,
                )
                assert after is not None, "the issue disappeared after the second sync"
                assert after.id == first_id, (
                    "an edited issue must reuse its record; a new id means the upsert "
                    "keyed on something that changed"
                )
                assert after.record_name == new_title
                assert after.version == first_version + 1, (
                    f"version {after.version} after an edit, expected "
                    f"{first_version + 1}"
                )
                assert after.external_revision_id == str(_epoch_ms(updated["updated_at"])), (
                    "the revision must track GitLab's updated_at, or a later change "
                    "cannot be detected"
                )
            finally:
                if created:
                    await delete_issue(gitlab_rest, project, created["iid"])
        logger.info("TC-INCR-ISSUE-001 passed")

    @pytest.mark.order(20)
    async def test_tc_incr_mr_001_update_only(
        self, gitlab_connector: dict[str, Any], gitlab_rest: GitLabRestClient,
        pipeshub_client: PipeshubClient, graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-INCR-MR-001: updating an MR must not create a second record.

        The pinned MR is updated in place rather than created per run: an MR per run
        would accumulate on the shared project, and its title sits outside the artifact
        pattern so the stale sweep can never reach it.
        """
        project = gitlab_connector["mutation_path"]
        branch = "mr-fixture-incremental"
        marker = f"{PINNED_MR_COMMENT_MARKER} {GL_IT_RUN_ID}"
        note_id: int | None = None

        pinned = await get_merge_request(gitlab_rest, project, GL_INCR_MR_IID)
        if pinned["state"] != "opened":
            pytest.skip(
                f"pinned MR !{GL_INCR_MR_IID} is {pinned['state']}, not open. It must "
                "stay open for this case; reopen it or re-run the fixture seed."
            )

        async with dedicated_connector(
            pipeshub_client, graph_provider,
            token=gitlab_connector["token"], name=_connector_name("incr-mr"),
            instance_url=gitlab_connector["instance_url"],
            filters=_mutation_filters(gitlab_connector),
        ) as connector_id:
            try:
                external_id = str(pinned["id"])
                before = await wait_for_record_by_external_id(
                    graph_provider, connector_id, external_id,
                    timeout=GL_SYNC_WAIT_SEC, description="pinned MR",
                )
                first_id, first_version = before.id, before.version
                mr_count_before = await graph_provider.count_records_by_type(
                    connector_id, RecordType.PULL_REQUEST.value, scoped=True,
                )

                new_title = f"IT fixture: incremental MR update target (do not close) [{GL_IT_RUN_ID}]"
                # A commit on the MR's branch, so the diff blocks change too — an MR
                # update that only touches metadata would not exercise the block
                # rebuild.
                await commit_actions(
                    gitlab_rest, project, branch,
                    f"TC-INCR-MR-001 run {GL_IT_RUN_ID}",
                    [{"action": "update", "file_path": PINNED_MR_FILE,
                      "content": f"Rewritten by run {GL_IT_RUN_ID}.\n"}],
                    allow_paths=(PINNED_MR_FILE,),
                )
                await update_merge_request(
                    gitlab_rest, project, GL_INCR_MR_IID, title=new_title,
                )
                note = await add_note(
                    gitlab_rest, project, "merge_requests", GL_INCR_MR_IID, marker,
                )
                note_id = note["id"]

                await _resync(pipeshub_client, graph_provider, connector_id)

                after = await graph_provider.get_record_by_external_id(
                    connector_id, external_id,
                )
                assert after is not None, "the pinned MR disappeared after the resync"
                assert after.id == first_id, (
                    "an updated MR must reuse its record; a second record means the "
                    "upsert keyed on something other than the MR's global id"
                )
                assert after.record_name == new_title
                assert after.version == first_version + 1, (
                    f"version {after.version} after an update, expected "
                    f"{first_version + 1}"
                )
                mr_count_after = await graph_provider.count_records_by_type(
                    connector_id, RecordType.PULL_REQUEST.value, scoped=True,
                )
                assert mr_count_after == mr_count_before, (
                    f"MR record count moved {mr_count_before} -> {mr_count_after} on an "
                    "update-only sync; the update created a duplicate"
                )
            finally:
                if note_id:
                    await delete_note(
                        gitlab_rest, project, "merge_requests", GL_INCR_MR_IID, note_id,
                    )
        logger.info("TC-INCR-MR-001 passed")

    @pytest.mark.order(21)
    async def test_tc_incr_code_001_five_deltas(
        self, gitlab_connector: dict[str, Any], gitlab_rest: GitLabRestClient,
        pipeshub_client: PipeshubClient, graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-INCR-CODE-001: new / update / rename / move / delete in one commit set.

        One commit, because git records a move as a single tree transition only when
        both sides land together — two commits produce a delete plus an add, and the
        rename branch is never exercised.

        All five paths live under ``it/<run_id>/``: the connector only ever syncs the
        default branch, so concurrent runs necessarily share it, and a per-run
        directory is the only thing keeping one run's deltas out of another's
        assertions.
        """
        project = gitlab_connector["mutation_path"]
        branch = gitlab_connector["mutation_branch"]

        keep = it_path("keep", "steady.py")
        update_me = it_path("keep", "updated.py")
        rename_src = it_path("keep", "rename_me.py")
        rename_dst = it_path("keep", "renamed.py")
        move_src = it_path("movesrc", "moved.py")
        move_dst = it_path("movedst", "moved.py")
        delete_me = it_path("keep", "delete_me.py")
        added = it_path("added", "brand_new.py")

        baseline = [
            {"action": "create", "file_path": p, "content": f"# {p}\nVALUE = 1\n"}
            for p in (keep, update_me, rename_src, move_src, delete_me)
        ]
        await commit_actions(
            gitlab_rest, project, branch,
            f"TC-INCR-CODE-001 baseline {GL_IT_RUN_ID}", baseline,
        )

        async with dedicated_connector(
            pipeshub_client, graph_provider,
            token=gitlab_connector["token"], name=_connector_name("incr-code"),
            instance_url=gitlab_connector["instance_url"],
            filters=_mutation_filters(gitlab_connector),
        ) as connector_id:
            before = await _code_records(graph_provider, connector_id)
            for path in (keep, update_me, rename_src, move_src, delete_me):
                assert find_code_record(before, path) is not None, (
                    f"baseline file {path} was not synced; the deltas below have "
                    "nothing to act on"
                )
            rename_before = find_code_record(before, rename_src)
            update_before = find_code_record(before, update_me)

            await commit_actions(
                gitlab_rest, project, branch,
                f"TC-INCR-CODE-001 deltas {GL_IT_RUN_ID}",
                [
                    {"action": "create", "file_path": added,
                     "content": "# added by TC-INCR-CODE-001\nVALUE = 0\n"},
                    {"action": "update", "file_path": update_me,
                     "content": f"# {update_me}\nVALUE = 2\n"},
                    {"action": "move", "file_path": rename_dst,
                     "previous_path": rename_src},
                    {"action": "move", "file_path": move_dst,
                     "previous_path": move_src},
                    {"action": "delete", "file_path": delete_me},
                ],
            )
            await _resync(pipeshub_client, graph_provider, connector_id)
            after = await _code_records(graph_provider, connector_id)

            # (a) new file, in a directory that did not exist before
            new_row = find_code_record(after, added)
            assert new_row is not None, f"new file {added} did not sync"
            new_parent = await graph_provider.get_record_parent_external_id(
                connector_id, str(new_row["externalRecordId"]),
            )
            assert new_parent == folder_id_of(str(new_row["externalRecordId"])), (
                "a file in a brand-new directory needs its folder chain built by the "
                f"incremental path, got parent {new_parent!r}"
            )

            # (b) update: same record, new blob sha
            update_row = find_code_record(after, update_me)
            assert update_row is not None, f"{update_me} vanished after an update"
            assert _record_key(update_row) == _record_key(update_before), (
                "an updated file must reuse its record vertex"
            )
            live_sha = await blob_sha_for_path(gitlab_rest, project, update_me, branch)
            assert str(update_row.get("externalRevisionId")) == str(live_sha), (
                f"revision is {update_row.get('externalRevisionId')!r} but the blob sha "
                f"is {live_sha!r}; the content change was not recorded"
            )

            # (c) rename in place: the vertex is reused under the new id, and the
            #     version does NOT move — content_changed compares the revision, and a
            #     rename carries the same blob sha, so there is nothing to reindex.
            assert find_code_record(after, rename_src) is None, (
                f"{rename_src} still has a record after being renamed away"
            )
            rename_row = find_code_record(after, rename_dst)
            assert rename_row is not None, f"renamed file {rename_dst} has no record"
            assert _record_key(rename_row) == _record_key(rename_before), (
                "a rename must reuse the record vertex, or every renamed file loses its "
                "indexed content and is re-embedded from scratch"
            )

            # (d) move to another directory: new parent, old directory cleaned up
            assert find_code_record(after, move_src) is None
            move_row = find_code_record(after, move_dst)
            assert move_row is not None, f"moved file {move_dst} has no record"
            move_parent = await graph_provider.get_record_parent_external_id(
                connector_id, str(move_row["externalRecordId"]),
            )
            assert move_parent == folder_id_of(str(move_row["externalRecordId"])), (
                f"moved file's parent is {move_parent!r}, expected its new directory"
            )
            emptied = folder_id_of(str(find_code_record(before, move_src)["externalRecordId"]))
            assert await graph_provider.get_record_by_external_id(
                connector_id, emptied,
            ) is None, (
                f"the emptied directory {emptied} still has a folder record; it now "
                "shows in the browse view as a folder with no contents"
            )

            # (e) delete
            assert find_code_record(after, delete_me) is None, (
                f"{delete_me} was deleted from the repository but still has a record"
            )

            # Untouched files must be left alone — an incremental sync that rewrites
            # every record would pass all of the above and still be a regression.
            keep_before = find_code_record(before, keep)
            keep_after = find_code_record(after, keep)
            assert keep_after is not None and _record_key(keep_after) == _record_key(keep_before)
            assert keep_after.get("version") == keep_before.get("version"), (
                "an untouched file's version moved; the sync rewrote records outside "
                "the delta"
            )
        logger.info("TC-INCR-CODE-001 passed: five deltas applied correctly")


# =============================================================================
# Filters — dedicated connectors
# =============================================================================


class TestGitLabFilters:

    @pytest.mark.order(22)
    async def test_tc_filter_001_group_scope(
        self, gitlab_connector: dict[str, Any],
        pipeshub_client: PipeshubClient, graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-FILTER-001: ``group_ids`` expands subgroups and parents the project.

        A different resolution path from ``project_ids``: the group is walked with
        ``include_subgroups``, and the group node the projects hang off is the *listed*
        path rather than each project's own namespace. Getting this wrong strands
        projects in the browse view even though every record synced.
        """
        subgroup = gitlab_connector["subgroup_path"]
        if not subgroup:
            pytest.skip("GITLAB_TEST_SUBGROUP not set — group scoping not exercised")

        primary = gitlab_connector["primary"]
        mutation = gitlab_connector["mutation"]
        if not primary["path_with_namespace"].startswith(f"{subgroup}/"):
            pytest.skip(
                f"the primary project is not under {subgroup!r}, so this case cannot "
                "distinguish subgroup expansion from a plain project filter"
            )

        async with dedicated_connector(
            pipeshub_client, graph_provider,
            token=gitlab_connector["token"], name=_connector_name("filter-group"),
            instance_url=gitlab_connector["instance_url"],
            filters=sync_filters(group_ids=list_filter("in", [subgroup])),
            min_records=1,
        ) as connector_id:
            group_node = await graph_provider.get_record_group_by_external_id(
                connector_id, subgroup,
            )
            assert group_node is not None, (
                f"no record group for the listed group {subgroup!r}"
            )

            project_group = await graph_provider.get_record_group_by_external_id(
                connector_id, str(primary["id"]),
            )
            assert project_group is not None, (
                f"{primary['path_with_namespace']} is under {subgroup} and should have "
                "synced through subgroup expansion"
            )
            assert project_group.parent_external_group_id == subgroup, (
                f"under a group_ids filter the project hangs off the listed group; got "
                f"parent {project_group.parent_external_group_id!r}, expected {subgroup!r}"
            )

            # The mutation project shares the top-level group but not the subgroup.
            outside = await graph_provider.get_record_group_by_external_id(
                connector_id, str(mutation["id"]),
            )
            assert outside is None, (
                f"{mutation['path_with_namespace']} is outside {subgroup} but synced "
                "anyway — the subgroup walk is reaching the parent group"
            )
        logger.info("TC-FILTER-001 passed: %s expanded, siblings excluded", subgroup)

    @pytest.mark.order(23)
    async def test_tc_filter_002_code_files_indexing_off(
        self, gitlab_connector: dict[str, Any],
        pipeshub_client: PipeshubClient, graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-FILTER-002: with code-file indexing off, records exist but do not index.

        An indexing filter is not a sync filter: the records are still written, so the
        browse view is complete, and only the pipeline handover is suppressed. A
        regression that turns it into a sync filter loses the records entirely — which
        looks identical from the search side and completely different from the graph.
        """
        filters = sync_filters(
            project_ids=list_filter("in", [gitlab_connector["mutation_path"]]),
        )
        filters.update(indexing_filters(code_files=bool_filter(False)))

        async with dedicated_connector(
            pipeshub_client, graph_provider,
            token=gitlab_connector["token"], name=_connector_name("filter-index"),
            instance_url=gitlab_connector["instance_url"],
            filters=filters, min_records=1,
        ) as connector_id:
            code = await _code_records(graph_provider, connector_id)
            assert code, (
                "no CODE_FILE records at all. Turning INDEXING off must still sync the "
                "records — this looks like the flag was applied as a sync filter."
            )
            not_off = [
                str(r.get("externalRecordId")) for r in code
                if r.get("indexingStatus") != ProgressStatus.AUTO_INDEX_OFF.value
            ]
            assert not not_off, (
                f"{len(not_off)} code record(s) are not AUTO_INDEX_OFF with code-file "
                f"indexing disabled, e.g. {not_off[:3]}"
            )

            folders = await _folder_records(graph_provider, connector_id)
            folders_not_off = [
                str(r.get("externalRecordId")) for r in folders
                if r.get("indexingStatus") != ProgressStatus.AUTO_INDEX_OFF.value
            ]
            assert not folders_not_off, (
                "folder records carry the same indexing flag as the files they hold; "
                f"{len(folders_not_off)} are still indexable, which publishes a Kafka "
                "message and two status writes per folder for a filter the user turned off"
            )
        logger.info(
            "TC-FILTER-002 passed: %d code record(s) synced and all AUTO_INDEX_OFF",
            len(code),
        )


# =============================================================================
# Small shared utilities used above
# =============================================================================


def _stream_blocks(pipeshub_client: PipeshubClient, record_id: str, *, label: str) -> bytes:
    """Stream one record's ``application/blocks`` payload.

    The client's 60s default is not enough for the first stream of a session: the
    connector service builds the block payload on demand, and the first call pays the
    parser and processor warm-up on top of the per-issue comment and attachment
    fetches. Later streams in the same session return in a few seconds. Raising the
    ceiling for these two calls beats making every request in the suite wait longer.
    """
    original = pipeshub_client.timeout_seconds
    pipeshub_client.timeout_seconds = GL_STREAM_WAIT_SEC
    try:
        resp = pipeshub_client.stream_record(record_id)
    finally:
        pipeshub_client.timeout_seconds = original
    assert resp.status_code == 200, f"stream_record({label}) HTTP {resp.status_code}"
    content_type = (resp.headers.get("content-type") or "").lower()
    assert "application/blocks" in content_type, (
        f"{label} streamed as {content_type!r}, not application/blocks"
    )
    return resp.content


def _require_stream_access(state: dict[str, Any]) -> None:
    """Skip unless the PipesHub account this suite streams as holds a GitLab grant.

    ``stream_record`` enforces the record ACL, and a GitLab member is bound to a
    PipesHub identity by ``public_email`` alone. When the fixture account publishes a
    different address from the one the harness logs in as, every grant lands on a
    stranger and the stream is a 403 that says nothing about the block builder.
    """
    if state.get("stream_user_email") in (state.get("app_user_emails") or {}).values():
        return
    pytest.skip(
        "The PipesHub account this suite runs as "
        f"({state.get('stream_user_email')!r}) holds no grant on the synced records: "
        f"the GitLab fixture members resolved to "
        f"{sorted((state.get('app_user_emails') or {}).values())}. GitLab binds a "
        "member to a PipesHub identity through public_email only, so set the fixture "
        "account's public email (GitLab → Profile → Public email) to the harness "
        "address, or clear it so the connector's creator bypass binds the member to "
        "the configuring user instead. Until then stream_record answers 403 and these "
        "cases would fail for a reason unrelated to the block builder."
    )


def _epoch_ms(timestamp: str) -> int:
    from app.utils.time_conversion import parse_timestamp  # noqa: PLC0415

    return int(parse_timestamp(timestamp))


async def _notes_for(
    gitlab_connector: dict[str, Any], kind: str, iid: int,
) -> list[dict[str, Any]]:
    return await list_notes(
        gitlab_connector["_rest"], gitlab_connector["primary_path"], kind, iid,
    )

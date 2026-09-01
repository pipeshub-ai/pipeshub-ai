# pyright: ignore-file

"""
Linear Connector – Integration Tests
=====================================

Test cases:
  TC-SYNC-001         — Full sync + strict graph baselines vs API snapshot
  TC-LINEAR-001       — User exists; USER_APP_RELATION == connector-style Linear user fetch
  TC-LINEAR-002       — Filtered teams as UserGroups + member edges
  TC-LINEAR-003       — Primary team as RecordGroup; reference issue belongs to team
  TC-LINEAR-004       — Reference issue TICKET fields, webUrl, edges
  TC-LINEAR-005       — Reference project ProjectRecord properties (skip if none)
  TC-LINEAR-006       — Reference attachment LinkRecord fields + edges (skip if none)
  TC-LINEAR-007       — Reference document WebpageRecord fields + edges (skip if none)
  TC-LINEAR-008       — Reference markdown FILE record fields + edges (skip if none)
  TC-LINEAR-IDX-001   — Reference issue ``indexing_status`` COMPLETED
  TC-INCR-001         — Create new issue (test-time); incremental sync picks it up; cleanup
  TC-UPDATE-001       — Create + edit title (test-owned issue); version +1; revision match; delete
  TC-LINEAR-PH-001    — Placeholder ancestors: minted → swept → promoted
  TC-LINEAR-EDGES-001 — Edge inventory after incremental tests
  TC-LINEAR-PERM-001  — Team privacy → ORG or GROUP permission on RecordGroup

Every run shares one Linear workspace with every other CI leg and PR; ``README.md`` in
this directory is the contract that keeps them from touching each other. In short: the
module connector syncs once and is read-only afterwards, anything that writes or needs its
own sync history runs on ``_dedicated_connector``, and every created issue carries
``artifact_title(...)`` and is registered for cleanup.
"""

import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Dict

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.config.constants.arangodb import ProgressStatus  # type: ignore[import-not-found]  # noqa: E402
from app.connectors.sources.linear.connector import (  # type: ignore[import-not-found]  # noqa: E402
    PLACEHOLDER_REVISION_PREFIX,
)
from app.models.entities import (  # type: ignore[import-not-found]  # noqa: E402
    LinkRecord,
    RecordType,
)
from app.sources.external.linear.linear import LinearDataSource  # type: ignore[import-not-found]  # noqa: E402
from helper.assertions import ConnectorAssertions, RecordAssertion  # noqa: E402
from helper.graph_provider import GraphProviderProtocol  # noqa: E402
from helper.graph_provider_utils import (  # noqa: E402
    apply_filter_full_sync,
    async_poll_until,
    count_owned_records,
    wait_for_record_by_external_id,
    wait_for_sync_completion,
)
from connectors.linear.linear_expected import LinearExpected  # noqa: E402
from validation.graph_entity_validator import (  # noqa: E402
    assert_graph_entity_matches,
    assert_graph_entity_with_edges,
    assert_user_app_edge,
)
from validation.graph_edge_validator import (  # noqa: E402
    assert_graph_edges,
    build_record_edge_expectations,
)
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]  # noqa: E402
from connectors.linear.constants import (  # noqa: E402
    LINEAR_INDEXING_WAIT_SEC,
    LINEAR_IT_ARTIFACT_PREFIX,
    LINEAR_IT_RUN_ID,
    LINEAR_PH_CHILD_IDENTIFIER,
    artifact_title,
)
from connectors.linear.linear_test_utils import (  # noqa: E402
    _api_call_with_retry,
    assert_linear_issues_match_graph_records,
    check_issue_exists_bool,
    count_linear_users_with_email,
    create_artifact_issue,
    delete_artifact_issue,
    fetch_ancestor_chain,
    get_linear_issue_updated_ms,
    parse_linear_timestamp,
    resolve_issue_by_identifier,
    wait_until_linear_condition,
    wait_until_record_indexing_completed,
)

logger = logging.getLogger("linear-lifecycle-test")


def _restart_sync(pipeshub_client: PipeshubClient, connector_id: str) -> None:
    """Disable then re-enable the connector to trigger a fresh incremental sync."""
    pipeshub_client.toggle_sync(connector_id, enable=False)
    pipeshub_client.wait(5)
    pipeshub_client.toggle_sync(connector_id, enable=True)
    pipeshub_client.wait(8)


def _team_filters(team_ids: list[str], **extra: Any) -> Dict[str, Any]:
    """Connector ``filters`` payload scoped to ``team_ids`` (plus any extra sync filters)."""
    values: Dict[str, Any] = {
        "team_ids": {"operator": "in", "type": "list", "value": list(team_ids)},
        **extra,
    }
    return {"sync": {"values": values}}


@asynccontextmanager
async def _dedicated_connector(
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
    *,
    name: str,
    filters: Dict[str, Any],
    min_records: int | None = None,
    timeout: int = 240,
) -> AsyncIterator[str]:
    """Create a Linear connector, run its first sync, yield its id, then remove it.

    Mutating tests and scope-sensitive tests each get their own connector so the module
    connector — the source of every baseline — never syncs again after setup. Cleanup
    failures are logged, not raised: they would mask the test body's own failure, and a
    leaked connector is visible in the dashboard.
    """
    instance = pipeshub_client.create_connector(
        connector_type="Linear",
        instance_name=name,
        scope="team",
        config={
            "auth": {"authType": "API_TOKEN", "apiToken": os.getenv("LINEAR_TEST_API_TOKEN")},
            "filters": filters,
        },
        auth_type="API_TOKEN",
    )
    connector_id = instance.connector_id
    assert connector_id, f"{name}: connector creation returned no id"
    try:
        pipeshub_client.toggle_sync(connector_id, enable=True)
        await wait_for_sync_completion(
            pipeshub_client, graph_provider, connector_id,
            min_records=min_records, timeout=timeout,
        )
        yield connector_id
    finally:
        try:
            pipeshub_client.toggle_sync(connector_id, enable=False)
            pipeshub_client.delete_connector(connector_id)
            pipeshub_client.wait(25)
            await graph_provider.assert_all_records_cleaned(
                connector_id,
                timeout=int(os.getenv("INTEGRATION_GRAPH_CLEANUP_TIMEOUT", "300")),
            )
        except Exception as e:
            logger.error(
                "%s cleanup: connector %s leaked, delete it manually: %s", name, connector_id, e,
            )


async def _record_absent(
    graph_provider: GraphProviderProtocol, connector_id: str, external_id: str,
) -> bool:
    return await graph_provider.get_record_by_external_id(connector_id, external_id) is None


pytestmark = [
    pytest.mark.integration,
    pytest.mark.linear,
    pytest.mark.asyncio(loop_scope="session"),
]


# =============================================================================
# TestLinearConnector — full sync, incremental, update
# =============================================================================


class TestLinearConnector:
    """Sync-pipeline tests: full sync, incremental, update (no reparent)."""

    @pytest.mark.order(1)
    async def test_tc_sync_001_full_sync_graph_validation(
        self,
        linear_connector: Dict[str, Any],
        linear_datasource: LinearDataSource,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-SYNC-001: validate the graph after the fixture's full sync vs API snapshot."""
        connector_id = linear_connector["connector_id"]
        team_ids = linear_connector["team_ids"]

        await graph_provider.assert_min_records(connector_id, 1)
        await graph_provider.assert_record_groups_and_edges(
            connector_id,
            min_groups=linear_connector["expected_record_groups"],
            min_record_edges=linear_connector["expected_total_records"],
        )
        await graph_provider.assert_no_orphan_records(connector_id)

        # Baselines compare owned counts (a concurrently running leg's mutation issue can
        # enter or leave this shared workspace mid-run); structural edge invariants below
        # compare the raw count, since every record carries those edges, artifact or not.
        owned_tickets = await count_owned_records(
            graph_provider, connector_id,
            prefix=LINEAR_IT_ARTIFACT_PREFIX, record_type=RecordType.TICKET.value,
        )
        owned_records = await count_owned_records(
            graph_provider, connector_id, prefix=LINEAR_IT_ARTIFACT_PREFIX,
        )
        all_records = await graph_provider.count_records(connector_id)
        project_count = await graph_provider.count_records_by_type(connector_id, RecordType.PROJECT.value)
        link_count = await graph_provider.count_records_by_type(connector_id, RecordType.LINK.value)
        webpage_count = await graph_provider.count_records_by_type(connector_id, RecordType.WEBPAGE.value)
        file_count = await graph_provider.count_records_by_type(connector_id, RecordType.FILE.value)

        assert owned_tickets == linear_connector["expected_ticket_count"]
        assert project_count == linear_connector["expected_project_count"]
        # Reference-record presence (checked in the fixture) already proves each
        # dependent type synced correctly; here we only confirm the type exists at all.
        assert link_count >= 1, "No LINK records after sync"
        assert webpage_count >= 1, "No WEBPAGE records after sync"
        assert file_count >= 1, "No FILE records after sync"

        assert owned_records >= linear_connector["expected_total_records"], (
            f"owned records: graph={owned_records} (of {all_records}) "
            f"< expected={linear_connector['expected_total_records']}"
        )

        pc_edges = await graph_provider.count_parent_child_edges(connector_id)
        assert pc_edges >= linear_connector["expected_parent_child_edges"], (
            f"PARENT_CHILD edges: {pc_edges} < expected={linear_connector['expected_parent_child_edges']}"
        )

        rg_edges = await graph_provider.count_record_group_edges(connector_id)
        assert rg_edges == all_records, (
            f"BELONGS_TO record->group count {rg_edges} must equal total records {all_records}"
        )

        inherit = await graph_provider.count_inherit_permissions_edges(connector_id)
        assert inherit == all_records

        app_edges = await graph_provider.count_app_record_group_edges(connector_id)
        rgs = await graph_provider.count_record_groups(connector_id)
        assert app_edges == rgs == linear_connector["expected_record_groups"]

        graph_app = await graph_provider.get_app_metadata_by_connector_id(connector_id)
        assert graph_app is not None, f"apps document missing for connector {connector_id}"
        expected_app = LinearExpected.app_metadata_for_full_sync_baseline(linear_connector)
        app_skip = frozenset({
            "created_at_timestamp", "updated_at_timestamp",
            "auth_type", "is_active", "is_agent_active", "is_configured",
            "is_authenticated", "created_by", "updated_by", "last_synced_by",
            "status", "is_locked",
        })
        assert_graph_entity_matches(
            expected_app, graph_app, entity="app_metadata", skip_compare=app_skip,
        )

        await assert_linear_issues_match_graph_records(
            linear_datasource, graph_provider, connector_id, team_ids,
            phase="TC-SYNC-001",
        )

        summary = await graph_provider.graph_summary(connector_id)
        logger.info("TC-SYNC-001 passed: %s", summary)

    @pytest.mark.order(8)
    async def test_tc_incr_001_incremental_sync_new_issue(
        self,
        linear_connector: Dict[str, Any],
        linear_datasource: LinearDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-INCR-001: an issue created after the first sync arrives on the next incremental
        sync; trashing it in Linear removes it on the one after (``_sync_deleted_issues``).

        Runs on a dedicated connector scoped to the mutation team: the module connector
        never syncs again after setup, so nothing here can move its baselines. Asserted by
        external id, never by a record-count delta — the workspace is shared with any
        concurrently running leg, whose own create/delete moves totals under us.
        """
        team_id = linear_connector["mutation_team_id"]
        title = artifact_title("IncrTest")
        issue_id: str | None = None

        async with _dedicated_connector(
            pipeshub_client, graph_provider,
            name=f"linear-incr-{LINEAR_IT_RUN_ID}", filters=_team_filters([team_id]),
        ) as connector_id:
            try:
                issue_id = await create_artifact_issue(
                    linear_datasource, team_id=team_id, title=title, context="TC-INCR-001",
                )
                await wait_until_linear_condition(
                    check_fn=lambda: check_issue_exists_bool(linear_datasource, issue_id),
                    description=f"TC-INCR-001: new issue fetchable ({issue_id})",
                    timeout=120,
                )

                _restart_sync(pipeshub_client, connector_id)
                await wait_for_sync_completion(
                    pipeshub_client, graph_provider, connector_id, timeout=240,
                )
                await wait_for_record_by_external_id(
                    graph_provider, connector_id, issue_id,
                    timeout=120, description="TC-INCR-001 new issue",
                )

                actual = await graph_provider.get_typed_record_by_external_id(connector_id, issue_id)
                assert actual is not None, f"typed TICKET record missing for {issue_id}"
                expected = await LinearExpected.ticket_record(
                    issue_id,
                    connector_id=connector_id,
                    datasource=linear_datasource,
                )
                await assert_graph_entity_with_edges(
                    expected, actual,
                    entity="ticket_record",
                    connector_id=connector_id,
                    graph_provider=graph_provider,
                    # created_at/updated_at are set by Linear and can drift between when the
                    # connector fetches the issue (during sync) and when the test re-fetches it here.
                    skip_compare=frozenset({"created_at", "updated_at"}),
                )
                await graph_provider.assert_record_paths_or_names_contain(connector_id, [title])

                # Deletion path: trash it in Linear; the trashed-issue pass of the next
                # incremental sync must hard-delete the record.
                assert await delete_artifact_issue(
                    linear_datasource, issue_id=issue_id, context="TC-INCR-001",
                ), f"TC-INCR-001: {issue_id} was not confirmed trashed in Linear"
                _restart_sync(pipeshub_client, connector_id)
                await wait_for_sync_completion(
                    pipeshub_client, graph_provider, connector_id, timeout=240,
                )
                await async_poll_until(
                    lambda: _record_absent(graph_provider, connector_id, issue_id),
                    timeout=90, interval=5,
                    description=f"TC-INCR-001: trashed issue {issue_id} removed from the graph",
                )
                logger.info("TC-INCR-001 passed: %s synced, then removed after trashing", issue_id)
            finally:
                if issue_id:
                    await delete_artifact_issue(
                        linear_datasource, issue_id=issue_id, context="TC-INCR-001 cleanup",
                    )

    @pytest.mark.order(9)
    async def test_tc_update_001_title_revision(
        self,
        linear_connector: Dict[str, Any],
        linear_datasource: LinearDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-UPDATE-001: edit a test-owned issue; version += 1; revision = Linear updatedAt ms.

        The issue is created here rather than reusing the pinned reference issue: this
        workspace is shared with any concurrently running leg, so editing a shared issue made
        each run assert against the other's title and restore the other's value permanently.
        Same dedicated-connector shape as TC-INCR-001.
        """
        team_id = linear_connector["mutation_team_id"]
        target_id: str | None = None

        async with _dedicated_connector(
            pipeshub_client, graph_provider,
            name=f"linear-update-{LINEAR_IT_RUN_ID}", filters=_team_filters([team_id]),
        ) as connector_id:
            try:
                target_id = await create_artifact_issue(
                    linear_datasource, team_id=team_id, title=artifact_title("UpdTest"),
                    context="TC-UPDATE-001",
                )
                await wait_until_linear_condition(
                    check_fn=lambda: check_issue_exists_bool(linear_datasource, target_id),
                    description=f"TC-UPDATE-001: new issue fetchable ({target_id})",
                    timeout=120,
                )

                _restart_sync(pipeshub_client, connector_id)
                await wait_for_sync_completion(
                    pipeshub_client, graph_provider, connector_id, timeout=240,
                )
                record_before = await wait_for_record_by_external_id(
                    graph_provider, connector_id, target_id,
                    timeout=120, description="TC-UPDATE-001 baseline record",
                )
                old_version = int(record_before.version)

                new_title = artifact_title("Edited")
                await _api_call_with_retry(
                    linear_datasource.issueUpdate, id=target_id, input={"title": new_title},
                    context="TC-UPDATE-001 issueUpdate",
                )

                pipeshub_client.wait(5)

                _restart_sync(pipeshub_client, connector_id)
                await wait_for_sync_completion(
                    pipeshub_client, graph_provider, connector_id, timeout=240,
                )

                record_after = await graph_provider.get_record_by_external_id(connector_id, target_id)
                assert record_after is not None, "Record missing after sync"
                assert record_after.version == old_version + 1, (
                    f"Expected version {old_version + 1}, got {record_after.version}"
                )

                linear_updated_ms = await get_linear_issue_updated_ms(linear_datasource, target_id)
                assert str(record_after.external_revision_id) == str(linear_updated_ms), (
                    f"Graph external_revision_id {record_after.external_revision_id!r} should equal "
                    f"Linear updatedAt epoch ms {linear_updated_ms}"
                )
                assert new_title in (record_after.record_name or ""), (
                    f"Record name '{record_after.record_name}' should contain new title '{new_title}'"
                )
                logger.info("TC-UPDATE-001 passed: version %s -> %s", old_version, record_after.version)

            finally:
                if target_id:
                    await delete_artifact_issue(
                        linear_datasource, issue_id=target_id, context="TC-UPDATE-001 cleanup",
                    )


# =============================================================================
# TestLinearValidation — entity / relationship validation (read-only)
# =============================================================================


class TestLinearValidation:
    """Entity / relationship validation against the synced state of existing data."""

    @pytest.mark.order(2)
    async def test_tc_linear_001_user_properties(
        self,
        linear_connector: Dict[str, Any],
        linear_datasource: LinearDataSource,
        connector_assertions: ConnectorAssertions,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-LINEAR-001: synced user exists; USER_APP_RELATION matches Linear user pool."""
        connector_id = linear_connector["connector_id"]
        viewer_id = linear_connector.get("viewer_id")
        viewer_email = linear_connector.get("viewer_email")

        if not viewer_id or not viewer_email:
            pytest.skip("viewer_id or viewer_email missing from fixture")

        await connector_assertions.assert_user_exists(
            connector_id=connector_id, source_user_id=viewer_id, email=viewer_email,
        )

        linear_users_with_email = await count_linear_users_with_email(linear_datasource)
        rel_count = await graph_provider.count_user_app_relation_edges(connector_id)
        assert rel_count == linear_users_with_email, (
            f"USER_APP_RELATION count {rel_count} != Linear active users with email "
            f"({linear_users_with_email}) (connector {connector_id})"
        )

        await assert_user_app_edge(
            viewer_id, connector_id=connector_id, graph_provider=graph_provider,
        )

        logger.info("TC-LINEAR-001 passed: %d users validated", rel_count)

    @pytest.mark.order(3)
    async def test_tc_linear_002_team_as_user_group(
        self,
        linear_connector: Dict[str, Any],
        connector_assertions: ConnectorAssertions,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-LINEAR-002: filtered teams synced as UserGroups with member edges."""
        connector_id = linear_connector["connector_id"]
        teams = linear_connector["teams"]

        graph_group_total = await graph_provider.count_user_groups(connector_id)
        assert graph_group_total == len(teams), (
            f"Graph UserGroup count {graph_group_total} != filtered team count {len(teams)}"
        )

        primary = teams[0]
        graph_ug = await connector_assertions.assert_group_exists(
            connector_id=connector_id,
            external_group_id=primary["id"],
            name=primary["name"],
        )

        expected_ug = LinearExpected.user_group(
            name=primary["name"],
            source_user_group_id=primary["id"],
            connector_id=connector_id,
        )
        ug_skip = frozenset({"created_at", "updated_at", "source_created_at", "source_updated_at"})
        assert_graph_entity_matches(
            expected_ug, graph_ug, entity="app_user_group", skip_compare=ug_skip,
        )

        logger.info("TC-LINEAR-002 passed: %d teams validated as UserGroups", graph_group_total)

    @pytest.mark.order(5)
    async def test_tc_linear_003_team_as_record_group(
        self,
        linear_connector: Dict[str, Any],
        connector_assertions: ConnectorAssertions,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-LINEAR-003: primary team synced as RecordGroup; reference issue belongs to it."""
        connector_id = linear_connector["connector_id"]
        primary_team_id = linear_connector["primary_team_id"]
        primary = linear_connector["teams"][0]

        rg = await graph_provider.get_record_group_by_external_id(connector_id, primary_team_id)
        assert rg is not None, f"Team {primary_team_id} missing as RecordGroup"

        expected_rg = LinearExpected.record_group(
            primary,
            connector_id=connector_id,
            organization_url_key=linear_connector.get("organization_url_key"),
        )
        rg_skip = frozenset({
            "created_at", "updated_at", "source_created_at", "source_updated_at",
            "web_url", "description",
        })
        await assert_graph_entity_with_edges(
            expected_rg, rg,
            entity="record_group",
            connector_id=connector_id,
            graph_provider=graph_provider,
            skip_compare=rg_skip,
        )

        ref_id = linear_connector.get("reference_issue_id")
        if ref_id:
            ref_record = await connector_assertions.assert_record_exists(connector_id, ref_id)
            assert ref_record.external_record_group_id == primary_team_id, (
                f"Reference issue should belong to team {primary_team_id}; "
                f"got {ref_record.external_record_group_id}"
            )
        logger.info("TC-LINEAR-003 passed: team %s validated as RecordGroup", primary_team_id)

    @pytest.mark.order(6)
    async def test_tc_linear_004_issue_properties(
        self,
        linear_connector: Dict[str, Any],
        linear_datasource: LinearDataSource,
        connector_assertions: ConnectorAssertions,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-LINEAR-004: reference issue has correct TICKET record properties."""
        ref_id = linear_connector.get("reference_issue_id")
        if not ref_id:
            pytest.skip("No reference issue discovered on primary — skipping")
        connector_id = linear_connector["connector_id"]
        primary_team_id = linear_connector["primary_team_id"]

        expected = RecordAssertion(
            external_record_id=ref_id,
            record_type=RecordType.TICKET.value,
            mime_type="application/blocks",
            external_record_group_id=primary_team_id,
        )
        record = await connector_assertions.assert_record_exists(connector_id, ref_id, expected)

        assert record.weburl is not None, "Issue should have weburl"
        assert "linear.app" in record.weburl, (
            f"weburl '{record.weburl}' should contain 'linear.app'"
        )
        assert record.source_created_at is not None

        record_edges = build_record_edge_expectations(record, connector_id)
        await assert_graph_edges(graph_provider, record_edges)

        logger.info("TC-LINEAR-004 passed: issue %s validated", ref_id)

    @pytest.mark.order(7)
    async def test_tc_linear_005_project_properties(
        self,
        linear_connector: Dict[str, Any],
        linear_datasource: LinearDataSource,
        connector_assertions: ConnectorAssertions,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-LINEAR-005: reference project as ProjectRecord (skip if none in team)."""
        ref_project_id = linear_connector.get("reference_project_id")
        if not ref_project_id:
            pytest.skip("No projects in primary team — skipping")

        connector_id = linear_connector["connector_id"]
        primary_team_id = linear_connector["primary_team_id"]

        expected = RecordAssertion(
            external_record_id=ref_project_id,
            record_type=RecordType.PROJECT.value,
            mime_type="application/blocks",
            external_record_group_id=primary_team_id,
        )
        record = await connector_assertions.assert_record_exists(
            connector_id, ref_project_id, expected,
        )

        assert record.weburl is not None, "Project should have weburl"
        assert "linear.app" in record.weburl, (
            f"weburl '{record.weburl}' should contain 'linear.app'"
        )
        assert record.source_created_at is not None

        logger.info("TC-LINEAR-005 passed: project %s validated", ref_project_id)

    @pytest.mark.order(10)
    async def test_tc_linear_006_link_record_properties(
        self,
        linear_connector: Dict[str, Any],
        linear_datasource: LinearDataSource,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-LINEAR-006: reference attachment as LinkRecord (skip if none)."""
        attachment_id = linear_connector.get("reference_attachment_id")
        if not attachment_id:
            pytest.skip("No attachments in filtered teams — skipping")

        connector_id = linear_connector["connector_id"]
        team_id = linear_connector.get("reference_attachment_team_id") or linear_connector["primary_team_id"]

        expected = await LinearExpected.link_record(
            attachment_id,
            connector_id=connector_id,
            datasource=linear_datasource,
            team_id=team_id,
        )
        parent_external_id = expected.parent_external_record_id
        parent = await graph_provider.get_record_by_external_id(connector_id, parent_external_id) if parent_external_id else None
        if parent:
            expected.parent_node_id = parent.id

        actual = await graph_provider.get_typed_record_by_external_id(connector_id, attachment_id)
        assert actual is not None, f"LINK record missing for attachment {attachment_id}"
        assert isinstance(actual, LinkRecord), f"Expected LinkRecord, got {type(actual).__name__}"

        await assert_graph_entity_with_edges(
            expected, actual,
            entity="link_record",
            connector_id=connector_id,
            graph_provider=graph_provider,
            skip_compare=frozenset({"created_at", "updated_at"}),
        )
        logger.info("TC-LINEAR-006 passed: attachment %s validated as LINK", attachment_id)

    @pytest.mark.order(11)
    async def test_tc_linear_007_webpage_record_properties(
        self,
        linear_connector: Dict[str, Any],
        linear_datasource: LinearDataSource,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-LINEAR-007: reference document as WebpageRecord (skip if none)."""
        document_id = linear_connector.get("reference_document_id")
        if not document_id:
            pytest.skip("No documents in filtered teams — skipping")

        connector_id = linear_connector["connector_id"]
        team_id = linear_connector.get("reference_document_team_id") or linear_connector["primary_team_id"]
        parent_type_str = linear_connector.get("reference_document_parent_type") or "TICKET"
        parent_type = (
            RecordType.PROJECT if parent_type_str == "PROJECT" else RecordType.TICKET
        )
        parent_external_id = linear_connector.get("reference_document_parent_id")

        parent = await graph_provider.get_record_by_external_id(connector_id, parent_external_id) if parent_external_id else None

        expected = await LinearExpected.webpage_record(
            document_id,
            connector_id=connector_id,
            datasource=linear_datasource,
            team_id=team_id,
            parent_external_id=parent_external_id,
            parent_record_type=parent_type,
            parent_node_id=parent.id if parent else None,
        )
        actual = await graph_provider.get_typed_record_by_external_id(connector_id, document_id)
        assert actual is not None, f"WEBPAGE record missing for document {document_id}"

        await assert_graph_entity_with_edges(
            expected, actual,
            entity="webpage_record",
            connector_id=connector_id,
            graph_provider=graph_provider,
            skip_compare=frozenset({"created_at", "updated_at"}),
        )
        logger.info("TC-LINEAR-007 passed: document %s validated as WEBPAGE", document_id)

    @pytest.mark.order(12)
    async def test_tc_linear_008_file_record_properties(
        self,
        linear_connector: Dict[str, Any],
        linear_datasource: LinearDataSource,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-LINEAR-008: reference markdown file as FileRecord (skip if none)."""
        file_url = linear_connector.get("reference_file_url")
        if not file_url:
            pytest.skip("No markdown files in filtered teams — skipping")

        connector_id = linear_connector["connector_id"]
        parent_type_str = linear_connector.get("reference_file_parent_type") or "TICKET"
        parent_type = (
            RecordType.PROJECT if parent_type_str == "PROJECT" else RecordType.TICKET
        )
        parent_external_id = linear_connector["reference_file_parent_id"]
        parent = await graph_provider.get_record_by_external_id(
            connector_id, parent_external_id,
        )
        assert parent is not None, (
            f"Parent record {parent_external_id} missing for FILE {file_url}"
        )

        size_in_bytes = 0
        try:
            fetched_size = await linear_datasource.get_file_size(file_url)
            if fetched_size is not None:
                size_in_bytes = fetched_size
        except Exception:
            pass

        expected = LinearExpected.file_record(
            file_url,
            linear_connector["reference_file_filename"],
            parent_external_id=parent_external_id,
            parent_record_type=parent_type,
            team_id=linear_connector["reference_file_team_id"],
            connector_id=connector_id,
            parent_weburl=linear_connector.get("reference_file_parent_weburl"),
            parent_created_at=linear_connector.get("reference_file_parent_created_at", 0),
            parent_updated_at=linear_connector.get("reference_file_parent_updated_at", 0),
            parent_node_id=parent.id,
            size_in_bytes=size_in_bytes,
        )
        actual = await graph_provider.get_typed_record_by_external_id(connector_id, file_url)
        assert actual is not None, f"FILE record missing for url {file_url}"

        await assert_graph_entity_with_edges(
            expected, actual,
            entity="file_record",
            connector_id=connector_id,
            graph_provider=graph_provider,
            skip_compare=frozenset({"created_at", "updated_at"}),
        )
        logger.info("TC-LINEAR-008 passed: file %s validated as FILE", file_url)


# =============================================================================
# TestLinearIndexing — graph ``indexing_status`` COMPLETED for reference issue
# =============================================================================


class TestLinearIndexing:
    """Indexing pipeline: reference TICKET reaches ``COMPLETED`` in graph."""

    @pytest.mark.order(13)
    async def test_tc_linear_idx_001_reference_issue_indexing_completed(
        self,
        linear_connector: Dict[str, Any],
        graph_provider: GraphProviderProtocol,
        pipeshub_client: PipeshubClient,
    ) -> None:
        """TC-LINEAR-IDX-001: reference issue reaches ``indexing_status == COMPLETED``."""
        external_id = linear_connector.get("reference_issue_id")
        if not external_id:
            pytest.skip("No reference issue discovered on primary — skipping")
        connector_id = linear_connector["connector_id"]
        rec = await wait_until_record_indexing_completed(
            graph_provider,
            connector_id,
            external_id,
            timeout=LINEAR_INDEXING_WAIT_SEC,
            description=f"TC-LINEAR-IDX-001 reference issue {external_id}",
            pipeshub_client=pipeshub_client,
        )
        assert rec.indexing_status == ProgressStatus.COMPLETED.value
        assert rec.virtual_record_id, (
            f"Issue should have virtual_record_id after indexing COMPLETED"
        )
        logger.info("TC-LINEAR-IDX-001 passed: %s indexing completed", external_id)


# =============================================================================
# TestLinearPlaceholders — parent stubs: minted, swept, promoted
# =============================================================================


class TestLinearPlaceholders:
    """Placeholder ancestor lifecycle driven by the ``modified`` sync filter."""

    @pytest.mark.order(14)
    async def test_tc_linear_ph_001_placeholder_sweep_and_promotion(
        self,
        linear_datasource: LinearDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-LINEAR-PH-001: out-of-window ancestors become stubs, get swept, then promote.

        Read-only against Linear — scope is driven purely by the ``modified`` filter.

        Runs on a **dedicated connector** whose very first sync is already narrowed.
        ``_handle_parent_record`` only mints a stub when no record exists for the parent,
        and a full sync deletes sync edges but never record nodes — so narrowing the
        module connector after it has synced the workspace would find the ancestors
        already present as real records and produce no placeholders at all.

        Phase 1 syncs with the window narrowed to the child issue. Its out-of-window
        ancestors are minted as placeholder stubs and then backfilled (but *not* promoted)
        by ``LinearConnector._sweep_placeholder_records``. Phase 2 widens the filter,
        which promotes them to real records.

        Only the *contiguous prefix* of ancestors below the cut becomes stubs. The first
        in-window ancestor syncs as a real record, and the sweep stops there
        (``connector.py`` boundary: ``not parent_record.is_placeholder``), so a chain that
        terminates in an in-scope ancestor is the expected shape — and asserted as such.

        Requires >= 2 stub ancestors: the stub minted for the immediate parent carries no
        parent pointer, so the grandparent only reaches the graph after the sweep reads the
        parent back from Linear and expands a second level. Depth 1 would not prove the loop ran.

        The cut is derived from the chain at test time rather than pinned to a constant:
        ``updatedAt`` is mutable, so a hard-coded epoch decays the moment anything touches
        the chain and strands the test until someone recomputes it by hand.
        """
        all_team_ids = [t.strip() for t in os.getenv("LINEAR_TEST_TEAM_IDS", "").split(",") if t.strip()]
        if not all_team_ids:
            pytest.skip("LINEAR_TEST_TEAM_IDS not set")

        child = await resolve_issue_by_identifier(linear_datasource, LINEAR_PH_CHILD_IDENTIFIER)
        assert child, f"TC-LINEAR-PH-001 setup: issue {LINEAR_PH_CHILD_IDENTIFIER!r} not found"
        child_id = child["id"]

        # Scope to the chain's own team rather than the primary one — stubs inherit the
        # child's team, and the chain need not live in team_ids[0].
        team_id = (child.get("team") or {}).get("id")
        if team_id not in all_team_ids:
            pytest.fail(
                f"TC-LINEAR-PH-001 setup: {LINEAR_PH_CHILD_IDENTIFIER} belongs to team "
                f"{team_id!r}, which is not in LINEAR_TEST_TEAM_IDS {all_team_ids}"
            )

        chain = await fetch_ancestor_chain(linear_datasource, child_id)
        if len(chain) < 2:
            pytest.fail(
                f"TC-LINEAR-PH-001 setup: {LINEAR_PH_CHILD_IDENTIFIER} has {len(chain)} "
                "ancestor(s); >= 2 required to exercise multi-level expansion — pick a "
                "deeper chain"
            )

        # Sit the cut on the newer of the two nearest ancestors: the connector emits
        # ``updatedAt: {gt: cut}``, so both fall outside the window and mint stubs, while
        # anything edited more recently stays in scope and bounds the sweep.
        cut = max(parse_linear_timestamp(a.get("updatedAt")) for a in chain[:2])
        child_updated_ms = parse_linear_timestamp(child.get("updatedAt"))
        if child_updated_ms <= cut:
            pytest.fail(
                f"TC-LINEAR-PH-001 setup: {LINEAR_PH_CHILD_IDENTIFIER} updatedAt="
                f"{child_updated_ms} is not newer than its two nearest ancestors "
                f"({cut}) — the child must sync while they do not. Touch "
                f"{LINEAR_PH_CHILD_IDENTIFIER} in Linear to bump its updatedAt."
            )

        # Walk down the chain collecting the out-of-window prefix. An ancestor exactly on
        # the cut is out of scope; the first one above it syncs for real and bounds the sweep.
        ancestors: list[str] = []
        boundary_id: str | None = None
        for ancestor in chain:
            if parse_linear_timestamp(ancestor.get("updatedAt")) > cut:
                boundary_id = ancestor["id"]
                break
            ancestors.append(ancestor["id"])

        stub_node_ids: Dict[str, str] = {}
        # ---- Phase 1: dedicated connector, narrowed on its very first sync ----
        narrowed = _team_filters(
            [team_id],
            modified={
                "type": "datetime",
                "operator": "is_after",
                "value": {"start": cut, "end": None},
            },
        )
        async with _dedicated_connector(
            pipeshub_client, graph_provider,
            name=f"linear-ph-{LINEAR_IT_RUN_ID}", filters=narrowed, min_records=1,
        ) as connector_id:
            synced_child = await graph_provider.get_typed_record_by_external_id(
                connector_id, child_id,
            )
            assert synced_child is not None, f"phase1: child {child_id} missing after sync"
            assert synced_child.is_placeholder is False, "phase1: child must sync as a real record"

            for depth, ancestor_id in enumerate(ancestors, start=1):
                stub = await graph_provider.get_typed_record_by_external_id(
                    connector_id, ancestor_id,
                )
                assert stub is not None, (
                    f"phase1: ancestor {ancestor_id} (depth {depth}) absent — "
                    "placeholder sweep did not expand this level"
                )
                assert stub.is_placeholder is True, (
                    f"phase1: ancestor {ancestor_id} is out of scope and must stay a stub"
                )
                stub_node_ids[ancestor_id] = stub.id

                expected_stub = await LinearExpected.placeholder_stub(
                    ancestor_id,
                    connector_id=connector_id,
                    datasource=linear_datasource,
                    team_id=team_id,
                )
                assert_graph_entity_matches(
                    expected_stub, stub,
                    entity="ticket_record",
                    skip_compare=frozenset({"created_at", "updated_at"}),
                )
                assert not stub.virtual_record_id, (
                    f"phase1: stub {ancestor_id} must not be indexed"
                )

            # The first in-window ancestor bounds the sweep: it syncs for real, so the BFS
            # must stop rather than re-stub it.
            if boundary_id:
                boundary = await graph_provider.get_typed_record_by_external_id(
                    connector_id, boundary_id,
                )
                assert boundary is not None, f"phase1: boundary ancestor {boundary_id} missing"
                assert boundary.is_placeholder is False, (
                    f"phase1: boundary ancestor {boundary_id} is inside the window and must "
                    "sync as a real record, not a stub"
                )

            # Scoped to TICKET: the sweep seeds only tickets (a Linear project has no parent
            # project), so a PROJECT stub from unrelated workspace data is not this test's business.
            ticket_stubs = [
                stub
                for stub in await graph_provider.get_placeholder_records(connector_id)
                if stub.record_type == RecordType.TICKET
            ]
            assert {s.external_record_id for s in ticket_stubs} == set(ancestors), (
                "phase1: TICKET placeholders must be exactly the out-of-window ancestors; "
                f"expected {sorted(ancestors)}, got {sorted(s.external_record_id for s in ticket_stubs)}"
            )
            unswept = [s.external_record_id for s in ticket_stubs if s.external_revision_id is None]
            assert not unswept, f"phase1: stubs never backfilled by the sweep: {unswept}"

            chain = [child_id] + ancestors + ([boundary_id] if boundary_id else [])
            for parent_id, kid_id in zip(chain[1:], chain):
                children = await graph_provider.get_record_outgoing_relations(
                    connector_id, parent_id, "PARENT_CHILD",
                )
                assert kid_id in children, (
                    f"phase1: PARENT_CHILD edge {parent_id} -> {kid_id} missing "
                    f"(outgoing children: {children})"
                )

            logger.info(
                "TC-LINEAR-PH-001 phase1 passed: %d ancestor stub(s) swept", len(ancestors),
            )

            # ---- Phase 2: drop the window; the resync promotes the stubs ----
            await apply_filter_full_sync(
                pipeshub_client, graph_provider, connector_id, _team_filters([team_id]),
            )

            for ancestor_id in ancestors:
                promoted = await graph_provider.get_typed_record_by_external_id(
                    connector_id, ancestor_id,
                )
                assert promoted is not None, f"phase2: ancestor {ancestor_id} missing after resync"
                assert promoted.is_placeholder is False, (
                    f"phase2: ancestor {ancestor_id} is back in scope and must be promoted"
                )
                assert promoted.id == stub_node_ids[ancestor_id], (
                    f"phase2: ancestor {ancestor_id} was replaced (node {promoted.id}) instead "
                    f"of promoted in place (node {stub_node_ids[ancestor_id]})"
                )
                promoted_revision = promoted.external_revision_id
                assert promoted_revision and not str(promoted_revision).startswith(
                    PLACEHOLDER_REVISION_PREFIX
                ), (
                    f"phase2: ancestor {ancestor_id} must carry the real issue revision, "
                    f"got {promoted_revision!r}"
                )

                expected = await LinearExpected.ticket_record(
                    ancestor_id, connector_id=connector_id, datasource=linear_datasource,
                )
                await assert_graph_entity_with_edges(
                    expected, promoted,
                    entity="ticket_record",
                    connector_id=connector_id,
                    graph_provider=graph_provider,
                    skip_compare=frozenset({"created_at", "updated_at"}),
                )

            indexed = await wait_until_record_indexing_completed(
                graph_provider,
                connector_id,
                ancestors[0],
                timeout=LINEAR_INDEXING_WAIT_SEC,
                description=f"TC-LINEAR-PH-001 promoted ancestor {ancestors[0]}",
                pipeshub_client=pipeshub_client,
            )
            assert indexed.virtual_record_id, "phase2: a promoted record must be indexed"

            logger.info("TC-LINEAR-PH-001 passed: %d ancestor(s) promoted", len(ancestors))


# =============================================================================
# TestLinearEdges — comprehensive edge inventory audit
# =============================================================================


class TestLinearEdges:
    """Edge audit after incremental tests."""

    @pytest.mark.order(20)
    async def test_tc_linear_edges_001_edge_inventory(
        self,
        linear_connector: Dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-LINEAR-EDGES-001: structural edge invariants at the end of the module.

        The mutation tests run on their own connectors, so this graph is still the fixture's
        single sync. Same split as TC-SYNC-001: the baseline compares owned records, the
        edge invariants compare the raw count.
        """
        connector_id = linear_connector["connector_id"]

        all_records = await graph_provider.count_records(connector_id)
        owned_records = await count_owned_records(
            graph_provider, connector_id, prefix=LINEAR_IT_ARTIFACT_PREFIX,
        )
        assert owned_records >= linear_connector["expected_total_records"], (
            f"owned records: graph={owned_records} (of {all_records}) "
            f"< expected={linear_connector['expected_total_records']}"
        )

        rg_edges = await graph_provider.count_record_group_edges(connector_id)
        assert rg_edges == all_records, (
            f"BELONGS_TO record->group {rg_edges} must equal records {all_records}"
        )

        pc = await graph_provider.count_parent_child_edges(connector_id)
        assert pc >= linear_connector["expected_parent_child_edges"], (
            f"PARENT_CHILD edges: {pc} < expected={linear_connector['expected_parent_child_edges']}"
        )

        inherit = await graph_provider.count_inherit_permissions_edges(connector_id)
        assert inherit == all_records, (
            f"INHERIT_PERMISSIONS {inherit} must equal records {all_records}"
        )

        app_edges = await graph_provider.count_app_record_group_edges(connector_id)
        rgs = await graph_provider.count_record_groups(connector_id)
        assert app_edges == rgs == linear_connector["expected_record_groups"]

        logger.info("TC-LINEAR-EDGES-001 passed")


# =============================================================================
# TestLinearPermissions — team privacy → ORG or GROUP permission
# =============================================================================


class TestLinearPermissions:
    """Team privacy-based permission validation on RecordGroups."""

    @pytest.mark.order(21)
    async def test_tc_linear_perm_001_team_privacy_permissions(
        self,
        linear_connector: Dict[str, Any],
        linear_datasource: LinearDataSource,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-LINEAR-PERM-001: verify PERMISSION edges to RecordGroups reflect team privacy.

        Public teams get an ORG-level PERMISSION edge.
        Private teams get a GROUP-level PERMISSION edge (external_id = team_id).
        """
        connector_id = linear_connector["connector_id"]
        teams = linear_connector["teams"]

        for team in teams:
            team_id = team["id"]
            is_private = team.get("private", False)

            rg = await graph_provider.get_record_group_by_external_id(connector_id, team_id)
            if rg is None:
                logger.warning("Team %s not found as RecordGroup — skipping", team_id)
                continue

            perm_count = await graph_provider.count_permission_edges_to_record_groups(
                connector_id, team_id,
            )
            assert perm_count >= 1, (
                f"Team {team_id} ({'private' if is_private else 'public'}) "
                f"should have at least 1 PERMISSION edge to its RecordGroup, got {perm_count}"
            )

        logger.info("TC-LINEAR-PERM-001 passed: %d teams checked", len(teams))

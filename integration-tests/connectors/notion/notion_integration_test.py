# pyright: ignore-file

"""
Notion Connector – Integration Tests (self-seeding, self-cleaning)
==================================================================

Every page, database, block, comment and attachment asserted on is created by the fixture under
a per-run root inside ``NOTION_TEST_ROOT_PAGE_ID`` and trashed at teardown. The only
pre-provisioned objects are that root page and ``NOTION_TEST_ROOT_DATABASE_ID``.

Two invariants this suite depends on — do not "fix" either without reading the connector:

1. **One IT run at a time per workspace.** ``_sync_objects_by_type`` searches workspace-wide with
   no scoping filter, so concurrent runs would see each other's content and break every count.
2. **Count assertions must use ``scoped=True``.** The connector only ever calls
   ``on_new_records`` — it has no delete path. A trashed page leaves scope when a full sync
   rebuilds ``BELONGS_TO``; its record document survives. Unscoped totals are meaningless here.

  order 1  TC-SYNC-001    — full sync baseline vs the seed manifest
  order 2  TC-USER-001    — users / USER_APP_RELATION vs live /v1/users
  order 3  TC-WS-001      — workspace RecordGroup
  order 4  TC-WS-002      — workspace READ permissions
  order 5  TC-CONN-001    — connection test (positive + bad-token negative)
  order 6  TC-PAGE-001    — page WebpageRecord properties + edges
  order 7  TC-PAGE-002    — nested page parentage
  order 8  TC-PAGE-003    — database row parentage (DATASOURCE parent)
  order 9  TC-DS-001      — data source properties
  order 10 TC-DS-002      — data source with a page parent
  order 11 TC-DS-003      — data source at workspace root (parent None)
  order 12 TC-FILE-001    — block attachments (file / pdf)
  order 13 TC-FILE-002    — image / embed / bookmark are NOT FileRecords
  order 14 TC-FILE-003    — comment attachment + per-comment filename dedup
  order 15 TC-TITLE-001   — title extraction (standard / Name / fallback)
  order 16 TC-STREAM-001  — page BlocksContainer snapshot + recursion
  order 17 TC-STREAM-002  — image base64 + SVG→PNG
  order 18 TC-STREAM-003  — comments, threads, authors, attachments
  order 19 TC-STREAM-004  — child references (page / database → data source)
  order 20 TC-STREAM-005  — data source table structure + rows
  order 21 TC-STREAM-006  — bookmark / embed become LINK blocks
  order 22 TC-STREAM-007  — file streaming round-trip (block + comment attachment)
  order 23 TC-INCR-*      — create / edit / rename / archive, data sources, attachments
  order 34 TC-INCR-CMT-*  — comments and comment attachments
  order 36 TC-DELTA-*     — delta sync + checkpoints
  order 39 TC-FILTER-*    — indexing filters (last: they force full syncs)
"""

import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator

import pytest
import pytest_asyncio

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.config.constants.arangodb import MimeTypes, ProgressStatus  # noqa: E402
from app.models.blocks import BlockSubType, BlockType, GroupSubType  # noqa: E402
from app.models.entities import RecordType  # noqa: E402
from helper.graph_provider import GraphProviderProtocol  # noqa: E402
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]  # noqa: E402
from validation.graph_entity_validator import (  # noqa: E402
    assert_graph_entity_matches,
    assert_graph_entity_with_edges,
    assert_user_app_edge,
)
from connectors.notion.constants import (  # noqa: E402
    BOOKMARK_URL,
    COMMENT_ATTACHMENT_FILENAME,
    COMMENT_ATTACHMENT_SAMPLE_PATH,
    DB_ROW_NAMED_TITLE,
    DEEP_PAGE_BLOCK_COUNT,
    EMBED_URL,
    NOTION_AUTH_TYPE,
    NOTION_CONNECTOR_TYPE,
    NOTION_SCOPE,
    TITLE_FALLBACK_TITLE,
    TITLE_PREFIX,
    TITLE_STANDARD_TITLE,
)
from connectors.notion.notion_block_utils import (  # noqa: E402
    DATA_SOURCE_SNAPSHOT_PATH,
    PAGE_SNAPSHOT_PATH,
    blocks_of_type,
    bootstrap_expected,
    groups_of_sub_type,
    iter_blocks,
    load_expected,
    normalize_blocks_container,
    parse_streamed_container,
)
from connectors.notion.notion_expected import (  # noqa: E402
    APP_METADATA_SKIP_COMPARE,
    RECORD_SKIP_COMPARE,
    NotionExpected,
)
from connectors.notion.notion_seed import (  # noqa: E402
    NotionSeed,
    foreign_run_object_ids,
    comment_attachment_external_id,
)
from connectors.notion.notion_source_helper import (  # noqa: E402
    NotionSourceHelper,
    file_upload_block,
    paragraph_block,
    rich_text,
)
from connectors.notion.notion_test_utils import (  # noqa: E402
    apply_indexing_filters,
    data_sources_sync_point_key,
    full_sync,
    incremental_sync,
    pages_sync_point_key,
    scoped_external_ids,
    wait_for_search_visibility,
    wait_past_checkpoint,
)

logger = logging.getLogger("notion-integration-test")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.notion,
    pytest.mark.asyncio(loop_scope="session"),
]


# =============================================================================
# helpers
# =============================================================================


async def _stream_container(
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
    connector_id: str,
    external_id: str,
) -> dict[str, Any]:
    """Stream a record's ``BlocksContainer`` and return it parsed."""
    record = await graph_provider.get_record_by_external_id(connector_id, external_id)
    assert record is not None, f"record {external_id} not synced — cannot stream"
    response = pipeshub_client.stream_record(record.id)
    assert response.headers.get("content-type", "").startswith("application/octet-stream"), (
        f"expected an octet-stream blocks body, got {response.headers.get('content-type')!r}"
    )
    return parse_streamed_container(response.content)


def _all_text(node: Any) -> str:
    """Flatten every string in a nested structure — used for 'does the payload mention X'."""
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        return " ".join(_all_text(v) for v in node.values())
    if isinstance(node, list):
        return " ".join(_all_text(v) for v in node)
    return ""


async def _scratch_page(
    helper: NotionSourceHelper, seed: NotionSeed, label: str, children=None
) -> dict[str, Any]:
    """Create a page under the run's scratch subtree."""
    return await helper.create_child_page(
        seed.scratch_page_id, f"{TITLE_PREFIX}{label}-{uuid.uuid4().hex[:6]}", children
    )


# =============================================================================
# TestNotionFullSync
# =============================================================================


class TestNotionFullSync:
    """Baseline sync: record inventory, users, workspace, connection."""

    @pytest.mark.order(1)
    async def test_tc_sync_001_full_sync_matches_seed(
        self,
        notion_connector: dict[str, Any],
        notion_seed: NotionSeed,
        graph_provider: GraphProviderProtocol,
        notion_source_helper: NotionSourceHelper,
    ) -> None:
        """TC-SYNC-001: the graph holds exactly what the seeder created.

        Covers Initial Full Sync, Pagination (pages / data sources), and Archived/Trashed Skip.
        The expectation comes from the manifest, not from re-running the connector's own search,
        so a sync that silently drops a page cannot also drop the expectation.
        """
        connector_id = notion_connector["connector_id"]

        graph_pages = await scoped_external_ids(
            graph_provider, connector_id, RecordType.WEBPAGE.value
        )
        graph_data_sources = await scoped_external_ids(
            graph_provider, connector_id, RecordType.DATASOURCE.value
        )
        graph_files = await scoped_external_ids(
            graph_provider, connector_id, RecordType.FILE.value
        )

        # The connector syncs the whole workspace, so a run happening at the same time on
        # another backend leg puts its fixture tree in this graph too. Drop those before
        # comparing: they are ours-by-title but not ours-by-id, and nothing about them says
        # anything about this run's sync. Ids we created stay in, so an archived page that
        # was wrongly synced still shows up as unexpected.
        foreign_pages, foreign_data_sources = await foreign_run_object_ids(
            notion_source_helper, notion_seed
        )
        if foreign_pages or foreign_data_sources:
            logger.warning(
                "TC-SYNC-001: ignoring %d page(s) and %d data source(s) from a concurrent "
                "run — this workspace is shared, so the two runs see each other's trees",
                len(foreign_pages),
                len(foreign_data_sources),
            )
            graph_pages -= foreign_pages
            graph_data_sources -= foreign_data_sources

        assert graph_pages == notion_seed.expected_page_ids, (
            f"page mismatch — missing from graph: "
            f"{sorted(notion_seed.expected_page_ids - graph_pages)}; "
            f"unexpected in graph: {sorted(graph_pages - notion_seed.expected_page_ids)}"
        )
        assert graph_data_sources == notion_seed.expected_data_source_ids, (
            f"data source mismatch — missing: "
            f"{sorted(notion_seed.expected_data_source_ids - graph_data_sources)}; "
            f"unexpected: {sorted(graph_data_sources - notion_seed.expected_data_source_ids)}"
        )
        # Unlike pages/data sources there is no cheap pre-seed baseline for files (they come
        # from block traversal, not Search), so an unexpected id here usually means the
        # permanent IT root page carries attachment blocks or comment attachments.
        # File ids come from block traversal, so a concurrent run's attachments cannot be
        # recognised by title the way its pages can. Only relax this when that run has
        # already been detected above, and name what was dropped so the signal is not lost.
        foreign_files: set[str] = set()
        if foreign_pages or foreign_data_sources:
            foreign_files = graph_files - notion_seed.expected_file_ids
            if foreign_files:
                logger.warning(
                    "TC-SYNC-001: ignoring %d file record(s) attributed to the concurrent "
                    "run: %s",
                    len(foreign_files),
                    sorted(foreign_files),
                )
                graph_files -= foreign_files

        assert graph_files == notion_seed.expected_file_ids, (
            f"file mismatch — missing: "
            f"{sorted(notion_seed.expected_file_ids - graph_files)}; "
            f"unexpected: {sorted(graph_files - notion_seed.expected_file_ids)} "
            "(unexpected ids usually mean NOTION_TEST_ROOT_PAGE_ID has attachment blocks or "
            "comment attachments of its own — the IT root page must hold no attachments)"
        )

        # Archived page / database were created then trashed — they must never have synced.
        # The database's *data source* is what the connector would record, and it is a
        # different id: asserting on archived_db_id compared a database id against a set of
        # data-source ids, which can never match and so never caught anything.
        assert notion_seed.archived_page_id not in graph_pages
        assert notion_seed.archived_data_source_id not in graph_data_sources

        # Pagination is only proven if the seed actually exceeds the connector's page_size of 20.
        assert len(graph_pages) > 20, (
            f"only {len(graph_pages)} pages in scope — Search pagination is not exercised"
        )
        assert len(graph_data_sources) > 20, (
            f"only {len(graph_data_sources)} data sources in scope — pagination not exercised"
        )

        # Structural invariants (graph self-consistency, no Notion dependency).
        total = await graph_provider.count_records(connector_id, scoped=True)
        # The three sets above had a concurrent run's objects removed; this count did not,
        # so drop the same objects here or the comparison measures the other run instead.
        # The edge invariants below deliberately keep the raw total: every record needs its
        # edges, whichever run created it.
        foreign_total = len(foreign_pages) + len(foreign_data_sources) + len(foreign_files)
        assert total - foreign_total == (
            len(graph_pages) + len(graph_data_sources) + len(graph_files)
        ), (
            f"scoped record total {total} (minus {foreign_total} from a concurrent run) "
            f"!= pages+data_sources+files (unexpected record types)"
        )
        rg_edges = await graph_provider.count_record_group_edges(connector_id)
        assert rg_edges == total, (
            f"every record needs one BELONGS_TO→RecordGroup ({rg_edges} != {total})"
        )
        inherit = await graph_provider.count_inherit_permissions_edges(connector_id)
        assert inherit == total, (
            f"every record needs one INHERIT_PERMISSIONS edge ({inherit} != {total})"
        )

        # App metadata document.
        graph_app = await graph_provider.get_app_metadata_by_connector_id(connector_id)
        assert graph_app is not None, f"apps document missing for connector {connector_id}"
        assert_graph_entity_matches(
            NotionExpected.app_metadata(
                connector_id=connector_id,
                connector_name=notion_connector["connector_name"],
            ),
            graph_app,
            entity="app_metadata",
            skip_compare=APP_METADATA_SKIP_COMPARE,
        )
        logger.info("TC-SYNC-001 passed: %d records in scope", total)

    @pytest.mark.order(2)
    async def test_tc_user_001_user_properties(
        self,
        notion_connector: dict[str, Any],
        notion_source_helper: NotionSourceHelper,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-USER-001: every person-with-email is synced; bots and emailless people are not.

        Notion has no user-creation API (SCIM/Enterprise only), so this is read-only against the
        workspace members. Pagination is asserted as *no drops* rather than a page count — a
        21-member fixture is not realistic.
        """
        connector_id = notion_connector["connector_id"]

        all_users = await notion_source_helper.list_all_users()
        expected_users = await notion_source_helper.person_users_with_email()
        assert expected_users, "IT workspace has no person users with a visible email"

        rel_count = await graph_provider.count_user_app_relation_edges(connector_id)
        assert rel_count == len(expected_users), (
            f"USER_APP_RELATION {rel_count} != Notion person-with-email users "
            f"{len(expected_users)}"
        )

        for user_data in expected_users:
            source_id = str(user_data["id"])
            graph_user = await graph_provider.get_user_by_source_id(source_id, connector_id)
            assert graph_user is not None, f"user {source_id} not synced"
            expected = NotionExpected.app_user(user_data, connector_id=connector_id)
            # Email is the identity the connector matches on. The display name is NOT asserted:
            # when a Notion user resolves to an existing platform user, the graph keeps the
            # platform's name rather than adopting Notion's.
            assert graph_user.email == expected.email, (
                f"user {source_id}: email {graph_user.email!r} != {expected.email!r}"
            )
            assert graph_user.full_name, f"user {source_id} synced without a name"
            await assert_user_app_edge(
                source_id, connector_id=connector_id, graph_provider=graph_provider
            )

        # Bots carry workspace info but must never become users.
        for user in all_users:
            if user.get("type") == "bot":
                assert await graph_provider.get_user_by_source_id(
                    str(user["id"]), connector_id
                ) is None, f"bot user {user['id']} must not be synced"

        # A person without a visible email is skipped (only assertable if one is provisioned).
        emailless = [
            u for u in all_users
            if u.get("type") == "person"
            and str(u["id"]) not in {str(e["id"]) for e in expected_users}
        ]
        for user in emailless:
            assert await graph_provider.get_user_by_source_id(
                str(user["id"]), connector_id
            ) is None, f"user {user['id']} has no email and must be skipped"
        if not emailless:
            logger.info(
                "TC-USER-001: no emailless member provisioned — that half of the case is inert"
            )
        logger.info("TC-USER-001 passed: %d users", rel_count)

    @pytest.mark.order(3)
    async def test_tc_ws_001_workspace_record_group(
        self,
        notion_connector: dict[str, Any],
        notion_seed: NotionSeed,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-WS-001: the workspace RecordGroup is created from the bot user's workspace info."""
        connector_id = notion_connector["connector_id"]

        actual = await graph_provider.get_record_group_by_external_id(
            connector_id, notion_seed.workspace_id
        )
        assert actual is not None, (
            f"workspace RecordGroup {notion_seed.workspace_id} missing"
        )
        expected = NotionExpected.workspace_record_group(
            workspace_id=notion_seed.workspace_id,
            workspace_name=notion_seed.workspace_name,
            connector_id=connector_id,
        )
        await assert_graph_entity_with_edges(
            expected, actual, entity="record_group",
            connector_id=connector_id, graph_provider=graph_provider,
            skip_compare=frozenset({"created_at", "updated_at", "web_url", "short_name"}),
        )

        # Notion has exactly one record group per connector: the workspace.
        group_count = await graph_provider.count_record_groups(connector_id, scoped=True)
        assert group_count == 1, f"expected exactly 1 workspace RecordGroup, got {group_count}"
        app_edges = await graph_provider.count_app_record_group_edges(connector_id)
        assert app_edges == 1, f"expected 1 app→RecordGroup edge, got {app_edges}"
        logger.info("TC-WS-001 passed: workspace %s", notion_seed.workspace_id)

    @pytest.mark.order(4)
    async def test_tc_ws_002_workspace_permissions(
        self,
        notion_connector: dict[str, Any],
        notion_seed: NotionSeed,
        notion_source_helper: NotionSourceHelper,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-WS-002: every synced user gets a READ permission edge to the workspace group."""
        connector_id = notion_connector["connector_id"]
        expected_users = await notion_source_helper.person_users_with_email()

        edges = await graph_provider.count_permission_edges_to_record_groups(
            connector_id, notion_seed.workspace_id
        )
        assert edges == len(expected_users), (
            f"workspace PERMISSION edges {edges} != synced users {len(expected_users)}"
        )
        logger.info("TC-WS-002 passed: %d workspace permission edges", edges)

    @pytest.mark.order(5)
    async def test_tc_conn_001_connection_test(
        self,
        notion_connector: dict[str, Any],
        pipeshub_client: PipeshubClient,
    ) -> None:
        """TC-CONN-001: a good token reaches active+IDLE; a bad one never activates.

        Enabling sync makes the backend run ``init()`` then ``test_connection_and_access()``,
        which calls ``retrieve_bot_user`` — so activation is the observable outcome of a passing
        connection test.
        """
        status = pipeshub_client.get_connector_status(notion_connector["connector_id"])
        assert status.get("isActive"), "connector with a valid token must be active"

        bad_connector_id = None
        try:
            instance = pipeshub_client.create_connector(
                connector_type=NOTION_CONNECTOR_TYPE,
                instance_name=f"notion-badtoken-{uuid.uuid4().hex[:8]}",
                scope=NOTION_SCOPE,
                config={"auth": {"authType": NOTION_AUTH_TYPE, "apiToken": "ntn_invalid_token"}},
                auth_type=NOTION_AUTH_TYPE,
            )
            bad_connector_id = instance.connector_id
            try:
                pipeshub_client.toggle_sync(bad_connector_id, enable=True)
            except Exception as e:  # noqa: BLE001 - the backend may reject the enable outright
                logger.info("TC-CONN-001: bad-token enable rejected as expected (%s)", e)
            bad_status = pipeshub_client.get_connector_status(bad_connector_id)
            assert not bad_status.get("isActive"), (
                "connector with an invalid token must not become active"
            )
        finally:
            if bad_connector_id:
                try:
                    pipeshub_client.delete_connector(bad_connector_id)
                except Exception as e:  # noqa: BLE001
                    logger.warning("TC-CONN-001 cleanup failed: %s", e)
        logger.info("TC-CONN-001 passed")


# =============================================================================
# TestNotionRecordProperties — read-only over the seeded Fixture subtree
# =============================================================================


class TestNotionRecordProperties:
    """Field-level and edge-level validation of the baseline sync output."""

    @pytest.mark.order(6)
    async def test_tc_page_001_page_properties(
        self,
        notion_connector: dict[str, Any],
        notion_seed: NotionSeed,
        notion_source_helper: NotionSourceHelper,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-PAGE-001: full WebpageRecord field + edge compare against live Notion."""
        connector_id = notion_connector["connector_id"]

        actual = await graph_provider.get_typed_record_by_external_id(
            connector_id, notion_seed.rich_page_id
        )
        assert actual is not None, f"typed WEBPAGE record missing for {notion_seed.rich_page_id}"

        page_data = await notion_source_helper.retrieve_page(notion_seed.rich_page_id)
        expected = NotionExpected.webpage_record(
            page_data, connector_id=connector_id, workspace_id=notion_seed.workspace_id
        )
        await assert_graph_entity_with_edges(
            expected, actual, entity="webpage_record",
            connector_id=connector_id, graph_provider=graph_provider,
            skip_compare=RECORD_SKIP_COMPARE,
        )
        assert actual.mime_type == MimeTypes.BLOCKS.value
        assert str(actual.external_record_group_id) == notion_seed.workspace_id
        logger.info("TC-PAGE-001 passed: %s", notion_seed.rich_page_id)

    @pytest.mark.order(7)
    async def test_tc_page_002_nested_page_parent(
        self,
        notion_connector: dict[str, Any],
        notion_seed: NotionSeed,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-PAGE-002: a page nested under another page points at its parent page."""
        connector_id = notion_connector["connector_id"]

        child = await graph_provider.get_record_by_external_id(
            connector_id, notion_seed.nested_child_id
        )
        assert child is not None, "nested child page not synced"
        assert str(child.parent_external_record_id) == notion_seed.nested_parent_id, (
            f"parent {child.parent_external_record_id!r} != {notion_seed.nested_parent_id!r}"
        )
        parent_external = await graph_provider.get_record_parent_external_id(
            connector_id, notion_seed.nested_child_id
        )
        assert str(parent_external) == notion_seed.nested_parent_id, (
            f"stored parent external id {parent_external!r} != {notion_seed.nested_parent_id!r}"
        )
        # And the hierarchy is materialised as an edge, parent → child.
        children = await graph_provider.get_record_outgoing_relations(
            connector_id, notion_seed.nested_parent_id, "PARENT_CHILD"
        )
        assert notion_seed.nested_child_id in children, (
            f"no PARENT_CHILD edge from {notion_seed.nested_parent_id} to the child; "
            f"parent's children are {children}"
        )
        logger.info("TC-PAGE-002 passed")

    @pytest.mark.order(8)
    async def test_tc_page_003_database_row_parent(
        self,
        notion_connector: dict[str, Any],
        notion_seed: NotionSeed,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-PAGE-003: a database row's parent is the DATA SOURCE, not the database.

        Under API version 2025-09-03 Notion reports a row's parent as ``data_source_id``, which
        the connector maps to ``RecordType.DATASOURCE``.
        """
        connector_id = notion_connector["connector_id"]

        row = await graph_provider.get_record_by_external_id(
            connector_id, notion_seed.db_row_named_id
        )
        assert row is not None, "database row page not synced"
        assert str(row.parent_external_record_id) == notion_seed.fixture_data_source_id, (
            f"row parent {row.parent_external_record_id!r} != data source "
            f"{notion_seed.fixture_data_source_id!r}"
        )
        assert row.record_name == DB_ROW_NAMED_TITLE, (
            f"row title {row.record_name!r} — the 'Name' title property was not picked up"
        )
        logger.info("TC-PAGE-003 passed")

    @pytest.mark.order(9)
    async def test_tc_ds_001_data_source_properties(
        self,
        notion_connector: dict[str, Any],
        notion_seed: NotionSeed,
        notion_source_helper: NotionSourceHelper,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-DS-001: full DATASOURCE record field + edge compare against live Notion."""
        connector_id = notion_connector["connector_id"]

        actual = await graph_provider.get_typed_record_by_external_id(
            connector_id, notion_seed.fixture_data_source_id
        )
        assert actual is not None, "typed DATASOURCE record missing"

        data = await notion_source_helper.retrieve_data_source(
            notion_seed.fixture_data_source_id
        )
        expected = NotionExpected.data_source_record(
            data, connector_id=connector_id, workspace_id=notion_seed.workspace_id,
            parent_external_id=notion_seed.fixture_page_id,
        )
        await assert_graph_entity_with_edges(
            expected, actual, entity="webpage_record",
            connector_id=connector_id, graph_provider=graph_provider,
            skip_compare=RECORD_SKIP_COMPARE,
        )
        assert actual.record_type == RecordType.DATASOURCE
        logger.info("TC-DS-001 passed")

    @pytest.mark.order(10)
    async def test_tc_ds_002_data_source_with_page_parent(
        self,
        notion_connector: dict[str, Any],
        notion_seed: NotionSeed,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-DS-002: a data source whose database sits under a page inherits that page parent."""
        connector_id = notion_connector["connector_id"]

        record = await graph_provider.get_record_by_external_id(
            connector_id, notion_seed.child_ref_data_source_id
        )
        assert record is not None, "child-ref data source not synced"
        assert str(record.parent_external_record_id) == notion_seed.rich_page_id, (
            f"data source parent {record.parent_external_record_id!r} != owning page "
            f"{notion_seed.rich_page_id!r}"
        )
        logger.info("TC-DS-002 passed")

    @pytest.mark.order(11)
    async def test_tc_ds_003_data_source_with_workspace_parent(
        self,
        notion_connector: dict[str, Any],
        notion_seed: NotionSeed,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-DS-003: a workspace-root database's data source has no parent record.

        Needs a database that sits at the workspace root, which an integration cannot create
        (``POST /v1/databases`` requires a page parent), so this skips unless one is shared.
        """
        connector_id = notion_connector["connector_id"]
        if not notion_seed.root_data_source_id:
            pytest.skip(
                "No workspace-root database shared with the integration. Share one (or set "
                "NOTION_TEST_ROOT_DATABASE_ID) to cover data sources with a workspace parent."
            )

        record = await graph_provider.get_record_by_external_id(
            connector_id, notion_seed.root_data_source_id
        )
        assert record is not None, (
            f"workspace-root data source {notion_seed.root_data_source_id} not synced"
        )
        assert record.parent_external_record_id is None, (
            f"expected no parent for a workspace-root data source, got "
            f"{record.parent_external_record_id!r}"
        )
        assert str(record.external_record_group_id) == notion_seed.workspace_id
        logger.info("TC-DS-003 passed")

    @pytest.mark.order(12)
    @pytest.mark.parametrize("block_type", ["file", "pdf"])
    async def test_tc_file_001_block_attachment_properties(
        self,
        block_type: str,
        notion_connector: dict[str, Any],
        notion_seed: NotionSeed,
        notion_source_helper: NotionSourceHelper,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-FILE-001: each downloadable block becomes a FileRecord parented to its page."""
        connector_id = notion_connector["connector_id"]
        block_id = notion_seed.file_block_ids.get(block_type)
        assert block_id, f"seed produced no {block_type} block"

        actual = await graph_provider.get_typed_record_by_external_id(connector_id, block_id)
        assert actual is not None, f"FileRecord missing for {block_type} block {block_id}"

        block_data = await notion_source_helper.retrieve_block(block_id)
        expected = NotionExpected.block_file_record(
            block_data, connector_id=connector_id, workspace_id=notion_seed.workspace_id,
            page_id=notion_seed.rich_page_id, page_url=notion_seed.rich_page_url,
        )
        await assert_graph_entity_with_edges(
            expected, actual, entity="file_record",
            connector_id=connector_id, graph_provider=graph_provider,
            # signed_url holds an expiring Notion token that rotates between fetches.
            skip_compare=RECORD_SKIP_COMPARE | frozenset({"signed_url"}),
            # A file hangs off its page as an ATTACHMENT, not a PARENT_CHILD.
            parent_relation_type="ATTACHMENT",
        )
        assert actual.is_file is True
        assert str(actual.parent_external_record_id) == notion_seed.rich_page_id
        # signed_url is deliberately NOT asserted: it lives only on to_kafka_record, never on
        # to_arango_record, because Notion's URLs expire in ~1h. TC-STREAM-007 covers the real
        # behaviour by streaming the bytes, which forces a fresh URL fetch.
        logger.info("TC-FILE-001[%s] passed", block_type)

    @pytest.mark.order(13)
    async def test_tc_file_002_non_downloadable_blocks_skipped(
        self,
        notion_connector: dict[str, Any],
        notion_seed: NotionSeed,
        connector_assertions: Any,
    ) -> None:
        """TC-FILE-002: images, external video embeds, bookmarks and embeds are NOT FileRecords.

        Images are filtered where attachment blocks are collected, not in the record transform —
        this is the test that pins that behaviour.
        """
        connector_id = notion_connector["connector_id"]
        assert notion_seed.skipped_block_ids, "seed captured no skip-case blocks"

        for label, block_id in notion_seed.skipped_block_ids.items():
            await connector_assertions.assert_record_not_exists(connector_id, block_id)
            logger.info("TC-FILE-002: %s block %s correctly absent", label, block_id)

    @pytest.mark.order(14)
    async def test_tc_file_003_comment_attachment(
        self,
        notion_connector: dict[str, Any],
        notion_seed: NotionSeed,
        notion_source_helper: NotionSourceHelper,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-FILE-003: comment attachments sync as ``ca_{comment_id}_{filename}``, deduped.

        The seeded comment carries the SAME filename twice, so the connector's per-comment
        normalized-filename dedup must collapse them into one record.
        """
        connector_id = notion_connector["connector_id"]

        actual = await graph_provider.get_typed_record_by_external_id(
            connector_id, notion_seed.comment_attachment_external_id
        )
        assert actual is not None, (
            f"comment attachment {notion_seed.comment_attachment_external_id} not synced"
        )

        comments = await notion_source_helper.list_comments(notion_seed.commented_page_id)
        source_comment = next(
            (c for c in comments if str(c["id"]) == notion_seed.attachment_comment_id), None
        )
        assert source_comment is not None, "attachment comment not readable from Notion"
        attachments = source_comment.get("attachments") or []
        assert len(attachments) >= 2, (
            f"expected 2 same-named attachments on the comment, got {len(attachments)} — "
            "the dedup assertion below would be vacuous"
        )

        expected = NotionExpected.comment_file_record(
            attachments[0], comment_id=notion_seed.attachment_comment_id,
            connector_id=connector_id, workspace_id=notion_seed.workspace_id,
            page_id=notion_seed.commented_page_id,
        )
        assert actual.record_name == expected.record_name
        assert actual.mime_type == expected.mime_type
        assert str(actual.parent_external_record_id) == notion_seed.commented_page_id
        assert actual.is_file is True

        # Dedup: exactly one FileRecord for this comment despite two attachments.
        comment_files = {
            fid for fid in await scoped_external_ids(
                graph_provider, connector_id, RecordType.FILE.value
            )
            if fid.startswith(f"ca_{notion_seed.attachment_comment_id}_")
        }
        assert len(comment_files) == 1, (
            f"expected 1 deduped comment attachment record, got {sorted(comment_files)}"
        )
        logger.info("TC-FILE-003 passed")

    @pytest.mark.order(15)
    async def test_tc_title_001_title_extraction(
        self,
        notion_connector: dict[str, Any],
        notion_seed: NotionSeed,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-TITLE-001: ``title`` / ``Name`` / fallback title-type property all resolve."""
        connector_id = notion_connector["connector_id"]

        cases = [
            ("standard title property", notion_seed.title_standard_id, TITLE_STANDARD_TITLE),
            ("Name property", notion_seed.db_row_named_id, DB_ROW_NAMED_TITLE),
            # FALLBACK_TITLE_PROPERTY is not title/Title/Name/name, so the connector must fall
            # through to "any title-type property".
            ("fallback title-type property", notion_seed.title_fallback_row_id,
             TITLE_FALLBACK_TITLE),
        ]
        for label, external_id, expected_title in cases:
            record = await graph_provider.get_record_by_external_id(connector_id, external_id)
            assert record is not None, f"{label}: page {external_id} not synced"
            assert record.record_name == expected_title, (
                f"{label}: record_name {record.record_name!r} != {expected_title!r}"
            )
        logger.info("TC-TITLE-001 passed")


# =============================================================================
# TestNotionStreaming
# =============================================================================


class TestNotionStreaming:
    """``stream_record`` output: blocks containers, comments, child refs, file bytes."""

    @pytest.mark.order(16)
    async def test_tc_stream_001_page_blocks_snapshot(
        self,
        notion_connector: dict[str, Any],
        notion_seed: NotionSeed,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-STREAM-001: page container matches the committed snapshot; nesting is preserved.

        Regenerate the snapshot with ``NOTION_BLOCKS_BOOTSTRAP=1``, hand-review, and commit.
        """
        connector_id = notion_connector["connector_id"]
        container = await _stream_container(
            pipeshub_client, graph_provider, connector_id, notion_seed.rich_page_id
        )

        # Wrapper groups preserve hierarchy for callout / quote (has_children=true).
        assert groups_of_sub_type(container, GroupSubType.CALLOUT.value), (
            "callout with children did not produce a wrapper BlockGroup"
        )
        assert groups_of_sub_type(container, GroupSubType.QUOTE.value), (
            "quote with children did not produce a wrapper BlockGroup"
        )
        # The synced-block reference must resolve to the ORIGINAL block's children.
        assert "Synced source content." in _all_text(container), (
            "synced_block reference did not pull in the original block's children"
        )

        actual = normalize_blocks_container(container)
        if os.getenv("NOTION_BLOCKS_BOOTSTRAP") == "1":
            bootstrap_expected(PAGE_SNAPSHOT_PATH, actual)
            pytest.skip(f"Bootstrapped {PAGE_SNAPSHOT_PATH} — hand-review and commit it")
        assert actual == load_expected(PAGE_SNAPSHOT_PATH), (
            "streamed page blocks differ from the committed snapshot"
        )
        logger.info("TC-STREAM-001 passed")

    @pytest.mark.order(16)
    async def test_tc_stream_001b_block_children_pagination(
        self,
        notion_connector: dict[str, Any],
        notion_seed: NotionSeed,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-STREAM-001b: a page with more blocks than one API page returns all of them."""
        connector_id = notion_connector["connector_id"]
        container = await _stream_container(
            pipeshub_client, graph_provider, connector_id, notion_seed.deep_page_id
        )
        text = _all_text(container)
        missing = [
            i for i in range(DEEP_PAGE_BLOCK_COUNT)
            if f"Deep block {i:03d}" not in text
        ]
        assert not missing, (
            f"{len(missing)} of {DEEP_PAGE_BLOCK_COUNT} blocks missing from the streamed page "
            f"(first few: {missing[:5]}) — block-children pagination dropped them"
        )
        logger.info("TC-STREAM-001b passed: all %d blocks streamed", DEEP_PAGE_BLOCK_COUNT)

    @pytest.mark.order(17)
    async def test_tc_stream_002_images_inlined_as_base64(
        self,
        notion_connector: dict[str, Any],
        notion_seed: NotionSeed,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-STREAM-002: image blocks carry base64 data URIs, and SVG is converted to PNG."""
        connector_id = notion_connector["connector_id"]
        container = await _stream_container(
            pipeshub_client, graph_provider, connector_id, notion_seed.rich_page_id
        )

        expected_images = 2 if notion_seed.has_svg_image else 1
        image_blocks = blocks_of_type(container, BlockType.IMAGE.value)
        assert len(image_blocks) >= expected_images, (
            f"expected {expected_images} image block(s), found {len(image_blocks)}"
        )

        uris = [((b.get("data") or {}).get("uri") or "") for b in image_blocks]
        data_uris = [u for u in uris if u.startswith("data:")]
        assert len(data_uris) >= expected_images, (
            f"images were not converted to base64 data URIs: {[u[:40] for u in uris]}"
        )

        if not notion_seed.has_svg_image:
            pytest.skip(
                "SVG→PNG not asserted: the shared sample-data repo has no .svg. Add one under "
                "sample-data/entities/files to activate this half of the case."
            )
        assert not any(u.startswith("data:image/svg") for u in data_uris), (
            "an SVG image was left as SVG — it must be converted to PNG"
        )
        assert any(u.startswith("data:image/png") for u in data_uris), (
            "no PNG data URI found; the SVG→PNG conversion did not run"
        )
        logger.info("TC-STREAM-002 passed: %d inlined images", len(data_uris))

    @pytest.mark.order(18)
    async def test_tc_stream_003_comments_threads_and_attachments(
        self,
        notion_connector: dict[str, Any],
        notion_seed: NotionSeed,
        notion_source_helper: NotionSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-STREAM-003: comments attach to blocks, thread into groups, and keep attachments."""
        connector_id = notion_connector["connector_id"]
        container = await _stream_container(
            pipeshub_client, graph_provider, connector_id, notion_seed.commented_page_id
        )
        text = _all_text(container)

        assert "Block level comment." in text, "block-level comment missing from the container"
        assert "Page level comment." in text, "page-level comment missing from the container"
        assert "Threaded reply." in text, "threaded reply missing from the container"

        # Page-level comments produce COMMENT_THREAD groups containing COMMENT groups.
        threads = groups_of_sub_type(container, GroupSubType.COMMENT_THREAD.value)
        assert threads, "page-level comments did not create a COMMENT_THREAD BlockGroup"
        comment_groups = groups_of_sub_type(container, GroupSubType.COMMENT.value)
        assert comment_groups, "no COMMENT BlockGroups created"

        # The block-level comment lands on its own block, not only in a page-level group.
        commented_blocks = [
            b for b in iter_blocks(container)
            if b.get("comments") and "Block level comment." in _all_text(b["comments"])
        ]
        assert commented_blocks, (
            "block-level comment was not attached to the block it was made on"
        )

        # Author resolution: comments created through the API are authored by the integration
        # bot, so the connector must resolve its display name rather than fall back to the
        # "User {id[:8]}" placeholder.
        bot = await notion_source_helper.retrieve_bot()
        bot_name = str(bot.get("name") or "")
        assert bot_name, "bot user has no name — cannot assert author resolution"
        assert bot_name in text, (
            f"comment author {bot_name!r} was not resolved in the streamed comments"
        )
        assert f"User {str(bot['id'])[:8]}" not in text, (
            "comment author fell back to the raw-id placeholder instead of resolving a name"
        )

        # The attachment comment carries a CommentAttachment.
        assert COMMENT_ATTACHMENT_FILENAME in text, (
            "comment attachment filename missing from BlockComment.attachments"
        )
        logger.info("TC-STREAM-003 passed: %d threads", len(threads))

    @pytest.mark.order(19)
    async def test_tc_stream_004_child_references(
        self,
        notion_connector: dict[str, Any],
        notion_seed: NotionSeed,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-STREAM-004: child_page / child_database blocks resolve to ChildRecords.

        A child database must resolve to its DATA SOURCE, not the database container.
        """
        connector_id = notion_connector["connector_id"]
        container = await _stream_container(
            pipeshub_client, graph_provider, connector_id, notion_seed.rich_page_id
        )

        child_blocks = [
            b for b in iter_blocks(container) if b.get("sub_type") == BlockSubType.CHILD_RECORD.value
        ]
        assert child_blocks, "no CHILD_RECORD blocks produced for child_page / child_database"

        # Resolved children live on ``table_row_metadata.children_records`` — the connector
        # reuses that slot for child_page / child_database references, not just table rows.
        child_ids: set[str] = set()
        for block in child_blocks:
            metadata = block.get("table_row_metadata") or {}
            for child in metadata.get("children_records") or []:
                if child.get("child_id"):
                    child_ids.add(str(child["child_id"]))

        page_record = await graph_provider.get_record_by_external_id(
            connector_id, notion_seed.child_ref_page_id
        )
        assert page_record is not None, "child reference page not synced"
        assert (
            notion_seed.child_ref_page_id in child_ids or page_record.id in child_ids
        ), f"child page not referenced; saw {sorted(child_ids)}"

        ds_record = await graph_provider.get_record_by_external_id(
            connector_id, notion_seed.child_ref_data_source_id
        )
        assert ds_record is not None, "child reference data source not synced"
        assert (
            notion_seed.child_ref_data_source_id in child_ids or ds_record.id in child_ids
        ), (
            "child_database resolved to the database container instead of its data source; "
            f"saw {sorted(child_ids)}"
        )
        assert notion_seed.child_ref_db_id not in child_ids, (
            "child_database resolved to the database id — it must resolve to the data source"
        )
        logger.info("TC-STREAM-004 passed: %d child references", len(child_ids))

    @pytest.mark.order(20)
    async def test_tc_stream_005_data_source_table(
        self,
        notion_connector: dict[str, Any],
        notion_seed: NotionSeed,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-STREAM-005: a data source streams as a TABLE with one row per database row."""
        connector_id = notion_connector["connector_id"]
        container = await _stream_container(
            pipeshub_client, graph_provider, connector_id, notion_seed.fixture_data_source_id
        )

        rows = blocks_of_type(container, BlockType.TABLE_ROW.value)
        assert rows, "data source streamed without TABLE_ROW blocks"
        text = _all_text(container)
        assert DB_ROW_NAMED_TITLE in text, "named row missing from the streamed table"
        assert "Tag" in text, "column definitions missing from the streamed table"

        # The row with a sub-page must carry its child in table_row_metadata.
        metadata_children = [
            child
            for row in rows
            for child in ((row.get("table_row_metadata") or {}).get("children_records") or [])
        ]
        assert metadata_children, (
            "no children_records on any TABLE_ROW — a row with a sub-page should populate them"
        )

        if os.getenv("NOTION_BLOCKS_BOOTSTRAP") == "1":
            bootstrap_expected(DATA_SOURCE_SNAPSHOT_PATH, normalize_blocks_container(container))
            pytest.skip(
                f"Bootstrapped {DATA_SOURCE_SNAPSHOT_PATH} — hand-review and commit it"
            )
        assert normalize_blocks_container(container) == load_expected(
            DATA_SOURCE_SNAPSHOT_PATH
        ), "streamed data source blocks differ from the committed snapshot"
        logger.info("TC-STREAM-005 passed: %d rows", len(rows))

    @pytest.mark.order(20)
    async def test_tc_stream_005b_data_source_row_pagination(
        self,
        notion_connector: dict[str, Any],
        notion_seed: NotionSeed,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-STREAM-005b: a data source with more rows than one API page returns all of them."""
        connector_id = notion_connector["connector_id"]
        container = await _stream_container(
            pipeshub_client, graph_provider, connector_id, notion_seed.bulk_data_source_id
        )
        rows = blocks_of_type(container, BlockType.TABLE_ROW.value)
        expected_ids = set(notion_seed.bulk_row_ids)
        expected = len(expected_ids)
        # Exactly the data rows, plus at most the optional header row. A bare >=
        # would also pass when pagination re-fetched a page and duplicated rows,
        # which is the failure this test exists to catch.
        assert expected <= len(rows) <= expected + 1, (
            f"streamed {len(rows)} TABLE_ROW blocks for {expected} database rows "
            f"(expected {expected} or {expected + 1} with a header) — "
            "row pagination dropped or duplicated some"
        )

        # The header row carries no source_id; only data rows map back to a seeded
        # page. Comparing identities catches a page silently swapped for a
        # duplicate of another, which the count alone cannot.
        streamed_ids = {
            row.get("source_id")
            for row in rows
            if not (row.get("table_row_metadata") or {}).get("is_header")
            and row.get("source_id")
        }
        assert streamed_ids == expected_ids, (
            f"streamed row ids do not match the seeded rows — "
            f"missing {sorted(expected_ids - streamed_ids)}, "
            f"unexpected {sorted(streamed_ids - expected_ids)}"
        )
        logger.info("TC-STREAM-005b passed: %d rows", len(rows))

    @pytest.mark.order(21)
    async def test_tc_stream_006_bookmark_and_embed_become_links(
        self,
        notion_connector: dict[str, Any],
        notion_seed: NotionSeed,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-STREAM-006: bookmark / embed / external video appear as LINK blocks."""
        connector_id = notion_connector["connector_id"]
        container = await _stream_container(
            pipeshub_client, graph_provider, connector_id, notion_seed.rich_page_id
        )

        link_blocks = [
            b for b in iter_blocks(container)
            if b.get("type") == BlockType.LINK.value
            or b.get("sub_type") == BlockSubType.LINK.value
        ]
        assert link_blocks, "no LINK blocks produced for bookmark / embed"
        link_text = _all_text(link_blocks)
        assert BOOKMARK_URL in link_text, f"bookmark url missing from LINK blocks: {link_text[:200]}"
        assert EMBED_URL in link_text, f"embed url missing from LINK blocks: {link_text[:200]}"
        logger.info("TC-STREAM-006 passed: %d LINK blocks", len(link_blocks))

    @pytest.mark.order(22)
    async def test_tc_stream_007_file_streaming_round_trip(
        self,
        notion_connector: dict[str, Any],
        notion_seed: NotionSeed,
        notion_source_helper: NotionSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-STREAM-007: streaming a FileRecord returns the uploaded bytes.

        Notion signed URLs expire in about an hour and the connector refetches a fresh one via
        ``retrieve_block`` / ``retrieve_comment`` on every stream, so a successful byte-for-byte
        round trip IS the signed-URL assertion — there is no separate signed-URL route.
        """
        connector_id = notion_connector["connector_id"]

        expected_bytes = notion_source_helper.sample_file_bytes(COMMENT_ATTACHMENT_SAMPLE_PATH)

        pdf_block_id = notion_seed.file_block_ids["pdf"]
        record = await graph_provider.get_record_by_external_id(connector_id, pdf_block_id)
        assert record is not None, "pdf FileRecord not synced"
        response = pipeshub_client.stream_record(record.id)
        assert response.content == expected_bytes, (
            "streamed block attachment bytes differ from the uploaded sample file"
        )
        # Notion pdf blocks carry no filename, so the connector falls back to "document.pdf";
        # assert against the record's own name rather than the uploaded file's.
        disposition = response.headers.get("content-disposition", "")
        assert record.record_name in disposition, (
            f"Content-Disposition {disposition!r} does not name the record "
            f"({record.record_name!r})"
        )

        comment_record = await graph_provider.get_record_by_external_id(
            connector_id, notion_seed.comment_attachment_external_id
        )
        assert comment_record is not None, "comment attachment FileRecord not synced"
        comment_response = pipeshub_client.stream_record(comment_record.id)
        assert comment_response.content == expected_bytes, (
            "streamed comment attachment bytes differ from the uploaded sample file"
        )
        logger.info("TC-STREAM-007 passed")


# =============================================================================
# TestNotionIncremental — mutations under Scratch/, self-cleaning
# =============================================================================


@pytest_asyncio.fixture(scope="class", loop_scope="session")
async def incr_additions(
    notion_connector: dict[str, Any],
    notion_seed: NotionSeed,
    notion_source_helper: NotionSourceHelper,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[dict[str, Any], None]:
    """One create-and-sync cycle shared by the purely *additive* incremental cases.

    TC-INCR-PAGE-001 / DS-001 / DS-004 / CMT-001 / CMT-002 each used to create its own
    object, wait out Notion's search index, and run its own incremental sync — five ~40s
    syncs and five index waits to answer five questions that never look at each other's
    objects. Creating all five up front and syncing once is safe here for two reasons that
    must keep holding:

    1. **The objects are disjoint.** Each case asserts on an id it alone created; none
       mutates anything another case reads.
    2. **No assertion is a global count.** Every one is "this record has these fields /
       edges / streamed text", so extra records in the same sync cannot change an outcome.

    Deliberately NOT extended to the mutation cases (PAGE-002/003, DS-002/003, PAGE-005,
    ATT-001/002) or to TestNotionDeltaSync. Those assert on a before→after transition, and
    TC-DELTA-001 asserts that an *untouched* record is not rewritten: batching unrelated
    writes into its sync window would leave it green while testing nothing.

    The cost of sharing is shared failure — if the sync itself breaks, all five report. That
    is the same trade the module-scoped ``notion_connector`` fixture already makes for the
    21 read-only cases, and the ids are logged below so the culprit stays identifiable.
    """
    connector_id = notion_connector["connector_id"]

    # Both checkpoints, because this batch creates pages *and* a data source; a write that
    # lands in the checkpoint's own minute is the bug TC-INCR-CMT-002 used to hit.
    await wait_past_checkpoint(graph_provider, connector_id, "page")
    await wait_past_checkpoint(graph_provider, connector_id, "data_source")

    created: dict[str, Any] = {}

    # TC-INCR-PAGE-001 — a plain new page.
    page = await _scratch_page(
        notion_source_helper, notion_seed, "IncrPage",
        [paragraph_block("Incremental page body.")],
    )
    created["page_id"] = str(page["id"])

    # TC-INCR-DS-001 — a new database under the scratch page.
    database = await notion_source_helper.create_database(
        notion_seed.scratch_page_id, f"{TITLE_PREFIX}IncrDB-{uuid.uuid4().hex[:6]}"
    )
    created["database_id"] = str(database["id"])
    created["data_source_id"] = await notion_source_helper.first_data_source_id(database)

    # TC-INCR-DS-004 — a new row in the seeded fixture data source.
    created["row_title"] = f"{TITLE_PREFIX}IncrRow-{uuid.uuid4().hex[:6]}"
    row = await notion_source_helper.create_row_page(
        notion_seed.fixture_data_source_id,
        {"Name": {"title": rich_text(created["row_title"])}},
    )
    created["row_id"] = str(row["id"])

    # TC-INCR-CMT-001 — page-level, block-level and threaded comments.
    comment_page = await _scratch_page(
        notion_source_helper, notion_seed, "IncrComment",
        [paragraph_block("Comment target paragraph.")],
    )
    created["comment_page_id"] = str(comment_page["id"])
    blocks = await notion_source_helper.list_block_children(created["comment_page_id"])
    target_block_id = str(blocks[0]["id"])
    await notion_source_helper.create_comment(
        page_id=created["comment_page_id"], text="Incremental page comment."
    )
    block_comment = await notion_source_helper.create_comment(
        block_id=target_block_id, text="Incremental block comment."
    )
    await notion_source_helper.create_comment(
        discussion_id=str(block_comment["discussion_id"]),
        text="Incremental threaded reply.",
    )

    # TC-INCR-CMT-002 — a comment carrying a file attachment.
    attach_page = await _scratch_page(notion_source_helper, notion_seed, "IncrCommentFile")
    created["attach_page_id"] = str(attach_page["id"])
    upload_id = await notion_source_helper.upload_sample_file(
        COMMENT_ATTACHMENT_SAMPLE_PATH, "application/pdf"
    )
    attach_comment = await notion_source_helper.create_comment(
        page_id=created["attach_page_id"],
        text="Incremental comment with attachment.",
        file_upload_ids=[upload_id],
    )
    created["attach_comment"] = attach_comment
    created["attach_comment_id"] = str(attach_comment["id"])

    logger.info(
        "INCR-ADD: page=%s ds=%s row=%s comments=%s attachment=%s",
        created["page_id"], created["data_source_id"], created["row_id"],
        created["comment_page_id"], created["attach_page_id"],
    )

    try:
        # The waits overlap in wall time — by the time the last object is created the first
        # is usually already indexed — which is where the saving over five cycles comes from.
        for object_type, external_id in (
            ("page", created["page_id"]),
            ("data_source", created["data_source_id"]),
            ("page", created["row_id"]),
            ("page", created["comment_page_id"]),
            ("page", created["attach_page_id"]),
        ):
            await wait_for_search_visibility(notion_source_helper, object_type, external_id)

        await incremental_sync(pipeshub_client, graph_provider, connector_id)
        yield created
    finally:
        await notion_source_helper.trash_database(created["database_id"])
        for page_id in (
            created["page_id"], created["row_id"],
            created["comment_page_id"], created["attach_page_id"],
        ):
            await notion_source_helper.trash_page(page_id)



class TestNotionIncremental:
    """Create / update / move / archive at the source, then re-sync and assert by external id."""

    @pytest.mark.order(23)
    async def test_tc_incr_page_001_page_added(
        self,
        notion_connector: dict[str, Any],
        notion_seed: NotionSeed,
        notion_source_helper: NotionSourceHelper,
        graph_provider: GraphProviderProtocol,
        incr_additions: dict[str, Any],
    ) -> None:
        """TC-INCR-PAGE-001: a newly created page is picked up with correct fields and edges."""
        connector_id = notion_connector["connector_id"]
        page_id = incr_additions["page_id"]

        actual = await graph_provider.get_typed_record_by_external_id(connector_id, page_id)
        assert actual is not None, f"new page {page_id} not synced"
        expected = NotionExpected.webpage_record(
            await notion_source_helper.retrieve_page(page_id),
            connector_id=connector_id, workspace_id=notion_seed.workspace_id,
        )
        await assert_graph_entity_with_edges(
            expected, actual, entity="webpage_record",
            connector_id=connector_id, graph_provider=graph_provider,
            skip_compare=RECORD_SKIP_COMPARE,
        )
        logger.info("TC-INCR-PAGE-001 passed: %s", page_id)

    @pytest.mark.order(24)
    async def test_tc_incr_page_002_content_updated(
        self,
        notion_connector: dict[str, Any],
        notion_seed: NotionSeed,
        notion_source_helper: NotionSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-INCR-PAGE-002: editing content bumps external_revision_id and the version."""
        connector_id = notion_connector["connector_id"]
        await wait_past_checkpoint(graph_provider, connector_id, "page")
        page = await _scratch_page(
            notion_source_helper, notion_seed, "IncrEdit", [paragraph_block("Original body.")]
        )
        page_id = str(page["id"])
        try:
            await wait_for_search_visibility(notion_source_helper, "page", page_id)
            await incremental_sync(pipeshub_client, graph_provider, connector_id)
            before = await graph_provider.get_record_by_external_id(connector_id, page_id)
            assert before is not None, "page not synced before the edit"

            await wait_past_checkpoint(graph_provider, connector_id, "page")
            await notion_source_helper.append_blocks(
                page_id, [paragraph_block("Appended after the first sync.")]
            )
            await wait_for_search_visibility(notion_source_helper, "page", page_id, after_edit=True)
            await incremental_sync(pipeshub_client, graph_provider, connector_id)

            after = await graph_provider.get_record_by_external_id(connector_id, page_id)
            assert after is not None, "page missing after the edit sync"
            assert after.external_revision_id != before.external_revision_id, (
                f"external_revision_id did not advance ({before.external_revision_id!r})"
            )
            live = await notion_source_helper.retrieve_page(page_id)
            assert str(after.external_revision_id) == str(live["last_edited_time"]), (
                "external_revision_id does not match Notion's last_edited_time"
            )
            assert after.source_updated_at > before.source_updated_at, (
                f"source_updated_at did not advance ({before.source_updated_at})"
            )
            # NB: `version` is not asserted — the Notion connector hardcodes version=1 on every
            # transform, so unlike Jira it never increments. Revision is the change signal here.
            logger.info("TC-INCR-PAGE-002 passed")
        finally:
            await notion_source_helper.trash_page(page_id)

    @pytest.mark.order(25)
    async def test_tc_incr_page_003_title_renamed(
        self,
        notion_connector: dict[str, Any],
        notion_seed: NotionSeed,
        notion_source_helper: NotionSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-INCR-PAGE-003: renaming a page updates record_name."""
        connector_id = notion_connector["connector_id"]
        await wait_past_checkpoint(graph_provider, connector_id, "page")
        page = await _scratch_page(notion_source_helper, notion_seed, "IncrRename")
        page_id = str(page["id"])
        new_title = f"{TITLE_PREFIX}Renamed-{uuid.uuid4().hex[:6]}"
        try:
            await wait_for_search_visibility(notion_source_helper, "page", page_id)
            await incremental_sync(pipeshub_client, graph_provider, connector_id)

            await wait_past_checkpoint(graph_provider, connector_id, "page")
            await notion_source_helper.rename_page(page_id, new_title)
            await wait_for_search_visibility(notion_source_helper, "page", page_id, after_edit=True)
            await incremental_sync(pipeshub_client, graph_provider, connector_id)

            record = await graph_provider.get_record_by_external_id(connector_id, page_id)
            assert record is not None, "renamed page missing"
            assert record.record_name == new_title, (
                f"record_name {record.record_name!r} != {new_title!r}"
            )
            logger.info("TC-INCR-PAGE-003 passed")
        finally:
            await notion_source_helper.trash_page(page_id)

    @pytest.mark.order(27)
    async def test_tc_incr_page_005_page_archived(
        self,
        notion_connector: dict[str, Any],
        notion_seed: NotionSeed,
        notion_source_helper: NotionSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-INCR-PAGE-005: an archived page leaves scope after a full sync.

        Asserted as *out of scope*, not deleted: the connector never removes records, so the
        document survives while its BELONGS_TO edge does not get rebuilt.
        """
        connector_id = notion_connector["connector_id"]
        await wait_past_checkpoint(graph_provider, connector_id, "page")
        page = await _scratch_page(notion_source_helper, notion_seed, "IncrArchive")
        page_id = str(page["id"])

        await wait_for_search_visibility(notion_source_helper, "page", page_id)
        await incremental_sync(pipeshub_client, graph_provider, connector_id)
        in_scope = await scoped_external_ids(
            graph_provider, connector_id, RecordType.WEBPAGE.value
        )
        assert page_id in in_scope, "page was not synced before archiving"

        await notion_source_helper.trash_page(page_id)
        await full_sync(pipeshub_client, graph_provider, connector_id)

        in_scope_after = await scoped_external_ids(
            graph_provider, connector_id, RecordType.WEBPAGE.value
        )
        assert page_id not in in_scope_after, (
            f"archived page {page_id} is still in scope after a full sync"
        )
        logger.info("TC-INCR-PAGE-005 passed")

    @pytest.mark.order(28)
    async def test_tc_incr_ds_001_data_source_added(
        self,
        notion_connector: dict[str, Any],
        notion_seed: NotionSeed,
        notion_source_helper: NotionSourceHelper,
        graph_provider: GraphProviderProtocol,
        incr_additions: dict[str, Any],
    ) -> None:
        """TC-INCR-DS-001: a new database syncs as a DATASOURCE record under its parent page."""
        connector_id = notion_connector["connector_id"]
        data_source_id = incr_additions["data_source_id"]

        record = await graph_provider.get_record_by_external_id(
            connector_id, data_source_id
        )
        assert record is not None, f"new data source {data_source_id} not synced"
        assert record.record_type == RecordType.DATASOURCE
        assert str(record.parent_external_record_id) == notion_seed.scratch_page_id
        logger.info("TC-INCR-DS-001 passed")

    @pytest.mark.order(29)
    async def test_tc_incr_ds_002_and_003_properties_and_schema(
        self,
        notion_connector: dict[str, Any],
        notion_seed: NotionSeed,
        notion_source_helper: NotionSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-INCR-DS-002/003: renaming and re-schemaing a data source update the record."""
        connector_id = notion_connector["connector_id"]
        await wait_past_checkpoint(graph_provider, connector_id, "data_source")
        database = await notion_source_helper.create_database(
            notion_seed.scratch_page_id, f"{TITLE_PREFIX}IncrDSEdit-{uuid.uuid4().hex[:6]}"
        )
        database_id = str(database["id"])
        data_source_id = await notion_source_helper.first_data_source_id(database)
        new_title = f"{TITLE_PREFIX}IncrDSRenamed-{uuid.uuid4().hex[:6]}"
        try:
            await wait_for_search_visibility(notion_source_helper, "data_source", data_source_id)
            await incremental_sync(pipeshub_client, graph_provider, connector_id)
            before = await graph_provider.get_record_by_external_id(connector_id, data_source_id)
            assert before is not None, "data source not synced before the edit"

            await wait_past_checkpoint(graph_provider, connector_id, "data_source")
            await notion_source_helper.rename_data_source(
                database_id, data_source_id, new_title
            )
            await wait_for_search_visibility(notion_source_helper, "data_source", data_source_id, after_edit=True)
            await incremental_sync(pipeshub_client, graph_provider, connector_id)

            renamed = await graph_provider.get_record_by_external_id(
                connector_id, data_source_id
            )
            assert renamed is not None and renamed.record_name == new_title, (
                f"data source name {renamed.record_name if renamed else None!r} != {new_title!r}"
            )
            assert renamed.external_revision_id != before.external_revision_id, (
                "external_revision_id did not advance after the rename"
            )

            # Schema change: adding a column is another last_edited_time bump.
            await wait_past_checkpoint(graph_provider, connector_id, "data_source")
            await notion_source_helper.add_data_source_column(
                database_id, data_source_id, "ExtraColumn"
            )
            await wait_for_search_visibility(notion_source_helper, "data_source", data_source_id, after_edit=True)
            await incremental_sync(pipeshub_client, graph_provider, connector_id)
            reschemaed = await graph_provider.get_record_by_external_id(
                connector_id, data_source_id
            )
            assert reschemaed is not None, "data source missing after the schema change"
            assert reschemaed.external_revision_id != renamed.external_revision_id, (
                "external_revision_id did not advance after the schema change"
            )
            assert str(reschemaed.parent_external_record_id) == notion_seed.scratch_page_id, (
                f"data source parent lost after update: "
                f"{reschemaed.parent_external_record_id!r} != {notion_seed.scratch_page_id!r}"
            )
            logger.info("TC-INCR-DS-002/003 passed")
        finally:
            await notion_source_helper.trash_database(database_id)

    @pytest.mark.order(30)
    async def test_tc_incr_ds_004_database_row_added(
        self,
        notion_connector: dict[str, Any],
        notion_seed: NotionSeed,
        notion_source_helper: NotionSourceHelper,
        graph_provider: GraphProviderProtocol,
        incr_additions: dict[str, Any],
    ) -> None:
        """TC-INCR-DS-004: a new database row syncs as a page parented to the data source."""
        connector_id = notion_connector["connector_id"]
        row_id = incr_additions["row_id"]
        row_title = incr_additions["row_title"]

        record = await graph_provider.get_record_by_external_id(connector_id, row_id)
        assert record is not None, f"new database row {row_id} not synced"
        assert record.record_type == RecordType.WEBPAGE
        assert record.record_name == row_title
        assert str(record.parent_external_record_id) == notion_seed.fixture_data_source_id
        logger.info("TC-INCR-DS-004 passed")

    @pytest.mark.order(32)
    async def test_tc_incr_att_001_and_002_attachment_added_and_removed(
        self,
        notion_connector: dict[str, Any],
        notion_seed: NotionSeed,
        notion_source_helper: NotionSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-INCR-ATT-001/002: adding a file block creates a FileRecord; deleting it drops scope."""
        connector_id = notion_connector["connector_id"]
        await wait_past_checkpoint(graph_provider, connector_id, "page")
        page = await _scratch_page(notion_source_helper, notion_seed, "IncrAttach")
        page_id = str(page["id"])
        try:
            upload_id = await notion_source_helper.upload_sample_file(
                COMMENT_ATTACHMENT_SAMPLE_PATH, "application/pdf"
            )
            await wait_past_checkpoint(graph_provider, connector_id, "page")
            created = await notion_source_helper.append_blocks(
                page_id,
                [file_upload_block("pdf", upload_id, COMMENT_ATTACHMENT_FILENAME)],
            )
            block_id = str(created[0]["id"])
            await wait_for_search_visibility(notion_source_helper, "page", page_id, after_edit=True)
            await incremental_sync(pipeshub_client, graph_provider, connector_id)
            record = await graph_provider.get_record_by_external_id(connector_id, block_id)
            assert record is not None, f"added attachment {block_id} not synced"
            assert str(record.parent_external_record_id) == page_id
            assert record.record_type == RecordType.FILE

            # Removal: the block is gone at source, so a full sync drops it from scope.
            await notion_source_helper.delete_block(block_id)
            await full_sync(pipeshub_client, graph_provider, connector_id)
            in_scope = await scoped_external_ids(
                graph_provider, connector_id, RecordType.FILE.value
            )
            assert block_id not in in_scope, (
                f"deleted attachment {block_id} is still in scope after a full sync"
            )
            logger.info("TC-INCR-ATT-001/002 passed")
        finally:
            await notion_source_helper.trash_page(page_id)

    @pytest.mark.order(34)
    async def test_tc_incr_cmt_001_comments_added(
        self,
        notion_connector: dict[str, Any],
        notion_seed: NotionSeed,
        notion_source_helper: NotionSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
        incr_additions: dict[str, Any],
    ) -> None:
        """TC-INCR-CMT-001: page-level, block-level and threaded comments all reach the stream."""
        connector_id = notion_connector["connector_id"]
        page_id = incr_additions["comment_page_id"]

        container = await _stream_container(
            pipeshub_client, graph_provider, connector_id, page_id
        )
        text = _all_text(container)
        for expected in (
            "Incremental page comment.",
            "Incremental block comment.",
            "Incremental threaded reply.",
        ):
            assert expected in text, f"{expected!r} missing from the streamed page"

        threads = groups_of_sub_type(container, GroupSubType.COMMENT_THREAD.value)
        assert threads, "page-level comment produced no COMMENT_THREAD group"
        logger.info("TC-INCR-CMT-001 passed")

    @pytest.mark.order(35)
    async def test_tc_incr_cmt_002_comment_with_attachment(
        self,
        notion_connector: dict[str, Any],
        notion_seed: NotionSeed,
        notion_source_helper: NotionSourceHelper,
        graph_provider: GraphProviderProtocol,
        incr_additions: dict[str, Any],
    ) -> None:
        """TC-INCR-CMT-002: a comment attachment syncs as ``ca_{comment_id}_{filename}``."""
        connector_id = notion_connector["connector_id"]
        page_id = incr_additions["attach_page_id"]

        # Derive the id from the attachment Notion stored — the connector takes the
        # filename from the signed URL path, not from the upload name.
        external_id = comment_attachment_external_id(
            incr_additions["attach_comment"], incr_additions["attach_comment_id"]
        )
        record = await graph_provider.get_record_by_external_id(connector_id, external_id)
        assert record is not None, f"comment attachment {external_id} not synced"
        assert str(record.parent_external_record_id) == page_id
        assert record.mime_type == MimeTypes.PDF.value or record.record_name.endswith(".pdf")
        logger.info("TC-INCR-CMT-002 passed")


# =============================================================================
# TestNotionDeltaSync
# =============================================================================


class TestNotionDeltaSync:
    """Time-based delta sync and the per-object-type checkpoints that drive it."""

    @pytest.mark.order(36)
    async def test_tc_delta_001_only_newer_records_processed(
        self,
        notion_connector: dict[str, Any],
        notion_seed: NotionSeed,
        notion_source_helper: NotionSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-DELTA-001: an untouched page is not rewritten while an edited one is reprocessed.

        The connector sorts descending by ``last_edited_time`` and stops at the first record
        strictly below the checkpoint. The observable consequence is that an unmodified page's record
        is left alone — neither its version nor its graph ``updated_at`` moves — while the
        edited page picks up the new revision. If the early stop regresses, every page is
        re-enumerated and rewritten on each run.
        """
        connector_id = notion_connector["connector_id"]
        untouched_id = notion_seed.title_standard_id
        await wait_past_checkpoint(graph_provider, connector_id, "page")
        page = await _scratch_page(notion_source_helper, notion_seed, "DeltaEdit")
        page_id = str(page["id"])
        try:
            await wait_for_search_visibility(notion_source_helper, "page", page_id)
            await incremental_sync(pipeshub_client, graph_provider, connector_id)
            untouched_before = await graph_provider.get_record_by_external_id(
                connector_id, untouched_id
            )
            assert untouched_before is not None, "control page not synced"

            await wait_past_checkpoint(graph_provider, connector_id, "page")
            await notion_source_helper.append_blocks(
                page_id, [paragraph_block("Delta edit body.")]
            )
            await wait_for_search_visibility(notion_source_helper, "page", page_id, after_edit=True)
            await incremental_sync(pipeshub_client, graph_provider, connector_id)

            edited = await graph_provider.get_record_by_external_id(connector_id, page_id)
            assert edited is not None, "edited page missing"
            live = await notion_source_helper.retrieve_page(page_id)
            assert str(edited.external_revision_id) == str(live["last_edited_time"]), (
                "the edited page was not reprocessed"
            )

            untouched_after = await graph_provider.get_record_by_external_id(
                connector_id, untouched_id
            )
            assert untouched_after is not None
            assert untouched_after.version == untouched_before.version, (
                f"untouched page was reprocessed: version "
                f"{untouched_before.version} → {untouched_after.version}"
            )
            assert untouched_after.updated_at == untouched_before.updated_at, (
                f"untouched page was rewritten: updated_at "
                f"{untouched_before.updated_at} → {untouched_after.updated_at}"
            )
            logger.info("TC-DELTA-001 passed")
        finally:
            await notion_source_helper.trash_page(page_id)

    @pytest.mark.order(37)
    async def test_tc_delta_002_checkpoint_advances(
        self,
        notion_connector: dict[str, Any],
        notion_seed: NotionSeed,
        notion_source_helper: NotionSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-DELTA-002: the pages checkpoint advances to the newest last_edited_time seen."""
        connector_id = notion_connector["connector_id"]
        key = pages_sync_point_key()

        before = await graph_provider.get_sync_point(connector_id, key)
        assert before is not None, f"pages sync point {key} missing"
        before_time = before.get("last_sync_time")
        assert before_time, "pages sync point has no last_sync_time"

        await wait_past_checkpoint(graph_provider, connector_id, "page")
        page = await _scratch_page(notion_source_helper, notion_seed, "DeltaCheckpoint")
        page_id = str(page["id"])
        try:
            await wait_for_search_visibility(notion_source_helper, "page", page_id)
            await incremental_sync(pipeshub_client, graph_provider, connector_id)

            after = await graph_provider.get_sync_point(connector_id, key)
            assert after is not None, "pages sync point disappeared"
            after_time = after.get("last_sync_time")
            assert after_time > before_time, (
                f"checkpoint did not advance: {before_time} → {after_time}"
            )
            live = await notion_source_helper.retrieve_page(page_id)
            assert after_time >= str(live["last_edited_time"]), (
                f"checkpoint {after_time} is older than the newest page edit "
                f"{live['last_edited_time']}"
            )
            logger.info("TC-DELTA-002 passed: %s → %s", before_time, after_time)
        finally:
            await notion_source_helper.trash_page(page_id)

    @pytest.mark.order(38)
    async def test_tc_delta_003_first_sync_checkpoints_initialized(
        self,
        notion_connector: dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-DELTA-003: both per-object-type checkpoints exist after the first sync."""
        connector_id = notion_connector["connector_id"]

        for label, key in (
            ("pages", pages_sync_point_key()),
            ("data sources", data_sources_sync_point_key()),
        ):
            sync_point = await graph_provider.get_sync_point(connector_id, key)
            assert sync_point is not None, f"{label} sync point {key} was never written"
            assert sync_point.get("last_sync_time"), (
                f"{label} sync point has no last_sync_time"
            )
        logger.info("TC-DELTA-003 passed")



# =============================================================================
# TestNotionFilters — last; each forces a full sync
# =============================================================================


class TestNotionFilters:
    """Indexing filters mark records AUTO_INDEX_OFF instead of dropping them.

    Asserted against records created *while the filter is set*, not against the whole graph.
    ``indexing_status`` on an already-synced record belongs to the indexing pipeline, not to
    the connector: ``DataSourceEntitiesProcessor`` resets a COMPLETED record to NOT_STARTED on
    re-sync, so a pre-existing record's status says nothing about the filter. What the filter
    actually controls is the status a record is *written with*.
    """

    @staticmethod
    async def _indexing_status(
        graph_provider: GraphProviderProtocol, connector_id: str, external_id: str
    ) -> str:
        record = await graph_provider.get_record_by_external_id(connector_id, external_id)
        assert record is not None, f"record {external_id} not synced"
        return str(record.indexing_status)

    @pytest.mark.order(39)
    async def test_tc_filter_001_disable_page_indexing(
        self,
        notion_connector: dict[str, Any],
        notion_seed: NotionSeed,
        notion_source_helper: NotionSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-FILTER-001: with pages=false a new page still syncs, but AUTO_INDEX_OFF."""
        connector_id = notion_connector["connector_id"]
        await apply_indexing_filters(
            pipeshub_client, graph_provider, connector_id,
            pages=False, databases=True, files=True,
        )

        await wait_past_checkpoint(graph_provider, connector_id, "page")
        page = await _scratch_page(notion_source_helper, notion_seed, "FilterPageOff")
        page_id = str(page["id"])
        await wait_past_checkpoint(graph_provider, connector_id, "data_source")
        database = await notion_source_helper.create_database(
            notion_seed.scratch_page_id, f"{TITLE_PREFIX}FilterPagesOffDB-{uuid.uuid4().hex[:6]}"
        )
        database_id = str(database["id"])
        data_source_id = await notion_source_helper.first_data_source_id(database)
        try:
            await wait_for_search_visibility(notion_source_helper, "page", page_id)
            await wait_for_search_visibility(notion_source_helper, "data_source", data_source_id)
            await incremental_sync(pipeshub_client, graph_provider, connector_id)

            page_status = await self._indexing_status(graph_provider, connector_id, page_id)
            assert page_status == ProgressStatus.AUTO_INDEX_OFF.value, (
                f"page synced with indexing_status {page_status!r}, expected AUTO_INDEX_OFF"
            )
            # The filter must be type-scoped: data sources are unaffected.
            ds_status = await self._indexing_status(
                graph_provider, connector_id, data_source_id
            )
            assert ds_status != ProgressStatus.AUTO_INDEX_OFF.value, (
                "the pages filter also switched off data sources"
            )
            logger.info("TC-FILTER-001 passed")
        finally:
            await notion_source_helper.trash_database(database_id)
            await notion_source_helper.trash_page(page_id)

    @pytest.mark.order(40)
    async def test_tc_filter_002_disable_database_indexing(
        self,
        notion_connector: dict[str, Any],
        notion_seed: NotionSeed,
        notion_source_helper: NotionSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-FILTER-002: databases=false marks a new data source AUTO_INDEX_OFF, pages not."""
        connector_id = notion_connector["connector_id"]
        await apply_indexing_filters(
            pipeshub_client, graph_provider, connector_id,
            pages=True, databases=False, files=True,
        )

        await wait_past_checkpoint(graph_provider, connector_id, "data_source")
        database = await notion_source_helper.create_database(
            notion_seed.scratch_page_id, f"{TITLE_PREFIX}FilterDbOff-{uuid.uuid4().hex[:6]}"
        )
        database_id = str(database["id"])
        data_source_id = await notion_source_helper.first_data_source_id(database)
        await wait_past_checkpoint(graph_provider, connector_id, "page")
        page = await _scratch_page(notion_source_helper, notion_seed, "FilterDbOffPage")
        page_id = str(page["id"])
        try:
            await wait_for_search_visibility(notion_source_helper, "data_source", data_source_id)
            await wait_for_search_visibility(notion_source_helper, "page", page_id)
            await incremental_sync(pipeshub_client, graph_provider, connector_id)

            ds_status = await self._indexing_status(
                graph_provider, connector_id, data_source_id
            )
            assert ds_status == ProgressStatus.AUTO_INDEX_OFF.value, (
                f"data source synced with {ds_status!r}, expected AUTO_INDEX_OFF"
            )
            page_status = await self._indexing_status(graph_provider, connector_id, page_id)
            assert page_status != ProgressStatus.AUTO_INDEX_OFF.value, (
                "the databases filter also switched off pages"
            )
            logger.info("TC-FILTER-002 passed")
        finally:
            await notion_source_helper.trash_database(database_id)
            await notion_source_helper.trash_page(page_id)

    @pytest.mark.order(41)
    async def test_tc_filter_003_disable_file_indexing(
        self,
        notion_connector: dict[str, Any],
        notion_seed: NotionSeed,
        notion_source_helper: NotionSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-FILTER-003: files=false covers BOTH block attachments and comment attachments."""
        connector_id = notion_connector["connector_id"]
        await apply_indexing_filters(
            pipeshub_client, graph_provider, connector_id,
            pages=True, databases=True, files=False,
        )

        await wait_past_checkpoint(graph_provider, connector_id, "page")
        page = await _scratch_page(notion_source_helper, notion_seed, "FilterFilesOff")
        page_id = str(page["id"])
        try:
            block_upload = await notion_source_helper.upload_sample_file(
                COMMENT_ATTACHMENT_SAMPLE_PATH, "application/pdf"
            )
            created = await notion_source_helper.append_blocks(
                page_id,
                [file_upload_block("pdf", block_upload, COMMENT_ATTACHMENT_FILENAME)],
            )
            block_id = str(created[0]["id"])

            comment_upload = await notion_source_helper.upload_sample_file(
                COMMENT_ATTACHMENT_SAMPLE_PATH, "application/pdf"
            )
            comment = await notion_source_helper.create_comment(
                page_id=page_id, text="Filter test attachment.",
                file_upload_ids=[comment_upload],
            )
            comment_external_id = comment_attachment_external_id(comment, str(comment["id"]))

            await wait_for_search_visibility(notion_source_helper, "page", page_id)
            await incremental_sync(pipeshub_client, graph_provider, connector_id)

            block_status = await self._indexing_status(graph_provider, connector_id, block_id)
            assert block_status == ProgressStatus.AUTO_INDEX_OFF.value, (
                f"block attachment synced with {block_status!r}, expected AUTO_INDEX_OFF"
            )
            comment_status = await self._indexing_status(
                graph_provider, connector_id, comment_external_id
            )
            assert comment_status == ProgressStatus.AUTO_INDEX_OFF.value, (
                f"comment attachment synced with {comment_status!r}, expected AUTO_INDEX_OFF"
            )
            # Pages stay indexable — the files filter is type-scoped.
            page_status = await self._indexing_status(graph_provider, connector_id, page_id)
            assert page_status != ProgressStatus.AUTO_INDEX_OFF.value, (
                "the files filter also switched off pages"
            )
            logger.info("TC-FILTER-003 passed")
        finally:
            await notion_source_helper.trash_page(page_id)

    @pytest.mark.order(42)
    async def test_tc_filter_004_restore_all_indexing(
        self,
        notion_connector: dict[str, Any],
        notion_seed: NotionSeed,
        notion_source_helper: NotionSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-FILTER-004: re-enabling every filter puts new records back in the index queue."""
        connector_id = notion_connector["connector_id"]
        await apply_indexing_filters(
            pipeshub_client, graph_provider, connector_id,
            pages=True, databases=True, files=True,
        )

        await wait_past_checkpoint(graph_provider, connector_id, "page")
        page = await _scratch_page(notion_source_helper, notion_seed, "FilterRestored")
        page_id = str(page["id"])
        try:
            await wait_for_search_visibility(notion_source_helper, "page", page_id)
            await incremental_sync(pipeshub_client, graph_provider, connector_id)

            status = await self._indexing_status(graph_provider, connector_id, page_id)
            assert status != ProgressStatus.AUTO_INDEX_OFF.value, (
                f"page created after re-enabling is still {status!r}"
            )
            logger.info("TC-FILTER-004 passed")
        finally:
            await notion_source_helper.trash_page(page_id)

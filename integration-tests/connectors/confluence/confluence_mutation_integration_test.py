# pyright: ignore-file

"""
Confluence incremental sync: pages created, updated, renamed and moved at the source.

Every page these tests touch is one they created, and all of them are deleted
when the module finishes, so the shared test space is left as it was found.
Each step waits for Confluence's v1 search to show the change before syncing,
because the connector reads through that search and it lags the write API.
"""

import asyncio
import logging
import sys
import uuid
from pathlib import Path
from typing import Any, Dict

import pytest
import pytest_asyncio

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.models.entities import RecordType  # type: ignore[import-not-found]  # noqa: E402
from app.sources.external.confluence.confluence import (  # type: ignore[import-not-found]  # noqa: E402
    ConfluenceDataSource,
)
from helper.assertions import ConnectorAssertions, RecordAssertion  # noqa: E402
from connectors.confluence.confluence_v1_test_utils import (  # noqa: E402
    assert_confluence_pages_match_graph_records,
    count_confluence_space_pages_v1_search,
    wait_for_space_page_count_at_least,
    get_confluence_page_version_number_v1,
    wait_until_confluence_condition,
    check_page_in_v1_search_bool,
    check_version_equals_bool,
    check_page_title_bool,
    check_ancestors_contain_bool,
    check_multiple_pages_in_search_bool,
)
from helper.graph_provider import GraphProviderProtocol  # noqa: E402
from helper.graph_provider_utils import wait_for_sync_completion  # noqa: E402
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]  # noqa: E402

logger = logging.getLogger("confluence-mutation")

CREATED_PAGES = "created_page_ids"


def _remember_created_page(state: Dict[str, Any], page_id: str) -> None:
    state.setdefault(CREATED_PAGES, []).append(page_id)


def _page_from_earlier_step(state: Dict[str, Any], key: str, step: str) -> str:
    """A page an earlier test in this module created; never a page of the shared space."""
    page_id = state.get(key)
    if not page_id:
        pytest.fail(
            f"{step} did not leave a page for this test to change, so it has nothing "
            "of its own to work on. Its failure above is the one to look at."
        )
    return str(page_id)


@pytest_asyncio.fixture(scope="module", loop_scope="session", autouse=True)
async def _delete_created_pages(
    confluence_connector: Dict[str, Any],
    confluence_datasource: ConfluenceDataSource,
):
    yield
    # Creation order puts a moved child before the parent it was moved under.
    left_behind = []
    for page_id in confluence_connector.get(CREATED_PAGES, []):
        try:
            resp = await confluence_datasource.delete_page(int(page_id))
            if resp.status not in (200, 204, 404):
                left_behind.append(f"{page_id} (HTTP {resp.status})")
        except Exception as exc:  # noqa: BLE001 - try every page, then report
            left_behind.append(f"{page_id} ({exc})")
    assert not left_behind, (
        "These pages created by the Confluence incremental tests could not be "
        f"deleted and are still in the shared test space: {left_behind}"
    )


@pytest.mark.integration
@pytest.mark.confluence
@pytest.mark.asyncio(loop_scope="session")
class TestConfluenceIncrementalSync:
    """Incremental sync tests for Confluence (TC-CF-024, TC-CF-026)."""

    @pytest.mark.order(11)
    async def test_tc_cf_024_page_added(
        self,
        confluence_connector: Dict[str, Any],
        confluence_datasource: ConfluenceDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        """TC-CF-024: Create new page, verify it appears in graph with correct properties."""
        connector_id = confluence_connector["connector_id"]
        space_id = confluence_connector["space_id"]
        space_key = confluence_connector["space_key"]
        before_count = await graph_provider.count_records(connector_id)

        title = f"TC-CF-024 Test Page {uuid.uuid4().hex[:8]}"
        content = f"<p>Test content for TC-CF-024 at {uuid.uuid4().hex}</p>"

        resp = await confluence_datasource.create_page(
            root_level=True,
            body={
                "spaceId": space_id,
                "status": "current",
                "title": title,
                "body": {
                    "representation": "storage",
                    "value": content,
                },
            },
        )
        page_data = resp.json()
        page_id = str(page_data["id"])
        _remember_created_page(confluence_connector, page_id)

        await wait_until_confluence_condition(
            check_fn=lambda: check_page_in_v1_search_bool(
                confluence_datasource, space_key, page_id
            ),
            description=f"TC-CF-024: page {page_id} visible in v1 content/search",
        )
        pipeshub_client.toggle_sync(connector_id, enable=False)
        pipeshub_client.toggle_sync(connector_id, enable=True)

        await wait_for_sync_completion(
            pipeshub_client,
            graph_provider,
            connector_id,
            min_records=before_count + 1,
            timeout=180,
        )

        expected = RecordAssertion(
            external_record_id=page_id,
            record_type=RecordType.CONFLUENCE_PAGE.value,
            record_name=title,
            mime_type="text/html",
            external_record_group_id=space_id,
        )

        record = await connector_assertions.assert_record_exists(
            connector_id, page_id, expected
        )

        assert record.weburl is not None
        assert record.source_created_at is not None

        confluence_connector["tc_cf_024_page_id"] = page_id
        logger.info("TC-CF-024: Page added and verified successfully")

    @pytest.mark.order(12)
    async def test_tc_cf_026_page_content_updated(
        self,
        confluence_connector: Dict[str, Any],
        confluence_datasource: ConfluenceDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        """TC-CF-026: Update page content, verify version is incremented."""
        connector_id = confluence_connector["connector_id"]
        page_id = int(_page_from_earlier_step(confluence_connector, "tc_cf_024_page_id", "TC-CF-024"))

        page_resp = await confluence_datasource.get_page_by_id(page_id, body_format="storage")
        page_data = page_resp.json()
        old_version = page_data["version"]["number"]

        record_before = await graph_provider.get_record_by_external_id(connector_id, str(page_id))
        assert record_before is not None, f"Page {page_id} not found in graph"
        old_revision_id = record_before.external_revision_id

        new_content = f"<p>Updated content at {uuid.uuid4().hex}</p>"
        await confluence_datasource.update_page(
            id=page_id,
            body={
                "id": str(page_id),
                "status": "current",
                "title": page_data["title"],
                "body": {
                    "representation": "storage",
                    "value": new_content,
                },
                "version": {
                    "number": old_version + 1,
                },
            },
        )

        expected_version = old_version + 1
        await wait_until_confluence_condition(
            check_fn=lambda: check_version_equals_bool(
                confluence_datasource, str(page_id), expected_version
            ),
            description=f"TC-CF-026: page {page_id} version == {expected_version}",
        )
        pipeshub_client.toggle_sync(connector_id, enable=False)
        pipeshub_client.toggle_sync(connector_id, enable=True)

        await wait_for_sync_completion(
            pipeshub_client,
            graph_provider,
            connector_id,
            timeout=180,
        )

        record_after = await connector_assertions.assert_record_updated(
            connector_id,
            str(page_id),
            old_revision_id,
        )

        new_revision_id = record_after.external_revision_id
        v1_after_sync = await get_confluence_page_version_number_v1(
            confluence_datasource, str(page_id)
        )
        assert str(new_revision_id) == str(v1_after_sync), (
            f"TC-CF-026: graph external_revision_id={new_revision_id!r} should match "
            f"Confluence v1 version.number={v1_after_sync}"
        )

        logger.info(
            "TC-CF-026: Page version updated from %s to %s",
            old_revision_id,
            new_revision_id,
        )


@pytest.mark.integration
@pytest.mark.confluence
@pytest.mark.asyncio(loop_scope="session")
class TestConfluenceReindexMutation:
    """Reindex tests that require Confluence content updates (TC-CF-047)."""

    @pytest.mark.order(15)
    async def test_tc_cf_047_reindex_updated_page(
        self,
        confluence_connector: Dict[str, Any],
        confluence_datasource: ConfluenceDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-CF-047: Reindex updated page - DB should update with new version."""
        connector_id = confluence_connector["connector_id"]
        page_id = int(_page_from_earlier_step(confluence_connector, "incr_page_id", "TC-INCR-001"))

        record_before = await graph_provider.get_record_by_external_id(
            connector_id, str(page_id)
        )
        assert record_before is not None, f"Page {page_id} not found"
        record_key = record_before.id
        version_before = record_before.external_revision_id

        page_resp = await confluence_datasource.get_page_by_id(page_id, body_format="storage")
        page_data = page_resp.json()

        new_content = f"<p>Reindex test update at {uuid.uuid4().hex}</p>"
        await confluence_datasource.update_page(
            id=page_id,
            body={
                "id": str(page_id),
                "status": "current",
                "title": page_data["title"],
                "body": {
                    "representation": "storage",
                    "value": new_content,
                },
                "version": {
                    "number": page_data["version"]["number"] + 1,
                },
            },
        )

        expected_v1_version = page_data["version"]["number"] + 1

        await wait_until_confluence_condition(
            check_fn=lambda: check_version_equals_bool(
                confluence_datasource, str(page_id), expected_v1_version
            ),
            description=f"TC-CF-047: page {page_id} version == {expected_v1_version}",
        )
        v1_version_after_update = expected_v1_version
        result = pipeshub_client.reindex_record(record_key)
        assert result.get("success") or result.get("status") == "success"
        await asyncio.sleep(30)

        v1_version_now = await get_confluence_page_version_number_v1(
            confluence_datasource, str(page_id)
        )
        assert v1_version_now == v1_version_after_update, (
            f"TC-CF-047: v1 version.number changed after reindex "
            f"({v1_version_after_update} -> {v1_version_now}); unexpected."
        )

        record_after = await graph_provider.get_record_by_external_id(
            connector_id, str(page_id)
        )
        assert record_after is not None, f"Page {page_id} not found in graph after reindex"
        graph_rev = record_after.external_revision_id or record_after.version
        assert str(graph_rev) == str(v1_version_now), (
            f"TC-CF-047: Graph external_revision_id/version={graph_rev!r} should match "
            f"Confluence v1 version.number={v1_version_now}. graph_before={version_before!r}."
        )
        assert str(graph_rev) != str(version_before), (
            f"TC-CF-047: Expected graph revision to change from {version_before!r}; "
            f"still {graph_rev!r} while v1 API reports version.number={v1_version_now}."
        )

        logger.info(
            "TC-CF-047: Reindex updated page - graph revision %s matches v1 version.number %s "
            "(was %s)",
            graph_rev,
            v1_version_now,
            version_before,
        )


@pytest.mark.integration
@pytest.mark.confluence
@pytest.mark.asyncio(loop_scope="session")
class TestConfluenceConnectorMutation:
    """Lifecycle mutation tests (TC-INCR-001, TC-UPDATE-001, TC-RENAME-001, TC-MOVE-001)."""

    @pytest.mark.order(7)
    async def test_tc_incr_001_incremental_sync_new_pages(
        self,
        confluence_connector: Dict[str, Any],
        confluence_datasource: ConfluenceDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-INCR-001: Create new pages, verify they appear in graph."""
        connector_id = confluence_connector["connector_id"]
        space_id = confluence_connector["space_id"]
        space_key = confluence_connector["space_key"]
        before_count = await graph_provider.count_records(connector_id)
        api_before = await count_confluence_space_pages_v1_search(
            confluence_datasource, space_key
        )

        title_1 = f"Integration Test Page Alpha {uuid.uuid4().hex[:8]}"
        title_2 = f"Integration Test Page Beta {uuid.uuid4().hex[:8]}"

        resp_1 = await confluence_datasource.create_page(
            root_level=True,
            body={
                "spaceId": space_id,
                "status": "current",
                "title": title_1,
                "body": {
                    "representation": "storage",
                    "value": "<p>This is test content for incremental sync testing.</p>",
                },
            },
        )
        new_page_1 = resp_1.json()
        _remember_created_page(confluence_connector, str(new_page_1["id"]))

        resp_2 = await confluence_datasource.create_page(
            root_level=True,
            body={
                "spaceId": space_id,
                "status": "current",
                "title": title_2,
                "body": {
                    "representation": "storage",
                    "value": "<p>Another test page for incremental sync.</p>",
                },
            },
        )
        new_page_2 = resp_2.json()
        _remember_created_page(confluence_connector, str(new_page_2["id"]))

        page_id_1 = str(new_page_1["id"])
        page_id_2 = str(new_page_2["id"])

        await wait_until_confluence_condition(
            check_fn=lambda: check_multiple_pages_in_search_bool(
                confluence_datasource, space_key, [page_id_1, page_id_2]
            ),
            description=f"TC-INCR-001: both new pages in v1 search for {space_key}",
        )
        api_after_create = await wait_for_space_page_count_at_least(
            confluence_datasource, space_key, api_before + 2
        )
        # At least: another run may be writing to the same space.
        assert api_after_create >= api_before + 2, (
            f"Confluence v1 page count should increase by 2; before={api_before}, "
            f"after_create={api_after_create}"
        )
        pipeshub_client.toggle_sync(connector_id, enable=False)
        pipeshub_client.toggle_sync(connector_id, enable=True)

        await wait_for_sync_completion(
            pipeshub_client,
            graph_provider,
            connector_id,
            timeout=180,
        )

        await assert_confluence_pages_match_graph_records(
            confluence_datasource,
            graph_provider,
            connector_id,
            space_key,
            phase="TC-INCR-001 after incremental sync",
        )

        after_count = await graph_provider.count_records(connector_id)

        confluence_connector["incr_page_id"] = str(new_page_1["id"])
        confluence_connector["incr_page_title"] = new_page_1["title"]
        logger.info(
            "TC-INCR-001 passed: %d -> %d records (v1 pages %d -> %d)",
            before_count,
            after_count,
            api_before,
            api_after_create,
        )

    @pytest.mark.order(8)
    async def test_tc_update_001_content_change_detection(
        self,
        confluence_connector: Dict[str, Any],
        confluence_datasource: ConfluenceDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-UPDATE-001: Update page content, verify record is updated."""
        connector_id = confluence_connector["connector_id"]
        page_id = int(_page_from_earlier_step(confluence_connector, "incr_page_id", "TC-INCR-001"))
        before_count = await graph_provider.count_records(connector_id)

        page_resp = await confluence_datasource.get_page_by_id(page_id, body_format="storage")
        page_data = page_resp.json()

        new_content = f"<p>Updated content at {uuid.uuid4().hex}</p>"
        await confluence_datasource.update_page(
            id=page_id,
            body={
                "id": str(page_id),
                "status": "current",
                "title": page_data["title"],
                "body": {
                    "representation": "storage",
                    "value": new_content,
                },
                "version": {
                    "number": page_data["version"]["number"] + 1,
                },
            },
        )

        expected_version = page_data["version"]["number"] + 1
        await wait_until_confluence_condition(
            check_fn=lambda: check_version_equals_bool(
                confluence_datasource, str(page_id), expected_version
            ),
            description=f"TC-UPDATE-001: page {page_id} version == {expected_version}",
        )
        pipeshub_client.toggle_sync(connector_id, enable=False)
        pipeshub_client.toggle_sync(connector_id, enable=True)

        after_count = await wait_for_sync_completion(
            pipeshub_client,
            graph_provider,
            connector_id,
            timeout=120,
        )
        assert after_count == before_count, (
            f"Record count should be stable after update; before={before_count}, after={after_count}"
        )

    @pytest.mark.order(9)
    async def test_tc_rename_001_rename_detection(
        self,
        confluence_connector: Dict[str, Any],
        confluence_datasource: ConfluenceDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-RENAME-001: Rename page, verify old title gone and new title present."""
        connector_id = confluence_connector["connector_id"]
        page_id = int(_page_from_earlier_step(confluence_connector, "incr_page_id", "TC-INCR-001"))
        old_title = confluence_connector["incr_page_title"]
        before_count = await graph_provider.count_records(connector_id)

        new_title = f"Renamed-{old_title}"

        await confluence_datasource.update_page_title(
            id=page_id,
            body={
                "status": "current",
                "title": new_title,
            },
        )

        await wait_until_confluence_condition(
            check_fn=lambda: check_page_title_bool(
                confluence_datasource, str(page_id), new_title
            ),
            description=f"TC-RENAME-001: page {page_id} title == '{new_title}'",
        )
        pipeshub_client.toggle_sync(connector_id, enable=False)
        pipeshub_client.toggle_sync(connector_id, enable=True)

        after_count = await wait_for_sync_completion(
            pipeshub_client,
            graph_provider,
            connector_id,
            timeout=120,
        )

        await graph_provider.assert_record_paths_or_names_contain(connector_id, [new_title])
        await graph_provider.assert_record_not_exists(connector_id, old_title)
        assert after_count == before_count, (
            f"Record count should be stable after rename; before={before_count}, after={after_count}"
        )

        confluence_connector["renamed_page_id"] = str(page_id)

    @pytest.mark.order(10)
    async def test_tc_move_001_move_detection(
        self,
        confluence_connector: Dict[str, Any],
        confluence_datasource: ConfluenceDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-MOVE-001: Move page under new parent, verify hierarchy change."""
        connector_id = confluence_connector["connector_id"]
        space_id = confluence_connector["space_id"]
        page_id = _page_from_earlier_step(confluence_connector, "renamed_page_id", "TC-RENAME-001")
        before_count = await graph_provider.count_records(connector_id)

        parent_title = f"Parent Page {uuid.uuid4().hex[:8]}"
        parent_resp = await confluence_datasource.create_page(
            root_level=True,
            body={
                "spaceId": space_id,
                "status": "current",
                "title": parent_title,
                "body": {
                    "representation": "storage",
                    "value": "<p>This is a parent page.</p>",
                },
            },
        )
        parent_page = parent_resp.json()
        _remember_created_page(confluence_connector, str(parent_page["id"]))

        parent_id_str = str(parent_page["id"])
        await wait_until_confluence_condition(
            check_fn=lambda: check_page_in_v1_search_bool(
                confluence_datasource, confluence_connector["space_key"], parent_id_str
            ),
            description=f"TC-MOVE-001: parent page {parent_id_str} in v1 search",
        )
        pipeshub_client.toggle_sync(connector_id, enable=False)
        pipeshub_client.toggle_sync(connector_id, enable=True)

        after_parent_count = await wait_for_sync_completion(
            pipeshub_client,
            graph_provider,
            connector_id,
            min_records=before_count + 1,
            timeout=120,
        )
        assert after_parent_count == before_count + 1, (
            f"Expected 1 new record (parent page); before={before_count}, after={after_parent_count}"
        )

        await confluence_datasource.move_page(page_id, str(parent_page["id"]))

        await wait_until_confluence_condition(
            check_fn=lambda: check_ancestors_contain_bool(
                confluence_datasource, str(page_id), parent_id_str
            ),
            description=f"TC-MOVE-001: page {page_id} ancestors contain {parent_id_str}",
        )
        pipeshub_client.toggle_sync(connector_id, enable=False)
        pipeshub_client.toggle_sync(connector_id, enable=True)

        final_count = await wait_for_sync_completion(
            pipeshub_client,
            graph_provider,
            connector_id,
            timeout=120,
        )
        assert final_count == after_parent_count, (
            f"Record count should be stable after move; before_move={after_parent_count}, "
            f"after_move={final_count}"
        )

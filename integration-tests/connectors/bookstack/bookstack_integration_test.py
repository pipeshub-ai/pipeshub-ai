# pyright: ignore-file

"""
BookStack Connector – Integration Tests
=======================================

Tests receive a fully set-up connector via the ``bookstack_connector`` fixture
(defined in conftest.py), which creates the run's books, chapter and pages,
creates the connector and waits for a full sync, then tears both down.

BookStack here runs in the integration stack, so this connector has live
coverage without an external account. After the first full sync the connector
syncs from BookStack's audit log, so every test after TC-SYNC-001 exercises
incremental sync.

Test cases:
  TC-SYNC-001   — Full sync: every page is a record, in its book or chapter
  TC-STREAM-001 — A page's record streams the page as markdown
  TC-INCR-001   — A page created after the first sync appears on the next one
  TC-UPD-001    — Editing a page re-indexes its record in place
  TC-FILTER-001 — A new page in a book the sync filter excludes is not indexed

Not covered, because the connector does not do it: deleting or moving a page
(its audit-log delete handling is disabled and moves are not implemented), and
removing pages indexed before a filter excluded their book.
"""

import logging
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from connectors.bookstack.bookstack_seed import (  # type: ignore[import-not-found]
    ENGINEERING,
    HANDBOOK,
    LEAVE_POLICY,
    POLICIES,
)
from connectors.bookstack.bookstack_source_helper import (  # type: ignore[import-not-found]
    BookStackSourceHelper,
)
from helper.graph_provider import GraphProviderProtocol
from helper.graph_provider_utils import (
    wait_for_sync_completion,
    wait_until_graph_condition,
)
from helper.storage_incremental import (
    DEFAULT_SYNC_TIMEOUT_SEC,
    restart_sync,
    settle_record_baseline,
    sync_until_names_visible,
    wait_for_record_reindex,
)
from pipeshub_client import (
    PipeshubClient,  # type: ignore[import-not-found]
)

logger = logging.getLogger("bookstack-lifecycle-test")


def _page_external_id(page_id: int) -> str:
    return f"page/{page_id}"


@pytest.mark.integration
@pytest.mark.bookstack
@pytest.mark.asyncio(loop_scope="session")
class TestBookStackConnector:
    """Lifecycle coverage for the BookStack connector."""

    @pytest.mark.order(1)
    async def test_tc_sync_001_full_sync_graph_validation(
        self,
        bookstack_connector: dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-SYNC-001: Every page is in the graph, grouped under its book or chapter."""
        connector_id = bookstack_connector["connector_id"]
        pages = bookstack_connector["seeded_pages"]
        folder = bookstack_connector["folder"]

        await graph_provider.assert_min_records(connector_id, len(pages))
        await graph_provider.assert_record_names_contain(connector_id, pages)
        await graph_provider.assert_no_orphan_records(connector_id)

        groups = await graph_provider.fetch_record_group_names(connector_id)
        for expected in (f"{folder} {HANDBOOK}", f"{folder} {ENGINEERING}", POLICIES):
            assert expected in groups, f"TC-SYNC-001: no record group {expected!r}; found {groups}"

        leave = await graph_provider.get_record_by_external_id(
            connector_id, _page_external_id(bookstack_connector["page_ids"][LEAVE_POLICY[0]])
        )
        chapter_id = bookstack_connector["chapters"][POLICIES]
        assert leave is not None and leave.external_record_group_id == f"chapter/{chapter_id}", (
            "TC-SYNC-001: a page inside a chapter belongs to the chapter"
        )
        logger.info("TC-SYNC-001 passed: %s (connector %s)", pages, connector_id)

    @pytest.mark.order(2)
    async def test_tc_stream_001_page_streams_as_markdown(
        self,
        bookstack_connector: dict[str, Any],
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-STREAM-001: Streaming a page's record returns its markdown, as indexing reads it."""
        connector_id = bookstack_connector["connector_id"]
        name, body = LEAVE_POLICY
        record = await graph_provider.get_record_by_external_id(
            connector_id, _page_external_id(bookstack_connector["page_ids"][name])
        )
        assert record is not None, f"TC-STREAM-001: {name} is not in the graph"

        response = pipeshub_client.stream_record(record.id)
        assert response.status_code == 200
        markdown = response.content.decode()
        assert f"# {name}" in markdown and body in markdown, markdown
        logger.info("TC-STREAM-001 passed: %s streamed", name)

    @pytest.mark.order(3)
    async def test_tc_incr_001_new_page_is_picked_up(
        self,
        bookstack_connector: dict[str, Any],
        bookstack_source: BookStackSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-INCR-001: A page created after the first sync appears on the next one."""
        connector_id = bookstack_connector["connector_id"]
        before_count = await settle_record_baseline(
            pipeshub_client, graph_provider, connector_id
        )

        name = "Security policy"
        bookstack_source.create_page(
            name, "Rotate keys every year.", book_id=bookstack_connector["books"][HANDBOOK]
        )
        after_count = await sync_until_names_visible(
            pipeshub_client, graph_provider, connector_id, [name]
        )
        assert after_count == before_count + 1, (
            f"TC-INCR-001: expected exactly one new record for {name}; "
            f"count went from {before_count} to {after_count}"
        )
        logger.info("TC-INCR-001 passed: before=%d, after=%d", before_count, after_count)

    @pytest.mark.order(4)
    async def test_tc_upd_001_editing_a_page_reindexes_it(
        self,
        bookstack_connector: dict[str, Any],
        bookstack_source: BookStackSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-UPD-001: An edited page is re-indexed in place: a higher version, no second record."""
        connector_id = bookstack_connector["connector_id"]
        name = LEAVE_POLICY[0]
        page_id = bookstack_connector["page_ids"][name]
        before_count = await settle_record_baseline(
            pipeshub_client, graph_provider, connector_id
        )
        before_record = await graph_provider.get_record_by_name(connector_id, name)
        assert before_record is not None, f"TC-UPD-001: {name} is not in the graph"
        before_version = before_record.get("version")

        updated = "Everyone gets 30 days of paid leave a year."
        bookstack_source.update_page(page_id, updated)
        restart_sync(pipeshub_client, connector_id)
        after_record = await wait_for_record_reindex(
            graph_provider, connector_id, name, before_version
        )
        assert after_record.get("version") > before_version

        after_count = await settle_record_baseline(
            pipeshub_client, graph_provider, connector_id
        )
        assert after_count == before_count, (
            f"TC-UPD-001: record count moved from {before_count} to {after_count} "
            f"after editing {name}; it must update the record, not add one"
        )
        record = await graph_provider.get_record_by_external_id(
            connector_id, _page_external_id(page_id)
        )
        assert updated in pipeshub_client.stream_record(record.id).content.decode()
        logger.info("TC-UPD-001 passed: %s re-indexed, count stable at %d", name, after_count)

    @pytest.mark.order(5)
    async def test_tc_filter_001_page_in_excluded_book_is_not_indexed(
        self,
        bookstack_connector: dict[str, Any],
        bookstack_source: BookStackSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-FILTER-001: With the Engineering book excluded, its new pages are skipped."""
        connector_id = bookstack_connector["connector_id"]
        books = bookstack_connector["books"]
        suffix = uuid.uuid4().hex[:6]
        excluded, included = f"Deploy guide {suffix}", f"Expenses {suffix}"
        bookstack_source.create_page(excluded, "Ship on Tuesdays.", book_id=books[ENGINEERING])
        bookstack_source.create_page(included, "Submit receipts monthly.", book_id=books[HANDBOOK])

        pipeshub_client.update_connector_filters_sync_safe(
            connector_id,
            filters={
                "sync": {
                    "values": {
                        "book_ids": {
                            "operator": "not_in",
                            "value": [str(books[ENGINEERING])],
                            "type": "list",
                        }
                    }
                }
            },
        )

        async def _included_visible() -> bool:
            return await graph_provider.get_record_by_name(connector_id, included) is not None

        await wait_until_graph_condition(
            connector_id,
            check=_included_visible,
            timeout=DEFAULT_SYNC_TIMEOUT_SEC,
            poll_interval=10,
            description=f"sync of {included} after the filter change",
        )
        await wait_for_sync_completion(
            pipeshub_client, graph_provider, connector_id, timeout=DEFAULT_SYNC_TIMEOUT_SEC
        )
        await graph_provider.assert_record_not_exists(connector_id, excluded)
        logger.info("TC-FILTER-001 passed: %s indexed, %s skipped", included, excluded)

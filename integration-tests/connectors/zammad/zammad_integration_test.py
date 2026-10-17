# pyright: ignore-file

"""
Zammad Connector – Integration Tests
====================================

Tests receive a fully set-up connector via the ``zammad_connector`` fixture
(defined in conftest.py), which creates the run's groups, tickets and
attachment, creates the connector and waits for a full sync, then tears both
down.

Zammad here runs in the integration stack, so this connector has live coverage
without an external account. The connector finds tickets through Zammad's
search API, which Elasticsearch serves — see the PR and the compose file for
why the index is not optional.

Test cases:
  TC-SYNC-001   — Full sync: every ticket is a record, under its Zammad group
  TC-STREAM-001 — A ticket's record streams the article text
  TC-ATTACH-001 — An article attachment is its own record, under the same group
  TC-PERM-001   — A group's record group carries a permission for that group
  TC-INCR-001   — A ticket filed after the first sync appears on the next one
  TC-UPD-001    — Retitling a ticket re-indexes its record in place

Not covered: knowledge-base answers. Zammad has no REST route that creates a
knowledge base — it is made through the web interface — so a run cannot seed
one deterministically. The sync path for it is exercised only in that a missing
knowledge base must not stop tickets syncing, which TC-SYNC-001 shows.
"""

import logging
import sys
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from connectors.zammad.zammad_seed import (  # type: ignore[import-not-found]
    ATTACHMENT_BODY,
    ATTACHMENT_NAME,
    BILLING_GROUP,
    INVOICE,
    LAPTOP,
    PRINTER,
    SUPPORT_GROUP,
)
from connectors.zammad.zammad_source_helper import (  # type: ignore[import-not-found]
    ZammadSourceHelper,
)
from helper.graph_provider import GraphProviderProtocol
from helper.storage_incremental import (
    restart_sync,
    settle_record_baseline,
    sync_until_names_visible,
    wait_for_record_reindex,
)
from pipeshub_client import (
    PipeshubClient,  # type: ignore[import-not-found]
)

logger = logging.getLogger("zammad-lifecycle-test")


@pytest.mark.integration
@pytest.mark.zammad
@pytest.mark.asyncio(loop_scope="session")
class TestZammadConnector:
    """Lifecycle coverage for the Zammad connector."""

    @pytest.mark.order(1)
    async def test_tc_sync_001_full_sync_graph_validation(
        self,
        zammad_connector: dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-SYNC-001: Every ticket is in the graph, grouped under its Zammad group."""
        connector_id = zammad_connector["connector_id"]
        titles = zammad_connector["seeded_tickets"]
        run = zammad_connector["folder"]

        await graph_provider.assert_min_records(connector_id, len(titles))
        await graph_provider.assert_record_names_contain(connector_id, titles)
        await graph_provider.assert_no_orphan_records(connector_id)

        groups = await graph_provider.fetch_record_group_names(connector_id)
        for expected in (f"{run} {SUPPORT_GROUP}", f"{run} {BILLING_GROUP}"):
            assert expected in groups, f"TC-SYNC-001: no record group {expected!r}; found {groups}"

        invoice = await graph_provider.get_record_by_external_id(
            connector_id, str(zammad_connector["ticket_ids"][INVOICE[0]])
        )
        billing_id = zammad_connector["groups"][BILLING_GROUP]
        assert invoice is not None and invoice.external_record_group_id == f"group_{billing_id}", (
            "TC-SYNC-001: a ticket belongs to the record group of its Zammad group"
        )
        logger.info("TC-SYNC-001 passed: %s (connector %s)", titles, connector_id)

    @pytest.mark.order(2)
    async def test_tc_stream_001_ticket_streams_its_article(
        self,
        zammad_connector: dict[str, Any],
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-STREAM-001: Streaming a ticket's record returns the article text, as indexing reads it."""
        connector_id = zammad_connector["connector_id"]
        record = await graph_provider.get_record_by_external_id(
            connector_id, str(zammad_connector["ticket_ids"][PRINTER[0]])
        )
        assert record is not None, f"TC-STREAM-001: {PRINTER[0]} is not in the graph"

        response = pipeshub_client.stream_record(record.id)
        assert response.status_code == 200
        body = response.content.decode()
        assert PRINTER[1] in body, body[:500]
        logger.info("TC-STREAM-001 passed: %s streamed", PRINTER[0])

    @pytest.mark.order(3)
    async def test_tc_attach_001_article_attachment_is_indexed(
        self,
        zammad_connector: dict[str, Any],
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-ATTACH-001: An attachment on an article is its own record, under the ticket's group."""
        connector_id = zammad_connector["connector_id"]
        support_id = zammad_connector["groups"][SUPPORT_GROUP]

        # get_record_by_name returns the stored document, whose id field differs
        # between the two graph backends. Take only its external id from there,
        # then read the record itself through the typed lookup, as the tests
        # above do, so the fields below are the model's and not a raw document's.
        stored = await graph_provider.get_record_by_name(connector_id, ATTACHMENT_NAME)
        assert stored is not None, (
            f"TC-ATTACH-001: {ATTACHMENT_NAME} is not in the graph; "
            "the connector indexes ticket attachments as their own records"
        )
        attachment = await graph_provider.get_record_by_external_id(
            connector_id, str(stored["externalRecordId"])
        )
        assert attachment is not None, (
            f"TC-ATTACH-001: {ATTACHMENT_NAME} is stored but has no record with "
            f"external id {stored['externalRecordId']!r}"
        )
        assert attachment.external_record_group_id == f"group_{support_id}", (
            "TC-ATTACH-001: an attachment belongs to the same record group as its "
            f"ticket; got {attachment.external_record_group_id!r}"
        )
        streamed = pipeshub_client.stream_record(attachment.id).content.decode()
        assert ATTACHMENT_BODY in streamed, streamed[:300]
        logger.info("TC-ATTACH-001 passed: %s indexed and streamed", ATTACHMENT_NAME)

    @pytest.mark.order(4)
    async def test_tc_perm_001_group_permission_reaches_the_record_group(
        self,
        zammad_connector: dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-PERM-001: Each record group carries a permission for its Zammad group.

        This is what decides who can find a ticket: the connector grants access
        to the Zammad group's members, not to each ticket individually.
        """
        connector_id = zammad_connector["connector_id"]
        support_id = zammad_connector["groups"][SUPPORT_GROUP]

        group_edges = await graph_provider.count_permission_edges_to_record_groups(
            connector_id, f"group_{support_id}"
        )
        assert group_edges > 0, (
            f"TC-PERM-001: record group group_{support_id} has no permission edge; "
            "members of the Zammad group would not find its tickets"
        )
        member_edges = await graph_provider.count_user_to_group_permission_edges(
            connector_id, str(support_id)
        )
        assert member_edges > 0, (
            f"TC-PERM-001: nobody is linked to Zammad group {support_id}, so the "
            "permission above grants access to no one"
        )
        logger.info(
            "TC-PERM-001 passed: %d group edge(s), %d member edge(s) on group_%s",
            group_edges, member_edges, support_id,
        )

    @pytest.mark.order(5)
    async def test_tc_incr_001_new_ticket_is_picked_up(
        self,
        zammad_connector: dict[str, Any],
        zammad_source: ZammadSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-INCR-001: A ticket filed after the first sync appears on the next one.

        This is the incremental path, where the connector asks Zammad's search
        for tickets updated since the last run.
        """
        connector_id = zammad_connector["connector_id"]
        before_count = await settle_record_baseline(pipeshub_client, graph_provider, connector_id)

        title = f"{zammad_connector['folder']} Monitor flickers"
        zammad_source.create_ticket(
            title, "The second monitor flickers after waking.", zammad_connector["groups"][SUPPORT_GROUP]
        )
        zammad_source.wait_until_searchable([title])

        after_count = await sync_until_names_visible(
            pipeshub_client, graph_provider, connector_id, [title]
        )
        assert after_count == before_count + 1, (
            f"TC-INCR-001: expected exactly one new record for {title}; "
            f"count went from {before_count} to {after_count}"
        )
        logger.info("TC-INCR-001 passed: before=%d, after=%d", before_count, after_count)

    @pytest.mark.order(6)
    async def test_tc_upd_001_retitling_a_ticket_reindexes_it(
        self,
        zammad_connector: dict[str, Any],
        zammad_source: ZammadSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-UPD-001: A retitled ticket is re-indexed in place: a higher version, no second record."""
        connector_id = zammad_connector["connector_id"]
        ticket_id = zammad_connector["ticket_ids"][LAPTOP[0]]
        old_title = zammad_connector["titles"][LAPTOP[0]]
        before_count = await settle_record_baseline(pipeshub_client, graph_provider, connector_id)
        before_record = await graph_provider.get_record_by_name(connector_id, old_title)
        assert before_record is not None, f"TC-UPD-001: {old_title} is not in the graph"
        before_version = before_record.get("version")

        new_title = f"{old_title} (escalated)"
        zammad_source.update_ticket_title(ticket_id, new_title)
        zammad_source.wait_until_searchable([new_title])
        restart_sync(pipeshub_client, connector_id)

        after_record = await wait_for_record_reindex(
            graph_provider, connector_id, new_title, before_version
        )
        assert after_record.get("version") > before_version

        after_count = await settle_record_baseline(pipeshub_client, graph_provider, connector_id)
        assert after_count == before_count, (
            f"TC-UPD-001: record count moved from {before_count} to {after_count} "
            f"after retitling {old_title}; it must update the record, not add one"
        )
        logger.info("TC-UPD-001 passed: %s re-indexed, count stable at %d", new_title, after_count)

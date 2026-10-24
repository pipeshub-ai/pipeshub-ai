"""Live Zendesk → PipesHub checks against a seeded ticket and public article.

These tests use a real Zendesk OAuth token and the running PipesHub stack. The
source objects are read-only; the module fixture creates and removes only a
PipesHub connector. See README.md for the required source fixture contract.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime
from typing import Any

import pytest

from app.models.entities import RecordType
from helper.graph_provider import GraphProviderProtocol
from helper.graph_provider_utils import wait_for_sync_completion
from helper.indexing_wait import wait_until_record_indexing_completed
from helper.oauth_token_helper import inject_access_token
from pipeshub_client import PipeshubClient, PipeshubClientError

pytestmark = [
    pytest.mark.integration,
    pytest.mark.zendesk,
    pytest.mark.asyncio(loop_scope="session"),
]


def _source_ms(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)


class TestZendeskLive:
    async def test_01_source_oauth_account_is_real(
        self, zendesk_source: dict[str, Any]
    ) -> None:
        assert int(zendesk_source["current_user"]["id"]) > 0

    async def test_02_connector_is_enabled(
        self,
        zendesk_connector: str,
        pipeshub_client: PipeshubClient,
    ) -> None:
        assert (
            pipeshub_client.get_connector_status(zendesk_connector)["isActive"] is True
        )

    async def test_03_graph_has_zendesk_app(
        self,
        zendesk_connector: str,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        app = await graph_provider.get_app_metadata_by_connector_id(zendesk_connector)
        assert app is not None
        assert app.type == "Zendesk" and app.scope == "team"

    async def test_04_seeded_ticket_was_synced(
        self,
        zendesk_connector: str,
        zendesk_source: dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        record = await graph_provider.get_record_by_external_id(
            zendesk_connector,
            str(zendesk_source["ticket"]["id"]),
        )
        assert record is not None
        assert record.record_type == RecordType.TICKET

    async def test_05_ticket_subject_and_link_match_source(
        self,
        zendesk_connector: str,
        zendesk_source: dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        ticket = zendesk_source["ticket"]
        record = await graph_provider.get_record_by_external_id(
            zendesk_connector, str(ticket["id"])
        )
        assert record is not None
        assert record.record_name == ticket["subject"]
        assert f"/agent/tickets/{ticket['id']}" in (record.weburl or "")

    async def test_06_ticket_source_timestamps_match(
        self,
        zendesk_connector: str,
        zendesk_source: dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        ticket = zendesk_source["ticket"]
        record = await graph_provider.get_record_by_external_id(
            zendesk_connector, str(ticket["id"])
        )
        assert record is not None
        assert record.source_created_at == _source_ms(ticket["created_at"])
        assert record.source_updated_at == _source_ms(ticket["updated_at"])

    async def test_07_ticket_tags_match_source(
        self,
        zendesk_connector: str,
        zendesk_source: dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        ticket = zendesk_source["ticket"]
        record = await graph_provider.get_typed_record_by_external_id(
            zendesk_connector, str(ticket["id"])
        )
        assert record is not None
        assert set(record.labels) == set(ticket.get("tags") or [])

    async def test_08_ticket_status_is_typed(
        self,
        zendesk_connector: str,
        zendesk_source: dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        ticket = zendesk_source["ticket"]
        record = await graph_provider.get_typed_record_by_external_id(
            zendesk_connector, str(ticket["id"])
        )
        assert record is not None
        expected = {
            "new": "NEW",
            "open": "OPEN",
            "pending": "PENDING",
            "hold": "BLOCKED",
            "solved": "RESOLVED",
            "closed": "CLOSED",
        }
        assert record.status.value == expected[ticket["status"]]

    async def test_09_ticket_group_matches_live_name(
        self,
        zendesk_connector: str,
        zendesk_source: dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        group_id = f"group_{zendesk_source['group']['id']}"
        group = await graph_provider.get_record_group_by_external_id(
            zendesk_connector, group_id
        )
        assert group is not None
        assert group.name == zendesk_source["group"]["name"]

    async def test_10_ticket_belongs_to_its_group(
        self,
        zendesk_connector: str,
        zendesk_source: dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        assert await graph_provider.record_belongs_to_external_group(
            zendesk_connector,
            str(zendesk_source["ticket"]["id"]),
            f"group_{zendesk_source['group']['id']}",
        )

    async def test_11_ticket_group_has_read_grant(
        self,
        zendesk_connector: str,
        zendesk_source: dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        assert await graph_provider.group_has_record_permission(
            zendesk_connector,
            f"group_{zendesk_source['group']['id']}",
            str(zendesk_source["ticket"]["id"]),
        )

    async def test_12_assignee_identity_matches_source(
        self,
        zendesk_connector: str,
        zendesk_source: dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        source = zendesk_source["assignee"]
        user = await graph_provider.get_user_by_source_id(
            str(source["id"]), zendesk_connector
        )
        assert user is not None
        assert user.email.casefold() == source["email"].casefold()

    async def test_13_public_article_title_matches_source(
        self,
        zendesk_connector: str,
        zendesk_source: dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        article = zendesk_source["article"]
        record = await graph_provider.get_record_by_external_id(
            zendesk_connector, f"article_{article['id']}"
        )
        assert record is not None
        assert record.record_type == RecordType.WEBPAGE
        assert record.record_name == article["title"]

    async def test_14_article_section_matches_live_name(
        self,
        zendesk_connector: str,
        zendesk_source: dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        section = zendesk_source["section"]
        group = await graph_provider.get_record_group_by_external_id(
            zendesk_connector, f"section_{section['id']}"
        )
        assert group is not None
        assert group.name == section["name"]

    async def test_15_article_belongs_to_section(
        self,
        zendesk_connector: str,
        zendesk_source: dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        assert await graph_provider.record_belongs_to_external_group(
            zendesk_connector,
            f"article_{zendesk_source['article']['id']}",
            f"section_{zendesk_source['section']['id']}",
        )

    async def test_16_ticket_stream_includes_only_public_comments(
        self,
        zendesk_connector: str,
        zendesk_source: dict[str, Any],
        graph_provider: GraphProviderProtocol,
        pipeshub_client: PipeshubClient,
    ) -> None:
        record = await graph_provider.get_record_by_external_id(
            zendesk_connector,
            str(zendesk_source["ticket"]["id"]),
        )
        assert record is not None
        with pipeshub_client.stream_record(record.id, timeout=120) as response:
            assert "application/blocks" in response.headers.get("content-type", "")
            groups = response.json()["block_groups"]
        streamed_ids = {str(group["source_group_id"]) for group in groups}
        public_ids = {
            str(c["id"]) for c in zendesk_source["comments"] if c.get("public")
        }
        private_ids = {
            str(c["id"]) for c in zendesk_source["comments"] if not c.get("public")
        }
        assert public_ids <= streamed_ids
        assert not (private_ids & streamed_ids)

    async def test_17_article_stream_contains_live_body(
        self,
        zendesk_connector: str,
        zendesk_source: dict[str, Any],
        graph_provider: GraphProviderProtocol,
        pipeshub_client: PipeshubClient,
    ) -> None:
        article = zendesk_source["article"]
        record = await graph_provider.get_record_by_external_id(
            zendesk_connector, f"article_{article['id']}"
        )
        assert record is not None
        with pipeshub_client.stream_record(record.id, timeout=120) as response:
            assert "application/blocks" in response.headers.get("content-type", "")
            groups = response.json()["block_groups"]
        assert groups[0]["source_group_id"] == str(article["id"])
        assert groups[0]["name"] == article["title"]
        assert groups[0]["data"].strip()

    async def test_18_ticket_reaches_completed_indexing(
        self,
        zendesk_connector: str,
        zendesk_source: dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        await wait_until_record_indexing_completed(
            graph_provider,
            zendesk_connector,
            str(zendesk_source["ticket"]["id"]),
            timeout=300,
            description="Zendesk ticket indexing",
        )

    async def test_19_resync_keeps_source_record_ids_stable(
        self,
        zendesk_connector: str,
        zendesk_source: dict[str, Any],
        graph_provider: GraphProviderProtocol,
        pipeshub_client: PipeshubClient,
    ) -> None:
        ticket_id = str(zendesk_source["ticket"]["id"])
        article_id = f"article_{zendesk_source['article']['id']}"
        before_ticket = await graph_provider.get_record_by_external_id(
            zendesk_connector, ticket_id
        )
        before_article = await graph_provider.get_record_by_external_id(
            zendesk_connector, article_id
        )
        assert before_ticket is not None and before_article is not None
        pipeshub_client.toggle_sync(zendesk_connector, enable=False)
        pipeshub_client.toggle_sync(zendesk_connector, enable=True)
        await wait_for_sync_completion(
            pipeshub_client,
            graph_provider,
            zendesk_connector,
            min_records=2,
            timeout=600,
        )
        after_ticket = await graph_provider.get_record_by_external_id(
            zendesk_connector, ticket_id
        )
        after_article = await graph_provider.get_record_by_external_id(
            zendesk_connector, article_id
        )
        assert after_ticket is not None and after_ticket.id == before_ticket.id
        assert after_article is not None and after_article.id == before_article.id

    async def test_20_invalid_oauth_token_fails_connection(
        self,
        zendesk_source: dict[str, Any],
        pipeshub_client: PipeshubClient,
    ) -> None:
        instance = pipeshub_client.create_connector(
            connector_type="Zendesk",
            instance_name=f"zendesk-invalid-oauth-{uuid.uuid4().hex[:8]}",
            scope="team",
            auth_type="OAUTH",
            config={"auth": {"subdomain": zendesk_source["subdomain"]}},
        )
        connector_id = instance.connector_id
        assert connector_id
        try:
            inject_access_token(connector_id, "invalid-zendesk-oauth-test-token")
            toggle_error = False
            try:
                pipeshub_client.toggle_sync(connector_id, enable=True)
            except PipeshubClientError:
                toggle_error = True

            deadline = time.monotonic() + 30
            connector = pipeshub_client.get_connector(connector_id)
            while not toggle_error and time.monotonic() < deadline:
                if connector.get("isAuthenticated") is False:
                    break
                await asyncio.sleep(2)
                connector = pipeshub_client.get_connector(connector_id)
            assert toggle_error or connector.get("isAuthenticated") is False
        finally:
            try:
                pipeshub_client.toggle_sync(connector_id, enable=False)
            except PipeshubClientError:
                pass
            pipeshub_client.delete_connector(connector_id)

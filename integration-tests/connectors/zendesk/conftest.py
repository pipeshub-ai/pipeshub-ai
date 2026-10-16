"""Live Zendesk fixtures: one read-only source snapshot and one PipesHub sync."""

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest_asyncio

from helper.graph_provider import GraphProviderProtocol
from helper.graph_provider_utils import wait_for_sync_completion
from helper.oauth_token_helper import inject_access_token
from helper.source_credentials import source_unavailable
from pipeshub_client import PipeshubClient

logger = logging.getLogger("zendesk-live-it")
REQUIRED_ENV = (
    "ZENDESK_TEST_SUBDOMAIN",
    "ZENDESK_TEST_ACCESS_TOKEN",
    "ZENDESK_TEST_TICKET_ID",
    "ZENDESK_TEST_ARTICLE_ID",
    "SECRET_KEY",
)


async def _get(client: httpx.AsyncClient, path: str, key: str) -> dict[str, Any]:
    response = await client.get(path)
    response.raise_for_status()
    result = response.json().get(key)
    assert isinstance(result, dict), f"Zendesk {path} did not return {key}"
    return result


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def zendesk_source() -> dict[str, Any]:
    missing = [name for name in REQUIRED_ENV if not os.getenv(name)]
    if missing:
        source_unavailable(
            "The live Zendesk tenant is not configured.",
            secrets=missing,
        )

    subdomain = os.environ["ZENDESK_TEST_SUBDOMAIN"].strip()
    assert subdomain and all(c.isalnum() or c == "-" for c in subdomain), (
        "Invalid Zendesk subdomain"
    )
    ticket_id = os.environ["ZENDESK_TEST_TICKET_ID"].strip()
    article_id = os.environ["ZENDESK_TEST_ARTICLE_ID"].strip()
    assert ticket_id.isdecimal() and article_id.isdecimal(), (
        "Zendesk fixture IDs must be numeric"
    )

    async with httpx.AsyncClient(
        base_url=f"https://{subdomain}.zendesk.com",
        headers={"Authorization": f"Bearer {os.environ['ZENDESK_TEST_ACCESS_TOKEN']}"},
        timeout=30,
    ) as client:
        current_user = await _get(client, "/api/v2/users/me.json", "user")
        ticket = await _get(client, f"/api/v2/tickets/{ticket_id}.json", "ticket")
        article = await _get(
            client, f"/api/v2/help_center/articles/{article_id}.json", "article"
        )
        assert ticket.get("group_id"), "Seeded ticket must belong to a Zendesk group"
        assert ticket.get("requester_id") and ticket.get("assignee_id"), (
            "Seeded ticket must have a requester and assignee"
        )
        assert ticket.get("subject") and ticket.get("status"), (
            "Seeded ticket must have a stable subject and status"
        )
        assert ticket["status"] != "deleted", "Seeded ticket must not be deleted"
        assert article.get("section_id") and not article.get("draft"), (
            "Seeded article must be published in a section"
        )
        assert article.get("published") is not False and article.get("body"), (
            "Seeded article must be published with a non-empty body"
        )
        assert article.get("user_segment_id") is None and not article.get(
            "user_segment_ids"
        ), "Seeded article must be public so the connector can sync it"
        group = await _get(client, f"/api/v2/groups/{ticket['group_id']}.json", "group")
        requester = await _get(
            client, f"/api/v2/users/{ticket['requester_id']}.json", "user"
        )
        assignee = await _get(
            client, f"/api/v2/users/{ticket['assignee_id']}.json", "user"
        )
        assert requester.get("email") and assignee.get("email"), (
            "Seeded requester and assignee must have email addresses"
        )
        section = await _get(
            client,
            f"/api/v2/help_center/sections/{article['section_id']}.json",
            "section",
        )
        comments_response = await client.get(
            f"/api/v2/tickets/{ticket_id}/comments.json"
        )
        comments_response.raise_for_status()
        comments = comments_response.json().get("comments") or []
        assert any(comment.get("public") for comment in comments), (
            "Seeded ticket must have at least one public comment"
        )

    return {
        "subdomain": subdomain,
        "current_user": current_user,
        "ticket": ticket,
        "article": article,
        "group": group,
        "requester": requester,
        "assignee": assignee,
        "section": section,
        "comments": comments,
    }


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def zendesk_connector(
    zendesk_source: dict[str, Any],
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncIterator[str]:
    group_id = str(zendesk_source["ticket"]["group_id"])
    instance = pipeshub_client.create_connector(
        connector_type="Zendesk",
        instance_name=f"zendesk-live-it-{uuid.uuid4().hex[:10]}",
        scope="team",
        auth_type="OAUTH",
        config={
            "auth": {"subdomain": zendesk_source["subdomain"]},
            "filters": {
                "sync": {
                    "values": {
                        "group_ids": {
                            "operator": "in",
                            "type": "list",
                            "value": [group_id],
                        },
                    }
                }
            },
        },
    )
    connector_id = instance.connector_id
    assert connector_id, "PipesHub did not return a Zendesk connector ID"
    try:
        inject_access_token(connector_id, os.environ["ZENDESK_TEST_ACCESS_TOKEN"])
        pipeshub_client.toggle_sync(connector_id, enable=True)
        await wait_for_sync_completion(
            pipeshub_client,
            graph_provider,
            connector_id,
            min_records=2,
            timeout=600,
        )
        yield connector_id
    finally:
        try:
            pipeshub_client.toggle_sync(connector_id, enable=False)
        except Exception:
            logger.exception(
                "Could not disable Zendesk test connector %s", connector_id
            )
        try:
            pipeshub_client.delete_connector(connector_id)
            await graph_provider.assert_all_records_cleaned(connector_id, timeout=300)
        except Exception:
            logger.exception(
                "Zendesk test connector %s could not be fully cleaned up", connector_id
            )
            raise

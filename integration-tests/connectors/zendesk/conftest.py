"""Live Zendesk fixtures: one read-only source snapshot and one PipesHub sync."""

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio

from ai_models_setup import SeededAIModel
from connectors.zendesk.constants import (
    ZENDESK_TEST_ARTICLE_ID,
    ZENDESK_TEST_TICKET_ID,
)
from helper.clients.ai_models_client import AIModelsClient
from helper.graph_provider import GraphProviderProtocol
from helper.graph_provider_utils import wait_for_sync_completion
from helper.oauth_token_helper import inject_access_token
from helper.source_credentials import source_unavailable
from pipeshub_client import PipeshubClient

logger = logging.getLogger("zendesk-live-it")
REQUIRED_ENV = (
    "ZENDESK_TEST_SUBDOMAIN",
    "SECRET_KEY",
)


@pytest.fixture(scope="session")
def ai_models_configured(pipeshub_client: PipeshubClient) -> SeededAIModel:
    """Use the admin account's existing models for this read-only source suite."""
    models_client = AIModelsClient(pipeshub_client)
    selected: dict[str, dict[str, Any]] = {}
    for model_type in ("llm", "embedding"):
        response = models_client.get_models_by_type(model_type)
        response.raise_for_status()
        models = response.json().get("models") or []
        defaults = [model for model in models if model.get("isDefault")]
        if len(defaults) != 1:
            pytest.fail(
                f"Zendesk live tests need one default {model_type} model in "
                "the PipesHub admin account."
            )
        selected[model_type] = defaults[0]
    llm = selected["llm"]
    return SeededAIModel(
        model_type="llm",
        provider=llm["provider"],
        model_name=llm["configuration"]["model"],
        model_key=llm["modelKey"],
        owned=False,
    )


def _access_token() -> str:
    token_file = os.getenv("ZENDESK_TEST_ACCESS_TOKEN_FILE")
    if token_file:
        # The workflow names the file before minting; a failed mint leaves it unwritten.
        if not Path(token_file).is_file():
            source_unavailable(
                "The Zendesk access token was not minted: see the 'Mint Zendesk test token'"
                " step's log for why.",
                secrets=[
                    "ZENDESK_TEST_SUBDOMAIN",
                    "ZENDESK_TEST_CLIENT_ID",
                    "ZENDESK_TEST_CLIENT_SECRET",
                ],
            )
        token = Path(token_file).read_text().strip()
    else:
        token = os.getenv("ZENDESK_TEST_ACCESS_TOKEN", "").strip()
    if not token:
        source_unavailable(
            "A Zendesk access token has not been prepared.",
            secrets=["ZENDESK_TEST_ACCESS_TOKEN_FILE or ZENDESK_TEST_ACCESS_TOKEN"],
        )
    return token


async def _get(client: httpx.AsyncClient, path: str, key: str) -> dict[str, Any]:
    response = await client.get(path)
    response.raise_for_status()
    result = response.json().get(key)
    assert isinstance(result, dict), f"Zendesk {path} did not return {key}"
    return result


SEED_TAG = "pipeshub-seed"
MAX_FALLBACK_CANDIDATES = 25


def _ticket_problem(ticket: dict[str, Any]) -> str | None:
    if not ticket.get("group_id"):
        return "it has no group"
    if not (ticket.get("requester_id") and ticket.get("assignee_id")):
        return "it has no requester or no assignee"
    if not (ticket.get("subject") and ticket.get("status")):
        return "it has no subject or status"
    if ticket["status"] == "deleted":
        return "it is deleted"
    return None


async def _ticket_snapshot(
    client: httpx.AsyncClient, ticket: dict[str, Any]
) -> dict[str, Any] | str:
    """Everything the suite asserts about one ticket, or why it cannot be used."""
    problem = _ticket_problem(ticket)
    if problem:
        return problem
    requester = await _get(
        client, f"/api/v2/users/{ticket['requester_id']}.json", "user"
    )
    assignee = await _get(client, f"/api/v2/users/{ticket['assignee_id']}.json", "user")
    if not (requester.get("email") and assignee.get("email")):
        return "its requester or assignee has no email address"
    response = await client.get(f"/api/v2/tickets/{ticket['id']}/comments.json")
    response.raise_for_status()
    comments = response.json().get("comments") or []
    if not any(comment.get("public") for comment in comments):
        return "it has no public comment"
    group = await _get(client, f"/api/v2/groups/{ticket['group_id']}.json", "group")
    return {
        "ticket": ticket,
        "group": group,
        "requester": requester,
        "assignee": assignee,
        "comments": comments,
    }


async def _load_ticket(
    client: httpx.AsyncClient, ticket_id: int
) -> dict[str, Any] | str:
    """Fetch a ticket fresh and snapshot it, or say why it cannot be used.

    A 404 on the ticket or a related record (a hard-deleted ticket still in the
    search index, a deleted user) rejects only that ticket. Other errors, such as
    auth or rate limits, still raise.
    """
    try:
        ticket = await _get(client, f"/api/v2/tickets/{ticket_id}.json", "ticket")
        return await _ticket_snapshot(client, ticket)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 404:
            raise
        return f"it or a related record was not found ({exc.request.url.path})"


async def _seeded_ticket(client: httpx.AsyncClient, ticket_id: int) -> dict[str, Any]:
    """Prefer the configured ticket, then any other seed-tagged ticket that qualifies.

    The tenant is shared with Zendesk automations: the AI agent escalated the
    configured ticket and cleared its assignee (tag ``escalated_by_ultimate``),
    and the suite's token is read-only so it cannot put the assignee back.
    """
    snapshot = await _load_ticket(client, ticket_id)
    if isinstance(snapshot, dict):
        return snapshot
    rejected = [f"ticket {ticket_id}: {snapshot}"]

    response = await client.get(
        "/api/v2/search.json",
        params={
            "query": f"type:ticket tags:{SEED_TAG} -assignee:none",
            "sort_by": "created_at",
            "sort_order": "asc",
            "per_page": MAX_FALLBACK_CANDIDATES,
        },
    )
    response.raise_for_status()
    for candidate in response.json().get("results") or []:
        if candidate.get("id") == ticket_id or candidate.get("result_type") != "ticket":
            continue
        # Search results come from an index that can lag the ticket itself.
        snapshot = await _load_ticket(client, candidate["id"])
        if isinstance(snapshot, dict):
            logger.warning(
                "Zendesk ticket %s no longer qualifies (%s); using seeded ticket %s",
                ticket_id,
                rejected[0],
                candidate["id"],
            )
            return snapshot
        rejected.append(f"ticket {candidate['id']}: {snapshot}")

    pytest.fail(
        "No Zendesk ticket tagged "
        f"{SEED_TAG!r} has a group, requester and assignee with emails, a subject, "
        "a status and a public comment. Repair the seed ticket in the tenant "
        "(and stop automations such as the AI agent from reassigning it). "
        "Rejected: " + "; ".join(rejected)
    )


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def zendesk_source() -> dict[str, Any]:
    missing = [name for name in REQUIRED_ENV if not os.getenv(name)]
    if missing:
        source_unavailable(
            "The live Zendesk tenant is not configured.",
            secrets=missing,
        )
    token = _access_token()

    subdomain = os.environ["ZENDESK_TEST_SUBDOMAIN"].strip()
    assert subdomain and all(c.isalnum() or c == "-" for c in subdomain), (
        "Invalid Zendesk subdomain"
    )
    if not all(
        isinstance(value, int) and not isinstance(value, bool) and value > 0
        for value in (ZENDESK_TEST_TICKET_ID, ZENDESK_TEST_ARTICLE_ID)
    ):
        pytest.fail(
            "Set ZENDESK_TEST_TICKET_ID and ZENDESK_TEST_ARTICLE_ID to seeded "
            "record IDs in connectors/zendesk/constants.py."
        )
    ticket_id = ZENDESK_TEST_TICKET_ID
    article_id = ZENDESK_TEST_ARTICLE_ID

    async with httpx.AsyncClient(
        base_url=f"https://{subdomain}.zendesk.com",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    ) as client:
        current_user = await _get(client, "/api/v2/users/me.json", "user")
        seeded = await _seeded_ticket(client, ticket_id)
        article = await _get(
            client, f"/api/v2/help_center/articles/{article_id}.json", "article"
        )
        assert article.get("section_id") and not article.get("draft"), (
            "Seeded article must be published in a section"
        )
        assert article.get("published") is not False and article.get("body"), (
            "Seeded article must be published with a non-empty body"
        )
        assert article.get("user_segment_id") is None and not article.get(
            "user_segment_ids"
        ), "Seeded article must be public so the connector can sync it"
        section = await _get(
            client,
            f"/api/v2/help_center/sections/{article['section_id']}.json",
            "section",
        )

    return {
        "subdomain": subdomain,
        "current_user": current_user,
        **seeded,
        "article": article,
        "section": section,
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
        inject_access_token(connector_id, _access_token())
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

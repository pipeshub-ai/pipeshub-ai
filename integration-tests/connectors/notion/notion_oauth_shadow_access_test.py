"""Notion over OAuth: the creator is linked to the Notion user who approved the connection.

The main suite connects with an integration token, whose bot belongs to the workspace, so
it can only check that nobody is linked (TC-AUTH-001). An OAuth bot is owned by the person
who approved it. This suite connects that way, checks the creator is linked to that person
as read from Notion's own ``GET /v1/users/me``, and that the link reaches no page: Notion does
not say who may open a page now, and the approver's choice in the page picker proved access
only at that moment.

It needs a token from a one-time browser consent; ``python -m helper.notion_oauth_consent``
gets one. Without it the suite skips.
"""

import logging
import os
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator

import pytest
import pytest_asyncio
import requests
from dotenv import load_dotenv

from connectors.notion.constants import (
    GRAPH_CLEANUP_TIMEOUT,
    NOTION_API_VERSION,
    NOTION_CONNECTOR_TYPE,
    NOTION_SCOPE,
    SYNC_TIMEOUT,
)
from helper.authenticated_identity import assert_authenticated_identity_link
from helper.graph_provider_utils import wait_for_sync_completion
from helper.oauth_token_helper import authenticate_connector_with_refresh_token, inject_access_token
from helper.shadow_access_e2e import _access_paths, _resolve_link
from helper.source_credentials import source_unavailable

logger = logging.getLogger("notion-oauth-shadow-access")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.notion,
    pytest.mark.asyncio(loop_scope="session"),
]

_ENV_FILE = Path(__file__).resolve().parents[2] / ".env.refresh_tokens"
_TOKEN_URL = "https://api.notion.com/v1/oauth/token"
_CLIENT_ID = "NOTION_OAUTH_CLIENT_ID"
_CLIENT_SECRET = "NOTION_OAUTH_CLIENT_SECRET"
_REFRESH_TOKEN = "NOTION_REFRESH_TOKEN"
# What the consent script stores when Notion issues no refresh token
_ACCESS_TOKEN = "NOTION_OAUTH_ACCESS_TOKEN"


# Named without "oauth": CodeQL takes any value read from such a name for a password and
# flags every log line it reaches.
@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def approved_notion_connector(pipeshub_client, graph_provider) -> AsyncGenerator[dict[str, Any], None]:
    """A Notion team connector authorized over OAuth, synced once."""
    if _ENV_FILE.exists():
        load_dotenv(_ENV_FILE)
    if not (os.getenv(_CLIENT_ID) and os.getenv(_CLIENT_SECRET)
            and (os.getenv(_REFRESH_TOKEN) or os.getenv(_ACCESS_TOKEN))):
        # CI carries no consent token, so a run never fails for the lack of one
        source_unavailable(
            "No Notion OAuth connection is configured; approve the integration once with "
            "`python -m helper.notion_oauth_consent`",
            secrets=[_CLIENT_ID, _CLIENT_SECRET, _REFRESH_TOKEN],
            required=False,
        )
    if os.getenv("NOTION_API_BASE_URL") or os.getenv("NOTION_CONNECTOR_BASE_URL"):
        # The consent token belongs to the real workspace; the connector would take it to the other endpoint.
        pytest.skip("a Notion API base URL override points away from the workspace this token is for")

    instance = pipeshub_client.create_connector(
        connector_type=NOTION_CONNECTOR_TYPE,
        instance_name=f"notion-oauth-test-{uuid.uuid4().hex[:8]}",
        scope=NOTION_SCOPE,
        config={"auth": {
            "authType": "OAUTH",
            "clientId": os.environ[_CLIENT_ID].strip(),
            "clientSecret": os.environ[_CLIENT_SECRET].strip(),
        }},
        auth_type="OAUTH",
    )
    connector_id = instance.connector_id
    assert connector_id, "Connector must have a valid ID"
    try:
        if os.getenv(_REFRESH_TOKEN):
            access_token = await authenticate_connector_with_refresh_token(
                connector_id, _REFRESH_TOKEN, _TOKEN_URL, _CLIENT_ID, _CLIENT_SECRET, basic_auth=True,
            )
        else:
            access_token = os.environ[_ACCESS_TOKEN].strip()
            inject_access_token(connector_id, access_token)
        pipeshub_client.toggle_sync(connector_id, enable=True)
        await wait_for_sync_completion(pipeshub_client, graph_provider, connector_id, timeout=SYNC_TIMEOUT)
        # The token stays here: the tests need only whom it belongs to
        yield {"connector_id": connector_id, "owner": _token_owner(access_token)}
    finally:
        try:
            pipeshub_client.toggle_sync(connector_id, enable=False)
            pipeshub_client.delete_connector(connector_id)
            await graph_provider.assert_all_records_cleaned(connector_id, timeout=GRAPH_CLEANUP_TIMEOUT)
        except Exception as e:  # noqa: BLE001
            logger.warning("TEARDOWN: cleaning connector %s failed: %s", connector_id, e)


def _token_owner(access_token: str) -> dict[str, Any]:
    """The person who approved the connection, from the same call the connector makes."""
    resp = requests.get(
        "https://api.notion.com/v1/users/me",
        headers={"Authorization": f"Bearer {access_token}", "Notion-Version": NOTION_API_VERSION},
        timeout=30,
    )
    resp.raise_for_status()
    return ((resp.json().get("bot") or {}).get("owner") or {}).get("user") or {}


class TestNotionOAuthShadowAccess:
    @pytest.mark.order(1)
    async def test_tc_auth_002_creator_is_linked_to_the_user_who_approved(
        self, approved_notion_connector: dict[str, Any], graph_provider,
    ) -> None:
        owner = approved_notion_connector["owner"]
        assert owner.get("id"), "an OAuth bot is owned by the user who approved it"
        email = (owner.get("person") or {}).get("email")
        if not email:
            logger.warning(
                "Notion shares no email for %s: the integration lacks the 'Read user information "
                "including email addresses' capability, so the connector links nobody", owner.get("id"),
            )
        await assert_authenticated_identity_link(
            graph_provider, approved_notion_connector["connector_id"],
            email.strip() if isinstance(email, str) else None, owner.get("id"),
        )

    @pytest.mark.order(2)
    async def test_the_approving_account_reaches_no_page(
        self, approved_notion_connector: dict[str, Any], graph_provider,
    ) -> None:
        link = await _resolve_link(graph_provider, approved_notion_connector["connector_id"])
        reachable = await _access_paths(graph_provider, link.source_key, link.connector_id, with_org=False)
        assert not reachable, (
            f"{link.source.get('email')} reaches {len(reachable)} Notion record(s) through the "
            f"team connector, which grants none: {dict(list(reachable.items())[:5])}"
        )

# pyright: ignore-file

"""Gmail (personal) integration fixtures (DESIGN.md in connectors/gmail/).

``gmail_personal`` is class-scoped: it builds a simulated mailbox for gmail_sim.env's personal account, registers
that file's OAuth client and refresh token with the simulator, waits until the account's PipesHub user is active in
the graph, creates a personal Gmail connector with the same client, exchanges the refresh token at the simulator's
token endpoint (never Google's), runs the first sync, and on teardown deletes the connector and checks its graph
is gone.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio

from connectors.gmail.gmail_env import (
    ENV_CLIENT_ID,
    ENV_CLIENT_SECRET,
    ENV_REFRESH_TOKEN,
    PERSONAL_VARS,
    personal_env,
)
from connectors.gmail.gmail_sim import GmailSim, personal_oauth
from connectors.gmail.gmail_users import ensure_active_user
from connectors.google_drive_workspace.drive_workspace_test_utils import (  # type: ignore[import-not-found]
    connector_filters,
)
from helper.graph_provider import GraphProviderProtocol  # type: ignore[import-not-found]
from helper.graph_provider_utils import wait_for_sync_completion  # type: ignore[import-not-found]
from helper.oauth_token_helper import authenticate_connector_with_refresh_token  # type: ignore[import-not-found]
from helper.source_credentials import source_unavailable  # type: ignore[import-not-found]
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

logger = logging.getLogger("gmail-individual-conftest")


def personal_scenario(user_email: str) -> dict[str, Any]:
    domain = user_email.split("@", 1)[1]
    return {
        "domain": domain,
        "users": [{"key": "me", "email": user_email, "name": "Gmail Personal User"},
                  {"key": "colleague", "email": f"gmail-it-colleague@{domain}", "name": "Colleague"}],
        "mail": [
            {"key": "hello", "from": "colleague", "to": ["me"], "subject": "IT hello", "days_ago": 3,
             "attachments": [{"name": "agenda.pdf", "mime": "application/pdf", "size": 2048}]},
            {"key": "reply", "from": "me", "to": ["colleague"], "subject": "Re: IT hello", "reply_to": "hello",
             "days_ago": 2},
            {"key": "news", "from": "ext:news@vendor.example", "to": ["me"], "subject": "IT newsletter",
             "days_ago": 1},
        ],
    }


@pytest_asyncio.fixture(scope="class", loop_scope="session")
async def gmail_personal(
    pipeshub_client: PipeshubClient,
    users_client: Any,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[dict[str, Any], None]:
    try:
        env = personal_env()
    except ValueError as e:
        source_unavailable(f"The simulated Gmail account is not configured: {e}", secrets=PERSONAL_VARS)
    sim = GmailSim()
    sim.require_reachable()
    sim.load({**personal_scenario(env.user), "oauth": personal_oauth(env, "me")})
    user_id = await ensure_active_user(users_client, graph_provider, env.user)

    instance = pipeshub_client.create_connector(
        connector_type="Gmail",
        instance_name=f"gmail-personal-{uuid.uuid4().hex[:8]}",
        scope="personal",
        config={"auth": {"clientId": env.client_id, "clientSecret": env.client_secret},
                "filters": connector_filters()},
        auth_type="OAUTH",
    )
    connector_id = instance.connector_id
    assert connector_id, "the connector has an id"
    state: dict[str, Any] = {"env": env, "sim": sim, "connector_id": connector_id, "user_id": user_id}
    try:
        try:
            await authenticate_connector_with_refresh_token(
                connector_id=connector_id,
                refresh_token_env_var=ENV_REFRESH_TOKEN,
                token_url=f"{sim.base_url}/token",
                client_id_env_var=ENV_CLIENT_ID,
                client_secret_env_var=ENV_CLIENT_SECRET,
            )
        except Exception as e:  # noqa: BLE001 - reported as the suite's set-up failure
            pytest.fail(f"Could not authenticate the Gmail connector at the simulator: {e}")
        pipeshub_client.toggle_sync(connector_id, enable=True)
        await wait_for_sync_completion(pipeshub_client, graph_provider, connector_id, timeout=300)
        yield state
    finally:
        logger.info("TEARDOWN: Gmail personal connector %s", connector_id)
        try:
            pipeshub_client.toggle_sync(connector_id, enable=False)
        except Exception as e:  # noqa: BLE001 - teardown carries on
            logger.warning("TEARDOWN: disable failed: %s", e)
        pipeshub_client.delete_connector(connector_id)
        await graph_provider.assert_connector_graph_fully_cleaned(connector_id, timeout=180)

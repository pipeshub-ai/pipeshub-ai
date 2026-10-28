# pyright: ignore-file

"""Gmail Workspace integration fixtures (DESIGN.md in connectors/gmail/).

``gmail_ws`` is class-scoped: it builds the simulated Workspace (gmail_sim.env's users, and a service account
whose key this run generated), makes sure the test users exist in PipesHub and are active in its graph, creates
a Gmail Workspace connector with that service account, runs the first sync, and on teardown deletes the
connector and checks its graph is gone.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest_asyncio

from connectors.gmail.gmail_env import WORKSPACE_VARS, workspace_env
from connectors.gmail.gmail_sim import GmailSim, workspace_app
from connectors.gmail.gmail_users import ensure_active_user
from connectors.gmail_workspace.gmail_workspace_scenario import workspace_scenario
from connectors.google_drive_workspace.drive_workspace_test_utils import (  # type: ignore[import-not-found]
    connector_filters,
)
from helper.graph_provider import GraphProviderProtocol  # type: ignore[import-not-found]
from helper.graph_provider_utils import wait_for_sync_completion  # type: ignore[import-not-found]
from helper.source_credentials import source_unavailable  # type: ignore[import-not-found]
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

logger = logging.getLogger("gmail-workspace-conftest")


@pytest_asyncio.fixture(scope="class", loop_scope="session")
async def gmail_ws(
    pipeshub_client: PipeshubClient,
    users_client: Any,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[dict[str, Any], None]:
    try:
        env = workspace_env()
    except ValueError as e:
        source_unavailable(f"The simulated Workspace is not configured: {e}", secrets=WORKSPACE_VARS)
    sim = GmailSim()
    sim.require_reachable()

    sim.load({**workspace_scenario(env), "app": workspace_app(env)})
    user_ids = {email: await ensure_active_user(users_client, graph_provider, email)
                for email in (env.test_user, env.second_user)}

    instance = pipeshub_client.create_connector(
        connector_type="Gmail Workspace",
        instance_name=f"gmail-ws-{uuid.uuid4().hex[:8]}",
        scope="team",
        config={"auth": {"adminEmail": env.admin_email, "serviceAccountJson": env.service_account_json},
                "filters": connector_filters()},
        auth_type="CUSTOM",
    )
    connector_id = instance.connector_id
    assert connector_id, "the connector has an id"
    state: dict[str, Any] = {"env": env, "sim": sim, "connector_id": connector_id, "user_ids": user_ids}
    try:
        pipeshub_client.toggle_sync(connector_id, enable=True)
        await wait_for_sync_completion(pipeshub_client, graph_provider, connector_id, timeout=300)
        yield state
    finally:
        logger.info("TEARDOWN: Gmail Workspace connector %s", connector_id)
        try:
            pipeshub_client.toggle_sync(connector_id, enable=False)
        except Exception as e:  # noqa: BLE001 - teardown carries on
            logger.warning("TEARDOWN: disable failed: %s", e)
        pipeshub_client.delete_connector(connector_id)
        await graph_provider.assert_connector_graph_fully_cleaned(connector_id, timeout=180)

# pyright: ignore-file

"""Google Drive personal folder-filter fixtures.

Creates a small Drive tree under the OAuth user via GoogleDriveDataSource, ensures
that user is an active Pipeshub org member, registers a Drive (personal) connector
with ``folder_ids`` filter after injecting OAuth credentials via refresh token,
waits for the seed subtree, then tears down.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any, AsyncGenerator

import pytest
import pytest_asyncio

from helper.assertions import ConnectorAssertions  # type: ignore[import-not-found]
from helper.clients.users_client import UsersClient  # type: ignore[import-not-found]
from helper.graph_provider import GraphProviderProtocol  # type: ignore[import-not-found]
from helper.graph_provider_utils import (  # type: ignore[import-not-found]
    async_poll_until,
    wait_for_sync_completion,
    wait_until_graph_condition,
)
from helper.oauth_token_helper import (  # type: ignore[import-not-found]
    authenticate_connector_with_refresh_token,
)
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

from app.sources.external.google.drive.drive import (  # type: ignore[import-not-found]
    GoogleDriveDataSource,
)
from connectors.google_drive_individual.drive_individual_test_utils import (  # type: ignore[import-not-found]
    ENV_CLIENT_ID,
    ENV_CLIENT_SECRET,
    ENV_REFRESH_TOKEN,
    ENV_TEST_USER,
    GOOGLE_TOKEN_URL,
    build_drive_datasource_from_refresh_token,
    create_folder_filter_fixtures,
    require_drive_individual_env,
)
from connectors.google_drive_workspace.drive_workspace_test_utils import (  # type: ignore[import-not-found]
    delete_drive_folder,
    ensure_pipeshub_user_exists,
)

logger = logging.getLogger("drive-individual-conftest")

_SYNC_TIMEOUT_SEC = int(os.getenv("GOOGLE_DRIVE_INDIVIDUAL_SYNC_TIMEOUT", "300"))
_USER_GRAPH_TIMEOUT_SEC = int(
    os.getenv("GOOGLE_DRIVE_INDIVIDUAL_USER_GRAPH_TIMEOUT", "120")
)

@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def drive_individual_datasource() -> GoogleDriveDataSource:
    """Session-scoped Drive datasource for the OAuth user (full drive scope)."""
    try:
        client_id, client_secret, refresh_token, _test_user = require_drive_individual_env()
    except ValueError as e:
        pytest.skip(str(e))

    try:
        return await build_drive_datasource_from_refresh_token(
            client_id, client_secret, refresh_token
        )
    except Exception as e:
        pytest.fail(f"Refresh token failed to fetch access token: {e}")


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def connector_assertions(graph_provider: GraphProviderProtocol):
    return ConnectorAssertions(graph_provider)


async def _wait_for_active_user_in_graph(
    graph_provider: GraphProviderProtocol,
    email: str,
) -> None:
    """Wait until the Pipeshub user exists in the graph and is not inactive."""

    async def _active() -> dict[str, Any] | None:
        user = await graph_provider.graph_find_user_by_email(email)
        if not user:
            return None
        if user.get("isActive") is False:
            return None
        return user

    await async_poll_until(
        _active,
        timeout=_USER_GRAPH_TIMEOUT_SEC,
        interval=2,
        description=f"active Pipeshub graph user for {email}",
    )


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def drive_individual_connector(
    drive_individual_datasource: GoogleDriveDataSource,
    pipeshub_client: PipeshubClient,
    users_client: UsersClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[dict[str, Any], None]:
    """Module-scoped personal Drive connector with folder_ids filter on seed."""
    client_id = os.environ[ENV_CLIENT_ID].strip()
    client_secret = os.environ[ENV_CLIENT_SECRET].strip()
    test_user = os.environ[ENV_TEST_USER].strip()

    connector_name = f"drive-personal-ff-{uuid.uuid4().hex[:8]}"
    state: dict[str, Any] = {
        "connector_name": connector_name,
        "connector_id": None,
        "test_user_email": test_user,
        "pipeshub_user_id": None,
        "pipeshub_user_created": False,
    }

    fixtures: dict[str, str] = {}
    try:
        user_id, created = ensure_pipeshub_user_exists(users_client, test_user)
        state["pipeshub_user_id"] = user_id
        state["pipeshub_user_created"] = created
        logger.info(
            "SETUP: Pipeshub user %s id=%s created=%s — waiting for graph",
            test_user,
            user_id,
            created,
        )
        await _wait_for_active_user_in_graph(graph_provider, test_user)

        fixtures = await create_folder_filter_fixtures(drive_individual_datasource)
        state.update(fixtures)

        seed_folder_id = fixtures["seed_folder_id"]
        config: dict[str, Any] = {
            "auth": {
                "clientId": client_id,
                "clientSecret": client_secret,
            },
            "filters": {
                "sync": {
                    "values": {
                        "folder_ids": {
                            "operator": "in",
                            "type": "list",
                            "value": [seed_folder_id],
                        }
                    }
                }
            },
        }

        instance = pipeshub_client.create_connector(
            connector_type="Drive",
            instance_name=connector_name,
            scope="personal",
            config=config,
            auth_type="OAUTH",
        )
        assert instance.connector_id, "Connector must have a valid ID"
        connector_id = instance.connector_id
        state["connector_id"] = connector_id
        logger.info(
            "SETUP: Drive personal connector %s with folder_ids=[%s] (user=%s)",
            connector_id,
            seed_folder_id,
            test_user,
        )

        try:
            authenticate_connector_with_refresh_token(
                connector_id=connector_id,
                refresh_token_env_var=ENV_REFRESH_TOKEN,
                token_url=GOOGLE_TOKEN_URL,
                client_id_env_var=ENV_CLIENT_ID,
                client_secret_env_var=ENV_CLIENT_SECRET,
            )
        except Exception as e:
            pytest.fail(f"Failed to authenticate connector with refresh token: {e}")

        pipeshub_client.toggle_sync(connector_id, enable=True)
        await wait_for_sync_completion(
            pipeshub_client,
            graph_provider,
            connector_id,
            timeout=_SYNC_TIMEOUT_SEC,
        )

        child_file_id = fixtures["child_file_id"]

        async def _seed_and_child_present() -> bool:
            seed = await graph_provider.get_record_by_external_id(
                connector_id, seed_folder_id
            )
            child = await graph_provider.get_record_by_external_id(
                connector_id, child_file_id
            )
            return seed is not None and child is not None

        try:
            await wait_until_graph_condition(
                connector_id,
                check=_seed_and_child_present,
                timeout=_SYNC_TIMEOUT_SEC,
                poll_interval=10,
                description=f"seed folder + child.txt in graph for {test_user}",
            )
        except TimeoutError:
            raise TimeoutError(
                f"Timed out waiting for folder-filter seed records. "
                f"connector_id={connector_id} user={test_user} "
                f"seed={seed_folder_id} child={child_file_id}"
            ) from None

        logger.info(
            "SETUP done: seed=%s nested=%s child=%s oos=%s",
            seed_folder_id,
            fixtures["nested_folder_id"],
            child_file_id,
            fixtures["oos_folder_id"],
        )

        yield state
    finally:
        connector_id = state.get("connector_id")
        logger.info("TEARDOWN: cleaning connector %s", connector_id)
        if connector_id:
            try:
                pipeshub_client.toggle_sync(connector_id, enable=False)
            except Exception as e:
                logger.warning("TEARDOWN: disable failed for %s: %s", connector_id, e)
            try:
                pipeshub_client.delete_connector(connector_id)
                pipeshub_client.wait(25)
                cleanup_timeout = int(
                    os.getenv("INTEGRATION_GRAPH_CLEANUP_TIMEOUT", "300")
                )
                await graph_provider.assert_all_records_cleaned(
                    connector_id, timeout=cleanup_timeout
                )
            except Exception as e:
                logger.warning(
                    "TEARDOWN: delete/clean failed for %s: %s", connector_id, e
                )

        # Drive cleanup is fixture-scoped only: delete the IT root folder tree.
        # Even after a cleared folder_ids sync may have indexed other My Drive
        # content into Pipeshub, do not delete non-fixture Drive items here.
        await delete_drive_folder(
            drive_individual_datasource,
            fixtures.get("root_folder_id") or state.get("root_folder_id"),
        )

        # Do not soft-delete GOOGLE_DRIVE_TEST_USER_EMAIL — reusable identity.

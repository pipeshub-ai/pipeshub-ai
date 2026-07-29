# pyright: ignore-file

"""Google Drive Workspace folder-filter fixtures.

Creates a small Drive tree under the test user via GoogleDriveDataSource, ensures that
user is an active Pipeshub org member (create + activate if needed — required because
Drive Workspace syncs Workspace ∩ active Pipeshub users), registers a Drive Workspace
connector with ``folder_ids`` filter, waits for the seed subtree, then tears down.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any, AsyncGenerator, Optional

import pytest
import pytest_asyncio
from google.auth.exceptions import RefreshError  # type: ignore[import-not-found]

from helper.assertions import ConnectorAssertions  # type: ignore[import-not-found]
from helper.clients.users_client import UsersClient  # type: ignore[import-not-found]
from helper.graph_provider import GraphProviderProtocol  # type: ignore[import-not-found]
from helper.graph_provider_utils import (  # type: ignore[import-not-found]
    async_poll_until,
    wait_for_sync_completion,
    wait_until_graph_condition,
)
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

from app.sources.external.google.drive.drive import (  # type: ignore[import-not-found]
    GoogleDriveDataSource,
)
from connectors.google_drive_workspace.drive_workspace_test_utils import (  # type: ignore[import-not-found]
    ENV_ADMIN_EMAIL,
    ENV_SA_JSON,
    ENV_TEST_USER,
    FULL_DRIVE_SCOPE,
    build_drive_datasource,
    create_drive_folder,
    create_drive_text_file,
    create_folder_filter_fixtures,
    create_shared_drive,
    create_shared_drive_folder_filter_fixtures,
    delete_drive_folder,
    delete_shared_drive,
    ensure_pipeshub_user_exists,
    load_service_account_info,
    require_drive_workspace_env,
    wait_until_shared_drives_listed,
)

logger = logging.getLogger("drive-workspace-conftest")

# Full Drive sync lists then filters client-side; keep this generous.
_SYNC_TIMEOUT_SEC = int(os.getenv("GOOGLE_DRIVE_WORKSPACE_SYNC_TIMEOUT", "300"))
_USER_GRAPH_TIMEOUT_SEC = int(os.getenv("GOOGLE_DRIVE_WORKSPACE_USER_GRAPH_TIMEOUT", "120"))


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def drive_workspace_datasource() -> GoogleDriveDataSource:
    """Session-scoped Drive datasource impersonating the test user (full drive scope)."""
    try:
        sa_json, admin_email, test_user = require_drive_workspace_env()
    except ValueError as e:
        pytest.skip(str(e))

    try:
        return await build_drive_datasource(sa_json, admin_email, test_user)
    except Exception as e:
        pytest.skip(f"Failed to build Drive Workspace datasource: {e}")


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def connector_assertions(graph_provider: GraphProviderProtocol):
    return ConnectorAssertions(graph_provider)


async def _wait_for_active_user_in_graph(
    graph_provider: GraphProviderProtocol,
    email: str,
) -> None:
    """Drive team sync reads active users from the graph — wait for entity-events."""

    async def _active() -> dict[str, Any] | None:
        user = await graph_provider.graph_find_user_by_email(email)
        if not user:
            return None
        # Missing isActive is treated as active (matches get_users filter semantics loosely);
        # explicit False means soft-deleted / inactive.
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
async def drive_workspace_connector(
    drive_workspace_datasource: GoogleDriveDataSource,
    pipeshub_client: PipeshubClient,
    users_client: UsersClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[dict[str, Any], None]:
    """Module-scoped connector synced with folder_ids filter on the created seed folder."""
    sa_json = os.environ[ENV_SA_JSON].strip()
    admin_email = os.environ[ENV_ADMIN_EMAIL].strip()
    test_user = os.environ[ENV_TEST_USER].strip()

    connector_name = f"drive-ws-ff-{uuid.uuid4().hex[:8]}"
    state: dict[str, Any] = {
        "connector_name": connector_name,
        "connector_id": None,
        "test_user_email": test_user,
        "admin_email": admin_email,
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

        try:
            fixtures = await create_folder_filter_fixtures(drive_workspace_datasource)
        except RefreshError as e:
            client_id = ""
            try:
                client_id = str(
                    load_service_account_info(sa_json, admin_email).get("client_id") or ""
                )
            except Exception:
                pass
            raise RuntimeError(
                "Google domain-wide delegation failed while creating Drive fixtures "
                f"(impersonating {test_user}). Ensure client_id="
                f"{client_id or '<SA client_id>'} authorizes {FULL_DRIVE_SCOPE} "
                f"in Admin Domain-wide delegation. Original error: {e}"
            ) from e
        state.update(fixtures)

        seed_folder_id = fixtures["seed_folder_id"]
        config: dict[str, Any] = {
            "auth": {
                "adminEmail": admin_email,
                "serviceAccountJson": sa_json,
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
            connector_type="Drive Workspace",
            instance_name=connector_name,
            scope="team",
            config=config,
            auth_type="CUSTOM",
        )
        assert instance.connector_id, "Connector must have a valid ID"
        connector_id = instance.connector_id
        state["connector_id"] = connector_id
        logger.info(
            "SETUP: Drive Workspace connector %s with folder_ids=[%s] (user=%s)",
            connector_id,
            seed_folder_id,
            test_user,
        )

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
                cleanup_timeout = int(os.getenv("INTEGRATION_GRAPH_CLEANUP_TIMEOUT", "300"))
                await graph_provider.assert_all_records_cleaned(
                    connector_id, timeout=cleanup_timeout
                )
            except Exception as e:
                logger.warning("TEARDOWN: delete/clean failed for %s: %s", connector_id, e)

        # Drive cleanup is fixture-scoped only: delete the IT root folder tree.
        # Even after a cleared folder_ids sync may have indexed other My Drive
        # content into Pipeshub, do not delete non-fixture Drive items here.
        await delete_drive_folder(
            drive_workspace_datasource,
            fixtures.get("root_folder_id") or state.get("root_folder_id"),
        )

        # Do not soft-delete GOOGLE_DRIVE_WORKSPACE_TEST_USER_EMAIL. Soft-delete keeps
        # the unique email index, so the next run's create_user hits E11000 while
        # getAllUsers cannot see the row. The Workspace test identity is reusable.


async def _teardown_connector(
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
    connector_id: Optional[str],
) -> None:
    if not connector_id:
        return
    logger.info("TEARDOWN: cleaning connector %s", connector_id)
    try:
        pipeshub_client.toggle_sync(connector_id, enable=False)
    except Exception as e:
        logger.warning("TEARDOWN: disable failed for %s: %s", connector_id, e)
    try:
        pipeshub_client.delete_connector(connector_id)
        pipeshub_client.wait(25)
        cleanup_timeout = int(os.getenv("INTEGRATION_GRAPH_CLEANUP_TIMEOUT", "300"))
        await graph_provider.assert_all_records_cleaned(
            connector_id, timeout=cleanup_timeout
        )
    except Exception as e:
        logger.warning("TEARDOWN: delete/clean failed for %s: %s", connector_id, e)


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def drive_workspace_shared_drives(
    drive_workspace_datasource: GoogleDriveDataSource,
) -> AsyncGenerator[dict[str, str], None]:
    """Create two temporary Shared Drives for the Shared Drive folder-filter suite."""
    try:
        sa_json, admin_email, _test_user = require_drive_workspace_env()
    except ValueError as e:
        pytest.skip(str(e))

    suffix = uuid.uuid4().hex[:8]
    drive_a_id: Optional[str] = None
    drive_b_id: Optional[str] = None
    admin_drive: Optional[GoogleDriveDataSource] = None

    try:
        try:
            drive_a_id = await create_shared_drive(
                drive_workspace_datasource, f"pipeshub-it-sd-a-{suffix}"
            )
            drive_b_id = await create_shared_drive(
                drive_workspace_datasource, f"pipeshub-it-sd-b-{suffix}"
            )
        except Exception as e:
            pytest.skip(
                "Failed to create Shared Drives for folder-filter ITs. Ensure the "
                f"test user can create Shared Drives in Workspace admin. Error: {e}"
            )

        # Per-user sync uses member drives.list (no domain admin). Wait until both
        # drives appear there before fixtures/sync, or the first sync can finish
        # with "No shared drives found" and 0 records.
        await wait_until_shared_drives_listed(
            drive_workspace_datasource, [drive_a_id, drive_b_id]
        )

        try:
            admin_drive = await build_drive_datasource(
                sa_json, admin_email, admin_email
            )
        except Exception as e:
            logger.warning(
                "Could not build admin Drive datasource for Shared Drive teardown: %s",
                e,
            )

        yield {"drive_a_id": drive_a_id, "drive_b_id": drive_b_id}
    finally:
        await delete_shared_drive(
            drive_workspace_datasource, drive_a_id, admin_drive=admin_drive
        )
        await delete_shared_drive(
            drive_workspace_datasource, drive_b_id, admin_drive=admin_drive
        )


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def drive_workspace_shared_drive_connector(
    drive_workspace_datasource: GoogleDriveDataSource,
    drive_workspace_shared_drives: dict[str, str],
    pipeshub_client: PipeshubClient,
    users_client: UsersClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[dict[str, Any], None]:
    """Connector with drive_ids=[A] and folder_ids=[seed] under Shared Drive A."""
    sa_json = os.environ[ENV_SA_JSON].strip()
    admin_email = os.environ[ENV_ADMIN_EMAIL].strip()
    test_user = os.environ[ENV_TEST_USER].strip()
    drive_a_id = drive_workspace_shared_drives["drive_a_id"]
    drive_b_id = drive_workspace_shared_drives["drive_b_id"]

    connector_name = f"drive-ws-sd-ff-{uuid.uuid4().hex[:8]}"
    state: dict[str, Any] = {
        "connector_name": connector_name,
        "connector_id": None,
        "test_user_email": test_user,
        "admin_email": admin_email,
        "drive_a_id": drive_a_id,
        "drive_b_id": drive_b_id,
    }
    fixtures: dict[str, str] = {}

    try:
        user_id, created = ensure_pipeshub_user_exists(users_client, test_user)
        state["pipeshub_user_id"] = user_id
        state["pipeshub_user_created"] = created
        await _wait_for_active_user_in_graph(graph_provider, test_user)

        try:
            fixtures = await create_shared_drive_folder_filter_fixtures(
                drive_workspace_datasource, drive_a_id, drive_b_id
            )
        except RefreshError as e:
            client_id = ""
            try:
                client_id = str(
                    load_service_account_info(sa_json, admin_email).get("client_id") or ""
                )
            except Exception:
                pass
            raise RuntimeError(
                "Google domain-wide delegation failed while creating Shared Drive "
                f"fixtures (impersonating {test_user}). Ensure client_id="
                f"{client_id or '<SA client_id>'} authorizes {FULL_DRIVE_SCOPE} "
                f"in Admin Domain-wide delegation. Original error: {e}"
            ) from e
        state.update(fixtures)

        seed_folder_id = fixtures["seed_folder_id"]
        config: dict[str, Any] = {
            "auth": {
                "adminEmail": admin_email,
                "serviceAccountJson": sa_json,
            },
            "filters": {
                "sync": {
                    "values": {
                        "drive_ids": {
                            "operator": "in",
                            "type": "list",
                            "value": [drive_a_id],
                        },
                        "folder_ids": {
                            "operator": "in",
                            "type": "list",
                            "value": [seed_folder_id],
                        },
                    }
                }
            },
        }

        instance = pipeshub_client.create_connector(
            connector_type="Drive Workspace",
            instance_name=connector_name,
            scope="team",
            config=config,
            auth_type="CUSTOM",
        )
        assert instance.connector_id, "Connector must have a valid ID"
        connector_id = instance.connector_id
        state["connector_id"] = connector_id
        logger.info(
            "SETUP: Shared Drive connector %s drive_ids=[%s] folder_ids=[%s]",
            connector_id,
            drive_a_id,
            seed_folder_id,
        )

        # Re-check member visibility immediately before the first sync (same API
        # the connector uses to discover Shared Drives for the test user).
        await wait_until_shared_drives_listed(
            drive_workspace_datasource, [drive_a_id]
        )

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
                description="Shared Drive seed folder + child.txt in graph",
            )
        except TimeoutError:
            raise TimeoutError(
                f"Timed out waiting for Shared Drive folder-filter seed records. "
                f"connector_id={connector_id} seed={seed_folder_id} child={child_file_id}"
            ) from None

        yield state
    finally:
        await _teardown_connector(
            pipeshub_client, graph_provider, state.get("connector_id")
        )


@pytest_asyncio.fixture(scope="function", loop_scope="session")
async def drive_workspace_shared_drive_root_connector(
    drive_workspace_datasource: GoogleDriveDataSource,
    drive_workspace_shared_drives: dict[str, str],
    pipeshub_client: PipeshubClient,
    users_client: UsersClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[dict[str, Any], None]:
    """Short-lived connector with folder_ids=[Shared Drive A root id]."""
    sa_json = os.environ[ENV_SA_JSON].strip()
    admin_email = os.environ[ENV_ADMIN_EMAIL].strip()
    test_user = os.environ[ENV_TEST_USER].strip()
    drive_a_id = drive_workspace_shared_drives["drive_a_id"]

    connector_name = f"drive-ws-sd-root-{uuid.uuid4().hex[:8]}"
    state: dict[str, Any] = {
        "connector_name": connector_name,
        "connector_id": None,
        "drive_a_id": drive_a_id,
        "test_user_email": test_user,
    }

    try:
        ensure_pipeshub_user_exists(users_client, test_user)
        await _wait_for_active_user_in_graph(graph_provider, test_user)

        root_folder_name = f"root-seed-{uuid.uuid4().hex[:6]}"
        root_folder_id = await create_drive_folder(
            drive_workspace_datasource, root_folder_name, parent_id=drive_a_id
        )
        root_file_id = await create_drive_text_file(
            drive_workspace_datasource,
            "root-child.txt",
            parent_id=root_folder_id,
            content="shared drive root seed it\n",
        )
        state.update(
            {
                "root_folder_id": root_folder_id,
                "root_folder_name": root_folder_name,
                "root_file_id": root_file_id,
                "root_file_name": "root-child.txt",
            }
        )

        config: dict[str, Any] = {
            "auth": {
                "adminEmail": admin_email,
                "serviceAccountJson": sa_json,
            },
            "filters": {
                "sync": {
                    "values": {
                        "drive_ids": {
                            "operator": "in",
                            "type": "list",
                            "value": [drive_a_id],
                        },
                        "folder_ids": {
                            "operator": "in",
                            "type": "list",
                            "value": [drive_a_id],
                        },
                    }
                }
            },
        }

        instance = pipeshub_client.create_connector(
            connector_type="Drive Workspace",
            instance_name=connector_name,
            scope="team",
            config=config,
            auth_type="CUSTOM",
        )
        assert instance.connector_id, "Connector must have a valid ID"
        connector_id = instance.connector_id
        state["connector_id"] = connector_id
        logger.info(
            "SETUP: Shared Drive root-seed connector %s folder_ids=[%s]",
            connector_id,
            drive_a_id,
        )

        await wait_until_shared_drives_listed(
            drive_workspace_datasource, [drive_a_id]
        )

        pipeshub_client.toggle_sync(connector_id, enable=True)
        await wait_for_sync_completion(
            pipeshub_client,
            graph_provider,
            connector_id,
            timeout=_SYNC_TIMEOUT_SEC,
        )

        async def _root_file_present() -> bool:
            return (
                await graph_provider.get_record_by_external_id(
                    connector_id, root_file_id
                )
                is not None
            )

        try:
            await wait_until_graph_condition(
                connector_id,
                check=_root_file_present,
                timeout=_SYNC_TIMEOUT_SEC,
                poll_interval=10,
                description="Shared Drive root-seed top-level file in graph",
            )
        except TimeoutError:
            raise TimeoutError(
                f"Timed out waiting for Shared Drive root-seed file. "
                f"connector_id={connector_id} drive={drive_a_id} file={root_file_id}"
            ) from None

        yield state
    finally:
        await _teardown_connector(
            pipeshub_client, graph_provider, state.get("connector_id")
        )

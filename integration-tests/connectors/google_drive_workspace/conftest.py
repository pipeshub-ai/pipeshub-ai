# pyright: ignore-file

"""Google Drive Workspace integration fixtures.

Class-scoped connectors (main IT module, ordered suites):

* ``drive_workspace_entity_connector`` — unfiltered sync for
  ``TestDriveWorkspaceEntitySync``. Tears down the connector **and** its Drive
  source tree / entity Shared Drives when that class finishes.
* ``drive_workspace_ff_connector`` — ``folder_ids=[seed]`` for
  ``TestDriveWorkspaceFolderFilter``. Creates a fresh Drive fixture tree after
  entity teardown.
* ``drive_workspace_ext_connector`` — ``folder_ids=[seed]`` + ``file_extensions``
  for ``TestDriveWorkspaceExtensionFilter``.
* ``drive_workspace_blocks_connector`` — ``folder_ids=[seed]`` with the five
  google-drive-it-files samples (Office converted to Docs/Sheets/Slides) for
  ``TestDriveWorkspaceBlocks`` (same main IT module, order 30–34).
* ``drive_blocks_stream_user`` — ``GOOGLE_DRIVE_WORKSPACE_TEST_USER_EMAIL``
  logged in to PipesHub, so streaming passes the ACL on Drive-owned samples.

Module-scoped Shared Drive fixtures (shared-drive IT module):

* ``drive_workspace_shared_drives`` / ``drive_workspace_shared_drive_connector``
* ``drive_workspace_drive_ids_connector`` — pure ``drive_ids=[A]`` (no folder_ids)
"""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, AsyncGenerator, Iterator, Optional

import pytest
import pytest_asyncio

from helper.source_credentials import source_unavailable
from google.auth.exceptions import RefreshError  # type: ignore[import-not-found]

from helper.assertions import ConnectorAssertions  # type: ignore[import-not-found]
from helper.clients.users_client import UsersClient  # type: ignore[import-not-found]
from helper.graph_provider import GraphProviderProtocol  # type: ignore[import-not-found]
from helper.graph_provider_utils import (  # type: ignore[import-not-found]
    async_poll_until,
    wait_for_sync_completion,
    wait_until_graph_condition,
)
from helper.second_user import (  # type: ignore[import-not-found]
    SecondUser,
    log_in_existing_user,
    log_out_existing_user,
)
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

from app.sources.external.google.drive.drive import (  # type: ignore[import-not-found]
    GoogleDriveDataSource,
)
from app.config.constants.arangodb import MimeTypes  # type: ignore[import-not-found]

from connectors.google_drive_workspace.drive_workspace_test_utils import (  # type: ignore[import-not-found]
    ENV_ADMIN_EMAIL,
    ENV_SA_JSON,
    ENV_SECOND_USER,
    ENV_TEST_USER,
    FULL_DRIVE_SCOPE,
    build_admin_directory_client,
    build_drive_datasource,
    connector_filters,
    create_drive_blocks_fixtures,
    create_drive_google_file,
    create_drive_ids_filter_fixtures,
    create_drive_text_file,
    create_extension_filter_fixtures,
    create_folder_filter_fixtures,
    create_lifecycle_fixtures,
    create_permission_fixtures,
    create_shared_drive,
    create_shared_drive_folder_filter_fixtures,
    create_shared_drive_lifecycle_fixtures,
    delete_drive_folder,
    delete_shared_drive,
    delete_stale_it_shared_drives,
    ensure_pipeshub_user_exists,
    find_reference_directory_group,
    get_drive_directory_user,
    load_service_account_info,
    pick_entity_sample_files,
    require_drive_workspace_env,
    wait_until_drive_files_listed,
    wait_until_shared_drive_files_listed,
    resolve_my_drive_root_id,
    sync_until,
    upload_drive_blocks_samples,
    upload_drive_local_file,
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
        source_unavailable(f"The Google Workspace tenant this suite syncs from is not configured. {e}")

    try:
        drive = await build_drive_datasource(sa_json, admin_email, test_user)
    except Exception as e:
        source_unavailable(f"Could not reach Google Drive with the configured Workspace credentials: {e}")

    try:
        await delete_stale_it_shared_drives(
            await build_drive_datasource(sa_json, admin_email, admin_email)
        )
    except Exception as e:
        logger.warning("Could not sweep stale IT Shared Drives: %s", e)
    return drive


@pytest.fixture(scope="session")
def drive_workspace_admin_datasource() -> Any:
    """Session-scoped Admin Directory client (DWD as Workspace admin)."""
    try:
        sa_json, admin_email, _test_user = require_drive_workspace_env()
    except ValueError as e:
        source_unavailable(f"The Google Workspace tenant this suite syncs from is not configured. {e}")

    try:
        return build_admin_directory_client(sa_json, admin_email)
    except Exception as e:
        source_unavailable(f"Could not reach the Admin Directory API with the configured Workspace credentials: {e}")


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


def _dwd_failure_message(sa_json: str, admin_email: str, test_user: str, err: Exception) -> str:
    client_id = ""
    try:
        client_id = str(
            load_service_account_info(sa_json, admin_email).get("client_id") or ""
        )
    except Exception:
        pass
    return (
        "Google domain-wide delegation failed while creating Drive fixtures "
        f"(impersonating {test_user}). Ensure client_id="
        f"{client_id or '<SA client_id>'} authorizes {FULL_DRIVE_SCOPE} "
        f"in Admin Domain-wide delegation. Original error: {err}"
    )


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


async def _populate_directory_user_state(
    state: dict[str, Any],
    *,
    drive: GoogleDriveDataSource,
    admin_directory: Any,
    test_user: str,
) -> None:
    my_drive_root_id = await resolve_my_drive_root_id(drive)
    state["my_drive_root_id"] = my_drive_root_id
    state["shared_with_me_group_id"] = f"0S:{test_user}"

    dir_user = await get_drive_directory_user(admin_directory, test_user)
    state["test_user_source_id"] = str(dir_user.get("id") or "")
    name_info = dir_user.get("name") or {}
    full_name = (
        name_info.get("fullName")
        or f"{name_info.get('givenName', '')} {name_info.get('familyName', '')}".strip()
        or test_user
    )
    state["test_user_full_name"] = full_name

    ref_group = await find_reference_directory_group(admin_directory)
    if ref_group:
        state["reference_group_email"] = ref_group["email"]
        state["reference_group_name"] = ref_group["name"]
    else:
        state["reference_group_email"] = None
        state["reference_group_name"] = None


def _auth_config(admin_email: str, sa_json: str) -> dict[str, Any]:
    return {
        "adminEmail": admin_email,
        "serviceAccountJson": sa_json,
    }


@pytest_asyncio.fixture(scope="class", loop_scope="session")
async def drive_workspace_entity_connector(
    drive_workspace_datasource: GoogleDriveDataSource,
    drive_workspace_admin_datasource: Any,
    sample_data_root: Path,
    pipeshub_client: PipeshubClient,
    users_client: UsersClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[dict[str, Any], None]:
    """Class-scoped unfiltered connector for ``TestDriveWorkspaceEntitySync``.

    No ``folder_ids`` filter — syncs the full Drive Workspace scope for active
    Pipeshub users. Drive fixture tree + entity Shared Drives are deleted in
    teardown so the folder-filter suite starts clean.
    """
    sa_json = os.environ[ENV_SA_JSON].strip()
    admin_email = os.environ[ENV_ADMIN_EMAIL].strip()
    test_user = os.environ[ENV_TEST_USER].strip()

    connector_name = f"drive-ws-entity-{uuid.uuid4().hex[:8]}"
    state: dict[str, Any] = {
        "connector_name": connector_name,
        "connector_id": None,
        "test_user_email": test_user,
        "admin_email": admin_email,
        "pipeshub_user_id": None,
        "pipeshub_user_created": False,
        "entity_shared_drives": [],
        "entity_sample_files": [],
    }

    fixtures: dict[str, str] = {}
    entity_shared_drive_ids: list[str] = []
    admin_drive: Optional[GoogleDriveDataSource] = None
    try:
        user_id, created = ensure_pipeshub_user_exists(users_client, test_user)
        state["pipeshub_user_id"] = user_id
        state["pipeshub_user_created"] = created
        logger.info(
            "SETUP(entity): Pipeshub user %s id=%s created=%s — waiting for graph",
            test_user,
            user_id,
            created,
        )
        await _wait_for_active_user_in_graph(graph_provider, test_user)

        try:
            fixtures = await create_folder_filter_fixtures(drive_workspace_datasource)
        except RefreshError as e:
            raise RuntimeError(
                _dwd_failure_message(sa_json, admin_email, test_user, e)
            ) from e
        state.update(fixtures)

        sd_suffix = uuid.uuid4().hex[:8]
        entity_shared_drives: list[dict[str, str]] = []
        try:
            for idx in range(1, 4):
                sd_name = f"phit-entity-sd-{sd_suffix}-{idx}"
                sd_id = await create_shared_drive(drive_workspace_datasource, sd_name)
                entity_shared_drives.append({"id": sd_id, "name": sd_name})
                entity_shared_drive_ids.append(sd_id)
        except Exception as e:
            raise RuntimeError(
                "Failed to create entity Shared Drives for Drive Workspace ITs. "
                f"Ensure the test user can create Shared Drives. Error: {e}"
            ) from e
        state["entity_shared_drives"] = entity_shared_drives
        await wait_until_shared_drives_listed(
            drive_workspace_datasource, entity_shared_drive_ids
        )

        try:
            admin_drive = await build_drive_datasource(
                sa_json, admin_email, admin_email
            )
        except Exception as e:
            logger.warning(
                "Could not build admin Drive datasource for entity Shared Drive teardown: %s",
                e,
            )

        nested_folder_id = fixtures["nested_folder_id"]
        sample_paths = pick_entity_sample_files(sample_data_root, n=2)
        entity_sample_files: list[dict[str, str]] = []
        for local_path in sample_paths:
            sample_id = await upload_drive_local_file(
                drive_workspace_datasource,
                local_path,
                nested_folder_id,
                name=local_path.name,
            )
            entity_sample_files.append(
                {
                    "id": sample_id,
                    "name": local_path.name,
                    "local_rel": str(local_path.relative_to(sample_data_root)),
                }
            )
        state["entity_sample_files"] = entity_sample_files

        await _populate_directory_user_state(
            state,
            drive=drive_workspace_datasource,
            admin_directory=drive_workspace_admin_datasource,
            test_user=test_user,
        )

        # Unfiltered sync — no folder_ids (entity / share / SWM coverage).
        config: dict[str, Any] = {
            "auth": _auth_config(admin_email, sa_json),
            "filters": connector_filters(),
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
            "SETUP(entity): Drive Workspace connector %s unfiltered (user=%s) "
            "entity_shared_drives=%s entity_samples=%s",
            connector_id,
            test_user,
            [d["id"] for d in entity_shared_drives],
            [s["id"] for s in entity_sample_files],
        )

        seed_folder_id = fixtures["seed_folder_id"]
        child_file_id = fixtures["child_file_id"]
        sample_ids = [s["id"] for s in entity_sample_files]

        await wait_until_drive_files_listed(
            drive_workspace_datasource,
            [seed_folder_id, fixtures["nested_folder_id"], child_file_id, *sample_ids],
        )

        pipeshub_client.toggle_sync(connector_id, enable=True)
        await wait_for_sync_completion(
            pipeshub_client,
            graph_provider,
            connector_id,
            min_records=1,
            timeout=_SYNC_TIMEOUT_SEC,
        )

        async def _entity_graph_ready() -> bool:
            seed = await graph_provider.get_record_by_external_id(
                connector_id, seed_folder_id
            )
            child = await graph_provider.get_record_by_external_id(
                connector_id, child_file_id
            )
            if seed is None or child is None:
                return False
            for sid in sample_ids:
                record = await graph_provider.get_record_by_external_id(
                    connector_id, sid
                )
                if record is None:
                    return False
            for drive_id in entity_shared_drive_ids:
                record_group = await graph_provider.get_record_group_by_external_id(
                    connector_id, drive_id
                )
                if record_group is None:
                    return False
            return True

        try:
            if not await _entity_graph_ready():
                await sync_until(
                    pipeshub_client,
                    graph_provider,
                    connector_id,
                    _entity_graph_ready,
                    description=(
                        f"entity records + {len(entity_shared_drive_ids)} Shared Drive "
                        f"RecordGroups in graph for {test_user}"
                    ),
                )
        except TimeoutError:
            raise TimeoutError(
                f"Timed out waiting for entity records and Shared Drive RecordGroups. "
                f"connector_id={connector_id} user={test_user} "
                f"seed={seed_folder_id} child={child_file_id} samples={sample_ids} "
                f"shared_drives={entity_shared_drive_ids}"
            ) from None

        logger.info(
            "SETUP(entity) done: seed=%s nested=%s child=%s oos=%s samples=%s shared_drives=%s",
            seed_folder_id,
            fixtures["nested_folder_id"],
            child_file_id,
            fixtures["oos_folder_id"],
            sample_ids,
            entity_shared_drive_ids,
        )

        yield state
    finally:
        await _teardown_connector(
            pipeshub_client, graph_provider, state.get("connector_id")
        )

        # Tear down entity Drive source so FF suite does not inherit mutated trees
        # (delta files, moves into Shared Drives, etc.).
        await delete_drive_folder(
            drive_workspace_datasource,
            fixtures.get("root_folder_id") or state.get("root_folder_id"),
        )
        for sd_id in entity_shared_drive_ids:
            await delete_shared_drive(
                drive_workspace_datasource, sd_id, admin_drive=admin_drive
            )

        # Do not soft-delete GOOGLE_DRIVE_WORKSPACE_TEST_USER_EMAIL — reusable identity.


@pytest_asyncio.fixture(scope="class", loop_scope="session")
async def drive_workspace_ff_connector(
    drive_workspace_datasource: GoogleDriveDataSource,
    drive_workspace_admin_datasource: Any,
    pipeshub_client: PipeshubClient,
    users_client: UsersClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[dict[str, Any], None]:
    """Class-scoped connector with ``folder_ids=[seed]`` for the FF suite.

    Creates a fresh Drive fixture tree (entity suite already deleted its source).
    """
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
            "SETUP(ff): Pipeshub user %s id=%s created=%s — waiting for graph",
            test_user,
            user_id,
            created,
        )
        await _wait_for_active_user_in_graph(graph_provider, test_user)

        try:
            fixtures = await create_folder_filter_fixtures(drive_workspace_datasource)
        except RefreshError as e:
            raise RuntimeError(
                _dwd_failure_message(sa_json, admin_email, test_user, e)
            ) from e
        state.update(fixtures)

        await _populate_directory_user_state(
            state,
            drive=drive_workspace_datasource,
            admin_directory=drive_workspace_admin_datasource,
            test_user=test_user,
        )

        seed_folder_id = fixtures["seed_folder_id"]
        config: dict[str, Any] = {
            "auth": _auth_config(admin_email, sa_json),
            "filters": connector_filters(
                sync_values={
                    "folder_ids": {
                        "operator": "in",
                        "type": "list",
                        "value": [seed_folder_id],
                    }
                }
            ),
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
            "SETUP(ff): Drive Workspace connector %s with folder_ids=[%s] (user=%s)",
            connector_id,
            seed_folder_id,
            test_user,
        )

        child_file_id = fixtures["child_file_id"]

        # My Drive is enumerated through the user-corpus files.list, whose index lags the
        # fixture creates above; sync before it settles and the tree is simply absent.
        await wait_until_drive_files_listed(
            drive_workspace_datasource,
            [seed_folder_id, fixtures["nested_folder_id"], child_file_id],
        )

        pipeshub_client.toggle_sync(connector_id, enable=True)
        await wait_for_sync_completion(
            pipeshub_client,
            graph_provider,
            connector_id,
            min_records=1,
            timeout=_SYNC_TIMEOUT_SEC,
        )

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
                description=(
                    f"FF seed folder + child.txt in graph for {test_user}"
                ),
            )
        except TimeoutError:
            raise TimeoutError(
                f"Timed out waiting for folder-filter seed records. "
                f"connector_id={connector_id} user={test_user} "
                f"seed={seed_folder_id} child={child_file_id}"
            ) from None

        logger.info(
            "SETUP(ff) done: seed=%s nested=%s child=%s oos=%s",
            seed_folder_id,
            fixtures["nested_folder_id"],
            child_file_id,
            fixtures["oos_folder_id"],
        )

        yield state
    finally:
        await _teardown_connector(
            pipeshub_client, graph_provider, state.get("connector_id")
        )
        await delete_drive_folder(
            drive_workspace_datasource,
            fixtures.get("root_folder_id") or state.get("root_folder_id"),
        )


@pytest_asyncio.fixture(scope="class", loop_scope="session")
async def drive_workspace_blocks_connector(
    drive_workspace_datasource: GoogleDriveDataSource,
    drive_workspace_admin_datasource: Any,
    drive_blocks_sample_root: Path,
    pipeshub_client: PipeshubClient,
    users_client: UsersClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[dict[str, Any], None]:
    """Class-scoped connector for blocks snapshot ITs.

    Uploads the five google-drive-it-files samples under ``seed`` (Office files
    converted to Docs/Sheets/Slides), syncs with ``folder_ids=[seed]``, and
    waits until all five file records exist in the graph. Auto-indexing is left
    on so the same five records also cover the live pipeline
    (``TC-DRIVE-IDX-*``); the snapshot tests parse in-process regardless.
    """
    sa_json = os.environ[ENV_SA_JSON].strip()
    admin_email = os.environ[ENV_ADMIN_EMAIL].strip()
    test_user = os.environ[ENV_TEST_USER].strip()

    connector_name = f"drive-ws-blocks-{uuid.uuid4().hex[:8]}"
    state: dict[str, Any] = {
        "connector_name": connector_name,
        "connector_id": None,
        "test_user_email": test_user,
        "admin_email": admin_email,
        "pipeshub_user_id": None,
        "pipeshub_user_created": False,
        "blocks_files": [],
        "blocks_by_kind": {},
    }

    fixtures: dict[str, Any] = {}
    try:
        user_id, created = ensure_pipeshub_user_exists(users_client, test_user)
        state["pipeshub_user_id"] = user_id
        state["pipeshub_user_created"] = created
        logger.info(
            "SETUP(blocks): Pipeshub user %s id=%s created=%s — waiting for graph",
            test_user,
            user_id,
            created,
        )
        await _wait_for_active_user_in_graph(graph_provider, test_user)

        try:
            fixtures = await create_drive_blocks_fixtures(drive_workspace_datasource)
        except RefreshError as e:
            raise RuntimeError(
                _dwd_failure_message(sa_json, admin_email, test_user, e)
            ) from e
        state.update(fixtures)

        seed_folder_id = fixtures["seed_folder_id"]
        try:
            blocks_files = await upload_drive_blocks_samples(
                drive_workspace_datasource,
                seed_folder_id,
                drive_blocks_sample_root,
            )
        except RefreshError as e:
            raise RuntimeError(
                _dwd_failure_message(sa_json, admin_email, test_user, e)
            ) from e
        state["blocks_files"] = blocks_files
        state["blocks_by_kind"] = {f["kind"]: f for f in blocks_files}

        await _populate_directory_user_state(
            state,
            drive=drive_workspace_datasource,
            admin_directory=drive_workspace_admin_datasource,
            test_user=test_user,
        )

        config: dict[str, Any] = {
            "auth": _auth_config(admin_email, sa_json),
            "filters": connector_filters(
                sync_values={
                    "folder_ids": {
                        "operator": "in",
                        "type": "list",
                        "value": [seed_folder_id],
                    }
                },
                manual_indexing=False,
            ),
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
            "SETUP(blocks): Drive Workspace connector %s with folder_ids=[%s] "
            "(%d sample files, user=%s)",
            connector_id,
            seed_folder_id,
            len(blocks_files),
            test_user,
        )

        sample_ids = [f["id"] for f in blocks_files]
        await wait_until_drive_files_listed(
            drive_workspace_datasource, [seed_folder_id, *sample_ids]
        )

        pipeshub_client.toggle_sync(connector_id, enable=True)
        await wait_for_sync_completion(
            pipeshub_client,
            graph_provider,
            connector_id,
            min_records=1,
            timeout=_SYNC_TIMEOUT_SEC,
        )

        async def _all_samples_present() -> bool:
            for file_id in sample_ids:
                rec = await graph_provider.get_record_by_external_id(
                    connector_id, file_id
                )
                if rec is None:
                    return False
            return True

        try:
            await wait_until_graph_condition(
                connector_id,
                check=_all_samples_present,
                timeout=_SYNC_TIMEOUT_SEC,
                poll_interval=10,
                description=(
                    f"blocks seed + {len(sample_ids)} sample files in graph "
                    f"for {test_user}"
                ),
            )
        except TimeoutError:
            raise TimeoutError(
                f"Timed out waiting for blocks sample records. "
                f"connector_id={connector_id} user={test_user} "
                f"seed={seed_folder_id} samples={sample_ids}"
            ) from None

        logger.info(
            "SETUP(blocks) done: seed=%s samples=%s",
            seed_folder_id,
            {f["kind"]: f["id"] for f in blocks_files},
        )

        yield state
    finally:
        await _teardown_connector(
            pipeshub_client, graph_provider, state.get("connector_id")
        )
        await delete_drive_folder(
            drive_workspace_datasource,
            fixtures.get("root_folder_id") or state.get("root_folder_id"),
        )


@pytest.fixture(scope="class")
def drive_blocks_stream_user(
    pipeshub_client: PipeshubClient,
    drive_workspace_blocks_connector: dict[str, Any],
) -> Iterator[SecondUser]:
    """The Drive test user logged in to PipesHub: streaming checks the record's owner ACL."""
    user = log_in_existing_user(
        pipeshub_client,
        drive_workspace_blocks_connector["pipeshub_user_id"],
        drive_workspace_blocks_connector["test_user_email"],
    )
    try:
        yield user
    finally:
        log_out_existing_user(pipeshub_client, user)


@pytest_asyncio.fixture(loop_scope="session")
async def drive_workspace_connector_factory(
    drive_workspace_datasource: GoogleDriveDataSource,
    pipeshub_client: PipeshubClient,
    users_client: UsersClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[Callable[..., Awaitable[str]], None]:
    """Create Drive Workspace connectors inside a test; every one is torn down afterwards.

    For tests whose subject is a connector's own filter history (unfiltered → filtered,
    full resync under a filter, a seed that disappears), where one shared connector
    would carry state from test to test.

    ``await factory(name_prefix=..., sync_values=..., listed_ids=[...])`` returns the
    connector id after its first sync settles. ``listed_ids`` are waited for in the
    test user's Drive listing first, as every fixture here does.
    """
    sa_json, admin_email, test_user = require_drive_workspace_env()
    ensure_pipeshub_user_exists(users_client, test_user)
    await _wait_for_active_user_in_graph(graph_provider, test_user)
    created: list[str] = []

    async def _create(
        *,
        name_prefix: str,
        sync_values: Optional[dict[str, Any]] = None,
        manual_indexing: bool = True,
        listed_ids: Sequence[str] = (),
    ) -> str:
        instance = pipeshub_client.create_connector(
            connector_type="Drive Workspace",
            instance_name=f"{name_prefix}-{uuid.uuid4().hex[:8]}",
            scope="team",
            config={
                "auth": _auth_config(admin_email, sa_json),
                "filters": connector_filters(sync_values=sync_values, manual_indexing=manual_indexing),
            },
            auth_type="CUSTOM",
        )
        assert instance.connector_id, "Connector must have a valid ID"
        created.append(instance.connector_id)
        if listed_ids:
            await wait_until_drive_files_listed(drive_workspace_datasource, list(listed_ids))
        pipeshub_client.toggle_sync(instance.connector_id, enable=True)
        # Drive's search index can drop an item the listing above just returned. The
        # first full listing then misses it, and incremental syncs never see it because
        # it predates the saved page token, so only a full resync picks it up.
        for attempt in (1, 2):
            try:
                await wait_for_sync_completion(
                    pipeshub_client, graph_provider, instance.connector_id,
                    min_records=1, timeout=_SYNC_TIMEOUT_SEC,
                )
                return instance.connector_id
            except AssertionError:
                logger.warning(
                    "Sync of %s found no records; full resync (attempt %d)",
                    instance.connector_id, attempt,
                )
                pipeshub_client.resync_connector(instance.connector_id, full_sync=True)
        await wait_for_sync_completion(
            pipeshub_client, graph_provider, instance.connector_id,
            min_records=1, timeout=_SYNC_TIMEOUT_SEC,
        )
        return instance.connector_id

    try:
        yield _create
    finally:
        for connector_id in created:
            await _teardown_connector(pipeshub_client, graph_provider, connector_id)


LIFECYCLE_FILE_KEYS = (
    "trash_file_id",
    "doomed_a_id",
    "doomed_b_id",
    "renamable_child_id",
    "dup_first_id",
    "dup_second_id",
    "share_out_file_id",
)
LIFECYCLE_FOLDER_KEYS = ("doomed_folder_id", "doomed_sub_id", "renamable_folder_id", "dup_folder_id")


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def drive_workspace_lifecycle_connector(
    drive_workspace_datasource: GoogleDriveDataSource,
    pipeshub_client: PipeshubClient,
    users_client: UsersClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[dict[str, Any], None]:
    """Connector scoped to a fresh lifecycle tree (``create_lifecycle_fixtures``), indexing on.

    Indexing stays on because the trash test has to show the record's embeddings
    leave the vector store, not just its graph node.
    """
    sa_json, admin_email, test_user = require_drive_workspace_env()
    state: dict[str, Any] = {
        "connector_id": None,
        "test_user_email": test_user,
        "admin_email": admin_email,
    }
    fixtures: dict[str, str] = {}
    try:
        user_id, _ = ensure_pipeshub_user_exists(users_client, test_user)
        state["pipeshub_user_id"] = user_id
        await _wait_for_active_user_in_graph(graph_provider, test_user)

        try:
            fixtures = await create_lifecycle_fixtures(drive_workspace_datasource)
        except RefreshError as e:
            raise RuntimeError(_dwd_failure_message(sa_json, admin_email, test_user, e)) from e
        state.update(fixtures)
        root_id = fixtures["root_folder_id"]
        item_ids = [fixtures[k] for k in (*LIFECYCLE_FOLDER_KEYS, *LIFECYCLE_FILE_KEYS)]

        instance = pipeshub_client.create_connector(
            connector_type="Drive Workspace",
            instance_name=f"drive-ws-lc-{uuid.uuid4().hex[:8]}",
            scope="team",
            config={
                "auth": _auth_config(admin_email, sa_json),
                "filters": connector_filters(
                    sync_values={
                        "folder_ids": {"operator": "in", "type": "list", "value": [root_id]}
                    },
                    manual_indexing=False,
                ),
            },
            auth_type="CUSTOM",
        )
        assert instance.connector_id, "Connector must have a valid ID"
        connector_id = instance.connector_id
        state["connector_id"] = connector_id

        await wait_until_drive_files_listed(drive_workspace_datasource, [root_id, *item_ids])
        pipeshub_client.toggle_sync(connector_id, enable=True)
        await wait_for_sync_completion(
            pipeshub_client, graph_provider, connector_id,
            min_records=len(item_ids), timeout=_SYNC_TIMEOUT_SEC,
        )

        async def _all_present() -> bool:
            for external_id in item_ids:
                if await graph_provider.get_record_by_external_id(connector_id, external_id) is None:
                    return False
            return True

        if not await _all_present():
            await sync_until(
                pipeshub_client, graph_provider, connector_id, _all_present,
                description=f"all {len(item_ids)} lifecycle fixture items in graph",
            )
        yield state
    finally:
        await _teardown_connector(pipeshub_client, graph_provider, state.get("connector_id"))
        await delete_drive_folder(drive_workspace_datasource, fixtures.get("root_folder_id"))


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def drive_workspace_sd_lifecycle_connector(
    drive_workspace_datasource: GoogleDriveDataSource,
    drive_workspace_admin_datasource: Any,
    pipeshub_client: PipeshubClient,
    users_client: UsersClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[dict[str, Any], None]:
    """A fresh Shared Drive behind a ``drive_ids=[it]`` connector, for rename/delete/group ITs.

    The drive is created here and deleted either by the delete test or in teardown.
    The second user is made active here, not left to whichever module ran before, so the
    group-share tests always see a group member who syncs.
    """
    sa_json, admin_email, test_user = require_drive_workspace_env()
    second_user = os.getenv(ENV_SECOND_USER, "").strip()
    state: dict[str, Any] = {
        "connector_id": None,
        "test_user_email": test_user,
        "second_user_email": second_user or None,
    }
    fixtures: dict[str, str] = {}
    admin_drive: Optional[GoogleDriveDataSource] = None
    try:
        user_id, _ = ensure_pipeshub_user_exists(users_client, test_user)
        state["pipeshub_user_id"] = user_id
        await _wait_for_active_user_in_graph(graph_provider, test_user)
        if second_user:
            ensure_pipeshub_user_exists(users_client, second_user)
            await _wait_for_active_user_in_graph(graph_provider, second_user)
        try:
            fixtures = await create_shared_drive_lifecycle_fixtures(drive_workspace_datasource)
        except RefreshError as e:
            raise RuntimeError(_dwd_failure_message(sa_json, admin_email, test_user, e)) from e
        state.update(fixtures)
        admin_drive = await build_drive_datasource(sa_json, admin_email, admin_email)
        state["admin_drive"] = admin_drive
        ref_group = await find_reference_directory_group(drive_workspace_admin_datasource)
        state["group_email"] = ref_group["email"] if ref_group else None

        drive_id = fixtures["drive_id"]
        item_ids = [fixtures["folder_id"], fixtures["file_id"], fixtures["group_file_id"]]
        instance = pipeshub_client.create_connector(
            connector_type="Drive Workspace",
            instance_name=f"drive-ws-sd-lc-{uuid.uuid4().hex[:8]}",
            scope="team",
            config={
                "auth": _auth_config(admin_email, sa_json),
                "filters": connector_filters(
                    sync_values={"drive_ids": {"operator": "in", "type": "list", "value": [drive_id]}}
                ),
            },
            auth_type="CUSTOM",
        )
        assert instance.connector_id, "Connector must have a valid ID"
        connector_id = instance.connector_id
        state["connector_id"] = connector_id

        await wait_until_shared_drives_listed(drive_workspace_datasource, [drive_id])
        await wait_until_shared_drive_files_listed(drive_workspace_datasource, drive_id, item_ids)
        pipeshub_client.toggle_sync(connector_id, enable=True)
        await wait_for_sync_completion(
            pipeshub_client, graph_provider, connector_id, min_records=1, timeout=_SYNC_TIMEOUT_SEC,
        )

        async def _all_present() -> bool:
            for external_id in item_ids:
                if await graph_provider.get_record_by_external_id(connector_id, external_id) is None:
                    return False
            return True

        if not await _all_present():
            await sync_until(
                pipeshub_client, graph_provider, connector_id, _all_present,
                description="Shared Drive lifecycle items in graph",
            )
        yield state
    finally:
        await _teardown_connector(pipeshub_client, graph_provider, state.get("connector_id"))
        if not state.get("drive_deleted"):
            await delete_shared_drive(
                drive_workspace_datasource, fixtures.get("drive_id"), admin_drive=admin_drive
            )


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def drive_workspace_audit_connector(
    drive_workspace_datasource: GoogleDriveDataSource,
    pipeshub_client: PipeshubClient,
    users_client: UsersClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[dict[str, Any], None]:
    """Two fresh Shared Drives behind one ``drive_ids=[both]`` connector, for the audit fix ITs.

    Drive A holds ``move-me.txt`` and a Google Doc; drive B is empty. Both drives are deleted in teardown.
    """
    sa_json, admin_email, test_user = require_drive_workspace_env()
    second_user = os.getenv(ENV_SECOND_USER, "").strip()
    state: dict[str, Any] = {"connector_id": None, "test_user_email": test_user,
                             "second_user_email": second_user or None}
    drive_ids: list[str] = []
    admin_drive: Optional[GoogleDriveDataSource] = None
    try:
        ensure_pipeshub_user_exists(users_client, test_user)
        await _wait_for_active_user_in_graph(graph_provider, test_user)
        if second_user:
            ensure_pipeshub_user_exists(users_client, second_user)
            await _wait_for_active_user_in_graph(graph_provider, second_user)
        suffix = uuid.uuid4().hex[:8]
        try:
            drive_a = await create_shared_drive(drive_workspace_datasource, f"pipeshub-it-audit-a-{suffix}")
        except RefreshError as e:
            raise RuntimeError(_dwd_failure_message(sa_json, admin_email, test_user, e)) from e
        drive_ids.append(drive_a)
        drive_b = await create_shared_drive(drive_workspace_datasource, f"pipeshub-it-audit-b-{suffix}")
        drive_ids.append(drive_b)
        state.update(drive_a=drive_a, drive_b=drive_b)
        state["move_file_id"] = await create_drive_text_file(
            drive_workspace_datasource, "move-me.txt", parent_id=drive_a, content=f"audit move {suffix}\n"
        )
        state["doc_id"] = await create_drive_google_file(
            drive_workspace_datasource, "audit-doc", parent_id=drive_a, mime_type=MimeTypes.GOOGLE_DOCS.value,
            content=f"audit doc {suffix}: a rename or a new share must not re-index this text.\n",
        )
        admin_drive = await build_drive_datasource(sa_json, admin_email, admin_email)
        instance = pipeshub_client.create_connector(
            connector_type="Drive Workspace",
            instance_name=f"drive-ws-audit-{suffix}",
            scope="team",
            config={
                "auth": _auth_config(admin_email, sa_json),
                "filters": connector_filters(
                    sync_values={"drive_ids": {"operator": "in", "type": "list", "value": drive_ids}}
                ),
            },
            auth_type="CUSTOM",
        )
        connector_id = instance.connector_id
        state["connector_id"] = connector_id
        await wait_until_shared_drives_listed(drive_workspace_datasource, drive_ids)
        await wait_until_shared_drive_files_listed(
            drive_workspace_datasource, drive_a, [state["move_file_id"], state["doc_id"]]
        )
        pipeshub_client.toggle_sync(connector_id, enable=True)
        await wait_for_sync_completion(
            pipeshub_client, graph_provider, connector_id, min_records=1, timeout=_SYNC_TIMEOUT_SEC,
        )

        async def _present() -> bool:
            for external_id in (state["move_file_id"], state["doc_id"]):
                if await graph_provider.get_record_by_external_id(connector_id, external_id) is None:
                    return False
            return True

        if not await _present():
            await sync_until(pipeshub_client, graph_provider, connector_id, _present,
                             description="audit fixture files in graph")
        yield state
    finally:
        await _teardown_connector(pipeshub_client, graph_provider, state.get("connector_id"))
        for drive_id in drive_ids:
            await delete_shared_drive(drive_workspace_datasource, drive_id, admin_drive=admin_drive)


PERMISSION_FIXTURE_KEYS = ("private", "shared", "revoke", "domain")


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def drive_workspace_permission_connector(
    drive_workspace_datasource: GoogleDriveDataSource,
    pipeshub_client: PipeshubClient,
    users_client: UsersClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[dict[str, Any], None]:
    """Files shared four ways by the test user, synced, with both users logged in.

    ``owner`` is the Drive test user and ``reader`` the second Workspace member,
    each logged in to PipesHub as that same address, because connector grants are
    matched by email. The reader must be an active PipesHub user before the first
    sync: a grant to an address PipesHub doesn't know is skipped, not deferred.
    """
    sa_json, admin_email, test_user = require_drive_workspace_env()
    second_user = os.getenv(ENV_SECOND_USER, "").strip()
    if not second_user:
        source_unavailable(
            "No second Workspace user is configured, so this suite cannot check what "
            "a colleague can see.",
            secrets=[ENV_SECOND_USER],
        )
    domain = test_user.rsplit("@", 1)[-1].lower()
    second_domain = second_user.rsplit("@", 1)[-1].lower()
    if second_domain != domain:
        # A domain share can't reach a user outside the domain, so the strict xfail
        # would report a misconfiguration as the known connector gap.
        raise RuntimeError(
            f"{ENV_SECOND_USER} must be in the same Workspace domain as {ENV_TEST_USER} "
            f"({domain}), got {second_domain}"
        )

    state: dict[str, Any] = {
        "connector_id": None,
        "test_user_email": test_user,
        "second_user_email": second_user,
    }
    fixtures: dict[str, str] = {}
    logged_in: list[SecondUser] = []
    try:
        users: dict[str, str] = {}
        for email in (test_user, second_user):
            users[email], _ = ensure_pipeshub_user_exists(users_client, email)
            await _wait_for_active_user_in_graph(graph_provider, email)

        fixtures = await create_permission_fixtures(
            drive_workspace_datasource, second_user, domain
        )
        state.update(fixtures)
        root_id = fixtures["root_folder_id"]
        file_ids = [fixtures[f"{key}_file_id"] for key in PERMISSION_FIXTURE_KEYS]

        instance = pipeshub_client.create_connector(
            connector_type="Drive Workspace",
            instance_name=f"drive-ws-perm-{uuid.uuid4().hex[:8]}",
            scope="team",
            config={
                "auth": {"adminEmail": admin_email, "serviceAccountJson": sa_json},
                "filters": {
                    "sync": {
                        "values": {
                            "folder_ids": {"operator": "in", "type": "list", "value": [root_id]}
                        }
                    }
                },
            },
            auth_type="CUSTOM",
        )
        assert instance.connector_id, "Connector must have a valid ID"
        connector_id = instance.connector_id
        state["connector_id"] = connector_id

        await wait_until_drive_files_listed(drive_workspace_datasource, [root_id, *file_ids])
        pipeshub_client.toggle_sync(connector_id, enable=True)
        await wait_for_sync_completion(
            pipeshub_client, graph_provider, connector_id,
            min_records=len(file_ids), timeout=_SYNC_TIMEOUT_SEC,
        )

        async def _all_files_present() -> bool:
            for key in PERMISSION_FIXTURE_KEYS:
                record = await graph_provider.get_record_by_external_id(
                    connector_id, fixtures[f"{key}_file_id"]
                )
                if record is None:
                    return False
                state[f"{key}_record_id"] = record.id
            return True

        await wait_until_graph_condition(
            connector_id,
            check=_all_files_present,
            timeout=_SYNC_TIMEOUT_SEC,
            poll_interval=10,
            description="all four permission fixture files in graph",
        )

        for role, email in (("owner", test_user), ("reader", second_user)):
            user = log_in_existing_user(pipeshub_client, users[email], email)
            logged_in.append(user)
            state[role] = user

        yield state
    finally:
        for user in logged_in:
            log_out_existing_user(pipeshub_client, user)
        await _teardown_connector(pipeshub_client, graph_provider, state.get("connector_id"))
        await delete_drive_folder(drive_workspace_datasource, fixtures.get("root_folder_id"))


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def drive_workspace_shared_drives(
    drive_workspace_datasource: GoogleDriveDataSource,
) -> AsyncGenerator[dict[str, str], None]:
    """Create two temporary Shared Drives for the Shared Drive folder-filter suite."""
    try:
        sa_json, admin_email, _test_user = require_drive_workspace_env()
    except ValueError as e:
        source_unavailable(f"The Google Workspace tenant this suite syncs from is not configured. {e}")

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
            source_unavailable(
                "Could not create the Shared Drives this suite needs. Check that the "
                f"test user may create Shared Drives in Workspace admin. Error: {e}"
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

        yield {
            "drive_a_id": drive_a_id,
            "drive_b_id": drive_b_id,
            "drive_a_name": f"pipeshub-it-sd-a-{suffix}",
            "drive_b_name": f"pipeshub-it-sd-b-{suffix}",
        }
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
            raise RuntimeError(
                _dwd_failure_message(sa_json, admin_email, test_user, e)
            ) from e
        state.update(fixtures)

        seed_folder_id = fixtures["seed_folder_id"]
        config: dict[str, Any] = {
            "auth": _auth_config(admin_email, sa_json),
            "filters": connector_filters(
                sync_values={
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
            ),
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

        child_file_id = fixtures["child_file_id"]

        # The connector enumerates a Shared Drive with a drive-wide files.list whose
        # index lags the create calls above. Without this the single sync this fixture
        # triggers finds no files, and the graph poll below can only time out.
        await wait_until_shared_drive_files_listed(
            drive_workspace_datasource,
            drive_a_id,
            [seed_folder_id, fixtures["nested_folder_id"], child_file_id],
        )

        pipeshub_client.toggle_sync(connector_id, enable=True)
        await wait_for_sync_completion(
            pipeshub_client,
            graph_provider,
            connector_id,
            min_records=1,
            timeout=_SYNC_TIMEOUT_SEC,
        )

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
    drive_workspace_shared_drive_connector: dict[str, Any],
    pipeshub_client: PipeshubClient,
    users_client: UsersClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[dict[str, Any], None]:
    """Short-lived connector with folder_ids=[Shared Drive A root id].

    Takes the root-seed pair from the module tree instead of creating its own: this
    connector's first sync reads the drive-wide files.list and nothing else, and that
    listing can omit an item created seconds earlier.
    """
    sa_json = os.environ[ENV_SA_JSON].strip()
    admin_email = os.environ[ENV_ADMIN_EMAIL].strip()
    test_user = os.environ[ENV_TEST_USER].strip()
    drive_a_id = drive_workspace_shared_drive_connector["drive_a_id"]
    root_folder_id = drive_workspace_shared_drive_connector["root_folder_id"]
    root_file_id = drive_workspace_shared_drive_connector["root_file_id"]

    connector_name = f"drive-ws-sd-root-{uuid.uuid4().hex[:8]}"
    state: dict[str, Any] = {
        "connector_name": connector_name,
        "connector_id": None,
        "drive_a_id": drive_a_id,
        "test_user_email": test_user,
        "root_folder_id": root_folder_id,
        "root_folder_name": drive_workspace_shared_drive_connector["root_folder_name"],
        "root_file_id": root_file_id,
        "root_file_name": drive_workspace_shared_drive_connector["root_file_name"],
    }

    try:
        ensure_pipeshub_user_exists(users_client, test_user)
        await _wait_for_active_user_in_graph(graph_provider, test_user)

        config: dict[str, Any] = {
            "auth": _auth_config(admin_email, sa_json),
            "filters": connector_filters(
                sync_values={
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
            ),
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

        await wait_until_shared_drive_files_listed(
            drive_workspace_datasource,
            drive_a_id,
            [root_folder_id, root_file_id],
        )

        pipeshub_client.toggle_sync(connector_id, enable=True)
        await wait_for_sync_completion(
            pipeshub_client,
            graph_provider,
            connector_id,
            min_records=1,
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


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def drive_workspace_drive_ids_connector(
    drive_workspace_datasource: GoogleDriveDataSource,
    drive_workspace_shared_drives: dict[str, str],
    pipeshub_client: PipeshubClient,
    users_client: UsersClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[dict[str, Any], None]:
    """Connector with drive_ids=[A] only (no folder_ids) for pure Shared Drive filter ITs."""
    sa_json = os.environ[ENV_SA_JSON].strip()
    admin_email = os.environ[ENV_ADMIN_EMAIL].strip()
    test_user = os.environ[ENV_TEST_USER].strip()
    drive_a_id = drive_workspace_shared_drives["drive_a_id"]
    drive_b_id = drive_workspace_shared_drives["drive_b_id"]

    connector_name = f"drive-ws-drive-ids-{uuid.uuid4().hex[:8]}"
    state: dict[str, Any] = {
        "connector_name": connector_name,
        "connector_id": None,
        "test_user_email": test_user,
        "admin_email": admin_email,
        "drive_a_id": drive_a_id,
        "drive_b_id": drive_b_id,
        "drive_a_name": drive_workspace_shared_drives["drive_a_name"],
        "drive_b_name": drive_workspace_shared_drives["drive_b_name"],
    }
    fixtures: dict[str, str] = {}

    try:
        user_id, created = ensure_pipeshub_user_exists(users_client, test_user)
        state["pipeshub_user_id"] = user_id
        state["pipeshub_user_created"] = created
        await _wait_for_active_user_in_graph(graph_provider, test_user)

        try:
            fixtures = await create_drive_ids_filter_fixtures(
                drive_workspace_datasource, drive_a_id, drive_b_id
            )
        except RefreshError as e:
            raise RuntimeError(
                _dwd_failure_message(sa_json, admin_email, test_user, e)
            ) from e
        state.update(fixtures)

        config: dict[str, Any] = {
            "auth": _auth_config(admin_email, sa_json),
            "filters": connector_filters(
                sync_values={
                    "drive_ids": {
                        "operator": "in",
                        "type": "list",
                        "value": [drive_a_id],
                    },
                }
            ),
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
            "SETUP: drive_ids-only connector %s drive_ids=[%s]",
            connector_id,
            drive_a_id,
        )

        await wait_until_shared_drives_listed(
            drive_workspace_datasource, [drive_a_id, drive_b_id]
        )
        drive_a_file_id = fixtures["drive_a_file_id"]
        await wait_until_shared_drive_files_listed(
            drive_workspace_datasource, drive_a_id, [drive_a_file_id]
        )
        await wait_until_shared_drive_files_listed(
            drive_workspace_datasource, drive_b_id, [fixtures["drive_b_file_id"]]
        )

        pipeshub_client.toggle_sync(connector_id, enable=True)
        await wait_for_sync_completion(
            pipeshub_client,
            graph_provider,
            connector_id,
            min_records=1,
            timeout=_SYNC_TIMEOUT_SEC,
        )

        async def _drive_a_file_present() -> bool:
            return (
                await graph_provider.get_record_by_external_id(
                    connector_id, drive_a_file_id
                )
                is not None
            )

        try:
            await wait_until_graph_condition(
                connector_id,
                check=_drive_a_file_present,
                timeout=_SYNC_TIMEOUT_SEC,
                poll_interval=10,
                description="drive_ids filter Drive A root file in graph",
            )
        except TimeoutError:
            raise TimeoutError(
                f"Timed out waiting for drive_ids Drive A file. "
                f"connector_id={connector_id} drive_a={drive_a_id} "
                f"file={drive_a_file_id}"
            ) from None

        yield state
    finally:
        await _teardown_connector(
            pipeshub_client, graph_provider, state.get("connector_id")
        )


@pytest_asyncio.fixture(scope="class", loop_scope="session")
async def drive_workspace_ext_connector(
    drive_workspace_datasource: GoogleDriveDataSource,
    pipeshub_client: PipeshubClient,
    users_client: UsersClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[dict[str, Any], None]:
    """Class-scoped connector: folder_ids=[seed] + file_extensions IN docs/sheets/txt."""
    sa_json = os.environ[ENV_SA_JSON].strip()
    admin_email = os.environ[ENV_ADMIN_EMAIL].strip()
    test_user = os.environ[ENV_TEST_USER].strip()

    connector_name = f"drive-ws-ext-{uuid.uuid4().hex[:8]}"
    state: dict[str, Any] = {
        "connector_name": connector_name,
        "connector_id": None,
        "test_user_email": test_user,
        "admin_email": admin_email,
    }
    fixtures: dict[str, str] = {}

    try:
        user_id, created = ensure_pipeshub_user_exists(users_client, test_user)
        state["pipeshub_user_id"] = user_id
        state["pipeshub_user_created"] = created
        await _wait_for_active_user_in_graph(graph_provider, test_user)

        try:
            fixtures = await create_extension_filter_fixtures(drive_workspace_datasource)
        except RefreshError as e:
            raise RuntimeError(
                _dwd_failure_message(sa_json, admin_email, test_user, e)
            ) from e
        state.update(fixtures)

        seed_folder_id = fixtures["seed_folder_id"]
        config: dict[str, Any] = {
            "auth": _auth_config(admin_email, sa_json),
            "filters": connector_filters(
                sync_values={
                    "folder_ids": {
                        "operator": "in",
                        "type": "list",
                        "value": [seed_folder_id],
                    },
                    "file_extensions": {
                        "operator": "in",
                        "type": "multiselect",
                        "value": [
                            MimeTypes.GOOGLE_DOCS.value,
                            MimeTypes.GOOGLE_SHEETS.value,
                            "txt",
                        ],
                    },
                }
            ),
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
            "SETUP(ext): connector %s folder_ids=[%s] file_extensions IN "
            "docs/sheets/txt (slides excluded)",
            connector_id,
            seed_folder_id,
        )

        doc_file_id = fixtures["doc_file_id"]
        await wait_until_drive_files_listed(
            drive_workspace_datasource,
            [
                seed_folder_id,
                *(fixtures[f"{kind}_file_id"] for kind in ("doc", "sheet", "slide", "txt")),
            ],
        )

        pipeshub_client.toggle_sync(connector_id, enable=True)
        await wait_for_sync_completion(
            pipeshub_client,
            graph_provider,
            connector_id,
            min_records=1,
            timeout=_SYNC_TIMEOUT_SEC,
        )

        async def _seed_and_doc_present() -> bool:
            seed = await graph_provider.get_record_by_external_id(
                connector_id, seed_folder_id
            )
            doc = await graph_provider.get_record_by_external_id(
                connector_id, doc_file_id
            )
            return seed is not None and doc is not None

        try:
            await wait_until_graph_condition(
                connector_id,
                check=_seed_and_doc_present,
                timeout=_SYNC_TIMEOUT_SEC,
                poll_interval=10,
                description="extension-filter seed folder + Google Doc in graph",
            )
        except TimeoutError:
            raise TimeoutError(
                f"Timed out waiting for extension-filter seed records. "
                f"connector_id={connector_id} seed={seed_folder_id} doc={doc_file_id}"
            ) from None

        yield state
    finally:
        await _teardown_connector(
            pipeshub_client, graph_provider, state.get("connector_id")
        )
        await delete_drive_folder(
            drive_workspace_datasource,
            fixtures.get("root_folder_id") or state.get("root_folder_id"),
        )

# pyright: ignore-file

"""Google Drive personal (individual) integration fixtures.

Class-scoped connectors, run in ``@pytest.mark.order`` sequence within the one
IT module:

* ``drive_individual_entity_connector`` — unfiltered sync for
  ``TestDriveIndividualEntitySync``. Tears down connector + Drive source tree
  before the folder-filter suite starts.
* ``drive_individual_ff_connector`` — ``folder_ids=[seed]`` for
  ``TestDriveIndividualFolderFilter``.
* ``drive_individual_ext_connector`` — ``folder_ids=[seed]`` + ``file_extensions``
  for ``TestDriveIndividualExtensionFilter``.
* ``drive_individual_blocks_connector`` — ``folder_ids=[seed]`` with the five
  google-drive-it-files samples for ``TestDriveIndividualBlocks``.
* ``drive_individual_blocks_stream_user`` — ``GOOGLE_DRIVE_TEST_USER_EMAIL``
  logged in to PipesHub, so streaming passes the record ACL.

Each connector registers with OAuth credentials injected from
``GOOGLE_DRIVE_REFRESH_TOKEN``; Drive fixture trees are created via
``GoogleDriveDataSource`` under the same OAuth user's My Drive.
"""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator, Iterator, Optional

import pytest
import pytest_asyncio

from helper.source_credentials import source_unavailable

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
from helper.second_user import (  # type: ignore[import-not-found]
    SecondUser,
    log_in_existing_user,
    log_out_existing_user,
)
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

from app.config.constants.arangodb import MimeTypes  # type: ignore[import-not-found]
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
    create_entity_fixtures,
    create_folder_filter_fixtures,
    drive_about_get,
    require_drive_individual_env,
)
from connectors.google_drive_workspace.drive_workspace_test_utils import (  # type: ignore[import-not-found]
    connector_filters,
    create_drive_blocks_fixtures,
    create_extension_filter_fixtures,
    create_lifecycle_fixtures,
    delete_drive_folder,
    ensure_pipeshub_user_exists,
    list_shared_drive_ids,
    wait_until_drive_files_listed,
    resolve_my_drive_root_id,
    upload_drive_blocks_samples,
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
        source_unavailable(f"The Google Drive account this suite syncs from is not configured. {e}")

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


async def _start_personal_connector(
    pipeshub_client: PipeshubClient,
    users_client: UsersClient,
    graph_provider: GraphProviderProtocol,
    state: dict[str, Any],
    drive: GoogleDriveDataSource,
    listed_ids: list[str],
    *,
    sync_values: Optional[dict[str, Any]] = None,
    manual_indexing: bool = True,
) -> str:
    """Register, authenticate and sync a personal Drive connector; return its id.

    Waits for *listed_ids* in Drive's own listing first: My Drive is enumerated
    through the user-corpus files.list, whose index lags fresh creates.

    Mutates *state* with ``pipeshub_user_id`` / ``pipeshub_user_created`` /
    ``connector_id`` so teardown can run off a partially built state.
    """
    test_user = state["test_user_email"]

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

    config: dict[str, Any] = {
        "auth": {
            "clientId": os.environ[ENV_CLIENT_ID].strip(),
            "clientSecret": os.environ[ENV_CLIENT_SECRET].strip(),
        },
        "filters": connector_filters(
            sync_values=sync_values, manual_indexing=manual_indexing
        ),
    }

    instance = pipeshub_client.create_connector(
        connector_type="Drive",
        instance_name=state["connector_name"],
        scope="personal",
        config=config,
        auth_type="OAUTH",
    )
    assert instance.connector_id, "Connector must have a valid ID"
    connector_id = instance.connector_id
    state["connector_id"] = connector_id

    try:
        await authenticate_connector_with_refresh_token(
            connector_id=connector_id,
            refresh_token_env_var=ENV_REFRESH_TOKEN,
            token_url=GOOGLE_TOKEN_URL,
            client_id_env_var=ENV_CLIENT_ID,
            client_secret_env_var=ENV_CLIENT_SECRET,
        )
    except Exception as e:
        pytest.fail(f"Failed to authenticate connector with refresh token: {e}")

    await wait_until_drive_files_listed(drive, listed_ids)
    pipeshub_client.toggle_sync(connector_id, enable=True)
    await wait_for_sync_completion(
        pipeshub_client,
        graph_provider,
        connector_id,
        min_records=1,
        timeout=_SYNC_TIMEOUT_SEC,
    )
    return connector_id


async def _wait_for_records(
    graph_provider: GraphProviderProtocol,
    connector_id: str,
    external_ids: list[str],
    *,
    description: str,
) -> None:
    """Wait until every id in *external_ids* has a Record; annotate timeouts."""

    async def _all_present() -> bool:
        for external_id in external_ids:
            if await graph_provider.get_record_by_external_id(connector_id, external_id) is None:
                return False
        return True

    try:
        await wait_until_graph_condition(
            connector_id,
            check=_all_present,
            timeout=_SYNC_TIMEOUT_SEC,
            poll_interval=10,
            description=description,
        )
    except TimeoutError:
        raise TimeoutError(
            f"Timed out waiting for {description}. "
            f"connector_id={connector_id} expected={external_ids}"
        ) from None


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


def _new_state(name_prefix: str) -> dict[str, Any]:
    return {
        "connector_name": f"{name_prefix}-{uuid.uuid4().hex[:8]}",
        "connector_id": None,
        "test_user_email": os.environ[ENV_TEST_USER].strip(),
        "pipeshub_user_id": None,
        "pipeshub_user_created": False,
    }


@pytest_asyncio.fixture(scope="class", loop_scope="session")
async def drive_individual_entity_connector(
    drive_individual_datasource: GoogleDriveDataSource,
    sample_data_root: Path,
    pipeshub_client: PipeshubClient,
    users_client: UsersClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[dict[str, Any], None]:
    """Class-scoped unfiltered personal connector for ``TestDriveIndividualEntitySync``.

    No ``folder_ids`` filter — syncs the OAuth user's whole My Drive, which is
    what exercises the full-sync path (record group, app user, file properties).
    """
    state = _new_state("drive-personal-entity")
    fixtures: dict[str, Any] = {}
    try:
        state["my_drive_root_id"] = await resolve_my_drive_root_id(
            drive_individual_datasource
        )
        about_user = await drive_about_get(drive_individual_datasource)
        state["test_user_source_id"] = str(about_user.get("permissionId") or "")
        state["test_user_full_name"] = str(about_user.get("displayName") or "")
        state["drive_account_email"] = str(about_user.get("emailAddress") or "")

        fixtures = await create_entity_fixtures(
            drive_individual_datasource, sample_data_root
        )
        state.update(fixtures)
        # Before the first sync, so drives other tests add or remove later are not expected.
        state["member_drive_ids_before_sync"] = await list_shared_drive_ids(drive_individual_datasource)

        entity_ids = [fixtures["seed_folder_id"], fixtures["child_file_id"]] + [
            s["id"] for s in fixtures["entity_sample_files"]
        ]
        connector_id = await _start_personal_connector(
            pipeshub_client,
            users_client,
            graph_provider,
            state,
            drive_individual_datasource,
            entity_ids,
        )
        logger.info(
            "SETUP(entity): Drive personal connector %s unfiltered (user=%s) samples=%s",
            connector_id,
            state["test_user_email"],
            [s["id"] for s in fixtures["entity_sample_files"]],
        )

        await _wait_for_records(
            graph_provider,
            connector_id,
            entity_ids,
            description="entity seed + child.txt + sample files in graph",
        )

        yield state
    finally:
        await _teardown_connector(
            pipeshub_client, graph_provider, state.get("connector_id")
        )
        await delete_drive_folder(
            drive_individual_datasource,
            fixtures.get("root_folder_id") or state.get("root_folder_id"),
        )


@pytest_asyncio.fixture(scope="class", loop_scope="session")
async def drive_individual_ff_connector(
    drive_individual_datasource: GoogleDriveDataSource,
    pipeshub_client: PipeshubClient,
    users_client: UsersClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[dict[str, Any], None]:
    """Class-scoped personal Drive connector with folder_ids filter on seed."""
    state = _new_state("drive-personal-ff")
    fixtures: dict[str, str] = {}
    try:
        fixtures = await create_folder_filter_fixtures(drive_individual_datasource)
        state.update(fixtures)

        seed_folder_id = fixtures["seed_folder_id"]
        connector_id = await _start_personal_connector(
            pipeshub_client,
            users_client,
            graph_provider,
            state,
            drive_individual_datasource,
            [seed_folder_id, fixtures["nested_folder_id"], fixtures["child_file_id"]],
            sync_values={
                "folder_ids": {
                    "operator": "in",
                    "type": "list",
                    "value": [seed_folder_id],
                }
            },
        )
        logger.info(
            "SETUP(ff): Drive personal connector %s with folder_ids=[%s] (user=%s)",
            connector_id,
            seed_folder_id,
            state["test_user_email"],
        )

        await _wait_for_records(
            graph_provider,
            connector_id,
            [seed_folder_id, fixtures["child_file_id"]],
            description="folder-filter seed folder + child.txt in graph",
        )
        logger.info(
            "SETUP(ff) done: seed=%s nested=%s child=%s oos=%s",
            seed_folder_id,
            fixtures["nested_folder_id"],
            fixtures["child_file_id"],
            fixtures["oos_folder_id"],
        )

        yield state
    finally:
        await _teardown_connector(
            pipeshub_client, graph_provider, state.get("connector_id")
        )
        # Drive cleanup is fixture-scoped only: delete the IT root folder tree.
        # Even after a cleared folder_ids sync may have indexed other My Drive
        # content into Pipeshub, do not delete non-fixture Drive items here.
        await delete_drive_folder(
            drive_individual_datasource,
            fixtures.get("root_folder_id") or state.get("root_folder_id"),
        )

        # Do not soft-delete GOOGLE_DRIVE_TEST_USER_EMAIL — reusable identity.


@pytest_asyncio.fixture(scope="class", loop_scope="session")
async def drive_individual_ext_connector(
    drive_individual_datasource: GoogleDriveDataSource,
    pipeshub_client: PipeshubClient,
    users_client: UsersClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[dict[str, Any], None]:
    """Class-scoped connector: folder_ids=[seed] + file_extensions IN docs/sheets/txt."""
    state = _new_state("drive-personal-ext")
    fixtures: dict[str, str] = {}
    try:
        fixtures = await create_extension_filter_fixtures(drive_individual_datasource)
        state.update(fixtures)

        seed_folder_id = fixtures["seed_folder_id"]
        connector_id = await _start_personal_connector(
            pipeshub_client,
            users_client,
            graph_provider,
            state,
            drive_individual_datasource,
            [
                seed_folder_id,
                *(fixtures[f"{kind}_file_id"] for kind in ("doc", "sheet", "slide", "txt")),
            ],
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
            },
        )
        logger.info(
            "SETUP(ext): connector %s folder_ids=[%s] file_extensions IN "
            "docs/sheets/txt (slides excluded)",
            connector_id,
            seed_folder_id,
        )

        await _wait_for_records(
            graph_provider,
            connector_id,
            [seed_folder_id, fixtures["doc_file_id"]],
            description="extension-filter seed folder + Google Doc in graph",
        )

        yield state
    finally:
        await _teardown_connector(
            pipeshub_client, graph_provider, state.get("connector_id")
        )
        await delete_drive_folder(
            drive_individual_datasource,
            fixtures.get("root_folder_id") or state.get("root_folder_id"),
        )


@pytest_asyncio.fixture(scope="class", loop_scope="session")
async def drive_individual_blocks_connector(
    drive_individual_datasource: GoogleDriveDataSource,
    drive_blocks_sample_root: Path,
    pipeshub_client: PipeshubClient,
    users_client: UsersClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[dict[str, Any], None]:
    """Class-scoped connector for blocks snapshot ITs.

    Uploads the five google-drive-it-files samples under ``seed`` (Office files
    converted to Docs/Sheets/Slides), syncs with ``folder_ids=[seed]``, and waits
    until all five file records exist. Auto-indexing is left on so the same five
    records also cover the live pipeline (``TC-DRIVE-IND-IDX-*``); the snapshot
    tests parse in-process and are unaffected by it.
    """
    state = _new_state("drive-personal-blocks")
    state["blocks_files"] = []
    state["blocks_by_kind"] = {}
    fixtures: dict[str, Any] = {}
    try:
        fixtures = await create_drive_blocks_fixtures(drive_individual_datasource)
        state.update(fixtures)

        seed_folder_id = fixtures["seed_folder_id"]
        blocks_files = await upload_drive_blocks_samples(
            drive_individual_datasource,
            seed_folder_id,
            drive_blocks_sample_root,
        )
        state["blocks_files"] = blocks_files
        state["blocks_by_kind"] = {f["kind"]: f for f in blocks_files}

        connector_id = await _start_personal_connector(
            pipeshub_client,
            users_client,
            graph_provider,
            state,
            drive_individual_datasource,
            [seed_folder_id, *(f["id"] for f in blocks_files)],
            sync_values={
                "folder_ids": {
                    "operator": "in",
                    "type": "list",
                    "value": [seed_folder_id],
                }
            },
            manual_indexing=False,
        )
        logger.info(
            "SETUP(blocks): Drive personal connector %s with folder_ids=[%s] "
            "(%d sample files, user=%s)",
            connector_id,
            seed_folder_id,
            len(blocks_files),
            state["test_user_email"],
        )

        await _wait_for_records(
            graph_provider,
            connector_id,
            [f["id"] for f in blocks_files],
            description=f"blocks seed + {len(blocks_files)} sample files in graph",
        )
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
            drive_individual_datasource,
            fixtures.get("root_folder_id") or state.get("root_folder_id"),
        )


@pytest.fixture(scope="class")
def drive_individual_blocks_stream_user(
    pipeshub_client: PipeshubClient,
    drive_individual_blocks_connector: dict[str, Any],
) -> Iterator[SecondUser]:
    """The Drive test user logged in to PipesHub: the personal connector grants only its OAuth account."""
    user = log_in_existing_user(
        pipeshub_client,
        drive_individual_blocks_connector["pipeshub_user_id"],
        drive_individual_blocks_connector["test_user_email"],
    )
    try:
        yield user
    finally:
        log_out_existing_user(pipeshub_client, user)


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def drive_individual_unfiltered_connector(
    drive_individual_datasource: GoogleDriveDataSource,
    pipeshub_client: PipeshubClient,
    users_client: UsersClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[dict[str, Any], None]:
    """Module-scoped personal connector with no filter: My Drive plus every member Shared Drive.

    For the Shared Drive and shortcut ITs, whose items live outside any single folder.
    """
    state = _new_state("drive-personal-unfiltered")
    try:
        state["my_drive_root_id"] = await resolve_my_drive_root_id(drive_individual_datasource)
        await _start_personal_connector(
            pipeshub_client, users_client, graph_provider, state,
            drive_individual_datasource, [],
        )
        yield state
    finally:
        await _teardown_connector(pipeshub_client, graph_provider, state.get("connector_id"))


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def drive_individual_lifecycle_connector(
    drive_individual_datasource: GoogleDriveDataSource,
    pipeshub_client: PipeshubClient,
    users_client: UsersClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[dict[str, Any], None]:
    """Personal connector scoped to a fresh lifecycle tree, indexing on (trash must clear vectors)."""
    state = _new_state("drive-personal-lc")
    fixtures: dict[str, str] = {}
    try:
        fixtures = await create_lifecycle_fixtures(drive_individual_datasource)
        state.update(fixtures)
        root_id = fixtures["root_folder_id"]
        item_ids = [v for k, v in fixtures.items() if k.endswith("_id") and k != "root_folder_id"]
        connector_id = await _start_personal_connector(
            pipeshub_client, users_client, graph_provider, state,
            drive_individual_datasource, [root_id, *item_ids],
            sync_values={"folder_ids": {"operator": "in", "type": "list", "value": [root_id]}},
            manual_indexing=False,
        )
        await _wait_for_records(
            graph_provider, connector_id, item_ids, description="personal lifecycle fixture items in graph",
        )
        yield state
    finally:
        await _teardown_connector(pipeshub_client, graph_provider, state.get("connector_id"))
        await delete_drive_folder(drive_individual_datasource, fixtures.get("root_folder_id"))

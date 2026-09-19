# pyright: ignore-file

"""Local FS connector fixtures.

Local FS needs no external service. Two connectors cover its two ways in:

- ``local_fs_connector`` syncs a folder the connector can read, the run's own
  subfolder of ``integration-tests/local_fs_root`` (mounted into the stack).
- ``local_fs_desktop_connector`` points at a folder the backend cannot see, as
  the desktop app does, and receives files only through the upload API.
"""

import os
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from connector_lifecycle import (
    create_connector_and_await_sync,
    destructor,
    source_unavailable,
)
from connectors.local_fs.local_fs_seed import SEED_FILES, SEED_FOLDERS
from connectors.local_fs.local_fs_source_helper import (
    DEFAULT_CONTAINER_ROOT,
    DEFAULT_HOST_ROOT,
    LocalFsFolder,
)
from helper.graph_provider import GraphProviderProtocol
from helper.run_folder import new_run_folder
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

CONNECTOR_TYPE = "Local FS"


@pytest.fixture(scope="session")
def local_fs_folder() -> LocalFsFolder:
    folder = LocalFsFolder(
        host_root=Path(os.getenv("LOCAL_FS_TEST_HOST_ROOT", str(DEFAULT_HOST_ROOT))),
        container_root=os.getenv("LOCAL_FS_TEST_CONTAINER_ROOT", DEFAULT_CONTAINER_ROOT),
    )
    try:
        folder.check_available()
    except OSError as exc:
        source_unavailable(f"Local FS test folder {folder.host_root} is not writable: {exc}")
    return folder


@pytest.mark.skip(
    reason=(
        "Local FS now pulls events from the desktop; creating this connector "
        "would wait forever for a folder-walk sync"
    )
)
@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def local_fs_connector(
    local_fs_folder: LocalFsFolder,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[dict[str, Any], None]:
    folder = new_run_folder()
    for rel_path, text in SEED_FILES.items():
        local_fs_folder.write(folder, rel_path, text)
    state: dict[str, Any] = {
        "resource_name": str(local_fs_folder.host_root),
        "folder": folder,
    }

    config = {
        "sync": {
            "sync_root_path": local_fs_folder.container_path(folder),
            "include_subfolders": True,
        }
    }
    await create_connector_and_await_sync(
        pipeshub_client,
        graph_provider,
        state,
        connector_type=CONNECTOR_TYPE,
        connector_name=f"local-fs-lifecycle-test-{uuid.uuid4().hex[:8]}",
        connector_config=config,
        # Local FS indexes folders as records too.
        expected_records=len(SEED_FILES) + len(SEED_FOLDERS),
        # Local FS is registered for personal scope only.
        scope="personal",
    )

    yield state

    await destructor(
        local_fs_folder,
        pipeshub_client,
        graph_provider,
        state,
        connector_type=CONNECTOR_TYPE,
    )


@pytest.mark.skip(
    reason=(
        "Local FS now pulls events from the desktop; this fixture still "
        "points at a folder the backend cannot see and expects upload ingest"
    )
)
@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def local_fs_desktop_connector(
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[dict[str, Any], None]:
    """A connector whose folder exists only on the user's machine, not the backend's."""
    instance = pipeshub_client.create_connector(
        connector_type=CONNECTOR_TYPE,
        instance_name=f"local-fs-desktop-test-{uuid.uuid4().hex[:8]}",
        scope="personal",
        config={
            "sync": {
                "sync_root_path": f"/Users/it-desktop/{uuid.uuid4().hex[:8]}",
                "include_subfolders": True,
            }
        },
    )
    assert instance.connector_id, "Connector must have a valid ID"
    # No folder of ours to clear: the uploaded files live in PipesHub's storage.
    state: dict[str, Any] = {"connector_id": instance.connector_id, "resource_name": ""}

    yield state

    await destructor(
        None,
        pipeshub_client,
        graph_provider,
        state,
        connector_type=CONNECTOR_TYPE,
    )

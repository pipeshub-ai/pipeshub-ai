# pyright: ignore-file

"""Local FS connector fixtures.

This connector reads a folder on the machine the connector service runs on. The
integration compose file bind-mounts one directory into that container, so the
test writes files on the host side and the connector reads them on the container
side — no external service, and nothing to procure.

Unlike the other connectors, its settings live under a ``sync`` key rather than
``auth``: the folder is a sync setting, not a credential.
"""

import os
import uuid
from typing import Any, AsyncGenerator, Dict

import pytest
import pytest_asyncio

from connector_lifecycle import create_connector_and_await_sync, destructor
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]
from helper.graph_provider import GraphProviderProtocol

from connectors.localfs.localfs_source_helper import LocalFsSourceHelper

# Defaults match deployment/docker-compose/docker-compose.integration.*.yml.
DEFAULT_HOST_DIR = "../deployment/docker-compose/localfs-test-data"
DEFAULT_CONNECTOR_ROOT = "/data/localfs-test"

SEED_FILES = [
    ("handbook/onboarding.txt", "How to set up a new workspace.\n"),
    ("handbook/billing.txt", "Answers to common billing questions.\n"),
    ("policies/leave.txt", "Annual leave accrues monthly.\n"),
]



def _unavailable(reason: str) -> None:
    """A source that is not reachable: skip locally, fail in CI.

    Skipping on any exception turns a broken stack into a green run. CI brings
    this source up itself, so if it is not reachable there, that is a result and
    not a reason to report success.
    """
    if os.getenv("CI"):
        pytest.fail(reason)
    pytest.skip(reason)

@pytest.fixture(scope="session")
def localfs_source() -> LocalFsSourceHelper:
    helper = LocalFsSourceHelper(os.getenv("LOCALFS_TEST_HOST_DIR", DEFAULT_HOST_DIR))
    # Skip rather than fail when the directory is not usable, so a partial local
    # stack stays usable. In CI the mount is always present.
    try:
        helper.ensure_root()
    except OSError as exc:
        _unavailable(f"Local FS test directory not writable at {helper.root}: {exc}")
    helper.clear_objects()
    return helper


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def localfs_connector(
    localfs_source: LocalFsSourceHelper,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[Dict[str, Any], None]:
    written = localfs_source.write_files(SEED_FILES)

    state: Dict[str, Any] = {
        "resource_name": str(localfs_source.root),
        "seeded_names": sorted(path.split("/")[-1] for path, _ in SEED_FILES),
        "uploaded_count": written,
    }

    # The folder is a sync setting, not a credential, so it goes under "sync".
    # The path is the one the *connector container* sees.
    config = {
        "sync": {
            "sync_root_path": os.getenv(
                "LOCALFS_CONNECTOR_ROOT", DEFAULT_CONNECTOR_ROOT
            ),
            "include_subfolders": True,
        }
    }

    await create_connector_and_await_sync(
        pipeshub_client,
        graph_provider,
        state,
        connector_type="LocalFS",
        connector_name=f"localfs-lifecycle-test-{uuid.uuid4().hex[:8]}",
        connector_config=config,
        expected_records=written,
    )

    yield state

    await destructor(
        localfs_source,
        pipeshub_client,
        graph_provider,
        state,
        connector_type="LocalFS",
    )

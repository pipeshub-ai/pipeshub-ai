# pyright: ignore-file

"""Nextcloud connector fixtures.

The connector syncs from ``nextcloud-source``, a Nextcloud server in the
integration stack (compose profile ``selfhosted-sources``), so it needs no
external account. See ``nextcloud_source_helper.py`` for how the test user and
its files are set up.
"""

import os
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from connector_lifecycle import (
    create_connector_and_await_sync,
    destructor,
    source_unavailable,
)
from connectors.nextcloud.nextcloud_seed import (
    DEFAULT_ADMIN_PASSWORD,
    DEFAULT_ADMIN_USER,
    SEED_FILES,
    SEED_FOLDERS,
    SYNC_PASSWORD,
    SYNC_USER,
)
from connectors.nextcloud.nextcloud_source_helper import NextcloudSourceHelper
from helper.graph_provider import GraphProviderProtocol
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]


@pytest.fixture(scope="session")
def nextcloud_source() -> NextcloudSourceHelper:
    helper = NextcloudSourceHelper(
        # The test process reaches the server on the published port.
        base_url=os.getenv("NEXTCLOUD_TEST_URL", "http://localhost:8090"),
        admin_user=os.getenv("NEXTCLOUD_ADMIN_USER", DEFAULT_ADMIN_USER),
        admin_password=os.getenv("NEXTCLOUD_ADMIN_PASSWORD", DEFAULT_ADMIN_PASSWORD),
        user=SYNC_USER,
        password=SYNC_PASSWORD,
        # Records are owned by the Nextcloud account's email, so it is the test user's.
        email=os.getenv("PIPESHUB_TEST_USER_EMAIL", "pipeshub-it@example.com"),
    )
    try:
        helper.ping()
        helper.ensure_user()
    except Exception as exc:  # noqa: BLE001 — any failure means "not available"
        source_unavailable(f"Nextcloud not reachable at {helper.base_url}: {exc}")
    return helper


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def nextcloud_connector(
    nextcloud_source: NextcloudSourceHelper,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[dict[str, Any], None]:
    # The sync user is only ever used by these tests; a folder left by an
    # earlier, interrupted run would otherwise throw the counts off.
    for path in nextcloud_source.list(""):
        if path.count("/") == 1 and path.endswith("/") and path.startswith("it-"):
            nextcloud_source.delete(path)

    folder = f"it-{uuid.uuid4().hex[:8]}"
    for name, content in SEED_FILES.items():
        nextcloud_source.put(f"{folder}/{name}", content)
    seeded = [folder, *SEED_FOLDERS, *(name.rsplit("/", 1)[-1] for name in SEED_FILES)]

    state: dict[str, Any] = {
        "resource_name": nextcloud_source.user,
        "folder": folder,
        "seeded_names": seeded,
        "uploaded_count": len(seeded),
    }
    # The connector runs inside the compose network and reaches the server by
    # service name.
    config = {
        "auth": {
            "baseUrl": os.getenv("NEXTCLOUD_CONNECTOR_URL", "http://nextcloud-source"),
            "username": nextcloud_source.user,
            "password": nextcloud_source.password,
        }
    }
    await create_connector_and_await_sync(
        pipeshub_client,
        graph_provider,
        state,
        connector_type="Nextcloud",
        connector_name=f"nextcloud-lifecycle-test-{uuid.uuid4().hex[:8]}",
        connector_config=config,
        expected_records=len(seeded),
        # Nextcloud is registered for personal scope only.
        scope="personal",
    )

    yield state

    await destructor(
        nextcloud_source, pipeshub_client, graph_provider, state, connector_type="Nextcloud"
    )

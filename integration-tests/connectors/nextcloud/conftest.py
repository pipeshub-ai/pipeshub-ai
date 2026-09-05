# pyright: ignore-file

"""Nextcloud connector fixtures.

Nextcloud is self-hostable and authenticates over WebDAV with plain
username/password, so the integration stack runs one and the connector reaches
it directly — no account to buy, and no app password to mint first.
"""

import os
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from connector_lifecycle import create_connector_and_await_sync, destructor
from connectors.nextcloud.nextcloud_source_helper import NextcloudSourceHelper
from helper.graph_provider import GraphProviderProtocol
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

# Defaults match deployment/docker-compose/docker-compose.integration.*.yml.
DEFAULT_USER = "pipeshubtest"
DEFAULT_PASSWORD = "pipeshubtest123"
TEST_ROOT = "pipeshub-test"

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
def nextcloud_source() -> NextcloudSourceHelper:
    helper = NextcloudSourceHelper(
        base_url=os.getenv("NEXTCLOUD_TEST_URL", "http://localhost:8096"),
        username=os.getenv("NEXTCLOUD_TEST_USER", DEFAULT_USER),
        password=os.getenv("NEXTCLOUD_TEST_PASSWORD", DEFAULT_PASSWORD),
        root=TEST_ROOT,
    )
    # Skip rather than fail when the instance is not up, so a partial local
    # stack stays usable. CI always brings nextcloud-source up.
    try:
        helper.ping()
        helper.ensure_root()
        helper.clear_objects()
    except Exception as exc:  # noqa: BLE001 — any failure means "not available"
        _unavailable(f"Nextcloud not reachable: {exc}")
    return helper


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def nextcloud_connector(
    nextcloud_source: NextcloudSourceHelper,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[dict[str, Any], None]:
    written = nextcloud_source.write_files(SEED_FILES)

    state: dict[str, Any] = {
        "resource_name": TEST_ROOT,
        "seeded_names": sorted(path.split("/")[-1] for path, _ in SEED_FILES),
        "uploaded_count": written,
    }

    # The connector runs inside the compose network and reaches Nextcloud by
    # service name; the test process uses the published port.
    config = {
        "auth": {
            "baseUrl": os.getenv("NEXTCLOUD_CONNECTOR_URL", "http://nextcloud-source"),
            "username": os.getenv("NEXTCLOUD_TEST_USER", DEFAULT_USER),
            "password": os.getenv("NEXTCLOUD_TEST_PASSWORD", DEFAULT_PASSWORD),
        }
    }

    await create_connector_and_await_sync(
        pipeshub_client,
        graph_provider,
        state,
        connector_type="Nextcloud",
        connector_name=f"nextcloud-lifecycle-test-{uuid.uuid4().hex[:8]}",
        connector_config=config,
        expected_records=written,
        auth_type="BASIC_AUTH",
    )

    yield state

    await destructor(
        nextcloud_source,
        pipeshub_client,
        graph_provider,
        state,
        connector_type="Nextcloud",
    )

# pyright: ignore-file

"""MariaDB connector fixtures.

Like MinIO and PostgreSQL, this connector needs no external account: the
integration compose file runs a MariaDB server for it to sync from.
"""

import os
import uuid
from typing import Any, AsyncGenerator, Dict

import pytest
import pytest_asyncio

from connector_lifecycle import create_connector_and_await_sync, destructor
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]
from helper.graph_provider import GraphProviderProtocol

from connectors.mariadb.mariadb_source_helper import MariaDBSourceHelper

# Defaults match deployment/docker-compose/docker-compose.integration.*.yml.
DEFAULT_USER = "pipeshubtest"
DEFAULT_PASSWORD = "pipeshubtest123"
DEFAULT_DB = "pipeshub_connector_test"

# One record per table, so the seed decides how many records to expect.
SEED_TABLES = {
    "articles": [
        ("Onboarding guide", "How to set up a new workspace."),
        ("Billing FAQ", "Answers to common billing questions."),
    ],
    "policies": [
        ("Leave policy", "Annual leave accrues monthly."),
        ("Security policy", "Rotate credentials every ninety days."),
    ],
}



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
def mariadb_source() -> MariaDBSourceHelper:
    helper = MariaDBSourceHelper(
        host=os.getenv("MARIADB_TEST_HOST", "localhost"),
        port=int(os.getenv("MARIADB_TEST_PORT", "3307")),
        user=os.getenv("MARIADB_TEST_USER", DEFAULT_USER),
        password=os.getenv("MARIADB_TEST_PASSWORD", DEFAULT_PASSWORD),
        database=os.getenv("MARIADB_TEST_DB", DEFAULT_DB),
    )
    # Skip rather than fail when the stack is not up, so a partial local stack
    # stays usable. CI always brings mariadb-source up.
    try:
        helper.ping()
    except Exception as exc:  # noqa: BLE001 — any failure means "not available"
        _unavailable(f"MariaDB source not reachable: {exc}")
    return helper


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def mariadb_connector(
    mariadb_source: MariaDBSourceHelper,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[Dict[str, Any], None]:
    for table, rows in SEED_TABLES.items():
        mariadb_source.create_table_with_rows(table, rows)

    state: Dict[str, Any] = {
        "resource_name": mariadb_source.database,
        "seeded_tables": sorted(SEED_TABLES),
        "uploaded_count": len(SEED_TABLES),
    }

    # The connector runs inside the compose network and reaches the source by
    # service name; the test process reaches it on the published port.
    config = {
        "auth": {
            "host": os.getenv("MARIADB_CONNECTOR_HOST", "mariadb-source"),
            "port": int(os.getenv("MARIADB_CONNECTOR_PORT", "3306")),
            "database": os.getenv("MARIADB_TEST_DB", DEFAULT_DB),
            "username": os.getenv("MARIADB_TEST_USER", DEFAULT_USER),
            "password": os.getenv("MARIADB_TEST_PASSWORD", DEFAULT_PASSWORD),
        }
    }

    await create_connector_and_await_sync(
        pipeshub_client,
        graph_provider,
        state,
        connector_type="MariaDB",
        connector_name=f"mariadb-lifecycle-test-{uuid.uuid4().hex[:8]}",
        connector_config=config,
        expected_records=len(SEED_TABLES),
    )

    yield state

    await destructor(
        mariadb_source,
        pipeshub_client,
        graph_provider,
        state,
        connector_type="MariaDB",
    )
    mariadb_source.drop_tables()

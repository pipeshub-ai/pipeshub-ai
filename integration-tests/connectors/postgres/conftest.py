# pyright: ignore-file

"""PostgreSQL connector fixtures.

Like MinIO, this connector needs no external account: the integration compose
file runs a PostgreSQL server for it to sync from. That server is a connector
*source* — data to be indexed — and is separate from the stores the platform
itself uses.
"""

import os
import uuid
from typing import Any, AsyncGenerator, Dict

import pytest
import pytest_asyncio

from connector_lifecycle import create_connector_and_await_sync, destructor
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]
from helper.graph_provider import GraphProviderProtocol

from connectors.postgres.postgres_source_helper import PostgresSourceHelper

# Defaults match deployment/docker-compose/docker-compose.integration.*.yml.
DEFAULT_USER = "pipeshubtest"
DEFAULT_PASSWORD = "pipeshubtest123"
DEFAULT_DB = "pipeshub_connector_test"
TEST_SCHEMA = "pipeshub_test"

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


def _host_for_tests() -> str:
    """Host as the test process reaches it (published port)."""
    return os.getenv("POSTGRES_TEST_HOST", "localhost")


def _port_for_tests() -> str:
    return os.getenv("POSTGRES_TEST_PORT", "5433")



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
def postgres_source() -> PostgresSourceHelper:
    user = os.getenv("POSTGRES_TEST_USER", DEFAULT_USER)
    password = os.getenv("POSTGRES_TEST_PASSWORD", DEFAULT_PASSWORD)
    database = os.getenv("POSTGRES_TEST_DB", DEFAULT_DB)
    dsn = (
        f"host={_host_for_tests()} port={_port_for_tests()} "
        f"dbname={database} user={user} password={password}"
    )
    helper = PostgresSourceHelper(dsn, schema=TEST_SCHEMA)
    # Skip rather than fail when the stack is not up: this suite runs both in
    # CI, where the source database is always present, and locally against a
    # partial stack.
    try:
        helper.ensure_schema()
    except Exception as exc:  # noqa: BLE001 — any failure means "not available"
        _unavailable(f"PostgreSQL source not reachable at {_host_for_tests()}: {exc}")
    return helper


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def postgres_connector(
    postgres_source: PostgresSourceHelper,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[Dict[str, Any], None]:
    for table, rows in SEED_TABLES.items():
        postgres_source.create_table_with_rows(table, rows)

    state: Dict[str, Any] = {
        "resource_name": TEST_SCHEMA,
        "seeded_tables": sorted(SEED_TABLES),
        "uploaded_count": len(SEED_TABLES),
    }

    # The connector runs inside the compose network and reaches the source by
    # service name; the test process reaches it on the published port.
    config = {
        "auth": {
            "host": os.getenv("POSTGRES_CONNECTOR_HOST", "postgres-source"),
            "port": int(os.getenv("POSTGRES_CONNECTOR_PORT", "5432")),
            "database": os.getenv("POSTGRES_TEST_DB", DEFAULT_DB),
            "username": os.getenv("POSTGRES_TEST_USER", DEFAULT_USER),
            "password": os.getenv("POSTGRES_TEST_PASSWORD", DEFAULT_PASSWORD),
        }
    }

    await create_connector_and_await_sync(
        pipeshub_client,
        graph_provider,
        state,
        connector_type="PostgreSQL",
        connector_name=f"postgres-lifecycle-test-{uuid.uuid4().hex[:8]}",
        connector_config=config,
        expected_records=len(SEED_TABLES),
    )

    yield state

    await destructor(
        postgres_source,
        pipeshub_client,
        graph_provider,
        state,
        connector_type="PostgreSQL",
    )
    postgres_source.drop_schema()

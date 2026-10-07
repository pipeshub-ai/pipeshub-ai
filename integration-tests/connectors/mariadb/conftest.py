# pyright: ignore-file

"""MariaDB connector fixtures.

Like PostgreSQL, this connector needs no external account: the integration
compose file runs a MariaDB server (``mariadb-source``) for it to sync from.
That server is a connector *source* — data to be indexed — and is separate from
the stores the platform itself uses.
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
from connectors.mariadb.mariadb_seed import (
    CHILD_TABLE,
    DROPPED_TABLE,
    KEYED_TABLE,
    KEYLESS_TABLE,
    NEW_TABLE,
    SEED_TABLES,
    VIEW,
)
from connectors.mariadb.mariadb_source_helper import MariaDBSourceHelper
from helper.graph_provider import GraphProviderProtocol
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

# Defaults match deployment/docker-compose/docker-compose.integration.*.yml.
DEFAULT_USER = "pipeshubtest"
DEFAULT_PASSWORD = "pipeshubtest123"
DEFAULT_DB = "pipeshub_connector_test"


def _env(name: str, default: str) -> str:
    return os.getenv(name, default)


@pytest.fixture(scope="session")
def mariadb_source() -> MariaDBSourceHelper:
    helper = MariaDBSourceHelper(
        # The test process reaches the source on the published port.
        host=_env("MARIADB_TEST_HOST", "localhost"),
        port=int(_env("MARIADB_TEST_PORT", "3307")),
        user=_env("MARIADB_TEST_USER", DEFAULT_USER),
        password=_env("MARIADB_TEST_PASSWORD", DEFAULT_PASSWORD),
        database=_env("MARIADB_TEST_DB", DEFAULT_DB),
    )
    try:
        helper.ping()
    except Exception as exc:  # noqa: BLE001 — any failure means "not available"
        source_unavailable(
            f"MariaDB source not reachable at {_env('MARIADB_TEST_HOST', 'localhost')}: {exc}"
        )
    return helper


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def mariadb_connector(
    mariadb_source: MariaDBSourceHelper,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[dict[str, Any], None]:
    mariadb_source.reset(
        tables=[CHILD_TABLE, NEW_TABLE, DROPPED_TABLE, *SEED_TABLES],
        views=[VIEW],
    )
    mariadb_source.create_table_with_rows(KEYED_TABLE, SEED_TABLES[KEYED_TABLE])
    mariadb_source.create_table_with_rows(
        KEYLESS_TABLE, SEED_TABLES[KEYLESS_TABLE], primary_key=False
    )
    mariadb_source.create_child_table(CHILD_TABLE, KEYED_TABLE)
    mariadb_source.create_view(VIEW, KEYED_TABLE)

    seeded = sorted([*SEED_TABLES, CHILD_TABLE])
    state: dict[str, Any] = {
        "resource_name": mariadb_source.database,
        "database": mariadb_source.database,
        "seeded_tables": seeded,
        "uploaded_count": len(seeded),
    }

    # The connector runs inside the compose network and reaches the source by
    # service name.
    config = {
        "auth": {
            "host": _env("MARIADB_CONNECTOR_HOST", "mariadb-source"),
            "port": _env("MARIADB_CONNECTOR_PORT", "3306"),
            "database": mariadb_source.database,
            "username": _env("MARIADB_TEST_USER", DEFAULT_USER),
            "password": _env("MARIADB_TEST_PASSWORD", DEFAULT_PASSWORD),
        }
    }

    await create_connector_and_await_sync(
        pipeshub_client,
        graph_provider,
        state,
        connector_type="MariaDB",
        connector_name=f"mariadb-lifecycle-test-{uuid.uuid4().hex[:8]}",
        connector_config=config,
        expected_records=len(seeded),
        # MariaDB is registered for team scope only.
        scope="team",
    )

    yield state

    await destructor(
        mariadb_source,
        pipeshub_client,
        graph_provider,
        state,
        connector_type="MariaDB",
    )
    mariadb_source.drop_created()

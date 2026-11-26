# pyright: ignore-file

"""Snowflake connector fixtures.

Unlike PostgreSQL and MariaDB, Snowflake cannot run in the integration stack,
so this suite needs an account (a trial account will do). It skips when the
account's settings are absent; set them as described in
``snowflake_source_helper``. No CI shard selects it.

Each run seeds a schema of its own, limits the connector to it with the sync
filters (a trial account shares sample databases with every role), and drops
the schema afterwards.
"""

import os
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest_asyncio
from connector_lifecycle import destructor
from connectors.snowflake.snowflake_source_helper import (
    CONNECTOR_ENV,
    KEY_ENV,
    OWNER_ENV,
    SnowflakeSourceHelper,
    key_pair_auth,
)
from helper.graph_provider import GraphProviderProtocol
from helper.graph_provider_utils import wait_until_graph_condition
from helper.source_credentials import missing_env, source_unavailable
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

SCHEMA = f"IT_{uuid.uuid4().hex[:8].upper()}"
# A second schema, which the reader loses access to partway through.
HIDDEN_SCHEMA = f"{SCHEMA}_HIDDEN"

CUSTOMERS, ORDERS, NOTES, NEW_TABLE, DROPPED_TABLE = "CUSTOMERS", "ORDERS", "NOTES", "RUNBOOKS", "SCRATCH"
VIEW, STAGE, BARE_STAGE, HIDDEN_TABLE = "ORDER_SUMMARY", "DOCS", "ARCHIVE", "PAYROLL"
SEEDED_TABLES = (CUSTOMERS, ORDERS, NOTES, DROPPED_TABLE)
# Every column of the seeded tables in the main schema, for the column-record count.
SEEDED_COLUMNS = 4 + 4 + 1 + 1


def _seed_statements(helper: SnowflakeSourceHelper) -> list[str]:
    q = helper.qualified
    return [
        f"CREATE TABLE {q(CUSTOMERS)} (ID NUMBER PRIMARY KEY, NAME STRING, EMAIL STRING, TIER STRING)",
        f"INSERT INTO {q(CUSTOMERS)} VALUES (1, 'Ada', 'ada@example.com', 'GOLD'), "
        "(2, 'Lin', 'lin@example.com', 'SILVER'), (3, 'Sam', 'sam@example.com', 'GOLD')",
        f"CREATE TABLE {q(ORDERS)} (ID NUMBER PRIMARY KEY, CUSTOMER_ID NUMBER REFERENCES {q(CUSTOMERS)}(ID), "
        "STATUS STRING, AMOUNT NUMBER(10,2))",
        f"INSERT INTO {q(ORDERS)} VALUES (1, 1, 'OPEN', 10.50), (2, 2, 'CLOSED', 99.00), (3, 1, 'OPEN', 5.25)",
        f"CREATE TABLE {q(NOTES)} (NOTE STRING)",
        f"INSERT INTO {q(NOTES)} VALUES ('Call back on Monday'), ('Renewal due in May')",
        f"CREATE TABLE {q(DROPPED_TABLE)} (ID NUMBER)",
        f"CREATE VIEW {q(VIEW)} AS SELECT STATUS, COUNT(*) AS N FROM {q(ORDERS)} GROUP BY STATUS",
        # Files are listed from a stage's directory table; the bare stage has none.
        f"CREATE STAGE {q(STAGE)} DIRECTORY = (ENABLE = TRUE)",
        f"COPY INTO @{q(STAGE)}/orders.csv FROM (SELECT * FROM {q(ORDERS)}) "
        "FILE_FORMAT = (TYPE = CSV COMPRESSION = NONE) SINGLE = TRUE OVERWRITE = TRUE",
        f"ALTER STAGE {q(STAGE)} REFRESH",
        f"CREATE STAGE {q(BARE_STAGE)}",
        f"CREATE TABLE {q(HIDDEN_TABLE, HIDDEN_SCHEMA)} (ID NUMBER, AMOUNT NUMBER)",
    ]


def _require_account() -> None:
    missing = missing_env([*CONNECTOR_ENV, *OWNER_ENV])
    if missing or not any(os.getenv(name) for name in KEY_ENV):
        source_unavailable(
            "Snowflake test account not configured",
            secrets=[*CONNECTOR_ENV, *OWNER_ENV, "SNOWFLAKE_TEST_PRIVATE_KEY_FILE",
                     "SNOWFLAKE_TEST_PRIVATE_KEY_PASSPHRASE"],
        )


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def snowflake_source() -> AsyncGenerator[SnowflakeSourceHelper, None]:
    _require_account()
    helper = SnowflakeSourceHelper(
        os.environ["SNOWFLAKE_TEST_DATABASE"], SCHEMA, os.environ["SNOWFLAKE_TEST_ROLE"],
    )
    try:
        await helper.whoami()
    except Exception as exc:  # noqa: BLE001 — any failure means "not available"
        await helper.close()
        source_unavailable(f"Snowflake test account not reachable: {exc}")
    yield helper
    await helper.close()


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def snowflake_connector(
    snowflake_source: SnowflakeSourceHelper,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[dict[str, Any], None]:
    database = snowflake_source.database
    state: dict[str, Any] = {"resource_name": SCHEMA, "schema": SCHEMA, "hidden_schema": HIDDEN_SCHEMA}
    # Cleanup runs even when setup fails: this account is real, and a failed run
    # must not leave schemas, or a connector syncing them, behind.
    try:
        await snowflake_source.create_schema()
        await snowflake_source.create_schema(HIDDEN_SCHEMA)
        await snowflake_source.run(*_seed_statements(snowflake_source))
        await snowflake_source.grant_reads()
        await snowflake_source.grant_reads(HIDDEN_SCHEMA)
        await snowflake_source.run(*(
            f"GRANT READ ON STAGE {snowflake_source.qualified(stage)} TO ROLE {snowflake_source.reader_role}"
            for stage in (STAGE, BARE_STAGE)
        ))

        instance = pipeshub_client.create_connector(
            connector_type="Snowflake",
            instance_name=f"snowflake-lifecycle-test-{uuid.uuid4().hex[:8]}",
            scope="team",
            config={"auth": key_pair_auth()},
            auth_type="KEY_PAIR",
        )
        connector_id = instance.connector_id
        state["connector_id"] = connector_id

        # Only this run's schemas: a trial account shares large sample databases with every role.
        pipeshub_client.update_connector_filters_sync_safe(
            connector_id,
            filters={"sync": {"values": {
                "databases": {"operator": "in", "value": [database], "type": "multiselect"},
                "schemas": {"operator": "in", "value": [f"{database}.{SCHEMA}", f"{database}.{HIDDEN_SCHEMA}"],
                            "type": "multiselect"},
            }}},
        )
        pipeshub_client.toggle_sync(connector_id, enable=True)

        async def _synced() -> bool:
            names = set(await graph_provider.fetch_record_names(connector_id))
            return {*SEEDED_TABLES, HIDDEN_TABLE} <= names

        await wait_until_graph_condition(
            connector_id, check=_synced, timeout=600, poll_interval=10, description="first Snowflake sync",
        )
        state["full_sync_count"] = await graph_provider.count_records(connector_id)

        yield state
    finally:
        try:
            if state.get("connector_id"):
                await destructor(
                    snowflake_source, pipeshub_client, graph_provider, state, connector_type="Snowflake",
                )
        finally:
            await snowflake_source.drop_schema(HIDDEN_SCHEMA)
            await snowflake_source.drop_schema()

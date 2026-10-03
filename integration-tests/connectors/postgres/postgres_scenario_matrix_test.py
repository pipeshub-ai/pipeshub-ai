# pyright: ignore-file

"""PostgreSQL in the shared scenario matrix (``connectors/scenario_matrix.py``).

The connector turns each table into one record, so an item here is a one-row
table in a schema of its own on the ``postgres-source`` container. Its content
is that row; editing the row is a content change. Incremental sync notices
tables through the server's DML counters and its list of tables.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from connector_lifecycle import create_connector_and_await_sync, destructor

from connectors.postgres.conftest import DEFAULT_DB, DEFAULT_PASSWORD, DEFAULT_USER
from connectors.postgres.postgres_source_helper import PostgresSourceHelper
from connectors.scenario_matrix import (
    Action,
    ConnectorScenarioMatrix,
    Role,
    ScenarioAdapter,
    SourceItem,
)
from helper.graph_provider import GraphProviderProtocol
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

MATRIX_SCHEMA = "pipeshub_mx"
ROW_TITLE = "matrix"


class PostgresAdapter(ScenarioAdapter):
    source = "PostgreSQL"

    def __init__(self, *, postgres: PostgresSourceHelper, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.postgres = postgres

    async def create_item(self, role: Role, text: str, token: str) -> SourceItem:
        table = f"mx_{role.value}_{token}"
        self.postgres.create_table_with_rows(table, [(ROW_TITLE, text)])
        return SourceItem(
            role=role, key=table, record_name=table, text=text, token=token,
            external_id=f"{self.postgres.schema}.{table}",
        )

    async def update_content(self, item: SourceItem, text: str, token: str) -> SourceItem:
        self.postgres.update_body(item.key, ROW_TITLE, text)
        return SourceItem(
            role=item.role, key=item.key, record_name=item.record_name, text=text,
            token=token, external_id=item.external_id,
        )

    async def delete_item(self, item: SourceItem) -> None:
        self.postgres.drop_table(item.key)

    async def exclusion_filter(self, excluded: SourceItem, kept: list[SourceItem]) -> dict[str, Any]:
        return {
            "sync": {
                "values": {
                    "tables": {
                        "operator": "not_in",
                        "value": [excluded.external_id],
                        "type": "multiselect",
                    }
                }
            }
        }


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def scenario_adapter(
    postgres_source: PostgresSourceHelper,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[PostgresAdapter, None]:
    postgres = postgres_source.for_schema(MATRIX_SCHEMA)
    # A schema an interrupted run left behind would become records here too.
    postgres.drop_schema()
    postgres.ensure_schema()
    postgres.create_table_with_rows("mx_seed", [(ROW_TITLE, "Scenario matrix seed table.")])

    state: dict[str, Any] = {"resource_name": MATRIX_SCHEMA}
    try:
        await create_connector_and_await_sync(
            pipeshub_client,
            graph_provider,
            state,
            connector_type="PostgreSQL",
            connector_name=f"postgres-matrix-{uuid.uuid4().hex[:8]}",
            connector_config={
                "auth": {
                    "host": os.getenv("POSTGRES_CONNECTOR_HOST", "postgres-source"),
                    "port": int(os.getenv("POSTGRES_CONNECTOR_PORT", "5432")),
                    "database": os.getenv("POSTGRES_TEST_DB", DEFAULT_DB),
                    "username": os.getenv("POSTGRES_TEST_USER", DEFAULT_USER),
                    "password": os.getenv("POSTGRES_TEST_PASSWORD", DEFAULT_PASSWORD),
                }
            },
            expected_records=1,
            scope="team",
        )
        yield PostgresAdapter(
            postgres=postgres,
            client=pipeshub_client,
            graph=graph_provider,
            connector_id=state["connector_id"],
        )
    finally:
        if "connector_id" in state:
            await destructor(
                postgres, pipeshub_client, graph_provider, state, connector_type="PostgreSQL"
            )
        postgres.drop_schema()


@pytest.mark.integration
@pytest.mark.postgres
class TestPostgresScenarioMatrix(ConnectorScenarioMatrix):
    SOURCE = "PostgreSQL"
    UNSUPPORTED = {
        Action.UPDATE_METADATA.value: (
            "a table's record is keyed by its schema-qualified name (schema.table), so "
            "renaming a table is a drop and a new table, not an edit of the same record"
        ),
        Action.CHANGE_PERMISSION.value: (
            "every table is shared with the whole org (_get_permissions returns one ORG "
            "permission); database GRANTs are not read"
        ),
    }

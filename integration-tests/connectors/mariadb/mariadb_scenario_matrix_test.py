# pyright: ignore-file

"""MariaDB in the shared scenario matrix (``connectors/scenario_matrix.py``).

The connector turns each base table into one record, so an item here is a
one-row table on the ``mariadb-source`` container. Its content is that row;
editing the row is a content change. Incremental sync notices tables through
their update time, row estimate and auto-increment, and the table list.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from connector_lifecycle import create_connector_and_await_sync, destructor

from connectors.mariadb.conftest import DEFAULT_PASSWORD, DEFAULT_USER
from connectors.mariadb.mariadb_source_helper import MariaDBSourceHelper
from connectors.scenario_matrix import (
    Action,
    ConnectorScenarioMatrix,
    FilterOutcome,
    Role,
    ScenarioAdapter,
    SourceItem,
)
from helper.graph_provider import GraphProviderProtocol
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

TABLE_PREFIX = "mx_"
ROW_TITLE = "matrix"


class MariaDBAdapter(ScenarioAdapter):
    source = "MariaDB"
    # A table filter change runs a full sync whose _remove_stale_tables deletes
    # the record of every table the filter no longer lists.
    filter_outcome = FilterOutcome.REMOVED

    def __init__(self, *, mariadb: MariaDBSourceHelper, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.mariadb = mariadb

    async def create_item(self, role: Role, text: str, token: str) -> SourceItem:
        table = f"{TABLE_PREFIX}{role.value}_{token}"
        # Keyless like the suite's TC-ROWS-001 table: an edit then shows only in the
        # table's update time, the signal every content change here must reach.
        self.mariadb.create_table_with_rows(table, [(ROW_TITLE, text)], primary_key=False)
        return SourceItem(
            role=role, key=table, record_name=table, text=text, token=token,
            external_id=self.mariadb.fqn(table),
        )

    async def update_content(self, item: SourceItem, text: str, token: str) -> SourceItem:
        assert self.mariadb.update_body(item.key, ROW_TITLE, text) == 1
        return SourceItem(
            role=item.role, key=item.key, record_name=item.record_name, text=text,
            token=token, external_id=item.external_id,
        )

    async def delete_item(self, item: SourceItem) -> None:
        self.mariadb.drop_table(item.key)

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
    mariadb_source: MariaDBSourceHelper,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[MariaDBAdapter, None]:
    # Tables an interrupted run left behind would become records here too.
    mariadb_source.drop_tables_starting(TABLE_PREFIX)
    mariadb_source.create_table_with_rows(
        f"{TABLE_PREFIX}seed", [(ROW_TITLE, "Scenario matrix seed table.")]
    )

    state: dict[str, Any] = {"resource_name": mariadb_source.database}
    try:
        await create_connector_and_await_sync(
            pipeshub_client,
            graph_provider,
            state,
            connector_type="MariaDB",
            connector_name=f"mariadb-matrix-{uuid.uuid4().hex[:8]}",
            connector_config={
                "auth": {
                    "host": os.getenv("MARIADB_CONNECTOR_HOST", "mariadb-source"),
                    "port": os.getenv("MARIADB_CONNECTOR_PORT", "3306"),
                    "database": mariadb_source.database,
                    "username": os.getenv("MARIADB_TEST_USER", DEFAULT_USER),
                    "password": os.getenv("MARIADB_TEST_PASSWORD", DEFAULT_PASSWORD),
                }
            },
            expected_records=1,
            scope="team",
        )
        yield MariaDBAdapter(
            mariadb=mariadb_source,
            client=pipeshub_client,
            graph=graph_provider,
            connector_id=state["connector_id"],
        )
    finally:
        if "connector_id" in state:
            await destructor(
                mariadb_source, pipeshub_client, graph_provider, state, connector_type="MariaDB"
            )
        mariadb_source.drop_tables_starting(TABLE_PREFIX)


@pytest.mark.integration
@pytest.mark.mariadb
class TestMariaDBScenarioMatrix(ConnectorScenarioMatrix):
    SOURCE = "MariaDB"
    UNSUPPORTED = {
        Action.UPDATE_METADATA.value: (
            "a table's record is keyed by its qualified name (database.table), so renaming "
            "a table is a drop and a new table, not an edit of the same record"
        ),
        Action.CHANGE_PERMISSION.value: (
            "every table is shared with the whole org (_get_permissions returns one ORG "
            "permission); database GRANTs are not read"
        ),
    }

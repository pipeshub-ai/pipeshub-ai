"""A ServiceNow department stays one group across syncs, on real Neo4j and ArangoDB.

The ServiceNow connector stores its companies, departments, locations and cost
centres as user groups. Each sync built them with a fresh id and wrote them by
that id, so a department read twice became two group nodes with the same
ServiceNow sys_id. The members a user sync attached, and the knowledge-base
permissions that name the department, were each written to whichever copy the
lookup by sys_id found first, so they could end up on different copies.

The test runs the connector's department sync twice over the real processor
and graph store, with the ServiceNow API faked to return the same department
both times (a full sync, as after a resync, or the department edited in
ServiceNow).

Runs in backend-matrix on both graph jobs. Environment: NEO4J_IT_URI,
NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.connectors.sources.servicenow.servicenow.connector import ServiceNowConnector
from app.connectors.sources.servicenow.servicenow.constants import (
    ORGANIZATIONAL_ENTITIES,
)
from app.sources.external.servicenow.models import TableAPIRecord, TableAPIResponse
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.models.entities import AppUserGroup
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

ARANGO_DB = "servicenow_org_groups_it"
DEPARTMENT_SYS_ID = "dept-sys-id-engineering"

logger = logging.getLogger("servicenow-org-groups-it")


@dataclass
class _World:
    graph: IGraphDBProvider
    connector: ServiceNowConnector
    org_id: str
    connector_id: str

    async def sync_departments(self, name: str = "Engineering") -> None:
        """One department sync, reading every department (no checkpoint yet)."""
        sync_point = MagicMock(
            read_sync_point=AsyncMock(return_value=None), update_sync_point=AsyncMock()
        )
        self.connector.org_entity_sync_points = {"department": sync_point}
        datasource = MagicMock(
            get_now_table_tableName=AsyncMock(
                return_value=TableAPIResponse(result=[TableAPIRecord(
                    sys_id=DEPARTMENT_SYS_ID, name=name,
                    sys_created_on="2026-01-01 00:00:00", sys_updated_on="2026-01-02 00:00:00",
                )], total_count=1)  # X-Total-Count: this one row is the whole listing
            )
        )
        with patch.object(self.connector, "_get_fresh_datasource", AsyncMock(return_value=datasource)):
            await self.connector._sync_single_organizational_entity(
                "department", ORGANIZATIONAL_ENTITIES["department"]
            )

    async def departments(self) -> list[AppUserGroup]:
        groups = await self.graph.get_user_groups(self.connector_id, self.org_id)
        return [g for g in groups if g.source_user_group_id == DEPARTMENT_SYS_ID]


@pytest.fixture(params=["neo4j", "arango"])
async def world(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_World]:
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            graph = await (
                connect_neo4j(logger, monkeypatch) if request.param == "neo4j"
                else connect_arango(logger, ARANGO_DB)
            )
        except Exception as exc:
            backend_unavailable(request.param, exc)
        disconnect = getattr(graph, "disconnect", None)
        if disconnect is not None:
            cleanup.push_async_callback(disconnect)

        run = uuid.uuid4().hex[:10]
        org_id, connector_id = f"org-sng-{run}", f"conn-sng-{run}"
        processor = DataSourceEntitiesProcessor(logger, GraphDataStore(logger, graph), MagicMock())
        processor.org_id = org_id
        with patch("app.connectors.sources.servicenow.servicenow.connector.ServicenowApp"):
            connector = ServiceNowConnector(
                logger=logger,
                data_entities_processor=processor,
                data_store_provider=MagicMock(),
                config_service=MagicMock(),
                connector_id=connector_id,
                scope="team",
                created_by="it-user",
            )
        connector.connector_name = Connectors.SERVICENOW
        w = _World(graph=graph, connector=connector, org_id=org_id, connector_id=connector_id)

        async def remove() -> None:
            with contextlib.suppress(Exception):
                ids = [g.id for g in await graph.get_user_groups(connector_id, org_id)]
                if ids:
                    await graph.delete_nodes_and_edges(ids, CollectionNames.GROUPS.value)

        cleanup.push_async_callback(remove)
        yield w


async def test_a_department_synced_twice_is_one_group(world: _World) -> None:
    await world.sync_departments()
    first = await world.departments()
    assert len(first) == 1, f"first sync wrote {len(first)} groups"

    await world.sync_departments()
    after = await world.departments()

    assert [g.id for g in after] == [first[0].id], (
        f"second sync left {len(after)} groups for one department: {sorted(g.id for g in after)}"
    )


async def test_a_renamed_department_is_renamed_in_place(world: _World) -> None:
    await world.sync_departments("Engineering")
    first = await world.departments()

    await world.sync_departments("Platform Engineering")
    after = await world.departments()

    assert [g.id for g in after] == [first[0].id]
    assert after[0].name == "DEPARTMENT_Platform Engineering"

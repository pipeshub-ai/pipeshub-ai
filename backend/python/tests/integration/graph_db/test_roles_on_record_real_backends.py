"""Against real servers: ``DataSourceEntitiesProcessor.get_roles_on_record`` reads the role on each user's and group's
own permission edge into a record, from each store's edge shape, on Neo4j and ArangoDB. PostgreSQL is in
``postgres/test_roles_on_record.py``.

  docker compose -f deployment/docker-compose/docker-compose.integration.graph-db.yml \\
    up -d --wait neo4j-graph-it arango-graph-it
  cd backend/python && pytest tests/integration/graph_db/test_roles_on_record_real_backends.py -m integration
"""
from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.models.entities import Person
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from tests.integration.graph_db.test_record_graph_write_concurrency import (
    _open_arango,
    _open_neo4j,
)
from tests.integration.graph_db.test_record_people_real_backends import _cleanup

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]
logger = logging.getLogger("roles-on-record-it")


@pytest.fixture(params=["neo4j", "arango"])
async def world(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[dict[str, Any]]:
    try:
        provider = await (_open_arango() if request.param == "arango" else _open_neo4j(monkeypatch, explicit=False))
    except Exception as exc:
        pytest.skip(f"{request.param} not available: {exc}")
    org = f"org-it-{uuid.uuid4().hex[:10]}"
    try:
        await _seed(provider, org)
        yield {"provider": provider, "org": org}
    finally:
        if not isinstance(provider, Neo4jProvider):
            await provider.http_client.execute_aql(
                f"FOR d IN {CollectionNames.GROUPS.value} FILTER d.orgId == @org REMOVE d IN {CollectionNames.GROUPS.value}",
                {"org": org},
            )
            await provider.http_client.execute_aql(
                f"FOR d IN {CollectionNames.PEOPLE.value} FILTER d.orgId == @org REMOVE d IN {CollectionNames.PEOPLE.value}",
                {"org": org},
            )
            await provider.http_client.execute_aql(
                f"FOR d IN {CollectionNames.RECORD_GROUPS.value} FILTER d.orgId == @org "
                f"REMOVE d IN {CollectionNames.RECORD_GROUPS.value}",
                {"org": org},
            )
            await provider.http_client.execute_aql(
                f"FOR e IN {CollectionNames.INHERIT_PERMISSIONS.value} FILTER CONTAINS(e._from, @org) "
                f"REMOVE e IN {CollectionNames.INHERIT_PERMISSIONS.value}",
                {"org": org},
            )
        await _cleanup(provider, org)


async def _seed(provider: Any, org: str) -> None:  # noqa: ANN401
    """A record with a user's own WRITER edge, a group's own READER edge and an external collaborator's (a Person's)
    READER edge, and a user whose READER edge is on the record group the record inherits from."""
    app = f"{org}-app"
    await provider.batch_upsert_nodes([
        {"id": f"{org}-writer", "userId": f"{org}-writer", "orgId": org, "email": f"Writer@{org}.test", "fullName": "W"},
        {"id": f"{org}-inherited", "userId": f"{org}-inherited", "orgId": org, "email": f"inherited@{org}.test",
         "fullName": "I"},
    ], CollectionNames.USERS.value)
    await provider.batch_upsert_nodes([
        {"id": f"{org}-grp", "orgId": org, "name": "Eng", "externalGroupId": f"eng@{org}.test",
         "connectorName": Connectors.GOOGLE_DRIVE_WORKSPACE.value, "connectorId": app, "createdAtTimestamp": 1,
         "updatedAtTimestamp": 1},
    ], CollectionNames.GROUPS.value)
    await provider.batch_upsert_nodes([
        {"id": f"{org}-rg", "orgId": org, "groupName": "Board", "groupType": "DRIVE",
         "connectorName": Connectors.GOOGLE_DRIVE_WORKSPACE.value, "connectorId": app, "createdAtTimestamp": 1},
    ], CollectionNames.RECORD_GROUPS.value)
    await provider.batch_upsert_nodes([
        {"id": f"{org}-r", "orgId": org, "recordName": "plan.txt", "recordType": "FILE",
         "externalRecordId": f"ext-{org}", "origin": "CONNECTOR", "connectorName": Connectors.GOOGLE_DRIVE_WORKSPACE.value,
         "connectorId": app, "version": 0, "createdAtTimestamp": 1, "updatedAtTimestamp": 1},
    ], CollectionNames.RECORDS.value)
    await provider.batch_upsert_people([Person(id=f"{org}-partner", org_id=org, email=f"Partner@{org}.example.org")])
    record, rg = f"records/{org}-r", f"recordGroups/{org}-rg"
    await provider.batch_create_edges([{"_from": record, "_to": rg, "createdAtTimestamp": 1}],
                                      CollectionNames.INHERIT_PERMISSIONS.value)
    await provider.batch_create_edges([
        {"_from": f"users/{org}-writer", "_to": record, "type": "USER", "role": "WRITER", "createdAtTimestamp": 1},
        {"_from": f"groups/{org}-grp", "_to": record, "type": "GROUP", "role": "READER", "createdAtTimestamp": 1},
        {"_from": f"person/{org}-partner", "_to": record, "type": "USER", "role": "READER", "createdAtTimestamp": 1},
        {"_from": f"users/{org}-inherited", "_to": rg, "type": "USER", "role": "READER", "createdAtTimestamp": 1},
    ], CollectionNames.PERMISSION.value)


async def test_roles_are_those_of_each_principals_own_edge_into_the_record(world: dict[str, Any]) -> None:
    provider, org = world["provider"], world["org"]
    processor = DataSourceEntitiesProcessor(logger, GraphDataStore(logger, provider), AsyncMock())
    processor.org_id = org

    users, groups = await processor.get_roles_on_record(f"{org}-r")

    assert users == {f"writer@{org}.test": "WRITER", f"partner@{org}.example.org": "READER"}, (
        "a user reaching it through the record group has no own edge"
    )
    assert groups == {f"eng@{org}.test": "READER"}

    for email in (f"Writer@{org}.test", f"Partner@{org}.example.org"):
        await processor.delete_principal_permission_from_record(f"{org}-r", email, f"{org}-app")

    assert await processor.get_roles_on_record(f"{org}-r") == ({}, {f"eng@{org}.test": "READER"})

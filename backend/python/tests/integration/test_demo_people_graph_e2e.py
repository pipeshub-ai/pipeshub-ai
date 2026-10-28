"""The demo sync's people and accounts, in a real graph, equal the fixture's ground truth.

Real: ``DemoConnector.run_sync`` over the bundled fixture (with ``acme-corp.d``),
``DataSourceEntitiesProcessor`` (so ``record_people`` and ``record_organizations``
write the edges as in production), ``GraphDataStore`` and the graph database,
Neo4j and ArangoDB. Stubbed: the message broker (events are captured) and the
config service. Then reads the graph back and compares it with
``expected_graph``: every person edge by role (members as users, outsiders as
person nodes), every FOR_ACCOUNT and dealOf link, and the external people and
organisations created. Extraction does not run, so ``mentioned_organizations``
is not checked here.

  docker compose -f deployment/docker-compose/docker-compose.integration.graph-db.yml \\
    up -d --wait neo4j-graph-it arango-graph-it
  cd backend/python && pytest tests/integration/test_demo_people_graph_e2e.py -m integration
"""

from __future__ import annotations

import contextlib
import logging
import os
import uuid
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors, EntityRelations
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.connectors.sources.demo.connector import DemoConnector
from app.connectors.sources.demo.fixture_loader import load_fixture
from app.connectors.sources.demo.ground_truth import ExpectedGraph, expected_graph
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from tests.integration.github_teams.harness import Producer, remove_owned
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(900)]

ARANGO_DB = "demo_people_it"
logging.getLogger("neo4j").setLevel(logging.ERROR)
logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)
os.environ.setdefault("SECRET_KEY", "demo-people-it-secret-key-0123456789abcdef")

RELATIONS = CollectionNames.ENTITY_RELATIONS.value


def _key(doc: dict) -> str:
    return str(doc.get("_key") or doc.get("id"))


def _source(edge: dict) -> str:
    return edge.get("_from") or f"{edge['from_collection']}/{edge['from_id']}"


class DemoWorld:
    """One org with the demo connector synced into a real graph."""

    def __init__(self, graph: Any) -> None:  # noqa: ANN401
        self.graph = graph
        suffix = uuid.uuid4().hex[:10]
        self.org_id = f"org-demo-{suffix}"
        self.connector_id = f"demo-{suffix}"
        self.logger = logging.getLogger(f"demo_people_it.{suffix}")
        self.store = GraphDataStore(self.logger, graph)
        self.processor = DataSourceEntitiesProcessor(self.logger, self.store, MagicMock())
        self.processor.org_id = self.org_id
        self.processor.messaging_producer = Producer()
        self._fixture: dict[str, Any] = {"people": []}

    async def seed(self, fx: dict[str, Any]) -> None:
        self._fixture = fx
        await self._drop_stale_users(fx)
        now = get_epoch_timestamp_in_ms()
        await self.graph.batch_upsert_nodes(
            [{"id": self.org_id, "name": "Acme Corp", "accountType": "enterprise", "isActive": True,
              "createdAtTimestamp": now, "updatedAtTimestamp": now}], collection=CollectionNames.ORGS.value)
        await self.graph.batch_upsert_nodes(
            [{"id": self.connector_id, "name": "Demo", "type": Connectors.DEMO.value, "appGroup": "Demo",
              "scope": "team", "isActive": True, "authType": "NONE", "isConfigured": True,
              "isAuthenticated": True, "orgId": self.org_id, "createdAtTimestamp": now,
              "updatedAtTimestamp": now}], collection=CollectionNames.APPS.value)
        # The sign-in personas have accounts, as after the first-run script; the
        # sync waits for them otherwise. The other members get the inactive row
        # the sync would write: the provider files a new user under the first
        # org in the database, which in this shared database is another test's.
        for person in fx["people"]:
            if person.get("kind", "member") == "member":
                await self._add_account(person["email"], now, active=bool(person.get("login")))

    async def _drop_stale_users(self, fx: dict[str, Any]) -> None:
        """Users with the demo's addresses outside this run's org.

        The app-user upsert files a user it creates under the first org in the
        database and looks users up by address in every org, so a stub an
        earlier run left under another org would take this run's app link, and
        every person named by user id would resolve to another org's member,
        i.e. to nobody. The fixture's addresses are reserved for the demo
        (``acme-demo.example``, checked by test_demo_fixture), so no other
        suite owns such a user."""
        stale: list[str] = []
        for person in fx["people"]:
            users = await self._nodes(CollectionNames.USERS.value, {"email": person["email"]})
            stale.extend(
                _key(u) for u in users
                if u.get("orgId") != self.org_id
            )
        if stale:
            await self.graph.delete_nodes_and_edges(stale, CollectionNames.USERS.value)

    async def _add_account(self, email: str, now: int, *, active: bool) -> None:
        key = f"ukey-{uuid.uuid4().hex[:12]}"
        await self.graph.batch_upsert_nodes(
            [{"id": key, "userId": f"user-{uuid.uuid4().hex[:10]}", "orgId": self.org_id, "email": email,
              "fullName": email, "isActive": active, "createdAtTimestamp": now, "updatedAtTimestamp": now}],
            collection=CollectionNames.USERS.value)
        await self.graph.batch_create_edges(
            [{"from_id": key, "from_collection": CollectionNames.USERS.value, "to_id": self.org_id,
              "to_collection": CollectionNames.ORGS.value, "entityType": "ORGANIZATION",
              "createdAtTimestamp": now, "updatedAtTimestamp": now}], collection=CollectionNames.BELONGS_TO.value)
        await self.graph.add_user_to_all_team(self.org_id, key)

    async def sync(self) -> None:
        connector = DemoConnector(self.logger, self.processor, self.store, MagicMock(), self.connector_id)
        await connector.run_sync()

    async def _nodes(self, collection: str, filters: dict[str, Any]) -> list[dict]:
        return await self.graph.get_nodes_by_filters(collection, filters) or []

    async def _person_of(self, node: str, fx: dict[str, Any]) -> str:
        """The fixture person a user or person node stands for, by email or source key."""
        key = node.split("/", 1)[1]
        by_email = {p["email"].lower(): p["id"] for p in fx["people"]}
        by_source_key = {f"{self.connector_id}:{p['id']}": p["id"] for p in fx["people"]}
        for collection in (CollectionNames.USERS.value, CollectionNames.PEOPLE.value):
            doc = await self.graph.get_document(key, collection)
            if doc:
                return (by_email.get((doc.get("email") or "").lower())
                        or by_source_key.get(doc.get("sourceKey") or "") or f"{collection}/{key}")
        return node

    async def read(self, fx: dict[str, Any]) -> ExpectedGraph:
        """The graph's people and account facts, named by fixture id like the ground truth.

        Edge targets are matched by key: providers name a node's collection
        differently in the edges they return (Neo4j by label)."""
        org_by_name = {o["name"]: o["id"] for o in fx.get("organizations", [])}
        orgs = {_key(o): org_by_name.get(o.get("name"))
                for o in await self._nodes(CollectionNames.ORGS.value, {"parentOrgId": self.org_id})}
        nodes: dict[str, tuple[str, bool]] = {}  # key -> (person id, is a user)

        async def who(node: str) -> str:
            key = node.split("/", 1)[1]
            if key not in nodes:
                person = await self._person_of(node, fx)
                nodes[key] = (person, await self.graph.get_document(key, CollectionNames.USERS.value) is not None)
            return nodes[key][0]

        person_edges: set[tuple[str, str, str]] = set()
        account_links: set[tuple[str, str]] = set()
        for record in await self._nodes(CollectionNames.RECORDS.value, {"connectorId": self.connector_id}):
            rid, node = record.get("externalRecordId"), f"records/{_key(record)}"
            for edge in await self.graph.get_edges_from_node(node, RELATIONS):
                edge_type, target = edge.get("edgeType"), edge["_to"]
                if edge_type == EntityRelations.FOR_ACCOUNT.value:
                    account_links.add((rid, orgs.get(target.split("/", 1)[1], target)))
                elif edge_type:
                    person_edges.add((rid, edge_type, await who(target)))
            for edge in await self.graph.get_edges_to_node(node, RELATIONS):
                if edge.get("edgeType") == EntityRelations.INVOLVED_IN.value:
                    person_edges.add((rid, EntityRelations.INVOLVED_IN.value, await who(_source(edge))))

        account_groups: set[tuple[str, str]] = set()
        for group in await self._nodes(CollectionNames.RECORD_GROUPS.value, {"connectorId": self.connector_id}):
            for edge in await self.graph.get_edges_from_node(
                f"recordGroups/{_key(group)}", CollectionNames.DEAL_OF.value,
            ):
                account_groups.add((group.get("externalGroupId"), orgs.get(edge["_to"].split("/", 1)[1])))

        linked = dict(nodes.values())
        return ExpectedGraph(
            person_edges=frozenset(person_edges),
            account_links=frozenset(account_links),
            account_groups=frozenset(account_groups),
            # Members the edges reach as users; outsiders the edges reach as person nodes.
            members=frozenset(p for p, is_user in linked.items() if is_user),
            external_people=frozenset(p for p, is_user in linked.items() if not is_user),
            organizations=frozenset(v for v in orgs.values() if v),
            mentioned_organizations=frozenset(),
        )

    async def destroy(self) -> None:
        with contextlib.suppress(Exception):
            orgs = await self._nodes(CollectionNames.ORGS.value, {"parentOrgId": self.org_id})
            if orgs:
                await self.graph.delete_nodes_and_edges([_key(o) for o in orgs], CollectionNames.ORGS.value)
        await remove_owned(self.graph, self.org_id, self.connector_id)
        await self._drop_stale_users(self._fixture)


@pytest.fixture(params=["neo4j", "arango"])
async def world(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[DemoWorld]:
    backend = request.param
    try:
        graph = await (connect_neo4j(logging.getLogger("demo_people_it"), monkeypatch) if backend == "neo4j"
                       else connect_arango(logging.getLogger("demo_people_it"), ARANGO_DB))
    except Exception as exc:  # reported as a skip or a failure by backend_unavailable
        backend_unavailable(backend, exc)
    w = DemoWorld(graph)
    try:
        yield w
    finally:
        await w.destroy()


async def test_the_synced_graph_holds_exactly_the_fixtures_people_and_accounts(world: DemoWorld) -> None:
    fx = load_fixture()
    truth = expected_graph(fx)
    await world.seed(fx)
    await world.sync()
    graph = await world.read(fx)

    assert sorted(truth.person_edges - graph.person_edges) == [], "person edges the sync did not write"
    assert sorted(graph.person_edges - truth.person_edges) == [], "person edges the fixture does not imply"
    assert graph.account_links == truth.account_links
    assert graph.account_groups == truth.account_groups
    assert graph.organizations == truth.organizations
    assert graph.external_people == truth.external_people
    # Every member a record names is reached as the member's user, never as a person node.
    assert graph.members == {pid for _, _, pid in truth.person_edges} & truth.members


async def test_a_resync_leaves_the_same_graph(world: DemoWorld) -> None:
    fx = load_fixture()
    await world.seed(fx)
    await world.sync()
    first = await world.read(fx)
    await world.sync()
    assert await world.read(fx) == first

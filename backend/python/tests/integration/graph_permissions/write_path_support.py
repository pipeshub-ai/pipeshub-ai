"""Live-graph helpers shared by the write-path tests.

Moved out of ``test_write_path.py`` so the connector write-path tests under
``connectors/`` drive the same two backends the same way. The reasons behind
each seed (why the Org and App nodes must exist first) are on the helpers.
"""

import uuid

import aiohttp
from neo4j import AsyncGraphDatabase

from app.config.constants.arangodb import CollectionNames, Connectors
from app.config.constants.neo4j import (
    collection_to_label,
    edge_collection_to_relationship,
)

ORG = "org-write-path"
TS = 1700000000000


def _suffix() -> str:
    return uuid.uuid4().hex[:8]


class _ArangoBackend:
    name = "arango"

    def __init__(self, provider, settings: dict) -> None:
        self.provider = provider
        self._settings = settings

    async def _aql(self, query: str, bind: dict) -> list:
        auth = aiohttp.BasicAuth(self._settings["username"], self._settings["password"])
        url = f"{self._settings['url'].rstrip('/')}/_db/{self._settings['db']}/_api/cursor"
        async with aiohttp.ClientSession(auth=auth) as session:
            async with session.post(url, json={"query": query, "bindVars": bind}) as resp:
                body = await resp.json()
                assert resp.status in (200, 201), body
                return body["result"]

    async def edge_exists(
        self, edge_collection: str, from_collection: str, from_id: str,
        to_collection: str, to_id: str,
    ) -> bool:
        rows = await self._aql(
            f"RETURN LENGTH(FOR e IN {edge_collection} "
            "FILTER e._from == @from AND e._to == @to LIMIT 1 RETURN 1) > 0",
            {"from": f"{from_collection}/{from_id}", "to": f"{to_collection}/{to_id}"},
        )
        return rows[0]

    async def add_node_relation(self, from_id: str, to_id: str, props: dict) -> None:
        """A record -> record edge in the hierarchy collection, as legacy data
        left it: the write path stores no link there."""
        await self._aql(
            f"INSERT MERGE(@props, {{_from: @from, _to: @to}}) INTO {CollectionNames.NODE_RELATIONS.value}",
            {"props": props, "from": f"{CollectionNames.RECORDS.value}/{from_id}",
             "to": f"{CollectionNames.RECORDS.value}/{to_id}"},
        )

    async def remove_edge(
        self, edge_collection: str, from_collection: str, from_id: str,
        to_collection: str, to_id: str,
    ) -> None:
        await self._aql(
            f"FOR e IN {edge_collection} FILTER e._from == @from AND e._to == @to REMOVE e IN {edge_collection}",
            {"from": f"{from_collection}/{from_id}", "to": f"{to_collection}/{to_id}"},
        )

    async def access_rule(self, collection: str, key: str) -> list[dict]:
        return await self._aql(
            f"FOR n IN {collection} FILTER n._key == @key "
            "RETURN {accessRule: n.accessRule}",
            {"key": key},
        )

    async def reachable_from_app(self, app_id: str, grantees: list[str]) -> set[str]:
        from .test_aql_parity import _TRAVERSAL
        from .test_qpp_semantics import HIERARCHY_TYPES

        rows = await self._aql(_TRAVERSAL, {
            "seeds": [app_id],
            "seedCollection": CollectionNames.APPS.value,
            "types": HIERARCHY_TYPES,
            "grantees": grantees,
            "allowStrict": True,
            "skipChecks": False,
            "maxDepth": 50,
        })
        return set(rows)


class _Neo4jBackend:
    name = "neo4j"

    def __init__(self, provider, settings: dict) -> None:
        self.provider = provider
        self._settings = settings

    async def _cypher(self, query: str, params: dict) -> list[dict]:
        driver = AsyncGraphDatabase.driver(
            self._settings["uri"],
            auth=(self._settings["username"], self._settings["password"]),
        )
        try:
            async with driver.session(database=self._settings["database"]) as session:
                result = await session.run(query, params)
                return [record.data() async for record in result]
        finally:
            await driver.close()

    async def edge_exists(
        self, edge_collection: str, from_collection: str, from_id: str,
        to_collection: str, to_id: str,
    ) -> bool:
        # Labels and relationship types come from the production maps, so the
        # assertion reads the graph exactly as the provider wrote it.
        rel = edge_collection_to_relationship(edge_collection)
        from_label = collection_to_label(from_collection)
        to_label = collection_to_label(to_collection)
        rows = await self._cypher(
            f"MATCH (:{from_label} {{id: $from}})-[r:{rel}]->(:{to_label} {{id: $to}}) "
            "RETURN count(r) AS n",
            {"from": from_id, "to": to_id},
        )
        return rows[0]["n"] > 0

    async def add_node_relation(self, from_id: str, to_id: str, props: dict) -> None:
        await self._cypher(
            "MATCH (a:Record {id: $from}), (b:Record {id: $to}) "
            "CREATE (a)-[r:NODE_RELATION]->(b) SET r += $props",
            {"from": from_id, "to": to_id, "props": props},
        )

    async def remove_edge(
        self, edge_collection: str, from_collection: str, from_id: str,
        to_collection: str, to_id: str,
    ) -> None:
        rel = edge_collection_to_relationship(edge_collection)
        await self._cypher(
            f"MATCH (:{collection_to_label(from_collection)} {{id: $from}})-[r:{rel}]->"
            f"(:{collection_to_label(to_collection)} {{id: $to}}) DELETE r",
            {"from": from_id, "to": to_id},
        )

    async def access_rule(self, collection: str, key: str) -> list[dict]:
        label = collection_to_label(collection)
        return await self._cypher(
            f"MATCH (n:{label} {{id: $key}}) "
            "RETURN n.accessRule AS accessRule",
            {"key": key},
        )

    async def reachable_from_app(self, app_id: str, grantees: list[str]) -> set[str]:
        from .test_qpp_semantics import HIERARCHY_TYPES, PER_HOP_RULE

        rows = await self._cypher(
            f"MATCH (root:App {{id: $app}}) "
            f"      ((p)-[r:NODE_RELATION]->(c) WHERE {PER_HOP_RULE})+ (n) "
            "RETURN DISTINCT n.id AS id",
            {"app": app_id, "types": HIERARCHY_TYPES, "grantees": grantees,
             "allowStrict": True, "skipChecks": False},
        )
        return {r["id"] for r in rows}


async def _seed_org(backend) -> None:
    """The Organization node every record group belongs to.

    Same reason as the App: Neo4j's batch_create_edges MATCHes both endpoints,
    so without this node it drops the group -> org edge while Arango writes the
    same edge dangling, and the two backends disagree for a reason unrelated to
    the code under test. `orgId` is deliberately absent -- the orgs validator
    does not declare it and runs with additionalProperties false.
    """
    await backend.provider.batch_upsert_nodes(
        [{
            "_key": ORG,
            "accountType": "enterprise",
            "isActive": True,
            "name": "Test Org",
            "createdAtTimestamp": TS,
            "updatedAtTimestamp": TS,
        }],
        collection=CollectionNames.ORGS.value,
    )


async def _seed_app(
    backend, connector_id: str, *, name: str = "Confluence",
    app_type: str = Connectors.CONFLUENCE.value, app_group: str = "Atlassian",
) -> None:
    """Create the App node connector registration would already have written.

    Without it Neo4j drops every RecordGroup -> App edge (its batch_create_edges
    MATCHes both endpoints) while Arango writes the same edge dangling, so the
    two backends would disagree for a reason unrelated to the code under test.
    `_key` rather than `id`: the strict Arango validator forbids unknown fields,
    and batch_upsert_nodes renames it for Neo4j.
    """
    await _seed_org(backend)
    await backend.provider.batch_upsert_nodes(
        [{
            "_key": connector_id,
            "orgId": ORG,
            "name": name,
            "type": app_type,
            "appGroup": app_group,
            "scope": "team",
            "isActive": True,
            "createdAtTimestamp": TS,
        }],
        collection=CollectionNames.APPS.value,
    )


async def _seed_user(backend, email: str) -> str:
    """A user node the processor can resolve a grant against.

    `_handle_record_permissions` looks a USER grant up by email and silently
    skips when no user exists, so without this the grant would never become an
    edge and the assertions below would pass for the wrong reason.
    """
    user_id = f"user-{_suffix()}"
    await backend.provider.batch_upsert_nodes(
        [{"_key": user_id, "email": email, "orgId": ORG,
          "isActive": True, "createdAtTimestamp": TS, "updatedAtTimestamp": TS}],
        collection=CollectionNames.USERS.value,
    )
    return user_id

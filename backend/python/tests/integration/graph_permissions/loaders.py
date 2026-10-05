"""Load the acceptance graph into Neo4j and ArangoDB.

Raw inserts, deliberately: a fixture written here can describe any shape the
rules need, including ones no connector emits, and load it byte-identically into
both backends. ``test_write_path.py`` covers the other half, driving the real
``DataSourceEntitiesProcessor``.

Arango edges go straight into their edge collections through the **document**
API rather than gharial, which is how production writes them too
(``batch_create_edges``). The document API does not validate ``_from``/``_to``
against the named graph's edge definitions — those definitions have since been
widened to match what the write path emits, but the loader does not depend on
them being right.
"""

import os

import aiohttp
from neo4j import AsyncGraphDatabase

# KH_PERM_LISTING_STATE=1 runs the suite with the knowledge hub listing state:
# every Neo4j load is stamped as the migration stamps it, and the stub config
# reports the migration done, so every listing reads the labels.
LISTING_STATE = os.environ.get("KH_PERM_LISTING_STATE") == "1"

# Neo4j relationship type / Arango edge collection for each logical edge.
# Edge collections are created on demand.
EDGE_TARGETS = {
    "NODE_RELATION": "nodeRelations",
    "INHERIT_PERMISSIONS": "inheritPermissions",
    "BELONGS_TO": "belongsTo",
    "PERMISSION": "permission",
    "USER_APP_RELATION": "userAppRelation",
    "ORG_APP_RELATION": "orgAppRelation",
    "AUTHENTICATED_AS": "authenticatedAs",
}

NODE_TARGETS = {
    "App": "apps",
    "RecordGroup": "recordGroups",
    "Record": "records",
    "User": "users",
    "Group": "groups",
    "Role": "roles",
    "Teams": "teams",
    "Organization": "organizations",
}

# Neo4j labels for the same kinds, deliberately a separate map from the Arango
# collection names above. They differ in case and number (App vs apps), and
# interpolating the wrong one yields a graph with the correct shape and
# unusable labels — edge counts still match, so only a label-typed query
# catches it.
NEO4J_LABELS = {
    "App": "App",
    "RecordGroup": "RecordGroup",
    "Record": "Record",
    "User": "User",
    "Group": "Group",
    "Role": "Role",
    "Teams": "Teams",
    "Organization": "Organization",
}


def _check_kinds(nodes: list[dict], edges: list[dict]) -> None:
    for node in nodes:
        if node["kind"] not in NODE_TARGETS:
            raise ValueError(f"unknown node kind {node['kind']!r}")
    for edge in edges:
        if edge["type"] not in EDGE_TARGETS:
            raise ValueError(f"unknown edge type {edge['type']!r}")


def _link_storage_groups(edge_type: str, rows: list[dict]) -> list[tuple[str, list[dict]]]:
    if edge_type != "NODE_RELATION":
        return [(edge_type, rows)]
    hierarchy = {"PARENT_CHILD", "ATTACHMENT"}
    is_link = lambda row: row["props"].get("relationshipType") not in hierarchy | {None}  # noqa: E731
    groups = [("NODE_RELATION", [r for r in rows if not is_link(r)]),
              ("RECORD_LINK", [r for r in rows if is_link(r)])]
    return [(rel, group) for rel, group in groups if group]


def arango_shape(nodes: list[dict], edges: list[dict]) -> tuple[list[dict], list[dict]]:
    """The same graph as Arango stores it. A null property is an absent one (as
    on Neo4j, where setting null removes it). Arango removes a group rather than
    flagging it deleted or a placeholder, and its validator has no field for
    either, so such groups and their edges are left out: the guards for them are
    no-ops there. So is a record without an origin, which its validator requires."""
    gone = {n["id"] for n in nodes
            if (n["kind"] == "RecordGroup" and (n["props"].get("isDeleted") or n["props"].get("isPlaceholder")))
            or (n["kind"] == "Record" and n["props"].get("origin") is None)}
    shaped = [
        {**n, "props": {k: v for k, v in n["props"].items() if v is not None
                        and not (n["kind"] == "RecordGroup" and k in ("isDeleted", "isPlaceholder"))}}
        for n in nodes if n["id"] not in gone
    ]
    return shaped, [e for e in edges if e["from"] not in gone and e["to"] not in gone]


async def load_into_neo4j(settings: dict, nodes: list[dict], edges: list[dict]) -> None:
    """Create every fixture node and edge in Neo4j.

    Labels and relationship types cannot be parameterised in Cypher, so each is
    interpolated from the fixed maps above — never from fixture data.
    """
    _check_kinds(nodes, edges)
    driver = AsyncGraphDatabase.driver(
        settings["uri"], auth=(settings["username"], settings["password"])
    )
    try:
        async with driver.session(database=settings["database"]) as session:
            for kind, label in NEO4J_LABELS.items():
                rows = [n["props"] for n in nodes if n["kind"] == kind]
                if rows:
                    await session.run(
                        f"UNWIND $rows AS row CREATE (n:{label}) SET n = row",
                        rows=rows,
                    )

            for edge_type in EDGE_TARGETS:
                rows = [
                    {"from": e["from"], "to": e["to"], "props": e["props"]}
                    for e in edges
                    if e["type"] == edge_type
                ]
                if not rows:
                    continue
                # Stored as the provider stores them: a link is not hierarchy and
                # lives on its own relationship type.
                for rel_type, group in _link_storage_groups(edge_type, rows):
                    await session.run(
                        f"""
                        UNWIND $rows AS row
                        MATCH (a {{id: row.from}})
                        MATCH (b {{id: row.to}})
                        CREATE (a)-[r:{rel_type}]->(b)
                        SET r = row.props
                        """,
                        rows=group,
                    )
            if LISTING_STATE:
                from app.services.graph_db.neo4j.neo4j_provider import cypher_kh_stamp_all

                await (await session.run(cypher_kh_stamp_all(1000))).consume()
    finally:
        await driver.close()


async def _ensure_edge_collection(
    session: aiohttp.ClientSession, base: str, db: str, name: str
) -> None:
    async with session.get(f"{base}/_db/{db}/_api/collection/{name}") as resp:
        if resp.status == 200:
            return
    async with session.post(
        f"{base}/_db/{db}/_api/collection", json={"name": name, "type": 3}
    ) as resp:
        if resp.status not in (200, 409):
            raise RuntimeError(
                f"could not create edge collection {name}: "
                f"{resp.status} {await resp.text()}"
            )


async def _insert(
    session: aiohttp.ClientSession, base: str, db: str, collection: str, docs: list[dict]
) -> None:
    if not docs:
        return
    async with session.post(
        f"{base}/_db/{db}/_api/document/{collection}", json=docs
    ) as resp:
        if resp.status not in (200, 201, 202):
            raise RuntimeError(
                f"insert into {collection} failed: {resp.status} {await resp.text()}"
            )
        for entry in await resp.json():
            if isinstance(entry, dict) and entry.get("error"):
                raise RuntimeError(f"insert into {collection} failed: {entry}")


async def load_into_arango(settings: dict, nodes: list[dict], edges: list[dict]) -> None:
    """Create every fixture node and edge in ArangoDB."""
    _check_kinds(nodes, edges)
    base = settings["url"].rstrip("/")
    db = settings["db"]
    handles = {n["id"]: f"{NODE_TARGETS[n['kind']]}/{n['id']}" for n in nodes}

    auth = aiohttp.BasicAuth(settings["username"], settings["password"])
    async with aiohttp.ClientSession(auth=auth) as session:
        for kind, collection in NODE_TARGETS.items():
            docs = [
                # Arango keys on _key. `id` is a Neo4j-only property and the
                # collection validators run with additionalProperties: False,
                # so sending it would fail every insert.
                {"_key": n["id"], **{k: v for k, v in n["props"].items() if k != "id"}}
                for n in nodes
                if n["kind"] == kind
            ]
            await _insert(session, base, db, collection, docs)

        for edge_type, collection in EDGE_TARGETS.items():
            rows = [e for e in edges if e["type"] == edge_type]
            # Links apart from the hierarchy, as on Neo4j.
            for stored_as, group in _link_storage_groups(edge_type, rows):
                target = "recordLinks" if stored_as == "RECORD_LINK" else collection
                docs = [{"_from": handles[e["from"]], "_to": handles[e["to"]], **e["props"]} for e in group]
                await _ensure_edge_collection(session, base, db, target)
                await _insert(session, base, db, target, docs)

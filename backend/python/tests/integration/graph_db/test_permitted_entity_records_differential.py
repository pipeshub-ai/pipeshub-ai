"""Against real servers: ``get_permitted_entity_records`` returns exactly
what the earlier client-side check returned (candidates, then
``filter_nodes_with_permission_role``, then the "anyone" lookup, with
app-level connectors passing on app access), in the same order, on Neo4j
5.26 and ArangoDB 3.12.

A seeded random fixture covers every grant path (direct, group, role, team,
org, source account authenticated as on the same and on another connector,
inherited record group, active, inactive and other-org "anyone" shares),
deleted records, records of another org and connectors outside the scope,
with many timestamp ties. Paging with small windows and the listing's
cursors must rebuild the same list with no gap or duplicate.

  docker compose -f deployment/docker-compose/docker-compose.integration.graph-db.yml \\
    up -d --wait neo4j-graph-it arango-graph-it
  cd backend/python && pytest tests/integration/graph_db/test_permitted_entity_records_differential.py -m integration
"""
from __future__ import annotations

import random
import uuid
from typing import TYPE_CHECKING, Any

import pytest

from app.config.constants.arangodb import CollectionNames as C
from app.config.constants.arangodb import Connectors, OriginTypes, ProgressStatus
from app.models.entities import Record, RecordType
from app.modules.retrieval.entity_permissions import (
    EntityAccessContext,
    list_accessible_entity_records,
)
from tests.integration.graph_db.test_permitted_entity_records_real_backends import (
    _insert,
    _open_arango,
    _open_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
    from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

KB, CONF, OUT = "kb-d", "conf-d", "out-d"
GRANTS = ["none", "direct", "group", "role", "team", "org", "auth_same", "auth_other",
          "inherited", "anyone", "anyone_off", "anyone_other_org"]
N = 200
SEED = 7


def plan(org: str) -> list[dict[str, Any]]:
    rnd = random.Random(SEED)
    records = [
        {
            "id": f"{org}-r{i}",
            "ts": rnd.randint(1, 50),  # many timestamp ties
            "conn": rnd.choice([KB, CONF, CONF, CONF, OUT]),
            "deleted": rnd.random() < 0.1,
            "org": org if rnd.random() > 0.05 else f"{org}-x",
            "grant": rnd.choice(GRANTS),
        }
        for i in range(N)
    ]
    for record in records:
        # The source account only grants on the connector it was linked for.
        if record["grant"] == "auth_same":
            record["conn"] = CONF
        elif record["grant"] == "auth_other" and record["conn"] == CONF:
            record["conn"] = KB if random.Random(record["id"]).random() < 0.3 else OUT
    return records


_NEO4J_GRANTS = {
    "direct": "CREATE (u)-[:PERMISSION {type: 'USER', role: 'READER'}]->(rec)",
    "group": "CREATE (g)-[:PERMISSION {type: 'GROUP', role: 'READER'}]->(rec)",
    "role": "CREATE (r)-[:PERMISSION {type: 'ROLE', role: 'READER'}]->(rec)",
    "team": "CREATE (tm)-[:PERMISSION {type: 'TEAM', role: 'READER'}]->(rec)",
    "org": "CREATE (o)-[:PERMISSION {type: 'ORG', role: 'READER'}]->(rec)",
    "auth_same": "CREATE (s)-[:PERMISSION {type: 'USER', role: 'READER'}]->(rec)",
    "auth_other": "CREATE (s)-[:PERMISSION {type: 'USER', role: 'READER'}]->(rec)",
    "inherited": "CREATE (rec)-[:INHERIT_PERMISSIONS]->(rg)",
    "anyone": "CREATE (:Anyone {file_key: row.id, organization: $org, active: true, orgId: $org})",
    "anyone_off": "CREATE (:Anyone {file_key: row.id, organization: $org, active: false, orgId: $org})",
    "anyone_other_org": "CREATE (:Anyone {file_key: row.id, organization: $org + '-x', active: true, orgId: $org})",
}


async def seed_neo4j(provider: Neo4jProvider, org: str, records: list[dict[str, Any]]) -> None:
    grants = "\n".join(
        f"FOREACH (_ IN CASE WHEN row.grant = '{grant}' THEN [1] ELSE [] END | {clause})"
        for grant, clause in _NEO4J_GRANTS.items()
    )
    await provider.client.execute_query(
        f"""
        CREATE (t:Topics {{id: $org + '-t', orgId: $org, name: 'x'}})
        CREATE (u:User {{id: $u, userId: $u, orgId: $org}})
        CREATE (s:User {{id: $src, userId: $src, orgId: $org}})
        CREATE (u)-[:AUTHENTICATED_AS {{connectorId: $conf}}]->(s)
        CREATE (g:Group {{id: $org + '-g', orgId: $org}})
        CREATE (r:Role {{id: $org + '-role', orgId: $org}})
        CREATE (tm:Teams {{id: $org + '-team', orgId: $org}})
        CREATE (o:Organization {{id: $org + '-o', orgId: $org}})
        CREATE (rg:RecordGroup {{id: $org + '-rg', orgId: $org}})
        CREATE (u)-[:PERMISSION {{type: 'USER', role: 'READER'}}]->(g)
        CREATE (u)-[:PERMISSION {{type: 'USER', role: 'READER'}}]->(r)
        CREATE (u)-[:PERMISSION {{type: 'USER', role: 'READER'}}]->(tm)
        CREATE (u)-[:BELONGS_TO {{entityType: 'ORGANIZATION'}}]->(o)
        CREATE (u)-[:PERMISSION {{type: 'USER', role: 'READER'}}]->(rg)
        WITH t, g, r, tm, o, rg, s, u
        UNWIND $records AS row
        CREATE (rec:Record {{id: row.id, orgId: row.org, connectorId: row.conn, recordName: row.id,
            recordType: 'FILE', isDeleted: row.deleted, indexingStatus: 'COMPLETED',
            sourceLastModifiedTimestamp: row.ts}})
        CREATE (rec)-[:BELONGS_TO_TOPIC]->(t)
        {grants}
        """,
        parameters={"org": org, "u": f"{org}-u", "src": f"{org}-src", "conf": CONF, "records": records},
    )


async def close_neo4j(provider: Neo4jProvider, org: str) -> None:
    await provider.client.execute_query(
        "MATCH (n) WHERE n.orgId STARTS WITH $org DETACH DELETE n", parameters={"org": org},
    )


def _arango_grants(org: str, records: list[dict[str, Any]]) -> tuple[list[dict], list[dict], list[dict]]:
    user, source = f"users/{org}-u", f"users/{org}-src"
    granters = {
        "direct": (user, "USER"),
        "group": (f"groups/{org}-g", "GROUP"),
        "role": (f"roles/{org}-role", "ROLE"),
        "team": (f"teams/{org}-team", "TEAM"),
        "org": (f"organizations/{org}-o", "ORG"),
        "auth_same": (source, "USER"),
        "auth_other": (source, "USER"),
    }
    shares = {"anyone": (org, True), "anyone_off": (org, False), "anyone_other_org": (f"{org}-x", True)}
    permissions = [
        {"_from": user, "_to": target, "type": "USER", "role": "READER"}
        for target in (f"groups/{org}-g", f"roles/{org}-role", f"teams/{org}-team", f"recordGroups/{org}-rg")
    ]
    inherits, anyone = [], []
    for record in records:
        to, grant = f"records/{record['id']}", record["grant"]
        if grant in granters:
            granter, kind = granters[grant]
            permissions.append({"_from": granter, "_to": to, "type": kind, "role": "READER"})
        elif grant == "inherited":
            inherits.append({"_from": to, "_to": f"recordGroups/{org}-rg", "createdAtTimestamp": 1})
        elif grant in shares:
            organization, active = shares[grant]
            anyone.append({"file_key": record["id"], "organization": organization, "active": active})
    return permissions, inherits, anyone


async def seed_arango(provider: ArangoHTTPProvider, org: str, records: list[dict[str, Any]]) -> None:
    user, source = f"{org}-u", f"{org}-src"
    await provider.create_taxonomy_node_if_absent(
        C.TOPICS.value, {"id": f"{org}-t", "name": "x", "normalizedName": "x", "orgId": org},
    )
    docs = []
    for record in records:
        doc = Record(
            id=record["id"], org_id=record["org"], record_name=record["id"], record_type=RecordType.FILE,
            external_record_id=f"e-{record['id']}", version=0, origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.KNOWLEDGE_BASE, connector_id=record["conn"],
            indexing_status=ProgressStatus.COMPLETED.value, source_updated_at=record["ts"],
        ).to_arango_base_record()
        doc["isDeleted"] = record["deleted"]
        docs.append(doc)
    await _insert(provider, C.RECORDS.value, docs)
    await _insert(provider, C.BELONGS_TO_TOPIC.value, [
        {"_from": f"records/{r['id']}", "_to": f"topics/{org}-t", "createdAtTimestamp": 1} for r in records
    ])
    await _insert(provider, C.USERS.value, [
        {"_key": key, "userId": key, "orgId": org, "email": f"{key}@x"} for key in (user, source)
    ])
    await _insert(provider, C.GROUPS.value, [{"_key": f"{org}-g", "orgId": org}])
    await _insert(provider, C.ROLES.value, [{
        "_key": f"{org}-role", "orgId": org, "name": "r", "externalRoleId": "x",
        "connectorName": "KB", "connectorId": CONF, "createdAtTimestamp": 1,
    }])
    await _insert(provider, C.TEAMS.value, [{"_key": f"{org}-team", "orgId": org, "name": "t"}])
    await _insert(provider, C.ORGS.value, [{"_key": f"{org}-o", "accountType": "enterprise", "isActive": True}])
    await _insert(provider, C.RECORD_GROUPS.value, [{
        "_key": f"{org}-rg", "orgId": org, "groupName": "x", "groupType": "KB",
        "connectorName": "KB", "createdAtTimestamp": 1,
    }])
    await _insert(provider, C.AUTHENTICATED_AS.value, [
        {"_from": f"users/{user}", "_to": f"users/{source}", "connectorId": CONF, "createdAtTimestamp": 1},
    ])
    await _insert(provider, C.BELONGS_TO.value, [
        {"_from": f"users/{user}", "_to": f"organizations/{org}-o", "entityType": "ORGANIZATION"},
    ])
    permissions, inherits, anyone = _arango_grants(org, records)
    await _insert(provider, C.PERMISSION.value, permissions)
    if inherits:
        await _insert(provider, C.INHERIT_PERMISSIONS.value, inherits)
    if anyone:
        await _insert(provider, C.ANYONE.value, anyone)


async def close_arango(provider: ArangoHTTPProvider, org: str) -> None:
    aql = provider.http_client.execute_aql
    for edges in (C.BELONGS_TO_TOPIC, C.PERMISSION, C.INHERIT_PERMISSIONS, C.AUTHENTICATED_AS, C.BELONGS_TO):
        await aql(
            f"FOR e IN {edges.value} FILTER CONTAINS(e._from, @o) OR CONTAINS(e._to, @o) REMOVE e IN {edges.value}",
            {"o": org},
        )
    await aql(f"FOR d IN {C.ORGS.value} FILTER STARTS_WITH(d._key, @o) REMOVE d IN {C.ORGS.value}", {"o": org})
    for docs in (C.TOPICS, C.RECORDS, C.USERS, C.GROUPS, C.ROLES, C.TEAMS, C.RECORD_GROUPS):
        await aql(f"FOR d IN {docs.value} FILTER STARTS_WITH(d.orgId, @o) REMOVE d IN {docs.value}", {"o": org})
    await aql(
        f"FOR d IN {C.ANYONE.value} FILTER STARTS_WITH(d.organization, @o) REMOVE d IN {C.ANYONE.value}",
        {"o": org},
    )


async def old_reference(provider: Neo4jProvider | ArangoHTTPProvider, org: str, kind: str) -> list[str]:
    """The client-side check this replaced, in candidate order."""
    refs = [{"id": f"{org}-t", "type": "topic", "connectorIds": [KB, CONF]}]
    candidates = (await provider.get_entity_candidate_records(refs, org, limit_per_entity=100_000))[
        ("topic", f"{org}-t")
    ]
    app_level = {row["_key"] for row in candidates if row["connectorId"] == KB}
    need = [row["_key"] for row in candidates if row["connectorId"] != KB]
    allowed = await provider.filter_nodes_with_permission_role(
        [{"id": key, "type": "record"} for key in need], f"{org}-u", org, raise_on_error=True,
    )
    refused = [key for key in need if key not in allowed]
    if kind == "neo4j":
        rows = await provider.client.execute_query(
            "MATCH (a:Anyone) WHERE a.file_key IN $ids AND a.organization = $org "
            "AND coalesce(a.active, true) = true RETURN DISTINCT a.file_key AS id",
            parameters={"ids": refused, "org": org},
        )
        allowed |= {row["id"] for row in rows}
    else:
        rows = await provider.http_client.execute_aql(
            "FOR a IN anyone FILTER a.file_key IN @ids AND a.organization == @org AND a.active == true "
            "RETURN DISTINCT a.file_key",
            {"ids": refused, "org": org},
        )
        allowed |= set(rows)
    visible = app_level | allowed
    return [row["_key"] for row in candidates if row["_key"] in visible]


@pytest.fixture(params=["neo4j", "arango"])
async def seeded(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[Neo4jProvider | ArangoHTTPProvider, str, str]]:
    kind = request.param
    try:
        provider = await (_open_neo4j(monkeypatch) if kind == "neo4j" else _open_arango(monkeypatch))
    except Exception as exc:
        pytest.skip(f"{kind} not available: {exc}")
    org = f"org-d-{uuid.uuid4().hex[:8]}"
    try:
        await (seed_neo4j if kind == "neo4j" else seed_arango)(provider, org, plan(org))
        yield provider, org, kind
    finally:
        await (close_neo4j if kind == "neo4j" else close_arango)(provider, org)
        if kind == "neo4j":
            await provider.disconnect()


def _refs(org: str) -> list[dict[str, Any]]:
    return [{"id": f"{org}-t", "type": "topic", "connectorIds": [KB, CONF]}]


async def test_the_in_query_check_matches_the_client_side_check(seeded) -> None:
    provider, org, kind = seeded
    expected = await old_reference(provider, org, kind)
    rows = (await provider.get_permitted_entity_records(
        _refs(org), org, f"{org}-u", app_level_connector_ids=[KB],
        limit_per_entity=10_000, window=10_000, timeout_seconds=60,
    ))[("topic", f"{org}-t")]
    assert [r["_key"] for r in rows] == expected
    assert expected, "the fixture must leave something readable"


@pytest.mark.parametrize(("limit", "window"), [(1, 1), (3, 7), (4, 20), (2, 33), (5, 150)])
async def test_small_windows_page_through_to_the_same_list(seeded, limit: int, window: int) -> None:
    provider, org, kind = seeded
    expected = await old_reference(provider, org, kind)
    keys: list[str] = []
    offset = 0
    for _ in range(N * 2):
        rows = (await provider.get_permitted_entity_records(
            _refs(org), org, f"{org}-u", app_level_connector_ids=[KB],
            limit_per_entity=limit, offset=offset, window=window,
        ))[("topic", f"{org}-t")]
        keys += [r["_key"] for r in rows]
        assert rows.examined > 0 or rows.window_size == 0
        offset += rows.examined
        if rows.window_size < window and rows.examined >= rows.window_size:
            break
    assert keys == expected


@pytest.mark.parametrize("limit", [1, 7, 50])
async def test_listing_cursors_rebuild_the_same_list(seeded, limit: int) -> None:
    provider, org, kind = seeded
    expected = await old_reference(provider, org, kind)
    context = EntityAccessContext(
        org_id=org, user_key=f"{org}-u", app_level_app_ids=frozenset({KB}),
        record_level_app_ids=frozenset({CONF}), record_group_ids=frozenset(), app_names={},
    )
    keys: list[str] = []
    cursor = None
    for _ in range(N + 2):
        page = await list_accessible_entity_records(
            provider, context, entity_id=f"{org}-t", entity_type="topic", limit=limit, cursor=cursor,
        )
        keys += [r["_key"] for r in page.records]
        cursor = page.next_cursor
        if cursor is None:
            break
    assert keys == expected

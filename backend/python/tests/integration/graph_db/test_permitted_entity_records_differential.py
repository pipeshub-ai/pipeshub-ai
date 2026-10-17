"""Against real servers: what the entity permission layer returns is exactly
an entity's candidates filtered by the batch access check (``check_access``),
in candidate order, on Neo4j 5.26 and ArangoDB 3.12.

A seeded random fixture, in the hierarchy the access check reads, covers every
way a record is admitted (a collection record of a gated KB App; a grant held
directly, through a group, role, team or the organization, or by the source
account authenticated as on that connector; inheritance from a granted record
group; inheritance from the gated App with no grant at all) and refused (no
grant; a RESTRICTED record that only inherits; a source-account grant on a
connector it was not linked for; active, inactive and other-org "anyone"
shares), plus deleted records, records of another org and a connector outside
the scope, with many timestamp ties. Paging with small windows and the
listing's cursors must rebuild the same list with no gap or duplicate.

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
from app.modules.retrieval import entity_permissions as ep
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

# The connector ids are App keys, so each run takes its own: the stores are shared.
_RUN = uuid.uuid4().hex[:8]
KB, CONF, WIKI, OUT = f"kb-d-{_RUN}", f"conf-d-{_RUN}", f"wiki-d-{_RUN}", f"out-d-{_RUN}"
SCOPE = [KB, CONF, WIKI]
# What a record of the record-level connector CONF is given, by whether the
# access check then admits it. A KB record is admitted whatever it is given.
ADMITTED = ("direct", "group", "role", "team", "org", "auth_same", "inherited", "gate_only")
REFUSED = ("none", "restricted", "auth_other", "anyone", "anyone_off", "anyone_other_org")
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
            "grant": rnd.choice([*ADMITTED, *REFUSED]),
        }
        for i in range(N)
    ]
    for record in records:
        # The user authenticated CONF as the source account, and no other connector.
        if record["grant"] == "auth_same":
            record["conn"] = CONF
        elif record["grant"] == "auth_other":
            record["conn"] = WIKI
        record["rule"] = "RESTRICTED" if record["grant"] == "restricted" else "OPEN"
    return records


def parent_of(org: str, record: dict[str, Any]) -> tuple[str, str] | None:
    """The node a record hangs under, belongs to and inherits from, as
    ``(collection, key)``: its App for a collection record, the granted group,
    the group that inherits from the App, or a group the user holds nothing on."""
    if record["conn"] == KB:
        return C.APPS.value, KB
    if record["conn"] == OUT:
        return None
    if record["conn"] == WIKI:
        return C.RECORD_GROUPS.value, f"{org}-wiki-closed"
    if record["grant"] in ("inherited", "restricted"):
        return C.RECORD_GROUPS.value, f"{org}-rg"
    if record["grant"] == "gate_only":
        return C.RECORD_GROUPS.value, f"{org}-open"
    return C.RECORD_GROUPS.value, f"{org}-closed"


def readable(org: str, records: list[dict[str, Any]]) -> list[str]:
    """The records the fixture makes readable, in candidate order."""
    kept = [
        r for r in records
        if r["org"] == org and not r["deleted"]
        and (r["conn"] == KB or (r["conn"] == CONF and r["grant"] in ADMITTED))
    ]
    return [r["id"] for r in sorted(kept, key=lambda r: (-r["ts"], r["id"]))]


_NEO4J_GRANTS = {
    "direct": "CREATE (u)-[:PERMISSION {type: 'USER', role: 'READER'}]->(rec)",
    "group": "CREATE (g)-[:PERMISSION {type: 'GROUP', role: 'READER'}]->(rec)",
    "role": "CREATE (r)-[:PERMISSION {type: 'ROLE', role: 'READER'}]->(rec)",
    "team": "CREATE (tm)-[:PERMISSION {type: 'TEAM', role: 'READER'}]->(rec)",
    "org": "CREATE (o)-[:PERMISSION {type: 'ORG', role: 'READER'}]->(rec)",
    "auth_same": "CREATE (s)-[:PERMISSION {type: 'USER', role: 'READER'}]->(rec)",
    "auth_other": "CREATE (s)-[:PERMISSION {type: 'USER', role: 'READER'}]->(rec)",
    "anyone": "CREATE (:Anyone {file_key: row.id, organization: $org, active: true, orgId: $org})",
    "anyone_off": "CREATE (:Anyone {file_key: row.id, organization: $org, active: false, orgId: $org})",
    "anyone_other_org": "CREATE (:Anyone {file_key: row.id, organization: $org + '-x', active: true, orgId: $org})",
}


async def seed_neo4j(provider: Neo4jProvider, org: str, records: list[dict[str, Any]]) -> None:
    grants = "\n".join(
        f"FOREACH (_ IN CASE WHEN row.grant = '{grant}' THEN [1] ELSE [] END | {clause})"
        for grant, clause in _NEO4J_GRANTS.items()
    )
    child = "{relationshipType: 'PARENT_CHILD'}"
    await provider.client.execute_query(
        f"""
        CREATE (t:Topics {{id: $org + '-t', orgId: $org, name: 'x'}})
        CREATE (u:User {{id: $u, userId: $u, orgId: $org}})
        CREATE (s:User {{id: $src, userId: $src, orgId: $org}})
        CREATE (u)-[:AUTHENTICATED_AS {{connectorId: $conf}}]->(s)
        CREATE (g:Group {{id: $org + '-g', orgId: $org}})
        CREATE (r:Role {{id: $org + '-role', orgId: $org, connectorId: $conf}})
        CREATE (tm:Teams {{id: $org + '-team', orgId: $org}})
        CREATE (o:Organization {{id: $org + '-o', orgId: $org}})
        CREATE (kb:App {{id: $kb, orgId: $org, name: 'KB', type: 'KB'}})
        CREATE (conf:App {{id: $conf, orgId: $org, name: 'Confluence', type: 'CONFLUENCE'}})
        CREATE (wiki:App {{id: $wiki, orgId: $org, name: 'Wiki', type: 'CONFLUENCE'}})
        CREATE (rg:RecordGroup {{id: $org + '-rg', orgId: $org, connectorId: $conf}})
        CREATE (open:RecordGroup {{id: $org + '-open', orgId: $org, connectorId: $conf}})
        CREATE (closed:RecordGroup {{id: $org + '-closed', orgId: $org, connectorId: $conf}})
        CREATE (wikiClosed:RecordGroup {{id: $org + '-wiki-closed', orgId: $org, connectorId: $wiki}})
        CREATE (u)-[:PERMISSION {{type: 'USER', role: 'READER'}}]->(g)
        CREATE (u)-[:PERMISSION {{type: 'USER', role: 'READER'}}]->(r)
        CREATE (u)-[:PERMISSION {{type: 'USER', role: 'READER'}}]->(tm)
        CREATE (u)-[:BELONGS_TO {{entityType: 'ORGANIZATION'}}]->(o)
        CREATE (u)-[:PERMISSION {{type: 'USER', role: 'OWNER'}}]->(kb)
        CREATE (u)-[:USER_APP_RELATION]->(wiki)
        CREATE (u)-[:PERMISSION {{type: 'USER', role: 'READER'}}]->(rg)
        FOREACH (grp IN [rg, open, closed] |
            CREATE (conf)-[:NODE_RELATION {child}]->(grp) CREATE (grp)-[:BELONGS_TO]->(conf))
        CREATE (wiki)-[:NODE_RELATION {child}]->(wikiClosed)
        CREATE (wikiClosed)-[:BELONGS_TO]->(wiki)
        CREATE (open)-[:INHERIT_PERMISSIONS]->(conf)
        WITH t, g, r, tm, o, s, u
        UNWIND $records AS row
        CREATE (rec:Record {{id: row.id, orgId: row.org, connectorId: row.conn, recordName: row.id,
            recordType: 'FILE', isDeleted: row.deleted, indexingStatus: 'COMPLETED',
            sourceLastModifiedTimestamp: row.ts, accessRule: row.rule}})
        CREATE (rec)-[:BELONGS_TO_TOPIC]->(t)
        {grants}
        """,
        parameters={"org": org, "u": f"{org}-u", "src": f"{org}-src", "kb": KB, "conf": CONF, "wiki": WIKI,
                    "records": records},
    )
    placed = [(record["id"], parent_of(org, record)) for record in records]
    await provider.client.execute_query(
        f"""
        UNWIND $rows AS row
        MATCH (rec:Record {{id: row.id}})
        MATCH (parent:App|RecordGroup {{id: row.parent}})
        CREATE (parent)-[:NODE_RELATION {child}]->(rec)
        CREATE (rec)-[:BELONGS_TO]->(parent)
        CREATE (rec)-[:INHERIT_PERMISSIONS]->(parent)
        """,
        parameters={"rows": [{"id": key, "parent": parent[1]} for key, parent in placed if parent]},
    )


async def close_neo4j(provider: Neo4jProvider, org: str) -> None:
    await provider.client.execute_query(
        "MATCH (n) WHERE n.orgId STARTS WITH $org DETACH DELETE n", parameters={"org": org},
    )


def _arango_grants(org: str, records: list[dict[str, Any]]) -> tuple[list[dict], list[dict]]:
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
    permissions.append({"_from": user, "_to": f"apps/{KB}", "type": "USER", "role": "OWNER"})
    anyone = []
    for record in records:
        to, grant = f"records/{record['id']}", record["grant"]
        if grant in granters:
            granter, kind = granters[grant]
            permissions.append({"_from": granter, "_to": to, "type": kind, "role": "READER"})
        elif grant in shares:
            organization, active = shares[grant]
            anyone.append({"file_key": record["id"], "organization": organization, "active": active})
    return permissions, anyone


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
        doc["accessRule"] = record["rule"]
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
    await _insert(provider, C.APPS.value, [
        {"_key": key, "orgId": org, "name": name, "type": kind, "appGroup": name, "scope": "team",
         "isActive": True, "createdAtTimestamp": 1}
        for key, name, kind in (
            (KB, "KB", Connectors.KNOWLEDGE_BASE.value),
            (CONF, "Confluence", Connectors.CONFLUENCE.value),
            (WIKI, "Wiki", Connectors.CONFLUENCE.value),
        )
    ])
    groups = {f"{org}-rg": CONF, f"{org}-open": CONF, f"{org}-closed": CONF, f"{org}-wiki-closed": WIKI}
    await _insert(provider, C.RECORD_GROUPS.value, [
        {"_key": key, "orgId": org, "groupName": key, "groupType": "KB", "connectorName": "KB",
         "connectorId": connector, "createdAtTimestamp": 1}
        for key, connector in groups.items()
    ])
    await _insert(provider, C.AUTHENTICATED_AS.value, [
        {"_from": f"users/{user}", "_to": f"users/{source}", "connectorId": CONF, "createdAtTimestamp": 1},
    ])
    await _insert(provider, C.USER_APP_RELATION.value, [
        {"_from": f"users/{user}", "_to": f"apps/{WIKI}", "syncState": "COMPLETED", "lastSyncUpdate": 1,
         "createdAtTimestamp": 1},
    ])
    permissions, anyone = _arango_grants(org, records)
    await _insert(provider, C.PERMISSION.value, permissions)
    if anyone:
        await _insert(provider, C.ANYONE.value, anyone)
    under_apps = [(f"recordGroups/{key}", f"apps/{connector}") for key, connector in groups.items()]
    placed = [
        (f"records/{record['id']}", "/".join(parent))
        for record in records if (parent := parent_of(org, record))
    ]
    await _insert(provider, C.NODE_RELATIONS.value, [
        {"_from": parent, "_to": child, "relationshipType": "PARENT_CHILD", "createdAtTimestamp": 1}
        for child, parent in [*under_apps, *placed]
    ])
    await _insert(provider, C.BELONGS_TO.value, [
        {"_from": f"users/{user}", "_to": f"organizations/{org}-o", "entityType": "ORGANIZATION"},
        *({"_from": child, "_to": parent, "createdAtTimestamp": 1} for child, parent in [*under_apps, *placed]),
    ])
    await _insert(provider, C.INHERIT_PERMISSIONS.value, [
        {"_from": child, "_to": parent, "createdAtTimestamp": 1}
        for child, parent in [(f"recordGroups/{org}-open", f"apps/{CONF}"), *placed]
    ])


async def close_arango(provider: ArangoHTTPProvider, org: str) -> None:
    aql = provider.http_client.execute_aql
    for edges in (C.BELONGS_TO_TOPIC, C.PERMISSION, C.INHERIT_PERMISSIONS, C.AUTHENTICATED_AS, C.BELONGS_TO,
                  C.NODE_RELATIONS, C.USER_APP_RELATION):
        await aql(
            f"FOR e IN {edges.value} FILTER CONTAINS(e._from, @o) OR CONTAINS(e._to, @o) REMOVE e IN {edges.value}",
            {"o": org},
        )
    await aql(f"FOR d IN {C.ORGS.value} FILTER STARTS_WITH(d._key, @o) REMOVE d IN {C.ORGS.value}", {"o": org})
    for docs in (C.TOPICS, C.RECORDS, C.USERS, C.GROUPS, C.ROLES, C.TEAMS, C.RECORD_GROUPS, C.APPS):
        await aql(f"FOR d IN {docs.value} FILTER STARTS_WITH(d.orgId, @o) REMOVE d IN {docs.value}", {"o": org})
    await aql(
        f"FOR d IN {C.ANYONE.value} FILTER STARTS_WITH(d.organization, @o) REMOVE d IN {C.ANYONE.value}",
        {"o": org},
    )


@pytest.fixture(params=["neo4j", "arango"])
async def seeded(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[Neo4jProvider | ArangoHTTPProvider, str, list[dict[str, Any]]]]:
    kind = request.param
    try:
        provider = await (_open_neo4j(monkeypatch) if kind == "neo4j" else _open_arango(monkeypatch))
    except Exception as exc:
        pytest.skip(f"{kind} not available: {exc}")
    org = f"org-d-{uuid.uuid4().hex[:8]}"
    records = plan(org)
    try:
        await (seed_neo4j if kind == "neo4j" else seed_arango)(provider, org, records)
        yield provider, org, records
    finally:
        await (close_neo4j if kind == "neo4j" else close_arango)(provider, org)
        if kind == "neo4j":
            await provider.disconnect()


def _refs(org: str) -> list[dict[str, Any]]:
    return [{"id": f"{org}-t", "type": "topic", "connectorIds": SCOPE}]


async def _candidates(provider: Neo4jProvider | ArangoHTTPProvider, org: str) -> list[str]:
    rows = (await provider.get_entity_candidate_records(_refs(org), org, limit_per_entity=100_000))[
        ("topic", f"{org}-t")
    ]
    return [row["_key"] for row in rows]


async def by_access_check(provider: Neo4jProvider | ArangoHTTPProvider, org: str) -> list[str]:
    """The candidates the batch access check admits, in candidate order."""
    candidates = await _candidates(provider, org)
    admitted = (await provider.check_access(f"{org}-u", org, node_ids=candidates)).node_ids
    return [key for key in candidates if key in admitted]


async def _listed(provider: Neo4jProvider | ArangoHTTPProvider, org: str, limit: int) -> list[str]:
    """Every page of the listing, cursor by cursor."""
    context = EntityAccessContext(
        org_id=org, user_key=f"{org}-u", app_level_app_ids=frozenset({KB}),
        record_level_app_ids=frozenset({CONF, WIKI}), record_group_ids=frozenset(), app_names={},
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
            return keys
    raise AssertionError("the listing never ran out of pages")


async def test_the_access_check_admits_what_the_fixture_says(seeded) -> None:
    provider, org, records = seeded
    in_conf = {r["grant"] for r in records if r["conn"] == CONF and r["org"] == org and not r["deleted"]}
    assert in_conf == {*ADMITTED, *REFUSED} - {"auth_other"}, "the fixture must cover every path"
    assert await by_access_check(provider, org) == readable(org, records)


@pytest.mark.parametrize(("limit", "window"), [(1, 1), (3, 7), (4, 20), (2, 33), (5, 150)])
async def test_the_query_pages_through_every_candidate(seeded, limit: int, window: int) -> None:
    """Given every connector of the ref, the query filters nothing: it is the pager the layer walks."""
    provider, org, _ = seeded
    keys: list[str] = []
    offset = 0
    for _ in range(N * 2):
        rows = (await provider.get_permitted_entity_records(
            _refs(org), org, f"{org}-u", app_level_connector_ids=SCOPE,
            limit_per_entity=limit, offset=offset, window=window,
        ))[("topic", f"{org}-t")]
        keys += [r["_key"] for r in rows]
        assert rows.examined > 0 or rows.window_size == 0
        offset += rows.examined
        if rows.window_size < window and rows.examined >= rows.window_size:
            break
    assert keys == await _candidates(provider, org)


@pytest.mark.parametrize(("limit", "window"), [(1, 1), (3, 7), (4, 20), (2, 33), (5, 150)])
async def test_small_windows_page_through_to_the_same_list(
    seeded, monkeypatch: pytest.MonkeyPatch, limit: int, window: int,
) -> None:
    provider, org, _ = seeded
    monkeypatch.setattr(ep, "LISTING_WINDOW_MIN", window)
    monkeypatch.setattr(ep, "LISTING_WINDOW_MAX", window)
    assert await _listed(provider, org, limit) == await by_access_check(provider, org)


@pytest.mark.parametrize("limit", [1, 7, 50])
async def test_listing_cursors_rebuild_the_same_list(seeded, limit: int) -> None:
    provider, org, _ = seeded
    assert await _listed(provider, org, limit) == await by_access_check(provider, org)

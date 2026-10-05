"""Against real servers: ``get_permitted_entity_records`` checks permissions
inside the query on Neo4j 5.26 and ArangoDB 3.12, and both backends agree on
one fixture (KG-11, KG-37, KG-38).

The fixture links one topic to:
  - 70 newest records the user cannot read (more than the old 60-row probe);
  - older readable records, one per grant: app-level connector, direct user
    permission, user -> group -> record, and user -> record group inherited
    by the record;
  - records shared with "anyone" (active or not) and a record with no grant,
    which stay hidden: "anyone" shares grant no access in any check (#3691).

The graph is in the hierarchy the access check reads: the user passes the gate
of both Apps, the app-level record is a collection record of its App, the
inherited record hangs under the granted record group, and every other record
hangs under a record group the user holds nothing on.

A second topic links one record no grant reaches: its record group inherits
from the gated record-level App and the record from the group.

  docker compose -f deployment/docker-compose/docker-compose.integration.graph-db.yml \\
    up -d --wait neo4j-graph-it arango-graph-it
  cd backend/python && pytest tests/integration/graph_db/test_permitted_entity_records_real_backends.py -m integration
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    OriginTypes,
    ProgressStatus,
)
from app.models.entities import Record, RecordGroupType, RecordType
from app.modules.retrieval.entity_permissions import (
    EntityAccessContext,
    list_accessible_entity_records,
    search_entities_for_user,
)
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.common.utils import PermittedEntityRows

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

NEO4J_URI = os.environ.get("NEO4J_IT_URI", "bolt://localhost:17687")
NEO4J_PASSWORD = os.environ.get("NEO4J_IT_PASSWORD", "ensure-it-pass")
ARANGO_URL = os.environ.get("ARANGO_IT_URL", "http://localhost:18529")
ARANGO_PASSWORD = os.environ.get("ARANGO_IT_PASSWORD", "ensure-it-pass")
ARANGO_DB = "entity_graph_it"
# The connector ids are App keys, so each run takes its own: the stores are shared.
_RUN = uuid.uuid4().hex[:8]
RECORD_LEVEL = f"conf-it-{_RUN}"
APP_LEVEL = f"kb-it-{_RUN}"
NOISE = 70
# Newest first among the readable records.
READABLE = ("app", "direct", "group", "inherited")
HIDDEN = ("anyone", "anyone-off", "denied")

logger = logging.getLogger("permitted-entity-records-it")


def _keys(org_id: str) -> dict[str, Any]:
    return {
        "topic": f"{org_id}-topic",
        "user": f"{org_id}-user",
        "group": f"{org_id}-group",
        "rg": f"{org_id}-rg",
        "closed": f"{org_id}-closed",
        "gate_topic": f"{org_id}-gate-topic",
        "open": f"{org_id}-open",
        "gated": f"{org_id}-gated",
        "records": {name: f"{org_id}-{name}" for name in (*READABLE, *HIDDEN)},
        "noise": [f"{org_id}-noise{i}" for i in range(NOISE)],
    }


def _parents(keys: dict[str, Any]) -> dict[str, tuple[str, str]]:
    """The node each record hangs under, belongs to and inherits from, as
    ``(collection, key)``."""
    r = keys["records"]
    closed = (CollectionNames.RECORD_GROUPS.value, keys["closed"])
    parents = {key: closed for key in (*r.values(), *keys["noise"])}
    parents[r["app"]] = (CollectionNames.APPS.value, APP_LEVEL)
    parents[r["inherited"]] = (CollectionNames.RECORD_GROUPS.value, keys["rg"])
    return parents


def _timestamps(keys: dict[str, Any]) -> dict[str, int]:
    stamps = {key: 100_000 + i for i, key in enumerate(keys["noise"])}
    for i, name in enumerate((*READABLE, *HIDDEN)):
        stamps[keys["records"][name]] = 10_000 - i * 100
    return stamps


def _connector(keys: dict[str, Any], record_key: str) -> str:
    return APP_LEVEL if record_key == keys["records"]["app"] else RECORD_LEVEL


async def _open_neo4j(monkeypatch: pytest.MonkeyPatch) -> Neo4jProvider:
    monkeypatch.setenv("NEO4J_URI", NEO4J_URI)
    monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", NEO4J_PASSWORD)
    monkeypatch.setenv("NEO4J_DATABASE", "neo4j")
    provider = Neo4jProvider(logger, MagicMock())
    if not await asyncio.wait_for(provider.connect(), timeout=60):
        raise ConnectionError("connect returned False")
    return provider


async def _seed_neo4j(provider: Neo4jProvider, org_id: str) -> None:
    keys = _keys(org_id)
    stamps = _timestamps(keys)
    records = [
        {"id": key, "ts": ts, "connector": _connector(keys, key)} for key, ts in stamps.items()
    ]
    r = keys["records"]
    await provider.client.execute_query(
        """
        CREATE (t:Topics {id: $topic, name: 'Security', orgId: $org})
        CREATE (:User {id: $user, userId: $user, orgId: $org})
        CREATE (:Group {id: $group, orgId: $org})
        CREATE (:App {id: $kb, orgId: $org, name: 'KB', type: 'KB'})
        CREATE (:App {id: $conf, orgId: $org, name: 'Confluence', type: 'CONFLUENCE'})
        CREATE (:RecordGroup {id: $rg, orgId: $org, connectorId: $conf})
        CREATE (:RecordGroup {id: $closed, orgId: $org, connectorId: $conf})
        WITH t
        UNWIND $records AS row
        CREATE (rec:Record {id: row.id, orgId: $org, connectorId: row.connector,
                            recordName: row.id, recordType: 'FILE', isDeleted: false,
                            indexingStatus: 'COMPLETED', sourceLastModifiedTimestamp: row.ts})
        CREATE (rec)-[:BELONGS_TO_TOPIC]->(t)
        """,
        parameters={"topic": keys["topic"], "user": keys["user"], "group": keys["group"],
                    "rg": keys["rg"], "closed": keys["closed"], "kb": APP_LEVEL, "conf": RECORD_LEVEL,
                    "org": org_id, "records": records},
    )
    await provider.client.execute_query(
        """
        MATCH (u:User {id: $user}), (g:Group {id: $group}), (rg:RecordGroup {id: $rg})
        MATCH (direct:Record {id: $direct}), (viaGroup:Record {id: $viaGroup})
        MATCH (kb:App {id: $kb}), (conf:App {id: $conf}), (closed:RecordGroup {id: $closed})
        CREATE (u)-[:PERMISSION {type: 'USER', role: 'OWNER'}]->(kb)
        CREATE (u)-[:USER_APP_RELATION]->(conf)
        CREATE (conf)-[:NODE_RELATION {relationshipType: 'PARENT_CHILD'}]->(rg)
        CREATE (rg)-[:BELONGS_TO]->(conf)
        CREATE (conf)-[:NODE_RELATION {relationshipType: 'PARENT_CHILD'}]->(closed)
        CREATE (closed)-[:BELONGS_TO]->(conf)
        CREATE (u)-[:PERMISSION {type: 'USER', role: 'READER'}]->(direct)
        CREATE (u)-[:PERMISSION {type: 'USER', role: 'READER'}]->(g)
        CREATE (g)-[:PERMISSION {type: 'GROUP', role: 'READER'}]->(viaGroup)
        CREATE (u)-[:PERMISSION {type: 'USER', role: 'READER'}]->(rg)
        CREATE (:Anyone {file_key: $anyone, organization: $org, active: true, orgId: $org})
        CREATE (:Anyone {file_key: $anyoneOff, organization: $org, active: false, orgId: $org})
        """,
        parameters={"user": keys["user"], "group": keys["group"], "rg": keys["rg"], "org": org_id,
                    "closed": keys["closed"], "kb": APP_LEVEL, "conf": RECORD_LEVEL,
                    "direct": r["direct"], "viaGroup": r["group"],
                    "anyone": r["anyone"], "anyoneOff": r["anyone-off"]},
    )
    await provider.client.execute_query(
        """
        UNWIND $rows AS row
        MATCH (rec:Record {id: row.id})
        MATCH (parent:App|RecordGroup {id: row.parent})
        CREATE (parent)-[:NODE_RELATION {relationshipType: 'PARENT_CHILD'}]->(rec)
        CREATE (rec)-[:BELONGS_TO]->(parent)
        CREATE (rec)-[:INHERIT_PERMISSIONS]->(parent)
        """,
        parameters={"rows": [{"id": key, "parent": parent} for key, (_, parent) in _parents(keys).items()]},
    )
    await provider.client.execute_query(
        """
        MATCH (conf:App {id: $conf})
        CREATE (t:Topics {id: $topic, name: 'Onboarding', orgId: $org})
        CREATE (open:RecordGroup {id: $open, orgId: $org, connectorId: $conf})
        CREATE (rec:Record {id: $gated, orgId: $org, connectorId: $conf, recordName: $gated,
                            recordType: 'FILE', isDeleted: false, indexingStatus: 'COMPLETED',
                            sourceLastModifiedTimestamp: 1})
        CREATE (rec)-[:BELONGS_TO_TOPIC]->(t)
        CREATE (conf)-[:NODE_RELATION {relationshipType: 'PARENT_CHILD'}]->(open)
        CREATE (open)-[:BELONGS_TO]->(conf)
        CREATE (open)-[:INHERIT_PERMISSIONS]->(conf)
        CREATE (open)-[:NODE_RELATION {relationshipType: 'PARENT_CHILD'}]->(rec)
        CREATE (rec)-[:BELONGS_TO]->(open)
        CREATE (rec)-[:INHERIT_PERMISSIONS]->(open)
        """,
        parameters={"conf": RECORD_LEVEL, "org": org_id, "topic": keys["gate_topic"],
                    "open": keys["open"], "gated": keys["gated"]},
    )


async def _close_neo4j(provider: Neo4jProvider, org_id: str) -> None:
    await provider.client.execute_query(
        "MATCH (n) WHERE n.orgId = $org DETACH DELETE n", parameters={"org": org_id},
    )
    await provider.disconnect()


async def _open_arango(monkeypatch: pytest.MonkeyPatch) -> ArangoHTTPProvider:
    config_service = MagicMock()
    config_service.get_config = AsyncMock(return_value={
        "url": ARANGO_URL, "username": "root", "password": ARANGO_PASSWORD, "db": ARANGO_DB,
    })
    provider = ArangoHTTPProvider(logger, config_service)
    if not await asyncio.wait_for(provider.connect(), timeout=60):
        raise ConnectionError("connect returned False")
    await provider.ensure_schema()
    return provider


async def _insert(provider: ArangoHTTPProvider, collection: str, docs: list[dict[str, Any]]) -> None:
    await provider.http_client.execute_aql(f"FOR d IN @docs INSERT d INTO {collection}", {"docs": docs})


async def _seed_arango(provider: ArangoHTTPProvider, org_id: str) -> None:
    keys = _keys(org_id)
    stamps = _timestamps(keys)
    r = keys["records"]
    await provider.create_taxonomy_node_if_absent(CollectionNames.TOPICS.value, {
        "id": keys["topic"], "name": "Security", "normalizedName": "security", "orgId": org_id,
    })
    await _insert(provider, CollectionNames.RECORDS.value, [
        Record(
            id=key, org_id=org_id, record_name=key, record_type=RecordType.FILE,
            external_record_id=f"ext-{key}", version=0, origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.KNOWLEDGE_BASE, connector_id=_connector(keys, key),
            indexing_status=ProgressStatus.COMPLETED.value, source_updated_at=ts,
        ).to_arango_base_record()
        for key, ts in stamps.items()
    ])
    await _insert(provider, CollectionNames.BELONGS_TO_TOPIC.value, [
        {"_from": f"records/{key}", "_to": f"topics/{keys['topic']}", "createdAtTimestamp": 1}
        for key in stamps
    ])
    await _insert(provider, CollectionNames.USERS.value, [
        {"_key": keys["user"], "userId": keys["user"], "orgId": org_id, "email": f"{org_id}@it.test"},
    ])
    await _insert(provider, CollectionNames.GROUPS.value, [{"_key": keys["group"], "orgId": org_id}])
    await _insert(provider, CollectionNames.APPS.value, [
        {"_key": key, "orgId": org_id, "name": name, "type": kind, "appGroup": name, "scope": "team",
         "isActive": True, "createdAtTimestamp": 1}
        for key, name, kind in (
            (APP_LEVEL, "KB", Connectors.KNOWLEDGE_BASE.value),
            (RECORD_LEVEL, "Confluence", Connectors.CONFLUENCE.value),
        )
    ])
    await _insert(provider, CollectionNames.RECORD_GROUPS.value, [
        {"_key": key, "orgId": org_id, "groupName": name, "groupType": RecordGroupType.KB.value,
         "connectorName": Connectors.KNOWLEDGE_BASE.value, "connectorId": RECORD_LEVEL, "createdAtTimestamp": 1}
        for key, name in ((keys["rg"], "Private"), (keys["closed"], "Closed"))
    ])
    user, group, rg = f"users/{keys['user']}", f"groups/{keys['group']}", f"recordGroups/{keys['rg']}"
    kb, conf, closed = f"apps/{APP_LEVEL}", f"apps/{RECORD_LEVEL}", f"recordGroups/{keys['closed']}"
    await _insert(provider, CollectionNames.PERMISSION.value, [
        {"_from": user, "_to": kb, "type": "USER", "role": "OWNER"},
        {"_from": user, "_to": f"records/{r['direct']}", "type": "USER", "role": "READER"},
        {"_from": user, "_to": group, "type": "USER", "role": "READER"},
        {"_from": group, "_to": f"records/{r['group']}", "type": "GROUP", "role": "READER"},
        {"_from": user, "_to": rg, "type": "USER", "role": "READER"},
    ])
    await _insert(provider, CollectionNames.USER_APP_RELATION.value, [
        {"_from": user, "_to": conf, "syncState": "COMPLETED", "lastSyncUpdate": 1, "createdAtTimestamp": 1},
    ])
    placed = [(f"records/{key}", f"{collection}/{parent}") for key, (collection, parent) in _parents(keys).items()]
    await _insert(provider, CollectionNames.NODE_RELATIONS.value, [
        {"_from": parent, "_to": child, "relationshipType": "PARENT_CHILD", "createdAtTimestamp": 1}
        for child, parent in [(rg, conf), (closed, conf), *placed]
    ])
    await _insert(provider, CollectionNames.BELONGS_TO.value, [
        {"_from": child, "_to": parent, "createdAtTimestamp": 1}
        for child, parent in [(rg, conf), (closed, conf), *placed]
    ])
    await _insert(provider, CollectionNames.INHERIT_PERMISSIONS.value, [
        {"_from": child, "_to": parent, "createdAtTimestamp": 1} for child, parent in placed
    ])
    await _insert(provider, CollectionNames.ANYONE.value, [
        {"file_key": r["anyone"], "organization": org_id, "active": True},
        {"file_key": r["anyone-off"], "organization": org_id, "active": False},
    ])
    await provider.create_taxonomy_node_if_absent(CollectionNames.TOPICS.value, {
        "id": keys["gate_topic"], "name": "Onboarding", "normalizedName": "onboarding", "orgId": org_id,
    })
    await _insert(provider, CollectionNames.RECORDS.value, [
        Record(
            id=keys["gated"], org_id=org_id, record_name=keys["gated"], record_type=RecordType.FILE,
            external_record_id=f"ext-{keys['gated']}", version=0, origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.KNOWLEDGE_BASE, connector_id=RECORD_LEVEL,
            indexing_status=ProgressStatus.COMPLETED.value, source_updated_at=1,
        ).to_arango_base_record(),
    ])
    await _insert(provider, CollectionNames.BELONGS_TO_TOPIC.value, [
        {"_from": f"records/{keys['gated']}", "_to": f"topics/{keys['gate_topic']}", "createdAtTimestamp": 1},
    ])
    await _insert(provider, CollectionNames.RECORD_GROUPS.value, [
        {"_key": keys["open"], "orgId": org_id, "groupName": "Open", "groupType": RecordGroupType.KB.value,
         "connectorName": Connectors.KNOWLEDGE_BASE.value, "connectorId": RECORD_LEVEL, "createdAtTimestamp": 1},
    ])
    open_group = f"recordGroups/{keys['open']}"
    gate_only = [(open_group, conf), (f"records/{keys['gated']}", open_group)]
    await _insert(provider, CollectionNames.NODE_RELATIONS.value, [
        {"_from": parent, "_to": child, "relationshipType": "PARENT_CHILD", "createdAtTimestamp": 1}
        for child, parent in gate_only
    ])
    for collection in (CollectionNames.BELONGS_TO.value, CollectionNames.INHERIT_PERMISSIONS.value):
        await _insert(provider, collection, [
            {"_from": child, "_to": parent, "createdAtTimestamp": 1} for child, parent in gate_only
        ])


async def _close_arango(provider: ArangoHTTPProvider, org_id: str) -> None:
    edge_cleanup = {
        CollectionNames.BELONGS_TO_TOPIC.value: "e._from",
        CollectionNames.PERMISSION.value: "e._from",
        CollectionNames.INHERIT_PERMISSIONS.value: "e._from",
        CollectionNames.NODE_RELATIONS.value: "e._from",
        CollectionNames.BELONGS_TO.value: "e._from",
        CollectionNames.USER_APP_RELATION.value: "e._from",
    }
    for collection, field in edge_cleanup.items():
        await provider.http_client.execute_aql(
            f"FOR e IN {collection} FILTER CONTAINS({field}, @org) OR CONTAINS(e._to, @org) "
            f"REMOVE e IN {collection}",
            {"org": org_id},
        )
    for collection in (
        CollectionNames.TOPICS.value, CollectionNames.RECORDS.value, CollectionNames.USERS.value,
        CollectionNames.GROUPS.value, CollectionNames.RECORD_GROUPS.value, CollectionNames.APPS.value,
    ):
        await provider.http_client.execute_aql(
            f"FOR d IN {collection} FILTER d.orgId == @org REMOVE d IN {collection}", {"org": org_id},
        )
    await provider.http_client.execute_aql(
        f"FOR d IN {CollectionNames.ANYONE.value} FILTER d.organization == @org "
        f"REMOVE d IN {CollectionNames.ANYONE.value}",
        {"org": org_id},
    )


@pytest.fixture(params=["neo4j", "arango"])
async def backend(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[Any, str]]:
    open_, seed, close = {
        "neo4j": (_open_neo4j, _seed_neo4j, _close_neo4j),
        "arango": (_open_arango, _seed_arango, _close_arango),
    }[request.param]
    try:
        provider = await open_(monkeypatch)
    except Exception as exc:
        pytest.skip(f"{request.param} not available: {exc}")
    org_id = f"org-it-{uuid.uuid4().hex[:10]}"
    try:
        await seed(provider, org_id)
        yield provider, org_id
    finally:
        await close(provider, org_id)


def _context(org_id: str) -> EntityAccessContext:
    return EntityAccessContext(
        org_id=org_id,
        user_key=_keys(org_id)["user"],
        app_level_app_ids=frozenset({APP_LEVEL}),
        record_level_app_ids=frozenset({RECORD_LEVEL}),
        record_group_ids=frozenset(),
        app_names={APP_LEVEL: "KB", RECORD_LEVEL: "Confluence"},
    )


async def _permitted(
    provider: Neo4jProvider | ArangoHTTPProvider, org_id: str, **kwargs: int | float,
) -> PermittedEntityRows:
    keys = _keys(org_id)
    out = await provider.get_permitted_entity_records(
        [{"id": keys["topic"], "type": "topic", "connectorIds": [APP_LEVEL, RECORD_LEVEL]}],
        org_id,
        keys["user"],
        app_level_connector_ids=[APP_LEVEL],
        **kwargs,
    )
    return out[("topic", keys["topic"])]


class TestPermittedEntityRecords:
    async def test_every_grant_is_honoured_and_nothing_else(self, backend) -> None:
        provider, org_id = backend
        rows = await _permitted(provider, org_id, limit_per_entity=20, window=200, timeout_seconds=30)
        records = _keys(org_id)["records"]
        assert [row["_key"] for row in rows] == [records[name] for name in READABLE]
        assert rows.window_size == NOISE + len(READABLE) + len(HIDDEN)
        assert rows.examined == rows.window_size

    async def test_the_walk_stops_at_the_limit(self, backend) -> None:
        provider, org_id = backend
        rows = await _permitted(provider, org_id, limit_per_entity=2, window=200)
        records = _keys(org_id)["records"]
        assert [row["_key"] for row in rows] == [records["app"], records["direct"]]
        assert rows.examined == NOISE + 2

    async def test_windows_page_through_the_candidates(self, backend) -> None:
        provider, org_id = backend
        first = await _permitted(provider, org_id, limit_per_entity=5, window=60)
        assert first == [] and (first.window_size, first.examined) == (60, 60)
        second = await _permitted(provider, org_id, limit_per_entity=5, offset=60, window=60)
        assert len(second) == len(READABLE)

    async def test_an_unknown_user_reads_nothing(self, backend) -> None:
        provider, org_id = backend
        keys = _keys(org_id)
        out = await provider.get_permitted_entity_records(
            [{"id": keys["topic"], "type": "topic", "connectorIds": [APP_LEVEL, RECORD_LEVEL]}],
            org_id, f"{org_id}-nobody", app_level_connector_ids=[],
        )
        assert out[("topic", keys["topic"])] == []

    async def test_a_record_ref_is_checked_too(self, backend) -> None:
        provider, org_id = backend
        records = _keys(org_id)["records"]
        out = await provider.get_permitted_entity_records(
            [{"id": records["direct"], "type": "record", "connectorIds": [RECORD_LEVEL]},
             {"id": records["denied"], "type": "record", "connectorIds": [RECORD_LEVEL]}],
            org_id, _keys(org_id)["user"], app_level_connector_ids=[APP_LEVEL],
        )
        assert [r["_key"] for r in out[("record", records["direct"])]] == [records["direct"]]
        assert out[("record", records["denied"])] == []


class TestThroughThePermissionLayer:
    async def test_search_keeps_an_entity_whose_readable_records_are_old(self, backend) -> None:
        """KG-11: the newest 70 are unreadable; the old probe saw only 60."""
        provider, org_id = backend
        keys = _keys(org_id)
        store = MagicMock()
        store.search_entities_passes = AsyncMock(side_effect=lambda q, org, passes, **kw: [
            [{"entityId": keys["topic"], "entityType": "topic", "name": "Security", "score": 0.9}],
            *([] for _ in passes[1:]),
        ])
        hits = await search_entities_for_user(store, provider, _context(org_id), "security")
        assert [h.entity_id for h in hits] == [keys["topic"]]
        assert [r["_key"] for r in hits[0].records] == [keys["records"][n] for n in READABLE[:3]]
        assert hits[0].more_records is True

    async def test_listing_returns_the_readable_records_with_a_cursor(self, backend) -> None:
        provider, org_id = backend
        records = _keys(org_id)["records"]
        page = await list_accessible_entity_records(
            provider, _context(org_id), entity_id=_keys(org_id)["topic"], entity_type="topic", limit=3,
        )
        assert [r["_key"] for r in page.records] == [records[n] for n in READABLE[:3]]
        rest = await list_accessible_entity_records(
            provider, _context(org_id), entity_id=_keys(org_id)["topic"], entity_type="topic",
            limit=10, cursor=page.next_cursor,
        )
        assert [r["_key"] for r in rest.records] == [records[n] for n in READABLE[3:]]
        assert rest.next_cursor is None

    async def test_a_record_reached_through_the_gate_and_inheritance_alone_is_returned(self, backend) -> None:
        """No grant anywhere, so the query's role test never offers it; the access check admits it."""
        provider, org_id = backend
        keys = _keys(org_id)
        ref = {"id": keys["gate_topic"], "type": "topic", "connectorIds": [APP_LEVEL, RECORD_LEVEL]}
        by_role = await provider.get_permitted_entity_records(
            [ref], org_id, keys["user"], app_level_connector_ids=[APP_LEVEL],
        )
        assert by_role[("topic", keys["gate_topic"])] == []
        admitted = await provider.check_access(keys["user"], org_id, node_ids=[keys["gated"]])
        assert admitted.node_ids == {keys["gated"]}

        page = await list_accessible_entity_records(
            provider, _context(org_id), entity_id=keys["gate_topic"], entity_type="topic",
        )
        assert [r["_key"] for r in page.records] == [keys["gated"]]
        assert page.next_cursor is None

        store = MagicMock()
        store.search_entities_passes = AsyncMock(side_effect=lambda q, org, passes, **kw: [
            [{"entityId": keys["gate_topic"], "entityType": "topic", "name": "Onboarding", "score": 0.9}],
            *([] for _ in passes[1:]),
        ])
        hits = await search_entities_for_user(store, provider, _context(org_id), "onboarding")
        assert [h.entity_id for h in hits] == [keys["gate_topic"]]
        assert [r["_key"] for r in hits[0].records] == [keys["gated"]]
        assert hits[0].more_records is False

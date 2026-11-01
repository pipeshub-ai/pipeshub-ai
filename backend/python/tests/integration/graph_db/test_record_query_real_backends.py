"""Against real servers: ``get_record_query_candidates`` on Neo4j 5.26 and
ArangoDB 3.12 returns exactly the seeded records a pushdown selects, in the
entity candidate order, hydrated with their ticket, deal, people,
organisations and links (``docs/record-query.md``).

Each case compares a provider's rows, every window concatenated, with a
Python reference: the seeded rows filtered by the pushdown's own semantics.

Seeded per test: Zendesk and Jira tickets (one with no status, one with no
created time), Salesforce deals, a Jira file with no type node; members and
an outside person in several roles, one member linked user -> record as
Slack writes it; accounts linked FOR_ACCOUNT and MENTIONS; a record link;
record groups nested three deep; a topic. Never returned: a deleted record, a
placeholder, another org's record, and another org's member, person and
account linked to this org's records.

The end-to-end case runs ``run_record_query`` with the real access check: a
member who reads some records gets totals, sums, groups and rows over those
only.

  docker compose -f deployment/docker-compose/docker-compose.integration.graph-db.yml \\
    up -d --wait neo4j-graph-it arango-graph-it
  cd backend/python && pytest tests/integration/graph_db/test_record_query_real_backends.py -m integration
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest

from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    OriginTypes,
    ProgressStatus,
)
from app.models.entities import (
    DealRecord,
    FileRecord,
    Person,
    Record,
    RecordType,
    TicketRecord,
)
from app.modules.retrieval.entity_permissions import EntityAccessContext
from app.modules.retrieval.record_fields import PERSON_EDGES, ROLE_EDGES
from app.modules.retrieval.record_query.engine import run_record_query
from app.modules.retrieval.record_query.model import RecordQuery
from app.modules.retrieval.record_query.pushdown import (
    Pushdown,
    PushedLinks,
    PushedPeople,
)
from app.services.graph_db.common.record_query_rows import (
    CLASSIFICATION_UNDECIDED,
    CLASSIFIED_ENTITY_TYPES,
    CandidateKey,
)
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

ARANGO_DB = "record_query_it"
logger = logging.getLogger("record-query-it")

C = CollectionNames
ZENDESK, JIRA, SALESFORCE = Connectors.ZENDESK, Connectors.JIRA, Connectors.SALESFORCE


@dataclass
class Spec:
    """One seeded record and what the graph holds for it."""

    kind: str  # ticket | deal | file
    connector: Connectors
    group: str | None
    created: int | None
    updated: int
    ticket: dict[str, Any] | None = None
    deal: dict[str, Any] | None = None
    readable: bool = True
    deleted: bool = False
    placeholder: bool = False
    other_org: bool = False
    topic: bool = False
    # extractionStatus: NOT_STARTED is a record waiting for classification on first use.
    extraction: str = ProgressStatus.COMPLETED.value


# Groups: root > child > grandchild (Zendesk), jira, account (Salesforce).
GROUP_PARENT = {"root": None, "child": "root", "grandchild": "child", "jira": None, "account": None}
GROUP_APP = {"root": ZENDESK, "child": ZENDESK, "grandchild": ZENDESK, "jira": JIRA, "account": SALESFORCE}


def _ticket(status: str | None, source_status: str | None, priority: str | None,
            resolved: int | None = None, due: int | None = None) -> dict[str, Any]:
    return {"status": status, "sourceStatus": source_status, "priority": priority,
            "resolvedAt": resolved, "dueAt": due}


SPECS: dict[str, Spec] = {
    "t1": Spec("ticket", ZENDESK, "child", 1_000, 50_000, ticket=_ticket("OPEN", "Open", "HIGH", due=90_000),
               topic=True),
    "t2": Spec("ticket", ZENDESK, "root", 2_000, 60_000,
               ticket=_ticket("RESOLVED", "Solved", "LOW", resolved=70_000), readable=False),
    "t3": Spec("ticket", JIRA, "jira", 3_000, 40_000, ticket=_ticket(None, None, None), topic=True),
    "t4": Spec("ticket", ZENDESK, "grandchild", 4_000, 80_000, ticket=_ticket("IN_PROGRESS", "Doing", "MEDIUM")),
    "t5": Spec("ticket", ZENDESK, None, None, 30_000, ticket=_ticket("NEW", "New", "LOW")),
    "d1": Spec("deal", SALESFORCE, "account", 5_000, 20_000,
               deal={"amount": 1000.0, "closeDate": "2026-01-15", "isWon": True, "isClosed": True}),
    "d2": Spec("deal", SALESFORCE, "account", 6_000, 10_000,
               deal={"amount": 50000.0, "closeDate": None, "isWon": False, "isClosed": False}, readable=False),
    # Same newest time as d1: the id breaks the tie.
    "f1": Spec("file", JIRA, "jira", 7_000, 20_000),
    # Not classified yet: no topic or MENTIONS edge can exist; u2 keeps a topic
    # edge from an earlier classification while it is classified again.
    "u1": Spec("file", JIRA, "jira", 3_500, 35_000, extraction=ProgressStatus.NOT_STARTED.value),
    "u2": Spec("file", JIRA, "jira", 3_600, 36_000, extraction=ProgressStatus.QUEUED.value, topic=True),
    # Classification ran and failed: no edges, and not known either.
    "u3": Spec("file", JIRA, "jira", 3_700, 37_000, extraction=ProgressStatus.FAILED.value),
    "deleted": Spec("ticket", ZENDESK, "child", 1_500, 99_000, ticket=_ticket("OPEN", "Open", "HIGH"), deleted=True),
    "placeholder": Spec("ticket", ZENDESK, "child", 1_600, 98_000, ticket=_ticket("OPEN", "Open", "HIGH"),
                        placeholder=True),
    "foreign": Spec("ticket", ZENDESK, "child", 1_700, 97_000, ticket=_ticket("OPEN", "Open", "HIGH"),
                    other_org=True),
}
LIVE = [name for name, s in SPECS.items() if not (s.deleted or s.placeholder or s.other_org)]

# (record, person, edge type); "ann" and "bob" are members, "pete" a Person
# node, "zed" another org's member and "xena" another org's Person. Ann is
# three nodes of one email: "ann", a second account "ann2" and an outsider
# node "annx"; "annz" has her email in another org.
PEOPLE = {"ann": "user", "bob": "user", "pete": "person", "zed": "user", "xena": "person",
          "ann2": "user", "annx": "person", "annz": "user"}
FOREIGN_PEOPLE = {"zed", "xena", "annz"}
ANN_NODES = ("ann", "ann2", "annx")
RECORD_PEOPLE = [
    ("t1", "ann", "ASSIGNED_TO"), ("t1", "pete", "REPORTED_BY"), ("t1", "zed", "ASSIGNED_TO"),
    ("t2", "ann", "REPORTED_BY"), ("t2", "bob", "ASSIGNED_TO"),
    ("t3", "bob", "ASSIGNED_TO"), ("t3", "xena", "REPORTED_BY"),
    ("t4", "ann", "ASSIGNED_TO"), ("t4", "ann", "REPORTED_BY"),
    ("d1", "ann", "OWNED_BY"), ("d1", "ann2", "LAST_MODIFIED_BY"), ("d2", "pete", "INVOLVED_IN"),
    ("t5", "annx", "REPORTED_BY"), ("t5", "annz", "ASSIGNED_TO"),
    ("deleted", "ann", "ASSIGNED_TO"), ("placeholder", "ann", "ASSIGNED_TO"), ("foreign", "ann", "ASSIGNED_TO"),
]
# Written user -> record, as Slack mentions are.
PEOPLE_TO_RECORD = [("f1", "bob", "MENTIONED_IN")]
ACCOUNTS = {"acme": True, "globex": True, "foreignco": False}  # whether this org's
RECORD_ORGS = [
    ("t1", "acme", "FOR_ACCOUNT"), ("t2", "globex", "MENTIONS"), ("t2", "foreignco", "FOR_ACCOUNT"),
    ("d1", "acme", "FOR_ACCOUNT"), ("d2", "acme", "MENTIONS"), ("d2", "globex", "FOR_ACCOUNT"),
    ("deleted", "acme", "FOR_ACCOUNT"),
]
RECORD_LINKS = [("t3", "t1", "BLOCKS"), ("f1", "t4", "RELATED")]
NAMES = {"ann": "Ann", "bob": "Bob", "pete": "Pete", "zed": "Zed", "xena": "Xena", "ann2": "", "annx": "Ann X",
         "annz": "Ann",
         "acme": "Acme", "globex": "Globex", "foreignco": "Foreign Co"}


@dataclass
class Graph:
    graph: IGraphDBProvider
    org: str
    other: str
    connectors: dict[Connectors, str] = field(default_factory=dict)

    def rid(self, name: str) -> str:
        return f"{self.org}-{name}"

    def nid(self, name: str) -> str:
        return f"{self.org}-n-{name}"

    def gid(self, name: str) -> str:
        return f"{self.org}-g-{name}"

    def email(self, person: str) -> str:
        """Ann's nodes share one email, in two spellings of its case."""
        if person in ("ann", "annx", "annz"):
            return f"ann@{self.org}.test"
        if person == "ann2":
            return f"ANN@{self.org}.test"
        return f"{person}@{self.org}.test" if PEOPLE[person] == "user" else f"{person}@outside.test"

    @property
    def connector_ids(self) -> list[str]:
        return sorted(self.connectors.values())

    @property
    def topic(self) -> str:
        return f"{self.org}-topic"


def _record(g: Graph, name: str, spec: Spec) -> Record:
    org = g.other if spec.other_org else g.org
    base: dict[str, Any] = {
        "id": g.rid(name), "org_id": org, "external_record_id": g.rid(name), "record_name": f"{name} title",
        "origin": OriginTypes.CONNECTOR, "connector_name": spec.connector, "connector_id": g.connectors[spec.connector],
        "version": 1, "source_created_at": spec.created, "source_updated_at": spec.updated,
        "weburl": f"https://example.test/{name}", "is_deleted": spec.deleted, "is_placeholder": spec.placeholder,
        "extraction_status": spec.extraction,
        "indexing_status": ProgressStatus.QUEUED.value if spec.placeholder else ProgressStatus.COMPLETED.value,
        "record_group_id": g.gid(spec.group) if spec.group else None,
    }
    if spec.kind == "ticket":
        t = spec.ticket or {}
        return TicketRecord(**base, record_type=RecordType.TICKET, status=t["status"], source_status=t["sourceStatus"],
                            priority=t["priority"], resolved_at=t["resolvedAt"], due_date=t["dueAt"])
    if spec.kind == "deal":
        d = spec.deal or {}
        return DealRecord(**base, record_type=RecordType.DEAL, amount=d["amount"], close_date=d["closeDate"],
                          is_won=d["isWon"], is_closed=d["isClosed"])
    return FileRecord(**base, record_type=RecordType.FILE, is_file=True)


async def _seed(g: Graph) -> None:
    graph = g.graph
    await graph.batch_upsert_nodes([
        {"id": key, "orgId": g.org, "name": connector.value, "type": connector.value, "appGroup": connector.value,
         "scope": "team", "isActive": True, "createdAtTimestamp": 1, "updatedAtTimestamp": 1}
        for connector, key in g.connectors.items()
    ], C.APPS.value)
    await graph.batch_upsert_nodes([
        {"id": g.nid(n), "userId": g.nid(n), "orgId": g.other if n in FOREIGN_PEOPLE else g.org,
         "email": g.email(n), "fullName": NAMES[n], "isActive": True}
        for n, kind in PEOPLE.items() if kind == "user"
    ], C.USERS.value)
    await graph.batch_upsert_nodes([
        Person(id=g.nid(n), org_id=g.other if n in FOREIGN_PEOPLE else g.org, email=g.email(n),
               full_name=NAMES[n]).to_arango_person()
        for n, kind in PEOPLE.items() if kind == "person"
    ], C.PEOPLE.value)
    await graph.batch_upsert_nodes([
        {"id": g.nid(n), "name": NAMES[n], "accountType": "enterprise", "isActive": True, "isExternal": True,
         "parentOrgId": g.org if ours else g.other}
        for n, ours in ACCOUNTS.items()
    ], C.ORGS.value)
    await graph.batch_upsert_nodes([
        {"id": g.gid(name), "orgId": g.org, "groupName": name, "groupType": "PROJECT",
         "connectorName": GROUP_APP[name].value, "connectorId": g.connectors[GROUP_APP[name]],
         "externalGroupId": g.gid(name), "createdAtTimestamp": 1}
        for name in GROUP_PARENT
    ], C.RECORD_GROUPS.value)
    await graph.create_taxonomy_node_if_absent(C.TOPICS.value, {
        "id": g.topic, "name": "Billing", "normalizedName": "billing", "orgId": g.org,
    })
    await graph.batch_upsert_records([_record(g, name, spec) for name, spec in SPECS.items()])

    def edge(from_id: str, from_col: str, to_id: str, to_col: str, **attrs: Any) -> dict[str, Any]:  # noqa: ANN401
        return {"from_id": from_id, "from_collection": from_col, "to_id": to_id, "to_collection": to_col,
                "createdAtTimestamp": 1, **attrs}

    # The hierarchy the access check walks: app > group > nested group > record.
    hierarchy: list[tuple[str, str, str, str]] = []  # (child, child collection, parent, parent collection)
    for name, parent in GROUP_PARENT.items():
        if parent is None:
            hierarchy.append((g.gid(name), C.RECORD_GROUPS.value, g.connectors[GROUP_APP[name]], C.APPS.value))
        else:
            hierarchy.append((g.gid(name), C.RECORD_GROUPS.value, g.gid(parent), C.RECORD_GROUPS.value))
    for name, spec in SPECS.items():
        if spec.other_org:
            continue
        if spec.group:
            hierarchy.append((g.rid(name), C.RECORDS.value, g.gid(spec.group), C.RECORD_GROUPS.value))
        else:
            hierarchy.append((g.rid(name), C.RECORDS.value, g.connectors[spec.connector], C.APPS.value))
    await graph.batch_create_edges([
        edge(parent, pcol, child, ccol, relationshipType="PARENT_CHILD") for child, ccol, parent, pcol in hierarchy
    ], C.NODE_RELATIONS.value)
    await graph.batch_create_edges([
        edge(child, ccol, parent, pcol) for child, ccol, parent, pcol in hierarchy
    ], C.BELONGS_TO.value)
    await graph.batch_create_edges([
        edge(child, ccol, parent, pcol) for child, ccol, parent, pcol in hierarchy if ccol == C.RECORDS.value
    ], C.INHERIT_PERMISSIONS.value)

    ann = g.nid("ann")
    await graph.batch_create_edges([
        edge(ann, C.USERS.value, key, C.APPS.value, syncState="COMPLETED", lastSyncUpdate=1)
        for key in g.connectors.values()
    ], C.USER_APP_RELATION.value)
    await graph.batch_create_edges([
        edge(ann, C.USERS.value, g.rid(name), C.RECORDS.value, type="USER", role="READER")
        for name in LIVE if SPECS[name].readable
    ], C.PERMISSION.value)

    collection = {"user": C.USERS.value, "person": C.PEOPLE.value}
    await graph.batch_create_entity_relations([
        {"_from": f"{C.RECORDS.value}/{g.rid(r)}", "_to": f"{collection[PEOPLE[p]]}/{g.nid(p)}",
         "edgeType": t, "createdAtTimestamp": 1}
        for r, p, t in RECORD_PEOPLE
    ] + [
        {"_from": f"{C.RECORDS.value}/{g.rid(r)}", "_to": f"{C.ORGS.value}/{g.nid(o)}",
         "edgeType": t, "createdAtTimestamp": 1}
        for r, o, t in RECORD_ORGS
    ])
    await graph.batch_create_entity_relations([
        {"_from": f"{collection[PEOPLE[p]]}/{g.nid(p)}", "_to": f"{C.RECORDS.value}/{g.rid(r)}",
         "edgeType": t, "createdAtTimestamp": 1}
        for r, p, t in PEOPLE_TO_RECORD
    ])
    await graph.batch_create_edges([
        edge(g.rid(a), C.RECORDS.value, g.rid(b), C.RECORDS.value, relationshipType=rel)
        for a, b, rel in RECORD_LINKS
    ], C.RECORD_LINKS.value)
    await graph.batch_create_edges([
        edge(g.rid(name), C.RECORDS.value, g.topic, C.TOPICS.value)
        for name, spec in SPECS.items() if spec.topic
    ], C.BELONGS_TO_TOPIC.value)


async def _cleanup(graph: IGraphDBProvider, org: str) -> None:
    if isinstance(graph, Neo4jProvider):
        await graph.client.execute_query(
            "MATCH (n) WHERE n.id STARTS WITH $org OR n.orgId STARTS WITH $org DETACH DELETE n",
            parameters={"org": org},
        )
        return
    aql = graph.http_client.execute_aql
    for edges in (
        C.ENTITY_RELATIONS.value, C.IS_OF_TYPE.value, C.RECORD_LINKS.value, C.NODE_RELATIONS.value,
        C.BELONGS_TO.value, C.INHERIT_PERMISSIONS.value, C.PERMISSION.value, C.USER_APP_RELATION.value,
        C.BELONGS_TO_TOPIC.value,
    ):
        await aql(f"FOR e IN {edges} FILTER CONTAINS(e._from, @org) OR CONTAINS(e._to, @org) REMOVE e IN {edges}",
                  {"org": org})
    for docs in (
        C.RECORDS.value, C.TICKETS.value, C.DEALS.value, C.USERS.value, C.PEOPLE.value, C.ORGS.value,
        C.RECORD_GROUPS.value, C.APPS.value, C.TOPICS.value,
    ):
        await aql(f"FOR d IN {docs} FILTER STARTS_WITH(d._key, @org) REMOVE d IN {docs}", {"org": org})


@pytest.fixture(params=["neo4j", "arango"])
async def seeded(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Graph]:
    async with contextlib.AsyncExitStack() as stack:
        try:
            graph = await (
                connect_neo4j(logger, monkeypatch) if request.param == "neo4j" else connect_arango(logger, ARANGO_DB)
            )
        except Exception as exc:
            backend_unavailable(request.param, exc)
        disconnect = getattr(graph, "disconnect", None)
        if disconnect is not None:
            stack.push_async_callback(disconnect)
        org = f"org-rq-{uuid.uuid4().hex[:10]}"
        g = Graph(graph, org, f"{org}-x", {c: f"{org}-app-{c.value.lower()}" for c in (ZENDESK, JIRA, SALESFORCE)})
        stack.push_async_callback(_cleanup, graph, org)
        await _seed(g)
        yield g


# ---------------------------------------------------------------- reference


def _expected_row(g: Graph, name: str, pushed: Pushdown | None = None) -> dict[str, Any]:
    spec = SPECS[name]
    people = [
        {"role": t, "id": g.nid(p), "name": NAMES[p], "email": g.email(p)}
        for r, p, t in RECORD_PEOPLE + PEOPLE_TO_RECORD if r == name and p not in FOREIGN_PEOPLE
    ]
    orgs = [{"id": g.nid(o), "name": NAMES[o]} for r, o, _ in RECORD_ORGS if r == name and ACCOUNTS[o]]
    links = [{"id": g.rid(b if a == name else a), "relation": rel}
             for a, b, rel in RECORD_LINKS if name in (a, b)]
    return {
        "_key": g.rid(name), "recordName": f"{name} title",
        "recordType": {"ticket": "TICKET", "deal": "DEAL", "file": "FILE"}[spec.kind],
        "connectorName": spec.connector.value, "connectorId": g.connectors[spec.connector],
        "recordGroupId": g.gid(spec.group) if spec.group else None, "webUrl": f"https://example.test/{name}",
        "createdAt": spec.created, "updatedAt": spec.updated,
        "ticket": dict(spec.ticket) if spec.ticket else None, "deal": dict(spec.deal) if spec.deal else None,
        "people": people, "organizations": orgs, "links": links,
        "extractionStatus": spec.extraction,
        "entities": [e for e in (pushed or {}).get("entities", [])
                     if e["type"] in CLASSIFIED_ENTITY_TYPES and _entity_holds(g, name, e)],
    }


def _descendants(groups: list[str]) -> set[str]:
    out = set(groups)
    changed = True
    while changed:
        more = {child for child, parent in GROUP_PARENT.items() if parent in out}
        changed = not more <= out
        out |= more
    return out


def _entity_holds(g: Graph, name: str, entity: dict[str, str]) -> bool:
    kind, eid = entity["type"], entity["id"]
    if kind == "record":
        return eid == g.rid(name)
    if kind == "record_group":
        return SPECS[name].group is not None and eid == g.gid(SPECS[name].group)
    if kind == "topic":
        return SPECS[name].topic and eid == g.topic
    if kind == "person":
        return any(r == name and p not in FOREIGN_PEOPLE and g.nid(p) == eid
                   for r, p, _ in RECORD_PEOPLE + PEOPLE_TO_RECORD)
    raise AssertionError(kind)


def _holds(g: Graph, name: str, pushed: Pushdown, within: list[dict[str, list[str]]] | None) -> bool:
    row, spec = _expected_row(g, name), SPECS[name]
    if "sources" in pushed and row["connectorName"] not in pushed["sources"]:
        return False
    if "record_types" in pushed and row["recordType"] not in pushed["record_types"]:
        return False
    if "containers" in pushed:
        groups = _descendants([n for n in GROUP_PARENT if g.gid(n) in pushed["containers"]])
        if spec.group is None or g.gid(spec.group) not in {g.gid(x) for x in groups} | set(pushed["containers"]):
            return False
    for key, value in (("created", row["createdAt"]), ("updated", row["updatedAt"])):
        start, end = pushed.get(key, [None, None])
        if (start is not None or end is not None) and value is None:
            return False
        if (start is not None and value < start) or (end is not None and value > end):
            return False
    for person in pushed.get("people", []):
        if not any(p["id"] in person["ids"] and p["role"] in person["edges"] for p in row["people"]):
            return False
    waiting = spec.extraction in CLASSIFICATION_UNDECIDED
    for org_ids in pushed.get("organizations", []):
        if not (any(o["id"] in org_ids for o in row["organizations"]) or waiting):
            return False
    for link in pushed.get("links", []):
        if not any(lk["id"] in link["ids"] and link["relation"] in (None, lk["relation"]) for lk in row["links"]):
            return False
    if not all(_entity_holds(g, name, e) or (waiting and e["type"] in CLASSIFIED_ENTITY_TYPES)
               for e in pushed.get("entities", [])):
        return False
    # An empty limit is a limit that admits nothing, as ``within_param`` reads it.
    for scope in ([] if within is None else within or [{}]):
        if not (row["connectorId"] in scope.get("apps", []) or row["_key"] in scope.get("records", [])
                or (spec.group is not None and g.gid(spec.group) in scope.get("groups", []))):
            return False
    return True


def _reference(g: Graph, pushed: Pushdown, within: list[dict[str, list[str]]] | None = None) -> list[dict[str, Any]]:
    rows = [_expected_row(g, n, pushed) for n in LIVE if _holds(g, n, pushed, within)]
    return sorted(rows, key=lambda r: (-r["updatedAt"], r["_key"]))


def _normal(row: dict[str, Any]) -> dict[str, Any]:
    """A row with its edge lists in a fixed order: the contract orders records, not edges."""
    def key(item: dict[str, Any]) -> tuple[str, ...]:
        return tuple(f"{k}={item[k]}" for k in sorted(item))

    # What first use needs to queue classification: checked in its own test.
    kept = {k: v for k, v in row.items() if k not in ("indexingStatus", "virtualRecordId", "contentRev", "mimeType")}
    return {**kept, **{k: sorted(row[k], key=key) for k in ("people", "organizations", "links", "entities")}}


async def _scan(g: Graph, pushed: Pushdown, *, scan_cap: int = 1_000,
                within: list[dict[str, list[str]]] | None = None) -> tuple[list[CandidateKey], bool]:
    found = await g.graph.get_record_query_candidates(
        g.org, connector_ids=g.connector_ids, pushdown=pushed, scan_cap=scan_cap, timeout_seconds=30, within=within,
    )
    return found.keys, found.capped


async def _all_windows(g: Graph, pushed: Pushdown, *, window: int = 2, scan_cap: int = 1_000,
                       within: list[dict[str, list[str]]] | None = None) -> tuple[list[dict[str, Any]], bool]:
    """The scan's candidates read back as rows, ``window`` ids at a time, in scan order."""
    keys, capped = await _scan(g, pushed, scan_cap=scan_cap, within=within)
    rows: list[dict[str, Any]] = []
    for start in range(0, len(keys), window):
        ids = [k.id for k in keys[start:start + window]]
        found = await g.graph.get_record_query_rows(g.org, record_ids=ids, pushdown=pushed, timeout_seconds=30)
        by_id = {r["_key"]: r for r in found}
        assert set(by_id) == set(ids)
        rows += [by_id[i] for i in ids]
    return rows, capped


async def _assert_matches(g: Graph, pushed: Pushdown, within: list[dict[str, list[str]]] | None = None) -> None:
    expected = _reference(g, pushed, within)
    rows, capped = await _all_windows(g, pushed, within=within)
    assert capped is False
    assert [r["_key"] for r in rows] == [r["_key"] for r in expected]
    assert [_normal(r) for r in rows] == [_normal(r) for r in expected]


# --------------------------------------------------------------------- cases


def _cases(g: Graph) -> dict[str, Pushdown]:
    ann, bob, pete, zed = (g.nid(n) for n in ("ann", "bob", "pete", "zed"))
    return {
        "nothing": Pushdown(),
        "sources": Pushdown(sources=["ZENDESK", "SALESFORCE"]),
        "record_types": Pushdown(record_types=["DEAL", "FILE"]),
        "containers_root_nested": Pushdown(containers=[g.gid("root")]),
        "containers_child": Pushdown(containers=[g.gid("child"), g.gid("jira")]),
        "created_window": Pushdown(created=[2_000, 5_000]),
        "created_open_end": Pushdown(created=[None, 3_000]),
        "updated_open_start": Pushdown(updated=[45_000, None]),
        "people_assignee": Pushdown(people=[PushedPeople(edges=list(ROLE_EDGES["assignee"]), ids=[ann])]),
        "people_any_role_person_node": Pushdown(people=[PushedPeople(edges=list(PERSON_EDGES), ids=[pete])]),
        "people_mentioned_user_to_record": Pushdown(
            people=[PushedPeople(edges=list(ROLE_EDGES["mentioned"]), ids=[bob])]),
        "people_two_conditions": Pushdown(people=[
            PushedPeople(edges=list(ROLE_EDGES["assignee"]), ids=[ann, bob]),
            PushedPeople(edges=list(ROLE_EDGES["reporter"]), ids=[ann]),
        ]),
        "people_foreign_member": Pushdown(people=[PushedPeople(edges=list(PERSON_EDGES), ids=[zed])]),
        "organizations": Pushdown(organizations=[[g.nid("acme")]]),
        "organizations_any_and_all": Pushdown(organizations=[[g.nid("acme"), g.nid("globex")], [g.nid("globex")]]),
        "organizations_foreign": Pushdown(organizations=[[g.nid("foreignco")]]),
        "links_either_direction": Pushdown(links=[PushedLinks(ids=[g.rid("t1")], relation=None)]),
        "links_incoming": Pushdown(links=[PushedLinks(ids=[g.rid("t3")], relation=None)]),
        "links_relation": Pushdown(links=[PushedLinks(ids=[g.rid("t4"), g.rid("t1")], relation="RELATED")]),
        "entity_topic": Pushdown(entities=[{"type": "topic", "id": g.topic}]),
        "entity_topic_from_a_source": Pushdown(entities=[{"type": "topic", "id": g.topic}], sources=["JIRA"]),
        "entity_topic_in_container": Pushdown(entities=[{"type": "topic", "id": g.topic}],
                                              containers=[g.gid("jira")]),
        "organizations_in_container": Pushdown(organizations=[[g.nid("acme")]], containers=[g.gid("jira")]),
        "entity_record": Pushdown(entities=[{"type": "record", "id": g.rid("t4")}]),
        "entity_record_group": Pushdown(entities=[{"type": "record_group", "id": g.gid("child")}]),
        "entity_person": Pushdown(entities=[{"type": "person", "id": bob}]),
        "entity_person_outsider": Pushdown(entities=[{"type": "person", "id": pete}]),
        "combined": Pushdown(
            sources=["ZENDESK", "JIRA"], record_types=["TICKET"], containers=[g.gid("root"), g.gid("jira")],
            created=[1_000, None], updated=[None, 85_000],
            people=[PushedPeople(edges=list(PERSON_EDGES), ids=[ann, bob])],
        ),
        "combined_edges": Pushdown(
            organizations=[[g.nid("acme")]], people=[PushedPeople(edges=list(ROLE_EDGES["owner"]), ids=[ann])],
            record_types=["DEAL"],
        ),
        "combined_entities": Pushdown(
            entities=[{"type": "topic", "id": g.topic}, {"type": "person", "id": ann}],
            links=[PushedLinks(ids=[g.rid("t3")], relation="BLOCKS")],
        ),
        "nothing_matches": Pushdown(sources=["ZENDESK"], record_types=["DEAL"]),
        "empty_choice": Pushdown(sources=[]),
    }


CASE_NAMES = list(_cases(Graph(None, "o", "x")).keys())  # type: ignore[arg-type]


@pytest.mark.parametrize("case", CASE_NAMES)
async def test_each_pushdown_equals_the_reference(seeded: Graph, case: str) -> None:
    pushed = _cases(seeded)[case]
    expected = _reference(seeded, pushed)
    if case not in ("nothing_matches", "empty_choice", "people_foreign_member", "organizations_foreign"):
        assert expected, f"case {case} selects nothing; it would not test the key"
    await _assert_matches(seeded, pushed)


async def test_the_reference_cases_select_what_they_say(seeded: Graph) -> None:
    """Guards the reference itself on a few hand-checked answers."""
    g = seeded
    cases = _cases(g)

    def keys(case: str) -> list[str]:
        return [r["_key"].removeprefix(f"{g.org}-") for r in _reference(g, cases[case])]

    assert keys("nothing") == ["t4", "t2", "t1", "t3", "u3", "u2", "u1", "t5", "d1", "f1", "d2"]
    # Records not classified yet stay candidates for what classification links.
    assert keys("entity_topic") == ["t1", "t3", "u3", "u2", "u1"]
    assert keys("organizations") == ["t1", "u3", "u2", "u1", "d1", "d2"]
    assert keys("organizations_in_container") == ["u3", "u2", "u1"]
    assert keys("containers_root_nested") == ["t4", "t2", "t1"]
    assert keys("people_assignee") == ["t4", "t1"]
    assert keys("people_any_role_person_node") == ["t1", "d2"]
    assert keys("links_either_direction") == ["t3"]
    assert keys("created_open_end") == ["t2", "t1", "t3"]


async def test_hydration_shapes_and_missing_values(seeded: Graph) -> None:
    g = seeded
    rows, _ = await _all_windows(g, Pushdown(), window=100)
    by_key = {r["_key"].removeprefix(f"{g.org}-"): r for r in rows}
    assert set(by_key) == set(LIVE)
    for row in rows:
        assert set(row) == {"_key", "recordName", "recordType", "connectorName", "connectorId", "recordGroupId",
                            "webUrl", "createdAt", "updatedAt", "ticket", "deal", "people", "organizations", "links",
                            "extractionStatus", "indexingStatus", "virtualRecordId", "contentRev", "mimeType",
                            "entities"}
    assert by_key["u3"]["extractionStatus"] == "FAILED"
    assert by_key["u1"]["extractionStatus"] == "NOT_STARTED" and by_key["t1"]["extractionStatus"] == "COMPLETED"
    assert by_key["u1"]["indexingStatus"] == "COMPLETED" and by_key["u1"]["entities"] == []
    assert by_key["t3"]["ticket"] == {"status": None, "sourceStatus": None, "priority": None,
                                      "resolvedAt": None, "dueAt": None}
    assert by_key["t2"]["ticket"]["resolvedAt"] == 70_000
    assert by_key["t1"]["ticket"]["dueAt"] == 90_000
    assert by_key["t5"]["createdAt"] is None and by_key["t5"]["recordGroupId"] is None
    assert by_key["f1"]["ticket"] is None and by_key["f1"]["deal"] is None
    assert by_key["d1"]["deal"] == {"amount": 1000.0, "closeDate": "2026-01-15", "isWon": True, "isClosed": True}
    assert by_key["d2"]["deal"]["closeDate"] is None and by_key["d2"]["deal"]["isWon"] is False
    assert by_key["d1"]["ticket"] is None and by_key["t1"]["deal"] is None
    # Another org's member and Person are never hydrated; one member in two roles is two entries.
    assert sorted((p["role"], p["name"]) for p in by_key["t1"]["people"]) == [("ASSIGNED_TO", "Ann"),
                                                                              ("REPORTED_BY", "Pete")]
    assert sorted(p["role"] for p in by_key["t4"]["people"]) == ["ASSIGNED_TO", "REPORTED_BY"]
    assert by_key["t3"]["people"] == [{"role": "ASSIGNED_TO", "id": g.nid("bob"), "name": "Bob",
                                       "email": g.email("bob")}]
    assert by_key["f1"]["people"] == [{"role": "MENTIONED_IN", "id": g.nid("bob"), "name": "Bob",
                                       "email": g.email("bob")}]
    assert by_key["t2"]["organizations"] == [{"id": g.nid("globex"), "name": "Globex"}]
    assert by_key["t1"]["links"] == [{"id": g.rid("t3"), "relation": "BLOCKS"}]
    assert by_key["t5"]["people"] == [{"role": "REPORTED_BY", "id": g.nid("annx"), "name": "Ann X",
                                       "email": g.email("annx")}]
    assert by_key["t5"]["organizations"] == [] and by_key["t5"]["links"] == []


async def test_the_scan_orders_keys_newest_first_with_the_time_it_orders_by(seeded: Graph) -> None:
    g = seeded
    keys, capped = await _scan(g, Pushdown())
    assert capped is False
    expected = _reference(g, Pushdown())
    assert keys == [CandidateKey(r["_key"], r["updatedAt"]) for r in expected]


async def test_rows_are_read_only_for_this_orgs_live_records(seeded: Graph) -> None:
    g = seeded
    ids = [g.rid(n) for n in ("t1", "deleted", "foreign", "placeholder")] + ["no-such-record"]
    rows = await g.graph.get_record_query_rows(g.org, record_ids=ids, pushdown=Pushdown())
    # The placeholder is a live record of the org: the scan, not the read, leaves it out.
    assert sorted(r["_key"] for r in rows) == sorted([g.rid("t1"), g.rid("placeholder")])
    assert await g.graph.get_record_query_rows(g.other, record_ids=[g.rid("foreign")], pushdown=Pushdown()) != []
    assert await g.graph.get_record_query_rows(g.org, record_ids=[], pushdown=Pushdown()) == []


async def test_the_scan_cap_bounds_the_candidates_and_says_so(seeded: Graph) -> None:
    g = seeded
    total = len(LIVE)
    keys, capped = await _scan(g, Pushdown(), scan_cap=3)
    assert capped is True
    assert len(keys) == 3
    assert len({k.id for k in keys}) == 3
    stamps = [(-k.sort_ts, k.id) for k in keys]
    assert stamps == sorted(stamps)
    _, capped = await _all_windows(g, Pushdown(), window=100, scan_cap=total + 1)
    assert capped is False
    # A seeded scan over edges counts records, not edges: ann is on t4 twice.
    people = Pushdown(people=[PushedPeople(edges=list(PERSON_EDGES), ids=[g.nid("ann")])])
    expected = _reference(g, people)
    rows, capped = await _all_windows(g, people, window=100, scan_cap=len(expected) + 1)
    assert capped is False
    assert [r["_key"] for r in rows] == [r["_key"] for r in expected]


async def test_within_narrows_as_the_access_check_does(seeded: Graph) -> None:
    g = seeded
    for within in (
        [{"apps": [g.connectors[ZENDESK]], "groups": [], "records": []}],
        [{"apps": [], "groups": [g.gid("jira")], "records": [g.rid("t4")]}],
        [{"apps": [g.connectors[ZENDESK], g.connectors[JIRA]], "groups": [], "records": []},
         {"apps": [], "groups": [g.gid("child"), g.gid("jira")], "records": []}],
        [],
    ):
        await _assert_matches(g, Pushdown(), within=within)
        await _assert_matches(g, Pushdown(record_types=["TICKET"]), within=within)


async def test_empty_scope_reads_nothing(seeded: Graph) -> None:
    g = seeded
    assert (await g.graph.get_record_query_candidates("", connector_ids=g.connector_ids, pushdown=Pushdown())).keys == []
    assert (await g.graph.get_record_query_candidates(g.org, connector_ids=[], pushdown=Pushdown())).keys == []
    only_jira = await g.graph.get_record_query_candidates(
        g.org, connector_ids=[g.connectors[JIRA]], pushdown=Pushdown(),
    )
    assert sorted(k.id for k in only_jira.keys) == sorted(g.rid(n) for n in ("f1", "t3", "u1", "u2", "u3"))
    unknown = await g.graph.get_record_query_candidates(
        g.org, connector_ids=g.connector_ids, pushdown=Pushdown(entities=[{"type": "nonsense", "id": "x"}]),
    )
    assert unknown.keys == []


# ---------------------------------------------------------------- end to end


def _context(g: Graph) -> EntityAccessContext:
    return EntityAccessContext(
        org_id=g.org, user_key=g.nid("ann"), app_level_app_ids=frozenset(),
        record_level_app_ids=frozenset(g.connectors.values()), record_group_ids=frozenset(),
        app_names={key: c.value for c, key in g.connectors.items()},
    )


async def test_a_member_counts_sums_and_groups_only_what_they_can_read(seeded: Graph) -> None:
    g = seeded
    readable = {g.rid(n) for n in LIVE if SPECS[n].readable}
    admitted = await g.graph.check_access(g.nid("ann"), g.org, node_ids=sorted(g.rid(n) for n in LIVE))
    assert set(admitted.node_ids) == readable

    tickets = await run_record_query(g.graph, _context(g), RecordQuery.model_validate({
        "where": [{"field": "record_type", "in": ["ticket"]}],
        "group_by": {"person_role": "assignee"}, "limit": 50,
    }), window=2)
    assert tickets.total == 4  # t1, t3, t4, t5; t2 is not readable
    assert tickets.approximate is False
    assert {r["record_id"] for r in tickets.rows} == {g.rid(n) for n in ("t1", "t3", "t4", "t5")}
    # Bob is assignee of t2 (unreadable) and t3; Ann of t1 and t4.
    assert {(grp.label, grp.count) for grp in tickets.groups} == {
        (f"Ann ({g.email('ann')})", 2), (f"Bob ({g.email('bob')})", 1),
    }

    deals = await run_record_query(g.graph, _context(g), RecordQuery.model_validate({
        "where": [{"field": "record_type", "in": ["deal"]}, {"organization": {"ids": [g.nid("acme")]}}],
        "aggregate": {"op": "sum", "field": "amount"},
    }))
    assert (deals.total, deals.value) == (1, 1000.0)
    assert [r["record_id"] for r in deals.rows] == [g.rid("d1")]

    linked = await run_record_query(g.graph, _context(g), RecordQuery.model_validate({
        "where": [{"person": {"role": "reporter", "ids": [g.nid("ann")]}}],
    }))
    # Ann reports t4 as herself and t5 as her outsider node.
    assert [r["record_id"] for r in linked.rows] == [g.rid("t4"), g.rid("t5")]
    assert linked.total == 2


# ---------------------------------------------------------------- one person


async def test_person_nodes_are_every_node_of_one_email_in_the_org(seeded: Graph) -> None:
    g = seeded

    async def ids(*names: str) -> list[str]:
        return [n["id"] for n in await g.graph.get_person_nodes(g.org, [g.nid(x) for x in names])]

    ann = sorted(g.nid(n) for n in ANN_NODES)
    assert await ids("ann") == ann
    assert await ids("ann2") == ann and await ids("annx") == ann
    assert await ids("bob") == [g.nid("bob")]
    assert await ids("pete") == [g.nid("pete")]
    # Another org's node, even one with Ann's email, names no one here.
    assert await ids("annz") == [] and await ids("zed") == []
    assert await g.graph.get_person_nodes(g.org, ["no-such-node"]) == []
    one = await g.graph.get_person_nodes(g.org, [g.nid("ann")])
    assert {n["id"]: (n["name"], n["email"]) for n in one}[g.nid("annx")] == ("Ann X", g.email("annx"))
    assert len(await g.graph.get_person_nodes(g.org, [g.nid("ann")], limit=2)) == 2


async def test_a_person_condition_and_me_match_every_node_of_the_person(seeded: Graph) -> None:
    g = seeded
    for ids in ([g.nid("ann")], [g.nid("annx")], ["me"]):
        found = await run_record_query(g.graph, _context(g), RecordQuery.model_validate({
            "where": [{"person": {"ids": ids}}], "group_by": {"by_person": True},
        }))
        # t2 is unreadable; t5 is Ann's as the outsider node, d1 as ann and ann2.
        assert {r["record_id"] for r in found.rows} == {g.rid(n) for n in ("t1", "t4", "d1", "t5")}, ids
    editor = await run_record_query(g.graph, _context(g), RecordQuery.model_validate({
        "where": [{"person": {"role": "editor", "ids": ["me"]}}],
    }))
    assert [r["record_id"] for r in editor.rows] == [g.rid("d1")]
    by_person = await run_record_query(g.graph, _context(g), RecordQuery.model_validate({
        "where": [{"field": "record_type", "in": ["deal", "ticket"]}], "group_by": {"by_person": True},
    }))
    ann_groups = [grp for grp in by_person.groups if grp.label.startswith("Ann")]
    assert [(grp.key, grp.label) for grp in ann_groups] == [(g.nid("ann"), f"Ann ({g.email('ann')})")]
    # t1, t4, t5 and d1 are Ann's and readable; t2 is not readable.
    assert ann_groups[0].count == 4


# ---------------------------------------------------------------- outsiders


async def test_an_outsider_is_a_person_entity_as_a_member_is(seeded: Graph) -> None:
    """record_people's person nodes are reached through their records by
    every path a member is: indexing, membership, listing and the access-checked walk."""
    g = seeded
    pete = g.nid("pete")
    from app.config.constants.arangodb import CollectionNames as Names

    people = await g.graph.get_record_people(g.rid("t1"), g.org)
    assert {p["id"] for p in people} == {g.nid("ann"), pete}
    assert {p["id"]: p["email"] for p in people}[pete] == g.email("pete")

    membership = await g.graph.get_taxonomy_entity_membership([{"id": pete, "type": "person"}], g.org)
    assert sorted(membership[("person", pete)]["connectorIds"]) == sorted(
        [g.connectors[ZENDESK], g.connectors[SALESFORCE]])

    ref = {"id": pete, "type": "person", "connectorIds": g.connector_ids}
    candidates = await g.graph.get_entity_candidate_records([ref], g.org)
    # The listing reads each person edge in its stored direction; d2's INVOLVED_IN
    # is seeded record -> person, the other way from how Slack stores it.
    assert [r["_key"] for r in candidates[("person", pete)]] == [g.rid("t1")]
    permitted = await g.graph.get_permitted_entity_records(
        [ref], g.org, g.nid("ann"), app_level_connector_ids=[],
    )
    # d2 is not readable to Ann.
    assert [r["_key"] for r in permitted[("person", pete)]] == [g.rid("t1")]

    page = await g.graph.page_entity_index_source(Names.PEOPLE.value, g.org, None, 50)
    assert {(r["_key"], r["name"]) for r in page} == {(pete, "Pete"), (g.nid("annx"), "Ann X")}
    # Another org's outsider is in no page and links to nothing here.
    foreign = await g.graph.get_taxonomy_entity_membership([{"id": g.nid("xena"), "type": "person"}], g.org)
    assert foreign[("person", g.nid("xena"))]["connectorIds"] == []


async def test_pages_follow_the_cursor_without_repeats_when_a_record_changes(seeded: Graph) -> None:
    g = seeded
    readable = [g.rid(n) for n in _reference_names(g) if SPECS[n].readable]
    seen: list[str] = []
    cursor = None
    for _ in range(10):
        page = await run_record_query(g.graph, _context(g), RecordQuery.model_validate(
            {"limit": 2, **({"cursor": cursor} if cursor else {})}), window=2)
        seen += [r["record_id"] for r in page.rows]
        if len(seen) == 2:
            # The oldest becomes the newest between pages: it moves to before the
            # cursor, and a position cursor would show the second record again.
            await _touch(g, readable[-1], 500_000)
        cursor = page.next_cursor
        if not cursor:
            break
    assert len(seen) == len(set(seen))
    assert seen == readable[:-1]


def _reference_names(g: Graph) -> list[str]:
    return [r["_key"].removeprefix(f"{g.org}-") for r in _reference(g, Pushdown())]


async def _touch(g: Graph, record_id: str, updated: int) -> None:
    if isinstance(g.graph, Neo4jProvider):
        await g.graph.client.execute_query(
            "MATCH (r:Record {id: $id}) SET r.sourceLastModifiedTimestamp = $ts",
            parameters={"id": record_id, "ts": updated},
        )
        return
    await g.graph.http_client.execute_aql(
        f"UPDATE @key WITH {{sourceLastModifiedTimestamp: @ts}} IN {C.RECORDS.value}", {"key": record_id, "ts": updated},
    )


async def test_sort_by_a_date_field_pages_in_order_with_unknowns_last(seeded: Graph) -> None:
    g = seeded
    seen: list[str] = []
    cursor = None
    for _ in range(10):
        page = await run_record_query(g.graph, _context(g), RecordQuery.model_validate(
            {"sort": "created desc", "limit": 4, **({"cursor": cursor} if cursor else {})}))
        seen += [r["record_id"].removeprefix(f"{g.org}-") for r in page.rows]
        cursor = page.next_cursor
        if not cursor:
            break
    # t5 has no created time: last, never placed as if it were oldest or newest.
    assert seen == ["f1", "d1", "t4", "u3", "u2", "u1", "t3", "t1", "t5"]


async def test_records_not_classified_yet_are_unknown_never_a_miss(seeded: Graph) -> None:
    g = seeded
    topic = await run_record_query(g.graph, _context(g), RecordQuery.model_validate({
        "where": [{"entity": {"type": "topic", "id": g.topic}}], "limit": 50,
    }))
    # u2 keeps its topic edge while it is classified again; u1 has none yet.
    assert sorted(r["record_id"] for r in topic.rows) == sorted(g.rid(n) for n in ("t1", "t3", "u2"))
    assert (topic.total, topic.unknown) == (3, {"classification": 1, "classification_failed": 1})
    assert sorted(d["_key"] for d in topic.not_classified) == sorted([g.rid("u1"), g.rid("u2")])

    acme = await run_record_query(g.graph, _context(g), RecordQuery.model_validate({
        "where": [{"organization": {"ids": [g.nid("acme")]}}],
    }))
    assert sorted(r["record_id"] for r in acme.rows) == sorted(g.rid(n) for n in ("t1", "d1"))
    assert acme.unknown == {"classification": 2, "classification_failed": 1}

    not_acme = await run_record_query(g.graph, _context(g), RecordQuery.model_validate({
        "where": [{"field": "source", "in": ["JIRA"]},
                  {"not": {"organization": {"ids": [g.nid("acme")]}}}],
    }))
    assert sorted(r["record_id"] for r in not_acme.rows) == sorted(g.rid(n) for n in ("t3", "f1"))
    assert not_acme.unknown == {"classification": 2, "classification_failed": 1}


async def test_grouping_by_organisation_lists_unplaced_records_apart(seeded: Graph) -> None:
    g = seeded
    result = await run_record_query(g.graph, _context(g), RecordQuery.model_validate({
        "group_by": {"organization": True}, "limit": 0,
    }))
    assert [(grp.label, grp.count) for grp in result.groups] == [("Acme", 2)]  # t1, d1
    assert [(grp.key, grp.count) for grp in result.unassigned] == [
        ("no_organization", 4), ("classification", 2), ("classification_failed", 1),
    ]
    assert sum(grp.count for grp in [*result.groups, *result.unassigned]) == result.total == 9


def _operators(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [plan, *(op for child in plan.get("children", []) for op in _operators(child))]


async def test_the_seed_of_records_not_classified_yet_reads_the_org_status_index(seeded: Graph) -> None:
    """A topic seed also reads every record of the org not classified yet;
    with classify on first use that is most of the org, so it must seek the
    (orgId, extractionStatus) index instead of scanning the org's records."""
    g = seeded
    pushed = Pushdown(entities=[{"type": "topic", "id": g.topic}])
    params = {"org_id": g.org, "connector_ids": g.connector_ids, "within": None, "completed": "COMPLETED",
              "scan_cap": 1_000}
    if isinstance(g.graph, Neo4jProvider):
        for statement in g.graph._generate_performance_indexes():
            if "extractionStatus" in statement:
                await g.graph.client.execute_query(statement)
        await g.graph.client.execute_query("CALL db.awaitIndexes(300)")
        query, built = g.graph._record_query_cypher(pushed)
        async with g.graph.client.driver.session(database=g.graph.client.database) as session:
            summary = await (await session.run(f"EXPLAIN {query}", {**built, **params})).consume()
        seeks = [op for op in _operators(summary.plan) if "IndexSeek" in op["operatorType"]]
        assert any("extractionStatus" in op["args"].get("Details", "") for op in seeks), summary.plan
    else:
        client = g.graph.http_client
        session = await client._get_session()
        db = f"{client.base_url}/_db/{client.database}/_api/index"
        async with session.get(db, params={"collection": C.RECORDS.value}) as resp:
            existing = (await resp.json())["indexes"]
        for index in existing:
            # The hint names the index: one left on the same fields under another name would not serve it.
            if index.get("fields") == ["orgId", "extractionStatus"] and index["name"] != "records_org_extraction_status":
                async with session.delete(f"{db}/{index['id']}") as resp:
                    assert resp.status == 200
        assert await client.ensure_persistent_index(C.RECORDS.value, ["orgId", "extractionStatus"],
                                                    name="records_org_extraction_status")
        query, built = g.graph._record_query_aql(pushed)
        used = {k: v for k, v in {**built, **params}.items() if f"@{k}" in query}
        async with session.post(f"{client.base_url}/_db/{client.database}/_api/explain",
                                json={"query": query, "bindVars": used}) as resp:
            nodes = (await resp.json())["plan"]["nodes"]

        def flat(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
            return [n for node in nodes for n in [node, *flat(node.get("subquery", {}).get("nodes", []))]]

        fields = [i["fields"] for n in flat(nodes) if n["type"] == "IndexNode" for i in n["indexes"]]
        assert ["orgId", "extractionStatus"] in fields, fields
    keys, _ = await _scan(g, pushed)
    assert [k.id for k in keys] == [r["_key"] for r in _reference(g, pushed)]

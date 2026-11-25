"""A small code graph with per-user access, and a rule for what each user may see written apart from the code under test."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient

from app.api.routes import graph_view as routes
from app.config.constants.arangodb import CollectionNames
from tests.unit.agents.actions.code_graph.conftest import (
    BLOCKS,
    CONN,
    ORG,
    FakeGraphProvider,
    _block,
)

SYNC_POINTS = CollectionNames.SYNC_POINTS.value
OTHER_CONN = "conn-2"


@dataclass
class Spec:
    """The graph and the access rules, as plain data."""

    records: dict[str, str] = field(default_factory=dict)            # record id -> connector id
    blocks: dict[str, str] = field(default_factory=dict)            # block key -> record id
    edges: list[tuple[str, str, str]] = field(default_factory=list)  # (from block, to block, type)
    acl: dict[str, set[str]] = field(default_factory=dict)          # user id -> record ids they read
    test_records: set[str] = field(default_factory=set)
    orgs: dict[str, str] = field(default_factory=dict)              # block key -> org, when not ORG


class World(FakeGraphProvider):
    """The shared fake, rebuilt from a ``Spec``, with per-user access and switchable failures."""

    def __init__(self, spec: Spec) -> None:
        super().__init__(file_roles=dict.fromkeys(spec.test_records, "test"))
        self.spec = spec
        self.fail_access: Exception | None = None
        self.fail_store: Exception | None = None
        self.blocks, self.code_files, self.edges = {}, {}, []
        self.records = {rid: {"_key": rid, "orgId": ORG, "connectorId": conn, "virtualRecordId": f"v-{rid}"}
                        for rid, conn in spec.records.items()}
        for key, rid in spec.blocks.items():
            doc = _block(key, rid, f"src/{rid}.py", f"sym_{key}", key)
            doc["connectorId"] = spec.records[rid]
            doc["orgId"] = spec.orgs.get(key, ORG)
            self.code_files[rid] = doc.pop("filePath")
            self.blocks[key] = doc
        self.edges = [{"_from": f"{BLOCKS}/{a}", "_to": f"{BLOCKS}/{b}", "relationshipType": t} for a, b, t in spec.edges]

    async def filter_accessible_record_ids(self, record_ids, user_id, org_id, *, transaction=None) -> set[str]:
        if self.fail_access:
            raise self.fail_access
        return set(record_ids) & self.spec.acl.get(user_id, set()) & set(self.records)

    async def get_neighbors_for_nodes_by_relationship_types(self, *a: object, **k: object) -> list[dict]:
        if self.fail_store:
            raise self.fail_store
        rows = await super().get_neighbors_for_nodes_by_relationship_types(*a, **k)
        for row in rows:  # the real stores carry the edge's line and confidence on the row
            row["line"] = 7
            row["confidence"] = "EXTRACTED"
        return rows

    async def get_nodes_by_filters(self, collection, filters, *a: object, **k: object) -> list[dict]:
        if collection == SYNC_POINTS:
            return [{"lastEdgeBuildAt": 1, "edgeBuildPending": False}]
        return await super().get_nodes_by_filters(collection, filters, *a, **k)


class _Container:
    def __init__(self, graph: World) -> None:
        self._graph = graph

    async def graph_provider(self) -> World:
        return self._graph


def client(graph: World, user: dict | None = None) -> TestClient:
    app = FastAPI()
    app.container = _Container(graph)

    @app.middleware("http")
    async def _auth(request: Request, call_next) -> Response:
        request.state.user = user or {"orgId": ORG, "userId": "alice"}
        return await call_next(request)

    app.include_router(routes.router, prefix="/api/v1")
    return TestClient(app)


def expand(graph: World, user: str, ids: list[str], **body: object):  # noqa: ANN201
    return client(graph, {"orgId": ORG, "userId": user}).post(
        "/api/v1/graph/expand", json={"source": "code", "nodeIds": [f"block:{i}" for i in ids], **body})


def revalidate(graph: World, user: str, ids: list[str], **body: object):  # noqa: ANN201
    return client(graph, {"orgId": ORG, "userId": user}).post(
        "/api/v1/graph/revalidate", json={"source": "code", "nodeIds": ids, **body})


def shown(res) -> tuple[set[str], set[tuple[str, str, str]]]:
    """The block keys and ``(from, to, type)`` triples of an expand response."""
    body = res.json()
    return ({n["id"].removeprefix("block:") for n in body["nodes"]},
            {(e["from"].removeprefix("block:"), e["to"].removeprefix("block:"), e["type"]) for e in body["edges"]})


def expected(spec: Spec, user: str, anchors: list[str], direction: str = "any", types: set[str] | None = None,
             include_tests: bool = False) -> tuple[set[str], set[tuple[str, str, str]]]:
    """What ``user`` may see around ``anchors``, from the rules alone: a block is seen when its file is readable,
    it is in the caller's org, it is not test code unless asked for, and (beside an anchor) it is in the anchor's connector."""
    def sees(block: str) -> bool:
        rid = spec.blocks.get(block)
        return (rid is not None and rid in spec.acl.get(user, set()) and spec.orgs.get(block, ORG) == ORG
                and (include_tests or rid not in spec.test_records))

    def conn(block: str) -> str:
        return spec.records[spec.blocks[block]]

    nodes: set[str] = set()
    edges: set[tuple[str, str, str]] = set()
    for a in (x for x in anchors if sees(x)):
        nodes.add(a)
        for src, dst, kind in spec.edges:
            if types is not None and kind not in types:
                continue
            for near, far, way in ((src, dst, "outbound"), (dst, src, "inbound")):
                if near == a and direction in (way, "any") and sees(far) and conn(far) == conn(a):
                    edges.add((src, dst, kind))
                    nodes |= {src, dst}
    return nodes, edges


def personas() -> Spec:
    """``alice`` reads everything, ``bob`` all but ``r_secret``, ``carol`` nothing."""
    spec = Spec(
        records={"r_main": CONN, "r_lib": CONN, "r_secret": CONN, "r_test": CONN, "r_other": OTHER_CONN},
        blocks={"main": "r_main", "lib": "r_lib", "secret": "r_secret", "tst": "r_test", "other": "r_other"},
        edges=[("main", "lib", "CALLS"), ("main", "secret", "CALLS"), ("tst", "main", "CALLS"),
               ("lib", "main", "IMPORTS_FROM"), ("secret", "lib", "CALLS"), ("main", "other", "CALLS")],
        test_records={"r_test"},
    )
    every = set(spec.records)
    spec.acl = {"alice": every, "bob": every - {"r_secret"}, "carol": set()}
    return spec


def body_bytes(res) -> bytes:
    return json.dumps(res.json(), sort_keys=True).encode() + str(res.status_code).encode()

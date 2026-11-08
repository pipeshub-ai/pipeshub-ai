"""Walk a real hub by cursor on Neo4j, ArangoDB and PostgreSQL: complete, no repeats, no dead ends, and the
same bytes with or without files the caller cannot read.
"""
from __future__ import annotations

import contextlib
import json
import uuid
from typing import Any

import pytest

from app.agents.actions.code_graph.ops import CODE_RELATIONS
from app.config.constants.arangodb import CollectionNames
from app.modules.graph_view.service import GraphViewError
from tests.integration.graph_db.test_code_graph_real_backends import (
    BLOCKS,
    RECORDS,
    _build,
    _grant,
    _Repo,
    _write_repo,
)
from tests.integration.graph_db.test_graph_view_real_backends import (
    _connect_postgres,
    _World,
)

pytestmark = [pytest.mark.integration, pytest.mark.timeout(900)]

N = 12
TARGET = "def target(x):\n    return x\n"


def caller(i: int, calls: int = 1) -> str:
    body = " + ".join(f"target({i + k})" for k in range(calls))
    return f"from hub import target\n\n\ndef caller_{i}():\n    return {body}\n"


FILES = {
    "src/hub.py": TARGET,
    **{f"src/c{i}.py": caller(i, calls=2 if i % 4 == 0 else 1) for i in range(N)},         # c0, c4, c8 call it twice
    **{f"src/secret{i}.py": caller(100 + i) for i in range(5)},
    "tests/test_hub.py": "from hub import target\n\n\ndef test_hub():\n    assert target(1)\n",
}
ROLES = {"tests/test_hub.py": "test"}
HIDDEN_FROM_BOB = {f"src/secret{i}.py" for i in range(5)}


@pytest.fixture(params=["neo4j", "arango", "postgres"])
async def hub(request, monkeypatch):  # noqa: ANN201
    import logging

    from tests.integration.real_graph import (
        backend_unavailable,
        connect_arango,
        connect_neo4j,
    )

    log = logging.getLogger("graph-view-paging")
    async with contextlib.AsyncExitStack() as cleanup:
        org_id = f"org-paging-{uuid.uuid4().hex[:10]}"
        if request.param == "postgres":
            graph, db, org_id = await _connect_postgres()
            cleanup.push_async_callback(db.close)
        else:
            try:
                graph = await (connect_neo4j(log, monkeypatch) if request.param == "neo4j"
                               else connect_arango(log, "graph_view_qa"))
            except Exception as exc:
                backend_unavailable(request.param, exc)
            if (disconnect := getattr(graph, "disconnect", None)) is not None:
                cleanup.push_async_callback(disconnect)
        run = uuid.uuid4().hex[:10]
        connector_id, group_id = f"conn-paging-{run}", f"g-paging-{run}"
        record_ids, blobs = await _write_repo(graph, org_id, connector_id, group_id, files=FILES, roles=ROLES)
        repo = _Repo(graph, request.param, org_id, connector_id, group_id, record_ids, blobs)
        users = {p: f"{p}-{run}" for p in ("alice", "bob")}
        keys = {p: f"u-{u}" for p, u in users.items()}

        async def remove() -> None:
            with contextlib.suppress(Exception):
                await graph.delete_blocks_for_records(list(repo.record_ids.values()))
            for coll, ids in ((CollectionNames.CODE_FILES.value, repo.record_ids.values()), (RECORDS, repo.record_ids.values()),
                              (CollectionNames.USERS.value, keys.values()), (CollectionNames.APPS.value, [connector_id])):
                with contextlib.suppress(Exception):
                    await graph.delete_nodes_and_edges(list(ids), coll)

        cleanup.push_async_callback(remove)
        await _build(repo)
        w = _World(repo, users, keys)
        await _grant(repo, keys["alice"], users["alice"], list(repo.record_ids.values()))
        await _grant(repo, keys["bob"], users["bob"], [r for p, r in repo.record_ids.items() if p not in HIDDEN_FROM_BOB])
        yield w


async def stored(w: _World) -> tuple[set[tuple[str, str, str]], dict[str, str]]:
    r = w.repo
    by_record = {rid: path for path, rid in r.record_ids.items()}
    blocks = await r.graph.get_nodes_by_field_in(BLOCKS, "orgId", [r.org_id], raise_on_error=True)
    owner = {b.get("_key") or b.get("id"): by_record[b["recordId"]] for b in blocks if b.get("recordId") in by_record}
    rows = await r.graph.get_neighbors_for_nodes_by_relationship_types(
        node_keys=list(owner), node_collection=BLOCKS, relationship_types=list(CODE_RELATIONS), direction="outbound",
        limit=100000, raise_on_error=True)
    return {(x["anchorKey"], x["key"], x["relationshipType"]) for x in rows if x["collection"] == BLOCKS}, owner


def rule(persona: str, st, anchors: list[str], direction: str, tests: bool) -> set[tuple[str, str, str]]:
    edges, owner = st

    def sees(key: str) -> bool:
        path = owner.get(key)
        return path is not None and (persona == "alice" or path not in HIDDEN_FROM_BOB) and (tests or ROLES.get(path) != "test")

    out = set()
    for a in (x for x in anchors if sees(x)):
        for src, dst, kind in edges:
            for near, far, way in ((src, dst, "outbound"), (dst, src, "inbound")):
                if near == a and direction in (way, "any") and sees(far):
                    out.add((src, dst, kind))
    return out


async def pages(w: _World, persona: str, ids: list[str], limit: int, **body: object) -> list[dict]:
    cursor, out = None, []
    for _ in range(300):
        res = await w.expand(persona, ids, limit=limit, **body, **({"cursor": cursor} if cursor else {}))
        assert not isinstance(res, GraphViewError), res
        out.append(res)
        cursor = res["page"].get("nextCursor")
        if not cursor:
            return out
    raise AssertionError("did not end")


@pytest.mark.parametrize("persona", ["alice", "bob"])
@pytest.mark.parametrize("tests", [False, True])
@pytest.mark.parametrize("direction", ["any", "inbound"])
@pytest.mark.parametrize("limit", [1, 2, 3, 100])
async def test_a_hub_walked_by_cursor_is_complete_once_each_and_ends(hub: _World, persona, tests, direction, limit) -> None:
    r, st = hub.repo, await stored(hub)
    target = r.block("src/hub.py", "function:target")
    for anchors in ([target], [target, r.block("src/c0.py", "function:caller_0")], [r.block("src/c4.py", "function:caller_4"), target]):
        got = await pages(hub, persona, [f"block:{a}" for a in anchors], limit, direction=direction, includeTests=tests)
        ids = [e["id"] for p in got for e in p["edges"]]
        triples = {(e["from"][6:], e["to"][6:], e["type"]) for p in got for e in p["edges"]}
        want = rule(persona, st, anchors, direction, tests)
        assert len(ids) == len(set(ids)), f"{r.backend}: an edge came back twice"
        assert triples == want, f"{r.backend} {persona} limit={limit}: missing {sorted(want - triples)[:3]} extra {sorted(triples - want)[:3]}"
        assert all(len(p["edges"]) <= limit for p in got)
        assert not any(p["page"]["truncated"] and not p["edges"] for p in got), "empty page that says there is more"


async def test_a_whole_walk_is_the_same_bytes_with_or_without_files_the_caller_cannot_read(hub: _World) -> None:
    r = hub.repo
    target = f"block:{r.block('src/hub.py', 'function:target')}"
    bob = hub.users["bob"]
    walks = {}
    for label in ("with", "without"):
        if label == "without":
            # Take the five files away from the graph altogether (the world bob would see if they never existed).
            for path in HIDDEN_FROM_BOB:
                rid = r.record_ids[path]
                await r.graph.delete_blocks_for_records([rid])
        walks[label] = {limit: json.dumps(await pages(hub, "bob", [target], limit, direction="any"), sort_keys=True)
                        for limit in (1, 2, 3, 100)}
    assert bob
    for limit in (1, 2, 3, 100):
        assert walks["with"][limit] == walks["without"][limit], f"{r.backend} limit={limit}"



async def test_a_connected_app_limited_to_the_whole_connector_sees_what_its_user_sees_and_one_limited_elsewhere_sees_nothing(hub: _World) -> None:
    from app.agents.actions.code_graph.ops import RecordAccess
    from app.modules.graph_view.models import ExpandRequest
    from app.modules.graph_view.policy import VisibilityPolicy
    from app.modules.graph_view.service import GraphViewService
    from app.modules.graph_view.sources.code import CodeSource
    from app.modules.retrieval.selection_scope import (
        groups_outside_connection,
        outside_connection,
    )

    r = hub.repo
    target = f"block:{r.block('src/hub.py', 'function:target')}"
    user = hub.users["bob"]

    async def expand_under(limit: dict) -> object:
        access = RecordAccess(r.graph, r.org_id, user)
        policy = VisibilityPolicy(r.graph, r.org_id, access,
                                  outside_limit=lambda ids: outside_connection(r.graph, user, r.org_id, ids, limit),
                                  groups_outside=lambda g: groups_outside_connection(r.graph, user, r.org_id, g, limit))
        service = GraphViewService({"code": CodeSource(r.graph, r.org_id, user)})
        try:
            return await service.expand(ExpandRequest(source="code", nodeIds=[target], limit=100), access=access, policy=policy)
        except GraphViewError as exc:
            return exc

    # The repo's record group, with the user's own permission on it, as a sync leaves it.
    now = 1_700_000_000_000
    await r.graph.batch_upsert_nodes(
        [{"id": r.group_id, "orgId": r.org_id, "connectorId": r.connector_id, "groupName": "acme/shop",
          "groupType": "REPOSITORY", "connectorName": "GITHUB", "createdAtTimestamp": now, "updatedAtTimestamp": now}],
        collection=CollectionNames.RECORD_GROUPS.value)
    await r.graph.batch_create_edges(
        [{"from_id": hub.keys["bob"], "from_collection": CollectionNames.USERS.value, "to_id": r.group_id,
          "to_collection": CollectionNames.RECORD_GROUPS.value, "role": "OWNER", "type": "USER",
          "createdAtTimestamp": now, "updatedAtTimestamp": now}], collection=CollectionNames.PERMISSION.value)
    free = await hub.expand("bob", [target], limit=100)
    whole = await expand_under({"apps": [r.connector_id]})
    other = await expand_under({"apps": ["some-other-connector"]})
    assert not isinstance(whole, GraphViewError) and json.dumps(whole, sort_keys=True) == json.dumps(free, sort_keys=True)
    assert isinstance(other, GraphViewError) and other.status == 404


async def test_limits_that_name_the_repo_a_folder_or_one_file(hub: _World) -> None:
    """Records what the graph view answers under limits narrower than the whole connector, next to what the
    existing code-graph routes decide (`_inside_limit`: is the repo's record group inside the limit?)."""
    from app.agents.actions.code_graph.ops import RecordAccess
    from app.modules.graph_view.policy import VisibilityPolicy
    from app.modules.retrieval.selection_scope import (
        groups_outside_connection,
        outside_connection,
    )

    r = hub.repo
    user = hub.users["bob"]
    now = 1_700_000_000_000
    await r.graph.batch_upsert_nodes(
        [{"id": r.group_id, "orgId": r.org_id, "connectorId": r.connector_id, "groupName": "acme/shop",
          "groupType": "REPOSITORY", "connectorName": "GITHUB", "createdAtTimestamp": now, "updatedAtTimestamp": now}],
        collection=CollectionNames.RECORD_GROUPS.value)
    await r.graph.batch_create_edges(
        [{"from_id": hub.keys["bob"], "from_collection": CollectionNames.USERS.value, "to_id": r.group_id,
          "to_collection": CollectionNames.RECORD_GROUPS.value, "role": "OWNER", "type": "USER",
          "createdAtTimestamp": now, "updatedAtTimestamp": now}], collection=CollectionNames.PERMISSION.value)
    hub_file = r.record_ids["src/hub.py"]
    target, c1 = r.block("src/hub.py", "function:target"), r.block("src/c1.py", "function:caller_1")
    ids = [f"block:{target}", f"block:{c1}"]
    out: dict[str, Any] = {}
    for label, limit in (("repo", {"recordGroups": [r.group_id]}), ("one file", {"records": [hub_file]}),
                         ("app", {"apps": [r.connector_id]})):
        access = RecordAccess(r.graph, r.org_id, user)
        policy = VisibilityPolicy(r.graph, r.org_id, access,
                                  outside_limit=lambda i, lim=limit: outside_connection(r.graph, user, r.org_id, i, lim),
                                  groups_outside=lambda g, lim=limit: groups_outside_connection(r.graph, user, r.org_id, g, lim))
        out[label] = (sorted(await policy.visible(ids)),
                      sorted(await groups_outside_connection(r.graph, user, r.org_id, {r.group_id: r.connector_id}, limit)))
    # The existing routes admit a repo only when its record group is inside the limit.
    for label, (visible, group_outside) in out.items():
        repo_inside = not group_outside
        blocks_visible = any(v.startswith("block:") for v in visible)
        assert blocks_visible == repo_inside, f"{r.backend} {label}: blocks visible={blocks_visible} but repo inside the limit={repo_inside}"

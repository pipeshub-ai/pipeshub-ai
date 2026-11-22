"""The graph view's visibility rule on real Neo4j, ArangoDB and PostgreSQL (the code graph's own pattern).

One small repo is parsed, projected and edge-built the way production does it, three people are given different
files, and ``/expand`` and ``/revalidate`` are checked against a rule written here from the stored edges and the
grants alone. Hidden files are then added and revoked, and the responses must not notice.

Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD, and for PostgreSQL PG_CORE_URL
(a migrated database, see ``scripts/prepare_pg_test_db.sh``) with PG_POOLER=none. A backend that is not configured skips.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest

from app.agents.actions.code_graph.ops import CODE_RELATIONS, RecordAccess
from app.config.constants.arangodb import CollectionNames
from app.modules.graph_view.models import (
    ExpandRequest,
    ResolveRequest,
    RevalidateRequest,
)
from app.modules.graph_view.policy import VisibilityPolicy
from app.modules.graph_view.service import GraphViewError, GraphViewService
from app.modules.graph_view.sources.code import CodeSource
from tests.integration.graph_db.test_code_graph_real_backends import (
    BLOCKS,
    RECORDS,
    _build,
    _grant,
    _Repo,
    _write_repo,
)
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(600)]

ARANGO_DB = "graph_view_it"
logger = logging.getLogger("graph-view-real-backends-it")

FILES = {
    "src/shop/b.py": ("from shop.hidden import hidden_fn\n\n\ndef target(x):\n    hidden_fn(x)\n    return helper(x)\n\n\n"
                      "def helper(x):\n    return x\n"),
    "src/shop/a.py": "from shop.b import target\n\n\ndef caller():\n    return target(1)\n",
    "src/shop/secret.py": "from shop.b import target\n\n\ndef leak():\n    return target(2)\n",
    "tests/test_b.py": "from shop.b import target\n\n\ndef test_target():\n    assert target(1)\n",
}
VISIBLE = {"alice": set(FILES) | {"src/shop/hidden.py"}, "bob": set(FILES) - {"src/shop/secret.py"}, "carol": set()}
HIDDEN = "src/shop/hidden.py"
HIDDEN_FILE = "from shop.b import target\n\n\ndef hidden_fn(x):\n    return target(x)\n\n\n" + "".join(
    f"def hidden_caller_{i}():\n    return target({i})\n\n\n" for i in range(30))
ROLES = {"tests/test_b.py": "test"}
SYMBOLS = {"src/shop/b.py": ["function:target", "function:helper"], "src/shop/a.py": ["function:caller"],
           "src/shop/secret.py": ["function:leak"], "tests/test_b.py": ["function:test_target"]}


@dataclass
class _World:
    repo: _Repo
    users: dict[str, str]          # persona -> user id
    keys: dict[str, str]           # persona -> user node key

    def block_id(self, path: str, name: str) -> str:
        return f"block:{self.repo.block(path, name)}"

    def service(self, persona: str, **policy: object) -> tuple[GraphViewService, VisibilityPolicy, RecordAccess]:
        r = self.repo
        access = RecordAccess(r.graph, r.org_id, self.users[persona])
        return (GraphViewService({"code": CodeSource(r.graph, r.org_id, self.users[persona])}),
                VisibilityPolicy(r.graph, r.org_id, access, **policy), access)

    async def expand(self, persona: str, ids: list[str], **body: object) -> dict | GraphViewError:
        service, policy, access = self.service(persona, include_tests=body.get("includeTests", False))
        try:
            return await service.expand(ExpandRequest(source="code", nodeIds=ids, **body), access=access, policy=policy)
        except GraphViewError as exc:
            return exc

    async def revalidate(self, persona: str, ids: list[str], **body: object) -> list[str]:
        service, policy, _ = self.service(persona, include_tests=body.get("includeTests", False))
        return (await service.revalidate(RevalidateRequest(source="code", nodeIds=ids, **body), policy=policy))["visible"]


async def _connect_postgres() -> tuple[Any, Any, str]:
    from app.core.db import DbConfig, KernelDB
    from tests.integration.graph_db.postgres.pg_support import (
        OrgBound,
        StandInProvider,
        new_org,
    )

    if not os.environ.get("PG_CORE_URL"):
        pytest.skip("postgres not available (set PG_CORE_URL to require it)")
    db = KernelDB(DbConfig.from_env("connectors"))
    await db.start()
    org = new_org()
    return OrgBound(StandInProvider(logger, db), org), db, org


@pytest.fixture(params=["neo4j", "arango", "postgres"])
async def world(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_World]:
    async with contextlib.AsyncExitStack() as cleanup:
        org_id = f"org-gv-{uuid.uuid4().hex[:10]}"
        if request.param == "postgres":
            graph, db, org_id = await _connect_postgres()
            cleanup.push_async_callback(db.close)
        else:
            try:
                graph = await (connect_neo4j(logger, monkeypatch) if request.param == "neo4j"
                               else connect_arango(logger, ARANGO_DB))
            except Exception as exc:
                backend_unavailable(request.param, exc)
            if (disconnect := getattr(graph, "disconnect", None)) is not None:
                cleanup.push_async_callback(disconnect)

        run = uuid.uuid4().hex[:10]
        connector_id, group_id = f"conn-gv-{run}", f"g-gv-{run}"
        record_ids, blobs = await _write_repo(graph, org_id, connector_id, group_id, files=FILES, roles=ROLES)
        repo = _Repo(graph, request.param, org_id, connector_id, group_id, record_ids, blobs)
        users = {p: f"{p}-{run}" for p in VISIBLE}
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
        yield _World(repo, users, keys)


async def _grant_all(w: _World) -> None:
    for persona, paths in VISIBLE.items():
        await _grant(w.repo, w.keys[persona], w.users[persona], [w.repo.record_ids[p] for p in paths if p in w.repo.record_ids])


async def _stored(w: _World) -> tuple[set[tuple[str, str, str]], dict[str, str]]:
    """Every block-to-block code edge as the store holds it, ``(from key, to key, type)``, and each block's file path.

    Read with no access check, so the rule below is checked against the whole graph.
    """
    r = w.repo
    by_record = {rid: path for path, rid in r.record_ids.items()}
    blocks = await r.graph.get_nodes_by_field_in(BLOCKS, "orgId", [r.org_id], raise_on_error=True)
    owner = {b.get("_key") or b.get("id"): by_record[b["recordId"]] for b in blocks if b.get("recordId") in by_record}
    rows = await r.graph.get_neighbors_for_nodes_by_relationship_types(
        node_keys=list(owner), node_collection=BLOCKS, relationship_types=list(CODE_RELATIONS), direction="outbound",
        limit=100000, raise_on_error=True)
    return {(row["anchorKey"], row["key"], row["relationshipType"]) for row in rows if row["collection"] == BLOCKS}, owner


def _expected(persona: str, stored: tuple[set[tuple[str, str, str]], dict[str, str]], anchors: list[str], direction: str,
              tests: bool) -> tuple[set[str], set[tuple[str, str, str]]]:
    """The rule: a block is seen when its file is granted and it is not a test file unless asked for."""
    edges_in_store, owner = stored

    def sees(key: str) -> bool:
        path = owner.get(key)
        return path in VISIBLE[persona] and (tests or ROLES.get(path) != "test")

    nodes, edges = set(), set()
    for a in (x for x in anchors if sees(x)):
        nodes.add(a)
        for src, dst, kind in edges_in_store:
            for near, far, way in ((src, dst, "outbound"), (dst, src, "inbound")):
                if near == a and direction in (way, "any") and sees(far):
                    edges.add((src, dst, kind))
                    nodes |= {src, dst}
    return nodes, edges


def _shown(res: dict) -> tuple[set[str], set[tuple[str, str, str]]]:
    return ({n["id"].removeprefix("block:") for n in res["nodes"]},
            {(e["from"].removeprefix("block:"), e["to"].removeprefix("block:"), e["type"]) for e in res["edges"]})


def _bytes(res: dict | GraphViewError) -> bytes:
    return json.dumps({"status": res.status, "detail": res.detail} if isinstance(res, GraphViewError) else res,
                      sort_keys=True).encode()


# -- the oracle ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("persona", ["alice", "bob", "carol"])
@pytest.mark.parametrize("direction", ["any", "inbound", "outbound"])
@pytest.mark.parametrize("tests", [False, True])
async def test_expand_equals_the_rule_on_the_stored_graph(world: _World, persona: str, direction: str, tests: bool) -> None:
    await _grant_all(world)
    stored = await _stored(world)
    assert {t for _, _, t in stored[0]} >= {"CALLS"}, "the edge builder wrote nothing"
    r = world.repo
    for path, names in SYMBOLS.items():
        for name in names:
            anchor = r.block(path, name)
            want_nodes, want_edges = _expected(persona, stored, [anchor], direction, tests)
            res = await world.expand(persona, [f"block:{anchor}"], direction=direction, includeTests=tests, limit=100)
            if not want_nodes:
                assert isinstance(res, GraphViewError) and res.status == 404, (persona, path, name)
                continue
            assert not isinstance(res, GraphViewError), (persona, path, name, res)
            assert _shown(res) == (want_nodes, want_edges), f"{world.repo.backend} {persona} {direction} {tests} {path}:{name}"


@pytest.mark.parametrize("persona", ["alice", "bob", "carol"])
async def test_revalidate_equals_the_rule(world: _World, persona: str) -> None:
    await _grant_all(world)
    r = world.repo
    ids = [f"block:{r.block(p, n)}" for p, names in SYMBOLS.items() for n in names] + [f"record:{rid}" for rid in r.record_ids.values()]
    ids += ["block:nope", "record:nope", "junk"]
    for tests in (False, True):
        owner = {f"block:{r.block(p, n)}": p for p, names in SYMBOLS.items() for n in names}
        # The records are asked about too: a record is no node, readable or not.
        want = [i for i in ids if i in owner and owner[i] in VISIBLE[persona] and (tests or ROLES.get(owner[i]) != "test")]
        assert await world.revalidate(persona, ids, includeTests=tests) == want


# -- noninterference ----------------------------------------------------------------------------------


async def test_a_hidden_file_and_its_edges_change_no_byte_of_the_answer(world: _World) -> None:
    await _grant_all(world)
    r = world.repo
    target = f"block:{r.block('src/shop/b.py', 'function:target')}"
    probes = [([target], {}), ([target], {"limit": 1}), ([target], {"direction": "inbound", "limit": 2}),
              ([f"block:{r.block('src/shop/a.py', 'function:caller')}", target], {})]
    before = [_bytes(await world.expand("bob", ids, **body)) for ids, body in probes]

    # Add a file bob cannot read: 31 functions that call target, one that target calls.
    path_rid = (await _write_repo(r.graph, r.org_id, r.connector_id, r.group_id, files={HIDDEN: HIDDEN_FILE}))
    r.record_ids.update(path_rid[0])
    r.blobs.records.update(path_rid[1].records)
    await _grant(r, world.keys["alice"], world.users["alice"], list(r.record_ids.values()))
    await _build(r)

    stored, _ = await _stored(world)
    assert any(r.block(HIDDEN, "function:hidden_fn") in (s, d) for s, d, _ in stored), "the hidden file's edges were not written"
    after = [_bytes(await world.expand("bob", ids, **body)) for ids, body in probes]
    for (_, body), old, new in zip(probes, before, after, strict=True):
        assert new == old, f"{body}\nbefore {old.decode()}\nafter  {new.decode()}"
    assert (await world.expand("alice", [target], direction="inbound", limit=100))["anchors"][0]["total"] > 30  # alice does see them
    hidden_anchor = await world.expand("bob", [f"block:{r.block(HIDDEN, 'function:hidden_fn')}"])
    missing_anchor = await world.expand("bob", ["block:does-not-exist"])
    assert _bytes(hidden_anchor) == _bytes(missing_anchor) and hidden_anchor.status == 404


# -- revocation ---------------------------------------------------------------------------------------


async def test_revoke_trash_grant_and_restore_take_effect_on_the_next_request(world: _World) -> None:
    await _grant_all(world)
    r = world.repo
    lib, caller = r.record_ids["src/shop/b.py"], r.record_ids["src/shop/a.py"]
    helper, target = f"block:{r.block('src/shop/b.py', 'function:helper')}", f"block:{r.block('src/shop/b.py', 'function:target')}"
    anchor = f"block:{r.block('src/shop/a.py', 'function:caller')}"
    assert target in {n["id"] for n in (await world.expand("bob", [anchor]))["nodes"]}

    # Trash the callee's file: it leaves the next response, the anchor's and a revalidation.
    await r.graph.update_node(lib, RECORDS, {"isDeleted": True})
    out = await world.expand("bob", [anchor])
    assert target not in {n["id"] for n in out["nodes"]}
    assert await world.revalidate("bob", [target, helper, anchor]) == [anchor]
    await r.graph.update_node(lib, RECORDS, {"isDeleted": False})
    assert await world.revalidate("bob", [target, helper, anchor]) == [target, helper, anchor]
    assert target in {n["id"] for n in (await world.expand("bob", [anchor]))["nodes"]}

    # Revoke the caller's file from bob: the anchor itself is now a 404, then back once granted.
    await r.graph.delete_nodes_and_edges([world.keys["bob"]], CollectionNames.USERS.value)
    gone = await world.expand("bob", [anchor])
    assert isinstance(gone, GraphViewError) and gone.status == 404
    await _grant(r, world.keys["bob"], world.users["bob"], [lib, caller])
    assert not isinstance(await world.expand("bob", [anchor]), GraphViewError)


async def test_a_search_hit_resolves_to_the_node_expand_takes(world: _World) -> None:
    await _grant_all(world)
    r = world.repo
    service, policy, access = world.service("bob")
    want = f"block:{r.block('src/shop/b.py', 'function:helper')}"
    for req in (ResolveRequest(source="code", repo="acme/shop", filePath="src/shop/b.py", qualifiedName="function:helper"),
                ResolveRequest(source="code", recordId=r.record_ids["src/shop/b.py"], qualifiedName="function:helper")):
        assert await service.resolve(req, r.connector_id, r.group_id, access=access, policy=policy) == {"nodeId": want}
    with pytest.raises(GraphViewError) as gone:
        await service.resolve(ResolveRequest(source="code", recordId="does-not-exist"), r.connector_id, r.group_id,
                              access=access, policy=policy)
    assert gone.value.status == 404

"""Numbered pages of a wide folder under the split browse, on a real Neo4j: what a page costs follows the page, not its number.

The service reads the rows before page N (`KnowledgeHubService._walk_pages`) and then the page. Each read used to decide the whole
folder's listing again with the batch check, so page 50 cost about fifty page 1s. Those reads now decide the listing once between them
and read 1,000 rows at a time; the page itself decides afresh. The cost is counted in statements and listing decisions, not time, so
the bound holds on any runner. Both browse modes must list the same rows, arrows and totals.

Runs in backend-matrix on the Neo4j graph job. Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import uuid
from typing import TYPE_CHECKING, Any

import pytest

from app.connectors.sources.localKB.handlers.knowledge_hub_service import (
    KnowledgeHubService,
)
from tests.integration.graph_permissions.loaders import EDGE_TARGETS, NODE_TARGETS
from tests.integration.real_graph import backend_unavailable, connect_neo4j
from tests.permissions.graph import Namespace, compile_world
from tests.permissions.scenario import world_from_data

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module"), pytest.mark.timeout(900)]

logger = logging.getLogger("kh-split-numbered-pages-it")
USERS = ["u0", "u1", "u2", "u3"]
FILES = 1_400
LIMIT = 25      # page 50 is rows 1,226-1,250: two reads before it, the second resuming the first
CHAINS = (49, 50)
DIAMONDS = 23   # Dx is 2 + 2 * 23 + 1 = 49 hops below App D, within the walks' cap of 50


def _world() -> Any:  # noqa: ANN401
    """A folder F of 1,400 files under a group R: u0 and u1 reach it by a group grant on R, u2 and u3 by their own grant on F (a seed).
    Every tenth file has its own sharing (STRICT, no inheritance), granted to u0 and u2 or to u1 and u3; F also holds ten sub-folders
    with two files each, four stubs whose STRICT child is granted to one pair, and a group that hides its children."""
    nodes: list[dict] = [{"id": "A", "kind": "app", "app_type": "DRIVE"},
                         {"id": "R", "kind": "group", "connector": "A", "rule": "OPEN", "parents": ["A"], "belongs": ["A"]},
                         {"id": "F", "kind": "group", "connector": "A", "rule": "OPEN", "parents": ["R"], "inherits": ["R"], "belongs": ["R"]}]
    grants = [["gAll", "R", "GROUP"], *([u, "F"] for u in USERS[2:])]
    pairs = (["u0", "u2"], ["u1", "u3"])
    inherit = {"connector": "A", "rule": "OPEN", "parents": ["F"], "inherits": ["F"], "belongs": ["F"]}
    for i in range(FILES):
        fid = f"Fx{i:05d}"
        if i % 10 == 0:
            nodes.append({"id": fid, "kind": "record", "connector": "A", "rule": "STRICT", "parents": ["F"], "belongs": ["F"]})
            grants += [[u, fid] for u in pairs[(i // 10) % 2]]
        else:
            nodes.append({"id": fid, "kind": "record", **inherit})
    for j in range(10):
        sid = f"Fs{j:02d}"
        nodes.append({"id": sid, "kind": "record", **inherit})
        nodes += [{"id": f"{sid}c{k}", "kind": "record", "connector": "A", "rule": "OPEN", "parents": [sid], "inherits": [sid],
                   "belongs": ["F"]} for k in range(2)]
    for j in range(4):
        pid = f"Fp{j:02d}"
        nodes.append({"id": pid, "kind": "record", "placeholder": True, **inherit})
        nodes.append({"id": f"{pid}c", "kind": "record", "connector": "A", "rule": "STRICT", "parents": [pid], "belongs": ["F"]})
        grants += [[u, f"{pid}c"] for u in pairs[j % 2]]
    nodes.append({"id": "Fh", "kind": "group", "hide_children": True, **inherit})
    nodes += [{"id": f"Fhr{k}", "kind": "record", "connector": "A", "rule": "OPEN", "parents": ["Fh"], "inherits": ["Fh"], "belongs": ["Fh"]}
              for k in range(2)]
    # Under App B, two chains of groups that inherit down from a top u0 is granted: C49 and C50 end in a start 49 and 50 hops below the App,
    # each with one record below it. The check's walk from the App goes 50 hops, so only C49's record is admitted by it.
    nodes.append({"id": "B", "kind": "app", "app_type": "DRIVE"})
    for depth in CHAINS:
        top = f"C{depth}n01"
        nodes.append({"id": top, "kind": "group", "connector": "B", "rule": "OPEN", "parents": ["B"], "belongs": ["B"]})
        grants.append(["u0", top])
        for i in range(2, depth + 1):
            above = f"C{depth}n{i - 1:02d}"
            nodes.append({"id": f"C{depth}n{i:02d}", "kind": "group", "connector": "B", "rule": "OPEN", "parents": [above], "inherits": [above],
                          "belongs": [above]})
        start = f"C{depth}n{depth:02d}"
        nodes.append({"id": f"C{depth}x", "kind": "record", "connector": "B", "rule": "OPEN", "parents": [start], "inherits": [start],
                      "belongs": [start]})
    # The same below a seed: S49 and S50 hang under a group G that u2 cannot read, from a top u2 is granted (a seed), so the walk below the
    # seed (50 hops at most) admits only S49's record.
    nodes.append({"id": "G", "kind": "group", "connector": "B", "rule": "OPEN", "parents": ["B"], "belongs": ["B"]})
    for depth in CHAINS:
        top = f"S{depth}n00"
        nodes.append({"id": top, "kind": "group", "connector": "B", "rule": "OPEN", "parents": ["G"], "belongs": ["G"]})
        grants.append(["u2", top])
        for i in range(1, depth + 1):
            above = f"S{depth}n{i - 1:02d}"
            nodes.append({"id": f"S{depth}n{i:02d}", "kind": "group", "connector": "B", "rule": "OPEN", "parents": [above], "inherits": [above],
                          "belongs": [above]})
        start = f"S{depth}n{depth:02d}"
        nodes.append({"id": f"S{depth}x", "kind": "record", "connector": "B", "rule": "OPEN", "parents": [start], "inherits": [start],
                      "belongs": [start]})
    # Under App D, a chain of DIAMONDS diamonds below a group G: every other level a node with two parents, so 2^DIAMONDS routes lead
    # from its bottom Dx up to its top D00. u0 is granted G (the walk from the App admits), u2 the top (a seed: G is not readable to u2).
    nodes += [{"id": "D", "kind": "app", "app_type": "DRIVE"},
              {"id": "DG", "kind": "group", "connector": "D", "rule": "OPEN", "parents": ["D"], "belongs": ["D"]},
              {"id": "D00", "kind": "group", "connector": "D", "rule": "OPEN", "parents": ["DG"], "inherits": ["DG"], "belongs": ["DG"]}]
    grants += [["u0", "DG"], ["u2", "D00"]]
    for i in range(1, DIAMONDS + 1):
        above = f"D{i - 1:02d}"
        sides = [f"D{i:02d}l", f"D{i:02d}r"]
        nodes += [{"id": x, "kind": "group", "connector": "D", "rule": "OPEN", "parents": [above], "inherits": [above], "belongs": [above]}
                  for x in sides]
        nodes.append({"id": f"D{i:02d}", "kind": "group", "connector": "D", "rule": "OPEN", "parents": sides, "inherits": sides,
                      "belongs": sides})
    nodes.append({"id": "Dx", "kind": "record", "connector": "D", "rule": "OPEN", "parents": [f"D{DIAMONDS:02d}"],
                  "inherits": [f"D{DIAMONDS:02d}"], "belongs": [f"D{DIAMONDS:02d}"]})
    return world_from_data({
        "principals": [{"id": u} for u in USERS] + [{"id": "gAll", "kind": "group"}],
        "nodes": nodes, "grants": grants, "memberships": [[u, "gAll"] for u in USERS[:2]],
        "gates": [[u, app] for u in USERS for app in ("A", "B", "D")],
    })


class _Config:
    async def get_config(self, *_a: Any, **_k: Any) -> dict:  # noqa: ANN401
        return {"scopedJwtSecret": "kh-split-numbered-pages-it"}


class _Hub:
    """The world loaded under its own prefix, the product's browse service over it, and a count of what each request asked."""

    def __init__(self, graph: Any, ns: Namespace) -> None:  # noqa: ANN401
        self.graph, self.ns = graph, ns
        self.service = KnowledgeHubService(logger, graph, _Config())
        self._log: contextvars.ContextVar[dict | None] = contextvars.ContextVar("kh_it_request", default=None)
        execute, listed = graph.client.execute_query, graph._kh_split_listed

        async def counted_execute(*a: Any, **k: Any) -> Any:  # noqa: ANN401
            if (log := self._log.get()) is not None:
                log["statements"] += 1
            return await execute(*a, **k)

        async def counted_listed(*a: Any, **k: Any) -> Any:  # noqa: ANN401
            if (log := self._log.get()) is not None:
                log["decisions"] += 1
            return await listed(*a, **k)

        graph.client.execute_query, graph._kh_split_listed = counted_execute, counted_listed

    async def browse(self, user: str, *, page: int = 1, cursor: str | None = None, sidebar: bool = False) -> dict:
        log = {"statements": 0, "decisions": 0}
        token = self._log.set(log)
        try:
            got = await self.service.get_nodes(
                user_id=self.ns.id(user), org_id=self.ns.org, parent_id=self.ns.id("F"), parent_type="recordGroup", page=page,
                limit=LIMIT, sort_by="name", sort_order="asc", only_containers=sidebar, cursor=cursor)
        finally:
            self._log.reset(token)
        assert got.success, (got.errorCode, got.error)
        return {"ids": [self.ns.local(i.id) for i in got.items], "arrows": [bool(i.hasChildren) for i in got.items],
                "total": got.pagination.totalItems, "next": got.pagination.nextCursor, **log}


@pytest.fixture(scope="module")
async def hub() -> AsyncIterator[_Hub]:
    with pytest.MonkeyPatch.context() as mp:
        try:
            graph = await connect_neo4j(logger, mp)
        except Exception as exc:
            backend_unavailable("neo4j", exc)
        prefix = f"khsplit{uuid.uuid4().hex[:8]}"
        compiled = compile_world(_world(), prefix)
        kind_of = {n["id"]: n["kind"] for n in compiled["nodes"]}
        try:
            for kind, collection in NODE_TARGETS.items():
                rows = [n["props"] for n in compiled["nodes"] if n["kind"] == kind]
                if rows:
                    assert await graph.batch_upsert_nodes(rows, collection=collection)
            for edge_type, collection in EDGE_TARGETS.items():
                rows = [{"from_id": e["from"], "from_collection": NODE_TARGETS[kind_of[e["from"]]],
                         "to_id": e["to"], "to_collection": NODE_TARGETS[kind_of[e["to"]]], **e["props"]}
                        for e in compiled["edges"] if e["type"] == edge_type]
                if rows:
                    assert await graph.batch_create_edges(rows, collection=collection)
            yield _Hub(graph, Namespace(prefix))
        finally:
            await graph.client.execute_query("MATCH (n) WHERE n.id STARTS WITH $p DETACH DELETE n", parameters={"p": f"{prefix}-"})
            await graph.client.execute_query("MATCH (n:Organization {id: $o}) DETACH DELETE n", parameters={"o": Namespace(prefix).org_node})
            await graph.disconnect()


async def test_a_numbered_page_costs_about_what_the_first_page_costs(hub: _Hub, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KH_BROWSE_MODE", "split")
    first = await hub.browse("u0")
    assert len(first["ids"]) == LIMIT and first["decisions"] == 1
    for page in (25, 50):
        deep = await hub.browse("u0", page=page)
        assert len(deep["ids"]) == LIMIT
        # The reads before the page decide the listing once between them, and the page decides it again: never one decision per page.
        assert deep["decisions"] == 2, (page, deep["decisions"])
        assert deep["statements"] <= 3 * first["statements"], (page, deep["statements"], first["statements"])


async def test_both_browse_modes_list_the_same_pages(hub: _Hub, monkeypatch: pytest.MonkeyPatch) -> None:
    for user in ("u0", "u2"):
        pages: dict[str, list] = {}
        for mode in ("legacy", "split"):
            monkeypatch.setenv("KH_BROWSE_MODE", mode)
            first = await hub.browse(user)
            shown = [first, await hub.browse(user, cursor=first["next"]), await hub.browse(user, page=25),
                     await hub.browse(user, page=50), await hub.browse(user, sidebar=True)]
            pages[mode] = [(p["ids"], p["arrows"], p["total"]) for p in shown]
        assert pages["split"] == pages["legacy"], user
        assert all(len(ids) == LIMIT for ids, _, _ in pages["split"][:4])


async def test_concurrent_viewers_are_each_answered_from_their_own_access(hub: _Hub, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KH_BROWSE_MODE", "split")
    alone = {u: (await hub.browse(u, page=50))["ids"] for u in USERS}
    together = await asyncio.gather(*(hub.browse(u, page=50) for u in USERS))
    assert {u: p["ids"] for u, p in zip(USERS, together, strict=True)} == alone
    # The pairs hold different files of their own sharing, so a page served from another viewer's decision would show.
    assert alone["u0"] != alone["u1"]


async def test_a_grant_removed_while_a_numbered_page_is_read_is_not_listed(hub: _Hub, monkeypatch: pytest.MonkeyPatch) -> None:
    """The reads before the page share a decision; the page does not reuse it, so access taken away meanwhile is not shown."""
    monkeypatch.setenv("KH_BROWSE_MODE", "split")
    before = (await hub.browse("u0", page=50))["ids"]
    target = next(i for i in before if i.startswith("Fx") and int(i[2:]) % 10 == 0)      # a file only its own grant opens
    user, record = hub.ns.id("u0"), hub.ns.id(target)
    page_v3 = hub.graph.get_knowledge_hub_connector_page_v3
    revoked: list[bool] = []

    async def revoke_before_the_page(*a: Any, **k: Any) -> Any:  # noqa: ANN401
        if k.get("limit") == LIMIT and not revoked:      # the page itself: the reads before it take 1,000 rows, then the 225 left
            await hub.graph.client.execute_query("MATCH (:User {id: $u})-[p:PERMISSION]->(:Record {id: $r}) DELETE p",
                                                 parameters={"u": user, "r": record})
            revoked.append(True)
        return await page_v3(*a, **k)

    monkeypatch.setattr(hub.graph, "get_knowledge_hub_connector_page_v3", revoke_before_the_page)
    try:
        after = (await hub.browse("u0", page=50))["ids"]
    finally:
        await hub.graph.client.execute_query(
            "MATCH (u:User {id: $u}), (r:Record {id: $r}) CREATE (u)-[:PERMISSION {type: 'USER', role: 'READER', createdAtTimestamp: 0}]->(r)",
            parameters={"u": user, "r": record})
    assert revoked == [True]
    assert target not in after
    assert set(after) - set(before) <= {i for i in after if i > before[-1]}, "only the next row moves up into the page"


async def test_the_children_their_hop_admits_are_exactly_those_the_check_admits_by_its_walk(hub: _Hub, monkeypatch: pytest.MonkeyPatch) -> None:
    """The split browse admits a child that passes its own hop under a start the walk from the App reaches, and asks the check about the rest
    alone. Held to the check itself, for a viewer the walk reaches the folder for (u0, by the group grant on R) and one it does not (u2, a grant
    on F alone), in both forms the check reads grants in: from a list, and from the nodes (as a browse below the App does)."""
    monkeypatch.setenv("KH_BROWSE_MODE", "split")
    graph, folder = hub.graph, hub.ns.id("F")
    seen: list[dict] = []
    listed = graph._kh_split_listed

    async def keep_access(*a: Any, **k: Any) -> Any:  # noqa: ANN401
        seen.append(k["access"])
        return await listed(*a, **k)

    monkeypatch.setattr(graph, "_kh_split_listed", keep_access)
    kids = [k["id"] for k in (await graph._kh_split_children([folder], app_id=hub.ns.id("A"), org_id=hub.ns.org, transaction=None))[folder]
            if not k["stub"]]
    for user, reached in (("u0", True), ("u2", False)):
        seen.clear()
        await hub.browse(user)
        listing = await graph.get_knowledge_hub_access_v3(user_key=hub.ns.id(user), org_id=hub.ns.org)
        for access in (seen[0], {**listing, "probe": None}):
            by_hop = await graph._kh_split_admitted_by_hop(folder, kids, app_id=hub.ns.id("A"), org_id=hub.ns.org, access=access, transaction=None)
            every = await graph._kh_v3_admitted(kids, org_id=hub.ns.org, access=access, transaction=None)
            rest = await graph._kh_v3_admitted([i for i in kids if i not in by_hop], org_id=hub.ns.org, access=access, transaction=None)
            assert by_hop <= every
            assert by_hop | rest == every
            # Every child that inherits from F, and every file of its own sharing granted to the viewer, passes its hop: only the files shared
            # with the other pair are left for the check.
            assert len(by_hop) == (len(kids) - FILES // 20 if reached else 0), (user, bool(access.get("probe")))


async def test_the_batch_check_is_asked_only_about_the_children_their_hop_does_not_admit(hub: _Hub, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KH_BROWSE_MODE", "split")
    asked: list[int] = []
    check = hub.graph.check_access

    async def count_asked(*a: Any, **k: Any) -> Any:  # noqa: ANN401
        asked.append(len(set(k.get("node_ids") or ())))
        return await check(*a, **k)

    monkeypatch.setattr(hub.graph, "check_access", count_asked)
    await hub.browse("u0")
    # The folder holds 1,415 children; the check is asked only about the 70 files shared with the other pair, which no hop admits.
    assert max(asked) == FILES // 20, asked


@pytest.mark.parametrize(("depth", "admitted"), [(49, True), (50, False)])
async def test_a_childs_hop_admits_it_only_where_its_route_from_the_app_stays_within_the_checks_cap(
    hub: _Hub, monkeypatch: pytest.MonkeyPatch, depth: int, admitted: bool,
) -> None:
    """The check walks at most 50 hops up to the App, so the hop admits a child only below a start the walk reaches within 49. Below a start
    50 hops down, the child's only route is 51 hops: the hop leaves it to the check, whose answer (no) is what the browse lists."""
    monkeypatch.setenv("KH_BROWSE_MODE", "split")
    graph, app, start, child = hub.graph, hub.ns.id("B"), hub.ns.id(f"C{depth}n{depth:02d}"), hub.ns.id(f"C{depth}x")
    seen: list[dict] = []
    listed = graph._kh_split_listed

    async def keep_access(*a: Any, **k: Any) -> Any:  # noqa: ANN401
        seen.append(k["access"])
        return await listed(*a, **k)

    monkeypatch.setattr(graph, "_kh_split_listed", keep_access)
    got = await hub.service.get_nodes(user_id=hub.ns.id("u0"), org_id=hub.ns.org, parent_id=start, parent_type="recordGroup", limit=LIMIT,
                                      sort_by="name", sort_order="asc")
    assert got.success, (got.errorCode, got.error)      # the walk reaches the start itself in both chains
    listing = await graph.get_knowledge_hub_access_v3(user_key=hub.ns.id("u0"), org_id=hub.ns.org)
    for access in (seen[0], {**listing, "probe": None}):
        by_hop = await graph._kh_split_admitted_by_hop(start, [child], app_id=app, org_id=hub.ns.org, access=access, transaction=None)
        assert by_hop == ({child} if admitted else frozenset()), (depth, bool(access.get("probe")))
        assert await graph._kh_v3_admitted([child], org_id=hub.ns.org, access=access, transaction=None) == by_hop
    assert [i.id for i in got.items] == ([child] if admitted else [])


async def _asked(hub: _Hub, ids: list[str], access: dict, *, by_parent: bool = True) -> tuple[frozenset[str], list[str], list[str]]:
    """What the check admits of ``ids``, what its by-parent statement admits, and what its full statement is asked."""
    graph, query = hub.graph, hub.graph._kh_query
    sent: list[tuple[str, dict, list[dict]]] = []

    async def keep(q: str, parameters: dict | None = None, txn_id: str | None = None) -> list[dict]:
        rows = await query(q, parameters=parameters, txn_id=txn_id)
        sent.append((q, parameters or {}, rows))
        return rows

    with pytest.MonkeyPatch.context() as m:
        m.setattr(graph, "_kh_query", keep)
        if not by_parent:
            m.setattr(graph, "_KH_CHECK_BY_PARENT_MIN_IDS", 10**9)
        got = await graph._kh_v3_admitted(ids, org_id=hub.ns.org, access=access, transaction=None)
    pre = [r["id"] for q, _, rows in sent if "kh_kids" in q for r in (rows or [{}])[0].get("items") or []]
    full = [i for q, p, _ in sent if "kh_kids" not in q for i in p["kh_ids"]]
    return got, pre, full


async def _accesses(hub: _Hub, user: str, start: str, monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """The access a browse of ``start`` hands the check (grants read from the nodes), and the same with grants read from a list."""
    seen: list[dict] = []
    graph, listed = hub.graph, hub.graph._kh_split_listed

    async def keep_access(*a: Any, **k: Any) -> Any:  # noqa: ANN401
        seen.append(k["access"])
        return await listed(*a, **k)

    with monkeypatch.context() as m:
        m.setattr(graph, "_kh_split_listed", keep_access)
        got = await hub.service.get_nodes(user_id=hub.ns.id(user), org_id=hub.ns.org, parent_id=start, parent_type="recordGroup", limit=LIMIT,
                                          sort_by="name", sort_order="asc")
    assert got.success, (got.errorCode, got.error)
    listing = await graph.get_knowledge_hub_access_v3(user_key=hub.ns.id(user), org_id=hub.ns.org)
    return [seen[0], {**listing, "probe": None}]


async def test_the_check_admits_by_parent_exactly_what_it_admits_by_every_arm(hub: _Hub, monkeypatch: pytest.MonkeyPatch) -> None:
    """The check decides the walk above a parent once for all the asked children and admits each by its own hop; the rest go to the full
    statement. Held to the full statement alone, for a viewer the walk from the App reaches the folder for (u0, by the group grant on R) and
    one it reaches only below a seed (u2, a grant on F alone), in both forms the check reads grants in."""
    monkeypatch.setenv("KH_BROWSE_MODE", "split")
    graph, folder = hub.graph, hub.ns.id("F")
    kids = [k["id"] for k in (await graph._kh_split_children([folder], app_id=hub.ns.id("A"), org_id=hub.ns.org, transaction=None))[folder]]
    for user, left in (("u0", FILES // 20), ("u2", FILES // 10)):
        for access in await _accesses(hub, user, folder, monkeypatch):
            every, _, full = await _asked(hub, kids, access, by_parent=False)
            assert len(full) == len(kids)
            got, pre, full = await _asked(hub, kids, access)
            assert got == every, (user, bool(access.get("probe")))
            assert set(pre) <= every and sorted(full) == sorted(set(kids) - set(pre))
            # u0: every child that inherits from F and every file of its own sharing granted to u0 passes its hop, and only the files shared
            # with the other pair are left. u2: below the seed F only the seed hop passes, so every file of its own sharing is left.
            assert len(full) == left, (user, bool(access.get("probe")), len(full))


async def test_a_browse_asks_the_full_check_only_about_the_children_no_parent_admits(hub: _Hub, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KH_BROWSE_MODE", "split")
    asked: list[int] = []
    query = hub.graph._kh_query

    async def count_asked(q: str, parameters: dict | None = None, txn_id: str | None = None) -> list[dict]:
        if "kh_vrids" in (parameters or {}) and "kh_kids" not in q:
            asked.append(len(parameters["kh_ids"]))
        return await query(q, parameters=parameters, txn_id=txn_id)

    monkeypatch.setattr(hub.graph, "_kh_query", count_asked)
    for user, left in (("u0", FILES // 20), ("u2", FILES // 10)):
        asked.clear()
        await hub.browse(user)
        # The folder holds 1,415 children; the full statement is asked only about the files of their own sharing no hop admits.
        assert max(asked) == left, (user, asked)


@pytest.mark.parametrize(("user", "chain"), [("u0", "C"), ("u2", "S")])
async def test_the_check_admits_by_parent_only_where_the_route_stays_within_its_cap(
    hub: _Hub, monkeypatch: pytest.MonkeyPatch, user: str, chain: str,
) -> None:
    """The check walks at most 50 hops, up to the App (u0, chains C) or to a seed (u2, chains S), so a parent's walk admits a child only
    within 49 more. Below a start 50 hops down the child's only route is 51 hops: it is left to the full statement, which refuses it."""
    monkeypatch.setenv("KH_BROWSE_MODE", "split")
    starts = {depth: hub.ns.id(f"{chain}{depth}n{depth:02d}") for depth in CHAINS}
    child = {depth: hub.ns.id(f"{chain}{depth}x") for depth in CHAINS}
    for access in await _accesses(hub, user, starts[49], monkeypatch):
        got, pre, full = await _asked(hub, [child[49], child[50]], access)
        assert pre == [child[49]] and full == [child[50]], (pre, full, bool(access.get("probe")))
        assert got == {child[49]}
        assert got == (await _asked(hub, [child[49], child[50]], access, by_parent=False))[0]
    for depth, admitted in ((49, True), (50, False)):
        got = await hub.service.get_nodes(user_id=hub.ns.id(user), org_id=hub.ns.org, parent_id=starts[depth], parent_type="recordGroup",
                                          limit=LIMIT, sort_by="name", sort_order="asc")
        assert got.success, (got.errorCode, got.error)      # the walk reaches the start itself in both chains
        assert [i.id for i in got.items] == ([child[depth]] if admitted else []), depth


@pytest.mark.parametrize("probe", [False, True], ids=["grant_list", "grants_from_nodes"])
async def test_the_check_reads_a_hierarchy_of_many_two_parent_levels_once_per_node(hub: _Hub, probe: bool) -> None:
    """Below 23 levels of two parents there are 2^23 routes to the top. The check reads each node above the target once, so it answers
    at once, and the same as before: admitted by the walk from the App (u0), by the walk below a seed (u2), refused (u1)."""
    graph = hub.graph
    asked = [hub.ns.id("Dx"), hub.ns.id("D12")]
    for user, admitted in (("u0", set(asked)), ("u2", set(asked)), ("u1", set())):
        access = await graph.get_knowledge_hub_access_v3(user_key=hub.ns.id(user), org_id=hub.ns.org)
        if probe:
            access = {**access, "probe": {"connector_id": hub.ns.id("D"), "grantees": access["grantee_ids"]}}
        got = await asyncio.wait_for(graph._kh_v3_admitted(asked, org_id=hub.ns.org, access=access, transaction=None), timeout=60)
        assert got == admitted, (user, sorted(hub.ns.local(i) for i in got))

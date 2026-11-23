"""What a Knowledge Hub browse costs on a real Neo4j, held to budgets counted in work, not time, so they hold on any runner.

For each browse of a fixed world (the numbered-pages world: a 1,415-child folder, chains to the walks' cap, 23 levels of two parents),
in both browse modes and for a viewer the walk from the App reaches and one reached only through a grant on the folder:

- statements: every statement the request sends;
- check ids: the most node ids one batch check (`check_access`) is asked about;
- db hits: the sum of each statement's db hits, profiled on its own after the request, on plans made from the world's resampled statistics
  (planned on stale index samples, the same statements read up to 100 times as much).

A change that sends more statements per page, asks the check about the whole folder, or makes a statement read far more of the graph
fails here before it reaches a deployment. A deliberate change to the cost updates BUDGETS; the failure prints what it measured.

Runs in permission-conformance.yml (job kh-browse-cost). Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

import pytest

from tests.integration.graph_db.test_kh_split_numbered_pages_real_neo4j import (
    LIMIT,
    _Hub,
)
from tests.integration.kh_browse_cost.statement_trace import forget_plans

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module"), pytest.mark.timeout(900)]

# Room above what main measured: a request's statement count can differ by one between runs, and db hits by about 0.1 %.
COUNT_MARGIN = 1.25
DB_HITS_MARGIN = 1.5

# (statements, check ids, db hits) measured on main; a budget is these times the margins above.
BUDGETS: dict[str, tuple[int, int, int]] = {
    "split page 1 u0": (25, 70, 134669),
    "split page 1 u2": (26, 1411, 167180),
    "split cursor u0": (22, 70, 129405),
    "split cursor u2": (24, 1411, 161915),
    "split page 50 u0": (49, 70, 383113),
    "split page 50 u2": (54, 1411, 446784),
    "split sidebar u0": (27, 70, 153653),
    "split sidebar u2": (29, 1411, 184867),
    "split flatten u0": (10, 10, 64109),
    "split flatten u2": (12, 9, 88161),
    "legacy page 1 u0": (18, 70, 240186),
    "legacy page 1 u2": (21, 140, 313948),
    "legacy cursor u0": (13, 69, 125794),
    "legacy cursor u2": (14, 138, 167887),
    "legacy page 50 u0": (557, 70, 6129324),
    "legacy page 50 u2": (608, 140, 7942250),
    "legacy sidebar u0": (19, 70, 569787),
    "legacy sidebar u2": (21, 140, 601516),
    "legacy flatten u0": (10, 10, 64109),
    "legacy flatten u2": (12, 9, 88161),
    "check diamonds u0": (1, 2, 9097),
    "check diamonds u2": (1, 2, 9223),
    "check diamonds u1": (2, 2, 96066),
}

KINDS = {
    "page 1": {},
    "cursor": {"cursor": True},
    "page 50": {"page": 50},
    "sidebar": {"only_containers": True},
    "flatten": {"flattened": True},
}


class _Sent:
    """Every statement and batch check a request sends, for the cost of one request."""

    def __init__(self, hub: _Hub, monkeypatch: pytest.MonkeyPatch) -> None:
        self.statements: list[tuple[str, dict]] = []
        self.asked: list[int] = []
        execute, check = hub.graph.client.execute_query, hub.graph.check_access

        async def keep(query: str, parameters: dict | None = None, *a: Any, **k: Any) -> Any:  # noqa: ANN401
            self.statements.append((query, parameters or {}))
            return await execute(query, parameters, *a, **k)

        async def count(*a: Any, **k: Any) -> Any:  # noqa: ANN401
            self.asked.append(len(set(k.get("node_ids") or ())))
            return await check(*a, **k)

        monkeypatch.setattr(hub.graph.client, "execute_query", keep)
        monkeypatch.setattr(hub.graph, "check_access", count)


def _db_hits(plan: dict) -> int:
    return plan.get("dbHits", 0) + sum(_db_hits(c) for c in plan.get("children", []))


async def _cost(hub: _Hub, monkeypatch: pytest.MonkeyPatch, request: Callable[[], Awaitable[Any]]) -> tuple[tuple[int, int, int], str]:
    """(statements, check ids, db hits) of the request, and its heaviest statements."""
    with monkeypatch.context() as m:
        sent = _Sent(hub, m)
        await request()
    hits: list[tuple[int, str]] = []
    async with hub.graph.client.driver.session(database=hub.graph.client.database) as session:
        for query, parameters in sent.statements:
            profiled = await (await session.run("PROFILE " + query, parameters)).to_eager_result()
            hits.append((_db_hits(profiled.summary.profile), " ".join(query.split())[:160]))
    heaviest = "\n".join(f"  {h:>10} db hits: {q}" for h, q in sorted(hits, reverse=True)[:3])
    return (len(sent.statements), max(sent.asked, default=0), sum(h for h, _ in hits)), heaviest


def _within(name: str, got: tuple[int, int, int], heaviest: str) -> None:
    print(f"KH-COST {name!r}: {got},")  # the line to paste into BUDGETS
    budget = BUDGETS[name]
    limits = (math.ceil(budget[0] * COUNT_MARGIN), math.ceil(budget[1] * COUNT_MARGIN), math.ceil(budget[2] * DB_HITS_MARGIN))
    over = [f"{what} {g} > {limit} (main {b})" for what, g, limit, b in zip(("statements", "check ids", "db hits"), got, limits, budget,
                                                                            strict=True) if g > limit]
    assert not over, f"{name}: {', '.join(over)}. If the new cost is intended, set BUDGETS[{name!r}] = {got}.\nHeaviest:\n{heaviest}"


@pytest.fixture(scope="module", autouse=True)
async def _fresh_plans(hub: _Hub) -> AsyncIterator[None]:
    # Plans made on statistics that include the whole world (index samples are otherwise refreshed in the background, whenever), whatever
    # ran before; and none of its plans left to the tests after it.
    await hub.graph.client.execute_query("CALL db.prepareForReplanning()")
    forget_plans()
    yield
    await hub.graph.client.execute_query("CALL db.clearQueryCaches()")
    forget_plans()


@pytest.mark.parametrize("user", ["u0", "u2"], ids=["reached_by_the_walk", "reached_by_a_seed"])
@pytest.mark.parametrize("kind", list(KINDS))
@pytest.mark.parametrize("mode", ["split", "legacy"])
async def test_a_browse_stays_within_its_budget(hub: _Hub, monkeypatch: pytest.MonkeyPatch, mode: str, kind: str, user: str) -> None:
    monkeypatch.setenv("KH_BROWSE_MODE", mode)
    args = dict(KINDS[kind])

    async def browse(**more: Any) -> Any:  # noqa: ANN401
        got = await hub.service.get_nodes(user_id=hub.ns.id(user), org_id=hub.ns.org, parent_id=hub.ns.id("F"), parent_type="recordGroup",
                                          limit=LIMIT, sort_by="name", sort_order="asc", **more)
        assert got.success, (got.errorCode, got.error)
        assert got.items, "the browse lists something"
        return got

    if args.pop("cursor", False):
        args["cursor"] = (await browse()).pagination.nextCursor
        assert args["cursor"]
    _within(f"{mode} {kind} {user}", *await _cost(hub, monkeypatch, lambda: browse(**args)))


@pytest.mark.parametrize("user", ["u0", "u2", "u1"], ids=["admitted_by_the_walk", "admitted_below_a_seed", "refused"])
async def test_the_check_over_many_two_parent_levels_stays_within_its_budget(hub: _Hub, monkeypatch: pytest.MonkeyPatch, user: str) -> None:
    """Below 23 levels of two parents there are 2^23 routes to the top: a walk that reads routes rather than nodes is caught here
    long before it runs out of memory."""
    graph = hub.graph
    access = await graph.get_knowledge_hub_access_v3(user_key=hub.ns.id(user), org_id=hub.ns.org)
    asked = [hub.ns.id("Dx"), hub.ns.id("D12")]
    _within(f"check diamonds {user}", *await _cost(
        hub, monkeypatch, lambda: graph._kh_v3_admitted(asked, org_id=hub.ns.org, access=access, transaction=None)))

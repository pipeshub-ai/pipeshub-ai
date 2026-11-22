"""The plan keeper plans the listing statements in use again before their cached plans age into Neo4j's stale range.

A statement's plan is made again (``EXPLAIN``, ``replan=force``) only once it is older than the refresh age and a request
has run the statement since; a statement idle for a day is forgotten. Lists are not kept, only their type.
"""

import logging

import pytest

from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


class Client:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def execute_query(self, query, parameters=None, txn_id=None, **kw) -> list[dict]:
        self.calls.append({"query": query, "parameters": parameters, **kw})
        return [{"ok": True}]


def provider() -> tuple[Neo4jProvider, Client]:
    p = object.__new__(Neo4jProvider)
    p.logger = logging.getLogger("test_kh_plan_refresh")
    p.client = Client()
    return p, p.client


def age(p: Neo4jProvider, seconds: float) -> None:
    """Every statement planned and last run ``seconds`` earlier than recorded."""
    for entry in p._kh_statements().values():
        entry[2] -= seconds
        entry[3] -= seconds


QUERY = "MATCH (a:App {id: $app_id}) WHERE a.id IN $gated RETURN a"


@pytest.mark.asyncio
async def test_a_statement_in_use_is_planned_again_once_its_plan_is_old() -> None:
    p, client = provider()
    await p._kh_query(QUERY, {"app_id": "a1", "gated": ["a1", "a2"]})
    assert await p.kh_refresh_plans(max_age=3600) == 0  # young
    age(p, 7200)
    assert await p.kh_refresh_plans(max_age=3600) == 0  # old, but no request has run it since it was planned
    await p._kh_query(QUERY, {"app_id": "a2", "gated": ["a2"]})
    client.calls.clear()
    assert await p.kh_refresh_plans(max_age=3600) == 1
    [call] = client.calls
    assert call["query"].startswith("CYPHER replan=force EXPLAIN MATCH (a:App")
    # The lists travel in one map (`_kh_query`), kept empty: the plan cache keys them by type only.
    assert call["parameters"] == {"app_id": "a1", p._KH_LISTS: {"gated": []}}
    assert await p.kh_refresh_plans(max_age=3600) == 0  # just planned


@pytest.mark.asyncio
async def test_an_idle_statement_is_forgotten_and_zero_turns_it_off() -> None:
    p, client = provider()
    await p._kh_query(QUERY, {"app_id": "a1", "gated": []})
    assert await p.kh_refresh_plans(max_age=0) == 0
    age(p, p._KH_PLAN_IDLE_S + 1)
    assert await p.kh_refresh_plans(max_age=3600) == 0
    assert p._kh_statements() == {}


@pytest.mark.asyncio
async def test_a_failed_refresh_leaves_the_statement_for_the_next_run() -> None:
    p, client = provider()
    await p._kh_query(QUERY, {"app_id": "a1", "gated": []})
    age(p, 7200)
    await p._kh_query(QUERY, {"app_id": "a1", "gated": []})

    async def down(*_a, **_k):
        raise RuntimeError("unavailable")

    p.client = type("Down", (), {"execute_query": staticmethod(down)})()
    assert await p.kh_refresh_plans(max_age=3600) == 0
    p.client = client
    assert await p.kh_refresh_plans(max_age=3600) == 1

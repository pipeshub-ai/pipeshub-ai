"""A listing over every gated connector reads the viewer's grants in the other connectors once for all of its pages.

Each page needs the grants of every connector but its own (`_kh_v3_cross`); read per page, n pages read n near-whole copies.
"""

import asyncio
import logging

import pytest

from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

BY_APP = {"a1": ["x1"], "a2": ["y1", "y2"], "a3": []}


class Client:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def execute_query(self, query, parameters=None, txn_id=None, **kw) -> list[dict]:
        self.queries.append(query)
        await asyncio.sleep(0.01)
        if "AUTHENTICATED_AS" in query:
            return [{"user": "u1", "orgs": ["o1"], "badges": [], "linked": []}]
        if "RETURN app, ids" in query:
            return [{"app": a, "ids": ids} for a, ids in BY_APP.items() if a in parameters["others"]]
        raise AssertionError(query)


def provider() -> Neo4jProvider:
    p = object.__new__(Neo4jProvider)
    p.logger = logging.getLogger("test_kh_cross_every")
    p.client = Client()
    return p


@pytest.mark.asyncio
async def test_every_page_gets_the_other_connectors_grants_from_one_read() -> None:
    p = provider()
    gated = ["a1", "a2", "a3"]
    got = await asyncio.gather(*(p._kh_v3_cross("u1", "o1", gated, a, None, every_app=True) for a in gated))
    assert got == [["y1", "y2"], ["x1"], ["x1", "y1", "y2"]]
    assert len(p.client.queries) == 2


@pytest.mark.asyncio
async def test_past_the_bound_the_page_tests_the_holders_on_each_node() -> None:
    p = provider()
    p._KH_CROSS_MAX = 2
    got = await p._kh_v3_cross("u1", "o1", ["a1", "a2", "a3"], "a2", None, every_app=True)
    assert got == ["x1"]
    got = await p._kh_v3_cross("u1", "o1", ["a1", "a2", "a3"], "a3", None, every_app=True)
    assert got["probe"] == ["a1", "a2"] and got["common"] == ["u1", "o1"]

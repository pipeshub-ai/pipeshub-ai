"""A restarted Neo4j has an empty plan cache: the provider forgets what it planned, so one request per text plans again.

The server's start time is read on a request's way in, at most once per interval and once for the requests that arrive
together; an unreadable one is asked for again soon, and leaves the planned texts as they are.
"""

import asyncio
import logging

import pytest

from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

JMX = "CALL dbms.queryJmx"


class Server:
    def __init__(self) -> None:
        self.started: object = 1000
        self.statements: list[str] = []
        self.jmx_reads = 0

    async def execute_query(self, query, parameters=None, txn_id=None, **kw) -> list[dict]:
        if query.startswith(JMX):
            self.jmx_reads += 1
            await asyncio.sleep(0.01)
            if isinstance(self.started, Exception):
                raise self.started
            return [{"started": self.started}]
        self.statements.append(query)
        return [{"ok": True}]


def provider(server: Server) -> Neo4jProvider:
    p = object.__new__(Neo4jProvider)
    p.logger = logging.getLogger("test_kh_server_restart")
    p.client = server
    p._KH_SERVER_CHECK_S = 30.0  # off for unit tests elsewhere (tests/unit/conftest.py)
    return p


def checked_long_ago(p: Neo4jProvider) -> None:
    vars(p)["_kh_server"]["next"] = 0.0


@pytest.mark.asyncio
async def test_a_new_start_time_forgets_the_planned_texts() -> None:
    server = Server()
    p = provider(server)
    await asyncio.gather(*(p._kh_query("MATCH (a:App) RETURN a") for _ in range(3)))
    assert server.jmx_reads == 1  # one read for the requests that came together
    assert len(p._kh_plans()[1]) == 1
    await p._kh_query("MATCH (a:App) RETURN a")
    assert server.jmx_reads == 1  # within the interval: not read again
    checked_long_ago(p)
    await p._kh_query("MATCH (a:App) RETURN a")
    assert server.jmx_reads == 2 and len(p._kh_plans()[1]) == 1  # same server: still planned
    server.started = 2000
    checked_long_ago(p)
    planning = p._kh_plans()[1]
    planning_before = set(planning)
    await p._kh_query("MATCH (r:Record) RETURN r")
    # The texts planned on the old server are forgotten; the one this request ran is planned anew.
    assert planning_before and not planning_before & p._kh_plans()[1]


@pytest.mark.asyncio
async def test_an_unreadable_start_time_keeps_the_plans_and_is_asked_again_soon() -> None:
    server = Server()
    p = provider(server)
    await p._kh_query("MATCH (a:App) RETURN a")
    server.started = RuntimeError("procedure refused")
    checked_long_ago(p)
    await p._kh_query("MATCH (a:App) RETURN a")
    assert len(p._kh_plans()[1]) == 1
    state = vars(p)["_kh_server"]
    assert state["started"] == 1000
    assert state["next"] - asyncio.get_running_loop().time() <= Neo4jProvider._KH_SERVER_RETRY_S + 1


@pytest.mark.asyncio
async def test_a_slow_read_holds_a_request_back_no_longer_than_the_wait() -> None:
    server = Server()
    answered = asyncio.Event()
    read = server.execute_query

    async def slow(query, parameters=None, txn_id=None, **kw) -> list[dict]:
        if query.startswith(JMX):
            await answered.wait()
        return await read(query, parameters, txn_id, **kw)

    server.execute_query = slow
    p = provider(server)
    p._KH_SERVER_WAIT_S = 0.05
    await asyncio.wait_for(p._kh_query("MATCH (a:App) RETURN a"), 2)
    assert len(server.statements) == 1
    answered.set()
    await vars(p)["_kh_server"]["check"]
    assert vars(p)["_kh_server"]["started"] == 1000  # the read still lands, for the requests after


@pytest.mark.asyncio
async def test_zero_turns_the_check_off() -> None:
    server = Server()
    p = provider(server)
    p._KH_SERVER_CHECK_S = 0
    await p._kh_query("MATCH (a:App) RETURN a")
    assert server.jmx_reads == 0

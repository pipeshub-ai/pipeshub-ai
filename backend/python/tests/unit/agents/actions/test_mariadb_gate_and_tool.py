"""The approval gate and the MariaDB tool share one definition of a read: what the gate lets through
without asking is exactly what the tool runs inside the read-only transaction."""
from __future__ import annotations

import pytest

from app.agent_loop_lib.hooks.middleware.decisions import PreDecision
from tests.unit.agents.actions.test_mariadb_toolset import (  # noqa: F401
    SHOP,
    _address_check_passes,
    _Catalog,
    make_tool,
)
from tests.unit.agents.agent_loop.test_toolset_tool_approvals import _context, _sql_gate

READ_ONLY = "START TRANSACTION READ ONLY"

# Statements where the gate and the tool once disagreed (a writing function, a lock, a CTE write).
ASKS = [
    "(SELECT audit_log_write('x'))",
    "SELECT * FROM customers FOR UPDATE",
    "WITH x AS (UPDATE customers SET email = NULL RETURNING *) SELECT * FROM x",
    "EXPLAIN ANALYZE DELETE FROM customers",
]
# Plain reads of text that looks odd: a comment first, a literal holding ';' or INTO.
READS = [
    "/* c */ SELECT audit_log_write('x')",
    "SELECT audit_log_write('x'), ';' AS s",
    "SELECT audit_log_write('x'), 'INTO' AS s",
]


@pytest.mark.parametrize("sql", ASKS)
async def test_what_the_gate_asks_about_is_not_run_read_only(make_tool, sql: str) -> None:  # noqa: F811
    gate = await _sql_gate(_context(), sql)
    tool, pool = make_tool(_Catalog(SHOP, on_query=lambda q, p: []))
    await tool.execute_query(query=sql)

    assert gate.decision == PreDecision.ASK
    assert not any(READ_ONLY in s for s in pool.statements())


@pytest.mark.parametrize("sql", READS)
async def test_what_the_gate_allows_runs_read_only(make_tool, sql: str) -> None:  # noqa: F811
    gate = await _sql_gate(_context(), sql)
    tool, pool = make_tool(_Catalog(SHOP, on_query=lambda q, p: []))
    await tool.execute_query(query=sql)

    assert gate.decision == PreDecision.ALLOW
    assert any(READ_ONLY in s for s in pool.statements())


async def test_a_plain_select_is_allowed_and_runs_read_only(make_tool) -> None:  # noqa: F811
    assert (await _sql_gate(_context(), "SELECT id FROM customers")).decision == PreDecision.ALLOW
    tool, pool = make_tool(_Catalog(SHOP, on_query=lambda q, p: []))
    ok, _ = await tool.execute_query(query="SELECT id FROM customers")

    assert ok is True and any(READ_ONLY in s for s in pool.statements())

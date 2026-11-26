"""The tests of this folder share one database across xdist workers. A test that takes a lock on a whole table runs alone (``pg_table_lock``, conftest.py): a read
of another worker queued behind that lock waits out its ``lock_timeout``, the walk answers it, and a test that counts kg's answers fails."""
from __future__ import annotations

import ast
import os
import re
from pathlib import Path

import asyncpg
import pytest

from .conftest import TABLE_LOCK_KEY

pytestmark = pytest.mark.integration
# statements whose lock blocks every reader or writer of the table, in every org
TABLE_LOCKS = re.compile(r"LOCK TABLE|DROP INDEX|CREATE (UNIQUE )?INDEX|REINDEX (TABLE|INDEX|SCHEMA|DATABASE)|VACUUM FULL|TRUNCATE (TABLE )?[a-z]|CLUSTER [a-z]|ROW LEVEL SECURITY")


def test_every_test_that_locks_a_shared_table_runs_alone() -> None:
    unmarked = []
    for path in sorted(Path(__file__).parent.glob("test_*.py")):
        if path.name == Path(__file__).name:
            continue
        source = path.read_text()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_") and TABLE_LOCKS.search(ast.get_source_segment(source, node) or ""):
                marks = {d.attr for d in node.decorator_list if isinstance(d, ast.Attribute)}
                if "pg_table_lock" not in marks:
                    unmarked.append(f"{path.name}::{node.name}")
    assert unmarked == [], f"mark these @pytest.mark.pg_table_lock: {unmarked}"


async def _other_may_take(shared: bool) -> bool:
    con = await asyncpg.connect(os.environ["PG_CORE_URL"])
    try:
        got = await con.fetchval("SELECT pg_try_advisory_lock_shared($1)" if shared else "SELECT pg_try_advisory_lock($1)", TABLE_LOCK_KEY)
    finally:
        await con.close()
    return got


async def test_a_test_holds_the_folder_lock_shared(pg_required: None) -> None:
    assert await _other_may_take(shared=True)
    assert not await _other_may_take(shared=False)          # a pg_table_lock test waits for this one


@pytest.mark.pg_table_lock
async def test_a_table_lock_test_holds_the_folder_lock_alone(pg_required: None) -> None:
    assert not await _other_may_take(shared=True)            # every other test waits for this one

"""The tests of this folder share one database across xdist workers. A test that does something to the whole database runs alone (``pg_table_lock``, conftest.py):
a read of another worker queued behind its table lock waits out its ``lock_timeout`` and the walk answers it, and statistics it clears or rewrites change the plans of
every other worker, so a test that counts kg's answers or checks a plan fails."""
from __future__ import annotations

import ast
import asyncio
import contextlib
import os
import re
from pathlib import Path

import asyncpg
import pytest

from . import conftest
from .conftest import LOCK_HOLDER, TABLE_LOCK_KEY

pytestmark = pytest.mark.integration
# What a test does to the whole database, whatever its org: a lock that blocks every reader or writer of a table, or the planner statistics and storage settings
# of a shared table, which every other worker's plans (and plan gates) read. Matched in the strings of a test and of the helpers, fixtures and constants it uses.
DATABASE_WIDE = re.compile(r"LOCK TABLE|DROP INDEX|CREATE (UNIQUE )?INDEX|REINDEX (TABLE|INDEX|SCHEMA|DATABASE)|VACUUM FULL|TRUNCATE (TABLE )?[a-z]|CLUSTER [a-z]"
                           r"|ROW LEVEL SECURITY|pg_statistic|UPDATE pg_class|SET STATISTICS|\bSET \(|\bRESET \(|(?<![(\w])ANALYZE(?=\s+[a-z{%]|\s*;|\s*$)|^analyze$")


def _strings(node: ast.AST) -> list[str]:
    """The string constants under ``node``, docstrings left out."""
    docs = {id(b[0].value) for b in (getattr(n, "body", None) for n in ast.walk(node)) if isinstance(b, list) and b and isinstance(b[0], ast.Expr)
            and isinstance(b[0].value, ast.Constant)}
    return [n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs]


def _names(node: ast.AST) -> set[str]:
    """The names ``node`` uses: those it reads, and the fixtures a function asks for."""
    out = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        out |= {a.arg for a in node.args.args + node.args.kwonlyargs}
    return out


def unmarked_database_wide_tests(folder: Path) -> list[str]:
    """``file::test`` of each test of ``folder`` that does something database-wide (``DATABASE_WIDE``) itself, or through a function, fixture, class or constant of
    the folder it uses, and is not marked ``pg_table_lock``."""
    defs: dict[tuple[str, str], ast.AST] = {}
    imports: dict[tuple[str, str], tuple[str, str]] = {}
    tests = []
    for path in sorted(folder.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                defs[(path.stem, node.name)] = node
                if node.name.startswith("test_") and path.name != Path(__file__).name:
                    tests.append((path, node))
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        defs[(path.stem, t.id)] = node
            elif isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
                for a in node.names:
                    imports[(path.stem, a.asname or a.name)] = (node.module, a.name)
    seen: dict[tuple[str, str], bool] = {}

    def wide(key: tuple[str, str]) -> bool:
        key = imports.get(key, key)
        if key in seen or key not in defs:
            return seen.get(key, False)
        seen[key] = False                                   # a cycle adds nothing
        node = defs[key]
        seen[key] = any(DATABASE_WIDE.search(x) for x in _strings(node)) or any(wide((key[0], n)) for n in _names(node))
        return seen[key]
    return [f"{path.name}::{node.name}" for path, node in tests
            if wide((path.stem, node.name)) and "pg_table_lock" not in {d.attr for d in node.decorator_list if isinstance(d, ast.Attribute)}]


def test_every_test_that_changes_the_whole_database_runs_alone() -> None:
    unmarked = unmarked_database_wide_tests(Path(__file__).parent)
    assert unmarked == [], f"mark these @pytest.mark.pg_table_lock: {unmarked}"


async def _other_may_take(shared: bool) -> bool:
    con = await asyncpg.connect(os.environ["PG_CORE_URL"])
    try:
        got = await con.fetchval("SELECT pg_try_advisory_lock_shared($1)" if shared else "SELECT pg_try_advisory_lock($1)", TABLE_LOCK_KEY)
    finally:
        await con.close()
    return got


async def _held(request: pytest.FixtureRequest) -> str | None:
    """The mode in which the test's lock session holds the folder lock, read from ``pg_locks``: a new request to test it would queue behind other workers' waiters."""
    con = await asyncpg.connect(os.environ["PG_CORE_URL"])
    try:
        return await con.fetchval("""SELECT mode FROM pg_locks WHERE locktype = 'advisory' AND granted AND pid = $1 AND objsubid = 1
            AND (classid::bigint << 32 | objid::bigint) = $2""", request.node.stash[LOCK_HOLDER], TABLE_LOCK_KEY)
    finally:
        await con.close()


async def test_a_test_holds_the_folder_lock_shared(pg_required: None, request: pytest.FixtureRequest) -> None:
    assert await _held(request) == "ShareLock"
    assert not await _other_may_take(shared=False)          # a pg_table_lock test waits for this one


async def test_the_shared_hold_is_seen_while_a_table_lock_test_waits_for_it(pg_required: None, request: pytest.FixtureRequest) -> None:
    """A marked test of another worker that asks for the lock queues behind this test's shared hold; the check of that hold must not depend on such a queue."""
    waiter = await asyncpg.connect(os.environ["PG_CORE_URL"])
    try:
        queued = asyncio.ensure_future(waiter.execute("SELECT pg_advisory_lock($1)", TABLE_LOCK_KEY))
        look = await asyncpg.connect(os.environ["PG_CORE_URL"])
        try:
            for _ in range(200):
                if await look.fetchval("SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype = 'advisory' AND pid = $1 AND NOT granted)", waiter.get_server_pid()):
                    break
                await asyncio.sleep(0.05)
            else:
                pytest.fail("the exclusive request never queued")
        finally:
            await look.close()
        assert await _held(request) == "ShareLock"
        queued.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await queued
    finally:
        await waiter.close()


@pytest.mark.pg_table_lock
async def test_a_table_lock_test_holds_the_folder_lock_alone(pg_required: None, request: pytest.FixtureRequest) -> None:
    assert await _held(request) == "ExclusiveLock"
    assert not await _other_may_take(shared=True)            # every other test waits for this one


def test_the_wait_for_the_folder_lock_is_not_counted_by_the_test_timeout(request: pytest.FixtureRequest) -> None:
    """A test queued behind a marked one waits for it and for those running before it; pytest-timeout's timer must start only once the lock is held. Hook
    wrappers run outermost last in the list."""
    plugins = [i.plugin for i in request.config.pluginmanager.hook.pytest_runtest_protocol.get_hookimpls()]
    timeout = request.config.pluginmanager.get_plugin("timeout")
    assert timeout in plugins and conftest in plugins
    assert plugins.index(conftest) > plugins.index(timeout)

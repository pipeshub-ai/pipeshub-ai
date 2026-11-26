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
# What a test does to the whole database, whatever its org: a lock that blocks every reader or writer of a table; the planner statistics and storage settings
# of a shared table, which every other worker's plans (and plan gates) read; a migration's statements, which rewrite every org's rows. Matched in the strings of a
# test and of the helpers, fixtures and constants it uses.
DATABASE_WIDE = re.compile(r"LOCK TABLE|DROP INDEX|CREATE (UNIQUE )?INDEX|REINDEX (TABLE|INDEX|SCHEMA|DATABASE)|\bVACUUM\b|TRUNCATE (TABLE )?[a-z]|CLUSTER [a-z]"
                           r"|ROW LEVEL SECURITY|pg_statistic|UPDATE pg_class|SET STATISTICS|\bSET \(|\bRESET \(|(?<![(\w])ANALYZE(?=\s+[a-z{%]|\s*;|\s*$)|^analyze$"
                           r"|^\d{4}_[a-z0-9_]+\.py$")       # a migration revision's own statements, which name no org


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


def _marked(node: ast.AST) -> bool:
    return any(isinstance(d, ast.Attribute) and d.attr == "pg_table_lock" for d in ast.walk(node))


def _self_attrs(node: ast.AST) -> set[str]:
    """The attributes a method reads off ``self`` or ``cls``."""
    return {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id in ("self", "cls")}


def unmarked_database_wide_tests(modules: dict[str, str], conftests: list[str]) -> list[str]:
    """``module::test`` (``module::Class::test`` for a method) of each test in ``modules`` (module name: source) that does something database-wide
    (``DATABASE_WIDE``) itself, or through a function, fixture, class, method or constant it uses, and is not marked ``pg_table_lock`` (on the function, its class
    or the module's ``pytestmark``). A name is looked up as pytest finds a fixture: in the test's module (or what it imports from the folder), then in
    ``conftests``, nearest first; ``self.x`` and ``cls.x`` in a method are the class's own ``x`` or a folder base class's. "Uses" is reachability: every node that
    reaches a database-wide statement, found from those statements backwards, so a cycle or the order of the modules cannot change the answer."""
    defs: dict[tuple[str, str], tuple[ast.AST, str | None]] = {}       # (module, name or Class.method): the node and its class
    imports: dict[tuple[str, str], tuple[str, str]] = {}
    tests: list[tuple[str, str, tuple[str, str], bool]] = []
    for mod, source in modules.items():
        tree = ast.parse(source)
        module_marked = any(isinstance(n, ast.Assign) and any(getattr(t, "id", None) == "pytestmark" for t in n.targets) and _marked(n.value) for n in tree.body)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                defs[(mod, node.name)] = (node, None)
                if isinstance(node, ast.ClassDef):
                    class_marked = module_marked or any(_marked(d) for d in node.decorator_list)
                    for m in node.body:
                        if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            defs[(mod, f"{node.name}.{m.name}")] = (m, node.name)
                            if mod not in conftests and node.name.startswith("Test") and m.name.startswith("test_"):
                                tests.append((mod, f"{node.name}::{m.name}", (mod, f"{node.name}.{m.name}"), class_marked or any(_marked(d) for d in m.decorator_list)))
                elif mod not in conftests and node.name.startswith("test_"):
                    tests.append((mod, node.name, (mod, node.name), module_marked or any(_marked(d) for d in node.decorator_list)))
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        defs[(mod, t.id)] = (node, None)
            elif isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
                for a in node.names:
                    imports[(mod, a.asname or a.name)] = (node.module, a.name)

    def resolve(mod: str, name: str) -> tuple[str, str] | None:
        for key in ((mod, name), imports.get((mod, name)), *((c, name) for c in conftests)):
            if key in defs:
                return key
        return None

    def member(cls: tuple[str, str], attr: str, seen: frozenset = frozenset()) -> tuple[str, str] | None:
        """``attr`` of the class ``cls`` or of the first folder base class that has it."""
        if (key := (cls[0], f"{cls[1]}.{attr}")) in defs:
            return key
        for base in defs[cls][0].bases:
            if (isinstance(base, ast.Name) and (b := resolve(cls[0], base.id)) and b not in seen and isinstance(defs[b][0], ast.ClassDef)
                    and (found := member(b, attr, seen | {cls}))):
                return found
        return None

    callers: dict[tuple[str, str], set[tuple[str, str]]] = {}
    hits = []
    for key, (node, cls) in defs.items():
        if any(DATABASE_WIDE.search(x) for x in _strings(node)):
            hits.append(key)
        uses = {resolve(key[0], n) for n in _names(node)}
        if cls is not None:
            uses |= {member((key[0], cls), a) for a in _self_attrs(node)}
        for used in uses - {None}:
            callers.setdefault(used, set()).add(key)
    wide, todo = set(hits), list(hits)
    while todo:
        for caller in callers.get(todo.pop(), ()):
            if caller not in wide:
                wide.add(caller)
                todo.append(caller)
    return [f"{mod}::{name}" for mod, name, key, marked in tests if not marked and key in wide]


def _folder() -> tuple[dict[str, str], list[str]]:
    """This folder's modules (this one left out: its patterns are not statements) and the conftest chain above them, up to ``tests``."""
    here = Path(__file__).parent
    modules = {p.stem: p.read_text() for p in sorted(here.glob("*.py")) if p.name != Path(__file__).name}
    conftests = ["conftest"]
    for parent in here.parents:
        if parent.name == "python" or not (parent / "conftest.py").exists():
            break
        modules[str(parent / "conftest")] = (parent / "conftest.py").read_text()
        conftests.append(str(parent / "conftest"))
    return modules, conftests


def test_every_test_that_changes_the_whole_database_runs_alone() -> None:
    unmarked = unmarked_database_wide_tests(*_folder())
    assert unmarked == [], f"mark these @pytest.mark.pg_table_lock: {unmarked}"


def test_the_guard_finds_class_methods_and_conftest_fixtures_and_honours_marks_and_overrides() -> None:
    analyze, alter = '"ANALYZE kg.records"', '"ALTER TABLE kg.records SET (autovacuum_enabled = false)"'
    modules = {
        "conftest": f"import pytest\n@pytest.fixture\ndef settings_off():\n    run({alter})\n",
        "test_a": f"import pytest\nclass TestPlans:\n    def test_one(self):\n        run({analyze})\n"
                  f"@pytest.mark.pg_table_lock\nclass TestMarked:\n    def test_two(self):\n        run({analyze})\n"
                  "def test_uses_the_conftest_fixture(settings_off):\n    pass\n",
        "test_b": "import pytest\n@pytest.fixture\ndef settings_off():\n    return None\ndef test_uses_its_own_fixture(settings_off):\n    pass\n",
        "test_c": f"import pytest\npytestmark = [pytest.mark.integration, pytest.mark.pg_table_lock]\ndef test_three():\n    run({analyze})\n",
    }
    assert sorted(unmarked_database_wide_tests(modules, ["conftest"])) == ["test_a::TestPlans::test_one", "test_a::test_uses_the_conftest_fixture"]


def test_the_guard_follows_self_attributes_to_the_class_and_its_bases() -> None:
    modules = {"test_a": 'class Base:\n    def analyze(self):\n        run("ANALYZE kg.records")\n'
                         'class TestPlans(Base):\n    def prepare(self):\n        self.analyze()\n    def test_one(self):\n        self.prepare()\n'
                         '    def test_two(self):\n        self.other()\n    def other(self):\n        pass\n'}
    assert unmarked_database_wide_tests(modules, []) == ["test_a::TestPlans::test_one"]


def test_the_guard_is_reachability_whatever_the_cycles_and_the_order() -> None:
    """a and b call each other and only b reaches the statement: the tests of both are flagged, whichever is looked at first."""
    helpers = 'def a():\n    b()\ndef b():\n    a()\n    c()\ndef c():\n    run("ANALYZE kg.records")\n'
    first, second = "def test_by_b():\n    b()\n", "def test_by_a():\n    a()\n"
    for tests in ((first, second), (second, first)):
        modules = {"test_x": helpers + tests[0] + tests[1]}
        assert sorted(unmarked_database_wide_tests(modules, [])) == ["test_x::test_by_a", "test_x::test_by_b"]


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

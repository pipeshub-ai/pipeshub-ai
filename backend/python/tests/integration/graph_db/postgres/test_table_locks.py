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
    or the module's ``pytestmark``).

    A name is looked up as pytest finds a fixture: in the module (or what it imports from the folder), then in ``conftests``, nearest first. A method is analysed
    for each class it runs on (its receiver): ``self.x`` and ``cls.x`` are ``x`` as that class's MRO among the folder's classes finds it, so a helper a base class
    defines reaches the override of the test's class. "Uses" is reachability: every node that reaches a database-wide statement, found from those statements
    backwards, so a cycle or the order of the modules cannot change the answer.

    shortcut: a static check of the folder's own code. It follows names, fixtures and ``self``/``cls`` attributes through the class MRO, not other dynamic
    dispatch (``getattr``, a callable passed as a value or stored in a container, code outside the folder); extend it if a test starts to depend on those."""
    defs: dict[tuple[str, str], ast.AST] = {}                  # (module, name): a module-level function, class or constant
    imports: dict[tuple[str, str], tuple[str, str]] = {}
    tests: list[tuple[str, str, tuple, bool]] = []
    for mod, source in modules.items():
        tree = ast.parse(source)
        module_marked = any(isinstance(n, ast.Assign) and any(getattr(t, "id", None) == "pytestmark" for t in n.targets) and _marked(n.value) for n in tree.body)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                defs[(mod, node.name)] = node
                if mod in conftests:
                    continue
                if isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                    class_marked = module_marked or any(_marked(d) for d in node.decorator_list)
                    tests += [(mod, f"{node.name}::{m.name}", ("method", (mod, node.name), m.name), class_marked or any(_marked(d) for d in m.decorator_list))
                              for m in node.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and m.name.startswith("test_")]
                elif not isinstance(node, ast.ClassDef) and node.name.startswith("test_"):
                    tests.append((mod, node.name, ("def", (mod, node.name)), module_marked or any(_marked(d) for d in node.decorator_list)))
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        defs[(mod, t.id)] = node
            elif isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
                for a in node.names:
                    imports[(mod, a.asname or a.name)] = (node.module, a.name)

    def resolve(mod: str, name: str) -> tuple | None:
        """The node a name of ``mod`` stands for: its definition there, its import (an alias node), or a conftest's definition."""
        if (mod, name) in defs:
            return ("def", (mod, name))
        if (mod, name) in imports:
            return ("alias", (mod, name))
        return next((("def", (c, name)) for c in conftests if (c, name) in defs), None)

    def definition(mod: str, name: str) -> tuple[str, str] | None:
        """The definition a name of ``mod`` comes to through its imports (for a base class); None for a name defined nowhere in ``modules``."""
        node = resolve(mod, name)
        for _ in range(len(imports) + 1):                   # each hop is a distinct import, so a longer chain is a cycle
            if node is None or node[0] == "def":
                return node and node[1]
            node = resolve(*imports[node[1]])
        return None

    def mro(cls: tuple[str, str]) -> list[tuple[str, str]]:
        """The class and its folder base classes, depth first and left to right, each once: C3 for single inheritance (shortcut: not for a diamond, which the folder does not have)."""
        out: list[tuple[str, str]] = []
        todo = [cls]
        while todo:
            c = todo.pop(0)
            if c in out:
                continue
            out.append(c)
            todo[:0] = [b for base in defs[c].bases if isinstance(base, ast.Name) and (b := definition(c[0], base.id)) and isinstance(defs[b], ast.ClassDef)]
        return out

    def methods(cls: tuple[str, str]) -> dict[str, tuple[str, ast.AST]]:
        """Each method the class has, as its MRO finds it: name: (the defining module, the function)."""
        out: dict[str, tuple[str, ast.AST]] = {}
        for c in mro(cls):
            for m in defs[c].body:
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out.setdefault(m.name, (c[0], m))
        return out

    # nodes: ("def", key) for a module-level definition, ("alias", key) for an import (an edge to what it imports, a definition or another import), and
    # ("method", receiver class, name) for a method run on that class
    callers: dict[tuple, set[tuple]] = {}
    hits: list[tuple] = []

    def add(node_id: tuple, node: ast.AST, mod: str, uses: set) -> None:
        if any(DATABASE_WIDE.search(x) for x in _strings(node)):
            hits.append(node_id)
        for used in uses | {k for n in _names(node) if (k := resolve(mod, n))}:
            callers.setdefault(used, set()).add(node_id)
    for key, (imod, iname) in imports.items():
        if (target := resolve(imod, iname)) is not None:
            callers.setdefault(target, set()).add(("alias", key))
    for key, node in defs.items():
        if isinstance(node, ast.ClassDef):
            own = methods(key)
            for name, (dmod, fn) in own.items():
                add(("method", key, name), fn, dmod, {("method", key, a) for a in _self_attrs(fn) if a in own})
            body = ast.Module(body=[n for n in node.body if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))], type_ignores=[])
            add(("def", key), body, key[0], {("method", key, name) for name in own})       # a class used is a class whose methods may run
        else:
            add(("def", key), node, key[0], set())
    wide, todo = set(hits), list(hits)
    while todo:
        for caller in callers.get(todo.pop(), ()):
            if caller not in wide:
                wide.add(caller)
                todo.append(caller)
    return [f"{mod}::{name}" for mod, name, node_id, marked in tests if not marked and node_id in wide]


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


def test_the_guard_resolves_self_against_the_test_class_that_runs_an_inherited_helper() -> None:
    """Base.prepare calls self.analyze: on TestPlans it runs TestPlans.analyze, which analyses; on TestQuiet it runs Base.analyze, which does not."""
    modules = {"test_a": 'class Base:\n    def prepare(self):\n        self.analyze()\n    def analyze(self):\n        pass\n'
                         'class TestPlans(Base):\n    def analyze(self):\n        run("ANALYZE kg.records")\n    def test_one(self):\n        self.prepare()\n'
                         'class TestQuiet(Base):\n    def test_two(self):\n        self.prepare()\n'}
    assert unmarked_database_wide_tests(modules, []) == ["test_a::TestPlans::test_one"]


def test_the_guard_follows_imports_through_re_exports_and_survives_an_import_cycle() -> None:
    """test_a imports prepare from helpers, which imports it from operations, where it analyses; two modules importing a name from each other define it nowhere."""
    modules = {"operations": 'def prepare():\n    run("ANALYZE kg.records")\n',
               "helpers": "from .operations import prepare\n",
               "test_a": "from .helpers import prepare\nfrom .operations import prepare as direct\nfrom .loop_a import ghost\n"
                         "def test_through_the_re_export():\n    prepare()\ndef test_direct():\n    direct()\ndef test_ghost():\n    ghost()\n",
               "loop_a": "from .loop_b import ghost\n", "loop_b": "from .loop_a import ghost\n"}
    assert sorted(unmarked_database_wide_tests(modules, [])) == ["test_a::test_direct", "test_a::test_through_the_re_export"]


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

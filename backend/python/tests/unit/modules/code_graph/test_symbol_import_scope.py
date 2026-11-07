"""A function-local `from m import f` binds `f` in that function only.

The symbol-binding twin of test_module_bindings.py: Python scopes the import to the function it is written
in, so another function's bare `f()` is the file's own top-level `f`.
"""
import pytest

from app.config.constants.arangodb import RecordRelations
from app.modules.code_graph.edge_builder import build_code_graph_edges

from .conftest import GROUP_ID, ORG_ID

CALLS = RecordRelations.CALLS.value


pytestmark = pytest.mark.asyncio


UTIL_PY = b'''
def helper():
    return 1
'''


async def _edges(graph, index_file, path: str, source: bytes) -> set[tuple[str, str, str]]:
    """(relation, caller, callee as `file::name`) for the calls of *source*."""
    await index_file("recP", "app/__init__.py", b"", "python")
    await index_file("recU", "app/util.py", UTIL_PY, "python")
    await index_file("recF", path, source, "python")
    await build_code_graph_edges(graph_provider=graph, facts_source=graph, org_id=ORG_ID, record_group_id=GROUP_ID)
    out = set()
    for relation in (CALLS,):
        for e in graph.code_edges(relation):
            src = graph.blocks.get(e["_from"].partition("/")[2], {})
            dst = graph.blocks.get(e["_to"].partition("/")[2], {})
            if src.get("recordId") == "recF":
                out.add((relation, src.get("qualifiedName"),
                         f"{graph.code_files[dst['recordId']]}::{dst.get('qualifiedName')}" if dst else None))
    return out


LOCAL_FIRST = b'''
def first():
    from app.util import helper

    return helper()


def helper():
    return 2


def second():
    return helper()
'''

LOCAL_LAST = b'''
def helper():
    return 2


def second():
    return helper()


def first():
    from app.util import helper

    return helper()
'''


@pytest.mark.parametrize("source", [LOCAL_FIRST, LOCAL_LAST], ids=["local import written first", "written last"])
async def test_a_local_symbol_import_binds_only_its_function(graph, index_file, source) -> None:
    edges = await _edges(graph, index_file, "app/flows.py", source)
    assert (CALLS, "function:first", "app/util.py::function:helper") in edges
    second = {e for e in edges if e[1] == "function:second"}
    assert second and all(e[2] == "app/flows.py::function:helper" for e in second), edges


IMPORT_THEN_DEF = b'''
from app.util import helper


def helper():
    return 2


def second():
    return helper()
'''


async def test_a_module_level_import_follows_the_def_after_rule(graph, index_file) -> None:
    """As for `import time`, then `def time()`: the later statement is what the name means."""
    assert (CALLS, "function:second", "app/flows.py::function:helper") in \
        await _edges(graph, index_file, "app/flows.py", IMPORT_THEN_DEF)


IMPORT_BASE_THEN_DEF = b'''
from app.util import Base


class Early(Base):
    pass


class Base:
    pass


class Late(Base):
    pass


def build():
    return Base()
'''


async def test_a_use_run_as_the_module_loads_sees_the_import_until_the_def_runs(graph, index_file) -> None:
    """django gis functions.py: `class IsValid(GeoFuncMixin, Transform)` above its own `class Transform` inherits
    the imported Transform; a class after the def, and a function body, see the file's own."""
    await index_file("recP", "app/__init__.py", b"", "python")
    await index_file("recU", "app/util.py", b"class Base:\n    pass\n", "python")
    await index_file("recF", "app/models.py", IMPORT_BASE_THEN_DEF, "python")
    await build_code_graph_edges(graph_provider=graph, facts_source=graph, org_id=ORG_ID, record_group_id=GROUP_ID)
    pairs = set()
    for e in graph.code_edges():
        src = graph.blocks.get(e["_from"].partition("/")[2], {})
        dst = graph.blocks.get(e["_to"].partition("/")[2], {})
        if src.get("recordId") == "recF" and dst:
            pairs.add((e["relationshipType"], src.get("qualifiedName"), graph.code_files[dst["recordId"]]))
    assert (RecordRelations.INHERITS.value, "class:Early", "app/util.py") in pairs
    assert (RecordRelations.INHERITS.value, "class:Late", "app/models.py") in pairs
    assert (CALLS, "function:build", "app/models.py") in pairs, pairs


BASE_CHAIN = IMPORT_BASE_THEN_DEF.replace(b"class Base:\n    pass", b"class Base:\n    def run(self):\n        return 2") \
    .replace(b"class Late(Base):\n    pass", b"class Late(Base):\n    def go(self):\n        return self.run()") \
    .replace(b"class Early(Base):\n    pass", b"class Early(Base):\n    def go(self):\n        return self.run()")


async def test_the_base_chain_takes_the_same_base_as_the_heritage_edge(graph, index_file) -> None:
    """`self.run()` walks the base INHERITS names: the file's own Base for `Late`, the imported one for `Early`."""
    await index_file("recP", "app/__init__.py", b"", "python")
    await index_file("recU", "app/util.py", b"class Base:\n    def run(self):\n        return 1\n", "python")
    await index_file("recF", "app/models.py", BASE_CHAIN, "python")
    await build_code_graph_edges(graph_provider=graph, facts_source=graph, org_id=ORG_ID, record_group_id=GROUP_ID)
    calls = set()
    for e in graph.code_edges(CALLS):
        src = graph.blocks.get(e["_from"].partition("/")[2], {})
        dst = graph.blocks.get(e["_to"].partition("/")[2], {})
        if dst.get("qualifiedName") == "method:Base.run":
            calls.add((src.get("qualifiedName"), graph.code_files[dst["recordId"]]))
    assert calls == {("method:Late.go", "app/models.py"), ("method:Early.go", "app/util.py")}, calls


DECORATED = b'''
from app.util import helper


def deco(x):
    return lambda f: f


@helper
def g():
    pass


@deco(helper())
def h():
    pass


def helper(f=None):
    return f


VALUE = helper()
'''


async def test_a_decorator_above_the_def_sees_the_import_and_a_statement_below_it_the_def(graph, index_file) -> None:
    """`@helper` and `@deco(helper())` run as the module loads, before `def helper`; their facts sit on
    the decorated function's block but on or above its first line. `VALUE = helper()` runs after the def."""
    edges = await _edges(graph, index_file, "app/flows.py", DECORATED)
    to_helper = {e[1:] for e in edges if e[2].endswith("::function:helper")}
    assert ("function:g", "app/util.py::function:helper") in to_helper
    assert ("function:h", "app/util.py::function:helper") in to_helper
    assert not {e for e in to_helper if e[0] in ("function:g", "function:h") and e[1].startswith("app/flows.py")}
    assert any(e[1] == "app/flows.py::function:helper" and e[0] not in ("function:g", "function:h")
               for e in to_helper), to_helper


LOCAL_LIBRARY = b'''
def first():
    from requests import get

    return get("u")


def get(url):
    return url


def second():
    return get("v")
'''


async def test_a_local_import_from_a_library_binds_only_its_function(graph, index_file) -> None:
    edges = await _edges(graph, index_file, "app/client.py", LOCAL_LIBRARY)
    assert (CALLS, "function:second", "app/client.py::function:get") in edges
    assert not {e for e in edges if e[1] == "function:first"}, edges

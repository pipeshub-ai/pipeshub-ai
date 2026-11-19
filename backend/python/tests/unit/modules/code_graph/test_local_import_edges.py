"""Where an import edge starts, and what a bare Python import may land on.

A Python import written inside a function or method is still the file's dependency: its IMPORTS and IMPORTS_FROM
edges start at the file, as a module-level import's do, while the
name it binds stays local to that function (test_symbol_import_scope.py).
"""
import pytest

from app.config.constants.arangodb import CollectionNames, RecordRelations

from .test_import_binding import _build, _package, _targets

CALLS, IMPORTS, IMPORTS_FROM = (RecordRelations.CALLS.value, RecordRelations.IMPORTS.value,
                                RecordRelations.IMPORTS_FROM.value)
RECORDS = CollectionNames.RECORDS.value


pytestmark = pytest.mark.asyncio


UTIL = b"def helper():\n    return 1\n\n\nclass Store:\n    pass\n"

LOCAL = b'''
import os


def first():
    from app.util import helper

    return helper()


class Job:
    def run(self):
        from app.util import Store
        from app import util

        return Store(), util


def helper():
    return 2


def second():
    return helper()
'''


def _sources(graph, relation: str, record_id: str) -> set[str]:
    """The `_from` collection of each *relation* edge leaving *record_id*'s record or blocks."""
    out = set()
    for e in graph.code_edges(relation):
        collection, _, key = e["_from"].partition("/")
        owner = key if collection == RECORDS else graph.blocks.get(key, {}).get("recordId")
        if owner == record_id:
            out.add(collection)
    return out


async def _repo(index_file) -> None:
    await _package(index_file, "app")
    await index_file("recU", "app/util.py", UTIL, "python")
    await index_file("recF", "app/flows.py", LOCAL, "python")


async def test_a_function_local_import_is_the_files_edge(graph, index_file) -> None:
    await _repo(index_file)
    await _build(graph)
    assert _targets(graph, IMPORTS, "app/flows.py") == {"app/util.py::function:helper", "app/util.py::class:Store"}
    assert _targets(graph, IMPORTS_FROM, "app/flows.py") == {"app/util.py"}  # an empty __init__.py has no record
    assert _sources(graph, IMPORTS, "recF") == {RECORDS}
    assert _sources(graph, IMPORTS_FROM, "recF") == {RECORDS}


async def test_the_local_binding_still_holds_only_in_its_function(graph, index_file) -> None:
    await _repo(index_file)
    await _build(graph)
    calls = {(graph.blocks[e["_from"].partition("/")[2]]["qualifiedName"], e["_to"].partition("/")[2])
             for e in graph.code_edges(CALLS) if e["_from"].partition("/")[2] in graph.blocks}
    names = {key: f"{graph.code_files[b['recordId']]}::{b['qualifiedName']}" for key, b in graph.blocks.items()}
    calls = {(src, names.get(dst)) for src, dst in calls}
    assert ("function:first", "app/util.py::function:helper") in calls
    assert ("function:second", "app/flows.py::function:helper") in calls
    assert ("function:second", "app/util.py::function:helper") not in calls


async def test_a_bare_import_never_lands_in_a_namespace_folder_inside_a_package(graph, index_file) -> None:
    # `app/modules/parsers` has no __init__.py but sits in the `app.modules` package: `import yaml` is PyYAML.
    await _package(index_file, "app", "app/modules")
    await index_file("yml", "app/modules/parsers/yaml/__init__.py", b"def safe_load(s):\n    return s\n", "python")
    await index_file("svc", "app/svc.py", b"import yaml\n\n\ndef load(s):\n    return yaml.safe_load(s)\n", "python")
    await _build(graph)
    assert _targets(graph, IMPORTS_FROM, "app/svc.py") == set()
    assert _targets(graph, CALLS, "app/svc.py") == set()


async def test_a_bare_import_never_lands_several_namespace_folders_below_a_package(graph, index_file) -> None:
    # `app` is a package; `agents/actions/dropbox` are namespace folders under it: `from dropbox import` is the SDK.
    await _package(index_file, "app")
    await index_file("dbx", "app/agents/actions/dropbox/dropbox.py", b"class Dropbox:\n    pass\n", "python")
    await index_file("cli", "app/sources/client.py", b"from dropbox import Dropbox\n\n\ndef make():\n"
                                                    b"    return Dropbox()\n", "python")
    await _build(graph)
    assert _targets(graph, IMPORTS_FROM, "app/sources/client.py") == set()
    assert _targets(graph, CALLS, "app/sources/client.py") == set()

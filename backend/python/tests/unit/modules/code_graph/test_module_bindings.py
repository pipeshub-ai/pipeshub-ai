"""A Python name an import binds to a module is that module, never a definition named like it; a file's own
top-level definition takes the name back where the import does not reach the caller.

django/core/mail/__init__.py's `__getattr__` does `from django.core.mail import message`: the name is the
submodule, yet a bare use of it was linked to the method `EmailMessage.message`.
"""
import pytest

from app.config.constants.arangodb import RecordRelations
from app.modules.code_graph.edge_builder import build_code_graph_edges

from .conftest import GROUP_ID, ORG_ID

CALLS = RecordRelations.CALLS.value


pytestmark = pytest.mark.asyncio


async def _edges(graph, index_file, files, relation: str = CALLS) -> set[tuple[str, str]]:
    for record, path, src, language in files:
        await index_file(record, path, src, language)
    await build_code_graph_edges(graph_provider=graph, facts_source=graph, org_id=ORG_ID, record_group_id=GROUP_ID)
    return {(graph.blocks.get(e["_from"].partition("/")[2], {}).get("qualifiedName"),
             graph.blocks.get(e["_to"].partition("/")[2], {}).get("qualifiedName"))
            for e in graph.code_edges(relation)}


MESSAGE = b'''
class EmailMessage:
    def message(self, policy=None):
        return policy
'''

MAIL_INIT = b'''
def __getattr__(name):
    from django.core.mail import message

    return message(name)
'''


async def test_a_name_bound_to_a_submodule_is_not_a_method_named_like_it(graph, index_file) -> None:
    edges = await _edges(graph, index_file, [
        ("recM", "django/core/mail/message.py", MESSAGE, "python"),
        ("recI", "django/core/mail/__init__.py", MAIL_INIT, "python")])
    assert not {e for e in edges if e[1] == "method:EmailMessage.message"}, edges


HELPERS_PY = b'''
def requests(url):
    return url
'''

CLIENT_PY = b'''
import requests


def fetch(url):
    return requests(url)
'''


async def test_a_module_from_outside_the_repo_is_not_a_repo_function_named_like_it(graph, index_file) -> None:
    edges = await _edges(graph, index_file, [("recH", "app/helpers.py", HELPERS_PY, "python"),
                                             ("recC", "app/client.py", CLIENT_PY, "python")])
    assert ("function:fetch", "function:requests") not in edges


UTIL_PKG = b'''
def helper():
    return 1
'''

LOCAL_THEN_OWN = b'''
def first():
    from app import util

    return util.helper()


def util():
    return 2


def second():
    return util()


def inside():
    from app import util

    return util()
'''


PACKAGE = [("recP", "app/__init__.py", b"", "python"), ("recU", "app/util.py", UTIL_PKG, "python")]


async def test_an_import_local_to_another_function_does_not_hide_the_files_own_definition(graph, index_file) -> None:
    """`from app import util` inside first() binds `util` there only: second()'s `util()` is the file's own
    top-level util, while inside()'s own local import keeps `util` the module there."""
    edges = await _edges(graph, index_file, [*PACKAGE, ("recL", "app/flows.py", LOCAL_THEN_OWN, "python")])
    assert ("function:second", "function:util") in edges
    assert not {e for e in edges if e[0] in ("function:first", "function:inside") and e[1] == "function:util"}


LOCAL_IMPORT_WRITTEN_LAST = b'''
def util():
    return 2


from app import util


def first():
    from app import util

    return util.helper()


def second():
    return util()
'''

LOCAL_IMPORT_WRITTEN_FIRST = b'''
def first():
    from app import util

    return util()


from app import util


def util():
    return 2


def second():
    return util()
'''


async def test_a_name_imported_at_module_level_and_in_a_function_is_decided_per_scope(graph, index_file) -> None:
    """The import written last does not decide for every caller: a module-level import after `def util()`
    rebinds `util` for second(); a `def util()` after it rebinds it back, while first()'s local import holds."""
    last = await _edges(graph, index_file, [*PACKAGE, ("recA", "app/last.py", LOCAL_IMPORT_WRITTEN_LAST, "python")])
    assert not {e for e in last if e[0] == "function:second" and e[1] == "function:util"}, last


async def test_a_local_import_written_before_the_module_level_one_still_holds_in_its_function(
        graph, index_file) -> None:
    edges = await _edges(graph, index_file, [*PACKAGE, ("recB", "app/first.py", LOCAL_IMPORT_WRITTEN_FIRST, "python")])
    assert ("function:second", "function:util") in edges
    assert not {e for e in edges if e[0] == "function:first" and e[1] == "function:util"}, edges

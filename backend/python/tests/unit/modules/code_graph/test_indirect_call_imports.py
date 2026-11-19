"""INDIRECT_CALL and imported names.

django/core/mail/__init__.py's module `__getattr__` does `from django.core.mail import message` and then
`getattr(message, name)`: the value is the submodule `message`, yet it was linked to the method
`EmailMessage.message`, which then outranked the real tests in impact.
"""
import pytest

from app.config.constants.arangodb import RecordRelations
from app.modules.code_graph.edge_builder import build_code_graph_edges

from .conftest import GROUP_ID, ORG_ID

INDIRECT = RecordRelations.INDIRECT_CALL.value

pytestmark = pytest.mark.asyncio

MESSAGE = b'''
class EmailMessage:
    def message(self, policy=None):
        return policy


def make_msgid(domain=None):
    return domain
'''

MAIL_INIT = b'''
import warnings


def __getattr__(name):
    from django.core.mail import message

    warnings.warn(name)
    return getattr(message, name)


def ids(domain):
    from django.core.mail.message import make_msgid

    return map(make_msgid, [domain])


def ids_aliased(domain):
    from django.core.mail.message import make_msgid as new_id

    return map(new_id, [domain])


def modules(domain):
    import django.core.mail.message as message

    return getattr(message, domain)
'''

TOP_LEVEL = b'''
from django.core.mail import message
from django.core.mail.message import make_msgid


def at_import(domain):
    return map(make_msgid, [domain])


def module_value(domain):
    return getattr(message, domain)
'''


async def _repo(graph, index_file) -> set[tuple[str, str]]:
    await index_file("recM", "django/core/mail/message.py", MESSAGE, "python")
    await index_file("recI", "django/core/mail/__init__.py", MAIL_INIT, "python")
    await index_file("recT", "django/core/mail/top.py", TOP_LEVEL, "python")
    await build_code_graph_edges(graph_provider=graph, facts_source=graph, org_id=ORG_ID, record_group_id=GROUP_ID)
    out = set()
    for edge in graph.code_edges(INDIRECT):
        src = graph.blocks.get(edge["_from"].partition("/")[2], {})
        dst = graph.blocks.get(edge["_to"].partition("/")[2], {})
        out.add((src.get("qualifiedName"), dst.get("qualifiedName")))
    return out


async def test_a_module_bound_by_a_local_import_is_not_a_method_named_like_it(graph, index_file) -> None:
    pairs = await _repo(graph, index_file)
    assert not {p for p in pairs if p[1] == "method:EmailMessage.message"}, pairs


async def test_a_function_bound_by_a_local_import_still_links_to_it(graph, index_file) -> None:
    pairs = await _repo(graph, index_file)
    assert ("function:ids", "function:make_msgid") in pairs
    assert ("function:ids_aliased", "function:make_msgid") in pairs


MESSAGE_TS = b'''
export class EmailMessage {
  message(): string { return "m"; }
}

export function makeId(): string { return "id"; }
'''

USES_TS = b'''
import * as message from "./message";
import { makeId } from "./message";

export function all(names: string[]) {
  return names.map((n) => Reflect.get(message, n));
}

export function ids(xs: string[]) {
  return xs.map(makeId);
}

export function lazy(n: string) {
  const mod = require("./message");
  return Reflect.get(mod, n);
}
'''


async def test_typescript_namespace_and_local_require_bindings_are_not_methods(graph, index_file) -> None:
    """A namespace import is the module; a local `require` is a local (the shadow guard): neither is a
    method named like it, while a named import of a function still links."""
    await index_file("recA", "web/message.ts", MESSAGE_TS, "typescript")
    await index_file("recB", "web/uses.ts", USES_TS, "typescript")
    await build_code_graph_edges(graph_provider=graph, facts_source=graph, org_id=ORG_ID, record_group_id=GROUP_ID)
    pairs = {(graph.blocks.get(e["_from"].partition("/")[2], {}).get("qualifiedName"),
              graph.blocks.get(e["_to"].partition("/")[2], {}).get("qualifiedName"))
             for e in graph.code_edges(INDIRECT)}
    assert not {p for p in pairs if p[1] and "EmailMessage" in p[1]}, pairs
    assert ("function:ids", "function:makeId") in pairs


HELPERS_PY = b'''
def requests(url):
    return url


def time():
    return 0
'''

CLIENT_PY = b'''
import time

import requests


def fetch(url):
    requests(url)
    return list(map(requests, [url]))


def stamp(patch):
    return patch(time, "time")
'''


async def test_a_module_from_outside_the_repo_is_not_a_repo_function_named_like_it(graph, index_file) -> None:
    """`import requests` binds a module no repo file holds; `requests()` and `map(requests, ...)` are not the
    repo's own `requests` function, and django's `mock.patch.object(time, "time")` is not a
    same-file `time` method."""
    await index_file("recH", "app/helpers.py", HELPERS_PY, "python")
    await index_file("recC", "app/client.py", CLIENT_PY, "python")
    await build_code_graph_edges(graph_provider=graph, facts_source=graph, org_id=ORG_ID, record_group_id=GROUP_ID)
    for relation in (RecordRelations.CALLS.value, INDIRECT):
        targets = {graph.blocks.get(e["_to"].partition("/")[2], {}).get("qualifiedName")
                   for e in graph.code_edges(relation)
                   if graph.blocks.get(e["_from"].partition("/")[2], {}).get("recordId") == "recC"}
        assert not targets & {"function:requests", "function:time"}, (relation, targets)


UTIL_PKG = b'''
def helper():
    return 1
'''

async def _calls_and_values(graph, index_file, files) -> set[tuple[str, str, str]]:
    for record, path, src in files:
        await index_file(record, path, src, "python")
    await build_code_graph_edges(graph_provider=graph, facts_source=graph, org_id=ORG_ID, record_group_id=GROUP_ID)
    out = set()
    for relation in (RecordRelations.CALLS.value, INDIRECT):
        for e in graph.code_edges(relation):
            out.add((relation, graph.blocks.get(e["_from"].partition("/")[2], {}).get("qualifiedName"),
                     graph.blocks.get(e["_to"].partition("/")[2], {}).get("qualifiedName")))
    return out


OWN_LOCAL_IMPORT = b'''
def first(items):
    from app import util

    util()
    return list(map(util, items))


def util():
    return 2
'''


async def test_inside_the_function_that_imports_it_the_name_stays_the_module(graph, index_file) -> None:
    """first()'s own `from app import util` makes `util` the module there, even with a top-level
    `def util()` later in the file: neither the call nor the value is that function."""
    edges = await _calls_and_values(graph, index_file, [
        ("recP", "app/__init__.py", b""), ("recU", "app/util.py", UTIL_PKG), ("recO", "app/own.py", OWN_LOCAL_IMPORT)])
    assert not {e for e in edges if e[1] == "function:first" and e[2] == "function:util"}, edges


async def test_module_level_imports_resolve_as_before(graph, index_file) -> None:
    pairs = await _repo(graph, index_file)
    assert ("function:at_import", "function:make_msgid") in pairs
    assert not {p for p in pairs if p[0] == "function:module_value"}

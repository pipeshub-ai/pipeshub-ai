"""A package `__init__.py` that re-exports with `from .fields import *`.

django's `models/__init__.py` makes `DateTimeField` available by `from django.db.models.fields import *`,
and `models.DateTimeField()` is how every model declares a field: 6,549 of django's CALLS misses were
constructor calls, most through star re-exports, because only named re-exports were followed.
"""
import pytest

from app.config.constants.arangodb import RecordRelations

from .test_import_binding import _build, _targets

CALLS = RecordRelations.CALLS.value


pytestmark = pytest.mark.asyncio


INIT = b'''
from pkg.models.fields import *  # noqa
from pkg.models.related import ForeignKey
'''

FIELDS = b'''
class Field:
    pass


class DateTimeField(Field):
    pass


def _internal():
    return 1
'''

RELATED = b'''
class ForeignKey:
    pass
'''

MODELS = b'''
from pkg import models
from pkg.models import DateTimeField as DTF


class Article:
    created = models.DateTimeField()
    author = models.ForeignKey()
    updated = DTF()

    def hidden(self):
        return models._internal(), models.Missing()
'''


async def _repo(graph, index_file) -> None:
    await index_file("recP", "pkg/__init__.py", b'"""pkg."""\n', "python")
    await index_file("recI", "pkg/models/__init__.py", INIT, "python")
    await index_file("recF", "pkg/models/fields.py", FIELDS, "python")
    await index_file("recR", "pkg/models/related.py", RELATED, "python")
    await index_file("recM", "app/models.py", MODELS, "python")
    await _build(graph)


async def test_a_member_call_through_a_star_reexport_reaches_the_class(graph, index_file) -> None:
    await _repo(graph, index_file)
    targets = _targets(graph, CALLS, "app/models.py")
    assert "pkg/models/fields.py::class:DateTimeField" in targets
    assert "pkg/models/related.py::class:ForeignKey" in targets  # a named re-export, as before
    assert "pkg/models/fields.py::function:_internal" not in targets  # a private name is not star-exported
    assert not any("Missing" in t for t in targets)


async def test_a_from_import_through_a_star_reexport_binds_the_class(graph, index_file) -> None:
    await _repo(graph, index_file)
    assert "pkg/models/fields.py::class:DateTimeField" in _targets(graph, RecordRelations.IMPORTS.value,
                                                                   "app/models.py")


WITH_ALL = b'''
__all__ = ["pub"]


def pub():
    return 1


def hidden():
    return 2
'''

# The same module, its `__all__` written over lines with a comment among the names.
WITH_COMMENTED_ALL = WITH_ALL.replace(b'__all__ = ["pub"]', b'__all__ = [\n    # functions\n    "pub",\n]')

WITHOUT_ALL = b'''
def open_fn():
    return 1


def _under():
    return 2
'''


def _calls_by_caller(graph) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for edge in graph.code_edges(RecordRelations.CALLS.value):
        src = (graph.blocks.get(edge["_from"].partition("/")[2]) or {}).get("qualifiedName")
        dst = (graph.blocks.get(edge["_to"].partition("/")[2]) or {}).get("qualifiedName")
        out.setdefault(src, set()).add(dst)
    return out


@pytest.mark.parametrize("module", [WITH_ALL, WITH_COMMENTED_ALL], ids=["one-line", "with-a-comment"])
async def test_a_star_reexport_brings_only_the_literal_all(graph, index_file, module: bytes) -> None:
    """`from pkg.with_all import *` in pkg/__init__.py re-exports `pub` only (its `__all__`)."""
    await index_file("r1", "pkg/__init__.py", b"from pkg.with_all import *  # noqa\n", "python")
    await index_file("r2", "pkg/with_all.py", module, "python")
    await index_file("r3", "app/re.py", b"from pkg import pub, hidden\nimport pkg\n\n\ndef via_from():\n    pub()\n"
                     b"    hidden()\n\n\ndef via_module():\n    pkg.pub()\n    pkg.hidden()\n", "python")
    await _build(graph)
    calls = _calls_by_caller(graph)
    assert calls["function:via_from"] == {"function:pub"}
    assert calls["function:via_module"] == {"function:pub"}


async def test_a_direct_star_import_brings_only_the_literal_all_else_the_public_names(graph, index_file) -> None:
    """`from lib.with_all import *` brings `pub` alone; without `__all__`, every name but `_under`."""
    await index_file("s1", "lib/__init__.py", b"", "python")
    await index_file("s2", "lib/with_all.py", WITH_ALL, "python")
    await index_file("s3", "lib/without_all.py", WITHOUT_ALL, "python")
    await index_file("s4", "app/user.py", b"from lib.with_all import *\nfrom lib.without_all import *\n\n\n"
                     b"def use():\n    pub()\n    hidden()\n    open_fn()\n    _under()\n", "python")
    await _build(graph)
    assert _targets(graph, CALLS, "app/user.py") == {"lib/with_all.py::function:pub",
                                                    "lib/without_all.py::function:open_fn"}

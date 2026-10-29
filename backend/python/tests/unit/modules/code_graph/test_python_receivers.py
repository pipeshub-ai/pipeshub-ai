"""Python member calls on untyped receivers: parameters, attributes, return values.

Most Python is unannotated, so `field.clean()` or `self.admin.get_actions()` named
no type and resolved to nothing; a Python repo's callers and impact were missing
most calls made through an object passed in or stored on `self`.
"""
import pytest

from app.config.constants.arangodb import RecordRelations

from .test_import_binding import _build, _targets

CALLS = RecordRelations.CALLS.value

QUERY = b'''
class QuerySet:
    def filter(self, **kw):
        clone = self._chain()
        return clone

    def _chain(self):
        return self

    def order_by(self, *names):
        return self

    def count(self):
        return 0


class Manager:
    def get_queryset(self):
        return QuerySet()


class Other:
    def order_by(self):
        pass
'''


async def _index(index_file, files: dict[str, bytes]) -> None:
    for path, src in files.items():
        await index_file(path.rsplit("/", 1)[-1].removesuffix(".py"), path, src, "python")


@pytest.mark.asyncio
async def test_a_module_level_instance_is_typed_where_it_is_imported(graph, index_file) -> None:
    await _index(index_file, {
        "pkg/registry.py": b"class Apps:\n    def get_model(self, name):\n        return name\n\napps = Apps()\n",
        "pkg/use.py": b"from pkg.registry import apps\n\ndef run():\n    return apps.get_model('a')\n",
    })
    await _build(graph)
    assert _targets(graph, CALLS, "pkg/use.py") == {"pkg/registry.py::method:Apps.get_model"}


@pytest.mark.asyncio
async def test_return_types_carry_a_call_chain(graph, index_file) -> None:
    """`queryset.filter().order_by()`: `order_by` is defined twice, so only the chain
    (`filter` returns `self._chain()`, which returns `self`) can say which one."""
    await _index(index_file, {
        "pkg/query.py": QUERY,
        "pkg/view.py": b'''
from pkg.query import Manager

class View:
    def __init__(self):
        self.manager = Manager()

    def run(self, queryset):
        queryset.filter(x=1).order_by("y")
        qs = self.manager.get_queryset()
        return qs.count()
''',
    })
    await _build(graph)
    assert _targets(graph, CALLS, "pkg/view.py") >= {
        "pkg/query.py::method:QuerySet.filter", "pkg/query.py::method:QuerySet.order_by",
        "pkg/query.py::method:Manager.get_queryset", "pkg/query.py::method:QuerySet.count",
    }
    assert "pkg/query.py::method:Other.order_by" not in _targets(graph, CALLS, "pkg/view.py")


@pytest.mark.asyncio
async def test_an_attribute_set_from_a_parameter_is_typed_by_the_parameters_name(graph, index_file) -> None:
    await _index(index_file, {
        "pkg/admin.py": b"class ModelAdmin:\n    def get_actions(self, request):\n        return []\n",
        "pkg/other.py": b"class Unrelated:\n    def get_actions(self):\n        return []\n",
        "pkg/view.py": b'''
class View:
    def __init__(self, model_admin):
        self.admin = model_admin

    def run(self, request):
        return self.admin.get_actions(request)
''',
    })
    await _build(graph)
    assert _targets(graph, CALLS, "pkg/view.py") == {"pkg/admin.py::method:ModelAdmin.get_actions"}


@pytest.mark.asyncio
async def test_a_family_name_resolves_to_the_base_of_the_family(graph, index_file) -> None:
    """`schema_editor` names every backend's `*SchemaEditor`; the call is to their base."""
    await _index(index_file, {
        "pkg/schema.py": b'''
class BaseDatabaseSchemaEditor:
    def add_field(self, model, field):
        pass

class SqliteSchemaEditor(BaseDatabaseSchemaEditor):
    def add_field(self, model, field):
        pass

class PostgresSchemaEditor(BaseDatabaseSchemaEditor):
    pass
''',
        "pkg/ops.py": b"def forwards(schema_editor, model, field):\n    schema_editor.add_field(model, field)\n",
    })
    await _build(graph)
    assert _targets(graph, CALLS, "pkg/ops.py") == {"pkg/schema.py::method:BaseDatabaseSchemaEditor.add_field"}


@pytest.mark.asyncio
async def test_untyped_guesses_stop_short_of_builtins_and_third_party(graph, index_file) -> None:
    """A unique repo method is a fair guess for an untyped receiver, but not when the
    name is a builtin value's method, the chain starts at a builtin or an external
    import, or one word happens to end a single class name (`cursor`)."""
    await _index(index_file, {
        "pkg/lib.py": b'''
class Report:
    def render_totals(self):
        pass
    def count(self):
        return 0
    def splitext(self):
        pass

class PlaceholderCursor:
    def execute(self, sql):
        pass

class Command:
    def execute(self, *args):
        pass
''',
        "pkg/use.py": b'''
import os

def run(report, data, cursor, name):
    report.render_totals()
    data.count(1)
    os.path.splitext(name)
    dict.fromkeys(data)
    cursor.execute("select 1")
''',
    })
    await _build(graph)
    assert _targets(graph, CALLS, "pkg/use.py") == {"pkg/lib.py::method:Report.render_totals"}


@pytest.mark.asyncio
async def test_two_argument_super_starts_above_the_named_class(graph, index_file) -> None:
    await _index(index_file, {
        "pkg/geo.py": b'''
class Base:
    def as_sql(self):
        pass

class Mid(Base):
    def as_sql(self):
        pass

class Leaf(Mid):
    def as_sql(self, clone):
        return super(Mid, clone).as_sql()
''',
    })
    await _build(graph)
    assert ("pkg/geo.py::method:Base.as_sql"
            in _targets(graph, CALLS, "pkg/geo.py"))

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


@pytest.mark.asyncio
async def test_a_name_only_guess_needs_code_the_caller_can_see(graph, index_file) -> None:
    """`client.patch()` in one integration is not the one `patch` another package
    defines: a guess by name needs the target's module (or its package) imported,
    and app code never lands in a test file."""
    await _index(index_file, {
        "pkg/skills/manager.py": b"class SkillManager:\n    def patch_skill(self):\n        pass\n",
        "tests/test_fakes.py": b"class FakeLogger:\n    def warn_once(self):\n        pass\n",
        "pkg/billing/__init__.py": b"",
        "pkg/billing/ledger.py": b"class Ledger:\n    def post_entry(self):\n        pass\n",
        "pkg/outlook.py": b'''
from pkg.billing import ledger

def sync(client, log, book):
    client.patch_skill()
    log.warn_once()
    book.post_entry()
''',
    })
    await _build(graph)
    assert _targets(graph, CALLS, "pkg/outlook.py") == {"pkg/billing/ledger.py::method:Ledger.post_entry"}


@pytest.mark.asyncio
async def test_a_name_shared_by_unrelated_classes_resolves_to_the_one_the_caller_imports(graph, index_file) -> None:
    """Model fields and form fields are both `Field` with `to_python`; a serializer that
    imports only the models package means the model one. Seeing both, it guesses neither."""
    field = b"class Field:\n    def to_python(self, value):\n        return value\n"
    files = {
        "app/__init__.py": b"",
        "app/db/__init__.py": b"",
        "app/db/models/__init__.py": b"from app.db.models.fields import Field\n",
        "app/db/models/fields/__init__.py": field,
        "app/forms/__init__.py": b"from app.forms.fields import Field\n",
        "app/forms/fields.py": field,
        "app/core/serializers/python.py": b'''
from app.db import models

def deserialize(Model, name, value):
    field = Model._meta.get_field(name)
    return field.to_python(value)
''',
        "app/core/both.py": b'''
from app.db import models
from app import forms

def convert(field, value):
    return field.to_python(value)
''',
    }
    for path, src in files.items():  # one record per file: several are `__init__.py`
        await index_file(path.replace("/", "_"), path, src, "python")
    await _build(graph)
    assert _targets(graph, CALLS, "app/core/serializers/python.py") == {
        "app/db/models/fields/__init__.py::method:Field.to_python"}
    assert _targets(graph, CALLS, "app/core/both.py") == set()


@pytest.mark.asyncio
async def test_app_code_never_guesses_a_class_only_a_test_file_defines(graph, index_file) -> None:
    await _index(index_file, {
        "tests/fakes.py": b"class Client:\n    def fetch_page(self):\n        pass\n",
        "pkg/sync.py": b"class Sync:\n    def run(self):\n        return self.client.fetch_page()\n",
    })
    await _build(graph)
    assert _targets(graph, CALLS, "pkg/sync.py") == set()


@pytest.mark.asyncio
async def test_a_constructor_returned_is_not_taken_for_a_local_of_its_last_letters(graph, index_file) -> None:
    """`return Ok()` is an `Ok`; it was read as `=Ok` cut to `k`, and a local `k` won."""
    await _index(index_file, {
        "pkg/result.py": b'''
class Ok:
    def unwrap(self):
        pass

class Pending:
    def unwrap(self):
        pass

def make(value):
    k = Pending()
    return Ok()

def use():
    return make(1).unwrap()
''',
    })
    await _build(graph)
    targets = _targets(graph, CALLS, "pkg/result.py")
    assert "pkg/result.py::method:Ok.unwrap" in targets
    assert "pkg/result.py::method:Pending.unwrap" not in targets


@pytest.mark.asyncio
async def test_super_follows_the_enclosing_classs_mro(graph, index_file) -> None:
    """`super(A, self).ping()` in `Child(A, B)` runs `B.ping`: B comes after A in
    Child's MRO, though A itself does not inherit B. `super()` in a diamond follows the MRO too."""
    await _index(index_file, {
        "pkg/mro.py": b'''
class A:
    pass

class B:
    def ping(self):
        pass

class Child(A, B):
    def ping(self):
        return super(A, self).ping()

class Root:
    def save(self):
        pass

class Left(Root):
    pass

class Right(Root):
    def save(self):
        pass

class Leaf(Left, Right):
    def save(self):
        return super().save()
''',
    })
    await _build(graph)
    targets = _targets(graph, CALLS, "pkg/mro.py")
    assert "pkg/mro.py::method:B.ping" in targets
    assert "pkg/mro.py::method:Right.save" in targets
    assert "pkg/mro.py::method:Root.save" not in targets

"""`super.` / `super()` reach the nearest base class that defines the method, never a declared interface."""
import pytest

from app.config.constants.arangodb import RecordRelations

from .test_import_binding import _build, _targets

CALLS = RecordRelations.CALLS.value


@pytest.mark.asyncio
async def test_java_super_skips_an_interface_the_class_implements(graph, index_file) -> None:
    # The base class is outside the repo, the interface declares the method.
    await index_file("u", "app/IUnit.java", b"package app;\npublic interface IUnit { void save(); }\n", "java")
    await index_file("c", "app/Ctx.java", b"package app;\npublic class Ctx extends ExternalContext implements IUnit {\n"
                     b"  public void save() { super.save(); }\n}\n", "java")
    await _build(graph)
    assert "app/IUnit.java::method:IUnit.save" not in _targets(graph, CALLS, "app/Ctx.java")


@pytest.mark.asyncio
async def test_java_super_still_reaches_the_base_class_past_an_interface(graph, index_file) -> None:
    await index_file("u", "app/IUnit.java", b"package app;\npublic interface IUnit { void save(); }\n", "java")
    await index_file("b", "app/Base.java", b"package app;\npublic class Base { public void save() {} }\n", "java")
    await index_file("c", "app/Ctx.java", b"package app;\npublic class Ctx extends Base implements IUnit {\n"
                     b"  public void save() { super.save(); }\n}\n", "java")
    await _build(graph)
    assert "app/Base.java::method:Base.save" in _targets(graph, CALLS, "app/Ctx.java")


@pytest.mark.asyncio
async def test_python_super_still_reaches_an_abc_base(graph, index_file) -> None:
    await index_file("b", "pkg/base.py", b"from abc import ABC\n\n\nclass Base(ABC):\n    def __init__(self):\n"
                     b"        self.x = 1\n", "python")
    await index_file("c", "pkg/child.py", b"from pkg.base import Base\n\n\nclass Child(Base):\n"
                     b"    def __init__(self):\n        super().__init__()\n", "python")
    await _build(graph)
    assert "pkg/base.py::method:Base.__init__" in _targets(graph, CALLS, "pkg/child.py")

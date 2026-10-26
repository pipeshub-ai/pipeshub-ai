"""Edges the B1 benchmark found missing on real repos, end to end: parse, project, build.

django: calls and bases through a package's re-exports, `super()` calls.
excalidraw: imports across workspace packages (`@scope/name`).
gin: method calls on a typed receiver. spring-petclinic: Java imports, heritage,
implicit `this` and typed receivers. guava: a package copied into two source trees.
"""
import pytest

from app.config.constants.arangodb import RecordRelations

from .test_import_binding import _build, _package, _targets

CALLS = RecordRelations.CALLS.value
IMPORTS = RecordRelations.IMPORTS.value
IMPORTS_FROM = RecordRelations.IMPORTS_FROM.value
HERITAGE = {RecordRelations.INHERITS.value, RecordRelations.IMPLEMENTS.value}

pytestmark = pytest.mark.asyncio


def _heritage(graph, path: str) -> set[str]:
    return set().union(*(_targets(graph, rel, path) for rel in HERITAGE))


async def test_a_package_reexport_is_followed_to_the_definition(graph, index_file) -> None:
    await _package(index_file, "db", "db/models")
    await index_file("init", "db/models/__init__.py",
                     b"from db.models.base import Model\nfrom db.models.fields import CharField\n",
                     "python")
    await index_file("base", "db/models/base.py", b"class Model:\n    def save(self):\n        return 1\n",
                     "python")
    await index_file("fields", "db/models/fields.py", b"class CharField:\n    pass\n", "python")
    await index_file("app", "shop/models.py",
                     b"from db import models\nfrom db.models import Model as M\n\n\n"
                     b"class Order(models.Model):\n    name = models.CharField()\n\n\n"
                     b"def make():\n    return M()\n", "python")
    await _build(graph)
    assert "db/models/base.py::class:Model" in _heritage(graph, "shop/models.py")
    calls = _targets(graph, CALLS, "shop/models.py")
    assert {"db/models/fields.py::class:CharField", "db/models/base.py::class:Model"} <= calls
    assert "db/models/base.py::class:Model" in _targets(graph, IMPORTS, "shop/models.py")


async def test_super_calls_reach_the_base_method(graph, index_file) -> None:
    await _package(index_file, "app")
    await index_file("m", "app/m.py",
                     b"class Base:\n    def setup(self):\n        return 1\n\n\n"
                     b"class Child(Base):\n    def setup(self):\n        return super().setup()\n",
                     "python")
    await _build(graph)
    assert "app/m.py::method:Base.setup" in _targets(graph, CALLS, "app/m.py")


async def test_a_workspace_package_import_resolves_to_its_entry(graph, index_file) -> None:
    await index_file("idx", "packages/element/src/index.ts",
                     b"export { mutate } from './mutate'\n", "typescript")
    await index_file("mut", "packages/element/src/mutate.ts",
                     b"export function mutate(x: number) { return x }\n", "typescript")
    await index_file("app", "packages/app/src/app.ts",
                     b"import { mutate } from '@excalidraw/element'\n\n"
                     b"export function run() { return mutate(1) }\n", "typescript")
    await _build(graph)
    assert "packages/element/src/index.ts" in _targets(graph, IMPORTS_FROM, "packages/app/src/app.ts")
    assert "packages/element/src/mutate.ts::function:mutate" in _targets(
        graph, CALLS, "packages/app/src/app.ts")


async def test_a_scoped_npm_package_stays_external(graph, index_file) -> None:
    await index_file("ui", "packages/themes/src/index.ts", b"export function Button() {}\n", "typescript")
    await index_file("a", "packages/app/src/a.ts",
                     b"import { Button } from '@radix-ui/react'\nexport const b = Button()\n", "typescript")
    await _build(graph)
    assert not _targets(graph, IMPORTS_FROM, "packages/app/src/a.ts")


async def test_a_go_receiver_types_its_method_calls(graph, index_file) -> None:
    await index_file("ctx", "gin/context.go",
                     b"package gin\n\ntype Context struct{}\n\n"
                     b"func (c *Context) Render(code int) {}\n\n"
                     b"func (c *Context) JSON(code int) { c.Render(code) }\n", "go")
    await index_file("other", "render/render.go",
                     b"package render\n\ntype Render interface{ Render(int) }\n\n"
                     b"type JSON struct{}\n\nfunc (j JSON) Render(code int) {}\n", "go")
    await _build(graph)
    assert _targets(graph, CALLS, "gin/context.go") == {"gin/context.go::method:Context.Render"}


async def test_java_imports_heritage_and_calls(graph, index_file) -> None:
    await index_file("base", "src/main/java/org/x/model/BaseEntity.java",
                     b"package org.x.model;\npublic class BaseEntity {\n"
                     b"  public Integer getId() { return 1; }\n}\n", "java")
    await index_file("pet", "src/main/java/org/x/owner/Pet.java",
                     b"package org.x.owner;\npublic class Pet {\n  public void addVisit() {}\n}\n", "java")
    await index_file("owner", "src/main/java/org/x/owner/Owner.java",
                     b"package org.x.owner;\nimport org.x.model.BaseEntity;\n"
                     b"public class Owner extends BaseEntity {\n"
                     b"  public Pet getPet(Integer id) { return null; }\n"
                     b"  public void addVisit(Integer id) {\n"
                     b"    Pet pet = getPet(id);\n    pet.addVisit();\n    getId();\n  }\n}\n", "java")
    await _build(graph)
    owner = "src/main/java/org/x/owner/Owner.java"
    assert "src/main/java/org/x/model/BaseEntity.java::class:BaseEntity" in _heritage(graph, owner)
    assert "src/main/java/org/x/model/BaseEntity.java::class:BaseEntity" in _targets(graph, IMPORTS, owner)
    assert {"src/main/java/org/x/owner/Owner.java::method:Owner.getPet",
            "src/main/java/org/x/owner/Pet.java::method:Pet.addVisit",
            "src/main/java/org/x/model/BaseEntity.java::method:BaseEntity.getId"} <= _targets(
        graph, CALLS, owner)


async def test_a_java_import_prefers_the_copy_in_its_own_source_tree(graph, index_file) -> None:
    for root in ("guava", "android/guava"):
        await index_file(f"{root}-pre", f"{root}/src/com/x/base/Preconditions.java",
                         b"package com.x.base;\npublic class Preconditions {}\n", "java")
    await index_file("absent", "guava/src/com/x/base/Absent.java",
                     b"package com.x.base;\nimport com.x.base.Preconditions;\npublic class Absent {}\n", "java")
    await _build(graph)
    assert _targets(graph, IMPORTS, "guava/src/com/x/base/Absent.java") == {
        "guava/src/com/x/base/Preconditions.java::class:Preconditions"}

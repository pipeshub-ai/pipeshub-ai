"""The base walk prefers a method with a body over a declaration, whatever the base-list order (hasBody)."""
import pytest

from app.config.constants.arangodb import RecordRelations

from .test_import_binding import _build, _targets

CALLS = RecordRelations.CALLS.value


async def _repo(index_file, language: str, files: dict[str, bytes]) -> None:
    for i, (path, src) in enumerate(files.items()):
        await index_file(f"r{i}", path, src, language)


@pytest.mark.asyncio
async def test_java_an_interface_default_beats_an_abstract_declaration_further_along(graph, index_file) -> None:
    await _repo(index_file, "java", {
        "p/I.java": b"package p;\npublic interface I { default void m() {} }\n",
        "p/A.java": b"package p;\npublic abstract class A { public abstract void m(); }\n",
        "p/C.java": b"package p;\npublic class C extends A implements I {}\n",
        "p/Use.java": b"package p;\nclass Use { void go(C c) { c.m(); } }\n",
    })
    await _build(graph)
    assert _targets(graph, CALLS, "p/Use.java") == {"p/I.java::method:I.m"}


@pytest.mark.asyncio
async def test_csharp_an_interface_listed_first_loses_to_the_base_class_body(graph, index_file) -> None:
    await _repo(index_file, "csharp", {
        "s/IThing.cs": b"namespace S;\npublic interface IThing { void M(); }\n",
        "s/Impl.cs": b"namespace S;\npublic class Impl { public void M() {} }\n",
        "s/C.cs": b"namespace S;\npublic class C : Impl, IThing {}\n",
        "s/Use.cs": b"namespace S;\nclass Use { void Go(C c) { c.M(); } }\n",
    })
    await _build(graph)
    assert _targets(graph, CALLS, "s/Use.cs") == {"s/Impl.cs::method:S.Impl.M"}


@pytest.mark.asyncio
async def test_typescript_an_abstract_declaration_loses_to_a_body_further_along(graph, index_file) -> None:
    await _repo(index_file, "typescript", {
        "a.ts": b"export class Base { run(): void {} }\n"
                b"export abstract class Mid extends Base { abstract run(): void; }\n"
                b"export class Leaf extends Mid {}\n"
                b"export function go(l: Leaf) { l.run(); }\n",
    })
    await _build(graph)
    assert _targets(graph, CALLS, "a.ts") == {"a.ts::method:Base.run"}


@pytest.mark.asyncio
async def test_with_no_body_on_the_chain_the_declaration_is_the_target(graph, index_file) -> None:
    await _repo(index_file, "csharp", {
        "s/IThing.cs": b"namespace S;\npublic interface IThing { void M(); }\n",
        "s/C.cs": b"namespace S;\npublic abstract class C : IThing { public abstract void N(); }\n",
        "s/Use.cs": b"namespace S;\nclass Use { void Go(C c) { c.M(); c.N(); } }\n",
    })
    await _build(graph)
    assert _targets(graph, CALLS, "s/Use.cs") == {"s/IThing.cs::method:S.IThing.M", "s/C.cs::method:S.C.N"}

"""A named import through a barrel of several `export *` lines binds to the one module defining it."""
import pytest

from app.config.constants.arangodb import RecordRelations

from .test_import_binding import _build, _targets

CALLS = RecordRelations.CALLS.value
IMPORTS = RecordRelations.IMPORTS.value
BARREL = b'export * from "./random";\nexport * from "./colors";\n'
USER = (b'import { randomInteger } from "../pkg/common";\n\n'
        b"export function run() {\n  return randomInteger();\n}\n")


@pytest.mark.asyncio
async def test_a_name_resolves_through_a_barrel_of_several_modules(graph, index_file) -> None:
    await index_file("b", "pkg/common/index.ts", BARREL, "typescript")
    await index_file("r", "pkg/common/random.ts", b"export function randomInteger() { return 4; }\n", "typescript")
    await index_file("c", "pkg/common/colors.ts", b"export function color() { return 'red'; }\n", "typescript")
    await index_file("u", "app/main.ts", USER, "typescript")
    await _build(graph)
    assert _targets(graph, IMPORTS, "app/main.ts") == {"pkg/common/random.ts::function:randomInteger"}
    assert _targets(graph, CALLS, "app/main.ts") == {"pkg/common/random.ts::function:randomInteger"}


@pytest.mark.asyncio
async def test_a_name_two_modules_behind_the_barrel_define_binds_nothing(graph, index_file) -> None:
    await index_file("b", "pkg/common/index.ts", BARREL, "typescript")
    await index_file("r", "pkg/common/random.ts", b"export function randomInteger() { return 4; }\n", "typescript")
    await index_file("c", "pkg/common/colors.ts", b"export function randomInteger() { return 5; }\n", "typescript")
    await index_file("u", "app/main.ts", USER, "typescript")
    await _build(graph)
    assert _targets(graph, CALLS, "app/main.ts") == set()


@pytest.mark.asyncio
async def test_the_barrels_own_definition_comes_first(graph, index_file) -> None:
    await index_file("b", "pkg/common/index.ts",
                     BARREL + b"export function randomInteger() { return 1; }\n", "typescript")
    await index_file("r", "pkg/common/random.ts", b"export function randomInteger() { return 4; }\n", "typescript")
    await index_file("c", "pkg/common/colors.ts", b"export function color() { return 'red'; }\n", "typescript")
    await index_file("u", "app/main.ts", USER, "typescript")
    await _build(graph)
    assert _targets(graph, CALLS, "app/main.ts") == {"pkg/common/index.ts::function:randomInteger"}


@pytest.mark.asyncio
async def test_barrels_that_re_export_each_other_terminate(graph, index_file) -> None:
    await index_file("a", "pkg/a/index.ts", b'export * from "../b";\nexport * from "./x";\n', "typescript")
    await index_file("b", "pkg/b/index.ts", b'export * from "../a";\nexport * from "./y";\n', "typescript")
    await index_file("x", "pkg/a/x.ts", b"export function fromX() { return 1; }\n", "typescript")
    await index_file("y", "pkg/b/y.ts", b"export function fromY() { return 2; }\n", "typescript")
    await index_file("u", "app/main.ts",
                     b'import { fromY, missing } from "../pkg/a";\n\nexport function run() {\n'
                     b"  return fromY() + missing();\n}\n", "typescript")
    await _build(graph)
    assert _targets(graph, CALLS, "app/main.ts") == {"pkg/b/y.ts::function:fromY"}

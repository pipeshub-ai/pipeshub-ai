"""A JS/TS import of a module-level value (`export const REVISION`) is an IMPORTS edge to its definition."""
import pytest

from app.config.constants.arangodb import RecordRelations

from .test_import_binding import _build, _targets

CALLS = RecordRelations.CALLS.value
IMPORTS = RecordRelations.IMPORTS.value
CONSTANTS = b"export const REVISION = '187';\nexport const vec3 = makeNode('vec3');\nfunction makeNode(t) { return t; }\n"
USER = (b'import { REVISION, vec3 } from "../constants";\n\n'
        b"export function run() {\n  return vec3(REVISION);\n}\n")


@pytest.mark.asyncio
async def test_an_imported_constant_is_an_import_of_its_definition(graph, index_file) -> None:
    await index_file("c", "src/constants.ts", CONSTANTS, "typescript")
    await index_file("u", "src/app/main.ts", USER, "typescript")
    await _build(graph)
    assert _targets(graph, IMPORTS, "src/app/main.ts") == {
        "src/constants.ts::field:REVISION", "src/constants.ts::field:vec3"}
    # `vec3(...)` runs a value, which is no call edge; the initializer's call is module code, the file's.
    assert _targets(graph, CALLS, "src/app/main.ts") == set()
    assert _targets(graph, CALLS, "src/constants.ts") == {"src/constants.ts::function:makeNode"}


@pytest.mark.asyncio
async def test_a_constant_resolves_through_an_export_star_barrel(graph, index_file) -> None:
    await index_file("b", "src/index.js", b'export * from "./constants.js";\nexport * from "./math.js";\n',
                     "javascript")
    await index_file("c", "src/constants.js", b"export const RGBAFormat = 1023;\n", "javascript")
    await index_file("m", "src/math.js", b"export function clamp(v) { return v; }\n", "javascript")
    await index_file("u", "src/use.js", b'import { RGBAFormat } from "./index.js";\nexport { RGBAFormat };\n',
                     "javascript")
    await _build(graph)
    assert _targets(graph, IMPORTS, "src/use.js") == {"src/constants.js::field:RGBAFormat"}


@pytest.mark.asyncio
async def test_the_named_modules_own_definition_beats_its_one_re_export(graph, index_file) -> None:
    # `api.ts` re-exports one name from base.ts and defines `test` itself: `test` is api.ts's, not base.ts's.
    await index_file("b", "e2e/base.ts", b"export const test = 1;\nexport const expect = 2;\n", "typescript")
    await index_file("a", "e2e/api.ts", b'import { test as base } from "./base";\nexport const test = base;\n'
                     b'export { expect } from "./base";\n', "typescript")
    await index_file("s", "e2e/a.spec.ts", b'import { test, expect } from "./api";\n', "typescript")
    await _build(graph)
    assert _targets(graph, IMPORTS, "e2e/a.spec.ts") == {"e2e/api.ts::field:test", "e2e/base.ts::field:expect"}

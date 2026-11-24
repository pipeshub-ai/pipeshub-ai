"""A JS/TS module-level value the module exports is a `field` definition; its initializer stays module code."""
import pytest

from app.modules.parsers.code_parser.code_file_parser import parse_code_to_blocks

SOURCE = b"""import { f, g } from "./lib";
export const A = f(1);
export let B;
export var C = 3;
const D = { x: g() };
const E = 5;
const internal = 6;
export { D };
export const h = () => f(2);
export const p = 1, q = 2;
export const { r } = { r: 1 };
function inner() { const local = f(); return local + internal; }
export default E;
"""


def _blocks(source: bytes, path: str, language: str) -> dict[str, list]:
    container = parse_code_to_blocks(source, path, path, language)
    out: dict[str, list] = {}
    for block in container.blocks:
        meta = block.code_metadata
        facts = [(fact["relation"], fact["toName"]) for fact in (meta.pending_edges or [])] if meta else []
        out[meta.qualified_name if meta.qualified_name else meta.kind] = facts
    return out


@pytest.mark.parametrize("language,path", [("typescript", "m.ts"), ("javascript", "m.js"), ("tsx", "m.tsx")])
def test_exported_values_are_fields(language: str, path: str) -> None:
    names = {q for q in _blocks(SOURCE, path, language) if q.startswith("field:")}
    # D by `export { D }`, E by `export default E`; never an internal constant, a function's local,
    # a multi-name declaration or a destructured one.
    assert names == {"field:A", "field:B", "field:C", "field:D", "field:E"}


def test_a_values_initializer_calls_come_from_the_file() -> None:
    blocks = _blocks(SOURCE, "m.ts", "typescript")
    assert blocks["field:A"] == [] and blocks["field:D"] == []
    summary = blocks["file_summary"]
    assert ("CALLS", "f") in summary and ("CALLS", "g") in summary
    assert {("EXPORTS", "A"), ("EXPORTS", "B"), ("EXPORTS", "C")} <= set(summary)


def test_a_namespace_members_value_is_no_module_value() -> None:
    blocks = _blocks(b"export namespace N {\n  export const v = 1;\n}\nexport const top = 2;\n", "m.ts", "typescript")
    assert {q for q in blocks if q.startswith("field:")} == {"field:top"}
    # A block-scoped value is no module value.
    scoped = (b"function f() {\n  const B = make();\n  return B;\n}\n{\n  const C = make();\n}\n"
              b"if (ready) {\n  const D = 1;\n}\nexport { B, C, D, f };\n")
    assert not any(q.startswith("field:") for q in _blocks(scoped, "m.js", "javascript"))


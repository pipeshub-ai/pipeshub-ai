"""A deep AST must not fail the record.

`_walk_facts` used to recurse once per AST level. A long `a + b + c + ...`
or `f(f(f(...)))` chain nests one level per term, past the interpreter's frame
limit, and the RecursionError failed the whole file.
"""
import pytest

from app.modules.parsers.code_parser.engine import parse_code

DEPTH = 3000
CALL_DEPTH = 1500


def _calls(parsed) -> list[str]:
    return [
        p.to_name for p in parsed.pending
        if getattr(p.relation, "value", p.relation) == "CALLS"
    ]


@pytest.mark.parametrize(
    ("language", "source"),
    [
        ("python", "x = " + " + ".join(["1"] * DEPTH) + "\n"),
        ("javascript", "const x = " + " + ".join(["1"] * DEPTH) + ";\n"),
        ("python", "w = " + "[" * DEPTH + "]" * DEPTH + "\n"),
    ],
    ids=["py-binop", "js-binop", "py-nested-lists"],
)
def test_a_deep_expression_parses(language: str, source: str) -> None:
    parsed = parse_code(source.encode(), language)
    assert parsed.skipped_reason is None
    assert len(parsed.symbols) == 1


@pytest.mark.parametrize(
    ("language", "source"),
    [
        ("python", "def g():\n    return " + "f(" * CALL_DEPTH + "0" + ")" * CALL_DEPTH + "\n"),
        ("javascript", "function g() { return " + "f(" * CALL_DEPTH + "0" + ")" * CALL_DEPTH + "; }\n"),
    ],
    ids=["py-nested-calls", "js-nested-calls"],
)
def test_every_call_in_a_deep_chain_is_still_recorded(language: str, source: str) -> None:
    parsed = parse_code(source.encode(), language)
    assert parsed.skipped_reason is None
    assert len(_calls(parsed)) == CALL_DEPTH


def test_facts_keep_source_order() -> None:
    """The stack replaces recursion without reordering what it emits."""
    source = (
        "def g():\n"
        "    return a(b(), [c(), {'k': d()}]) + e(f())\n"
    )
    parsed = parse_code(source.encode(), "python")
    assert _calls(parsed) == ["a", "b", "c", "d", "e", "f"]

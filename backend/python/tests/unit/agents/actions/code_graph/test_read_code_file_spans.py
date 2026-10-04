"""`_file_code` returns each byte of a file once.

The blob stores a class group with its whole body *and* each method as its own
block, and a nested definition's bytes are inside its parent block's. Emitting
every stored span repeated a class once per method, charged the budget twice,
and tripped `truncated` early.
"""
from app.agents.actions.code_graph.ops import _file_code


def _item(qualified_name: str | None, kind: str, start: int, end: int, text: str | None = None) -> dict:
    return {
        "code_metadata": {
            "qualified_name": qualified_name, "kind": kind,
            "start_line": start, "end_line": end,
        },
        "data": {"text": text or f"<{qualified_name} {start}-{end}>"},
    }


RECORD = {
    "block_containers": {
        "blocks": [
            _item(None, "imports", 1, 1, "import os"),
            _item("method:Thing.run", "method", 3, 6),
            _item("method:Thing.stop", "method", 8, 10),
            _item("function:outer", "function", 11, 20),
            _item("function:outer.inner", "function", 14, 16),
        ],
        "block_groups": [
            _item("class:Thing", "class", 2, 10),
        ],
    }
}


def _read(span: tuple[int, int] | None = None, budget: int = 600) -> dict:
    return _file_code(RECORD, "src/thing.py", "conn-1", span, budget)


def test_only_outermost_spans_are_returned() -> None:
    out = _read()
    assert [b["qualified_name"] for b in out["blocks"]] == [
        None, "class:Thing", "function:outer",
    ]
    assert out["truncated"] is False


def test_every_line_is_covered_exactly_once() -> None:
    blocks = _read()["blocks"]
    covered = []
    for b in blocks:
        covered.extend(range(b["start_line"], b["end_line"] + 1))
    assert covered == sorted(set(covered))
    assert covered == list(range(1, 21))


def test_budget_is_charged_once_per_region() -> None:
    # imports (1) + class body (9) = 10 lines fit; `outer` (10) would not.
    out = _read(budget=12)
    assert [b["qualified_name"] for b in out["blocks"]] == [None, "class:Thing"]
    assert out["truncated"] is True
    assert out["next"] == (
        "Stopped at line 10 of 20. Continue with lines='11-20', or raise max_lines."
    )


def test_a_line_range_inside_a_nested_definition_returns_its_container_once() -> None:
    out = _read(span=(14, 16))
    assert [b["qualified_name"] for b in out["blocks"]] == ["function:outer"]


def test_a_line_range_inside_a_class_returns_the_class_once() -> None:
    out = _read(span=(4, 5))
    assert [b["qualified_name"] for b in out["blocks"]] == ["class:Thing"]

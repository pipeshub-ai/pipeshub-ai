"""`_file_code` returns each byte of a file once.

The blob stores a class group with its whole body *and* each method as its own
block, and a nested definition's bytes are inside its parent block's. Emitting
every stored span repeated a class once per method, charged the budget twice,
and tripped `truncated` early.
"""
from app.agents.actions.code_graph.read import _file_code


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


def _lines(start: int, end: int, prefix: str) -> str:
    return "".join(f"{prefix} {n}\n" for n in range(start, end + 1))


SOURCE_RECORD = {
    "block_containers": {
        "blocks": [
            _item(None, "imports", 1, 1, "import os\n"),
            _item("method:Thing.run", "method", 3, 6, _lines(3, 6, "run")),
            _item("method:Thing.stop", "method", 7, 10, _lines(7, 10, "stop")),
            _item("function:outer", "function", 11, 20, _lines(11, 20, "outer")),
            _item("function:outer.inner", "function", 14, 16, _lines(14, 16, "inner")),
            _item(None, "file_summary", None, None, "thing.py (python) - class:Thing"),
        ],
        "block_groups": [
            _item("class:Thing", "class", 2, 10, "class Thing:\n" + _lines(3, 6, "run")
                  + _lines(7, 10, "stop")),
        ],
    }
}


def _read_source(span: tuple[int, int] | None = None, budget: int = 600) -> dict:
    return _file_code(SOURCE_RECORD, "src/thing.py", "conn-1", span, budget)


def test_a_line_range_returns_exactly_those_lines() -> None:
    out = _read_source(span=(4, 5))
    [block] = out["blocks"]
    assert (block["start_line"], block["end_line"]) == (4, 5)
    assert block["code"] == "run 4\nrun 5"
    assert block["qualified_name"] == "method:Thing.run"
    assert out["symbols"] == ["class:Thing", "method:Thing.run"]


def test_a_line_range_inside_a_nested_definition_names_the_innermost_symbol() -> None:
    [block] = _read_source(span=(14, 16))["blocks"]
    assert block["qualified_name"] == "function:outer.inner"
    assert block["code"] == "outer 14\nouter 15\nouter 16"


def test_a_line_range_across_symbols_is_sliced_across_them() -> None:
    [block] = _read_source(span=(6, 8))["blocks"]
    assert block["code"] == "run 6\nstop 7\nstop 8"
    assert block["qualified_name"] == "class:Thing"


def test_a_line_range_is_clipped_to_the_budget() -> None:
    out = _read_source(span=(11, 20), budget=3)
    assert out["blocks"][0]["code"] == "outer 11\nouter 12\nouter 13"
    assert out["truncated"] is True
    assert out["next"] == (
        "Stopped at line 13 (max_lines=3). Continue with lines='14-20', or raise max_lines."
    )


def test_the_file_summary_is_not_returned_as_source() -> None:
    assert all(b["kind"] != "file_summary" for b in _read_source()["blocks"])


def test_a_class_that_is_only_its_header_keeps_its_body() -> None:
    record = {"block_containers": {
        "blocks": [_item("header:L1-2", "header", 1, 2, "class C(B):\n    ")],
        "block_groups": [_item("class:C", "class", 1, 2, "class C(B):\n    status = 1\n")],
    }}
    out = _file_code(record, "c.py", "conn-1", None, 600)
    assert [b["qualified_name"] for b in out["blocks"]] == ["class:C"]

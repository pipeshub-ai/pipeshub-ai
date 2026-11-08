"""impact_of_diff: a patch in, the symbols it changes and what they break out.

In the fake graph `src/b.py` holds `function:target` (lines 1-5), `class:Thing` (10-30) with `method:run` (12-20);
`src/a.py`'s `function:caller` calls target, and target inherits Thing.
"""
import pytest

from app.agents.actions.code_graph import diff_impact
from app.agents.actions.code_graph.diff_impact import impact_of_diff_impl
from app.agents.actions.code_graph.results import without_marker

from .conftest import CONN, ORG, USER, QueryGraphProvider, _block

pytestmark = pytest.mark.asyncio


def _graph(**kwargs) -> QueryGraphProvider:
    graph = QueryGraphProvider(**kwargs)
    for key, (start, end) in {"k_target": (1, 5), "k_class": (10, 30), "k_method": (12, 20)}.items():
        graph.blocks[key].update(startLine=start, endLine=end)
    return graph


def _patch(path: str, hunk: str, body: str) -> str:
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n{hunk}\n{body}"


async def _impact(patch: str, graph: QueryGraphProvider | None = None, **kwargs) -> dict:
    return without_marker(await impact_of_diff_impl(
        graph_provider=graph or _graph(), connector_id=CONN, org_id=ORG, user_id=USER, patch=patch, **kwargs))


def _changed(out: dict) -> list[tuple[str, str | None]]:
    return [(c["file_path"], c.get("qualified_name")) for c in out["changed"]]


async def test_a_changed_line_names_its_symbol_and_what_calls_it() -> None:
    out = await _impact(_patch("src/b.py", "@@ -2,3 +2,3 @@", "     a\n-    b\n+    c\n     d\n"))
    assert _changed(out) == [("src/b.py", "function:target")]
    assert out["changed"][0]["lines"] == "1-5"
    assert [f["file_path"] for f in out["impacted_files"]] == ["src/a.py"]
    assert out["impacted_files"][0]["symbols"] == ["function:caller"]


async def test_a_line_inside_a_method_is_the_method_not_its_class() -> None:
    out = await _impact(_patch("src/b.py", "@@ -15 +15 @@", "-x\n+y\n"))
    assert _changed(out) == [("src/b.py", "method:run")]


async def test_lines_added_after_a_symbols_last_line_extend_it() -> None:
    """An append to a Python function lands after its last line; context alone cannot say it left."""
    out = await _impact(_patch("src/b.py", "@@ -20,0 +21,1 @@", "+    more()\n"))
    assert _changed(out) == [("src/b.py", "method:run")]


async def test_two_changes_in_one_symbol_name_it_once() -> None:
    out = await _impact(_patch("src/b.py", "@@ -2,3 +2,3 @@", "-a\n+b\n c\n-d\n+e\n"))
    assert _changed(out) == [("src/b.py", "function:target")]


async def test_a_change_outside_every_symbol_is_a_change_to_the_file() -> None:
    out = await _impact(_patch("src/b.py", "@@ -40 +40 @@", "-x\n+y\n"))
    assert out["changed"] == [{"file_path": "src/b.py", "whole_file": True}]
    assert "src/a.py" in [f["file_path"] for f in out["impacted_files"]]


async def test_a_new_file_is_listed_and_has_no_impact_yet() -> None:
    patch = "diff --git a/src/n.py b/src/n.py\nnew file mode 100644\n--- /dev/null\n+++ b/src/n.py\n@@ -0,0 +1 @@\n+x\n"
    out = await _impact(patch)
    assert out["added_files"] == ["src/n.py"] and out["changed"] == [] and out["impacted_files"] == []


async def test_a_file_the_caller_cannot_read_reads_as_not_indexed() -> None:
    """Same words as a path that does not exist: naming its symbols would say what is in it."""
    hidden = await _impact(_patch("src/b.py", "@@ -2 +2 @@", "-a\n+b\n"), _graph(deny_records={"rec-b"}))
    missing = await _impact(_patch("src/nope.py", "@@ -2 +2 @@", "-a\n+b\n"))
    assert hidden["changed"] == missing["changed"] == []
    assert hidden["not_indexed"] == ["src/b.py"] and missing["not_indexed"] == ["src/nope.py"]
    assert hidden["impacted_files"] == []


async def test_callers_no_test_reaches_are_named() -> None:
    out = await _impact(_patch("src/b.py", "@@ -2 +2 @@", "-a\n+b\n"))
    assert out["untested_callers"] == [{"file_path": "src/a.py", "qualified_name": "function:caller"}]


async def test_a_caller_that_is_itself_a_test_is_not_untested() -> None:
    out = await _impact(_patch("src/b.py", "@@ -2 +2 @@", "-a\n+b\n"), _graph(file_roles={"rec-a": "test"}))
    assert out["untested_callers"] == []
    assert [f["file_path"] for f in out["impacted_tests"]] == ["src/a.py"]


async def test_more_changed_symbols_than_the_cap_are_counted_not_walked(monkeypatch) -> None:
    monkeypatch.setattr(diff_impact, "_MAX_SYMBOLS", 1)
    patch = _patch("src/b.py", "@@ -2,14 +2,14 @@", "-a\n+b\n" + " x\n" * 12 + "-c\n+d\n")
    out = await _impact(patch)
    assert len(out["changed"]) == 1 and out["changed_omitted"] == 1 and out["truncated"] is True


@pytest.mark.parametrize("patch", ["", "nothing to see\n"])
async def test_a_patch_with_no_file_is_an_error(patch) -> None:
    out = await _impact(patch)
    assert "error" in out and "unified diff" in out["error"]


CLASS_SRC = (b"class S:\n    def first(self):\n        return 1\n\n    def second(self, x):\n        return x\n\n"
             b"    def third(self):\n        return 3\n")


def _parsed_graph() -> QueryGraphProvider:
    """src/s.py with the parser's own spans: a member ends on the next member's first line (first 2-5, second
    5-8), because spans tile the file byte for byte and the gap before a def is the previous member's."""
    from app.modules.parsers.code_parser.engine import parse_code

    from .conftest import _block

    g = _graph()
    g.code_files["rec-s"] = "src/s.py"
    g.records["rec-s"] = {"_key": "rec-s", "orgId": ORG, "connectorId": CONN, "virtualRecordId": "v-s",
                          "recordGroupId": "repo-1", "recordType": "CODE_FILE"}
    for sym in parse_code(CLASS_SRC, "python").symbols:
        if sym.kind == "method":
            key = f"k_{sym.name}"
            g.blocks[key] = {**_block(key, "rec-s", "src/s.py", key, f"S.{sym.name}", "method"),
                             "startLine": sym.start_line, "endLine": sym.end_line}
            g.blocks[key].pop("filePath")
    return g


async def test_a_change_on_a_members_def_line_is_that_member_not_the_one_before() -> None:
    """Line 5 is both first's last line (its trailing gap) and second's def line; a signature edit there
    is the most breaking change a PR makes, and belongs to second."""
    out = await _impact(_patch("src/s.py", "@@ -5 +5 @@", "-    def second(self, x):\n+    def second(self, x, y):\n"),
                        _parsed_graph())
    assert _changed(out) == [("src/s.py", "method:S.second")]


async def test_a_line_inserted_right_after_a_members_def_line_is_that_member() -> None:
    out = await _impact(_patch("src/s.py", "@@ -5,0 +6,1 @@", "+        x = x or 0\n"), _parsed_graph())
    assert _changed(out) == [("src/s.py", "method:S.second")]


async def test_a_mode_only_change_walks_nothing() -> None:
    """A chmod changes no line, so it changes no symbol and breaks nothing."""
    patch = "diff --git a/src/b.py b/src/b.py\nold mode 100644\nnew mode 100755\n"
    out = await _impact(patch)
    assert out["changed"] == [] and out["impacted_files"] == [] and out["mode_only"] == ["src/b.py"]


async def test_the_result_warns_that_lines_are_read_against_the_indexed_commit() -> None:
    out = await _impact(_patch("src/b.py", "@@ -2 +2 @@", "-a\n+b\n"))
    assert "indexed commit" in out["note"]


async def test_callers_past_the_check_cap_are_counted_not_called_tested(monkeypatch) -> None:
    monkeypatch.setattr(diff_impact, "_MAX_UNTESTED_CHECKS", 0)
    out = await _impact(_patch("src/b.py", "@@ -2 +2 @@", "-a\n+b\n"))
    assert out["untested_callers"] == []
    assert out["untested_unchecked"] == 1 and out["truncated"] is True


async def test_a_failed_walk_is_named_and_its_caller_is_not_called_untested(monkeypatch) -> None:
    """A symbol re-indexed away between the block read and its walk: the failure is reported, not dropped, and a
    caller whose check failed is unchecked, not untested."""
    real = diff_impact.impact_impl

    async def flaky(**kwargs) -> dict:
        if kwargs["qualified_name"] == "function:caller" or kwargs.get("depth") != 1:
            return {"error": "No symbol 'x' in this file."}
        return await real(**kwargs)

    monkeypatch.setattr(diff_impact, "impact_impl", flaky)
    out = await _impact(_patch("src/b.py", "@@ -2 +2 @@", "-a\n+b\n"))
    assert out["failed_walks"] == [{"file_path": "src/b.py", "qualified_name": "function:target",
                                    "error": "No symbol 'x' in this file."}]
    assert out["untested_callers"] == [] and out["untested_unchecked"] == 1 and out["truncated"] is True


async def test_a_failed_caller_lookup_is_named_not_dropped(monkeypatch) -> None:
    """When the direct-caller lookup of a changed symbol fails, its callers are unknown: say so."""
    real = diff_impact.impact_impl

    async def no_callers(**kwargs) -> dict:
        if kwargs.get("edge_types") == ["CALLS"] and kwargs.get("depth") == 1:
            return {"error": "Caller lookup failed."}
        return await real(**kwargs)

    monkeypatch.setattr(diff_impact, "impact_impl", no_callers)
    out = await _impact(_patch("src/b.py", "@@ -2 +2 @@", "-a\n+b\n"))
    assert out["failed_walks"] == [{"file_path": "src/b.py", "qualified_name": "function:target",
                                    "error": "Caller lookup failed."}]
    assert out["untested_callers"] == [] and out["truncated"] is True


def _with_filler(graph: QueryGraphProvider) -> QueryGraphProvider:
    """src/b.py's imports (6-7) and a module-level constant (8-9), between target (1-5) and Thing (10-30)."""
    for key, kind, (start, end) in (("k_imports", "imports", (6, 7)), ("k_consts", "statements", (8, 9))):
        graph.blocks[key] = {**_block(key, "rec-b", "src/b.py", f"src_b_{key}", f"@{key}", kind=kind),
                             "startLine": start, "endLine": end}
    return graph


async def test_a_module_level_change_beside_a_symbol_also_walks_the_whole_file() -> None:
    """A changed constant is read by whatever uses the file, not only by the symbol changed beside it."""
    out = await _impact(_patch("src/b.py", "@@ -2,8 +2,8 @@", "-a\n+b\n" + " x\n" * 6 + "-C = 1\n+C = 2\n"),
                        _with_filler(_graph()))
    assert _changed(out) == [("src/b.py", "function:target"), ("src/b.py", None)]
    assert out["changed"][1]["whole_file"] is True


async def test_an_import_change_beside_a_symbol_is_not_a_whole_file_change() -> None:
    """A file's imports are what it uses, not what uses it: the symbol's walk is the whole answer."""
    out = await _impact(_patch("src/b.py", "@@ -2,6 +2,6 @@", "-a\n+b\n" + " x\n" * 4 + "-import os\n+import re\n"),
                        _with_filler(_graph()))
    assert _changed(out) == [("src/b.py", "function:target")]

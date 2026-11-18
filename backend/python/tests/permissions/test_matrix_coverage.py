"""The coverage matrix: every connector classified, evidence real, MATRIX.md current, gaps reported.

Gaps are reported (xfail) for now. TODO: drop the xfail once the corpus covers every cell, so a gap fails CI.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from . import matrix
from .scenario import SCENARIOS_ROOT, load_all

BACKEND = Path(__file__).resolve().parents[2]
SOURCES = BACKEND / "app" / "connectors" / "sources"
SCENARIOS = load_all()


def _connector_dirs() -> set[str]:
    return {str(p.parent.relative_to(SOURCES)) for p in SOURCES.rglob("connector.py")}


def test_every_connector_is_classified() -> None:
    classified = {c.path for c in matrix.CONNECTORS}
    assert _connector_dirs() - classified == set(), "add the new connector to matrix.CONNECTORS"
    assert classified - _connector_dirs() == set(), "a classified connector no longer exists"


def test_evidence_still_holds() -> None:
    assert matrix.evidence_problems(SOURCES) == []


def test_every_connector_has_an_anchor() -> None:
    assert {c.path for c in matrix.CONNECTORS} == set(matrix.ANCHORS)


_WRITER = """\
@register(model="RECORD_LEVEL")
class Sample:
    def write(self, record):
        record.inherit_permissions = False

    def other(self, record):
        pass
"""
_MOVED = """\
@register(model="RECORD_LEVEL")
class Sample:
    def write(self, record):
        pass

    def other(self, record):
        record.inherit_permissions = False
"""
_SAMPLE = (matrix.Connector("sample", "item-acl", matrix.OVERRIDE, "sample/connector.py::Sample.write"),
           matrix.Connector("sample_app", "container", matrix.CONTAINER, "sample/connector.py::Sample"))


@pytest.mark.parametrize(("source", "failing"), [
    (_WRITER, []),
    ("import os\n\n\n" + _WRITER, []),                                        # an unrelated edit shifts every line
    (_WRITER.replace("record.inherit_permissions = False", "pass"), ["sample"]),
    (_WRITER.replace("def write", "def write_all"), ["sample"]),
    (_MOVED, ["sample"]),
    (_WRITER.replace("RECORD_LEVEL", "APP_LEVEL"), ["sample_app"]),            # a class's decorators are part of it
    (_WRITER.replace("        record.inherit_permissions = False",
                     "        def helper():\n            record.inherit_permissions = False"), ["sample"]),
    (_WRITER.replace("        record.inherit_permissions = False",
                     "        # record.inherit_permissions = False\n        pass"), ["sample"]),
    ("# a comment with a line separator \u2028 in it\n" + _WRITER, []),       # ast counts only \n as a line break
    (_WRITER.replace("        record.inherit_permissions = False", "        pass  # record.inherit_permissions = False"), ["sample"]),
    (_WRITER.replace("        record.inherit_permissions = False", '        """record.inherit_permissions = False"""'), ["sample"]),
    (_WRITER + "\n    def write(self, record):\n        pass\n", ["sample"]),        # a later def replaces the cited one
], ids=["intact", "lines-shifted", "code-deleted", "function-renamed", "moved-to-another-function", "decorator-changed",
        "moved-into-a-nested-function", "commented-out", "unicode-line-separator", "trailing-comment", "docstring",
        "redefined"])
def test_a_citation_holds_only_while_its_function_holds_the_anchor(tmp_path: Path, monkeypatch, source, failing) -> None:
    (tmp_path / "sample").mkdir()
    (tmp_path / "sample" / "connector.py").write_text(source, encoding="utf-8")
    monkeypatch.setitem(matrix.ANCHORS, "sample", "record.inherit_permissions = False")
    monkeypatch.setitem(matrix.ANCHORS, "sample_app", 'model="RECORD_LEVEL"')
    assert [p.split(":")[0] for p in matrix.evidence_problems(tmp_path, _SAMPLE)] == failing


def test_the_resolved_line_follows_the_code(tmp_path: Path) -> None:
    path = tmp_path / "connector.py"
    path.write_text(_WRITER)
    assert matrix.cited_line(path, "Sample.write", "inherit_permissions = False") == 4
    path.write_text("import os\n\n\n" + _WRITER)
    assert matrix.cited_line(path, "Sample.write", "inherit_permissions = False") == 7


def test_every_connector_group_exists() -> None:
    assert {c.group for c in matrix.CONNECTORS} <= set(matrix.GROUPS)


def test_scenario_tags_name_known_cells() -> None:
    assert matrix.unknown_cells(SCENARIOS) == []


def test_matrix_file_is_current() -> None:
    committed = (SCENARIOS_ROOT / "MATRIX.md").read_text()
    assert committed == matrix.render(SCENARIOS), "run: python -m tests.permissions.matrix"


def test_coverage_gaps_are_reported() -> None:
    gaps = matrix.gaps(SCENARIOS)
    if gaps:
        pytest.xfail(f"{len(gaps)} matrix cells have no scenario yet (to become a failure once the corpus is complete); first: {gaps[:3]}")


def test_a_scenario_with_two_tags_in_one_category_counts_once() -> None:
    from .scenario import WorldStore, scenario_from_data

    group, cat, kinds = next(
        (g, cat, ks) for g in matrix.GROUPS for cat in matrix.CATEGORY
        if len(ks := [k for _, k in matrix.cell_ids(g) if matrix.KIND_CATEGORY.get(k) == cat]) >= 2
    )
    world = {"principals": [{"id": "u"}], "nodes": [{"id": "A", "kind": "app"}]}
    s = scenario_from_data({"id": "ONE", "title": "t", "world": world, "tags": [f"cell:{group}/{k}" for k in kinds[:2]]},
                           WorldStore())
    text = matrix.render([s])
    lines = text[text.index("## Connector x change kind"):text.index("## Cells by group")].splitlines()
    header = [h.strip() for h in next(ln for ln in lines if ln.startswith("| Connector | ")).split("|")[1:-1]]
    connector = next(c for c in matrix.CONNECTORS if c.group == group)
    row = next(ln for ln in lines if ln.startswith(f"| `{connector.path}` |"))
    assert [x.strip() for x in row.split("|")[1:-1]][header.index(cat)] == "1"

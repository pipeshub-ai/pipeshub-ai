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


def test_deleting_a_cited_line_is_caught(tmp_path: Path) -> None:
    """The guard has teeth: on a copy of the sources, delete each cited line and the citation must fail."""
    for c in (x for x in matrix.CONNECTORS if x.path in ("box", "microsoft/sharepoint_online", "linear")):
        file, _, at = c.evidence.rpartition(":")
        root = tmp_path / c.path.replace("/", "_")
        copy = root / file
        copy.parent.mkdir(parents=True)
        text = (SOURCES / file).read_text().splitlines()
        copy.write_text("\n".join(text))
        assert matrix.evidence_problems(root, [c]) == [], f"{c.path}: an intact copy passes"
        lo = max(0, int(at) - 1 - 3)
        copy.write_text("\n".join(text[:lo] + text[int(at) + 3:]))  # the cited line and its neighbours are gone
        assert matrix.evidence_problems(root, [c]), f"{c.path}: the deletion was not caught"


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

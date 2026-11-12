"""The Knowledge Hub layer's whole-tree browse finds what it is meant to find: fed store answers that are wrong in each way it checks, it raises
each class (`browse_extra`, `arrow`, `stub`, `browse_gap`, a row listed twice, a total that does not count the rows), and it counts the rows it
leaves unopened past its cap instead of dropping them silently. No store: `browse_pages` is replaced by canned answers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import yaml

from . import kh_layer
from .graph import Namespace
from .runner import OracleLayer
from .scenario import world_from_data

WORLD = world_from_data(yaml.safe_load("""
principals: [{id: u}]
nodes:
  - {id: A, kind: app, app_type: DRIVE}
  - {id: f, kind: group, connector: A, rule: OPEN, parents: [A], belongs: [A]}
  - {id: r1, kind: record, connector: A, rule: OPEN, parents: [f], inherits: [f], belongs: [f]}
  - {id: r2, kind: record, connector: A, rule: OPEN, parents: [f], inherits: [f], belongs: [f]}
  - {id: s, kind: record, connector: A, rule: OPEN, placeholder: true, parents: [f], inherits: [f], belongs: [f]}
  - {id: x, kind: group, connector: A, rule: OPEN, parents: [A], belongs: [A]}
grants: [[u, f]]
gates: [[u, A]]
"""))


def _item(node_id: str, node_type: str = "record", *, arrow: bool = False, stub: bool = False) -> SimpleNamespace:
    return SimpleNamespace(id=node_id, nodeType=node_type, hasChildren=arrow, isPlaceholder=stub)


class _Layer:
    async def _ns(self, _world) -> Namespace:
        return Namespace("t")


async def _browse(monkeypatch, answers: dict) -> list[kh_layer.KhFinding]:
    async def browse_pages(_layer, _world, _viewer, node, _parent_type, _page_size=kh_layer.PAGE) -> tuple | None:
        return answers.get(node, ([], [0], [0]))
    monkeypatch.setattr(kh_layer, "browse_pages", browse_pages)
    reference = OracleLayer()
    return await kh_layer._browse(_Layer(), reference, WORLD, "u", kh_layer.visible(reference, WORLD, "u"), "self")


async def test_a_right_store_gives_no_finding(monkeypatch) -> None:
    rows_f = [_item("r1"), _item("r2")]
    assert await _browse(monkeypatch, {
        "A": ([_item("f", "recordGroup", arrow=True)], [1], [1]),
        "f": (rows_f, [2], [2]),
    }) == []


@pytest.mark.parametrize("wrong, kind, detail", [
    ({"A": ([_item("f", "recordGroup", arrow=True), _item("x", "recordGroup")], [2], [2])},
     "browse_extra", "A: lists x, which the oracle does not let the viewer open"),
    ({"f": ([_item("r1", arrow=True), _item("r2")], [2], [2])}, "arrow", "f: r1 hasChildren True, opening it lists something: False"),
    ({"f": ([_item("r1"), _item("r2"), _item("s", stub=True)], [3], [3])}, "stub", "f: lists the stub s, and opening it lists nothing"),
    ({"f": ([_item("r1")], [1], [1])}, "browse_gap", "f: the browse leaves out r2, which the viewer may open"),
    ({"f": None}, "browse_gap", "f: the browse does not open it"),
    ({"f": ([_item("r1"), _item("r2"), _item("r1")], [3], [3])}, "browse", "f: lists r1 more than once"),
    ({"f": ([_item("r1"), _item("r2")], [3], [2])}, "browse", "f: totalItems per page [3], counts.total [2], rows 2"),
])
async def test_each_wrong_answer_raises_its_class(monkeypatch, wrong: dict, kind: str, detail: str) -> None:
    answers = {"A": ([_item("f", "recordGroup", arrow=True)], [1], [1]), "f": ([_item("r1"), _item("r2")], [2], [2]), **wrong}
    found = await _browse(monkeypatch, answers)
    assert (kind, detail) in {(f.kind, f.detail) for f in found}, found


async def test_the_rows_left_unopened_past_the_cap_are_counted(monkeypatch) -> None:
    monkeypatch.setattr(kh_layer, "MAX_OPENS", 2)
    found = await _browse(monkeypatch, {
        "A": ([_item("f", "recordGroup", arrow=True)], [1], [1]),
        "f": ([_item("r1"), _item("r2")], [2], [2]),
    })
    # A and f, and the stub s opened to see whether leaving it out was right; r1 and r2 are counted, not opened
    assert [(f.kind, f.detail) for f in found] == [("truncated", "opened 3 nodes (the cap); 2 listed rows not opened")]

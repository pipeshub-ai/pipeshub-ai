"""The explanation walk's own behaviour over hand-built nodes, with no store."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from types import SimpleNamespace

import pytest

from app.services.permissions import explain as ex
from app.services.permissions.ports import StampNode


def _facts(nodes: list[StampNode]) -> ex._Facts:
    return ex._Facts({n.id: n for n in nodes}, frozenset(), frozenset({"A"}), "A", False)


def _n(i: str, kind: str, parents: tuple[str, ...] = (), inherits: tuple[str, ...] = (), **kw: object) -> StampNode:
    return StampNode(i, kind, "A", parents, frozenset(inherits), **kw)  # type: ignore[arg-type]


#: Two ancestors at the same depth that fail differently: `a` is restricted, `b` is deleted.
TWO_FAILURES = [
    _n("A", "app"),
    _n("r", "record", ("a", "b"), ("a", "b")),
    _n("a", "group", ("A",), rule="OPEN"),
    _n("b", "group", ("A",), ("A",), deleted=True),
]
#: `a` (the lowest id) dies at a declared-readers group; `b` fails its own hop, which is the reason named.
TWO_PATHS = [
    _n("A", "app"),
    _n("r", "record", ("a", "b"), ("a", "b")),
    _n("a", "group", ("g",), ("g",)),
    _n("g", "group", ("A",), ("A",), group_level=True),
    _n("b", "group", ("A",), rule="OPEN"),
]


def test_the_code_does_not_depend_on_the_hash_order_of_the_walk() -> None:
    script = textwrap.dedent("""
        from tests.permissions.test_explain_walk import TWO_FAILURES, _facts
        from app.services.permissions import explain as ex
        f = _facts(TWO_FAILURES)
        w = ex._walk(f, f.nodes["r"], f.nodes["A"])
        print(w.code, w.node_id)
    """)
    seen = {
        subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True,
                       env={"PYTHONHASHSEED": str(seed), "PYTHONPATH": ":".join(sys.path)}).stdout.strip()
        for seed in range(12)
    }
    assert seen == {f"{ex.RESTRICTED_CONTAINER} a"}


def test_the_path_runs_through_the_container_the_reason_names() -> None:
    f = _facts(TWO_PATHS)
    w = ex._walk(f, f.nodes["r"], f.nodes["A"])
    assert (w.code, w.node_id) == (ex.RESTRICTED_CONTAINER, "b")
    assert "b" not in ex.ExplainService._chain(f, f.nodes["r"])
    assert ex.ExplainService._chain(f, f.nodes["r"], "b") == ["A", "b"]
    assert ex.ExplainService._chain(f, f.nodes["r"], None) == ["A", "g", "a"]


async def test_nothing_to_name_asks_nothing_about_the_asking_admin() -> None:
    def refuse(*_: object) -> None:
        raise AssertionError("the asking admin was looked up")

    service = ex.ExplainService.__new__(ex.ExplainService)
    service._principals = SimpleNamespace(principal=refuse, readable=refuse)  # type: ignore[assignment]
    assert await service._names("org", "admin", _facts([]), []) == {}


@pytest.mark.parametrize("ids", [["a"]])
async def test_something_to_name_does_ask(ids: list[str]) -> None:
    async def principal(*_: object) -> str:
        raise RuntimeError("asked")

    service = ex.ExplainService.__new__(ex.ExplainService)
    service._principals = SimpleNamespace(principal=principal)  # type: ignore[assignment]
    with pytest.raises(RuntimeError, match="asked"):
        await service._names("org", "admin", _facts(TWO_PATHS), ids)

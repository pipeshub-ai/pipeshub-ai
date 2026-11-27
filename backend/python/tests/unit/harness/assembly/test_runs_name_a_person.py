"""Every durable attempt reaches its run through one place, which refuses a run that names no person when the edition
runs agents only as the identified caller (``app.edition.AGENTS_RUN_AS_THE_CALLER``).

A first claim, a resumed attempt, a scheduled run and a child run are all claims: both assemblers (the native loop's
and the coding engines') read the run from ``RunContainer.run_context``, which ``_run_context`` builds. The scan fails
when code anywhere else in ``app/`` builds a run from a claim or a container of its own, which would skip the check.
"""

from __future__ import annotations

import ast
import functools
from pathlib import Path

import pytest

from app import edition
from app.harness.assembly.assembler import _run_context
from app.harness.assembly.context import PIPESHUB
from harness_core.driver import RunRefused
from tests.unit.harness.assembly.world import claim, pinned_spec

APP = Path(__file__).resolve().parents[4] / "app"


_NAMES = ("from_claim", "RunContainer")


@functools.cache
def _callers() -> dict[str, frozenset[tuple[str, str]]]:
    """For each name in ``_NAMES``: (file under app/, enclosing function) of every call whose callee ends in it."""
    found: dict[str, set[tuple[str, str]]] = {name: set() for name in _NAMES}
    for path in APP.rglob("*.py"):
        text = path.read_text(encoding="utf-8-sig")
        if not any(name in text for name in _NAMES):
            continue
        for fn in ast.walk(ast.parse(text)):
            if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for node in ast.walk(fn):
                if isinstance(node, ast.Call):
                    callee = node.func
                    called = callee.attr if isinstance(callee, ast.Attribute) else getattr(callee, "id", None)
                    if called in found:
                        found[called].add((path.relative_to(APP).as_posix(), fn.name))
    return {name: frozenset(sites) for name, sites in found.items()}


def test_a_run_is_built_from_a_claim_only_where_the_person_is_checked() -> None:
    assert _callers()["from_claim"] == {("harness/assembly/assembler.py", "_run_context")}
    assert _callers()["RunContainer"] == {("harness/assembly/assembler.py", "container")}


def test_the_check_comes_first() -> None:
    tree = ast.parse((APP / "harness/assembly/assembler.py").read_text(encoding="utf-8"))
    (fn,) = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_run_context"]
    called = [
        n.func.id if isinstance(n.func, ast.Name) else getattr(n.func, "attr", "")
        for n in ast.walk(fn) if isinstance(n, ast.Call)
    ]
    assert called.index("refuse_unidentified_run") < called.index("offer")


def _claim(via: str):  # noqa: ANN202 - a NodeClaim
    spec = pinned_spec(tool_ids=[])
    spec.extensions[PIPESHUB]["via"] = via
    return claim(spec)


def test_a_service_run_is_refused_where_agents_run_as_the_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(edition, "AGENTS_RUN_AS_THE_CALLER", True)
    with pytest.raises(RunRefused) as refused:
        _run_context(_claim("service"))
    assert refused.value.error_type == "unidentified_caller"
    assert _run_context(_claim("session")).via == "session"


def test_with_the_switch_off_a_service_run_still_builds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(edition, "AGENTS_RUN_AS_THE_CALLER", False)
    assert _run_context(_claim("service")).via == "service"

"""MG-15 (C3.68) guard: no harness path builds a model route around the placement check.

Every model a harness run calls comes from one of two funnels, and each applies the org's region pin:
- a run's own routes come from a ``RunPolicy`` that only ``RunPolicies.for_run`` constructs, and it hands every one
  through the ``RunModelPlacement`` extension point (asserted by ``test_placement_ports.py`` RSP-5);
- an engine's calls go through the model gateway, whose only resolver over the org's models is the
  ``ConfiguredModelAccess`` built inside ``guarded_access``, behind ``GuardedModelAccess``.

So a model client, a run policy or a route resolver built anywhere else under ``app/harness`` (or the model
gateway's entry point) would be a path around the placement check. The single transport funnel planned for the run's
transports (WP-49/WP-55) composes with this: it consumes routes these funnels resolve.
"""

from __future__ import annotations

import ast
from typing import Final

from tests.cohesion.support import PYTHON_ROOT, modules

#: builder -> the modules (and the function within) that may call it.
ALLOWED: Final = {
    # The run's model from its pin, or from its modelKey for a run admitted without one.
    "resolved_model": {"app/harness/run_policy/pinned.py::_build", "app/harness/unpinned.py::resolve_model"},
    "get_generator_model_async": {
        "app/harness/run_policy/pinned.py::_build",
        # The gateway's transport for a configured model, behind ``GuardedModelAccess``.
        "app/harness/access/configured.py::build_configured_transport",
    },
    "get_llm_for_chat": {"app/harness/unpinned.py::resolve_model"},
    "ConfiguredModelAccess": {"app/model_gateway_main.py::guarded_access"},
    "PinnedRunPolicy": {"app/harness/run_policy/select.py::_policy"},
    "UnpinnedRunPolicy": {"app/harness/run_policy/select.py::_policy"},
    "StoredRunPolicy": {"app/harness/run_policy/select.py::_policy"},
}


def _calls() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {name: set() for name in ALLOWED}
    for path in [*modules("app/harness"), PYTHON_ROOT / "app" / "model_gateway_main.py"]:
        tree = ast.parse(path.read_text(), filename=str(path))
        where = str(path.relative_to(PYTHON_ROOT))
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(function):
                if not isinstance(node, ast.Call):
                    continue
                name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", None)
                if name in found:
                    found[name].add(f"{where}::{function.name}")
    return found


def test_mg15_every_model_route_is_built_inside_a_placement_funnel() -> None:
    calls = _calls()
    outside = {name: sorted(sites - ALLOWED[name]) for name, sites in calls.items() if sites - ALLOWED[name]}
    assert outside == {}, f"a model route built around the placement check (MG-15): {outside}"


def test_mg15_the_funnels_are_still_where_this_guard_looks() -> None:
    calls = _calls()
    missing = {name: sorted(ALLOWED[name] - sites) for name, sites in calls.items() if ALLOWED[name] - sites}
    assert missing == {}, f"a funnel moved: update the guard with it ({missing})"


def test_mg15_no_production_assembler_takes_a_model_from_outside_its_policy() -> None:
    """``model_factory`` is the assemblers' test seam: a model it hands over never met a run policy."""
    passed = [
        f"{path.relative_to(PYTHON_ROOT)}:{node.lineno}"
        for path in modules("app")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig")))
        if isinstance(node, ast.Call) and any(k.arg == "model_factory" and not _is_none(k.value) for k in node.keywords)
    ]
    assert passed == []


def _is_none(value: ast.expr) -> bool:
    return isinstance(value, ast.Constant) and value.value is None


def test_mg15_the_gateway_resolver_is_guarded() -> None:
    source = (PYTHON_ROOT / "app" / "model_gateway_main.py").read_text()
    tree = ast.parse(source)
    (guarded,) = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "guarded_access"]
    built = [n for n in ast.walk(guarded) if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "GuardedModelAccess"]
    assert built and any(
        isinstance(arg, ast.Call) and getattr(arg.func, "id", None) == "ConfiguredModelAccess" for arg in built[0].args
    ), "ConfiguredModelAccess is resolved only behind GuardedModelAccess"

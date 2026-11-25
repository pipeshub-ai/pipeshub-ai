"""C-1: a public type is defined once across the harness packages (EXECUTION-PLAN §4).

Work packages built side by side each brought their own copy of shared types
(E5's ``ObjectRef`` and sandbox ports beside P3.2's and P5.1's; two
``IsolationUnavailable``). Two definitions of one concept drift apart, and an
``except`` or ``isinstance`` on one silently misses the other.
"""

from __future__ import annotations

from collections import defaultdict

from tests.cohesion.support import PYTHON_ROOT, modules, top_level_classes

# Same name, different concepts, each with the work package that resolves it.
KNOWN_HOMONYMS: dict[str, str] = {
    "ApprovalPolicy": (
        "kernel/ports/approval.py is the legacy loop's approval-store enum; effects/types.py is the "
        "durable approval port. P2.6c (interactions) retires or renames the legacy one."
    ),
    "RunContext": (
        "kernel/core/context.py is the legacy loop's run context; app/harness/assembly/context.py is the "
        "durable assembler's per-run view of PipesHub. Kept apart until the legacy loop is retired."
    ),
}


def _definitions() -> dict[str, list[str]]:
    found: dict[str, list[str]] = defaultdict(list)
    for path in modules("harness_core", "harness_engines", "app/harness"):
        for node in top_level_classes(path):
            if not node.name.startswith("_"):
                found[node.name].append(str(path.relative_to(PYTHON_ROOT)))
    return found


def test_every_public_class_is_defined_once() -> None:
    duplicates = {
        name: places for name, places in _definitions().items() if len(places) > 1 and name not in KNOWN_HOMONYMS
    }
    assert duplicates == {}, f"defined more than once (move to one module and re-export): {duplicates}"


def test_known_homonyms_are_still_homonyms() -> None:
    stale = [name for name in KNOWN_HOMONYMS if len(_definitions().get(name, [])) < 2]
    assert stale == [], f"resolved; remove from KNOWN_HOMONYMS: {stale}"


def test_shared_types_have_one_home() -> None:
    from app.harness.contracts import workspace as app_workspace
    from harness_core import driver, sandbox, workspace
    from harness_core.sandbox import errors as sandbox_errors
    from harness_core.workspace import types as workspace_types

    assert app_workspace.ObjectRef is workspace.ObjectRef is workspace_types.ObjectRef
    assert app_workspace.ObjectScope is workspace_types.ObjectScope
    assert driver.IsolationUnavailable is sandbox.IsolationUnavailable is sandbox_errors.IsolationUnavailable

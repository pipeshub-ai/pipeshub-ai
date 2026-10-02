"""Domain, "anyone" and "anyone with the link" shares stay off: nothing starts writing them.

Search honours an ``anyone`` document for a file (org-wide read access), so the
product decision to ignore link-style shares holds only while nothing writes
those documents and the permission step keeps ignoring these share types. This
pins both statically; tests/integration/test_link_and_domain_shares_e2e.py
checks the outcome on real graphs.
"""

from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[4] / "app"

ANYONE_WRITERS = {
    "process_file_permissions",
    "batch_upsert_anyone",
    "batch_upsert_anyone_with_link",
    "batch_upsert_anyone_same_org",
    "create_anyone",
    "create_anyone_with_link",
    "create_anyone_same_org",
}
# Where the writers are defined and passed through; calling them from here is not a use.
STORE_LAYERS = ("services/graph_db/", "connectors/core/base/data_store/")
LINK_STYLE = {"DOMAIN", "ANYONE", "ANYONE_WITH_LINK"}


def _calls(tree: ast.AST) -> list[tuple[int, str]]:
    """Every call's name: ``x.writer(...)`` and a bare, imported ``writer(...)``."""
    calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                calls.append((node.lineno, node.func.attr))
            elif isinstance(node.func, ast.Name):
                calls.append((node.lineno, node.func.id))
    return calls


def test_no_production_code_writes_anyone_documents() -> None:
    callers = []
    for path in APP.rglob("*.py"):
        relative = path.relative_to(APP).as_posix()
        if relative.startswith(STORE_LAYERS):
            continue
        for line, name in _calls(ast.parse(path.read_text(encoding="utf-8-sig"))):
            if name in ANYONE_WRITERS:
                callers.append(f"app/{relative}:{line} calls {name}")
    assert not callers, (
        "Link-style shares are off by product decision, and search grants org-wide "
        f"access to any file with an anyone document. New callers: {callers}"
    )


def test_the_permission_step_ignores_link_style_share_types() -> None:
    path = APP / "connectors/core/base/data_processor/data_source_entities_processor.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    handled = [
        f"line {node.lineno}: EntityType.{node.attr}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "EntityType"
        and node.attr in LINK_STYLE
    ]
    assert not handled, (
        "The permission step now handles a link-style share type, which would start "
        f"granting access that is off by product decision: {handled}"
    )

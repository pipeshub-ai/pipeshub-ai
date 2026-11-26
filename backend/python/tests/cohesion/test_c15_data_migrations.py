"""C-15, Python half (P2.2): every harness data migration is registered, reports before it writes, and reverts.

Node's half (``tests/cohesion/c15-index-migrations.test.ts``) keeps every Mongo index in a registered
migration. Here: a class under ``app/harness`` that rewrites stored documents (it defines ``apply``
and ``revert``) is in ``HARNESS_DATA_MIGRATIONS`` under its own ``id``, has ``dry_run``, ``apply`` and
``revert`` coroutines and ``run(mode)``, and keeps a ledger under ``/migrations/``.
"""

from __future__ import annotations

import ast
import inspect

from app.harness.migrations import HARNESS_DATA_MIGRATIONS
from app.harness.tool_identity.migration import MIGRATION_ID
from tests.cohesion.support import PYTHON_ROOT, modules


def _migration_classes() -> set[str]:
    found: set[str] = set()
    for path in modules("app/harness"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                methods = {m.name for m in node.body if isinstance(m, ast.AsyncFunctionDef)}
                if {"apply", "revert"} <= methods:
                    found.add(f"{path.relative_to(PYTHON_ROOT)}:{node.name}")
    return found


def test_c15_every_data_migration_class_is_registered() -> None:
    registered = {
        f"{inspect.getsourcefile(cls).split('backend/python/', 1)[1]}:{cls.__name__}"  # type: ignore[union-attr]
        for cls in HARNESS_DATA_MIGRATIONS.values()
    }
    assert _migration_classes() == registered


def test_c15_each_migration_reports_writes_and_reverts() -> None:
    assert MIGRATION_ID in HARNESS_DATA_MIGRATIONS
    for migration_id, cls in HARNESS_DATA_MIGRATIONS.items():
        assert cls.id == migration_id
        for name in ("dry_run", "apply", "revert", "run"):
            assert inspect.iscoroutinefunction(getattr(cls, name)), f"{migration_id}.{name}"
        assert cls.ledger_path.startswith("/migrations/"), migration_id
        assert inspect.signature(cls.run).parameters["mode"].annotation in ("Mode", "Literal['dry_run', 'apply', 'revert']")

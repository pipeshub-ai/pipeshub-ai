"""The knowledge-graph tools never read a platform flag themselves.

The request's `KnowledgeGraphPolicy` is decided once, before tools load, and
stored on the tool state; a tool that read a flag again could disagree with
the tool list and prompt the same request was given (a flag saved in Labs
mid-run, or a read that failed once and not the other time).
"""

from __future__ import annotations

import ast
from pathlib import Path

import app.agents.actions.knowledge_graph as knowledge_graph_package

_PACKAGE = Path(knowledge_graph_package.__file__).parent
_FORBIDDEN_MODULES = ("app.services.featureflag",)
_FORBIDDEN_NAMES = frozenset({
    "platform_settings",
    "read_platform_feature_flag",
    "is_named_entity_extraction_enabled",
    "knowledge_graph_flags",
    "FeatureFlagService",
    "PLATFORM_SETTINGS_KEY",
})


def _violations(path: Path) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.startswith(_FORBIDDEN_MODULES):
                found.append(f"line {node.lineno}: from {node.module} import ...")
            found.extend(
                f"line {node.lineno}: imports {alias.name}" for alias in node.names if alias.name in _FORBIDDEN_NAMES
            )
        elif isinstance(node, ast.Import):
            found.extend(
                f"line {node.lineno}: import {alias.name}"
                for alias in node.names
                if alias.name.startswith(_FORBIDDEN_MODULES)
            )
        elif isinstance(node, ast.Name | ast.Attribute):
            name = node.id if isinstance(node, ast.Name) else node.attr
            if name in _FORBIDDEN_NAMES:
                found.append(f"line {node.lineno}: uses {name}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and "featureflag" in node.value:
            found.append(f"line {node.lineno}: names {node.value!r}")
    return found


def test_no_knowledge_graph_module_reads_a_flag() -> None:
    modules = sorted(_PACKAGE.rglob("*.py"))
    assert len(modules) > 10, "the scan found the package"
    violations = {
        str(path.relative_to(_PACKAGE)): found for path in modules if (found := _violations(path))
    }
    assert violations == {}, (
        "read the request's KnowledgeGraphPolicy from tool state instead "
        f"(knowledge_graph_policy_in): {violations}"
    )


def test_the_guard_catches_a_lazy_import(tmp_path: Path) -> None:
    module = tmp_path / "op.py"
    module.write_text(
        "async def f(state):\n"
        "    from app.services.featureflag.platform_settings import is_named_entity_extraction_enabled\n"
        "    return await is_named_entity_extraction_enabled(state.get('config_service'))\n"
    )
    assert len(_violations(module)) >= 2

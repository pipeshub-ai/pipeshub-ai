"""C-14, model transports (H4 GS-10): a transport keeps nothing of a call on its instance.

Transports are cached and shared: the model gateway keeps one per org configuration entry for every run of the
org (``ConfiguredModelAccess``), the query service one per request. Data of a call held on the instance (Gemini's
thought signatures were) outlives its run, is never erased with it, and grows without bound. What a later request
needs back travels on the message (``AssistantMessage.provider_state``), which the journal keeps and erasure
erases. So no method of a production ``LLMTransport`` but ``__init__`` writes into a container held on ``self``.
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from tests.cohesion.support import PYTHON_ROOT, modules

if TYPE_CHECKING:
    from pathlib import Path

ROOTS = ("harness_core/models/transports", "harness_core/budget", "app/agents/agent_loop")
MUTATORS = frozenset({"append", "add", "update", "setdefault", "extend", "insert", "__setitem__"})
# Reviewed: what the attribute holds is configuration, not data of a call.
EXEMPT: dict[str, str] = {
    "LangChainTransport._bound_by_tools": "the model bound per tool-name set; built per request, holds no content",
}


def _is_transport(node: ast.ClassDef) -> bool:
    return any(isinstance(base, ast.Name) and base.id == "LLMTransport" for base in node.bases)


def _self_attribute(node: ast.expr) -> str | None:
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self":
        return node.attr
    return None


def _container_writes(method: ast.FunctionDef | ast.AsyncFunctionDef) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for node in ast.walk(method):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        subscripted = (_self_attribute(t.value) for t in targets if isinstance(t, ast.Subscript))
        found.extend((f"self.{name}[...] =", name) for name in subscripted if name)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in MUTATORS
            and (name := _self_attribute(node.func.value))
        ):
            found.append((f"self.{name}.{node.func.attr}()", name))
    return found


def _transports() -> list[tuple[Path, ast.ClassDef]]:
    found: list[tuple[Path, ast.ClassDef]] = []
    for path in modules(*ROOTS):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found.extend((path, node) for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and _is_transport(node))
    return found


def test_the_scan_finds_the_transports() -> None:
    names = {node.name for _, node in _transports()}
    assert {"GeminiTransport", "AnthropicTransport", "OpenAITransport", "MeteredTransport"} <= names


def test_no_transport_keeps_data_of_a_call_on_its_instance() -> None:
    offending: list[str] = []
    for path, cls in _transports():
        for method in cls.body:
            if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) and method.name != "__init__":
                offending.extend(
                    f"{path.relative_to(PYTHON_ROOT)}:{cls.name}.{method.name}: {write}"
                    for write, attribute in _container_writes(method)
                    if f"{cls.name}.{attribute}" not in EXEMPT
                )
    assert offending == [], "carry it on the message (provider_state), not on the shared transport"

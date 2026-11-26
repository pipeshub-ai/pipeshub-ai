"""Per-call effect classifiers for tools whose effect depends on their arguments (C-3).

A tool listed here keeps its static ``effect`` tag as the fail-closed default;
the durable tool port asks the classifier for each call and uses its answer.
Evidence for each classifier is in ``docs/agent-harness-v2/impl/tool-effects.md``.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from app.sources.client.utils.sql_statement import outside_literals

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from pydantic import JsonValue

    from harness_core.kernel.durable.effects import CallEffect, EffectKind
    from harness_core.tools.registry import ToolRegistry

_SELECT = re.compile(r"\A\s*SELECT\b", re.IGNORECASE)
# DML never runs through GET /query, but a statement that names it is not a
# plain query. FOR VIEW / FOR REFERENCE update last-viewed dates, FOR UPDATE
# locks rows, UPDATE TRACKING / UPDATE VIEWSTAT update article statistics.
_WRITES = re.compile(
    r"\b(?:INSERT|UPDATE|DELETE|UPSERT|MERGE|UNDELETE)\b|\bFOR\s+(?:VIEW|REFERENCE)\b",
    re.IGNORECASE,
)


def soql_query_effect(arguments: Mapping[str, JsonValue]) -> EffectKind:
    """``read`` only for one plain SELECT; anything else, or anything that
    does not parse, is a ``write``."""
    statement = arguments.get("query")
    if not isinstance(statement, str):
        return "write"
    code = outside_literals(statement)
    if code is None or ";" in code or not _SELECT.match(code) or _WRITES.search(code):
        return "write"
    return "read"


CALL_EFFECTS: dict[str, CallEffect] = {
    "/tools/salesforce/soql_query": soql_query_effect,
}


def call_effects_for(registry: ToolRegistry, names: Iterable[str]) -> dict[str, CallEffect]:
    """The classifiers of the run's tools, keyed by the name the model calls."""
    found: dict[str, CallEffect] = {}
    for name in names:
        # The declared path: an instance's tool lives under its own scoped path (P2.2).
        path = registry.path_for_name(name)
        if path is not None:
            path = getattr(registry.resolve_by_name(name), "declared_path", None) or path
        if path is not None and path in CALL_EFFECTS:
            found[name] = CALL_EFFECTS[path]
    return found


__all__ = ["CALL_EFFECTS", "call_effects_for", "soql_query_effect"]

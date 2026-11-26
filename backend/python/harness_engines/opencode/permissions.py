"""opencode's permission rules, evaluated the way opencode evaluates them (F4 §4, §9).

Rules are ``(permission, pattern, action)`` in order; the last match wins and
the fallback is ``ask``. A tool is removed from the model's tool list when the
last rule matching its permission is ``deny`` with pattern ``*``. Used to check
a rendered config before launch and by the recorded fake server.
"""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import TYPE_CHECKING, Literal, cast

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from harness_core.engines.types import JsonValue

Action = Literal["allow", "ask", "deny"]
_ACTIONS: frozenset[str] = frozenset({"allow", "ask", "deny"})
# Tools gated by another tool's permission key.
_PERMISSION_KEY: dict[str, str] = {"write": "edit", "apply_patch": "edit"}


@dataclass(frozen=True, slots=True)
class Rule:
    permission: str
    pattern: str
    action: Action


def _action(value: JsonValue) -> Action:
    if not isinstance(value, str) or value not in _ACTIONS:
        raise ValueError(f"invalid permission action {value!r}")
    return cast("Action", value)


def flatten(config: Mapping[str, JsonValue]) -> list[Rule]:
    """Rules from a config ``permission`` object, in declaration order."""
    rules: list[Rule] = []
    for permission, value in config.items():
        if isinstance(value, str):
            rules.append(Rule(permission, "*", _action(value)))
            continue
        if not isinstance(value, dict):
            raise ValueError(f"invalid permission entry for {permission!r}")
        for pattern, action in cast("Mapping[str, JsonValue]", value).items():
            rules.append(Rule(permission, pattern, _action(action)))
    return rules


def permission_key(tool: str) -> str:
    return _PERMISSION_KEY.get(tool, tool)


def evaluate(rules: Sequence[Rule], permission: str, pattern: str = "*") -> Action:
    action: Action = "ask"
    for rule in rules:
        if fnmatchcase(permission, rule.permission) and fnmatchcase(pattern, rule.pattern):
            action = rule.action
    return action


def visible_tools(rules: Sequence[Rule], tools: Iterable[str]) -> list[str]:
    """The tools opencode shows the model, sorted as opencode sends them."""
    visible: list[str] = []
    for tool in tools:
        last: Rule | None = None
        for rule in rules:
            if fnmatchcase(permission_key(tool), rule.permission):
                last = rule
        if last is not None and last.action == "deny" and last.pattern == "*":
            continue
        visible.append(tool)
    return sorted(visible)


__all__ = ["Action", "Rule", "evaluate", "flatten", "permission_key", "visible_tools"]

"""LLM-facing names for MCP tools: `mcp_{namespace}_{tool}`.

Providers accept tool names matching `^[a-zA-Z0-9_-]{1,64}$` and reject the whole request
otherwise, while MCP tool names may contain `.` or `/` and instance names anything. Names
that already fit are produced exactly as before — agents, projects and chat selections
persist them — and only a name that has to change gets a hash, which keeps two tools that
sanitise to the same text apart.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

__all__ = [
    "MAX_TOOL_NAME_LENGTH",
    "assign_namespaces",
    "build_namespaced_tool_name",
    "instance_tag",
    "namespace_key",
    "unique_keys",
]

_T = TypeVar("_T")

MAX_TOOL_NAME_LENGTH = 64
_PREFIX = "mcp_"
_MAX_KEY_LENGTH = 24
_HASH_LENGTH = 6
_INVALID_KEY_CHARS = re.compile(r"[^a-z0-9_]")
_INVALID_TOOL_CHARS = re.compile(r"[^A-Za-z0-9_-]")


def _short_hash(text: str, length: int) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:length]


def _legacy_key(raw: str) -> str:
    return raw.lower().strip().replace(" ", "_").replace("-", "_")


def namespace_key(type_id: str | None, name: str | None) -> str:
    """The server part of a tool name: the catalog type, else the instance name."""
    return _INVALID_KEY_CHARS.sub("_", _legacy_key(type_id or name or "")) or "server"


def instance_tag(instance_id: str) -> str:
    """Stable short suffix telling apart instances that share a namespace key."""
    return _short_hash(instance_id, 4)


def unique_keys(
    items: Iterable[_T], *, key_of: Callable[[_T], str], id_of: Callable[[_T], str], created_of: Callable[[_T], Any],
) -> dict[str, str]:
    """`id -> key` where items sharing a key are ordered oldest first and all but the first get
    their instance tag. The oldest keeps the plain key, so names saved before a second
    instance appeared still match; the order doesn't depend on how the items arrive."""
    by_key: defaultdict[str, list[_T]] = defaultdict(list)
    for item in items:
        by_key[key_of(item)].append(item)
    assigned: dict[str, str] = {}
    for key, group in by_key.items():
        ordered = sorted(group, key=lambda item: (created_of(item) or 0, id_of(item)))
        for position, item in enumerate(ordered):
            assigned[id_of(item)] = key if position == 0 else f"{key}_{instance_tag(id_of(item))}"
    return assigned


def assign_namespaces(instances: Iterable[dict[str, Any]]) -> dict[str, str]:
    """Instance id → tool namespace across a set of stored instance records. Whoever lists or
    loads tools for the same set gets the same names, so a selection made from a listing
    names exactly one instance."""
    return unique_keys(
        instances,
        key_of=lambda i: namespace_key(i.get("typeId"), i.get("name")),
        id_of=lambda i: i["_id"],
        created_of=lambda i: i.get("createdAt"),
    )


def build_namespaced_tool_name(namespace: str, tool_name: str) -> str:
    key = _INVALID_KEY_CHARS.sub("_", _legacy_key(namespace)) or "server"
    tool = _INVALID_TOOL_CHARS.sub("_", tool_name) or "tool"
    name = f"{_PREFIX}{key}_{tool}"
    # Only " " and "-" were ever rewritten in the key; any other change means the old
    # name was invalid, so nothing persisted can depend on it.
    unchanged = key == _legacy_key(namespace) and tool == tool_name
    if unchanged and len(name) <= MAX_TOOL_NAME_LENGTH:
        return name

    suffix = f"_{_short_hash(f'{namespace}/{tool_name}', _HASH_LENGTH)}"
    key = key[:_MAX_KEY_LENGTH] if len(name) + len(suffix) > MAX_TOOL_NAME_LENGTH else key
    room = MAX_TOOL_NAME_LENGTH - len(_PREFIX) - len(key) - 1 - len(suffix)
    return f"{_PREFIX}{key}_{tool[:room]}{suffix}"

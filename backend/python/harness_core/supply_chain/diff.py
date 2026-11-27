"""What changed in a pinned artefact, as a reviewer reads it (ADR-SEC-01 D4): files as line diffs, fields as before
and after. The output is data; labels are the client's (translated) words for ``change`` and ``field``."""

from __future__ import annotations

import difflib
from typing import TYPE_CHECKING, Final, Literal, TypedDict, cast

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.engines.types import JsonValue

FileChange = Literal["added", "removed", "changed", "binary"]
CONTEXT_LINES: Final = 2
# A diff is for reading: past this many lines per file the rest is counted, not shown.
MAX_LINES_PER_FILE: Final = 400


class DiffLine(TypedDict):
    op: Literal["+", "-", " ", "@"]
    text: str


class FileDiff(TypedDict):
    path: str
    change: FileChange
    lines: list[DiffLine]
    hidden: int


class FieldDiff(TypedDict):
    field: str
    before: JsonValue
    after: JsonValue


def _text(content: bytes) -> list[str] | None:
    if b"\x00" in content:
        return None
    try:
        return content.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return None


def _lines(before: list[str], after: list[str]) -> tuple[list[DiffLine], int]:
    lines: list[DiffLine] = []
    for raw in difflib.unified_diff(before, after, lineterm="", n=CONTEXT_LINES):
        if raw.startswith(("---", "+++")):
            continue
        op = raw[:1]
        if op == "@":
            lines.append({"op": "@", "text": raw})
        elif op in ("+", "-", " "):
            lines.append({"op": op, "text": raw[1:]})  # type: ignore[typeddict-item]
    return lines[:MAX_LINES_PER_FILE], max(0, len(lines) - MAX_LINES_PER_FILE)


def files_diff(
    before: Mapping[str, bytes] | None, after: Mapping[str, bytes]
) -> list[FileDiff]:
    """Each file that differs, by path: added and removed files in full, changed ones as hunks. A file that is not
    UTF-8 text is ``binary`` (its bytes changed; a reviewer cannot read them, which is itself worth seeing)."""
    old = dict(before or {})
    out: list[FileDiff] = []
    for path in sorted({*old, *after}):
        a, b = old.get(path), after.get(path)
        if a == b:
            continue
        a_text = _text(a) if a is not None else []
        b_text = _text(b) if b is not None else []
        if a_text is None or b_text is None:
            out.append({"path": path, "change": "binary", "lines": [], "hidden": 0})
            continue
        change: FileChange = (
            "added" if a is None else "removed" if b is None else "changed"
        )
        lines, hidden = _lines(a_text, b_text)
        out.append({"path": path, "change": change, "lines": lines, "hidden": hidden})
    return out


def fields_diff(
    before: Mapping[str, JsonValue] | None, after: Mapping[str, JsonValue]
) -> list[FieldDiff]:
    """Each top-level field whose value differs, with both values (None for a field one side lacks)."""
    old = dict(before or {})
    return [
        {"field": name, "before": old.get(name), "after": after.get(name)}
        for name in sorted({*old, *after})
        if old.get(name) != after.get(name)
    ]


ToolChange = Literal[
    "description", "input_added", "input_removed", "input_changed", "required", "hints"
]


class ToolChangeEntry(TypedDict):
    change: ToolChange
    name: str | None
    before: JsonValue
    after: JsonValue


def _field(schema: object, name: str) -> object:
    return cast("dict[str, object]", schema).get(name) if isinstance(schema, dict) else None


def _properties(schema: object) -> dict[str, JsonValue]:
    props = _field(schema, "properties")
    return dict(cast("dict[str, JsonValue]", props)) if isinstance(props, dict) else {}


def _required(schema: object) -> list[str]:
    required = _field(schema, "required")
    return sorted(str(r) for r in cast("list[object]", required)) if isinstance(required, list) else []


def tool_changes(
    before: Mapping[str, JsonValue] | None, after: Mapping[str, JsonValue]
) -> list[ToolChangeEntry]:
    """An MCP tool's change against what an admin approved (``description``, ``inputSchema``, ``annotations``), one
    entry per thing a reviewer decides on: the text the model reads, each input, which inputs are required, and the
    behaviour hints that set the tool's effect (FG2)."""
    old = dict(before or {})
    out: list[ToolChangeEntry] = []
    if old.get("description") != after.get("description"):
        out.append(
            {
                "change": "description",
                "name": None,
                "before": old.get("description"),
                "after": after.get("description"),
            }
        )
    was, now = (
        _properties(old.get("inputSchema")),
        _properties(after.get("inputSchema")),
    )
    for name in sorted({*was, *now}):
        if name not in was:
            out.append(
                {
                    "change": "input_added",
                    "name": name,
                    "before": None,
                    "after": now[name],
                }
            )
        elif name not in now:
            out.append(
                {
                    "change": "input_removed",
                    "name": name,
                    "before": was[name],
                    "after": None,
                }
            )
        elif was[name] != now[name]:
            out.append(
                {
                    "change": "input_changed",
                    "name": name,
                    "before": was[name],
                    "after": now[name],
                }
            )
    if _required(old.get("inputSchema")) != _required(after.get("inputSchema")):
        out.append(
            {
                "change": "required",
                "name": None,
                "before": list(_required(old.get("inputSchema"))),
                "after": list(_required(after.get("inputSchema"))),
            }
        )
    if (old.get("annotations") or {}) != (after.get("annotations") or {}):
        out.append(
            {
                "change": "hints",
                "name": None,
                "before": old.get("annotations"),
                "after": after.get("annotations"),
            }
        )
    return out


__all__ = [
    "ToolChange",
    "ToolChangeEntry",
    "tool_changes",
    "CONTEXT_LINES",
    "MAX_LINES_PER_FILE",
    "DiffLine",
    "FieldDiff",
    "FileChange",
    "FileDiff",
    "fields_diff",
    "files_diff",
]

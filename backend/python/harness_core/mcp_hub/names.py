"""The names an MCP server's tools may have, and the names they reach a model under.

A server's tool name must meet the MCP rule (2025-11-25, unchanged in 2026-07-28):
1-128 characters of ``A-Za-z0-9_.-``. A model provider's function name is narrower,
``^[A-Za-z0-9_-]{1,64}$``, and an engine adds its own prefix to every tool it is
given (``mcp__tools__``, the longest), refusing a grant with a name it cannot carry.
So the exported name is at most 52 characters: today's ``mcp_{ns}_{tool}`` whenever
that fits, otherwise escaped and cut with a digest of the raw name, so two raw names
never meet silently and no approved tool refuses an engine run. Descriptions are model-facing data: characters that
could restyle or hide text are removed, and the length is capped.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import TYPE_CHECKING

from harness_core.mcp_hub.errors import (
    MCPHubRefused,
    ToolNameCollision,
    ToolNameInvalid,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from harness_core.engines.types import JsonValue

SPEC_TOOL_NAME = re.compile(r"[A-Za-z0-9_.\-]{1,128}")
EXPORT_TOOL_NAME = re.compile(r"[A-Za-z0-9_\-]{1,64}")
# The providers' 64 less the longest engine prefix (12): what every executor can show its model.
EXPORT_NAME_MAX = 52
EXPORT_PREFIX = "mcp"
DESCRIPTION_MAX = 1024
_DIGEST_HEX = 8
_NAMESPACE = re.compile(r"[a-z0-9_]+")
_UNSAFE_NAMESPACE = re.compile(r"[^a-z0-9_]")
_UNSAFE_EXPORT = re.compile(r"[^A-Za-z0-9_\-]")
# Characters a reader cannot see and a model still reads (D-MCP-4): soft hyphen, combining grapheme joiner, Hangul
# fillers, Mongolian and other variation selectors, zero-width and bidi controls, and the Unicode tag block, which
# spells ASCII invisibly ("ASCII smuggling"). Every other format character (Unicode category Cf) goes too.
INVISIBLE_CHARS = (
    "\u00ad\u034f\u061c\u115f\u1160\u17b4\u17b5\u180b-\u180f\u200b-\u200f\u202a-\u202e\u2060-\u206f\u3164"
    "\ufe00-\ufe0f\ufeff\uffa0\U000e0000-\U000e007f\U000e0100-\U000e01ef"
)
INVISIBLE = re.compile(f"[{INVISIBLE_CHARS}]")
# ANSI escape sequences; C0/C1 controls but tab and newline; the invisible characters above.
_UNSAFE_TEXT = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|[\x00-\x08\x0b-\x1f\x7f-\x9f" + INVISIBLE_CHARS + "]")
# The schema keys whose text a model reads as prose; any other string (an enum value, a pattern) is data and kept.
_SCHEMA_TEXT_KEYS = frozenset({"description", "title"})
_TRUNCATED = "…"


def name_digest(*parts: str) -> str:
    """The 8-hex digest an escaped or instance-scoped name carries, so two sources never meet."""
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()[:_DIGEST_HEX]


def fit_export_name(candidate: str, *digest_parts: str, limit: int = EXPORT_NAME_MAX) -> str:
    """``candidate`` when it is a valid function name of at most ``limit``; else escaped, cut, and suffixed with the parts' digest."""
    if EXPORT_TOOL_NAME.fullmatch(candidate) and len(candidate) <= limit:
        return candidate
    suffix = f"_{name_digest(*digest_parts)}"
    return _UNSAFE_EXPORT.sub("_", candidate)[: limit - len(suffix)] + suffix


def check_spec_name(name: str) -> None:
    if not SPEC_TOOL_NAME.fullmatch(name):
        raise ToolNameInvalid(f"tool name {name[:140]!r} is not 1-128 characters of A-Za-z0-9_.-")


def export_namespace(label: str) -> str:
    """Today's namespace (``mcp_{type}_…``'s ``type``) when it is plain; else escaped and made unique."""
    legacy = label.lower().strip().replace(" ", "_").replace("-", "_")
    if _NAMESPACE.fullmatch(legacy):
        return legacy
    return f"{_UNSAFE_NAMESPACE.sub('_', legacy)}_{name_digest(label)}".lstrip("_")


def export_name(namespace: str, raw: str) -> str:
    """The name a model and the run tool gateway see for ``raw`` on a server namespaced ``namespace``."""
    return fit_export_name(f"{EXPORT_PREFIX}_{namespace}_{raw}", namespace, raw)


def export_names(namespace: str, raws: Iterable[str]) -> tuple[dict[str, str], dict[str, MCPHubRefused]]:
    """Exported name per raw name, and the raw names refused with why; a collision refuses every party to it."""
    refused: dict[str, MCPHubRefused] = {}
    seen: set[str] = set()
    by_export: dict[str, list[str]] = {}
    for raw in raws:
        if raw in seen:
            refused[raw] = ToolNameCollision(f"tool {raw!r} is listed more than once")
            continue
        seen.add(raw)
        try:
            check_spec_name(raw)
        except ToolNameInvalid as exc:
            refused[raw] = exc
            continue
        by_export.setdefault(export_name(namespace, raw), []).append(raw)
    exported: dict[str, str] = {}
    for name, owners in by_export.items():
        if len(owners) > 1:
            for raw in owners:
                refused[raw] = ToolNameCollision(f"tools {sorted(owners)!r} would all be named {name!r}")
        elif owners[0] not in refused:
            exported[owners[0]] = name
    return exported, refused


def invisible_char(text: str) -> str | None:
    """The first character of ``text`` a reader cannot see (see ``INVISIBLE_CHARS``), if any."""
    found = INVISIBLE.search(text)
    if found is not None:
        return found.group()
    return next((c for c in text if unicodedata.category(c) == "Cf"), None)


def strip_unsafe_text(text: str) -> str:
    cleaned = _UNSAFE_TEXT.sub("", text)
    return "".join(c for c in cleaned if unicodedata.category(c) != "Cf")


def export_description(text: str | None, *, limit: int = DESCRIPTION_MAX) -> str:
    cleaned = strip_unsafe_text(text or "")
    if len(cleaned) > limit:
        return cleaned[: limit - len(_TRUNCATED)] + _TRUNCATED
    return cleaned


def export_schema(schema: JsonValue) -> JsonValue:
    """``schema`` with the invisible and control characters taken out of every ``description`` and ``title``: the
    prose a model reads in a tool's inputs. Values a call must match exactly (enums, patterns, defaults) are kept."""
    if isinstance(schema, dict):
        return {
            key: strip_unsafe_text(value) if key in _SCHEMA_TEXT_KEYS and isinstance(value, str) else export_schema(value)
            for key, value in schema.items()
        }
    if isinstance(schema, list):
        return [export_schema(item) for item in schema]
    return schema


__all__ = [
    "DESCRIPTION_MAX",
    "INVISIBLE",
    "INVISIBLE_CHARS",
    "EXPORT_NAME_MAX",
    "EXPORT_TOOL_NAME",
    "SPEC_TOOL_NAME",
    "check_spec_name",
    "export_description",
    "export_schema",
    "invisible_char",
    "strip_unsafe_text",
    "export_name",
    "export_names",
    "export_namespace",
    "fit_export_name",
    "name_digest",
]

"""What a tool does to data, for its starting approval rule: only reads, changes, or deletes.

A server says so in a tool's annotations (`readOnlyHint`, `destructiveHint`), but a server can
claim anything, so a name with a deleting word in it counts as deleting whatever the hints say.
A name never makes a tool read-only. A tool without hints changes data: it isn't denied, it asks.

A toolset tool (Jira, Slack, ...) says so in its `type` tag. Those tags are ours, so unlike a
server's hints a tag can make a tool read-only; a deleting name still wins.
"""
from __future__ import annotations

import re
from typing import Any, Literal, Optional

ToolKind = Literal["read", "write", "destructive"]
# Where the kind came from: the tool's name, the server's hints, or neither (the default).
KindSource = Literal["name", "server", "none"]

_DELETING_STEMS = {
    "delete": ("delete", "deletes", "deleted", "deleting", "deletion"),
    "remove": ("remove", "removes", "removed", "removing", "removal"),
    "destroy": ("destroy", "destroys", "destroyed", "destroying"),
    "drop": ("drop", "drops", "dropped", "dropping"),
    "purge": ("purge", "purges", "purged", "purging"),
    "erase": ("erase", "erases", "erased", "erasing"),
    "wipe": ("wipe", "wipes", "wiped", "wiping"),
    "truncate": ("truncate", "truncates", "truncated", "truncating"),
    "revoke": ("revoke", "revokes", "revoked", "revoking"),
    "uninstall": ("uninstall", "uninstalls", "uninstalled", "uninstalling"),
    "reset": ("reset", "resets", "resetting"),
}
DELETING_WORDS = frozenset(form for forms in _DELETING_STEMS.values() for form in forms)

# Words in `deleteJiraIssue`, `delete_issue`, `issues.delete`, `DROP-TABLE`.
_WORD_RE = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")


def name_words(name: str) -> list[str]:
    return [word.lower() for word in _WORD_RE.findall(name or "")]


def deletes_by_name(name: str) -> bool:
    return any(word in DELETING_WORDS for word in name_words(name))


def tool_kind(name: str, annotations: Optional[dict[str, Any]]) -> tuple[ToolKind, KindSource]:
    if deletes_by_name(name):
        return "destructive", "name"
    hints = annotations if isinstance(annotations, dict) else {}
    read_only = hints.get("readOnlyHint")
    destructive = hints.get("destructiveHint")
    if read_only is True:
        return "read", "server"
    if destructive is True:
        return "destructive", "server"
    if destructive is False or read_only is False:
        # The spec's default for a missing destructiveHint is true; a tool isn't denied on that alone.
        return "write", "server"
    return "write", "none"


_READ_TYPE_TAGS = frozenset({"read", "search", "utility"})
_DELETING_TYPE_TAGS = frozenset({"delete", "destructive"})


def toolset_tool_kind(name: str, type_tag: Optional[str]) -> ToolKind:
    """A toolset tool's kind from its name and its `type` tag; an unknown tag changes data."""
    if deletes_by_name(name) or type_tag in _DELETING_TYPE_TAGS:
        return "destructive"
    return "read" if type_tag in _READ_TYPE_TAGS else "write"

"""The chat's files a turn's model calls are told about, decided once per turn.

A file the model is told about (the artifact reminder, `artifacts.list_artifacts`) is a parent of its answer, so a
file some member of the chat cannot open hides the answer from them. In a run others read
(`AgentContext.read_by_others`), the model is told about the files every member of the chat may open, as the file gate
judges them for the chat's weakest member, and about the files the request picked (`filters.records` and
`filters.recordsExact`), as its asker may open them. A run nobody else reads is told about every file its asker may
open. It is the rule of the sources a prompt names (`named_sources`), for the chat's own files.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.agents.agent_loop.context import AgentContext

#: The ids of the records the turn's request picked (`picked_file_ids`); None when it picked none.
PICKED_FILES_KEY = "picked_file_ids"
#: The turn's `ToldFiles`, written by its context once worked out.
TOLD_FILES_KEY = "told_files"

# The keys of a request's filters that pick records themselves, rather than a source to search.
_RECORD_PICKS = ("records", "recordsExact")


@dataclass(frozen=True)
class ToldFiles:
    """Which of the chat's files the turn's model calls are told about.

    ``every_member_only``: only those every member of the chat may open, and the ``picked`` ones. Unset, every file the
    asker may open.
    """

    every_member_only: bool = False
    picked: frozenset[str] = field(default_factory=frozenset)


def picked_file_ids(requested: object) -> list[str] | None:
    """The records a request picked by id (`filters.records`, `filters.recordsExact`); None when it picked none. Taken
    from the client: a pick widens nothing, since a picked file is listed only as its asker may open it."""
    if not isinstance(requested, Mapping):
        return None
    ids = [
        value.strip()
        for key in _RECORD_PICKS
        for value in (requested.get(key) or [])
        if isinstance(value, str) and value.strip()
    ]
    return list(dict.fromkeys(ids)) or None


def told_files_for(context: AgentContext) -> ToldFiles:
    """Works out which files the turn's model is told about. `AgentContext.told_files` calls it once per turn and keeps
    the answer; read that, not this."""
    if not context.read_by_others:
        return ToldFiles()
    picked = context.tool_state.get(PICKED_FILES_KEY)
    return ToldFiles(
        every_member_only=True,
        picked=frozenset(p for p in (picked or []) if isinstance(p, str)),
    )


def told_files_in(state: Mapping[str, Any]) -> ToldFiles | None:
    """The `ToldFiles` the turn's context stored; None outside a run (every file its asker may open)."""
    told = state.get(TOLD_FILES_KEY)
    return told if isinstance(told, ToldFiles) else None


__all__ = ["PICKED_FILES_KEY", "TOLD_FILES_KEY", "ToldFiles", "picked_file_ids", "told_files_for", "told_files_in"]

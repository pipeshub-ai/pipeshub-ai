"""The knowledge sources a turn's model calls may name, decided once per turn.

The source table in the prompts (`SourceCatalog`), the router's capability block and the answer's label all read the
one `NamedSources` the turn's `AgentContext` works out (`AgentContext.named_sources`), so a source a model call is
told about by name is a source the label lists, and no other.

In a run others read (`AgentContext.read_by_others`), the assistant's prompts name the sources the request picked and
nothing else. Its tools still search every source the person asking may read, with their access; the prompts say so in
one entry that names none of them.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.services.graph_db.interface.graph_db_provider import (
    SELECTION_APPS_FILTER_KEY,
    SELECTION_FILTER_KEYS,
)

if TYPE_CHECKING:
    from app.agents.agent_loop.context import AgentContext

#: The ids of the sources the turn's request picked (`picked_source_ids`); None when it picked none.
PICKED_SOURCES_KEY = "picked_source_ids"
#: The turn's `NamedSources`, written by its context once worked out.
NAMED_SOURCES_KEY = "named_sources"
ASSISTANT_AGENT_KEY = "agentIdPlaceholder"
_NO_KB_SELECTED = "NO_KB_SELECTED"


@dataclass(frozen=True)
class NamedSources:
    """What the turn's model calls may name.

    ``knowledge`` holds the agent route's entries (``connectorId``, name, type), named with their ids; ``connectors``
    the chat route's connectors, named by type. ``unnamed`` is set when the run also searches sources that no model
    call is told about.
    """

    knowledge: tuple[Mapping[str, Any], ...] = ()
    connectors: tuple[Mapping[str, Any], ...] = ()
    unnamed: bool = False
    #: The request picked its sources: what goes unnamed is the pick (on the chat route, which names none of a
    #: turn others read), never everything.
    picked: bool = False
    #: Only `knowledge` may be named, in prompts and in tool results alike (`nameable_source_ids`).
    limited: bool = False


def _entries(value: object) -> tuple[Mapping[str, Any], ...]:
    return tuple(v for v in value if isinstance(v, Mapping)) if isinstance(value, list | tuple) else ()


def _ids(values: object) -> list[str]:
    if not isinstance(values, list | tuple):
        return []
    return [v.strip() for v in values if isinstance(v, str) and v.strip() and v.strip() != _NO_KB_SELECTED]


def picked_source_ids(requested: object, resolved: object = None) -> list[str] | None:
    """The sources a request picked: the apps and collections its ``filters`` name and, for a selection below app
    level, the apps that selection touches (from the ``resolved`` filters). None when it picked none, which on the
    universal agent means every source the person may read."""
    requested = requested if isinstance(requested, Mapping) else {}
    ids = [*_ids(requested.get("apps")), *_ids(requested.get("kb"))]
    if any(requested.get(key) for key in SELECTION_FILTER_KEYS):
        touched = resolved.get(SELECTION_APPS_FILTER_KEY) if isinstance(resolved, Mapping) else None
        return list(dict.fromkeys([*ids, *_ids(touched)]))
    return list(dict.fromkeys(ids)) or None


def runs_as_assistant(context: AgentContext) -> bool:
    """The default assistant (Agent mode's universal agent, or a chat-mode turn), whose sources are the person's own,
    rather than a saved agent, whose sources are its configuration."""
    key = context.agent_key
    return not key or key == ASSISTANT_AGENT_KEY or bool(context.tool_state.get("is_placeholder_agent"))


def everything_named(state: Mapping[str, Any]) -> NamedSources:
    return NamedSources(
        knowledge=_entries(state.get("agent_knowledge")),
        connectors=_entries(state.get("available_connectors")),
    )


def named_sources_for(context: AgentContext) -> NamedSources:
    """Works out what the turn's model calls may name. `AgentContext.named_sources` calls it once per turn and keeps
    the answer; read that, not this."""
    state = context.tool_state
    everything = everything_named(state)
    if not runs_as_assistant(context) or not context.read_by_others:
        return everything
    raw_picked = state.get(PICKED_SOURCES_KEY)
    picked = set(_ids(raw_picked))
    knowledge = tuple(k for k in everything.knowledge if str(k.get("connectorId") or "").strip() in picked)
    # The chat route lists connectors by type only, and searches collections it never lists: nothing of it is named.
    unnamed = len(knowledge) < len(everything.knowledge) if everything.knowledge else bool(state.get("has_knowledge"))
    return NamedSources(knowledge=knowledge, unnamed=unnamed, picked=raw_picked is not None, limited=True)


def named_sources_in(state: Mapping[str, Any]) -> NamedSources:
    """The named sources the turn's context stored; every source the state holds when no context has worked them out
    (a state built outside a run)."""
    named = state.get(NAMED_SOURCES_KEY)
    return named if isinstance(named, NamedSources) else everything_named(state)


def nameable_source_ids(state: Mapping[str, Any]) -> frozenset[str] | None:
    """The sources a tool may name (or give the id of) in this run's results: the ones its prompts name. None when
    any may be. A tool shows another source without its name ("another source")."""
    named = state.get(NAMED_SOURCES_KEY)
    if not isinstance(named, NamedSources) or not named.limited:
        return None
    return frozenset(cid for k in named.knowledge if (cid := str(k.get("connectorId") or "").strip()))


ANOTHER_SOURCE = "another source"
# How a record's header names its source: the first segment of its Location trail, and its Connector ID line.
_TRAIL_SOURCE = re.compile(r"(Location: )([^\n]*?) \(App ID: ([^)\n]+)\)")
_CONNECTOR_ID = re.compile(r"(Connector ID: )([^\s|]+)")


def without_unnamed_sources(text: str, nameable: frozenset[str]) -> str:
    """`text` with every source outside `nameable` that a record header names (its Location trail's first segment,
    and its Connector ID) written as "another source". Keyed by the source's id, so no other text changes."""

    def trail(match: re.Match[str]) -> str:
        return match.group(0) if match.group(3).strip() in nameable else f"{match.group(1)}{ANOTHER_SOURCE}"

    def connector(match: re.Match[str]) -> str:
        value = match.group(2)
        return match.group(0) if value in nameable or value == "N/A" else f"{match.group(1)}{ANOTHER_SOURCE}"

    return _CONNECTOR_ID.sub(connector, _TRAIL_SOURCE.sub(trail, text))


__all__ = [
    "ANOTHER_SOURCE",
    "ASSISTANT_AGENT_KEY",
    "NAMED_SOURCES_KEY",
    "PICKED_SOURCES_KEY",
    "NamedSources",
    "everything_named",
    "named_sources_for",
    "named_sources_in",
    "nameable_source_ids",
    "picked_source_ids",
    "runs_as_assistant",
    "without_unnamed_sources",
]

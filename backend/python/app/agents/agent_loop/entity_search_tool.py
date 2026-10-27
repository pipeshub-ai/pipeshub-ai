"""Whether a request is offered ``knowledgegraph__search_by_entities``, and
whether it has named-entity extraction on at all.

The deployment switch ``ENTITY_SEARCH_TOOL`` turns the tool on. It stays off
under ``HIGH_ACCURACY_MODE`` unless set, because the tool adds a model call to
every lookup it cannot settle by exact name and has not been shown to help on
every model. It is also offered only where named-entity extraction is on and
the entity vector store is wired, since without them it could only fail. With
extraction off, the knowledge-graph tools that read named entities are left
out too, for the same reason.

Both are decided once per request, before tools load, from one read of the
named-entity flag, and stored on the tool state: the toolset reads them to
decide its own tool list, so the list and the system prompt are fixed for the
whole run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.utils.env_utils import env_bool

if TYPE_CHECKING:
    from collections.abc import Mapping

ENTITY_SEARCH_TOOL_ENV_VAR = "ENTITY_SEARCH_TOOL"
SEARCH_BY_ENTITIES_TOOL_NAME = "knowledgegraph__search_by_entities"
ENTITY_SEARCH_OFFERED_STATE_KEY = "entity_search_tool_offered"
NAMED_ENTITIES_ENABLED_STATE_KEY = "named_entity_extraction_enabled"


def entity_search_tool_switched_on() -> bool:
    return env_bool(ENTITY_SEARCH_TOOL_ENV_VAR, False)


def entity_search_tool_offered(tool_state: Mapping[str, Any]) -> bool:
    """The switch, the entity store, and the named-entity flag this request read."""
    return (
        entity_search_tool_switched_on()
        and bool(tool_state.get("entity_vector_store"))
        and named_entities_enabled_in(tool_state)
    )


def entity_search_offered_in(tool_state: Mapping[str, Any] | None) -> bool:
    """What `entity_search_tool_offered` decided for this request."""
    return bool(tool_state and tool_state.get(ENTITY_SEARCH_OFFERED_STATE_KEY))


def named_entities_enabled_in(tool_state: Mapping[str, Any] | None) -> bool:
    """The named-entity flag as this request read it; off when it was not read."""
    return bool(tool_state and tool_state.get(NAMED_ENTITIES_ENABLED_STATE_KEY))


__all__ = [
    "ENTITY_SEARCH_OFFERED_STATE_KEY",
    "ENTITY_SEARCH_TOOL_ENV_VAR",
    "NAMED_ENTITIES_ENABLED_STATE_KEY",
    "SEARCH_BY_ENTITIES_TOOL_NAME",
    "entity_search_offered_in",
    "entity_search_tool_offered",
    "entity_search_tool_switched_on",
    "named_entities_enabled_in",
]

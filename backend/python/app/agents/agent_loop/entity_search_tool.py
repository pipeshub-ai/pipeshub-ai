"""Whether a request is offered ``knowledgegraph__search_by_entities``.

The deployment switch ``ENTITY_SEARCH_TOOL`` turns it on. It stays off under
``HIGH_ACCURACY_MODE`` unless set, because the tool adds a model call to every
lookup it cannot settle by exact name and has not been shown to help on every
model. It is also offered only where named-entity extraction is on and the
entity vector store is wired, since without them it could only fail.

The decision is made once per request, before tools load, and stored on the
tool state: the toolset reads it to decide its own tool list, so the list and
the system prompt are fixed for the whole run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.utils.env_utils import env_bool

if TYPE_CHECKING:
    from collections.abc import Mapping

ENTITY_SEARCH_TOOL_ENV_VAR = "ENTITY_SEARCH_TOOL"
SEARCH_BY_ENTITIES_TOOL_NAME = "knowledgegraph__search_by_entities"
ENTITY_SEARCH_OFFERED_STATE_KEY = "entity_search_tool_offered"


def entity_search_tool_switched_on() -> bool:
    return env_bool(ENTITY_SEARCH_TOOL_ENV_VAR, False)


async def entity_search_tool_offered(tool_state: Mapping[str, Any]) -> bool:
    """The switch, then the entity store, then the named-entity flag; the flag
    is a live config read, so it is made only when the others hold."""
    if not entity_search_tool_switched_on() or not tool_state.get("entity_vector_store"):
        return False
    from app.services.featureflag.platform_settings import (
        is_named_entity_extraction_enabled,
    )

    return await is_named_entity_extraction_enabled(tool_state.get("config_service"))


def entity_search_offered_in(tool_state: Mapping[str, Any] | None) -> bool:
    """What `entity_search_tool_offered` decided for this request."""
    return bool(tool_state and tool_state.get(ENTITY_SEARCH_OFFERED_STATE_KEY))


__all__ = [
    "ENTITY_SEARCH_OFFERED_STATE_KEY",
    "ENTITY_SEARCH_TOOL_ENV_VAR",
    "SEARCH_BY_ENTITIES_TOOL_NAME",
    "entity_search_offered_in",
    "entity_search_tool_offered",
    "entity_search_tool_switched_on",
]

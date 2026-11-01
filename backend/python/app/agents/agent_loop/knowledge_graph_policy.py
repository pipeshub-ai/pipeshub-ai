"""What the knowledge graph offers one request: ``KnowledgeGraphPolicy``.

Three arms, picked by two Labs flags:

- ``off`` (``KNOWLEDGE_GRAPH_TOOLS`` false): no entity tools and no Entities
  paragraph in the system prompt.
- ``taxonomy`` (the default): entity tools over departments, categories,
  topics, languages, record titles and record groups; no people, no
  organisations, and no extracted named entities, which name both.
- ``full`` (``KNOWLEDGE_GRAPH_PEOPLE_AND_COMPANIES`` true as well): everything,
  named entities included where extraction is on.

``knowledgegraph__search_by_entities`` is offered only in ``full`` with
named entities on, the entity store wired, and the deployment switch
``ENTITY_SEARCH_TOOL`` set. The switch stays off under ``HIGH_ACCURACY_MODE``
unless set, because the tool adds a model call to every lookup it cannot
settle by exact name and has not been shown to help on every model.

The policy is decided once per request, before tools load, from one read of
the settings, and stored on the tool state. The tool list, the system prompt
and every tool call read it from there and never read a flag themselves, so
they cannot disagree within a run even if a flag is saved in Labs meanwhile.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from app.modules.retrieval.entity_permissions import (
    NAMED_ENTITY_TYPE,
    ORGANIZATION_ENTITY_TYPE,
    PERSON_ENTITY_TYPE,
    RECORD_ENTITY_TYPE,
    RECORD_GROUP_ENTITY_TYPE,
    TAXONOMY_ENTITY_TYPES,
)
from app.services.featureflag.platform_settings import knowledge_graph_flags
from app.utils.env_utils import env_bool

if TYPE_CHECKING:
    from collections.abc import Mapping, MutableMapping

logger = logging.getLogger(__name__)

ENTITY_SEARCH_TOOL_ENV_VAR = "ENTITY_SEARCH_TOOL"
SEARCH_BY_ENTITIES_TOOL_NAME = "knowledgegraph__search_by_entities"
KNOWLEDGE_GRAPH_POLICY_STATE_KEY = "knowledge_graph_policy"

_TAXONOMY_ARM_TYPES = TAXONOMY_ENTITY_TYPES | {RECORD_ENTITY_TYPE, RECORD_GROUP_ENTITY_TYPE}


class KnowledgeGraphMode(StrEnum):
    OFF = "off"
    TAXONOMY = "taxonomy"
    FULL = "full"


@dataclass(frozen=True, slots=True)
class KnowledgeGraphPolicy:
    """One request's knowledge-graph arm. The defaults are the flags' defaults."""

    mode: KnowledgeGraphMode = KnowledgeGraphMode.TAXONOMY
    named_entities: bool = False
    """Extracted named entities are searchable (``full`` with extraction on)."""
    search_by_entities: bool = False
    """``knowledgegraph__search_by_entities`` is offered."""
    record_query: bool = False
    """``knowledgegraph__find_records`` replaces ``find_records_by_entity``."""

    @classmethod
    def from_flags(
        cls, *, tools: bool, people_and_companies: bool, named_entities: bool, search_by_entities_wired: bool,
        record_query: bool = False,
    ) -> KnowledgeGraphPolicy:
        if not tools:
            mode = KnowledgeGraphMode.OFF
        elif people_and_companies:
            mode = KnowledgeGraphMode.FULL
        else:
            mode = KnowledgeGraphMode.TAXONOMY
        named = named_entities and mode is KnowledgeGraphMode.FULL
        return cls(
            mode=mode, named_entities=named, search_by_entities=named and search_by_entities_wired,
            record_query=record_query and mode is not KnowledgeGraphMode.OFF,
        )

    @property
    def arm(self) -> str:
        """The arm as the eval harness names it: the mode, plus "+query" with ``find_records``."""
        return f"{self.mode.value}+query" if self.record_query else self.mode.value

    @property
    def entity_tools(self) -> bool:
        return self.mode is not KnowledgeGraphMode.OFF

    @property
    def people_and_companies(self) -> bool:
        return self.mode is KnowledgeGraphMode.FULL

    @property
    def entity_types(self) -> frozenset[str]:
        """The entity types this request may search, list or scope by. The one
        place that decides it; the tools and ``entity_permissions`` ask here."""
        if self.mode is KnowledgeGraphMode.OFF:
            return frozenset()
        if self.mode is KnowledgeGraphMode.TAXONOMY:
            return _TAXONOMY_ARM_TYPES
        people = {PERSON_ENTITY_TYPE, ORGANIZATION_ENTITY_TYPE}
        return _TAXONOMY_ARM_TYPES | people | ({NAMED_ENTITY_TYPE} if self.named_entities else set())

    def allows(self, entity_type: str) -> bool:
        return entity_type in self.entity_types


def entity_search_tool_switched_on() -> bool:
    return env_bool(ENTITY_SEARCH_TOOL_ENV_VAR, False)


async def decide_knowledge_graph_policy(tool_state: MutableMapping[str, Any]) -> KnowledgeGraphPolicy:
    """The request's policy, decided on the first call from one read of the
    Labs settings and stored on ``tool_state``; later calls return it unread."""
    decided = tool_state.get(KNOWLEDGE_GRAPH_POLICY_STATE_KEY)
    if isinstance(decided, KnowledgeGraphPolicy):
        return decided
    flags = await knowledge_graph_flags(tool_state.get("config_service"))
    policy = KnowledgeGraphPolicy.from_flags(
        tools=flags.tools,
        people_and_companies=flags.people_and_companies,
        named_entities=flags.named_entities,
        search_by_entities_wired=entity_search_tool_switched_on() and bool(tool_state.get("entity_vector_store")),
        record_query=flags.record_query,
    )
    tool_state[KNOWLEDGE_GRAPH_POLICY_STATE_KEY] = policy
    logger.info(
        "knowledge graph policy mode=%s named_entities=%s search_by_entities=%s org_id=%s conversation_id=%s",
        policy.mode.value, policy.named_entities, policy.search_by_entities,
        tool_state.get("org_id"), tool_state.get("conversation_id"),
    )
    return policy


def knowledge_graph_policy_in(tool_state: Mapping[str, Any] | None) -> KnowledgeGraphPolicy:
    """The policy this request decided; the defaults where none was decided."""
    decided = tool_state.get(KNOWLEDGE_GRAPH_POLICY_STATE_KEY) if tool_state else None
    return decided if isinstance(decided, KnowledgeGraphPolicy) else KnowledgeGraphPolicy()


def entity_tools_granted(tool_state: Mapping[str, Any] | None) -> bool:
    """Whether ``search_entities`` is offered: the arm has entity tools and the
    entity store they search is wired (without it they could only fail)."""
    return bool(tool_state and tool_state.get("entity_vector_store")) and (
        knowledge_graph_policy_in(tool_state).entity_tools
    )


__all__ = [
    "ENTITY_SEARCH_TOOL_ENV_VAR",
    "KNOWLEDGE_GRAPH_POLICY_STATE_KEY",
    "SEARCH_BY_ENTITIES_TOOL_NAME",
    "KnowledgeGraphMode",
    "KnowledgeGraphPolicy",
    "decide_knowledge_graph_policy",
    "entity_search_tool_switched_on",
    "entity_tools_granted",
    "knowledge_graph_policy_in",
]

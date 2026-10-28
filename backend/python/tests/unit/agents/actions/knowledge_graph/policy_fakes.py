"""The ``full`` knowledge-graph arm, for tests of the tools that serve people,
companies and named entities."""

from __future__ import annotations

from app.agents.agent_loop.knowledge_graph_policy import (
    KnowledgeGraphMode,
    KnowledgeGraphPolicy,
)


def full_arm(*, named_entities: bool = True) -> KnowledgeGraphPolicy:
    return KnowledgeGraphPolicy(mode=KnowledgeGraphMode.FULL, named_entities=named_entities)

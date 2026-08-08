"""Extraction routing (KG Clean Rebuild plan, Part B §3.1 / Part E Layers 1-3).

Routes each document (by ``org_id`` + ``domain`` + ``doc_type``) to one of
the three extraction modes. Routing is a decision table, not a global
schema — the mode is a per-document decision, never a property of the whole
enterprise.
"""

from app.modules.knowledge_graph.routing.engine import (
    OntologyLookup,
    RoutingDecision,
    RoutingEngine,
)

__all__ = ["OntologyLookup", "RoutingDecision", "RoutingEngine"]

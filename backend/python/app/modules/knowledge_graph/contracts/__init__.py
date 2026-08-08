"""Typed contracts for the knowledge-graph extraction/indexing pipeline.

These models are the shared vocabulary between extraction workers (Phase 4),
entity resolution (Phase 3), the bi-temporal graph writer (Phase 6), and the
query-side filter contracts served to agent tools (Phase 5). They are
intentionally decoupled from ``app.models.entities.EntityRecord`` — that
model is the slim vector-store payload; these are the richer, provenance-
carrying shapes used before and during indexing.
"""

from app.modules.knowledge_graph.contracts.envelope import (
    ExtractedEntity,
    ExtractedRelationship,
    ExtractionEnvelope,
    ExtractionMode,
    ExtractionProvenance,
    TextSpan,
)
from app.modules.knowledge_graph.contracts.filter_contract import (
    AttributeProfile,
    FilterContract,
    FilterContractSource,
    FilterField,
    FilterFieldOperator,
)
from app.modules.knowledge_graph.contracts.graph_models import (
    GraphEdge,
    GraphNode,
    ProvenanceRef,
)
from app.modules.knowledge_graph.contracts.ontology import (
    AttributeDataType,
    OntologyAttribute,
    OntologyDefinition,
    OntologyEntityType,
    OntologyRelationshipType,
    OntologyStatus,
    RelationshipCardinality,
)
from app.modules.knowledge_graph.contracts.resolution import (
    EntityResolutionService,
    LLMAdjudication,
    MergeRecord,
    ResolutionResult,
)

__all__ = [
    "AttributeDataType",
    "AttributeProfile",
    "EntityResolutionService",
    "ExtractedEntity",
    "ExtractedRelationship",
    "ExtractionEnvelope",
    "ExtractionMode",
    "ExtractionProvenance",
    "FilterContract",
    "FilterContractSource",
    "FilterField",
    "FilterFieldOperator",
    "GraphEdge",
    "GraphNode",
    "LLMAdjudication",
    "MergeRecord",
    "OntologyAttribute",
    "OntologyDefinition",
    "OntologyEntityType",
    "OntologyRelationshipType",
    "OntologyStatus",
    "ProvenanceRef",
    "RelationshipCardinality",
    "ResolutionResult",
    "TextSpan",
]

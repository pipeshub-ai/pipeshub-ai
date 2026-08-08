"""LLM extraction workers (KG Clean Rebuild plan, Part B §3.2 / Part E Layers 1-3).

All three modes share one mechanism (``LLMEnvelopeExtractor``) — what varies
is the type constraint communicated to the LLM and enforced afterwards:

  ontology_guided      closed enum, drawn from a compiled ``OntologyDefinition``
  domain_schema_free   soft (non-exhaustive) vocabulary; off-list types allowed,
                        flagged ``is_novel_type``
  generic_schema_free  fully open vocabulary, no seed list

Extraction never deduplicates or assigns global identity — every entity here
is document-scoped (``local_id``); see ``modules.knowledge_graph.indexing``.
"""

from app.modules.knowledge_graph.extraction.llm_extractor import (
    LLMEnvelopeExtractor,
    SoftVocabularyTerm,
)
from app.modules.knowledge_graph.extraction.validation import (
    EnvelopeValidationResult,
    QuarantinedItem,
    validate_envelope,
)

__all__ = [
    "LLMEnvelopeExtractor",
    "SoftVocabularyTerm",
    "EnvelopeValidationResult",
    "QuarantinedItem",
    "validate_envelope",
]

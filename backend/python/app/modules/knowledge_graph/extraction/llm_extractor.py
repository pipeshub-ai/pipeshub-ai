"""Shared LLM extraction mechanics for all three extraction modes.

One extractor, parameterised by ``ExtractionMode`` plus an optional closed
ontology or soft vocabulary — see module docstring in
``modules.knowledge_graph.extraction`` for why this isn't three separate
classes (they would be near-identical copies differing only in prompt text
and post-processing, which is exactly the duplication CLAUDE.md flags).
"""

import logging
import uuid
from typing import TYPE_CHECKING, Any

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from app.modules.knowledge_graph.contracts.envelope import (
    ExtractedEntity,
    ExtractedRelationship,
    ExtractionEnvelope,
    ExtractionMode,
    ExtractionProvenance,
)
from app.modules.knowledge_graph.contracts.ontology import OntologyDefinition
from app.utils.llm import get_llm_for_role
from app.utils.streaming import invoke_with_structured_output_and_reflection

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService

_LLM_ROLE = "indexing"

# Hard cap on characters sent to the LLM per extraction call — this module
# extracts from a single document's pre-chunked text; upstream callers are
# responsible for chunking documents that exceed this (out of scope here).
_MAX_TEXT_CHARS = 24_000


class SoftVocabularyTerm(BaseModel):
    """One entry in a domain's non-exhaustive seed type list (Part B §3.2)."""
    name: str
    description: str = ""


class _LLMExtractedEntity(BaseModel):
    local_id: str = Field(description="Short id you assign, unique within this response, e.g. 'e1'")
    type: str
    name: str
    attributes: dict[str, Any] = Field(default_factory=dict)
    extraction_confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class _LLMExtractedRelationship(BaseModel):
    local_id: str
    type: str
    subject_local_id: str
    object_local_id: str
    extraction_confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class _LLMExtractionOutput(BaseModel):
    entities: list[_LLMExtractedEntity] = Field(default_factory=list)
    relationships: list[_LLMExtractedRelationship] = Field(default_factory=list)


class LLMEnvelopeExtractor:
    """Runs one LLM extraction pass and returns a raw (not-yet-validated)
    :class:`ExtractionEnvelope`.

    Raw here means: structurally an envelope, but not yet checked by
    ``modules.knowledge_graph.extraction.validation.validate_envelope`` —
    callers must run that before handing the envelope to resolution.
    """

    def __init__(
        self,
        config_service: "ConfigurationService",
        logger: logging.Logger,
    ) -> None:
        self.config_service = config_service
        self.logger = logger

    async def extract(
        self,
        *,
        document_id: str,
        org_id: str,
        domain: str,
        text: str,
        mode: ExtractionMode,
        ontology: OntologyDefinition | None = None,
        soft_vocabulary: list[SoftVocabularyTerm] | None = None,
        model_id: str | None = None,
    ) -> ExtractionEnvelope:
        if mode == ExtractionMode.ONTOLOGY_GUIDED and ontology is None:
            raise ValueError("ontology_guided extraction requires an OntologyDefinition")

        extraction_id = str(uuid.uuid4())
        truncated_text = text[:_MAX_TEXT_CHARS]

        parsed: _LLMExtractionOutput | None = None
        try:
            llm, config = await get_llm_for_role(self.config_service, _LLM_ROLE)
            prompt = self._build_prompt(mode, truncated_text, ontology, soft_vocabulary)
            parsed = await invoke_with_structured_output_and_reflection(
                llm, [HumanMessage(content=prompt)], _LLMExtractionOutput
            )
            model_id = model_id or config.get("modelKey")
        except Exception as exc:
            self.logger.warning(
                "LLM extraction failed for document=%s mode=%s (returning empty envelope): %s",
                document_id, mode.value, exc,
            )

        if parsed is None:
            parsed = _LLMExtractionOutput()

        entities, relationships = self._to_contract_entities(
            parsed, mode, soft_vocabulary
        )

        return ExtractionEnvelope(
            extraction_id=extraction_id,
            document_id=document_id,
            org_id=org_id,
            extraction_mode=mode,
            ontology_id=ontology.ontology_id if ontology else None,
            domain=domain,
            entities=entities,
            relationships=relationships,
            provenance=ExtractionProvenance(model_id=model_id),
        )

    # ------------------------------------------------------------------
    # Prompt construction — the only real per-mode difference
    # ------------------------------------------------------------------

    @staticmethod
    def _build_prompt(
        mode: ExtractionMode,
        text: str,
        ontology: OntologyDefinition | None,
        soft_vocabulary: list[SoftVocabularyTerm] | None,
    ) -> str:
        header = (
            "Extract entities and relationships mentioned in the DOCUMENT below. "
            "Assign each entity a short local_id (e1, e2, ...) unique within your "
            "response; relationships reference entities by that local_id.\n\n"
        )

        if mode == ExtractionMode.ONTOLOGY_GUIDED:
            assert ontology is not None  # enforced by extract()
            type_lines = "\n".join(
                f"  - {t.name}: {t.description}" for t in ontology.entity_types
            )
            rel_lines = "\n".join(
                f"  - {r.name} ({r.domain_type} -> {r.range_type})"
                for r in ontology.relationship_types
            )
            constraint = (
                "Use ONLY these entity types (closed set) — do not invent new "
                f"types:\n{type_lines}\n\nAllowed relationship types:\n{rel_lines}\n\n"
            )
        elif mode == ExtractionMode.DOMAIN_SCHEMA_FREE:
            vocab_lines = "\n".join(
                f"  - {t.name}: {t.description}" for t in (soft_vocabulary or [])
            )
            constraint = (
                "Prefer these entity types when they fit, but you may use a "
                f"different type if none apply:\n{vocab_lines}\n\n"
            )
        else:
            constraint = (
                "There is no predefined type list — use whatever type names "
                "best describe each entity (e.g. person, organization, date, "
                "concept, product).\n\n"
            )

        return f"{header}{constraint}DOCUMENT:\n{text}"

    # ------------------------------------------------------------------
    # Post-processing — apply the mode's vocabulary-membership tagging
    # ------------------------------------------------------------------

    @staticmethod
    def _to_contract_entities(
        parsed: _LLMExtractionOutput,
        mode: ExtractionMode,
        soft_vocabulary: list[SoftVocabularyTerm] | None,
    ) -> tuple[list[ExtractedEntity], list[ExtractedRelationship]]:
        soft_names = {t.name for t in (soft_vocabulary or [])}

        entities: list[ExtractedEntity] = []
        for raw in parsed.entities:
            if not raw.name.strip():
                continue
            is_novel = mode == ExtractionMode.DOMAIN_SCHEMA_FREE and raw.type not in soft_names
            entities.append(
                ExtractedEntity(
                    local_id=raw.local_id,
                    type=raw.type,
                    name=raw.name,
                    attributes=raw.attributes,
                    extraction_confidence=raw.extraction_confidence,
                    is_novel_type=is_novel,
                )
            )

        known_local_ids = {e.local_id for e in entities}
        relationships = [
            ExtractedRelationship(
                local_id=raw.local_id,
                type=raw.type,
                subject_local_id=raw.subject_local_id,
                object_local_id=raw.object_local_id,
                extraction_confidence=raw.extraction_confidence,
            )
            for raw in parsed.relationships
            if raw.subject_local_id in known_local_ids and raw.object_local_id in known_local_ids
        ]
        return entities, relationships

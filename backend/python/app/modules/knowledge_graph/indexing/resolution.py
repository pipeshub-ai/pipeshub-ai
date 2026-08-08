"""Semantic + LLM entity resolution (KG Clean Rebuild plan, Part C).

Implements :class:`app.modules.knowledge_graph.contracts.resolution.EntityResolutionService`.

Policy (see plan Part C "Deduplication (semantic + LLM)"):

    hard key exact match (email / external_id / issue_key / fqn)  -> auto-merge, no LLM
    exact canonical-name match                                    -> auto-merge, no LLM
    name-similarity >= hard, entity type == PERSON                -> LLM adjudicates
    name-similarity >= hard, other types                          -> auto-merge
    soft <= name-similarity < hard                                -> LLM adjudicates
    name-similarity < soft                                        -> new entity

Fail-closed: any error resolving a mention (vector search, LLM call, or a
non-parseable LLM response) resolves that entity as *distinct* — a new
canonical node — rather than risking an incorrect merge. Over-merging two
unrelated people is a much costlier, harder-to-detect failure than a
duplicate.

Why name-similarity, not the vector store's match score: candidates are
retrieved via ``EntityVectorStore.search_entities`` for ANN recall, but the
merge decision itself is made against a normalized name/alias similarity
score, not that call's returned ``score``. ``IVectorDBService`` backends
apply RRF fusion even to single-modality queries (see
``QdrantUtils.search_request_to_qdrant``), so the returned score is a
fused-rank score, not a calibrated cosine similarity — there is no fixed
threshold that means the same thing across backends, or even across
dense-only vs. hybrid queries on the same backend. Name similarity is
backend-independent and is what the soft/hard cuts below are calibrated
against.
"""

import difflib
import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from app.models.entities import EntityType
from app.modules.knowledge_graph.contracts.envelope import (
    ExtractedEntity,
    ExtractionEnvelope,
)
from app.modules.knowledge_graph.contracts.resolution import (
    LLMAdjudication,
    MergeRecord,
    ResolutionResult,
)
from app.utils.llm import get_llm_for_role
from app.utils.streaming import invoke_with_structured_output_and_reflection

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService
    from app.modules.knowledge_graph.governance.suggestions import MergeSuggestionStore
    from app.modules.transformers.entity_vectorstore import EntityVectorStore

# Attribute keys checked (in order) for a hard identity key on an extracted
# entity. Matched against a candidate's aliases/name — EntityRecord.aliases
# is expected to carry hard keys (e.g. an email) for entities where one is
# known, so this works without a dedicated graph-side hard-key index.
_HARD_KEY_ATTRIBUTES = ("email", "external_id", "issue_key", "fqn")

# Types that must never auto-merge on name-similarity alone, even at/above
# the hard threshold — always routed to the LLM. Coreference errors between
# two different people are the costliest, hardest-to-detect merge mistake.
_NEVER_AUTO_MERGE_ABOVE_HARD = frozenset({EntityType.PERSON.value})

_LLM_ROLE = "indexing"


class _LLMMergeDecision(BaseModel):
    """Structured response schema for LLM adjudication."""
    decision: Literal["merge", "distinct"]
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""


@dataclass(frozen=True)
class ResolutionThresholds:
    soft: float = 0.72
    hard: float = 0.90
    candidate_pool: int = 5


class SemanticLLMEntityResolver:
    """Resolves extracted entities to canonical vector-store identities.

    Pure resolution seam per the protocol: does not write to the graph or
    vector store itself. Callers use ``ResolutionResult.local_to_canonical``
    (and ``merges[].is_new_node``) to know which canonical ids to create vs.
    reuse in the graph write step (Part E Layer 4 / Phase 6).
    """

    def __init__(
        self,
        entity_vector_store: "EntityVectorStore",
        config_service: "ConfigurationService",
        logger: logging.Logger,
        thresholds: ResolutionThresholds | None = None,
        suggestion_store: "MergeSuggestionStore | None" = None,
    ) -> None:
        self.entity_vector_store = entity_vector_store
        self.config_service = config_service
        self.logger = logger
        self.thresholds = thresholds or ResolutionThresholds()
        # Optional (Phase 7 EE governance): when set, every ambiguous
        # LLM-adjudicated decision is also durably queued for human review —
        # see app.modules.knowledge_graph.governance.suggestions. None (the
        # default) preserves Phase 3 behavior exactly: adjudications only
        # ever live in the in-memory ResolutionResult returned to the caller.
        self.suggestion_store = suggestion_store

    # ------------------------------------------------------------------
    # Protocol entrypoint
    # ------------------------------------------------------------------

    async def resolve_envelope(self, envelope: ExtractionEnvelope) -> ResolutionResult:
        result = ResolutionResult()
        for entity in envelope.entities:
            if entity.is_novel_type and entity.type not in result.novel_types:
                result.novel_types.append(entity.type)
            try:
                await self._resolve_one(envelope.org_id, entity, result)
            except Exception as exc:
                self.logger.warning(
                    "Entity resolution errored for local_id=%s (fail-closed to distinct): %s",
                    entity.local_id, exc,
                )
                self._mark_new(entity, result)
        return result

    # ------------------------------------------------------------------
    # Per-entity resolution
    # ------------------------------------------------------------------

    async def _resolve_one(
        self, org_id: str, entity: ExtractedEntity, result: ResolutionResult
    ) -> None:
        name = entity.name.strip()
        if not name:
            self._mark_new(entity, result)
            return

        candidates = await self.entity_vector_store.search_entities(
            query=name,
            org_id=org_id,
            entity_types=[entity.type],
            top_k=self.thresholds.candidate_pool,
        )
        if not candidates:
            self._mark_new(entity, result)
            return

        hard_key = self._extract_hard_key(entity)
        if hard_key:
            hard_match = self._find_hard_key_match(hard_key, candidates)
            if hard_match:
                self._mark_merge(entity, result, hard_match["entityId"], "hard_key", 1.0)
                return

        exact_match = self._find_exact_name_match(name, candidates)
        if exact_match:
            self._mark_merge(entity, result, exact_match["entityId"], "canonical_name", 1.0)
            return

        best_candidate, best_score = max(
            ((c, self._name_similarity(name, c)) for c in candidates),
            key=lambda pair: pair[1],
        )

        if best_score >= self.thresholds.hard:
            if entity.type in _NEVER_AUTO_MERGE_ABOVE_HARD:
                await self._adjudicate(org_id, entity, best_candidate, best_score, result)
            else:
                self._mark_merge(
                    entity, result, best_candidate["entityId"], "canonical_name", best_score
                )
            return

        if best_score >= self.thresholds.soft:
            await self._adjudicate(org_id, entity, best_candidate, best_score, result)
            return

        self._mark_new(entity, result)

    # ------------------------------------------------------------------
    # Signal extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_hard_key(entity: ExtractedEntity) -> str | None:
        for field_name in _HARD_KEY_ATTRIBUTES:
            value = entity.attributes.get(field_name)
            if value:
                return str(value).strip().lower()
        return None

    @staticmethod
    def _find_hard_key_match(hard_key: str, candidates: list[dict]) -> dict | None:
        for candidate in candidates:
            keys = {str(candidate.get("name", "")).strip().lower()}
            keys.update(str(a).strip().lower() for a in candidate.get("aliases") or [])
            if candidate.get("canonicalName"):
                keys.add(str(candidate["canonicalName"]).strip().lower())
            if hard_key in keys:
                return candidate
        return None

    @staticmethod
    def _find_exact_name_match(name: str, candidates: list[dict]) -> dict | None:
        normalized = name.strip().lower()
        for candidate in candidates:
            candidate_name = candidate.get("canonicalName") or candidate.get("name") or ""
            if str(candidate_name).strip().lower() == normalized:
                return candidate
        return None

    @staticmethod
    def _name_similarity(name: str, candidate: dict) -> float:
        target = str(candidate.get("canonicalName") or candidate.get("name") or "")
        return difflib.SequenceMatcher(
            None, name.strip().lower(), target.strip().lower()
        ).ratio()

    # ------------------------------------------------------------------
    # LLM adjudication (ambiguous band only)
    # ------------------------------------------------------------------

    async def _adjudicate(
        self,
        org_id: str,
        entity: ExtractedEntity,
        candidate: dict,
        score: float,
        result: ResolutionResult,
    ) -> None:
        decision: _LLMMergeDecision | None = None
        try:
            llm, _ = await get_llm_for_role(self.config_service, _LLM_ROLE)
            decision = await invoke_with_structured_output_and_reflection(
                llm,
                [HumanMessage(content=self._build_prompt(entity, candidate, score))],
                _LLMMergeDecision,
            )
        except Exception as exc:
            self.logger.warning(
                "LLM adjudication errored for local_id=%s vs candidate=%s "
                "(fail-closed to distinct): %s",
                entity.local_id, candidate.get("entityId"), exc,
            )

        if decision is None:
            self._mark_new(entity, result)
            return

        adjudication = LLMAdjudication(
            local_id=entity.local_id,
            candidate_node_id=candidate["entityId"],
            decision=decision.decision,
            confidence=decision.confidence,
            reason=decision.reason,
        )
        result.llm_adjudications.append(adjudication)

        if self.suggestion_store is not None:
            try:
                await self.suggestion_store.record(
                    org_id, adjudication, entity_type=entity.type, entity_name=entity.name,
                )
            except Exception as exc:
                self.logger.warning(
                    "SemanticLLMEntityResolver: suggestion persistence failed for "
                    "local_id=%s (non-fatal): %s", entity.local_id, exc,
                )

        if decision.decision == "merge":
            self._mark_merge(
                entity, result, candidate["entityId"], "llm_adjudicated", decision.confidence
            )
        else:
            self._mark_new(entity, result)

    @staticmethod
    def _build_prompt(entity: ExtractedEntity, candidate: dict, score: float) -> str:
        span_text = entity.text_span.text if entity.text_span else None
        return (
            "You are deduplicating entities in an enterprise knowledge graph. "
            "Decide whether the NEW mention refers to the SAME real-world entity "
            "as the EXISTING candidate, or is a DISTINCT entity. When uncertain, "
            "prefer 'distinct' — an incorrect merge is worse than a duplicate.\n\n"
            f"Entity type: {entity.type}\n"
            f"Name-similarity score: {score:.2f}\n\n"
            f"NEW mention:\n"
            f"  name: {entity.name}\n"
            f"  attributes: {entity.attributes}\n"
            f"  context: {span_text or 'n/a'}\n\n"
            f"EXISTING candidate:\n"
            f"  id: {candidate.get('entityId')}\n"
            f"  name: {candidate.get('name')}\n"
            f"  aliases: {candidate.get('aliases') or []}\n"
        )

    # ------------------------------------------------------------------
    # Result bookkeeping
    # ------------------------------------------------------------------

    @staticmethod
    def _mark_new(entity: ExtractedEntity, result: ResolutionResult) -> None:
        new_id = str(uuid.uuid4())
        result.local_to_canonical[entity.local_id] = new_id
        result.merges.append(
            MergeRecord(
                local_id=entity.local_id,
                canonical_node_id=new_id,
                is_new_node=True,
                matched_signal="new",
                confidence=1.0,
            )
        )

    @staticmethod
    def _mark_merge(
        entity: ExtractedEntity,
        result: ResolutionResult,
        canonical_node_id: str,
        signal: str,
        confidence: float,
    ) -> None:
        result.local_to_canonical[entity.local_id] = canonical_node_id
        result.merges.append(
            MergeRecord(
                local_id=entity.local_id,
                canonical_node_id=canonical_node_id,
                is_new_node=False,
                matched_signal=signal,
                confidence=confidence,
            )
        )

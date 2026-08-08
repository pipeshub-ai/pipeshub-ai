"""Durable queue of ambiguous LLM-adjudicated merge decisions awaiting human
review (KG Clean Rebuild plan, Phase 7 / Part E governance "list_suggestions").

``SemanticLLMEntityResolver`` (Part C) already makes an auto-apply decision
for every ambiguous pair the moment the LLM responds — this store does not
change that. It exists so a **copy** of each ambiguous decision can be
audited/reviewed after the fact and, for the subset an admin disagrees with,
manually corrected via :class:`app.modules.knowledge_graph.governance.merge.EntityMergeService`
or left as distinct. Recording is opt-in and best-effort (see
``resolution.py``'s optional ``suggestion_store`` constructor arg) so a
storage hiccup here never blocks entity resolution itself.

Storage: the existing generic node primitives on ``IGraphDBProvider``
(``batch_upsert_nodes`` / ``get_nodes_by_filters`` / ``update_node``) against
a dedicated ``kgMergeSuggestions`` collection — no new abstract provider
methods needed (see ``governance/__init__.py``).
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any, Literal

from app.config.constants.arangodb import CollectionNames
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    import logging

    from app.modules.knowledge_graph.contracts.resolution import LLMAdjudication
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

_COLLECTION = CollectionNames.KG_MERGE_SUGGESTIONS.value

SuggestionStatus = Literal["pending", "approved", "rejected"]


def _suggestion_id(org_id: str, local_id: str, candidate_node_id: str) -> str:
    """Deterministic id so re-running resolution against the same ambiguous
    pair (e.g. a document re-extracted after an edit) upserts the same
    suggestion instead of piling up duplicates for an unresolved decision.
    """
    # Identity key, not a security boundary -- collision resistance, not
    # cryptographic strength, is what matters here.
    digest = hashlib.sha1(
        f"{org_id}:{local_id}:{candidate_node_id}".encode()
    ).hexdigest()
    return f"sugg_{digest[:24]}"


class MergeSuggestionStore:
    """Persists and reviews :class:`LLMAdjudication` records."""

    def __init__(self, graph_provider: "IGraphDBProvider", logger: logging.Logger) -> None:
        self.graph_provider = graph_provider
        self.logger = logger

    async def record(
        self,
        org_id: str,
        adjudication: "LLMAdjudication",
        *,
        entity_type: str = "",
        entity_name: str = "",
    ) -> str | None:
        """Persist one ambiguous adjudication as a pending suggestion.
        Best-effort: returns ``None`` (and logs) on storage failure rather
        than raising, so this never breaks the resolution path it's called
        from.
        """
        if not org_id:
            return None
        suggestion_id = _suggestion_id(org_id, adjudication.local_id, adjudication.candidate_node_id)
        doc = {
            "id": suggestion_id,
            "orgId": org_id,
            "localId": adjudication.local_id,
            "candidateNodeId": adjudication.candidate_node_id,
            "entityType": entity_type,
            "entityName": entity_name,
            "llmDecision": adjudication.decision,
            "llmConfidence": adjudication.confidence,
            "llmReason": adjudication.reason,
            "modelId": adjudication.model_id,
            "status": "pending",
            "createdAtTimestamp": get_epoch_timestamp_in_ms(),
        }
        try:
            await self.graph_provider.batch_upsert_nodes([doc], collection=_COLLECTION)
            return suggestion_id
        except Exception as exc:
            self.logger.warning(
                "MergeSuggestionStore.record failed for local_id=%s (non-fatal): %s",
                adjudication.local_id, exc,
            )
            return None

    async def list_suggestions(
        self, org_id: str, *, status: SuggestionStatus | None = "pending", limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List suggestions for ``org_id``, newest first. ``status=None``
        returns suggestions in any state (approved/rejected included) for
        audit purposes.
        """
        if not org_id:
            return []
        filters: dict[str, Any] = {"orgId": org_id}
        if status is not None:
            filters["status"] = status
        try:
            rows = await self.graph_provider.get_nodes_by_filters(_COLLECTION, filters)
        except Exception as exc:
            self.logger.warning("MergeSuggestionStore.list_suggestions failed: %s", exc)
            return []
        rows.sort(key=lambda r: r.get("createdAtTimestamp") or 0, reverse=True)
        # get_nodes_by_filters returns raw docs, not the generic-id-translated
        # shape get_document gives — ArangoDB drops the literal 'id' field in
        # favor of '_key' on write (see batch_upsert_nodes), Neo4j keeps 'id'.
        # Normalize so every row has 'id' regardless of backend.
        for row in rows:
            if not row.get("id") and row.get("_key"):
                row["id"] = row["_key"]
        return rows[:limit]

    async def resolve(
        self,
        org_id: str,
        suggestion_id: str,
        outcome: Literal["approved", "rejected"],
        *,
        resolved_by: str | None = None,
    ) -> dict[str, Any] | None:
        """Mark a suggestion as reviewed. Returns the pre-update suggestion
        document (so the caller — the governance route — has the
        ``localId``/``candidateNodeId`` needed to actually perform a merge on
        ``approved``), or ``None`` if it doesn't exist (or belongs to a
        different org — ``get_document`` is a plain key lookup with no
        built-in org scoping, so that check happens here).
        """
        doc = await self.graph_provider.get_document(suggestion_id, _COLLECTION)
        if not doc or doc.get("orgId") != org_id:
            return None
        await self.graph_provider.update_node(
            key=suggestion_id,
            collection=_COLLECTION,
            node_updates={
                "status": outcome,
                "resolvedBy": resolved_by,
                "resolvedAtTimestamp": get_epoch_timestamp_in_ms(),
            },
        )
        return doc


__all__ = ["MergeSuggestionStore", "SuggestionStatus"]

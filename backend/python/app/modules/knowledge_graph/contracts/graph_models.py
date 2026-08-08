"""Canonical graph node/edge contracts, produced by indexing (never by
extraction directly) — see KG Clean Rebuild plan Part D.

Edges are bi-temporal, Graphiti-style: ``valid_at``/``invalid_at`` track when
a fact was true *in the world*; ``created_at``/``expired_at`` track when this
system knew about it. A contradiction closes the old edge (``invalid_at`` +
``expired_at`` set) rather than deleting it, so ``as_of`` queries can
reconstruct graph state at any point in either timeline (Phase 6).
"""

from typing import Any

from pydantic import BaseModel, Field

from app.models.entities import EntityTypeCategory
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class ProvenanceRef(BaseModel):
    """One source document/extraction that contributed to a node or edge."""
    document_id: str
    local_id: str
    extraction_mode: str
    extracted_at: int | None = Field(default=None, description="Epoch ms")


class GraphNode(BaseModel):
    """A canonical entity node, post entity-resolution.

    ``node_id`` is the global canonical id minted by the indexing pipeline —
    never a document-scoped ``local_id``. Multiple envelopes' ``local_id``s
    can resolve to the same ``node_id`` (that is the point of resolution).
    """
    node_id: str
    org_id: str
    canonical_type: str
    type_category: EntityTypeCategory
    domain: str = ""
    canonical_name: str = ""
    attributes: dict[str, Any] = Field(default_factory=dict)
    aliases: list[str] = Field(default_factory=list)
    embedding_ref: str | None = Field(
        default=None, description="Point id in the entity vector store, if embedded"
    )
    provenance: list[ProvenanceRef] = Field(default_factory=list)
    created_at: int = Field(default_factory=get_epoch_timestamp_in_ms, description="Epoch ms")
    updated_at: int = Field(default_factory=get_epoch_timestamp_in_ms, description="Epoch ms")


class GraphEdge(BaseModel):
    """A canonical, bi-temporal relationship between two :class:`GraphNode`\\ s."""
    edge_id: str
    org_id: str
    subject_node_id: str
    object_node_id: str
    canonical_type: str
    type_category: EntityTypeCategory
    attributes: dict[str, Any] = Field(default_factory=dict)
    provenance: list[ProvenanceRef] = Field(default_factory=list)

    # Valid time: when the fact was/is true in the world. Epoch ms throughout,
    # matching Record/RecordGroup timestamp conventions elsewhere in the repo.
    valid_at: int = Field(default_factory=get_epoch_timestamp_in_ms)
    invalid_at: int | None = Field(
        default=None, description="Set when a contradicting fact supersedes this edge"
    )

    # Transaction time: when this system learned about it.
    created_at: int = Field(default_factory=get_epoch_timestamp_in_ms)
    expired_at: int | None = Field(
        default=None, description="Set alongside invalid_at — never mutated independently"
    )

    @property
    def is_current(self) -> bool:
        return self.invalid_at is None and self.expired_at is None

    def is_active_as_of(self, as_of: int) -> bool:
        """True when this edge was valid (in the world) and known (in the
        system) at ``as_of`` — the standard Graphiti bi-temporal check."""
        if self.valid_at > as_of:
            return False
        if self.invalid_at is not None and self.invalid_at <= as_of:
            return False
        if self.created_at > as_of:
            return False
        return not (self.expired_at is not None and self.expired_at <= as_of)

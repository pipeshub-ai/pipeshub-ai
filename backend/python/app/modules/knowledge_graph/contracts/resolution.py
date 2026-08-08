"""Entity resolution contracts (KG Clean Rebuild plan, Part C / Part D).

The ``EntityResolutionService`` protocol is the seam between extraction
(Phase 4) and the canonical graph writer (Phase 6): extraction never assigns
global identity, resolution always does. Implemented by
``modules.knowledge_graph.indexing.resolution.SemanticLLMEntityResolver``
(Phase 3).
"""

from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel, Field

from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from app.modules.knowledge_graph.contracts.envelope import ExtractionEnvelope


class MergeRecord(BaseModel):
    """One local entity resolved onto a (possibly pre-existing) canonical node."""
    local_id: str
    canonical_node_id: str
    is_new_node: bool
    matched_signal: str = Field(
        description="'hard_key' | 'canonical_name' | 'llm_adjudicated' | 'new'"
    )
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class LLMAdjudication(BaseModel):
    """Audit record for one ambiguous-range LLM merge/distinct decision."""
    local_id: str
    candidate_node_id: str
    decision: str = Field(description="'merge' | 'distinct'")
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""
    model_id: str | None = None
    decided_at: int = Field(default_factory=get_epoch_timestamp_in_ms, description="Epoch ms")


class ResolutionResult(BaseModel):
    """Output of resolving one :class:`ExtractionEnvelope` against the
    existing canonical graph."""
    local_to_canonical: dict[str, str] = Field(
        default_factory=dict, description="local_id -> canonical node_id"
    )
    merges: list[MergeRecord] = Field(default_factory=list)
    novel_types: list[str] = Field(default_factory=list)
    llm_adjudications: list[LLMAdjudication] = Field(default_factory=list)

    def canonical_id_for(self, local_id: str) -> str | None:
        return self.local_to_canonical.get(local_id)


class EntityResolutionService(Protocol):
    """Pure resolution seam — no I/O contract beyond "reads/writes the graph
    and vector store it was constructed with"."""

    async def resolve_envelope(self, envelope: "ExtractionEnvelope") -> ResolutionResult:
        """Resolve every entity in ``envelope`` to a canonical node id,
        creating new canonical nodes for anything below the merge threshold.
        Must be fail-closed: on any internal (e.g. LLM) error, ambiguous
        candidates resolve to *distinct* new nodes rather than risking an
        incorrect merge."""
        ...

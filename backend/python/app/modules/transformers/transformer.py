from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Set
from pydantic import BaseModel
from app.models.entities import Record

class ReconciliationContext(BaseModel):
    """Context for reconciliation-based incremental indexing."""
    new_metadata: Dict[str, Any] = {}
    blocks_to_index_ids: Optional[Set[str]] = None  # None means index all
    block_ids_to_delete: Optional[Set[str]] = None  # None means delete none

#: ``TransformContext.settings`` key: the record is handed to the pipeline
#: stages once indexed, so the indexed write also marks extraction QUEUED (the
#: classify stage owns it from there). Without it a reader could see the record
#: indexed with an extractionStatus that looks final, and a duplicate would copy it.
ENRICHMENT_FOLLOWS = "enrichment_follows"

#: ``TransformContext.settings`` key, set by the indexed write in place of
#: ``ENRICHMENT_FOLLOWS`` when classification waits for the record's first use:
#: extraction is marked NOT_STARTED and the classify stage is not dispatched.
ENRICHMENT_DEFERRED = "enrichment_deferred"

#: Record field written with that NOT_STARTED, so a hand-off lost to a crash is
#: told apart from a cut-short enrichment or an older record, which are final.
EXTRACTION_DEFERRED_FIELD = "extractionDeferred"

#: ``TransformContext.settings`` key: seconds the image-description pass may
#: take before the remaining images are stored undescribed. Unset means no limit.
IMAGE_DESCRIPTION_BUDGET_S = "image_description_budget_s"


class TransformContext(BaseModel):
    record: Record
    settings: Dict[str, Any] = {}
    event_type: Optional[str] = None
    reconciliation_context: Optional[ReconciliationContext] = None
    prev_virtual_record_id: Optional[str] = None
    # ``EntityResolution`` set by the resolver in apply mode and read by
    # GraphDBTransformer; ``Any`` keeps this module free of that import.
    entity_resolution: Optional[Any] = None
    # Set when extraction was skipped by configuration (not failed), e.g. no LLM configured.
    extraction_skip_reason: str | None = None

class Transformer(ABC):
    @abstractmethod
    async def apply(self, ctx: TransformContext) -> TransformContext:
        """Return a new ctx (or the same with mutated record) after transformation."""

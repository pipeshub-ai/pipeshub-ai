from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Set
from pydantic import BaseModel
from app.models.entities import Record

class ReconciliationContext(BaseModel):
    """Context for reconciliation-based incremental indexing."""
    new_metadata: Dict[str, Any] = {}
    blocks_to_index_ids: Optional[Set[str]] = None  # None means index all
    block_ids_to_delete: Optional[Set[str]] = None  # None means delete none

class TransformContext(BaseModel):
    record: Record
    settings: Dict[str, Any] = {}
    event_type: Optional[str] = None
    reconciliation_context: Optional[ReconciliationContext] = None
    prev_virtual_record_id: Optional[str] = None
    # Decided once per record from mime/extension by `events._is_code_file`, not
    # from recordType — connectors type every repo blob CODE_FILE, including
    # README and asset files that must not get the code prompt.
    is_code: bool = False

class Transformer(ABC):
    @abstractmethod
    async def apply(self, ctx: TransformContext) -> TransformContext:
        """Return a new ctx (or the same with mutated record) after transformation."""

from abc import ABC, abstractmethod
from typing import Any, Dict

from pydantic import BaseModel

from app.models.entities import Record


class TransformContext(BaseModel):
    record: Record
    settings: Dict[str, Any] = {}

class Transformer(ABC):
    @abstractmethod
    async def apply(self, ctx: TransformContext) -> TransformContext:
        """Return a new ctx (or the same with mutated record) after transformation."""

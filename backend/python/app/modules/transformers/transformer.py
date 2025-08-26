from pydantic import BaseModel
from typing import Dict, Any
from app.models.entities import Record
from abc import ABC, abstractmethod

class TransformContext(BaseModel):
    record: Record
    settings: Dict[str, Any] = {}

class Transformer(ABC):
    @abstractmethod
    async def apply(self, ctx: TransformContext) -> TransformContext:
        """Return a new ctx (or the same with mutated record) after transformation."""
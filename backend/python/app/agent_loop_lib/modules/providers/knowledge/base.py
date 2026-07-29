from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel


class KnowledgeChunk(BaseModel):
    content: str
    source: str
    score: float = 1.0


class KnowledgeProvider(ABC):
    """Abstract read-only knowledge / RAG source."""

    @abstractmethod
    async def query(self, query: str, top_k: int = 5) -> list[KnowledgeChunk]: ...

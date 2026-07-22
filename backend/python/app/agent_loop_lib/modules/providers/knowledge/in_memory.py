from __future__ import annotations

from app.agent_loop_lib.modules.providers.knowledge.base import (
    KnowledgeChunk,
    KnowledgeProvider,
)


class InMemoryKnowledge(KnowledgeProvider):
    """Static list-backed knowledge for testing."""

    def __init__(self, chunks: list[KnowledgeChunk] | None = None) -> None:
        self._chunks: list[KnowledgeChunk] = chunks if chunks is not None else []

    async def query(self, query: str, top_k: int = 5) -> list[KnowledgeChunk]:
        query_lower = query.lower()
        if query_lower == "":
            matched = list(self._chunks)
        else:
            matched = [
                chunk for chunk in self._chunks if query_lower in chunk.content.lower()
            ]
        return matched[:top_k]

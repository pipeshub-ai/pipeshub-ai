from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field


class MemoryScope(BaseModel):
    """Scopes memory operations to a specific agent/user/session.

    Production backends (mem0, Graphiti, Zep) use these to namespace memories.
    """

    agent_id: str | None = None
    user_id: str | None = None
    session_id: str | None = None
    # Agent teams (Phase 4): set from RunContext.team_id for siblings
    # spawned together in the same parallel spawn_agent batch — lets a
    # team-scoped query (team_id set, agent_id left None) retrieve every
    # teammate's automatic turn-memory writes together.
    team_id: str | None = None


class MemoryResult(BaseModel):
    """A single retrieved memory item."""

    id: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    score: float = 1.0


class MemoryProvider(ABC):
    """Abstract memory store designed for production AI memory backends.

    Intended implementations:
    - mem0        — https://github.com/mem0ai/mem0
    - Graphiti    — https://github.com/getzep/graphiti (graph-based, temporal)
    - Zep         — https://github.com/getzep/zep
    - Pinecone / Weaviate / Qdrant (vector stores)
    - InMemory    — test fixture only, no vector search

    The ABC is shaped around semantic add/search semantics, not key-value.
    Use MemoryScope to namespace across agents, users, and sessions.
    """

    @abstractmethod
    async def add(
        self,
        content: str,
        metadata: dict[str, Any] | None = None,
        scope: MemoryScope | None = None,
    ) -> str:
        """Store content. Returns the assigned memory_id."""
        ...

    @abstractmethod
    async def search(
        self,
        query: str,
        top_k: int = 10,
        scope: MemoryScope | None = None,
    ) -> list[MemoryResult]:
        """Semantic/vector search. Each backend implements appropriate retrieval
        (embedding similarity, BM25, graph traversal, etc.)."""
        ...

    @abstractmethod
    async def get(self, memory_id: str) -> MemoryResult | None:
        """Retrieve a specific memory by ID. Returns None if not found."""
        ...

    @abstractmethod
    async def delete(self, memory_id: str) -> None:
        """Delete a specific memory. Silent no-op if not found."""
        ...

    @abstractmethod
    async def clear(self, scope: MemoryScope | None = None) -> None:
        """Delete all memories, optionally filtered to a scope."""
        ...

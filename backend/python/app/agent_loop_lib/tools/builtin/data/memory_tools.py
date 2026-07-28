from __future__ import annotations

import json
from typing import Any

from app.agent_loop_lib.modules.providers.memory.base import MemoryProvider
from app.agent_loop_lib.tools.base import (
    ParameterType,
    Tag,
    Tool,
    ToolOutput,
    ToolParameter,
)


class MemoryReadTool(Tool):
    """Retrieve a specific memory by ID."""

    def __init__(self, memory: MemoryProvider) -> None:
        self._memory = memory

    @property
    def name(self) -> str:
        return "memory_read"

    @property
    def short_description(self) -> str:
        return "Retrieve a specific memory by ID."

    @property
    def description(self) -> str:
        return "Retrieve a specific memory by ID"

    @property
    def path(self) -> str:
        return "/toolsets/memory/memory_read"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="memory_id",
                type=ParameterType.STRING,
                description="The memory ID to retrieve",
                required=True,
            ),
        ]

    async def execute(self, **kwargs: Any) -> ToolOutput:
        memory_id: str = kwargs["memory_id"]
        result = await self._memory.get(memory_id)
        if result is None:
            return ToolOutput(success=True, data={"found": False})
        return ToolOutput(
            success=True,
            data={
                "found": True,
                "id": result.id,
                "content": result.content,
                "metadata": result.metadata,
            },
        )


class MemoryWriteTool(Tool):
    """Store information in memory for future retrieval."""

    def __init__(self, memory: MemoryProvider) -> None:
        self._memory = memory

    @property
    def name(self) -> str:
        return "memory_write"

    @property
    def short_description(self) -> str:
        return "Store information in memory for future retrieval."

    @property
    def description(self) -> str:
        return "Store information in memory for future retrieval"

    @property
    def path(self) -> str:
        return "/toolsets/memory/memory_write"

    @property
    def tags(self) -> list[Tag]:
        return [Tag("category", "write")]

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="content",
                type=ParameterType.STRING,
                description="Content to store",
                required=True,
            ),
            ToolParameter(
                name="metadata",
                type=ParameterType.STRING,
                description="JSON metadata string",
                required=False,
                default=None,
            ),
        ]

    async def execute(self, **kwargs: Any) -> ToolOutput:
        content: str = kwargs["content"]
        metadata_str: str | None = kwargs.get("metadata")
        parsed: dict[str, Any] = {}
        if metadata_str is not None:
            try:
                parsed = json.loads(metadata_str)
            except (json.JSONDecodeError, ValueError):
                parsed = {}
        mid = await self._memory.add(content, metadata=parsed)
        return ToolOutput(success=True, data={"memory_id": mid})


class MemorySearchTool(Tool):
    """Search memory using a natural language query."""

    def __init__(self, memory: MemoryProvider) -> None:
        self._memory = memory

    @property
    def name(self) -> str:
        return "memory_search"

    @property
    def short_description(self) -> str:
        return "Search memory using a natural language query."

    @property
    def description(self) -> str:
        return "Search memory using a natural language query"

    @property
    def path(self) -> str:
        return "/toolsets/memory/memory_search"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="query",
                type=ParameterType.STRING,
                description="Search query",
                required=True,
            ),
            ToolParameter(
                name="top_k",
                type=ParameterType.STRING,
                description="Maximum results (default 5)",
                required=False,
                default=None,
            ),
        ]

    async def execute(self, **kwargs: Any) -> ToolOutput:
        query: str = kwargs["query"]
        top_k: str = kwargs.get("top_k") or "5"
        results = await self._memory.search(query, top_k=int(top_k))
        return ToolOutput(
            success=True,
            data=[{"id": r.id, "content": r.content, "score": r.score} for r in results],
        )


class MemoryConsolidateTool(Tool):
    """Memory curation: the agent — not a background job — decides
    when a set of memories has become redundant/stale and replaces them
    with one distilled summary it authors itself. Deliberately "agent-
    curated" per the roadmap: the model reads what memory_search returned,
    writes the consolidated summary as plain text, and this tool just does
    the mechanical delete-many + add-one swap. No LLM call happens inside
    the tool/hook layer — consolidation quality is entirely the calling
    agent's judgment, kept auditable via the `consolidated_from` metadata.
    """

    def __init__(self, memory: MemoryProvider) -> None:
        self._memory = memory

    @property
    def name(self) -> str:
        return "memory_consolidate"

    @property
    def short_description(self) -> str:
        return "Replace several redundant/stale memories with one summary."

    @property
    def description(self) -> str:
        return (
            "Replace several redundant/stale memories with one summary you write. "
            "Fetch candidates with memory_search first, then pass their ids here "
            "along with your consolidated summary text."
        )

    @property
    def path(self) -> str:
        return "/toolsets/memory/memory_consolidate"

    @property
    def tags(self) -> list[Tag]:
        return [Tag("category", "write")]

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="memory_ids",
                type=ParameterType.ARRAY,
                description="IDs of the memories being merged/replaced",
                required=True,
                items={"type": "string"},
            ),
            ToolParameter(
                name="summary",
                type=ParameterType.STRING,
                description="The consolidated summary to store in their place",
                required=True,
            ),
        ]

    async def execute(self, **kwargs: Any) -> ToolOutput:
        memory_ids: list[str] = list(kwargs.get("memory_ids") or [])
        summary: str = kwargs["summary"]
        removed: list[str] = []
        for mid in memory_ids:
            existing = await self._memory.get(mid)
            if existing is not None:
                await self._memory.delete(mid)
                removed.append(mid)
        new_id = await self._memory.add(summary, metadata={"consolidated_from": removed})
        return ToolOutput(success=True, data={"memory_id": new_id, "consolidated_from": removed})

from __future__ import annotations

from typing import Any

from app.agent_loop_lib.core.types import Source
from app.agent_loop_lib.modules.providers.knowledge.base import KnowledgeProvider
from app.agent_loop_lib.tools.base import ParameterType, Tool, ToolOutput, ToolParameter


class KnowledgeQueryTool(Tool):
    """Query the knowledge base for relevant information."""

    def __init__(self, knowledge: KnowledgeProvider) -> None:
        self._knowledge = knowledge

    @property
    def name(self) -> str:
        return "knowledge_query"

    @property
    def short_description(self) -> str:
        return "Query the knowledge base for relevant information."

    @property
    def description(self) -> str:
        return "Query the knowledge base for relevant information"

    @property
    def path(self) -> str:
        return "/toolsets/knowledge/knowledge_query"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="query",
                type=ParameterType.STRING,
                description="Natural language query",
                required=True,
            ),
            ToolParameter(
                name="top_k",
                type=ParameterType.STRING,
                description="Maximum results",
                required=False,
                default=None,
            ),
        ]

    async def execute(self, **kwargs: Any) -> ToolOutput:
        query: str = kwargs["query"]
        top_k: str = kwargs.get("top_k") or "5"
        chunks = await self._knowledge.query(query, top_k=int(top_k))
        content = [
            {"content": c.content, "source": c.source, "score": c.score}
            for c in chunks
        ]
        sources = [Source(query=query, title=c.source) for c in chunks if c.source]
        return ToolOutput(success=True, data=content, sources=sources)

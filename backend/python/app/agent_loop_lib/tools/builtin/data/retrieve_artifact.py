"""Built-in tool: retrieve artifact content from the artifact store.

Used by the model when it needs full content from a previously-compacted
tool result artifact for synthesis.
"""

from __future__ import annotations

from typing import Any, Protocol

from app.agent_loop_lib.core.tokens import count_text_tokens
from app.agent_loop_lib.tools.base import (
    ParameterType,
    Tool,
    ToolOutput,
    ToolParameter,
)

_DEFAULT_MAX_CONTENT_TOKENS = 35_000


class ArtifactReader(Protocol):
    """Read-side contract for fetching persisted tool-result artifacts."""

    async def get(self, artifact_id: str) -> str | None: ...


class RetrieveArtifactContentTool(Tool):

    def __init__(
        self,
        store: ArtifactReader,
        *,
        max_content_tokens: int = _DEFAULT_MAX_CONTENT_TOKENS,
    ) -> None:
        self._store = store
        self._max_content_tokens = max_content_tokens

    @property
    def name(self) -> str:
        return "retrieve_artifact_content"

    @property
    def short_description(self) -> str:
        return "Retrieve full content of a previously-compacted artifact."

    @property
    def description(self) -> str:
        return (
            "Fetch the full content of a tool-result artifact that was "
            "previously compacted into a reference. Use this when you need "
            "the original data for text synthesis."
        )

    @property
    def path(self) -> str:
        return "/tools/data/retrieve_artifact_content"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="artifact_id",
                type=ParameterType.STRING,
                required=True,
                description="The artifact ID to retrieve.",
            ),
            ToolParameter(
                name="max_lines",
                type=ParameterType.INTEGER,
                required=False,
                default=None,
                description="Limit output to first N lines.",
            ),
        ]

    async def execute(
        self,
        artifact_id: str,
        max_lines: int | None = None,
        **kwargs: Any,
    ) -> ToolOutput:
        content = await self._store.get(artifact_id)
        if content is None:
            return ToolOutput(
                success=False,
                error=f"Artifact {artifact_id!r} not found.",
            )
        if max_lines is not None:
            lines = content.splitlines(keepends=True)
            content = "".join(lines[:max_lines])

        token_count = count_text_tokens(content)
        if token_count > self._max_content_tokens:
            char_limit = self._max_content_tokens * 4
            content = (
                content[:char_limit]
                + f"\n\n[…truncated: {token_count} tokens total, showing first "
                f"{self._max_content_tokens}. Filter and curate the visible data "
                f"before generating code or synthesis.]"
            )
        return ToolOutput(success=True, data=content)

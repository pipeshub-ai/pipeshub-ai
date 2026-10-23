"""Built-in tool: retrieve artifact content from the artifact store.

Used by the model when it needs full content from a previously-compacted
tool result artifact for synthesis.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from app.agent_loop_lib.hooks.middleware.builtin.budget_reduction import DEFAULT_MAX_RESULT_CHARS
from app.agent_loop_lib.tools.base import (
    ParameterType,
    Tool,
    ToolOutput,
    ToolParameter,
)

logger = logging.getLogger(__name__)

# Room kept for the "showing x–y of n" footer, so a full page plus its footer
# stays under the per-result cap and is never cut in the middle by it.
_FOOTER_RESERVE = 256

# A page ends at the last line break in its second half, so rows are not split
# across pages; a page with no line break that late is cut by characters.
_MIN_LINE_SNAP = 0.5


class ArtifactReader(Protocol):
    """Read-side contract for fetching persisted tool-result artifacts."""

    async def get(self, artifact_id: str) -> str | None: ...


class RetrieveArtifactContentTool(Tool):

    def __init__(
        self,
        store: ArtifactReader,
        *,
        max_result_chars: int = DEFAULT_MAX_RESULT_CHARS,
    ) -> None:
        """`max_result_chars` is the per-result cap the context pipeline
        applies (`shape_budget_reduction`); pages are sized under it."""
        self._store = store
        self._page_chars = max(1, max_result_chars - _FOOTER_RESERVE)

    @property
    def name(self) -> str:
        return "retrieve_artifact_content"

    @property
    def short_description(self) -> str:
        return "Retrieve full content of a previously-compacted artifact."

    @property
    def description(self) -> str:
        return (
            "Fetch the content of a tool-result artifact that was previously "
            "compacted into a reference. Use this when you need the original "
            "data for text synthesis. Large artifacts come back one page at a "
            "time; the footer says which characters were shown and the offset "
            "to pass for the next page."
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
                name="offset",
                type=ParameterType.INTEGER,
                required=False,
                default=0,
                description="Character offset to start reading from, as given by the previous page's footer.",
            ),
            ToolParameter(
                name="limit",
                type=ParameterType.INTEGER,
                required=False,
                default=None,
                description=f"Maximum characters to return (at most {self._page_chars}).",
            ),
        ]

    async def execute(
        self,
        artifact_id: str,
        offset: int = 0,
        limit: int | None = None,
        **kwargs: Any,
    ) -> ToolOutput:
        content = await self._store.get(artifact_id)
        if content is None:
            return ToolOutput(
                success=False,
                error=f"Artifact {artifact_id!r} not found.",
            )
        total = len(content)
        offset = offset or 0
        if offset < 0 or (offset >= total and total > 0):
            return ToolOutput(
                success=False,
                error=f"offset {offset} is outside artifact {artifact_id!r}, which has {total} characters.",
            )
        size = min(limit, self._page_chars) if limit and limit > 0 else self._page_chars
        if offset == 0 and total <= size:
            return ToolOutput(success=True, data=content)

        end = min(offset + size, total)
        if end < total:
            line_end = content.rfind("\n", offset, end)
            if line_end >= offset + int(size * _MIN_LINE_SNAP):
                end = line_end + 1

        footer = f"\n\n[Showing characters {offset + 1:,}–{end:,} of {total:,}."
        if end < total:
            footer += f' Continue with retrieve_artifact_content(artifact_id="{artifact_id}", offset={end}).'
        footer += "]"
        logger.info(
            "retrieve_artifact_content: artifact %s page %d-%d of %d chars",
            artifact_id, offset, end, total,
        )
        return ToolOutput(success=True, data=content[offset:end] + footer)

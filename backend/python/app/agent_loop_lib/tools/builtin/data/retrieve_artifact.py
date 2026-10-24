"""Built-in tool: retrieve artifact content from the artifact store.

Used by the model when it needs full content from a previously-compacted
tool result artifact for synthesis.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any, Protocol

from app.agent_loop_lib.hooks.middleware.builtin.budget_reduction import (
    DEFAULT_MAX_RESULT_CHARS,
)
from app.agent_loop_lib.tools.base import (
    ParameterType,
    Tool,
    ToolOutput,
    ToolParameter,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)

RETRIEVE_ARTIFACT_TOOL_NAME = "retrieve_artifact_content"

# Room kept for the "showing x–y of n" footer, so a full page plus its footer
# stays under the per-result cap and is never cut in the middle by it.
_FOOTER_RESERVE = 256

# A page ends at the last line break in its second half, so rows are not split
# across pages; a page with no line break that late is cut by characters.
_MIN_LINE_SNAP = 0.5

# A cap smaller than this leaves no room for a page next to its footer.
_MIN_RESULT_CHARS = 2 * _FOOTER_RESERVE

_FOOTER_RE = re.compile(r"\n\n\[Showing characters [\d,]+–[\d,]+ of (?P<total>[\d,]+)\.[^\n]*\]\Z")


def _footer(artifact_id: str, offset: int, end: int, total: int) -> str:
    footer = f"\n\n[Showing characters {offset + 1:,}–{end:,} of {total:,}."
    if end < total:
        footer += f' Continue with retrieve_artifact_content(artifact_id="{artifact_id}", offset={end}).'
    return footer + "]"


def _footer_room(artifact_id: str, total: int) -> int:
    """The longest footer a page of this artifact can get: one with a continuation offset."""
    last = max(total, 1)
    return len(_footer(artifact_id, last - 1, last - 1, last))


def _snap_to_line(text: str, size: int) -> int:
    """Characters of `text` to keep within `size`, ending on a line break when one is late enough."""
    if size >= len(text):
        return len(text)
    line_end = text.rfind("\n", 0, size)
    return line_end + 1 if line_end >= int(size * _MIN_LINE_SNAP) else size


def refit_artifact_page(text: str, limit: int, arguments: Mapping[str, Any]) -> str | None:
    """A `retrieve_artifact_content` result cut to `limit` characters as a shorter page.

    The context pipeline's per-result cap can be below a page when the window is small. Cutting
    the page anywhere else drops characters the footer's continuation offset then skips, so the
    page keeps its head and the footer is rewritten to the offset it actually reached. None when
    `limit` cannot hold a page and its footer.
    """
    artifact_id = str(arguments.get("artifact_id") or "")
    offset = int(arguments.get("offset") or 0)
    match = _FOOTER_RE.search(text)
    body = text[:match.start()] if match else text
    total = int(match["total"].replace(",", "")) if match else offset + len(body)
    room = limit - _footer_room(artifact_id, total)
    if not artifact_id or room <= 0:
        return None
    keep = _snap_to_line(body, room)
    return body[:keep] + _footer(artifact_id, offset, offset + keep, total)


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
        if max_result_chars < _MIN_RESULT_CHARS:
            raise ValueError(
                f"max_result_chars={max_result_chars} leaves no room for a page and its footer; "
                f"use at least {_MIN_RESULT_CHARS}",
            )
        self._store = store
        self._max_result_chars = max_result_chars
        self._page_chars = max_result_chars - _FOOTER_RESERVE

    @property
    def name(self) -> str:
        return RETRIEVE_ARTIFACT_TOOL_NAME

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
        # Sized with the footer this artifact actually gets, so page and footer stay under the cap.
        page_cap = self._max_result_chars - _footer_room(artifact_id, total)
        size = min(limit, page_cap) if limit and limit > 0 else page_cap
        if offset == 0 and total <= size:
            return ToolOutput(success=True, data=content)

        end = offset + _snap_to_line(content[offset:], size)
        footer = _footer(artifact_id, offset, end, total)
        logger.info(
            "retrieve_artifact_content: artifact %s page %d-%d of %d chars",
            artifact_id, offset, end, total,
        )
        return ToolOutput(success=True, data=content[offset:end] + footer)

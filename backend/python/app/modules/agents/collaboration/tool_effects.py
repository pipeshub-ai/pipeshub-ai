"""Single classification table for what a tool call can do to the outside world."""

from __future__ import annotations

import re
from enum import Enum
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from app.agent_loop_lib.tools.base import Tag


class ToolEffect(str, Enum):
    READ = "read"
    EGRESS = "egress"
    WRITE = "write"
    EXECUTE = "execute"


WRITE_TYPE_TAGS: Final = frozenset({"write", "create", "update", "delete", "destructive", "action"})
EXECUTE_TAG: Final = "execute"
EGRESS_TAG: Final = "egress"

# Paths of known outbound-fetch tools, for tools that carry no tag. `web_scrape` and
# browser navigation are only registered by agent_loop_lib's ControlPlane today.
_EGRESS_PATHS: Final = (
    re.compile(r"^/dynamic/[^/]+/fetch_url$"),
    re.compile(r"^/toolsets/web/web_scrape$"),
    re.compile(r"^/toolsets/browser/browser_navigate$"),
)


def classify_tool(tool_path: str, tags: tuple[Tag, ...] | list[Tag]) -> ToolEffect:
    """MCP tools carry no classification, so they count as WRITE unless tagged read."""
    pairs = [(str(t.key).lower(), str(t.value).lower()) for t in tags]
    values = {v for k, v in pairs if k in ("type", "category")}
    if EXECUTE_TAG in values:
        return ToolEffect.EXECUTE
    if values & WRITE_TYPE_TAGS:
        return ToolEffect.WRITE
    if EGRESS_TAG in values:
        return ToolEffect.EGRESS
    if tool_path.startswith("/mcp/") and "read" not in values:
        return ToolEffect.WRITE
    if any(p.match(tool_path) for p in _EGRESS_PATHS):
        return ToolEffect.EGRESS
    return ToolEffect.READ

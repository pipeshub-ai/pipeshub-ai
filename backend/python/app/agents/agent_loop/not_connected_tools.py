"""The attached toolsets and MCP servers the person asking hasn't connected, as stubs the model can call.

The run can't use them (`app.modules.agents.readiness` leaves them out), so each one is offered as a tool with only a
name and one line, no schema. A call runs nothing: it tells the model the tool isn't connected and keeps it as
needed by this answer, so the answer offers to connect exactly the tools it needed (`AnswerFinalizer`).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.tools.base import Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.errors import (
    DuplicateToolNameError,
    DuplicateToolPathError,
)

if TYPE_CHECKING:
    from app.agent_loop_lib.tools.registry import ToolRegistry
    from app.agents.agent_loop.context import AgentContext

# The run's `tool_state` key: the items whose stub this answer called, in call order.
NEEDED_TOOLS_STATE_KEY = "needed_unavailable_tools"
STUB_SUFFIX = "__not_connected"
_MAX_NAME = 80
_MAX_ABOUT = 100
_MAX_SLUG = 40


def _one_line(text: object, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


def _slug(item: Mapping[str, Any]) -> str:
    raw = str(item.get("name") or item.get("displayName") or "").lower()
    return re.sub(r"[^a-z0-9]+", "_", raw).strip("_")[:_MAX_SLUG] or "tool"


def _about(item: Mapping[str, Any]) -> str:
    """What a toolset does, from the app's own catalog. An MCP server's own words would need its sign-in."""
    if item.get("kind") != "toolset" or not item.get("name"):
        return ""
    from app.agents.registry.toolset_registry import get_toolset_registry

    try:
        metadata = get_toolset_registry().get_toolset_metadata(str(item["name"])) or {}
    except Exception:
        return ""
    about = _one_line(metadata.get("description"), _MAX_ABOUT)
    return f" {about.rstrip('.')}." if about else ""


class NotConnectedTool(Tool):
    """Holds the item's names alone: no credential, no client, nothing it could run."""

    def __init__(self, name: str, item: Mapping[str, Any], needed: list[dict[str, Any]]) -> None:
        self._name = name
        self._item = dict(item)
        self._needed = needed
        self._label = _one_line(item.get("displayName") or item.get("name"), _MAX_NAME) or "This tool"
        self._by_user = bool(item.get("userCanConnect"))

    @property
    def name(self) -> str:
        return self._name

    @property
    def short_description(self) -> str:
        return self.description

    @property
    def description(self) -> str:
        who = "the user can connect it" if self._by_user else "an admin must set it up"
        return f"{self._label}: not connected ({who}).{_about(self._item)} Call only if the request needs it."

    @property
    def path(self) -> str:
        return f"/toolsets/not_connected/{self._name}"

    @property
    def parameters(self) -> list[ToolParameter]:
        return []

    @property
    def silent(self) -> bool:
        # The answer's steps are read by everyone who reads the answer; these tools are its asker's own.
        return True

    async def execute(self, **kwargs: object) -> ToolOutput:
        key = (self._item.get("kind"), self._item.get("instanceId"))
        if all((i.get("kind"), i.get("instanceId")) != key for i in self._needed):
            # The mark tells this answer's list from one saved before the stubs, which named every tool left out.
            self._needed.append({**self._item, "needed": True})
        fix = (
            "Tell the user it needs connecting: a Connect button shows under your answer for them."
            if self._by_user else "Tell the user an admin must set it up."
        )
        return ToolOutput(
            success=True,
            data=f"{self._label} isn't connected, so nothing ran. {fix} Don't invent its results.",
        )


def register_not_connected_tools(tool_registry: ToolRegistry, context: AgentContext) -> list[str]:
    """One stub per toolset or MCP server this run left out (`tool_state["unavailable_tools"]`, the asker's own).
    A server that is gone, or not this person's to see (`not_found`), gets none. Returns the stubs' names."""
    needed: list[dict[str, Any]] = context.tool_state.setdefault(NEEDED_TOOLS_STATE_KEY, [])
    taken = set(tool_registry.names())
    names: list[str] = []
    for item in context.tool_state.get("unavailable_tools") or []:
        if not isinstance(item, Mapping) or not item.get("instanceId") or item.get("problem") == "not_found":
            continue
        slug, n = _slug(item), 1
        name = f"{slug}{STUB_SUFFIX}"
        while name in taken:
            n += 1
            name = f"{slug}_{n}{STUB_SUFFIX}"
        try:
            tool_registry.register_tool(NotConnectedTool(name, item, needed))
        except (DuplicateToolNameError, DuplicateToolPathError):
            continue
        taken.add(name)
        names.append(name)
    return names


__all__ = ["NEEDED_TOOLS_STATE_KEY", "NotConnectedTool", "register_not_connected_tools"]

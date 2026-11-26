"""Tools of a granted item its owner has not connected: listed, never run (WS-09, OWD-7 "mask, don't remove").

An unconnected MCP server cannot be asked what its tools are, so the run lists the tools its pin granted under the
names discovery gives them, each answering how to connect the server. A model or engine that calls one learns why
it cannot run and where the person connects it, instead of finding the tool gone.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final, override

from app.harness.credentials.connect import connect_text
from harness_core.effects.failures import ToolFailure
from harness_core.tools.base import Tool, ToolOutput, ToolParameter
from harness_core.tools.tags import TAG_EFFECT_READ, TAG_SINK_NONE

if TYPE_CHECKING:
    from harness_core.tools.base import Tag

# Whatever the engine sends: the call is refused before any argument matters.
_ANY_ARGUMENTS: Final[dict[str, Any]] = {"type": "object", "additionalProperties": True}


class UnconnectedMcpTool(Tool):
    """One granted tool of an MCP server whose credential is missing."""

    def __init__(self, *, instance: str, server: str, tool: str, name: str) -> None:
        super().__init__()
        self._instance = instance
        self._server = server
        self._tool = tool
        self._name = name

    @property
    @override
    def name(self) -> str:
        return self._name

    @property
    @override
    def short_description(self) -> str:
        return f"{self._server}: {self._tool} (not connected)"

    @property
    @override
    def description(self) -> str:
        return (
            f"{self._server} tool {self._tool}. {self._server} is not connected for this person, so the tool "
            "cannot run; calling it returns where they connect it."
        )

    @property
    @override
    def path(self) -> str:
        return f"/mcp/{self._instance}/{self._tool}"

    @property
    @override
    def tags(self) -> list[Tag]:
        # It reads nothing and sends nothing: no approval stands between the call and its answer.
        return [TAG_EFFECT_READ, TAG_SINK_NONE]

    @property
    @override
    def display_name(self) -> str:
        return f"{self._server} · {self._tool}"

    @property
    @override
    def parameters(self) -> list[ToolParameter]:
        return []

    @property
    @override
    def raw_input_schema(self) -> dict[str, Any]:
        return dict(_ANY_ARGUMENTS)

    @override
    def validate(self, kwargs: dict[str, Any]) -> None:
        return None

    @override
    async def execute(self, **kwargs: Any) -> ToolOutput:
        raise ToolFailure("auth", connect_text(self._server, "mcp"))


__all__ = ["UnconnectedMcpTool"]

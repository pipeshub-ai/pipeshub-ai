"""The tools a run gate serves itself (``harness@gate/…``), named apart from every catalog's."""

from __future__ import annotations

from typing import Final

GATE_TOOL_PREFIX: Final = "harness@gate/"


def is_gate_tool(tool_id: str | None) -> bool:
    return tool_id is not None and tool_id.startswith(GATE_TOOL_PREFIX)


__all__ = ["GATE_TOOL_PREFIX", "is_gate_tool"]

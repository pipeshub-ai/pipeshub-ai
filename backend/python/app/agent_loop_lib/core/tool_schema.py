"""Typed tool-calling schema, replacing the untyped `dict[str, Any]` that
used to flow from `Tool.to_schema()` through `ToolRegistry.schemas()` into
`Model.complete(tools=...)` — every producer and consumer of "a tool
schema" now shares one explicit shape instead of an implicit dict
convention (`{"name", "description", "input_schema"}`) that nothing
enforced.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

# "none" asks for a reply without tool calls while the tool list is still sent:
# providers reject tool-call history without the definitions it refers to.
ToolChoice = Literal["auto", "none"]


class ToolSchema(BaseModel):
    """Provider-agnostic function-calling schema for one tool. Transports
    translate this into their own wire shape (e.g. OpenAI's
    `{"type": "function", "function": {...}}` wrapper) — this type itself
    stays provider-neutral, matching the Anthropic-shaped fields the rest
    of the framework already standardized on."""

    name: str
    description: str
    input_schema: dict[str, Any]

    model_config = {"frozen": True}

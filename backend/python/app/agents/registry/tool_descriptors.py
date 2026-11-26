"""A registered toolset tool as a ``ToolDescriptor``: the one form both a run's version-2 pin and the catalog digest.

Built from the registry's serialized metadata (what ``ToolsetRegistry.get_toolset_metadata`` returns), so the pin
admission takes and the catalog item a release ships agree on every tool by construction.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from harness_core.mcp_hub.manifest import ToolDescriptor
from harness_core.tools.tags import EFFECT_TAG_KEY, SINK_TAG_KEY

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.engines.types import JsonValue


# The constraints a parameter dict carries beside its name, type, description, required and default, by the
# ``ToolParameter`` attribute each comes from.
PARAMETER_CONSTRAINTS: Mapping[str, str] = {
    "enum": "enum",
    "items": "items",
    "properties": "properties",
    "requiredProperties": "required_properties",
}


def _tags(tool: Mapping[str, object]) -> dict[str, str]:
    tags = cast("list[object]", tool.get("tags") or [])
    return {
        str(tag["key"]): str(tag["value"])
        for tag in (cast("dict[str, object]", t) for t in tags if isinstance(t, dict))
        if "key" in tag and "value" in tag
    }


def toolset_tool_descriptor(tool: Mapping[str, object]) -> ToolDescriptor:
    """One tool of a toolset's registry metadata: its name, description, declared parameters, effect and sink."""
    description = tool.get("description")
    tags = _tags(tool)
    parameters = cast("JsonValue", tool.get("parameters") or [])
    return ToolDescriptor(
        str(tool["name"]),
        description if isinstance(description, str) else None,
        {"parameters": parameters},
        effect=tags.get(EFFECT_TAG_KEY),
        sink=tags.get(SINK_TAG_KEY),
    )


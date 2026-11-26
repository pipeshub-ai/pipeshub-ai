"""Release catalog items for the registered toolsets (01-hld §5.1).

Each tool's descriptor is ``toolset_tool_descriptor``'s, the one a run pins at admission, so a toolset tool's
catalog digest and its version-2 pin digest are one value.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from app.agents.registry.tool_descriptors import toolset_tool_descriptor
from app.agents.registry.toolset_registry import ToolsetRegistry, get_toolset_registry
from app.control_plane.catalog.items import (
    CredentialRequirement,
    Display,
    ReleaseItem,
    ToolDescriptor,
    risk_class_of,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

_OAUTH = "oauth"


def _descriptor(tool: Mapping[str, object], provider: str | None) -> ToolDescriptor:
    declared = toolset_tool_descriptor(tool)
    return ToolDescriptor(
        name=declared.name,
        description=declared.description or "",
        input_schema=declared.input_schema,
        effect=declared.effect,
        sink=declared.sink,
        provider=provider,
    )


class ToolsetsCatalogSource:
    kind = "toolset"
    catalog_file = "toolsets.json"

    def __init__(self, registry: ToolsetRegistry) -> None:
        self._registry = registry

    def items(self) -> list[ReleaseItem]:
        return [self._item(name) for name in sorted(self._registry.get_all_toolsets())]

    def _item(self, name: str) -> ReleaseItem:
        metadata = self._registry.get_toolset_metadata(name, serialize=True) or {}
        tools = cast("list[dict[str, object]]", metadata.get("tools") or [])
        provider = metadata.get("provider")
        descriptors = tuple(_descriptor(tool, provider) for tool in tools)
        categories = dict.fromkeys(c for c in (metadata.get("category"), metadata.get("app_group")) if c)
        auth_types = cast("list[object]", metadata.get("supported_auth_types") or [])
        return ReleaseItem(
            kind=self.kind,
            name=name,
            display=Display(
                name=metadata.get("display_name") or name,
                description=metadata.get("description") or "",
                icon=metadata.get("icon_path"),
                categories=tuple(categories),
                hidden=bool(metadata.get("isInternal")),
            ),
            risk_class=risk_class_of(d.effect for d in descriptors),
            config_schema=metadata.get("config") or {},
            credential_requirements=tuple(
                CredentialRequirement(mode=mode, provider=name if mode == _OAUTH else None)
                for mode in (str(auth).lower() for auth in auth_types)
            ),
            descriptor_kind="tool",
            descriptors=descriptors,
        ).with_content_version()


def release_source() -> ToolsetsCatalogSource:
    registry = get_toolset_registry()
    if not registry.get_all_toolsets():
        registry.auto_discover_toolsets()
    return ToolsetsCatalogSource(registry)

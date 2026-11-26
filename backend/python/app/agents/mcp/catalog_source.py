"""Release catalog items for the MCP server templates (01-hld §5.1). Templates carry no tool
descriptors: tools are discovered per installation and approved there (02-lld §2.1)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from app.agents.mcp.models import MCPAuthMode
from app.agents.mcp.registry import get_mcp_registry
from app.control_plane.catalog.items import CredentialRequirement, Display, ReleaseItem

if TYPE_CHECKING:
    from app.agents.mcp.models import MCPServerTemplate

_DISPLAY_FIELDS = {"type_id", "display_name", "description", "icon", "tags"}


class TemplateCatalog(Protocol):
    def list_templates(self) -> list[MCPServerTemplate]: ...


class McpTemplatesCatalogSource:
    kind = "mcp"
    catalog_file = "mcp.json"

    def __init__(self, registry: TemplateCatalog) -> None:
        self._registry = registry

    def items(self) -> list[ReleaseItem]:
        return [self._item(template) for template in self._registry.list_templates()]

    def _item(self, template: MCPServerTemplate) -> ReleaseItem:
        modes = template.supported_auth_modes or [template.default_auth_mode]
        return ReleaseItem(
            kind=self.kind,
            name=template.type_id,
            display=Display(
                name=template.display_name,
                description=template.description,
                icon=template.icon,
                categories=tuple(template.tags),
            ),
            risk_class="write-external",
            config_schema=template.model_dump(mode="json", by_alias=True, exclude=_DISPLAY_FIELDS),
            credential_requirements=tuple(
                CredentialRequirement(
                    mode=mode.value,
                    provider=template.type_id if mode is MCPAuthMode.OAUTH else None,
                )
                for mode in modes
            ),
            descriptor_kind="tool",
        ).with_content_version()


def release_source() -> McpTemplatesCatalogSource:
    registry = get_mcp_registry()
    registry.auto_discover_templates()
    return McpTemplatesCatalogSource(registry)

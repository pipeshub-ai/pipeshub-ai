"""
Toolset Registry
Similar to ConnectorRegistry but for toolsets (agent-focused tools)
"""

import importlib
import inspect
import logging
from typing import Any, Dict, List, Optional, Type

logger = logging.getLogger(__name__)


class ToolsetRegistry:
    """Registry for managing toolset definitions and instances"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._toolsets: Dict[str, Dict[str, Any]] = {}
        self._initialized = True
        logger.info("ToolsetRegistry initialized")

    def register_toolset(self, toolset_class: Type) -> bool:
        """
        Register a toolset class and its tools in the in-memory registry.

        This extracts metadata from the toolset class (added by @Toolset decorator)
        and registers individual tools into the global tools registry.
        """
        try:
            # Get metadata from the toolset class (added by @Toolset decorator)
            metadata = getattr(toolset_class, '_toolset_metadata', {})
            if not metadata:
                logger.warning(f"Class {toolset_class.__name__} missing _toolset_metadata")
                return False

            toolset_name = metadata.get('name')
            if not toolset_name:
                logger.warning(f"Toolset class {toolset_class.__name__} missing name in metadata")
                return False

            # Normalize name
            normalized_name = self._normalize_toolset_name(toolset_name)

            # Extract tools list
            tools = metadata.get('tools', [])

            # Store toolset info
            self._toolsets[normalized_name] = {
                'class': toolset_class,
                'name': toolset_name,
                'normalized_name': normalized_name,
                'display_name': toolset_name,  # Use toolset name as display name (e.g., "Google Drive", "JIRA")
                'description': metadata.get('description', ''),
                'category': metadata.get('category', 'app'),
                'app_group': metadata.get('appGroup', ''),  # Group/category like "Storage", "Project Management"
                'group': metadata.get('appGroup', ''),  # Alias for consistency with API response
                'supported_auth_types': self._normalize_auth_types(metadata.get('supportedAuthTypes', ['API_TOKEN'])),
                'config': metadata.get('config', {}),
                'tools': tools,
                'icon_path': self._extract_icon_path(metadata),
                'isInternal': metadata.get('isInternal', False),  # Store internal flag (backend-only, not sent to frontend)
            }

            # Register individual tools into global tools registry
            from app.agents.tools.registry import _global_tools_registry
            for tool in tools:
                tool_name = tool.get('name')
                if tool_name and not _global_tools_registry.get_tool(normalized_name, tool_name):
                    # Tool will be registered via the @tool decorator on the actual class methods
                    # We just log here for visibility
                    logger.debug(f"Tool '{tool_name}' from toolset '{toolset_name}' available in registry")

            logger.info(f"Registered toolset: {toolset_name} ({normalized_name}) with {len(tools)} tools")
            return True

        except Exception as e:
            logger.error(f"Failed to register toolset {toolset_class.__name__}: {e}", exc_info=True)
            return False

    def _extract_icon_path(self, metadata: Dict[str, Any]) -> str:
        """Extract icon path from metadata or config"""
        # Try direct icon_path field
        icon = metadata.get('icon_path')
        if icon:
            return icon

        # Try config.iconPath
        config = metadata.get('config', {})
        icon = config.get('iconPath')
        if icon:
            return icon

        # Default
        return '/assets/icons/toolsets/default.svg'

    def _normalize_toolset_name(self, name: str) -> str:
        """Normalize toolset name (lowercase, no spaces/underscores)"""
        return name.lower().replace(' ', '').replace('_', '')

    def _normalize_auth_types(self, auth_types: Any) -> List[str]:
        """Normalize auth types to list"""
        if isinstance(auth_types, str):
            return [auth_types]
        return list(auth_types) if auth_types else ['API_TOKEN']

    def discover_toolsets(self, module_paths: List[str]) -> None:
        """Discover and register toolsets from module paths"""
        for module_path in module_paths:
            try:
                module = importlib.import_module(module_path)

                for name, obj in inspect.getmembers(module):
                    # Check for _toolset_metadata (added by @Toolset decorator)
                    if inspect.isclass(obj) and hasattr(obj, '_toolset_metadata'):
                        self.register_toolset(obj)

            except Exception as e:
                logger.error(f"Failed to discover toolsets in {module_path}: {e}")

    def auto_discover_toolsets(self) -> None:
        """Auto-discover toolsets from action files"""
        standard_paths = [
            # Internal toolsets (always available, no auth)
            'app.agents.actions.retrieval.retrieval',
            'app.agents.actions.calculator.calculator',
            # Google toolsets
            'app.agents.actions.google.drive.drive',
            'app.agents.actions.google.calendar.calendar',
            'app.agents.actions.google.gmail.gmail',
            'app.agents.actions.google.meet.meet',
            # 'app.agents.actions.google.slides.slides',
            # 'app.agents.actions.google.forms.forms',
            # 'app.agents.actions.google.docs.docs',
            # 'app.agents.actions.google.sheets.sheets',
            # Other toolsets
            'app.agents.actions.slack.slack',
            'app.agents.actions.jira.jira',
            'app.agents.actions.confluence.confluence',
            # 'app.agents.actions.github.github',
            # 'app.agents.actions.gitlab.gitlab',
            # 'app.agents.actions.linear.linear',
            # 'app.agents.actions.notion.notion',
            # 'app.agents.actions.microsoft.one_drive.one_drive',
            # 'app.agents.actions.microsoft.sharepoint.sharepoint',
            # 'app.agents.actions.microsoft.teams.teams',
            # 'app.agents.actions.microsoft.outlook.outlook',
            # 'app.agents.actions.airtable.airtable',
            # 'app.agents.actions.dropbox.dropbox',
            # 'app.agents.actions.box.box',
            # 'app.agents.actions.linkedin.linkedin',
            # 'app.agents.actions.posthog.posthog',
            # 'app.agents.actions.zendesk.zendesk',
            # 'app.agents.actions.discord.discord',
            # 'app.agents.actions.s3.s3',
            # 'app.agents.actions.azure.azure_blob',
            # 'app.agents.actions.evernote.evernote',
            # 'app.agents.actions.freshdesk.freshdesk',
            # 'app.agents.actions.bookstack.bookstack',
        ]
        self.discover_toolsets(standard_paths)
        logger.info(f"Auto-discovered {len(self._toolsets)} toolsets with in-memory registry")

    def get_toolset_metadata(self, toolset_name: str) -> Optional[Dict[str, Any]]:
        """Get metadata for a toolset"""
        normalized = self._normalize_toolset_name(toolset_name)
        return self._toolsets.get(normalized)

    def list_toolsets(self) -> List[str]:
        """List all registered toolset names"""
        return list(self._toolsets.keys())

    def get_all_toolsets(self) -> Dict[str, Dict[str, Any]]:
        """Get all registered toolsets"""
        return self._toolsets.copy()

    async def get_all_registered_toolsets(
        self,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        include_tools: bool = True
    ) -> Dict[str, Any]:
        """
        Get all registered toolsets with pagination and search.
        Includes tools for frontend drag-and-drop selection.
        """

        all_toolsets = []

        for normalized_name, metadata in self._toolsets.items():
            # Filter out internal toolsets (not sent to frontend)
            if metadata.get('isInternal', False):
                continue

            # Build tools list for frontend
            tools = []
            if include_tools:
                for tool_def in metadata.get('tools', []):
                    tools.append({
                        'name': tool_def['name'],
                        'fullName': f"{normalized_name}.{tool_def['name']}",
                        'description': tool_def.get('description', ''),
                        'parameters': tool_def.get('parameters', []),
                        'tags': tool_def.get('tags', [])
                    })

            toolset_info = {
                'name': metadata['name'],
                'normalized_name': normalized_name,
                'displayName': metadata['display_name'],
                'description': metadata['description'],
                'category': metadata['category'],
                'appGroup': metadata['app_group'],
                'supportedAuthTypes': metadata['supported_auth_types'],
                'iconPath': metadata['icon_path'],
                'toolCount': len(metadata.get('tools', [])),
                'tools': tools,  # Include tools for drag-and-drop
                'config': metadata.get('config', {}),
            }
            all_toolsets.append(toolset_info)

        # Apply search filter
        if search:
            search_lower = search.lower()
            all_toolsets = [
                t for t in all_toolsets
                if search_lower in t['displayName'].lower()
                or search_lower in t['description'].lower()
                or search_lower in t['appGroup'].lower()
            ]

        # Sort by display name
        all_toolsets.sort(key=lambda x: x['displayName'])

        # Pagination
        total = len(all_toolsets)
        total_pages = (total + limit - 1) // limit if limit > 0 else 1
        start_idx = (page - 1) * limit
        end_idx = start_idx + limit
        paginated_toolsets = all_toolsets[start_idx:end_idx]

        return {
            'toolsets': paginated_toolsets,
            'pagination': {
                'page': page,
                'limit': limit,
                'total': total,
                'totalPages': total_pages,
                'hasNext': page < total_pages,
                'hasPrev': page > 1,
            }
        }

    def get_toolset_config(self, toolset_name: str) -> Optional[Dict[str, Any]]:
        """Get configuration schema for a toolset"""
        metadata = self.get_toolset_metadata(toolset_name)
        if not metadata:
            return None

        return metadata.get('config', {})


def get_toolset_registry() -> ToolsetRegistry:
    """Get the global toolset registry instance"""
    return ToolsetRegistry()

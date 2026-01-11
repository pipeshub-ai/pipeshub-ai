"""
Tool Registry

Manages toolset metadata and database synchronization, similar to ConnectorRegistry.
A toolset is a collection of related tools (e.g., "jira toolset" with search, create, update tools).
"""

from inspect import isclass
from typing import Any, Callable, Dict, List, Optional, Type, Union

from app.connectors.core.registry.tool_builder import ToolCategory, ToolDefinition


def Toolset(
    name: str,
    app_group: str,
    supported_auth_types: Union[str, List[str]],  # Supported auth types (user selects one during creation)
    description: str = "",
    category: ToolCategory = ToolCategory.APP,
    config: Optional[Dict[str, Any]] = None,
    tools: Optional[List[ToolDefinition]] = None
) -> Callable[[Type], Type]:
    """
    Decorator to register a toolset with metadata and configuration schema.

    Args:
        name: Name of the toolset (e.g., "Jira", "Slack")
        app_group: Group the toolset belongs to (e.g., "Atlassian")
        auth_type: Authentication type(s) (e.g., "api_token", ["OAUTH", "API_TOKEN"])
        description: Description of the toolset
        category: Category of the toolset
        config: Complete configuration schema for the toolset
        tools: List of tool definitions
        auth_types: Explicit list of auth types (if not provided, derived from auth_type)

    Returns:
        Decorator function that marks a class as a toolset

    Example:
        @Toolset(
            name="Jira",
            app_group="Atlassian",
            auth_type="API_TOKEN",
            description="Jira issue management tools",
            category=ToolCategory.APP,
            tools=[...]
        )
        class JiraToolset:
            pass
    """
    def decorator(cls: Type) -> Type:
        # Normalize supported auth types
        if isinstance(supported_auth_types, str):
            supported_auth_types_list = [supported_auth_types]
        elif isinstance(supported_auth_types, list):
            if not supported_auth_types:
                raise ValueError("supported_auth_types list cannot be empty")
            supported_auth_types_list = supported_auth_types
        else:
            raise ValueError(f"supported_auth_types must be str or List[str], got {type(supported_auth_types)}")

        # Convert tools to dict format
        tools_dict = []
        if tools:
            for tool in tools:
                tools_dict.append({
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                    "returns": tool.returns,
                    "examples": tool.examples,
                    "tags": tool.tags
                })

        # Store metadata in the class (no authType - it comes from etcd/database when toolset is created)
        cls._toolset_metadata = {
            "name": name,
            "appGroup": app_group,
            "supportedAuthTypes": supported_auth_types_list,  # Supported types (user selects one during creation)
            "description": description,
            "category": category.value,
            "config": config or {},
            "tools": tools_dict
        }

        # Mark class as a toolset
        cls._is_toolset = True

        return cls
    return decorator


class ToolsetRegistry:
    """
    Registry for managing toolset metadata and database synchronization.

    This class handles:
    - Registration of toolset classes from code
    - Providing toolset information
    - Creating and updating toolset instances
    - Pagination for large toolset lists
    """

    def __init__(self) -> None:
        """Initialize the toolset registry"""
        self._toolsets: Dict[str, Dict[str, Any]] = {}

    def register_toolset(self, toolset_class: Type) -> bool:
        """
        Register a toolset class with the registry.

        Args:
            toolset_class: The toolset class to register (must be decorated with @Toolset)

        Returns:
            True if registered successfully, False otherwise
        """
        try:
            if not hasattr(toolset_class, '_toolset_metadata'):
                return False

            metadata = toolset_class._toolset_metadata
            toolset_name = metadata['name']

            # Store in memory
            self._toolsets[toolset_name] = metadata.copy()

            return True

        except Exception:
            return False

    def discover_toolsets(self, module_paths: List[str]) -> None:
        """
        Discover and register all toolset classes from specified modules.

        Args:
            module_paths: List of module names to search for toolsets
        """
        try:
            for module_path in module_paths:
                try:
                    module = __import__(module_path, fromlist=['*'])

                    for attribute_name in dir(module):
                        attribute = getattr(module, attribute_name)

                        if (isclass(attribute) and
                            hasattr(attribute, '_toolset_metadata') and
                            hasattr(attribute, '_is_toolset')):

                            self.register_toolset(attribute)

                except ImportError:
                    continue

        except Exception:
            pass

    def get_toolset_metadata(self, toolset_name: str) -> Optional[Dict[str, Any]]:
        """
        Get toolset metadata by name from the registry.

        Args:
            toolset_name: Name of the toolset

        Returns:
            Toolset metadata or None if not found
        """
        if toolset_name not in self._toolsets:
            return None

        metadata = self._toolsets[toolset_name]
        return self._build_toolset_info(toolset_name, metadata)

    def get_all_toolsets(
        self,
        category: Optional[ToolCategory] = None,
        auth_type: Optional[str] = None,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get all registered toolsets from the registry.

        Args:
            category: Optional category filter
            auth_type: Optional auth type filter
            page: Page number (1-indexed)
            limit: Number of items per page
            search: Optional search query

        Returns:
            Dictionary with toolsets list and pagination info
        """
        toolsets = []

        # Prepare search tokens
        tokens: List[str] = []
        if search:
            tokens = [t.strip().lower() for t in str(search).split() if t.strip()]

        def matches_search(info: Dict[str, Any]) -> bool:
            if not tokens:
                return True
            haystacks: List[str] = []
            haystacks.append(str(info.get('name', '')).lower())
            haystacks.append(str(info.get('appGroup', '')).lower())
            haystacks.append(str(info.get('description', '')).lower())
            combined = ' '.join(haystacks)
            return all(tok in combined for tok in tokens)

        for toolset_name, metadata in self._toolsets.items():
            # Filter by category
            if category and metadata.get('category') != category.value:
                continue

            # Filter by auth type
            if auth_type:
                supported_auth_types = metadata.get('supportedAuthTypes', [])
                if auth_type.upper() not in [at.upper() for at in supported_auth_types]:
                    continue

            toolset_info = self._build_toolset_info(toolset_name, metadata)
            if matches_search(toolset_info):
                toolsets.append(toolset_info)

        # Calculate pagination
        total_count = len(toolsets)
        total_pages = (total_count + limit - 1) // limit
        start_idx = (page - 1) * limit
        end_idx = start_idx + limit
        has_prev = page > 1
        has_next = end_idx < total_count

        paginated_toolsets = toolsets[start_idx:end_idx]

        return {
            "toolsets": paginated_toolsets,
            "pagination": {
                "page": page,
                "limit": limit,
                "search": search,
                "totalCount": total_count,
                "totalPages": total_pages,
                "hasPrev": has_prev,
                "hasNext": has_next,
                "prevPage": page - 1,
                "nextPage": page + 1
            }
        }

    def _build_toolset_info(
        self,
        toolset_name: str,
        metadata: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Build toolset information dictionary from metadata.

        Args:
            toolset_name: Name of the toolset
            metadata: Toolset metadata from registry

        Returns:
            Complete toolset information dictionary
        """
        toolset_config = metadata.get('config', {})

        toolset_info = {
            'name': toolset_name,
            'appGroup': metadata['appGroup'],
            'supportedAuthTypes': metadata.get('supportedAuthTypes', []),  # Supported types (user selects one)
            'description': metadata.get('description', ''),
            'category': metadata.get('category', ToolCategory.APP.value),
            'iconPath': toolset_config.get(
                'iconPath',
                '/assets/icons/toolsets/default.svg'
            ),
            'config': toolset_config,
            'tools': metadata.get('tools', [])
        }

        return toolset_info

    def get_toolsets_by_category(self, category: ToolCategory) -> List[Dict[str, Any]]:
        """Get all toolsets in a specific category"""
        toolsets = []
        for toolset_name, metadata in self._toolsets.items():
            if metadata.get('category') == category.value:
                toolsets.append(self._build_toolset_info(toolset_name, metadata))
        return toolsets

    def get_toolsets_by_auth_type(self, auth_type: str) -> List[Dict[str, Any]]:
        """Get all toolsets that support a specific auth type"""
        toolsets = []
        for toolset_name, metadata in self._toolsets.items():
            supported_auth_types = metadata.get('supportedAuthTypes', [])
            if auth_type.upper() in [at.upper() for at in supported_auth_types]:
                toolsets.append(self._build_toolset_info(toolset_name, metadata))
        return toolsets

    def list_toolsets(self) -> List[str]:
        """List all registered toolset names"""
        return list(self._toolsets.keys())


# Global toolset registry instance
_global_toolset_registry = ToolsetRegistry()


def get_toolset_registry() -> ToolsetRegistry:
    """Get the global toolset registry instance"""
    return _global_toolset_registry


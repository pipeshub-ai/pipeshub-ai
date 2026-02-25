"""
Toolset Registry
Similar to ConnectorRegistry but for toolsets (agent-focused tools)
"""

import importlib
import inspect
import logging
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Type, Union

if TYPE_CHECKING:
    pass

from app.connectors.core.registry.tool_builder import ToolDefinition, ToolsetCategory

logger = logging.getLogger(__name__)


def Toolset(
    name: str,
    app_group: str,
    supported_auth_types: Union[str, List[str]],  # Supported auth types (user selects one during creation)
    description: str = "",
    category: ToolsetCategory = ToolsetCategory.APP,
    config: Optional[Dict[str, Any]] = None,
    tools: Optional[List[ToolDefinition]] = None,
    internal: bool = False  # If True, toolset is internal and not sent to frontend
) -> Callable[[Type], Type]:
    """
    Decorator to register a toolset with metadata and configuration schema.

    Args:
        name: Name of the toolset (e.g., "Jira", "Slack")
        app_group: Group the toolset belongs to (e.g., "Atlassian")
        supported_auth_types: Authentication type(s) (e.g., "api_token", ["OAUTH", "API_TOKEN"])
        description: Description of the toolset
        category: Category of the toolset
        config: Complete configuration schema for the toolset
        tools: List of tool definitions

    Returns:
        Decorator function that marks a class as a toolset

    Example:
        @Toolset(
            name="Jira",
            app_group="Atlassian",
            supported_auth_types=["OAUTH", "API_TOKEN"],
            description="Jira issue management tools",
            category=ToolsetCategory.APP,
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
        # Safely extract category value
        category_value = category
        if hasattr(category, 'value'):
            category_value = category.value
        elif isinstance(category, str):
            category_value = category
        else:
            category_value = str(category)

        cls._toolset_metadata = {
            "name": name,
            "appGroup": app_group,
            "supportedAuthTypes": supported_auth_types_list,  # Supported types (user selects one during creation)
            "description": description,
            "category": category_value,
            "config": config or {},
            "tools": tools_dict,
            "toolsetClass": cls,  # Store reference to the class for tool discovery
            "isInternal": internal  # Mark as internal if True (not sent to frontend)
        }

        # Mark class as a toolset
        cls._is_toolset = True

        return cls
    return decorator


class ToolsetRegistry:
    """Registry for managing toolset definitions and instances"""

    _instance = None

    def __new__(cls) -> 'ToolsetRegistry':
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
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

            # Discover tools from @tool decorators (single source of truth)
            # This replaces the old tools list that was passed to @Toolset decorator
            discovered_tools = self._discover_tools_from_class(toolset_class)

            # Convert discovered tools to dict format for frontend API
            tools = []
            for tool_name, func in discovered_tools.items():
                tool_metadata = func._tool_metadata

                # Convert to dict format (same as old tools list format)
                tool_dict = {
                    'name': tool_metadata.tool_name,
                    'description': tool_metadata.description,
                    'parameters': self._convert_parameters_to_dict(tool_metadata),
                    'returns': tool_metadata.returns,
                    'examples': tool_metadata.examples,
                    'tags': tool_metadata.tags
                }
                tools.append(tool_dict)

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

            # Tools are already registered in global registry by @tool decorator
            # We just log here for visibility
            from app.agents.tools.registry import _global_tools_registry
            for tool_dict in tools:
                tool_name = tool_dict.get('name')
                if tool_name:
                    full_tool_name = f"{normalized_name}.{tool_name}"
                    # Check if tool exists in global registry (registered by @tool decorator)
                    try:
                        registered_tool = _global_tools_registry.get_tool_by_full_name(full_tool_name)
                        if registered_tool:
                            logger.debug(f"Tool '{tool_name}' from toolset '{toolset_name}' registered in global registry")
                    except Exception:
                        pass

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

    def _normalize_auth_types(self, auth_types: Union[str, List[str], None]) -> List[str]:
        """Normalize auth types to list"""
        if isinstance(auth_types, str):
            return [auth_types]
        return list(auth_types) if auth_types else ['API_TOKEN']

    def _discover_tools_from_class(self, toolset_class: Type) -> Dict[str, Any]:
        """
        Discover tools from @tool decorated methods in a class.

        Args:
            toolset_class: The toolset class

        Returns:
            Dictionary mapping tool_name -> function
        """
        tools = {}

        # Use inspect.getmembers to properly discover methods (including instance methods)
        # This is more reliable than dir() for decorated methods
        for attr_name, attr in inspect.getmembers(toolset_class, predicate=inspect.isfunction):
            try:
                # Check if the attribute has _tool_metadata (set by @tool decorator)
                if hasattr(attr, '_tool_metadata'):
                    tool_metadata = attr._tool_metadata
                    tools[tool_metadata.tool_name] = attr
            except Exception as e:
                import traceback
                logger.error(f"Failed to discover tools from class {toolset_class.__name__}: {e}", exc_info=True)
                logger.debug(f"Full traceback for {toolset_class.__name__}:\n{traceback.format_exc()}")
                continue

        # Also check class __dict__ directly for methods that might not be found by getmembers
        # This handles cases where methods are defined but not yet bound
        for attr_name in toolset_class.__dict__:
            try:
                attr = toolset_class.__dict__[attr_name]
                # Check if it's a function or method descriptor
                if (inspect.isfunction(attr) or inspect.ismethod(attr) or
                    (callable(attr) and hasattr(attr, '__func__'))):
                    # Get the actual function if it's a method descriptor
                    actual_func = getattr(attr, '__func__', attr)
                    if hasattr(actual_func, '_tool_metadata'):
                        tool_metadata = actual_func._tool_metadata
                        tools[tool_metadata.tool_name] = actual_func
            except Exception as e:
                import traceback
                logger.error(f"Failed to discover tools from class {toolset_class.__name__}: {e}", exc_info=True)
                logger.debug(f"Full traceback for {toolset_class.__name__}:\n{traceback.format_exc()}")
                continue

        return tools

    def _convert_parameters_to_dict(self, tool_metadata) -> List[Dict[str, Any]]:
        """
        Convert tool parameters to dict format for frontend API.

        Handles both Pydantic schemas (args_schema) and legacy ToolParameter lists.
        """
        parameters = []

        # Check if tool has Pydantic schema (preferred)
        if hasattr(tool_metadata, 'args_schema') and tool_metadata.args_schema:
            schema = tool_metadata.args_schema

            # Convert Pydantic schema fields to parameter dict format
            for field_name, field_info in schema.model_fields.items():
                param_type = self._map_pydantic_type_to_parameter_type(field_info.annotation)
                required = field_info.is_required
                description = field_info.description or f"Parameter {field_name}"
                default = field_info.default if not required else None

                param_dict = {
                    "name": field_name,
                    "type": param_type,
                    "description": description,
                    "required": required,
                }
                if default is not None:
                    param_dict["default"] = default
                parameters.append(param_dict)
        elif hasattr(tool_metadata, 'parameters') and tool_metadata.parameters:
            # Fallback to legacy ToolParameter list
            for param in tool_metadata.parameters:
                param_dict = {
                    "name": getattr(param, 'name', 'unknown'),
                    "type": getattr(getattr(param, 'type', None), 'value', 'string') if hasattr(param, 'type') else 'string',
                    "description": getattr(param, 'description', ''),
                    "required": getattr(param, 'required', False),
                }
                if hasattr(param, 'default') and param.default is not None:
                    param_dict["default"] = param.default
                parameters.append(param_dict)

        return parameters

    def _map_pydantic_type_to_parameter_type(self, type_hint) -> str:
        """Map Pydantic/Python type hint to parameter type string"""
        import typing
        from typing import get_args, get_origin

        # Handle Optional types
        origin = get_origin(type_hint)
        if origin is typing.Union:
            args = get_args(type_hint)
            # Optional[X] is Union[X, None]
            non_none_args = [arg for arg in args if arg is not type(None)]
            if non_none_args:
                type_hint = non_none_args[0]
                origin = get_origin(type_hint)

        # Map basic types
        if type_hint is str:
            return 'string'
        elif type_hint is int:
            return 'integer'
        elif type_hint is float:
            return 'number'
        elif type_hint is bool:
            return 'boolean'
        elif origin is list:
            return 'array'
        elif origin is dict:
            return 'object'

        # Default to string
        return 'string'

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
                import traceback
                logger.error(f"Failed to discover toolsets in {module_path}: {e}", exc_info=True)
                logger.debug(f"Full traceback for {module_path}:\n{traceback.format_exc()}")

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
            # 'app.agents.actions.google.meet.meet',
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
            'app.agents.actions.microsoft.outlook.outlook',
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

    def get_toolset_metadata(self, toolset_name: str, serialize: bool = True) -> Optional[Dict[str, Any]]:
        """
        Get metadata for a toolset.

        Args:
            toolset_name: Name of the toolset
            serialize: If True, returns serializable data (dicts). If False, returns original dataclass instances.

        Returns:
            Toolset metadata dict. If serialize=False, OAuth configs remain as dataclass instances.
        """
        normalized = self._normalize_toolset_name(toolset_name)
        metadata = self._toolsets.get(normalized)
        if not metadata:
            return None

        if not serialize:
            # Return original metadata with dataclass instances intact
            # Remove non-serializable fields but keep OAuth configs as dataclass instances
            return {
                'name': metadata.get('name'),
                'normalized_name': metadata.get('normalized_name'),
                'display_name': metadata.get('display_name'),
                'description': metadata.get('description'),
                'category': metadata.get('category'),
                'app_group': metadata.get('app_group'),
                'group': metadata.get('group'),
                'supported_auth_types': metadata.get('supported_auth_types'),
                'config': metadata.get('config', {}),  # Keep original config with dataclass instances
                'tools': metadata.get('tools', []),
                'icon_path': metadata.get('icon_path'),
                'isInternal': metadata.get('isInternal', False),
            }

        # Serialize for API responses
        # Sanitize config to remove any non-serializable objects (like OAuth config objects with methods)
        config = metadata.get('config', {})
        sanitized_config = self._sanitize_config(config)

        # Sanitize tools to ensure all fields are serializable
        tools = metadata.get('tools', [])
        sanitized_tools = [self._sanitize_tool_dict(tool) for tool in tools]

        # Return a copy without non-serializable fields (like 'class')
        # This ensures FastAPI can serialize the response
        serializable_metadata = {
            'name': metadata.get('name'),
            'normalized_name': metadata.get('normalized_name'),
            'display_name': metadata.get('display_name'),
            'description': metadata.get('description'),
            'category': metadata.get('category'),
            'app_group': metadata.get('app_group'),
            'group': metadata.get('group'),
            'supported_auth_types': metadata.get('supported_auth_types'),
            'config': sanitized_config,
            'tools': sanitized_tools,
            'icon_path': metadata.get('icon_path'),
            'isInternal': metadata.get('isInternal', False),
        }
        return serializable_metadata

    def _sanitize_config(self, config: Union[Dict[str, Any], object]) -> Dict[str, Any]:
        """Sanitize config dict to remove non-serializable objects"""
        if not isinstance(config, dict):
            return {}

        sanitized = {}
        for key, value in config.items():
            # Preserve _oauth_configs even though it starts with _ (needed for OAuth functionality)
            if key == '_oauth_configs':
                # Convert OAuth config dataclass instances to dicts for serialization
                if isinstance(value, dict):
                    sanitized[key] = self._sanitize_oauth_configs(value)
                else:
                    sanitized[key] = value
                continue

            # Skip other internal keys that shouldn't be exposed
            if key.startswith('_'):
                continue

            # Skip non-serializable objects (methods, classes, functions, dataclass instances)
            if callable(value) and not isinstance(value, type):
                continue
            if isinstance(value, type):
                continue

            # Skip dataclass instances (like OAuthConfig) - they should be converted to dict elsewhere
            from dataclasses import is_dataclass
            if is_dataclass(value) and not isinstance(value, type):
                continue

            # Recursively sanitize nested dicts
            if isinstance(value, dict):
                sanitized[key] = self._sanitize_config(value)
            elif isinstance(value, list):
                sanitized[key] = [
                    self._sanitize_config(item) if isinstance(item, dict)
                    else (item if not (callable(item) and not isinstance(item, type)) and
                          not (is_dataclass(item) and not isinstance(item, type)) else None)
                    for item in value
                ]
                # Remove None values that were filtered out
                sanitized[key] = [item for item in sanitized[key] if item is not None]
            else:
                sanitized[key] = value

        return sanitized

    def _sanitize_oauth_configs(self, oauth_configs: Dict[str, Any]) -> Dict[str, Any]:
        """Sanitize OAuth configs by converting dataclass instances to dicts"""
        from dataclasses import asdict, is_dataclass

        sanitized = {}
        for auth_type, oauth_config in oauth_configs.items():
            if is_dataclass(oauth_config) and not isinstance(oauth_config, type):
                # Convert dataclass to dict
                try:
                    sanitized[auth_type] = asdict(oauth_config)
                except Exception:
                    # Don't log exception details as they may contain sensitive OAuth config data
                    # Don't include auth_type in log message to avoid potential sensitive data exposure
                    logger.warning("Failed to convert OAuth config dataclass to dict")
                    # Fallback: try to get attributes manually
                    sanitized[auth_type] = {
                        attr: getattr(oauth_config, attr, None)
                        for attr in dir(oauth_config)
                        if not attr.startswith('_') and not callable(getattr(oauth_config, attr, None))
                    }
            elif isinstance(oauth_config, dict):
                sanitized[auth_type] = self._sanitize_config(oauth_config)
            else:
                sanitized[auth_type] = oauth_config

        return sanitized

    def _sanitize_tool_dict(self, tool: Dict[str, Any]) -> Dict[str, Any]:
        """Sanitize tool dict to ensure all fields are serializable"""
        if not isinstance(tool, dict):
            return {}

        sanitized = {}
        for key, value in tool.items():
            # Skip non-serializable objects
            if callable(value) and not isinstance(value, type):
                continue
            if isinstance(value, type):
                continue

            # Recursively sanitize nested structures
            if isinstance(value, dict):
                sanitized[key] = self._sanitize_config(value)
            elif isinstance(value, list):
                sanitized[key] = [self._sanitize_config(item) if isinstance(item, dict) else item
                                 for item in value if not (callable(item) and not isinstance(item, type))]
            else:
                sanitized[key] = value

        return sanitized

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
        Returns serialized data (OAuth configs as dicts).
        """

        all_toolsets = []

        for normalized_name, metadata in self._toolsets.items():
            # Filter out internal toolsets (not sent to frontend)
            if metadata.get('isInternal', False):
                continue

            # Get serialized metadata (OAuth configs converted to dicts)
            serialized_metadata = self.get_toolset_metadata(metadata['name'], serialize=True)
            if not serialized_metadata:
                continue

            # Build tools list for frontend (ensure all data is serializable)
            tools = []
            if include_tools:
                for tool_def in serialized_metadata.get('tools', []):
                    # Ensure tool_def is a dict and all values are serializable
                    if isinstance(tool_def, dict):
                        tools.append({
                            'name': tool_def.get('name', ''),
                            'fullName': f"{normalized_name}.{tool_def.get('name', '')}",
                            'description': tool_def.get('description', ''),
                            'parameters': tool_def.get('parameters', []),
                            'tags': tool_def.get('tags', [])
                        })

            toolset_info = {
                'name': serialized_metadata['name'],
                'normalized_name': normalized_name,
                'displayName': serialized_metadata['display_name'],
                'description': serialized_metadata['description'],
                'category': serialized_metadata['category'],
                'appGroup': serialized_metadata['app_group'],
                'supportedAuthTypes': serialized_metadata['supported_auth_types'],
                'iconPath': serialized_metadata['icon_path'],
                'toolCount': len(serialized_metadata.get('tools', [])),
                'tools': tools,  # Include tools for drag-and-drop
                'config': serialized_metadata.get('config', {}),  # Serialized config (OAuth as dicts)
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

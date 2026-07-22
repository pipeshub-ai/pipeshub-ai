"""
Toolset Registry
Similar to ConnectorRegistry but for toolsets (agent-focused tools)
"""

import importlib
import inspect
import logging
from collections.abc import Callable
from typing import Any

from app.connectors.core.registry.tool_builder import ToolDefinition, ToolsetCategory

AGENT_LOOP_TOOL_META_ATTR = "_agent_tool_meta"

logger = logging.getLogger(__name__)


def Toolset(
    name: str,
    app_group: str,
    supported_auth_types: str | list[str],  # Supported auth types (user selects one during creation)
    description: str = "",
    category: ToolsetCategory = ToolsetCategory.APP,
    config: dict[str, Any] | None = None,
    tools: list[ToolDefinition] | None = None,
    internal: bool = False,  # If True, toolset is internal and not sent to frontend
    essential: bool = False,  # If True, stays visible under lazy tool disclosure (see ToolsetBuilder.as_essential)
) -> Callable[[type], type]:
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
        essential: If True, `PipesHubAgentFactory` pins this toolset's tools
            back into `AgentSpec.pinned_toolsets` under lazy tool disclosure
            (see `factory.py`) — declarative single source of truth for
            "essential" vs "searchable" classification, replacing a
            hardcoded pin list.

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
    def decorator(cls: type) -> type:
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
            tools_dict.extend({
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
                "returns": tool.returns,
                "examples": tool.examples,
                "tags": tool.tags
            } for tool in tools)

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
            "supportedAuthTypes": supported_auth_types_list,
            "description": description,
            "category": category_value,
            "config": config or {},
            "tools": tools_dict,
            "toolsetClass": cls,
            "isInternal": internal,
            "essential": essential,
        }

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

        self._toolsets: dict[str, dict[str, Any]] = {}
        self._initialized = True
        logger.info("ToolsetRegistry initialized")

    def register_toolset(self, toolset_class: type) -> bool:
        """
        Register a toolset class and its tools in the in-memory registry.

        Extracts metadata from the toolset class (added by @Toolset decorator)
        and discovers tools from decorated methods.
        """
        try:
            metadata = getattr(toolset_class, '_toolset_metadata', {})
            if not metadata:
                logger.warning(f"Class {toolset_class.__name__} missing _toolset_metadata")
                return False

            toolset_name = metadata.get('name')
            if not toolset_name:
                logger.warning(f"Toolset class {toolset_class.__name__} missing name in metadata")
                return False

            normalized_name = self._normalize_toolset_name(toolset_name)
            discovered_tools = self._discover_tools_from_class(toolset_class)

            category_value = category = metadata.get('category', 'app')
            if hasattr(category, 'value'):
                category_value = category.value
            elif not isinstance(category, str):
                category_value = str(category)

            self._toolsets[normalized_name] = {
                'class': toolset_class,
                'name': toolset_name,
                'normalized_name': normalized_name,
                'display_name': toolset_name,
                'description': metadata.get('description', ''),
                'category': category_value,
                'app_group': metadata.get('appGroup', ''),
                'group': metadata.get('appGroup', ''),
                'supported_auth_types': self._normalize_auth_types(metadata.get('supportedAuthTypes', ['API_TOKEN'])),
                'config': metadata.get('config', {}),
                'tools': discovered_tools,
                'icon_path': self._extract_icon_path(metadata),
                'isInternal': metadata.get('isInternal', False),
                'essential': metadata.get('essential', False),
            }

            logger.info(f"Registered toolset: {toolset_name} ({normalized_name}) with {len(discovered_tools)} tools")
            return True

        except Exception as e:
            logger.error(f"Failed to register toolset {toolset_class.__name__}: {e}", exc_info=True)
            return False

    def _extract_icon_path(self, metadata: dict[str, Any]) -> str:
        """Extract icon path from metadata or config"""
        icon = metadata.get('icon_path')
        if icon:
            return icon

        config = metadata.get('config', {})
        icon = config.get('iconPath')
        if icon:
            return icon

        return '/icons/toolsets/default.svg'

    def _normalize_toolset_name(self, name: str) -> str:
        """Normalize toolset name (lowercase, no spaces/underscores)"""
        return name.lower().replace(' ', '').replace('_', '')

    def _normalize_auth_types(self, auth_types: str | list[str] | None) -> list[str]:
        """Normalize auth types to list"""
        if isinstance(auth_types, str):
            return [auth_types]
        return list(auth_types) if auth_types else ['API_TOKEN']

    def _discover_tools_from_class(self, toolset_class: type) -> list[dict[str, Any]]:
        """Discover tools from @tool decorated methods in a class.

        Supports both the legacy ``_tool_metadata`` and the new
        ``_agent_tool_meta`` from agent_loop_lib.
        Returns a list of unified dicts for frontend API consumption.
        """
        tools: list[dict[str, Any]] = []
        seen: set[str] = set()

        for attr_name in list(vars(toolset_class)):
            try:
                attr = vars(toolset_class).get(attr_name)
                if not (inspect.isfunction(attr) or inspect.ismethod(attr) or
                        (callable(attr) and hasattr(attr, '__func__'))):
                    continue
                actual_func = getattr(attr, '__func__', attr)

                if hasattr(actual_func, '_tool_metadata'):
                    meta = actual_func._tool_metadata
                    tool_name = meta.tool_name
                    if tool_name in seen:
                        continue
                    seen.add(tool_name)
                    tools.append({
                        'name': tool_name,
                        'description': meta.description,
                        'parameters': self._convert_legacy_parameters(meta),
                        'returns': getattr(meta, 'returns', None),
                        'examples': getattr(meta, 'examples', []),
                        'tags': getattr(meta, 'tags', []),
                    })
                elif hasattr(actual_func, AGENT_LOOP_TOOL_META_ATTR):
                    agent_meta = getattr(actual_func, AGENT_LOOP_TOOL_META_ATTR)
                    tool_name = agent_meta.path.rsplit("/", 1)[-1]
                    if tool_name in seen:
                        continue
                    seen.add(tool_name)
                    tools.append({
                        'name': tool_name,
                        'description': agent_meta.short_description,
                        'parameters': self._convert_agent_loop_parameters(agent_meta),
                        'returns': None,
                        'examples': [],
                        'tags': [{'key': t.key, 'value': t.value} for t in getattr(agent_meta, 'tags', [])],
                    })
            except Exception as e:
                logger.error("Failed to discover tool from %s.%s: %s", toolset_class.__name__, attr_name, e, exc_info=True)
                continue

        return tools

    def _convert_legacy_parameters(self, tool_metadata: Any) -> list[dict[str, Any]]:
        """Convert legacy _tool_metadata parameters to dict format."""
        return self._convert_parameters_to_dict(tool_metadata)

    def _convert_agent_loop_parameters(self, agent_meta: Any) -> list[dict[str, Any]]:
        """Convert agent_loop_lib ToolMeta parameters to dict format."""
        parameters = []
        for param in getattr(agent_meta, 'parameters', []):
            param_dict: dict[str, Any] = {
                "name": param.name,
                "type": param.type.value if hasattr(param.type, 'value') else 'string',
                "description": getattr(param, 'description', ''),
                "required": getattr(param, 'required', False),
            }
            if getattr(param, 'default', None) is not None:
                param_dict["default"] = param.default
            parameters.append(param_dict)
        return parameters

    def _convert_parameters_to_dict(self, tool_metadata: Any) -> list[dict[str, Any]]:
        """
        Convert tool parameters to dict format for frontend API.

        Handles Pydantic schemas (args_schema) and legacy ToolParameter lists.
        """
        parameters: list[dict[str, Any]] = []

        if hasattr(tool_metadata, 'args_schema') and tool_metadata.args_schema:
            schema = tool_metadata.args_schema
            for field_name, field_info in schema.model_fields.items():
                param_type = self._map_pydantic_type_to_parameter_type(field_info.annotation)
                required = field_info.is_required
                description = field_info.description or f"Parameter {field_name}"
                default = field_info.default if not required else None

                param_dict: dict[str, Any] = {
                    "name": field_name,
                    "type": param_type,
                    "description": description,
                    "required": required,
                }
                if default is not None:
                    param_dict["default"] = default
                parameters.append(param_dict)
        elif hasattr(tool_metadata, 'parameters') and tool_metadata.parameters:
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

    def _map_pydantic_type_to_parameter_type(self, type_hint: type) -> str:
        """Map Pydantic/Python type hint to parameter type string"""
        import typing
        from typing import get_args, get_origin

        origin = get_origin(type_hint)
        if origin is typing.Union:
            args = get_args(type_hint)
            non_none_args = [arg for arg in args if arg is not type(None)]
            if non_none_args:
                type_hint = non_none_args[0]
                origin = get_origin(type_hint)

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

        return 'string'

    def discover_toolsets(self, module_paths: list[str]) -> None:
        """Discover and register toolsets from module paths"""
        for module_path in module_paths:
            try:
                module = importlib.import_module(module_path)

                for _name, obj in inspect.getmembers(module):
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
            'app.agents.actions.calculator.date_calculator',
            'app.agents.actions.knowledge_hub.knowledge_hub',
            'app.agents.actions.coding_sandbox.coding_sandbox',
            'app.agents.actions.database_sandbox.database_sandbox',
            'app.agents.actions.image_generator.image_generator',
            'app.agents.actions.artifacts.artifacts',
            # Google toolsets
            'app.agents.actions.google.drive.drive',
            'app.agents.actions.google.calendar.calendar',
            'app.agents.actions.google.gmail.gmail',
            'app.agents.actions.internal_tools.intrim_tools',

            # 'app.agents.actions.google.meet.meet',
            # 'app.agents.actions.google.slides.slides',
            # 'app.agents.actions.google.forms.forms',
            # 'app.agents.actions.google.docs.docs',
            # 'app.agents.actions.google.sheets.sheets',
            # Other toolsets
            'app.agents.actions.slack.slack',
            'app.agents.actions.jira.jira',
            'app.agents.actions.jira_data_center.jira_data_center',
            'app.agents.actions.confluence.confluence',
            'app.agents.actions.confluence_data_center.confluence_data_center',
            'app.agents.actions.clickup.clickup',
            'app.agents.actions.github.github',
            'app.agents.actions.mariadb.mariadb',
            'app.agents.actions.lumos.lumos',
            'app.agents.actions.redshift.redshift',
            # 'app.agents.actions.gitlab.gitlab',
            # 'app.agents.actions.linear.linear',
            # 'app.agents.actions.notion.notion',
            'app.agents.actions.microsoft.one_drive.one_drive',
            'app.agents.actions.microsoft.sharepoint.sharepoint',
            'app.agents.actions.microsoft.teams.teams',
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
            'app.agents.actions.zoom.zoom',
            'app.agents.actions.salesforce.salesforce',
        ]
        self.discover_toolsets(standard_paths)
        logger.info(f"Auto-discovered {len(self._toolsets)} toolsets with in-memory registry")

    def get_toolset_metadata(self, toolset_name: str, serialize: bool = True) -> dict[str, Any] | None:
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
            return {
                'name': metadata.get('name'),
                'normalized_name': metadata.get('normalized_name'),
                'display_name': metadata.get('display_name'),
                'description': metadata.get('description'),
                'category': metadata.get('category'),
                'app_group': metadata.get('app_group'),
                'group': metadata.get('group'),
                'supported_auth_types': metadata.get('supported_auth_types'),
                'config': metadata.get('config', {}),
                'tools': metadata.get('tools', []),
                'icon_path': metadata.get('icon_path'),
                'isInternal': metadata.get('isInternal', False),
                'essential': metadata.get('essential', False),
            }

        config = metadata.get('config', {})
        sanitized_config = self._sanitize_config(config)

        tools = metadata.get('tools', [])
        sanitized_tools = [self._sanitize_tool_dict(tool) for tool in tools]

        return {
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
            'essential': metadata.get('essential', False),
        }

    def _sanitize_config(self, config: dict[str, Any] | object) -> dict[str, Any]:
        """Sanitize config dict to remove non-serializable objects"""
        if not isinstance(config, dict):
            return {}

        sanitized = {}
        for key, value in config.items():
            if key == '_oauth_configs':
                if isinstance(value, dict):
                    sanitized[key] = self._sanitize_oauth_configs(value)
                else:
                    sanitized[key] = value
                continue

            if key.startswith('_'):
                continue

            if callable(value) and not isinstance(value, type):
                continue
            if isinstance(value, type):
                continue

            from dataclasses import is_dataclass
            if is_dataclass(value) and not isinstance(value, type):
                continue

            if isinstance(value, dict):
                sanitized[key] = self._sanitize_config(value)
            elif isinstance(value, list):
                sanitized[key] = [
                    self._sanitize_config(item) if isinstance(item, dict)
                    else (item if not (callable(item) and not isinstance(item, type)) and
                          not (is_dataclass(item) and not isinstance(item, type)) else None)
                    for item in value
                ]
                sanitized[key] = [item for item in sanitized[key] if item is not None]
            else:
                sanitized[key] = value

        return sanitized

    def _sanitize_oauth_configs(self, oauth_configs: dict[str, Any]) -> dict[str, Any]:
        """Sanitize OAuth configs by converting dataclass instances to dicts"""
        from dataclasses import asdict, is_dataclass

        sanitized = {}
        for auth_type, oauth_config in oauth_configs.items():
            if is_dataclass(oauth_config) and not isinstance(oauth_config, type):
                try:
                    sanitized[auth_type] = asdict(oauth_config)
                except Exception:
                    logger.warning("Failed to convert OAuth config dataclass to dict")
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

    def _sanitize_tool_dict(self, tool: dict[str, Any]) -> dict[str, Any]:
        """Sanitize tool dict to ensure all fields are serializable"""
        if not isinstance(tool, dict):
            return {}

        sanitized = {}
        for key, value in tool.items():
            if callable(value) and not isinstance(value, type):
                continue
            if isinstance(value, type):
                continue

            if isinstance(value, dict):
                sanitized[key] = self._sanitize_config(value)
            elif isinstance(value, list):
                sanitized[key] = [self._sanitize_config(item) if isinstance(item, dict) else item
                                 for item in value if not (callable(item) and not isinstance(item, type))]
            else:
                sanitized[key] = value

        return sanitized

    def list_toolsets(self) -> list[str]:
        """List all registered toolset names"""
        return list(self._toolsets.keys())

    def get_all_toolsets(self) -> dict[str, dict[str, Any]]:
        """Get all registered toolsets"""
        return self._toolsets.copy()

    async def get_all_registered_toolsets(
        self,
        page: int = 1,
        limit: int = 20,
        search: str | None = None,
        include_tools: bool = True,
    ) -> dict[str, Any]:
        """
        Get all registered toolsets with pagination and search.
        Includes tools for frontend drag-and-drop selection.
        Returns serialized data (OAuth configs as dicts).
        """

        all_toolsets = []

        for normalized_name, metadata in self._toolsets.items():
            if metadata.get('isInternal', False):
                continue

            serialized_metadata = self.get_toolset_metadata(metadata['name'], serialize=True)
            if not serialized_metadata:
                continue

            tools = []
            if include_tools:
                tools.extend(
                    {
                        'name': tool_def.get('name', ''),
                        'fullName': f"{normalized_name}.{tool_def.get('name', '')}",
                        'description': tool_def.get('description', ''),
                        'parameters': tool_def.get('parameters', []),
                        'tags': tool_def.get('tags', []),
                    }
                    for tool_def in serialized_metadata.get('tools', [])
                    if isinstance(tool_def, dict)
                )

            serialized_cfg = serialized_metadata.get('config', {}) or {}
            toolset_info = {
                'name': serialized_metadata['name'],
                'normalized_name': normalized_name,
                'displayName': serialized_metadata['display_name'],
                'description': serialized_metadata['description'],
                'category': serialized_metadata['category'],
                'appGroup': serialized_metadata['app_group'],
                'supportedAuthTypes': serialized_metadata['supported_auth_types'],
                'iconPath': serialized_metadata['icon_path'],
                'documentationLinks': serialized_cfg.get('documentationLinks', []),
                'toolCount': len(serialized_metadata.get('tools', [])),
                'tools': tools,
                'config': serialized_cfg,
            }
            all_toolsets.append(toolset_info)

        if search:
            search_lower = search.lower()
            all_toolsets = [
                t for t in all_toolsets
                if search_lower in t['displayName'].lower()
                or search_lower in t['description'].lower()
                or search_lower in t['appGroup'].lower()
            ]

        all_toolsets.sort(key=lambda x: x['displayName'])

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

    def get_toolset_config(self, toolset_name: str) -> dict[str, Any] | None:
        """Get configuration schema for a toolset"""
        metadata = self.get_toolset_metadata(toolset_name)
        if not metadata:
            return None

        return metadata.get('config', {})


def get_toolset_registry() -> ToolsetRegistry:
    """Get the global toolset registry instance"""
    return ToolsetRegistry()

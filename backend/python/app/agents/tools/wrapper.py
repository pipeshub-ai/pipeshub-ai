"""
Enhanced wrapper to adapt registry tools to LangChain format with proper client initialization.
"""

import asyncio
import inspect
import json
from typing import Callable, Dict, List, Optional, Union

from langchain_core.tools import BaseTool
from pydantic import ConfigDict, Field

from app.agents.tools.factories.registry import ClientFactoryRegistry
from app.modules.agents.qna.chat_state import ChatState

# Constants
TOOL_RESULT_TUPLE_LENGTH = 2

# Type aliases
ToolResult = Union[tuple, str, dict, list, int, float, bool]


class ToolInstanceCreator:
    """Handles creation of tool instances with proper client initialization"""

    def __init__(self, state: ChatState) -> None:
        """Initialize tool instance creator.
        Args:
            state: Chat state containing configuration

        Raises:
            RuntimeError: If configuration service is not available
        """
        self.state = state
        self.logger = state.get("logger")
        self.config_service = self._get_config_service()

    def _get_config_service(self) -> object:
        """Get configuration service from state.

        Returns:
            Configuration service instance

        Raises:
            RuntimeError: If configuration service is not available
        """
        retrieval_service = self.state.get("retrieval_service")
        if not retrieval_service or not hasattr(retrieval_service, 'config_service'):
            raise RuntimeError("ConfigurationService not available")
        return retrieval_service.config_service

    def create_instance(self, action_class: type, app_name: str, tool_full_name: str = None) -> object:
        """Create an instance of an action class with proper client.
        Args:
            action_class: Class to instantiate
            app_name: Name of the application
            tool_full_name: Full tool name (e.g., "slack.send_message") for toolset lookup
        Returns:
            Instance of action_class
        """
        factory = ClientFactoryRegistry.get_factory(app_name)

        if factory:
            return self._create_with_factory(factory, action_class, app_name, tool_full_name)
        else:
            return self._fallback_creation(action_class)

    def _create_with_factory(
        self,
        factory: object,
        action_class: type,
        app_name: str,
        tool_full_name: str = None
    ) -> object:
        """Create instance using factory with toolset-based auth.

        Args:
            factory: Client factory instance
            action_class: Class to instantiate
            app_name: Application name
            tool_full_name: Full tool name for toolset lookup

        Returns:
            Instance of action_class
        """
        try:
            # Get toolset configuration
            toolset_config = self._get_toolset_config(tool_full_name) if tool_full_name else None

            if toolset_config:
                if self.logger:
                    self.logger.debug(f"Using toolset auth for {app_name} (ID: {tool_full_name})")
                client = factory.create_client_sync(
                    self.config_service,
                    self.logger,
                    toolset_config,
                    self.state
                )
                return action_class(client)

            # Fall back to legacy connector-based auth (if no toolset config)
            if self.logger:
                self.logger.warning(
                    f"No toolset config for {app_name} (tool: {tool_full_name}), "
                    f"falling back to legacy auth"
                )
            # Use empty toolset_config for legacy fallback
            client = factory.create_client_sync(
                self.config_service,
                self.logger,
                {},  # Empty toolset_config for legacy
                self.state
            )
            return action_class(client)
        except Exception as e:
            if self.logger:
                self.logger.error(
                    f"Failed to create client for {app_name}: {e}",
                    exc_info=True
                )
            # Check if this is an authentication error
            error_msg = str(e).lower()
            if "not authenticated" in error_msg or "oauth" in error_msg or "authentication" in error_msg:
                # Re-raise authentication errors with user-friendly message
                toolset_name = app_name.capitalize() if app_name else "Toolset"
                raise ValueError(
                    f"{toolset_name} toolset is not authenticated. Please complete the OAuth flow first. "
                    f"Go to Settings > Toolsets to authenticate your {toolset_name} account."
                ) from e
            # For other errors, fall back to legacy creation
            return self._fallback_creation(action_class)

    def _get_toolset_config(self, tool_full_name: str) -> Optional[Dict]:
        """Get toolset config for a tool from state.

        Args:
            tool_full_name: Full tool name (e.g., "slack.send_message")

        Returns:
            Toolset config dict or None
        """
        tool_to_toolset_map = self.state.get("tool_to_toolset_map", {})
        toolset_id = tool_to_toolset_map.get(tool_full_name)

        if not toolset_id:
            if self.logger:
                self.logger.debug(
                    f"No toolset ID found for tool {tool_full_name} in tool_to_toolset_map"
                )
            return None

        toolset_configs = self.state.get("toolset_configs", {})
        config = toolset_configs.get(toolset_id)

        if not config and self.logger:
            self.logger.warning(
                f"Toolset config not found for toolset ID {toolset_id} "
                f"(tool: {tool_full_name}). Config may not be loaded."
            )

        return config

    def _fallback_creation(self, action_class: type) -> object:
        """Attempt to create instance without client.

        Args:
            action_class: Class to instantiate

        Returns:
            Instance of action_class
        """
        try:
            # Try passing state for tools that need it (like retrieval)
            instance = action_class(state=self.state)
            # If instance has set_state method, also call it for compatibility
            if hasattr(instance, 'set_state'):
                instance.set_state(self.state)
            return instance
        except (TypeError, Exception):
            try:
                instance = action_class()
                # Try to set state if method exists
                if hasattr(instance, 'set_state'):
                    instance.set_state(self.state)
                return instance
            except (TypeError, Exception):
                try:
                    instance = action_class({})
                    if hasattr(instance, 'set_state'):
                        instance.set_state(self.state)
                    return instance
                except Exception:
                    instance = action_class(None)
                    if hasattr(instance, 'set_state'):
                        instance.set_state(self.state)
                    return instance


class RegistryToolWrapper(BaseTool):
    """Enhanced wrapper to adapt registry tools to LangChain format.

    Features:
    - Automatic client creation using factories
    - Proper error handling and formatting
    - Support for both standalone functions and class methods
    """

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra='allow',
        validate_assignment=True
    )

    app_name: str = Field(default="", description="Application name")
    tool_name: str = Field(default="", description="Tool name")
    registry_tool: object = Field(default=None, description="Registry tool instance")
    chat_state: object = Field(default=None, description="Chat state")
    instance_creator: object = Field(default=None, description="Tool instance creator")

    def __init__(
        self,
        app_name: str,
        tool_name: str,
        registry_tool: object,
        state: ChatState,
        **kwargs: Union[str, int, bool, dict, list, None]
    ) -> None:
        """Initialize registry tool wrapper.
        Args:
            app_name: Application name
            tool_name: Tool name
            registry_tool: Registry tool instance
            state: Chat state
            **kwargs: Additional arguments
        """
        base_description = getattr(
            registry_tool,
            'description',
            f"Tool: {app_name}.{tool_name}"
        )
        full_description = self._build_description(base_description, registry_tool)

        instance_creator = ToolInstanceCreator(state)

        init_data: Dict[str, Union[str, object]] = {
            'name': f"{app_name}.{tool_name}",
            'description': full_description,
            'app_name': app_name,
            'tool_name': tool_name,
            'registry_tool': registry_tool,
            'chat_state': state,
            'instance_creator': instance_creator,
            **kwargs
        }

        super().__init__(**init_data)

    def _build_description(self, base_description: str, registry_tool: object) -> str:
        """Build comprehensive description with parameters.

        Args:
            base_description: Base description text
            registry_tool: Registry tool instance

        Returns:
            Complete description with parameters
        """
        try:
            params = getattr(registry_tool, 'parameters', []) or []
            if not params:
                return base_description

            formatted_params = self._format_parameters(params)
            params_doc = "\nParameters:\n- " + "\n- ".join(formatted_params)
            return f"{base_description}{params_doc}"
        except Exception:
            return base_description

    @staticmethod
    def _format_parameters(params: List[object]) -> List[str]:
        """Format parameters for description.

        Args:
            params: List of parameter objects

        Returns:
            List of formatted parameter strings
        """
        formatted_params = []
        for param in params:
            try:
                type_name = getattr(
                    param.type,
                    'name',
                    str(getattr(param, 'type', 'string'))
                )
            except Exception:
                type_name = 'string'

            required_marker = (
                ' (required)' if getattr(param, 'required', False) else ''
            )
            formatted_params.append(
                f"{param.name}{required_marker}: "
                f"{getattr(param, 'description', '')} [{type_name}]"
            )
        return formatted_params

    @property
    def state(self) -> ChatState:
        """Access the chat state.

        Returns:
            Chat state object
        """
        return self.chat_state

    async def arun(self, *args, **kwargs) -> Union[str, tuple]:
        """Async execution - runs directly in the event loop, no thread executor needed.

        This ensures tools run in the same event loop as FastAPI and Neo4j driver.
        All tool methods should be async to avoid event loop conflicts.
        """
        try:
            # Extract arguments - arun can be called with args[0] as dict or **kwargs
            if args and isinstance(args[0], dict):
                arguments = args[0]
            else:
                arguments = kwargs if kwargs else {}

            result = await self._execute_tool_async(arguments)
            # Preserve tuple structure for success detection
            if isinstance(result, (tuple, list)) and len(result) == TOOL_RESULT_TUPLE_LENGTH:
                return result
            return self._format_result(result)
        except Exception as e:
            return self._format_error(e, kwargs if kwargs else (args[0] if args else {}))

    def _run(self, **kwargs: Union[str, int, bool, dict, list, None]) -> Union[str, tuple]:
        """Execute the registry tool (sync fallback - should not be used in async context).

        Args:
            **kwargs: Tool arguments

        Returns:
            Tool result (tuple if (bool, str) format, otherwise string)
            Preserves tuple structure for success detection in nodes.py
        """
        try:
            result = self._execute_tool(kwargs)
            # Preserve tuple structure for success detection
            # nodes.py will handle formatting for LLM
            if isinstance(result, (tuple, list)) and len(result) == TOOL_RESULT_TUPLE_LENGTH:
                return result
            return self._format_result(result)
        except Exception as e:
            return self._format_error(e, kwargs)

    def _execute_tool(
        self,
        arguments: Dict[str, Union[str, int, bool, dict, list, None]]
    ) -> ToolResult:
        """Execute the registry tool function.

        Args:
            arguments: Tool arguments

        Returns:
            Tool execution result
        """
        tool_function = self.registry_tool.function

        if self._is_class_method(tool_function):
            return self._execute_class_method(tool_function, arguments)
        else:
            return tool_function(**arguments)

    @staticmethod
    def _is_class_method(func: Callable) -> bool:
        """Check if function is a class method.

        Args:
            func: Function to check

        Returns:
            True if function is a class method
        """
        return hasattr(func, '__qualname__') and '.' in func.__qualname__

    async def _execute_tool_async(
        self,
        arguments: Dict[str, Union[str, int, bool, dict, list, None]]
    ) -> ToolResult:
        """Execute the registry tool function asynchronously.

        Args:
            arguments: Tool arguments

        Returns:
            Tool execution result
        """
        tool_function = self.registry_tool.function

        if self._is_class_method(tool_function):
            return await self._execute_class_method_async(tool_function, arguments)
        else:
            # If it's a regular function, check if it's async
            # Use inspect.iscoroutinefunction for better compatibility
            if inspect.iscoroutinefunction(tool_function):
                return await tool_function(**arguments)
            else:
                return tool_function(**arguments)

    async def _execute_class_method_async(
        self,
        tool_function: Callable,
        arguments: Dict[str, Union[str, int, bool, dict, list, None]]
    ) -> ToolResult:
        """Execute a class method asynchronously by creating an instance.

        Args:
            tool_function: Tool function to execute
            arguments: Function arguments
        Returns:
            Execution result

        Raises:
            RuntimeError: If method execution fails
        """
        try:
            class_name = tool_function.__qualname__.split('.')[0]
            module_name = tool_function.__module__

            action_module = __import__(module_name, fromlist=[class_name])
            action_class = getattr(action_module, class_name)

            # Pass tool full name for toolset auth lookup
            tool_full_name = self.name  # self.name is "app_name.tool_name"
            instance = self.instance_creator.create_instance(
                action_class,
                self.app_name,
                tool_full_name
            )

            bound_method = getattr(instance, self.tool_name)

            try:
                # Always call the method first - the @tool decorator may wrap `async def` such that
                # iscoroutinefunction() returns False on the bound method, but calling it still
                # returns a coroutine. Detect and await it at runtime.
                result = bound_method(**arguments)
                # Check if the result is actually a coroutine (handles decorator-wrapped async methods)
                if asyncio.iscoroutine(result):
                    result = await result
                return result
            finally:
                # Teardown background resources if the action provides shutdown()
                shutdown = getattr(instance, 'shutdown', None)
                if callable(shutdown):
                    try:
                        # Check if shutdown is async too
                        if inspect.iscoroutinefunction(shutdown):
                            await shutdown()
                        else:
                            shutdown()
                    except Exception:
                        pass

        except Exception as e:
            raise RuntimeError(
                f"Failed to execute class method "
                f"'{self.app_name}.{self.tool_name}': {str(e)}"
            ) from e

    def _execute_class_method(
        self,
        tool_function: Callable,
        arguments: Dict[str, Union[str, int, bool, dict, list, None]]
    ) -> ToolResult:
        """Execute a class method by creating an instance (sync fallback).

        Args:
            tool_function: Tool function to execute
            arguments: Function arguments
        Returns:
            Execution result

        Raises:
            RuntimeError: If method execution fails
        """
        try:
            class_name = tool_function.__qualname__.split('.')[0]
            module_name = tool_function.__module__

            action_module = __import__(module_name, fromlist=[class_name])
            action_class = getattr(action_module, class_name)

            # Pass tool full name for toolset auth lookup
            tool_full_name = self.name  # self.name is "app_name.tool_name"
            instance = self.instance_creator.create_instance(
                action_class,
                self.app_name,
                tool_full_name
            )

            bound_method = getattr(instance, self.tool_name)
            try:
                return bound_method(**arguments)
            finally:
                # Teardown background resources if the action provides shutdown()
                shutdown = getattr(instance, 'shutdown', None)
                if callable(shutdown):
                    try:
                        shutdown()
                    except Exception:
                        pass

        except Exception as e:
            raise RuntimeError(
                f"Failed to execute class method "
                f"'{self.app_name}.{self.tool_name}': {str(e)}"
            ) from e

    def _format_result(self, result: ToolResult) -> str:
        """Format tool result for LLM consumption.

        Args:
            result: Tool execution result

        Returns:
            Formatted result string
        """
        if isinstance(result, (tuple, list)) and len(result) == TOOL_RESULT_TUPLE_LENGTH:
            success, result_data = result
            return str(result_data)

        return str(result)

    def _format_error(
        self,
        error: Exception,
        arguments: Dict[str, Union[str, int, bool, dict, list, None]]
    ) -> str:
        """Format error message.

        Args:
            error: Exception that occurred
            arguments: Tool arguments

        Returns:
            Formatted error message as JSON string
        """
        error_msg = (
            f"Error executing tool {self.app_name}.{self.tool_name}: "
            f"{str(error)}"
        )

        logger = self.state.get("logger") if hasattr(self.state, 'get') else None
        if logger:
            logger.error(error_msg)

        return json.dumps({
            "status": "error",
            "message": error_msg,
            "tool": f"{self.app_name}.{self.tool_name}",
            "args": arguments
        }, indent=2)

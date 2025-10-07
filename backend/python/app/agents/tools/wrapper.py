"""
Enhanced wrapper to adapt registry tools to LangChain format with proper client initialization.
"""

import json
from typing import Any, Callable, Union

from langchain.tools import BaseTool
from pydantic import ConfigDict, Field

from app.agents.tools.factories.registry import ClientFactoryRegistry
from app.modules.agents.qna.chat_state import ChatState


class ToolInstanceCreator:
    """Handles creation of tool instances with proper client initialization"""

    def __init__(self, state: 'ChatState') -> None:
        """
        Initialize tool instance creator.

        Args:
            state: Chat state containing configuration
        """
        self.state = state
        self.logger = state.get("logger")
        self.config_service = self._get_config_service()

    def _get_config_service(self):
        """Get configuration service from state"""
        retrieval_service = self.state.get("retrieval_service")
        if not retrieval_service or not hasattr(retrieval_service, 'config_service'):
            raise RuntimeError("ConfigurationService not available")
        return retrieval_service.config_service

    def create_instance(self, action_class, app_name: str):
        """
        Create an instance of an action class with proper client.

        Args:
            action_class: Class to instantiate
            app_name: Name of the application

        Returns:
            Instance of action_class
        """
        # Try to get client factory
        factory = ClientFactoryRegistry.get_factory(app_name)

        if factory:
            # Create client using factory
            try:
                client = factory.create_client_sync(
                    self.config_service,
                    self.logger
                )
                return action_class(client)
            except Exception as e:
                if self.logger:
                    self.logger.error(
                        f"Failed to create client for {app_name}: {e}"
                    )
                # Fallback to no-arg construction
                return self._fallback_creation(action_class)
        else:
            # No factory, try direct construction
            return self._fallback_creation(action_class)

    def _fallback_creation(self, action_class):
        """Attempt to create instance without client"""
        try:
            return action_class()
        except TypeError:
            try:
                return action_class({})
            except Exception:
                return action_class(None)


class RegistryToolWrapper(BaseTool):
    """
    Enhanced wrapper to adapt registry tools to LangChain format.

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
    registry_tool: Any = Field(default=None, description="Registry tool instance")
    chat_state: Any = Field(default=None, description="Chat state")
    instance_creator: Any = Field(default=None, description="Tool instance creator")

    def __init__(
        self,
        app_name: str,
        tool_name: str,
        registry_tool,
        state: 'ChatState',
        **kwargs
    ) -> None:
        """
        Initialize registry tool wrapper.

        Args:
            app_name: Application name
            tool_name: Tool name
            registry_tool: Registry tool instance
            state: Chat state
            **kwargs: Additional arguments
        """
        # Build description
        base_description = getattr(
            registry_tool,
            'description',
            f"Tool: {app_name}.{tool_name}"
        )
        full_description = self._build_description(base_description, registry_tool)

        # Create instance creator
        instance_creator = ToolInstanceCreator(state)

        init_data = {
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

    def _build_description(self, base_description: str, registry_tool) -> str:
        """Build comprehensive description with parameters"""
        try:
            params = getattr(registry_tool, 'parameters', []) or []
            if not params:
                return base_description

            formatted_params = []
            for p in params:
                try:
                    type_name = getattr(
                        p.type, 'name',
                        str(getattr(p, 'type', 'string'))
                    )
                except Exception:
                    type_name = 'string'

                required_marker = (
                    ' (required)' if getattr(p, 'required', False) else ''
                )
                formatted_params.append(
                    f"{p.name}{required_marker}: "
                    f"{getattr(p, 'description', '')} [{type_name}]"
                )

            params_doc = "\nParameters:\n- " + "\n- ".join(formatted_params)
            return f"{base_description}{params_doc}"
        except Exception:
            return base_description

    @property
    def state(self) -> 'ChatState':
        """Access the chat state"""
        return self.chat_state

    def _run(self, **kwargs) -> str:
        """Execute the registry tool"""
        try:
            result = self._execute_tool(kwargs)
            return self._format_result(result)
        except Exception as e:
            return self._format_error(e, kwargs)

    def _execute_tool(
        self,
        arguments: dict
    ) -> Union[tuple, str, dict, list, int, float, bool]:
        """Execute the registry tool function"""
        tool_function = self.registry_tool.function

        # Check if this is a class method
        if self._is_class_method(tool_function):
            return self._execute_class_method(tool_function, arguments)
        else:
            # Standalone function
            return tool_function(**arguments)

    def _is_class_method(self, func: Callable) -> bool:
        """Check if function is a class method"""
        return hasattr(func, '__qualname__') and '.' in func.__qualname__

    def _execute_class_method(self, tool_function: Callable, arguments: dict):
        """Execute a class method by creating an instance"""
        try:
            # Get class information
            class_name = tool_function.__qualname__.split('.')[0]
            module_name = tool_function.__module__

            # Import module and get class
            action_module = __import__(module_name, fromlist=[class_name])
            action_class = getattr(action_module, class_name)

            # Create instance using factory pattern
            instance = self.instance_creator.create_instance(
                action_class,
                self.app_name
            )

            # Execute method
            bound_method = getattr(instance, self.tool_name)
            return bound_method(**arguments)

        except Exception as e:
            raise RuntimeError(
                f"Failed to execute class method "
                f"'{self.app_name}.{self.tool_name}': {str(e)}"
            )

    def _format_result(self, result) -> str:
        """Format tool result for LLM consumption"""
        # Handle tuple format (success, json_string)
        if isinstance(result, (tuple, list)) and len(result) == 2:
            success, result_data = result
            return str(result_data)

        return str(result)

    def _format_error(self, error: Exception, arguments: dict) -> str:
        """Format error message"""
        error_msg = (
            f"Error executing tool {self.app_name}.{self.tool_name}: "
            f"{str(error)}"
        )

        if self.state.get("logger"):
            self.state["logger"].error(error_msg)

        return json.dumps({
            "status": "error",
            "message": error_msg,
            "tool": f"{self.app_name}.{self.tool_name}",
            "args": arguments
        }, indent=2)

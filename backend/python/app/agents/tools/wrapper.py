"""
Enhanced wrapper to adapt registry tools to LangChain format with proper client initialization.
"""

import json
from typing import Callable, Dict, List, Union

from langchain.tools import BaseTool
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

    def create_instance(self, action_class: type, app_name: str) -> object:
        """Create an instance of an action class with proper client.
        Args:
            action_class: Class to instantiate
            app_name: Name of the application
        Returns:
            Instance of action_class
        """
        factory = ClientFactoryRegistry.get_factory(app_name)

        if factory:
            return self._create_with_factory(factory, action_class, app_name)
        else:
            return self._fallback_creation(action_class)

    def _create_with_factory(
        self,
        factory: object,
        action_class: type,
        app_name: str
    ) -> object:
        """Create instance using factory.

        Args:
            factory: Client factory instance
            action_class: Class to instantiate
            app_name: Application name

        Returns:
            Instance of action_class
        """
        try:
            client = factory.create_client_sync(
                self.config_service,
                self.logger,
                self.state,
            )
            return action_class(client)
        except Exception as e:
            if self.logger:
                self.logger.error(
                    f"Failed to create client for {app_name}: {e}"
                )
            return self._fallback_creation(action_class)

    def _fallback_creation(self, action_class: type) -> object:
        """Attempt to create instance without client.

        Args:
            action_class: Class to instantiate

        Returns:
            Instance of action_class
        """
        try:
            return action_class()
        except TypeError:
            try:
                return action_class({})
            except Exception:
                return action_class(None)


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

    def _run(self, **kwargs: Union[str, int, bool, dict, list, None]) -> str:
        """Execute the registry tool.

        Args:
            **kwargs: Tool arguments

        Returns:
            Formatted result string
        """
        try:
            result = self._execute_tool(kwargs)
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

    def _execute_class_method(
        self,
        tool_function: Callable,
        arguments: Dict[str, Union[str, int, bool, dict, list, None]]
    ) -> ToolResult:
        """Execute a class method by creating an instance.

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

            instance = self.instance_creator.create_instance(
                action_class,
                self.app_name
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

import json
import os
from typing import Any, Union

from langchain.tools import BaseTool
from pydantic import ConfigDict, Field

from app.agents.tools.registry import _global_tools_registry
from app.modules.agents.qna.chat_state import ChatState


class RegistryToolWrapper(BaseTool):
    """Wrapper to adapt registry tools to LangChain BaseTool format"""

    # Proper Pydantic v2 configuration
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra='allow',
        validate_assignment=True
    )

    # Define the custom fields as class attributes with proper typing
    app_name: str = Field(default="", description="Application name")
    tool_name: str = Field(default="", description="Tool name")
    registry_tool: Any = Field(default=None, description="Registry tool instance")
    chat_state: Any = Field(default=None, description="Chat state")

    def __init__(self, app_name: str, tool_name: str, registry_tool, state: ChatState, **kwargs) -> None:
        # Prepare the initialization data
        init_data = {
            'name': f"{app_name}.{tool_name}",  # Use dot notation for consistency
            'description': getattr(registry_tool, 'description', f"Tool: {app_name}.{tool_name}"),
            'app_name': app_name,
            'tool_name': tool_name,
            'registry_tool': registry_tool,
            'chat_state': state,
            **kwargs
        }

        # Call parent constructor with all the data
        super().__init__(**init_data)

    @property
    def state(self) -> ChatState:
        """Access the chat state"""
        return self.chat_state

    def _run(self, **kwargs) -> str:
        """Execute the registry tool directly"""
        try:
            # Execute the tool function directly - no ToolExecutor
            result = self._execute_tool_directly(kwargs)

            # Convert result to string format for LLM consumption
            return self._format_result(result)

        except Exception as e:
            error_msg = f"Error executing tool {self.app_name}.{self.tool_name}: {str(e)}"
            if hasattr(self.state, 'get') and self.state.get("logger"):
                self.state["logger"].error(error_msg)
            return json.dumps({
                "status": "error",
                "message": error_msg,
                "tool": f"{self.app_name}.{self.tool_name}",
                "args": kwargs
            }, indent=2)

    def _execute_tool_directly(self, arguments: dict) -> Union[tuple, str, dict, list, int, float, bool]:
        """Execute the registry tool function directly"""
        tool_function = self.registry_tool.function

        # Check if this is a class method (has 'self' parameter)
        if hasattr(tool_function, '__qualname__') and '.' in tool_function.__qualname__:
            # This is a class method, we need to create an instance
            class_name = tool_function.__qualname__.split('.')[0]
            module_name = tool_function.__module__

            try:
                # Import the module and get the class
                action_module = __import__(module_name, fromlist=[class_name])
                action_class = getattr(action_module, class_name)

                # Create an instance with configuration
                instance = self._create_tool_instance(action_class)

                # Get the bound method and call it
                bound_method = getattr(instance, self.tool_name)
                return bound_method(**arguments)

            except Exception as e:
                raise RuntimeError(f"Failed to create instance for tool '{self.app_name}.{self.tool_name}': {str(e)}")
        else:
            # This is a standalone function
            return tool_function(**arguments)

    def _create_tool_instance(self, action_class) -> object:
        """Create an instance of the tool class with appropriate configuration"""
        try:
            # Get tool configurations from state
            tool_configs = self.state.get("tool_configs", {})
            app_config = tool_configs.get(self.app_name, {})

            # Try different initialization strategies based on the tool type
            if self.app_name == "slack":
                from app.agents.actions.slack.config import SlackTokenConfig
                if app_config.get("slack_bot_token"):
                    config = SlackTokenConfig(token=app_config["slack_bot_token"])
                    return action_class(config)

            elif self.app_name == "jira":
                from app.agents.actions.jira.config import (
                    JiraTokenConfig,
                    JiraUsernamePasswordConfig,
                )
                if app_config.get("jira_api_token"):
                    config = JiraTokenConfig(
                        base_url=app_config.get("jira_url", ""),
                        token=app_config["jira_api_token"]
                    )
                    return action_class(config)
                elif app_config.get("jira_username") and app_config.get("jira_password"):
                    config = JiraUsernamePasswordConfig(
                        base_url=app_config.get("jira_url", ""),
                        username=app_config["jira_username"],
                        password=app_config["jira_password"]
                    )
                    return action_class(config)

            elif self.app_name == "confluence":
                from app.agents.actions.confluence.config import (
                    ConfluenceTokenConfig,
                    ConfluenceUsernamePasswordConfig,
                )
                if app_config.get("confluence_api_token"):
                    config = ConfluenceTokenConfig(
                        base_url=app_config.get("confluence_url", ""),
                        token=app_config["confluence_api_token"]
                    )
                    return action_class(config)
                elif app_config.get("confluence_username") and app_config.get("confluence_password"):
                    config = ConfluenceUsernamePasswordConfig(
                        base_url=app_config.get("confluence_url", ""),
                        username=app_config["confluence_username"],
                        password=app_config["confluence_password"]
                    )
                    return action_class(config)

            elif self.app_name == "gmail":
                from app.agents.actions.google.gmail.config import GoogleGmailConfig
                config = GoogleGmailConfig(
                    credentials_path=app_config.get("google_credentials_path"),
                    token_file_path=app_config.get("google_token_path"),
                    scopes=app_config.get("google_scopes")
                )
                return action_class(config)

            elif self.app_name == "google_calendar":
                from app.agents.actions.google.google_calendar.config import (
                    GoogleCalendarConfig,
                )
                config = GoogleCalendarConfig(
                    credentials_path=app_config.get("google_credentials_path"),
                    token_file_path=app_config.get("google_token_path"),
                    scopes=app_config.get("google_scopes")
                )
                return action_class(config)

            elif self.app_name == "google_drive":
                from app.agents.actions.google.google_drive.config import (
                    GoogleDriveConfig,
                )
                config = GoogleDriveConfig(
                    credentials_path=app_config.get("google_credentials_path"),
                    token_file_path=app_config.get("google_token_path"),
                    scopes=app_config.get("google_scopes")
                )
                return action_class(config)

            elif self.app_name == "github":
                from app.agents.actions.github.config import GithubConfig
                config = GithubConfig(
                    base_url=app_config.get("github_url", "https://api.github.com"),
                    token=app_config.get("github_token", "")
                )
                return action_class(config)

            elif self.app_name == "calculator":
                # Calculator doesn't need configuration
                return action_class()

            # Fallback: try to create without arguments
            try:
                return action_class()
            except TypeError:
                # Try with empty config
                try:
                    return action_class({})
                except Exception:
                    # Last resort: try with None
                    return action_class(None)

        except Exception as e:
            raise RuntimeError(f"Failed to create instance for {self.app_name}: {str(e)}")

    def _format_result(self, result) -> str:
        """Format tool result for LLM consumption"""
        # Handle tuple format (success, json_string) from registry tools
        MAX_RESULT_LENGTH = 2
        if isinstance(result, (tuple, list)) and len(result) == MAX_RESULT_LENGTH:
            success, result_data = result
            return str(result_data)

        # Handle direct result
        return str(result)


def get_agent_tools(state: ChatState) -> list:
    """Get all available tools from the global registry - no filtering, let LLM decide"""
    tools = []
    logger = state.get("logger")

    # Get ALL tools from the global registry
    registry_tools = _global_tools_registry.get_all_tools()
    if logger:
        logger.info(f"Loading {len(registry_tools)} tools from global registry")

    # User-configured tools filter (optional - if None or empty, load all tools)
    user_enabled_tools = state.get("tools", None)

    # If tools is an empty list [], treat it as "load all tools"
    if user_enabled_tools is not None and len(user_enabled_tools) == 0:
        user_enabled_tools = None
        if logger:
            logger.info("Empty tools list detected - loading ALL available tools")

    # Convert all registry tools to LangChain format
    for full_tool_name, registry_tool in registry_tools.items():
        try:
            if "." not in full_tool_name:
                # Handle tools without app prefix
                app_name = "default"
                tool_name = full_tool_name
            else:
                app_name, tool_name = full_tool_name.split(".", 1)

            # Determine if this tool should be included
            should_include = False

            # Case 1: No filter specified - include all tools
            if user_enabled_tools is None:
                should_include = True
                if logger:
                    logger.debug(f"Including {full_tool_name} - no filter specified")

            # Case 2: User has specified tools - check if this tool is enabled
            elif user_enabled_tools is not None:
                should_include = (
                    full_tool_name in user_enabled_tools or  # Exact match: "slack.send_message"
                    tool_name in user_enabled_tools or      # Tool name match: "send_message"
                    app_name in user_enabled_tools          # App name match: "slack" (includes all slack tools)
                )
                if should_include and logger:
                    logger.debug(f"Including {full_tool_name} - matches user filter")

            # Case 3: Always include essential tools
            if not should_include and _is_essential_tool(full_tool_name):
                should_include = True
                if logger:
                    logger.debug(f"Including {full_tool_name} - essential tool")

            if should_include:
                wrapper_tool = RegistryToolWrapper(app_name, tool_name, registry_tool, state)
                tools.append(wrapper_tool)
                if logger:
                    logger.debug(f"✅ Added tool: {full_tool_name}")
            else:
                if logger:
                    logger.debug(f"❌ Skipped tool: {full_tool_name}")

        except Exception as e:
            if logger:
                logger.error(f"Failed to add tool {full_tool_name}: {e}")
            # Continue with other tools even if one fails
            continue

    # Initialize tool state storage
    _initialize_tool_state(state)

    # Store available tools in state for reference
    state["available_tools"] = [tool.name for tool in tools]

    if logger:
        logger.info(f"Total tools available to LLM: {len(tools)}")

        # Log breakdown by app for debugging
        tool_breakdown = {}
        for tool in tools:
            # Extract app name from tool name (format: app_name.tool_name)
            if "." in tool.name:
                app = tool.name.split(".")[0]
                tool_breakdown[app] = tool_breakdown.get(app, 0) + 1

        logger.info(f"Tool breakdown by app: {tool_breakdown}")

    return tools


def _is_essential_tool(full_tool_name: str) -> bool:
    """Check if a tool is essential and should always be included"""
    essential_patterns = [
        "calculator.",
        "web_search",
        "get_current_datetime"
    ]
    return any(pattern in full_tool_name for pattern in essential_patterns)


def _initialize_tool_state(state: ChatState) -> None:
    """Initialize tool-related state variables"""
    state.setdefault("tool_results", [])
    state.setdefault("all_tool_results", [])
    state.setdefault("web_search_results", [])
    state.setdefault("web_search_template_context", {})
    state.setdefault("tool_configs", {})

    # Initialize tool configurations from environment
    _load_tool_configs(state)


def _load_tool_configs(state: ChatState) -> None:
    """Load tool configurations from environment variables"""
    configs = {}

    # Load all possible tool configurations from environment
    env_mappings = {
        "slack": {"slack_bot_token": "SLACK_BOT_TOKEN"},
        "jira": {
            "jira_url": "JIRA_URL",
            "jira_username": "JIRA_USERNAME",
            "jira_password": "JIRA_PASSWORD",
            "jira_api_token": "JIRA_API_TOKEN"
        },
        "confluence": {
            "confluence_url": "CONFLUENCE_URL",
            "confluence_username": "CONFLUENCE_USERNAME",
            "confluence_password": "CONFLUENCE_PASSWORD",
            "confluence_api_token": "CONFLUENCE_API_TOKEN"
        },
        "gmail": {
            "google_credentials_path": "GOOGLE_CREDENTIALS_PATH",
            "google_token_path": "GOOGLE_TOKEN_PATH",
            "google_scopes": "GOOGLE_SCOPES"
        },
        "google_calendar": {
            "google_credentials_path": "GOOGLE_CREDENTIALS_PATH",
            "google_token_path": "GOOGLE_TOKEN_PATH",
            "google_scopes": "GOOGLE_SCOPES"
        },
        "google_drive": {
            "google_credentials_path": "GOOGLE_CREDENTIALS_PATH",
            "google_token_path": "GOOGLE_TOKEN_PATH",
            "google_scopes": "GOOGLE_SCOPES"
        },
        "github": {
            "github_url": "GITHUB_URL",
            "github_token": "GITHUB_TOKEN"
        }
    }

    for tool_app, env_mapping in env_mappings.items():
        app_config = {}
        for config_key, env_var in env_mapping.items():
            value = os.getenv(env_var)
            if value:
                app_config[config_key] = value

        # Only add config if at least one value is present
        if app_config:
            configs[tool_app] = app_config

    state["tool_configs"] = configs


def get_tool_by_name(tool_name: str, state: ChatState) -> RegistryToolWrapper | None:
    """Get a specific tool by name from the registry"""
    registry_tools = _global_tools_registry.get_all_tools()

    # Try exact full name match first
    if tool_name in registry_tools:
        if "." in tool_name:
            app_name, actual_tool_name = tool_name.split(".", 1)
        else:
            app_name, actual_tool_name = "default", tool_name
        return RegistryToolWrapper(app_name, actual_tool_name, registry_tools[tool_name], state)

    # Try partial name match
    for full_name, registry_tool in registry_tools.items():
        if hasattr(registry_tool, 'tool_name') and (
            registry_tool.tool_name == tool_name or full_name.endswith(f".{tool_name}")
        ):
            if "." in full_name:
                app_name, actual_tool_name = full_name.split(".", 1)
            else:
                app_name, actual_tool_name = "default", full_name
            return RegistryToolWrapper(app_name, actual_tool_name, registry_tool, state)

    return None


def get_tool_results_summary(state: ChatState) -> str:
    """Get a summary of all tool results for the LLM"""
    all_results = state.get("all_tool_results", [])

    if not all_results:
        return "No tools have been executed yet."

    summary = f"Tool Execution Summary (Total: {len(all_results)}):\n"

    # Group by tool type and status
    tool_summary = {}
    for result in all_results:
        tool_name = result.get("tool_name", "unknown")
        status = result.get("status", "unknown")

        if tool_name not in tool_summary:
            tool_summary[tool_name] = {"success": 0, "error": 0, "results": []}

        tool_summary[tool_name][status] += 1
        tool_summary[tool_name]["results"].append(result)

    for tool_name, stats in tool_summary.items():
        summary += f"\n{tool_name}:\n"
        summary += f"  - Successful executions: {stats['success']}\n"
        summary += f"  - Failed executions: {stats['error']}\n"

        # Show last result for context
        if stats["results"]:
            last_result = stats["results"][-1]
            MAX_RESULT_PREVIEW_LENGTH = 150
            result_preview = str(last_result.get("result", ""))[:MAX_RESULT_PREVIEW_LENGTH]
            if len(result_preview) == MAX_RESULT_PREVIEW_LENGTH:
                result_preview += "..."
            summary += f"  - Last result: {result_preview}\n"

    return summary


def get_all_available_tool_names() -> dict:
    """Get list of all available tool names from registry"""
    registry_tools = list(_global_tools_registry.list_tools())

    return {
        "registry_tools": registry_tools,
        "total_count": len(registry_tools)
    }


def get_tool_usage_guidance() -> str:
    """Provide comprehensive guidance for tool usage"""
    return """
COMPREHENSIVE TOOL USAGE GUIDANCE:

You have access to a comprehensive set of enterprise and utility tools. Use them naturally based on user requests:

APPROACH:
- Analyze the user's request to understand what they want to accomplish
- Choose the most appropriate tools to fulfill their request
- Execute tools in logical sequence when multiple steps are needed
- Provide clear, helpful responses based on tool results

AVAILABLE TOOL CATEGORIES:
- Mathematical calculations and data processing
- Web search and information retrieval
- File operations and content management
- Communication (Slack, Email)
- Project management (JIRA)
- Documentation (Confluence)
- Calendar and scheduling
- And many more enterprise tools

BEST PRACTICES:
- Use tools when they can provide better, more current, or more accurate information
- Combine multiple tools when needed to complete complex tasks
- Handle errors gracefully and try alternative approaches
- Provide context about what tools you're using and why

You have complete freedom to decide which tools to use and how to use them based on the user's needs.
"""

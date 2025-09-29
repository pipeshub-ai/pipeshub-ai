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
        # Build a helpful description that includes parameters so the LLM uses correct arg names
        base_description = getattr(registry_tool, 'description', f"Tool: {app_name}.{tool_name}")
        try:
            params = getattr(registry_tool, 'parameters', []) or []
            if params:
                formatted_params = []
                for p in params:
                    # p has: name, type, description, required
                    try:
                        type_name = getattr(p.type, 'name', str(getattr(p, 'type', 'string')))
                    except Exception:
                        type_name = 'string'
                    formatted_params.append(
                        f"{p.name}{' (required)' if getattr(p, 'required', False) else ''}: {getattr(p, 'description', '')} [{type_name}]"
                    )
                params_doc = "\nParameters:\n- " + "\n- ".join(formatted_params)
                full_description = f"{base_description}{params_doc}"
            else:
                full_description = base_description
        except Exception:
            full_description = base_description

        init_data = {
            'name': f"{app_name}.{tool_name}",  # Use dot notation for consistency
            'description': full_description,
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

    # Note: We avoid providing args_schema/tool_call_schema to prevent recursion issues in LC conversion.

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
            # Try different initialization strategies based on the tool type
            if self.app_name == "slack":
                return self._create_slack_tool_instance(action_class)

            elif self.app_name == "jira":
                return self._create_jira_tool_instance(action_class)

            elif self.app_name == "confluence":
                return self._create_confluence_tool_instance(action_class)

            elif self.app_name in ["onedrive", "outlook", "sharepoint", "one_note", "users_groups", "teams"]:
                return self._create_microsoft_tool_instance(action_class)

            elif self.app_name == "notion":
                return self._create_notion_tool_instance(action_class)

            elif self.app_name in ["gmail", "google_calendar", "drive"]:
                # Use the proper Google client initialization from sources
                return self._create_google_tool_instance(action_class)

            elif self.app_name == "github":
                return self._create_github_tool_instance(action_class)

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

    def _create_google_tool_instance(self, action_class) -> object:
        """Create Google tool instance using proper client initialization"""
        try:
            # Get ConfigurationService from retrieval_service
            retrieval_service = self.state.get("retrieval_service")
            if not retrieval_service or not hasattr(retrieval_service, 'config_service'):
                raise RuntimeError("ConfigurationService not available through retrieval_service")

            config_service = retrieval_service.config_service
            logger = self.state.get("logger")

            # Determine service name and version based on app_name
            service_mapping = {
                "gmail": ("gmail", "v1"),
                "google_calendar": ("calendar", "v3"),
                "drive": ("drive", "v3")
            }

            service_name, version = service_mapping.get(self.app_name, ("drive", "v3"))

            # Import the Google client
            # Create the Google client using the proper initialization pattern
            import asyncio
            import concurrent.futures

            from app.sources.client.google.google import GoogleClient

            # Check if we're already in an async context
            try:
                asyncio.get_running_loop()
                # We're in an async context, use a thread pool to run the async code
                logger.debug(f"Async context detected for {self.app_name} tool creation - using thread pool")

                # Use a thread pool to run the async code
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(
                        asyncio.run,
                        GoogleClient.build_from_services(
                            service_name=service_name,
                            logger=logger,
                            config_service=config_service,
                            is_individual=True,  # Default to individual, can be made configurable
                            version=version
                        )
                    )
                    google_client = future.result()

            except RuntimeError:
                # No running loop, we can use asyncio.run
                google_client = asyncio.run(GoogleClient.build_from_services(
                    service_name=service_name,
                    logger=logger,
                    config_service=config_service,
                    is_individual=True,  # Default to individual, can be made configurable
                    version=version
                ))

            # Get the actual client object
            client = google_client.get_client()

            # Create the tool instance with the client
            return action_class(client)

        except Exception as e:
            logger = self.state.get("logger")
            if logger:
                logger.error(f"Failed to create Google tool instance for {self.app_name}: {str(e)}")
            # Try to create without arguments as last resort
            return action_class()

    def _create_microsoft_tool_instance(self, action_class) -> object:
        """Create Microsoft tool instance (OneDrive, Outlook, SharePoint, OneNote, Teams, Users/Groups) using MSGraphClient"""
        try:
            # Get ConfigurationService from retrieval_service
            retrieval_service = self.state.get("retrieval_service")
            if not retrieval_service or not hasattr(retrieval_service, 'config_service'):
                raise RuntimeError("ConfigurationService not available through retrieval_service")

            config_service = retrieval_service.config_service
            logger = self.state.get("logger")
            service_name = self.app_name
            service_name = service_name.replace(" ", "").lower()
            # Import the Microsoft Graph client
            # Build the client (async) similar to Google/Jira patterns
            import asyncio
            import concurrent.futures

            from app.sources.client.microsoft.microsoft import GraphMode, MSGraphClient

            try:
                asyncio.get_running_loop()
                if logger:
                    logger.debug(f"Async context detected for {self.app_name} tool creation - using thread pool (MSGraph)")
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(
                        asyncio.run,
                        MSGraphClient.build_from_services(
                            service_name=service_name,
                            logger=logger,
                            config_service=config_service,
                            mode=GraphMode.APP
                        )
                    )
                    ms_graph_client = future.result()
            except RuntimeError:
                ms_graph_client = asyncio.run(MSGraphClient.build_from_services(
                    service_name=service_name,
                    logger=logger,
                    config_service=config_service,
                    mode=GraphMode.APP
                ))

            # Pass the MSGraphClient wrapper to the action class (data sources expect this wrapper)
            return action_class(ms_graph_client)

        except Exception as e:
            logger = self.state.get("logger")
            if logger:
                logger.error(f"Failed to create Microsoft tool instance for {self.app_name}: {str(e)}")
            # Fallback
            try:
                return action_class()
            except Exception:
                raise

    def _create_notion_tool_instance(self, action_class) -> object:
        """Create Notion tool instance using proper client initialization"""
        try:
            retrieval_service = self.state.get("retrieval_service")
            if not retrieval_service or not hasattr(retrieval_service, 'config_service'):
                raise RuntimeError("ConfigurationService not available through retrieval_service")

            config_service = retrieval_service.config_service
            logger = self.state.get("logger")

            # Import the Notion client
            import asyncio
            import concurrent.futures

            from app.sources.client.notion.notion import NotionClient

            try:
                asyncio.get_running_loop()
                if logger:
                    logger.debug(f"Async context detected for {self.app_name} tool creation - using thread pool (Notion)")
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(
                        asyncio.run,
                        NotionClient.build_from_services(
                            logger=logger,
                            config_service=config_service
                        )
                    )
                    notion_client = future.result()
            except RuntimeError:
                notion_client = asyncio.run(NotionClient.build_from_services(
                    logger=logger,
                    config_service=config_service
                ))

            # Try common patterns: pass wrapper; if fails, pass inner client
            try:
                return action_class(notion_client)
            except Exception:
                try:
                    inner = notion_client.get_client() if hasattr(notion_client, 'get_client') else notion_client
                    return action_class(inner)
                except Exception:
                    # Last resort
                    return action_class()

        except Exception as e:
            logger = self.state.get("logger")
            if logger:
                logger.error(f"Failed to create Notion tool instance for {self.app_name}: {str(e)}")
            return action_class()

    def _create_jira_tool_instance(self, action_class) -> object:
        """Create Jira tool instance using proper client initialization"""
        try:
            # Get ConfigurationService from retrieval_service
            retrieval_service = self.state.get("retrieval_service")
            if not retrieval_service or not hasattr(retrieval_service, 'config_service'):
                raise RuntimeError("ConfigurationService not available through retrieval_service")

            config_service = retrieval_service.config_service
            logger = self.state.get("logger")

            # Import the Jira client
            # Create the Jira client using the proper initialization pattern
            import asyncio
            import concurrent.futures

            from app.sources.client.jira.jira import JiraClient

            # Check if we're already in an async context
            try:
                asyncio.get_running_loop()
                # We're in an async context, use a thread pool to run the async code
                logger.debug(f"Async context detected for {self.app_name} tool creation - using thread pool")

                # Use a thread pool to run the async code
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(
                        asyncio.run,
                        JiraClient.build_from_services(
                            logger=logger,
                            config_service=config_service
                        )
                    )
                    jira_client = future.result()

            except RuntimeError:
                # No running loop, we can use asyncio.run
                jira_client = asyncio.run(JiraClient.build_from_services(
                    logger=logger,
                    config_service=config_service
                ))

            # Create the tool instance with the client
            return action_class(jira_client)

        except Exception as e:
            logger = self.state.get("logger")
            if logger:
                logger.error(f"Failed to create Jira tool instance for {self.app_name}: {str(e)}")
            # Try to create without arguments as last resort
            return action_class()

    def _create_confluence_tool_instance(self, action_class) -> object:
        """Create Confluence tool instance using proper client initialization"""
        try:
            # Get ConfigurationService from retrieval_service
            retrieval_service = self.state.get("retrieval_service")
            if not retrieval_service or not hasattr(retrieval_service, 'config_service'):
                raise RuntimeError("ConfigurationService not available through retrieval_service")

            config_service = retrieval_service.config_service
            logger = self.state.get("logger")

            # Import the Confluence client
            # Create the Confluence client using the proper initialization pattern
            import asyncio
            import concurrent.futures

            from app.sources.client.confluence.confluence import ConfluenceClient

            # Check if we're already in an async context
            try:
                asyncio.get_running_loop()
                # We're in an async context, use a thread pool to run the async code
                logger.debug(f"Async context detected for {self.app_name} tool creation - using thread pool")

                # Use a thread pool to run the async code
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(
                        asyncio.run,
                        ConfluenceClient.build_from_services(
                            logger=logger,
                            config_service=config_service
                        )
                    )
                    confluence_client = future.result()

            except RuntimeError:
                # No running loop, we can use asyncio.run
                confluence_client = asyncio.run(ConfluenceClient.build_from_services(
                    logger=logger,
                    config_service=config_service
                ))

            # Create the tool instance with the client
            return action_class(confluence_client)

        except Exception as e:
            logger = self.state.get("logger")
            if logger:
                logger.error(f"Failed to create Confluence tool instance for {self.app_name}: {str(e)}")
            # Try to create without arguments as last resort
            return action_class()

    def _create_slack_tool_instance(self, action_class) -> object:
        """Create Slack tool instance using proper client initialization"""
        try:
            # Get ConfigurationService from retrieval_service
            retrieval_service = self.state.get("retrieval_service")
            if not retrieval_service or not hasattr(retrieval_service, 'config_service'):
                raise RuntimeError("ConfigurationService not available through retrieval_service")

            config_service = retrieval_service.config_service
            logger = self.state.get("logger")

            # Import the Slack client
            # Create the Slack client using the proper initialization pattern
            import asyncio
            import concurrent.futures

            from app.sources.client.slack.slack import SlackClient

            # Check if we're already in an async context
            try:
                asyncio.get_running_loop()
                # We're in an async context, use a thread pool to run the async code
                logger.debug(f"Async context detected for {self.app_name} tool creation - using thread pool")

                # Use a thread pool to run the async code
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(
                        asyncio.run,
                        SlackClient.build_from_services(
                            logger=logger,
                            config_service=config_service
                        )
                    )
                    slack_client = future.result()

            except RuntimeError:
                # No running loop, we can use asyncio.run
                slack_client = asyncio.run(SlackClient.build_from_services(
                    logger=logger,
                    config_service=config_service
                ))

            # Create the tool instance with the client
            return action_class(slack_client)

        except Exception as e:
            logger = self.state.get("logger")
            if logger:
                logger.error(f"Failed to create Slack tool instance for {self.app_name}: {str(e)}")
            # Try to create without arguments as last resort
            return action_class()

    def _create_github_tool_instance(self, action_class) -> object:
        """Create GitHub tool instance using proper client initialization"""
        try:
            # Get tool configurations from state for GitHub
            tool_configs = self.state.get("tool_configs", {})
            app_config = tool_configs.get(self.app_name, {})

            # Use the old pattern for GitHub since it doesn't have build_from_services yet
            from app.agents.actions.github.config import GithubConfig
            config = GithubConfig(
                base_url=app_config.get("github_url", "https://api.github.com"),
                token=app_config.get("github_token", "")
            )
            return action_class(config)

        except Exception as e:
            logger = self.state.get("logger")
            if logger:
                logger.error(f"Failed to create GitHub tool instance for {self.app_name}: {str(e)}")
            # Try to create without arguments as last resort
            return action_class()

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
        "drive": {
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

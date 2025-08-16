"""
Unified Tool Registry for Multi-Service Agent System
Handles Slack, JIRA, Confluence, Gmail, Google Drive, Google Calendar tools
with proper client initialization and execution patterns.
Fixed for Pydantic v2 compatibility.
"""

import asyncio
import inspect
import json
import logging
import os
from typing import Any, Dict, List, Optional

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field, create_model

from app.agents.actions.confluence.confluence import Confluence
from app.agents.actions.google.enterprise.enterprise import GoogleDriveEnterprise
from app.agents.actions.google.gmail.gmail import Gmail
from app.agents.actions.google.google_calendar.google_calendar import GoogleCalendar
from app.agents.actions.google.google_drive.google_drive import GoogleDrive
from app.agents.actions.jira.jira import Jira
from app.agents.actions.slack.config import SlackTokenConfig

# Import your existing classes
from app.agents.actions.slack.slack import Slack
from app.agents.client.confluence import ConfluenceClient, ConfluenceTokenConfig
from app.agents.client.google import GoogleClient
from app.agents.client.jira import JiraClient, JiraTokenConfig
from app.agents.tools.registry import _global_tools_registry


class ToolExecutionError(Exception):
    """Custom exception for tool execution errors"""
    pass


class AgentToolRegistry:
    """
    Unified registry for all agent tools with proper client management
    and execution patterns for different service types.
    """

    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self.tool_instances: Dict[str, Any] = {}
        self.client_instances: Dict[str, Any] = {}
        self.tools_by_name: Dict[str, BaseTool] = {}

    async def initialize_all_tools(
        self,
        org_id: str,
        user_id: str,
        config_service: Any,
        arango_service: Any
    ) -> List[BaseTool]:
        """
        Initialize all available tools based on environment configuration
        """
        tools = []

        # Initialize Slack tools
        slack_tools = await self._initialize_slack_tools()
        if slack_tools:
            tools.extend(slack_tools)

        # Initialize JIRA tools
        jira_tools = await self._initialize_jira_tools()
        if jira_tools:
            tools.extend(jira_tools)

        # Initialize Confluence tools
        confluence_tools = await self._initialize_confluence_tools()
        if confluence_tools:
            tools.extend(confluence_tools)

        # Initialize Google tools (Gmail, Drive, Calendar)
        google_tools = await self._initialize_google_tools(
            org_id, user_id, config_service, arango_service
        )
        if google_tools:
            tools.extend(google_tools)

        # Cache tools by name for quick lookup
        for tool in tools:
            self.tools_by_name[tool.name] = tool

        self.logger.info(f"Initialized {len(tools)} tools: {[t.name for t in tools]}")
        return tools

    async def _initialize_slack_tools(self) -> Optional[List[BaseTool]]:
        """Initialize Slack tools if token is available"""
        try:
            slack_token = os.getenv("SLACK_BOT_TOKEN")
            if not slack_token:
                self.logger.warning("SLACK_TOKEN not found, skipping Slack tools")
                return None

            # Create Slack client
            slack_config = SlackTokenConfig(token=slack_token)
            slack_instance = Slack(slack_config)
            self.tool_instances["slack"] = slack_instance

            # Get Slack tools from global registry
            slack_tools = []
            for tool_name, tool in _global_tools_registry.get_all_tools().items():
                if tool.app_name == "slack":
                    # Create LangChain tool wrapper
                    langchain_tool = self._create_langchain_tool(
                        tool_name=tool_name,
                        description=tool.description,
                        function=tool.function,
                        parameters=tool.parameters,
                        instance=slack_instance
                    )
                    if langchain_tool:
                        slack_tools.append(langchain_tool)

            self.logger.info(f"Initialized {len(slack_tools)} Slack tools")
            return slack_tools

        except Exception as e:
            self.logger.error(f"Failed to initialize Slack tools: {e}", exc_info=True)
            return None

    async def _initialize_jira_tools(self) -> Optional[List[BaseTool]]:
        """Initialize JIRA tools if token is available"""
        try:
            # Use CONFLUENCE_TOKEN for JIRA as they share the same Atlassian token
            jira_token = os.getenv("CONFLUENCE_TOKEN")
            if not jira_token:
                self.logger.warning("CONFLUENCE_TOKEN not found, skipping JIRA tools")
                return None

            # Create JIRA client
            jira_config = JiraTokenConfig(
                base_url="https://api.atlassian.com/ex/jira",
                token=jira_token
            )
            jira_client = JiraClient.build_with_config(jira_config)
            jira_instance = Jira(jira_client, "https://api.atlassian.com/ex/jira")
            self.tool_instances["jira"] = jira_instance

            # Get JIRA tools from global registry
            jira_tools = []
            for tool_name, tool in _global_tools_registry.get_all_tools().items():
                if tool.app_name == "jira":
                    langchain_tool = self._create_langchain_tool(
                        tool_name=tool_name,
                        description=tool.description,
                        function=tool.function,
                        parameters=tool.parameters,
                        instance=jira_instance
                    )
                    if langchain_tool:
                        jira_tools.append(langchain_tool)

            self.logger.info(f"Initialized {len(jira_tools)} JIRA tools")
            return jira_tools

        except Exception as e:
            self.logger.error(f"Failed to initialize JIRA tools: {e}", exc_info=True)
            return None

    async def _initialize_confluence_tools(self) -> Optional[List[BaseTool]]:
        """Initialize Confluence tools if token is available"""
        try:
            confluence_token = os.getenv("CONFLUENCE_TOKEN")
            if not confluence_token:
                self.logger.warning("CONFLUENCE_TOKEN not found, skipping Confluence tools")
                return None

            # Create Confluence client
            confluence_config = ConfluenceTokenConfig(
                base_url="https://api.atlassian.com/ex/confluence",
                token=confluence_token
            )
            confluence_client = ConfluenceClient.build_with_config(confluence_config)
            confluence_instance = Confluence(confluence_client, "https://api.atlassian.com/ex/confluence")
            self.tool_instances["confluence"] = confluence_instance

            # Get Confluence tools from global registry
            confluence_tools = []
            for tool_name, tool in _global_tools_registry.get_all_tools().items():
                if tool.app_name == "confluence":
                    langchain_tool = self._create_langchain_tool(
                        tool_name=tool_name,
                        description=tool.description,
                        function=tool.function,
                        parameters=tool.parameters,
                        instance=confluence_instance
                    )
                    if langchain_tool:
                        confluence_tools.append(langchain_tool)

            self.logger.info(f"Initialized {len(confluence_tools)} Confluence tools")
            return confluence_tools

        except Exception as e:
            self.logger.error(f"Failed to initialize Confluence tools: {e}", exc_info=True)
            return None

    async def _initialize_google_tools(
        self,
        org_id: str,
        user_id: str,
        config_service: Any,
        arango_service: Any
    ) -> Optional[List[BaseTool]]:
        """Initialize Google tools (Gmail, Drive, Calendar)"""
        try:
            google_tools = []

            # Check if we should use individual or enterprise authentication
            is_individual = os.getenv("GOOGLE_AUTH_TYPE", "individual") == "individual"

            # Initialize Gmail tools
            try:
                gmail_client = await GoogleClient.build_from_services(
                    service_name="gmail",
                    logger=self.logger,
                    config_service=config_service,
                    arango_service=arango_service,
                    org_id=org_id,
                    user_id=user_id,
                    is_individual=is_individual,
                    version="v1"
                )
                gmail_instance = Gmail(gmail_client.get_client())
                self.tool_instances["gmail"] = gmail_instance

                # Get Gmail tools
                for tool_name, tool in _global_tools_registry.get_all_tools().items():
                    if tool.app_name == "gmail":
                        langchain_tool = self._create_langchain_tool(
                            tool_name=tool_name,
                            description=tool.description,
                            function=tool.function,
                            parameters=tool.parameters,
                            instance=gmail_instance
                        )
                        if langchain_tool:
                            google_tools.append(langchain_tool)

            except Exception as e:
                self.logger.warning(f"Failed to initialize Gmail tools: {e}")

            # Initialize Google Drive tools
            try:
                drive_client = await GoogleClient.build_from_services(
                    service_name="drive",
                    logger=self.logger,
                    config_service=config_service,
                    arango_service=arango_service,
                    org_id=org_id,
                    user_id=user_id,
                    is_individual=is_individual,
                    version="v3"
                )
                drive_instance = GoogleDrive(drive_client.get_client())
                self.tool_instances["google_drive"] = drive_instance

                # Get Google Drive tools
                for tool_name, tool in _global_tools_registry.get_all_tools().items():
                    if tool.app_name == "google_drive":
                        langchain_tool = self._create_langchain_tool(
                            tool_name=tool_name,
                            description=tool.description,
                            function=tool.function,
                            parameters=tool.parameters,
                            instance=drive_instance
                        )
                        if langchain_tool:
                            google_tools.append(langchain_tool)

                # Initialize Enterprise Drive tools if not individual
                if not is_individual:
                    enterprise_client = await GoogleClient.build_from_services(
                        service_name="admin",
                        logger=self.logger,
                        config_service=config_service,
                        arango_service=arango_service,
                        org_id=org_id,
                        user_id=user_id,
                        is_individual=False,
                        version="directory_v1"
                    )
                    enterprise_instance = GoogleDriveEnterprise(enterprise_client.get_client())
                    self.tool_instances["google_drive_enterprise"] = enterprise_instance

                    # Get Enterprise Drive tools
                    for tool_name, tool in _global_tools_registry.get_all_tools().items():
                        if tool.app_name == "google_drive_enterprise":
                            langchain_tool = self._create_langchain_tool(
                                tool_name=tool_name,
                                description=tool.description,
                                function=tool.function,
                                parameters=tool.parameters,
                                instance=enterprise_instance
                            )
                            if langchain_tool:
                                google_tools.append(langchain_tool)

            except Exception as e:
                self.logger.warning(f"Failed to initialize Google Drive tools: {e}")

            # Initialize Google Calendar tools
            try:
                calendar_client = await GoogleClient.build_from_services(
                    service_name="calendar",
                    logger=self.logger,
                    config_service=config_service,
                    arango_service=arango_service,
                    org_id=org_id,
                    user_id=user_id,
                    is_individual=is_individual,
                    version="v3"
                )
                calendar_instance = GoogleCalendar(calendar_client.get_client())
                self.tool_instances["google_calendar"] = calendar_instance

                # Get Google Calendar tools
                for tool_name, tool in _global_tools_registry.get_all_tools().items():
                    if tool.app_name == "google_calendar":
                        langchain_tool = self._create_langchain_tool(
                            tool_name=tool_name,
                            description=tool.description,
                            function=tool.function,
                            parameters=tool.parameters,
                            instance=calendar_instance
                        )
                        if langchain_tool:
                            google_tools.append(langchain_tool)

            except Exception as e:
                self.logger.warning(f"Failed to initialize Google Calendar tools: {e}")

            self.logger.info(f"Initialized {len(google_tools)} Google tools")
            return google_tools if google_tools else None

        except Exception as e:
            self.logger.error(f"Failed to initialize Google tools: {e}", exc_info=True)
            return None

    def _create_langchain_tool(
        self,
        tool_name: str,
        description: str,
        function: Any,
        parameters: List[Any],
        instance: Any
    ) -> Optional[BaseTool]:
        """Create a LangChain compatible tool wrapper with proper Pydantic v2 support"""

        try:
            # Create Pydantic model fields using create_model
            model_fields = {}

            for param in parameters:
                # Determine the proper type
                param_type = str  # Default type

                if hasattr(param, 'type') and hasattr(param.type, 'value'):
                    type_value = param.type.value
                    if type_value == "integer":
                        param_type = int
                    elif type_value == "number":
                        param_type = float
                    elif type_value == "boolean":
                        param_type = bool
                    elif type_value in ["array", "list"]:
                        param_type = List[str]
                    elif type_value in ["object", "dict"]:
                        param_type = Dict[str, Any]

                # Handle optional vs required fields
                if param.required:
                    field_info = Field(description=param.description)
                    model_fields[param.name] = (param_type, field_info)
                else:
                    # For optional fields, use Optional and provide default
                    default_value = getattr(param, 'default', None)
                    field_info = Field(default=default_value, description=param.description)
                    model_fields[param.name] = (Optional[param_type], field_info)

            # Use create_model to properly create the Pydantic model
            safe_tool_name = tool_name.replace('.', '_').replace('-', '_')
            InputModel = create_model(
                f"{safe_tool_name}Input",
                **model_fields
            )

            # Capture variables in closure
            captured_tool_name = tool_name
            captured_description = description
            captured_function = function
            captured_instance = instance
            captured_logger = self.logger

            class CustomTool(BaseTool):
                name: str = captured_tool_name
                description: str = captured_description
                args_schema: type[BaseModel] = InputModel

                def _run(self, **kwargs) -> str:
                    return asyncio.run(self._arun(**kwargs))

                async def _arun(self, **kwargs) -> str:
                    try:
                        # Execute the tool function
                        if inspect.iscoroutinefunction(captured_function):
                            result = await captured_function(captured_instance, **kwargs)
                        else:
                            result = captured_function(captured_instance, **kwargs)

                        # Handle different return formats
                        if isinstance(result, tuple):
                            success, data = result
                            if success:
                                return data if isinstance(data, str) else json.dumps(data)
                            else:
                                error_msg = data if isinstance(data, str) else json.dumps(data)
                                raise ToolExecutionError(f"Tool execution failed: {error_msg}")
                        else:
                            return result if isinstance(result, str) else json.dumps(result)

                    except Exception as e:
                        error_msg = f"Error executing {captured_tool_name}: {str(e)}"
                        captured_logger.error(error_msg)
                        raise ToolExecutionError(error_msg)

            return CustomTool()

        except Exception as e:
            self.logger.error(f"Failed to create LangChain tool for {tool_name}: {e}", exc_info=True)
            return None

    async def execute_tool_simple(
        self,
        tool_name: str,
        tool_args: Dict[str, Any]
    ) -> Any:
        """
        Simplified tool execution method for direct use in nodes
        """
        try:
            if tool_name not in self.tools_by_name:
                raise ToolExecutionError(f"Tool '{tool_name}' not found")

            tool = self.tools_by_name[tool_name]

            # Execute the tool
            if inspect.iscoroutinefunction(tool._arun):
                result = await tool._arun(**tool_args)
            else:
                result = tool._run(**tool_args)

            self.logger.debug(f"Tool {tool_name} executed successfully")
            return result

        except Exception as e:
            error_msg = f"Error executing tool {tool_name}: {str(e)}"
            self.logger.error(error_msg)
            raise ToolExecutionError(error_msg)

    def get_agent_tools(self, allowed_tools: Optional[List[str]] = None) -> List[BaseTool]:
        """
        Get tools for LLM binding, optionally filtered by allowed tool names
        """
        if allowed_tools is None:
            return list(self.tools_by_name.values())
        else:
            filtered_tools = []
            for tool_name in allowed_tools:
                if tool_name in self.tools_by_name:
                    filtered_tools.append(self.tools_by_name[tool_name])
                else:
                    self.logger.warning(f"Requested tool '{tool_name}' not available")
            return filtered_tools

    def get_tool_usage_guidance(self) -> str:
        """
        Return guidance text for tool usage
        """
        return """
Tool Usage Guidelines:
- Use tools when you need to interact with external services
- Always check tool responses for success/failure status
- For Slack: Use channel names or IDs as needed
- For JIRA: Use project keys and issue keys correctly
- For Confluence: Use space IDs and page IDs
- For Google services: Ensure proper date/time formats
- Handle errors gracefully and inform the user of any issues
"""

    def get_tool_results_summary(self, all_tool_results: List[Dict[str, Any]]) -> str:
        """
        Generate a summary of tool execution results
        """
        if not all_tool_results:
            return "No tools have been executed yet."

        summary_parts = []
        for i, result in enumerate(all_tool_results[-10:], 1):  # Last 10 results
            tool_name = result.get("tool_name", "unknown")
            status = result.get("status", "unknown")

            if status == "success":
                summary_parts.append(f"{i}. {tool_name}: ✅ Success")
            else:
                summary_parts.append(f"{i}. {tool_name}: ❌ Failed")

        return "\n".join(summary_parts)


# Global registry instance
_agent_tool_registry: Optional[AgentToolRegistry] = None


def get_agent_tool_registry(logger: logging.Logger) -> AgentToolRegistry:
    """Get or create the global agent tool registry"""
    global _agent_tool_registry
    if _agent_tool_registry is None:
        _agent_tool_registry = AgentToolRegistry(logger)
    return _agent_tool_registry


# Convenience functions for use in nodes.py
def get_agent_tools(state: Dict[str, Any]) -> List[BaseTool]:
    """Get available tools for the agent"""
    logger = state.get("logger")
    if not logger:
        return []

    registry = get_agent_tool_registry(logger)
    allowed_tools = state.get("tools")  # None means all tools
    return registry.get_agent_tools(allowed_tools)


async def execute_tool_simple(tool: BaseTool, tool_args: Dict[str, Any], logger: logging.Logger) -> Any:
    """Execute a tool with the given arguments"""
    try:
        if inspect.iscoroutinefunction(tool._arun):
            return await tool._arun(**tool_args)
        else:
            return tool._run(**tool_args)
    except Exception as e:
        logger.error(f"Tool execution failed: {e}")
        raise


def get_tool_usage_guidance() -> str:
    """Get tool usage guidance"""
    return """
Tool Usage Guidelines:
- Use tools when you need to interact with external services
- Always check tool responses for success/failure status
- For Slack: Use channel names or IDs as needed
- For JIRA: Use project keys and issue keys correctly
- For Confluence: Use space IDs and page IDs
- For Google services: Ensure proper date/time formats
- Handle errors gracefully and inform the user of any issues
"""


def get_tool_results_summary(state: Dict[str, Any]) -> str:
    """Get a summary of tool execution results"""
    all_tool_results = state.get("all_tool_results", [])
    if not all_tool_results:
        return "No tools have been executed yet."

    summary_parts = []
    for i, result in enumerate(all_tool_results[-10:], 1):  # Last 10 results
        tool_name = result.get("tool_name", "unknown")
        status = result.get("status", "unknown")

        if status == "success":
            summary_parts.append(f"{i}. {tool_name}: ✅ Success")
        else:
            summary_parts.append(f"{i}. {tool_name}: ❌ Failed")

    return "\n".join(summary_parts)

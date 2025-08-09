"""
Tool Registry for LangGraph Agent System

This module provides tool registration and execution capabilities for agents.
"""

import logging
import asyncio
from typing import Dict, List, Callable, Optional, Any, Union
from langchain_core.tools import BaseTool, tool
from langgraph.prebuilt import ToolExecutor

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Registry for managing and executing tools"""
    
    def __init__(self):
        self.tools: Dict[str, BaseTool] = {}
        self.async_tools: Dict[str, Callable] = {}
        self.tool_executor = None
    
    def register_tool(self, tool_name: str, tool_function: Callable, description: str = ""):
        """Register a tool function"""
        try:
            # Check if it's an async function
            if asyncio.iscoroutinefunction(tool_function):
                # Store async function separately
                self.async_tools[tool_name] = tool_function
                logger.info(f"Registered async tool: {tool_name}")
            else:
                # Create LangChain tool for sync functions
                langchain_tool = tool(tool_function)
                langchain_tool.name = tool_name
                langchain_tool.description = description
                
                self.tools[tool_name] = langchain_tool
                logger.info(f"Registered sync tool: {tool_name}")
            
        except Exception as e:
            logger.error(f"Failed to register tool {tool_name}: {str(e)}")
    
    def get_tools(self, tool_names: List[str]) -> List[BaseTool]:
        """Get tools by names"""
        return [self.tools.get(name) for name in tool_names if name in self.tools]
    
    def get_all_tools(self) -> List[BaseTool]:
        """Get all registered tools"""
        return list(self.tools.values())
    
    def get_async_tool(self, tool_name: str) -> Optional[Callable]:
        """Get async tool by name"""
        return self.async_tools.get(tool_name)
    
    def get_all_async_tools(self) -> Dict[str, Callable]:
        """Get all async tools"""
        return self.async_tools.copy()
    
    def create_tool_executor(self):
        """Create tool executor for LangGraph"""
        if self.tools:
            self.tool_executor = ToolExecutor(self.tools)
            return self.tool_executor
        return None
    
    def get_tool(self, tool_name: str) -> Optional[BaseTool]:
        """Get a specific tool by name"""
        return self.tools.get(tool_name)
    
    def list_tools(self) -> List[str]:
        """List all registered tool names"""
        return list(self.tools.keys()) + list(self.async_tools.keys())
    
    def remove_tool(self, tool_name: str) -> bool:
        """Remove a tool from registry"""
        removed = False
        if tool_name in self.tools:
            del self.tools[tool_name]
            removed = True
        if tool_name in self.async_tools:
            del self.async_tools[tool_name]
            removed = True
        
        if removed:
            logger.info(f"Removed tool: {tool_name}")
        return removed
    
    def clear_tools(self):
        """Clear all tools"""
        self.tools.clear()
        self.async_tools.clear()
        self.tool_executor = None
        logger.info("Cleared all tools")


# Built-in tools that can be registered
def web_search_tool(query: str) -> str:
    """Search the web for information"""
    # This would integrate with a real web search API
    return f"Web search results for: {query} (placeholder implementation)"


def calculator_tool(expression: str) -> str:
    """Evaluate mathematical expressions"""
    try:
        # Safe evaluation of mathematical expressions
        allowed_names = {
            k: v for k, v in __builtins__.items() 
            if k in ['abs', 'round', 'min', 'max', 'sum']
        }
        allowed_names.update({
            'abs': abs, 'round': round, 'min': min, 'max': max, 'sum': sum
        })
        
        result = eval(expression, {"__builtins__": {}}, allowed_names)
        return f"Result: {result}"
    except Exception as e:
        return f"Error evaluating expression: {str(e)}"


def file_reader_tool(file_path: str) -> str:
    """Read content from a file"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        return f"File content: {content[:1000]}..." if len(content) > 1000 else f"File content: {content}"
    except Exception as e:
        return f"Error reading file: {str(e)}"


def weather_tool(location: str) -> str:
    """Get weather information for a location"""
    # This would integrate with a weather API
    return f"Weather information for {location} (placeholder implementation)"


def email_tool(subject: str, body: str, recipient: str) -> str:
    """Send an email"""
    # This would integrate with an email service
    return f"Email sent to {recipient} with subject: {subject} (placeholder implementation)"


def database_query_tool(query: str) -> str:
    """Execute a database query"""
    # This would integrate with your database
    return f"Database query executed: {query} (placeholder implementation)"


def api_call_tool(url: str, method: str = "GET", data: str = "") -> str:
    """Make an API call"""
    # This would integrate with HTTP client
    return f"API call to {url} with method {method} (placeholder implementation)"


# Initialize default tools
def initialize_default_tools(registry: ToolRegistry):
    """Initialize registry with default tools"""
    default_tools = [
        (web_search_tool, "web_search", "Search the web for information"),
        (calculator_tool, "calculator", "Evaluate mathematical expressions"),
        (file_reader_tool, "file_reader", "Read content from files"),
        (weather_tool, "weather", "Get weather information"),
        (email_tool, "email", "Send emails"),
        (database_query_tool, "database_query", "Execute database queries"),
        (api_call_tool, "api_call", "Make API calls")
    ]
    
    for tool_func, name, description in default_tools:
        registry.register_tool(name, tool_func, description)
    
    logger.info("Initialized default tools") 
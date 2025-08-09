"""
Agent System Package

This package provides a comprehensive agent system for creating, configuring,
and executing AI agents for workflow automation and task completion.

Main Components:
- AgentService: Manages agent templates, instances, and configurations
- WorkflowOrchestrator: Handles workflow execution and task management
- AgentExecutionEngine: Core execution engine for agent reasoning and actions
- LangGraphAgentExecutor: LangGraph-based agent execution with real tool/model integration
- Data Models: Comprehensive models for agents, tools, workflows, etc.
"""

from .core.execution_engine import (
    AgentExecutionEngine,
    AgentExecutionContext,
    ToolExecutor,
    ModelExecutor,
    ActionExecutor,
    MemoryManager
)

from .core.langgraph_executor import (
    LangGraphAgentExecutor,
    AgentExecutionState,
    AgentState
)

from .core.tool_registry import (
    ToolRegistry,
    initialize_default_tools,
    web_search_tool,
    calculator_tool,
    file_reader_tool,
    weather_tool,
    email_tool,
    database_query_tool,
    api_call_tool
)

from .core.model_manager import (
    ModelManager,
    initialize_default_models,
    create_default_model_configs
)

from app.services.agent_service import AgentService
from app.services.workflow_orchestrator import WorkflowOrchestrator
from app.models.agents import (
    AgentTemplate,
    Agent,
    Tool,
    AIModel,
    AppAction,
    Task,
    Workflow,
    MemoryType,
    UserRole,
    TaskPriority,
    ModelProvider,
    ModelType,
    ToolConfig,
    ModelConfig,
    ActionConfig,
    MemoryConfig,
    ApprovalConfig
)

__version__ = "1.0.0"
__author__ = "Agent System Team"

__all__ = [
    # Core execution components
    "AgentExecutionEngine",
    "AgentExecutionContext",
    "ToolExecutor",
    "ModelExecutor",
    "ActionExecutor",
    "MemoryManager",
    
    # LangGraph components
    "LangGraphAgentExecutor",
    "AgentExecutionState",
    "AgentState",
    
    # Tool management
    "ToolRegistry",
    "initialize_default_tools",
    "web_search_tool",
    "calculator_tool",
    "file_reader_tool",
    "weather_tool",
    "email_tool",
    "database_query_tool",
    "api_call_tool",
    
    # Model management
    "ModelManager",
    "initialize_default_models",
    "create_default_model_configs",
    
    # Services
    "AgentService",
    "WorkflowOrchestrator",
    
    # Data models
    "AgentTemplate",
    "Agent",
    "Tool",
    "AIModel",
    "AppAction",
    "Task",
    "Workflow",
    
    # Enums
    "MemoryType",
    "UserRole",
    "TaskPriority",
    "ModelProvider",
    "ModelType",
    
    # Configuration models
    "ToolConfig",
    "ModelConfig",
    "ActionConfig",
    "MemoryConfig",
    "ApprovalConfig",
] 
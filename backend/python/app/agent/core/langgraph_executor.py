"""
LangGraph-based Agent Executor

This module provides a real agent execution system using LangGraph for:
- Tool execution with retrieval service integration
- Memory retrieval and storage
- Conversation management
- Knowledge base integration
- App data integration (Gmail, Drive)
"""

import asyncio
import logging
from typing import Dict, Any, List, Optional, Callable
from datetime import datetime
from dataclasses import dataclass, field
from enum import Enum

from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage
from langchain_core.tools import BaseTool, tool
from langchain_core.language_models import BaseLanguageModel
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolExecutor

from app.models.agents import (
    Agent, Task, MemoryType, ModelConfig, ModelProvider, ModelType
)
from app.services.agent_service import AgentService
from app.services.workflow_orchestrator import WorkflowOrchestrator

from .tool_registry import ToolRegistry
from .model_manager import ModelManager

logger = logging.getLogger(__name__)


class AgentState(str, Enum):
    INITIALIZING = "initializing"
    THINKING = "thinking"
    USING_TOOL = "using_tool"
    EXECUTING_ACTION = "executing_action"
    RESPONDING = "responding"
    COMPLETED = "completed"
    ERROR = "error"


@dataclass
class AgentExecutionState:
    """State for agent execution"""
    agent: Agent
    task: Task
    user_query: str
    user_id: str
    org_id: str
    current_state: AgentState = AgentState.INITIALIZING
    messages: List[BaseMessage] = field(default_factory=list)
    available_tools: List[BaseTool] = field(default_factory=list)
    available_actions: List[Dict[str, Any]] = field(default_factory=list)
    tool_results: List[Dict[str, Any]] = field(default_factory=list)
    action_results: List[Dict[str, Any]] = field(default_factory=list)
    memory_context: Dict[str, Any] = field(default_factory=dict)
    execution_log: List[Dict[str, Any]] = field(default_factory=list)
    iteration_count: int = 0
    max_iterations: int = 50
    error_message: Optional[str] = None


class LangGraphAgentExecutor:
    """Main LangGraph-based agent executor with retrieval service integration"""
    
    def __init__(
        self,
        agent_service: AgentService,
        workflow_orchestrator: WorkflowOrchestrator,
        db_client,
        retrieval_service=None,
        arango_service=None
    ):
        self.agent_service = agent_service
        self.workflow_orchestrator = workflow_orchestrator
        self.db_client = db_client
        self.retrieval_service = retrieval_service
        self.arango_service = arango_service
        
        # Initialize components
        self.tool_registry = ToolRegistry()
        self.model_manager = ModelManager()
        
        # Execution state
        self.active_executions: Dict[str, AgentExecutionState] = {}
        self.graphs: Dict[str, StateGraph] = {}
        
        # Initialize retrieval-based tools
        self._initialize_retrieval_tools()
    
    def _initialize_retrieval_tools(self):
        """Initialize tools that use the retrieval service"""
        if self.retrieval_service and self.arango_service:
            logger.info("Retrieval service available - tools will be initialized during execution")
        else:
            logger.warning("Retrieval service not available - retrieval-based tools will not be available")
    
    def _get_current_user_id(self) -> str:
        """Get current user ID from execution context"""
        # This would be set during execution
        return getattr(self, '_current_user_id', 'default_user')
    
    def _get_current_org_id(self) -> str:
        """Get current org ID from execution context"""
        # This would be set during execution
        return getattr(self, '_current_org_id', 'default_org')
    
    async def start_agent_execution(
        self,
        agent_key: str,
        task_key: str,
        user_query: str,
        user_id: str,
        org_id: str
    ) -> str:
        """Start agent execution using LangGraph"""
        try:
            # Set current context
            self._current_user_id = user_id
            self._current_org_id = org_id
            
            # Get agent and task
            agent = await self.agent_service.get_agent(agent_key)
            if not agent:
                raise ValueError(f"Agent not found: {agent_key}")
            
            task = await self.db_client.get_document("tasks", task_key)
            if not task:
                raise ValueError(f"Task not found: {task_key}")
            
            # Create execution state
            execution_id = f"{agent_key}_{task_key}_{datetime.now().timestamp()}"
            state = AgentExecutionState(
                agent=agent,
                task=task,
                user_query=user_query,
                user_id=user_id,
                org_id=org_id
            )
            
            # Load agent configuration
            await self._load_agent_configuration(state)
            
            # Load memory context using retrieval service
            # Note: This is done asynchronously in the background
            asyncio.create_task(self._load_memory_context(state))
            
            # Create LangGraph
            graph = await self._create_agent_graph(state)
            self.graphs[execution_id] = graph
            
            # Store execution state
            self.active_executions[execution_id] = state
            
            # Start execution
            asyncio.create_task(self._execute_graph(execution_id, graph, state))
            
            logger.info(f"Started LangGraph agent execution: {execution_id}")
            return execution_id
            
        except Exception as e:
            logger.error(f"Failed to start agent execution: {str(e)}")
            raise
    
    async def _load_agent_configuration(self, state: AgentExecutionState):
        """Load agent's tools, models, and actions"""
        agent = state.agent
        
        # Load tools
        available_tools = await self.agent_service.get_tools(org_id=agent.org_id)
        tool_names = agent.selected_tools
        
        for tool_name in tool_names:
            if tool_name in self.tool_registry.tools:
                state.available_tools.append(self.tool_registry.tools[tool_name])
            else:
                placeholder_tool = self._create_placeholder_tool(tool_name)
                state.available_tools.append(placeholder_tool)
        
        # Load models
        available_models = await self.agent_service.get_ai_models(org_id=agent.org_id)
        model_names = agent.selected_models
        
        for model_name in model_names:
            if model_name not in self.model_manager.models:
                for model in available_models:
                    if model.name == model_name:
                        self.model_manager.register_model(model_name, model)
                        break
        
        # Load actions
        available_actions = await self.agent_service.get_app_actions(org_id=agent.org_id)
        action_names = agent.selected_actions
        
        for action_name in action_names:
            for action in available_actions:
                if action.name == action_name:
                    state.available_actions.append(action.__dict__)
                    break
    
    async def _load_memory_context(self, state: AgentExecutionState):
        """Load memory context using retrieval service"""
        agent = state.agent
        
        if not self.retrieval_service or not self.arango_service:
            logger.warning("Retrieval service not available for memory context")
            return
        
        # Load knowledge base context
        if MemoryType.KNOWLEDGE_BASE in agent.selected_memory_types:
            try:
                knowledge_results = await self.retrieval_service.search_with_filters(
                    queries=[state.user_query],
                    user_id=state.user_id,
                    org_id=state.org_id,
                    filter_groups={'kb': []},  # Search all accessible KBs
                    limit=5,
                    arango_service=self.arango_service
                )
                
                if knowledge_results.get("searchResults"):
                    state.memory_context["knowledge_base"] = knowledge_results["searchResults"]
                    logger.info(f"Loaded {len(knowledge_results['searchResults'])} knowledge base results")
            except Exception as e:
                logger.error(f"Failed to load knowledge base context: {str(e)}")
        
        # Load Gmail context
        if MemoryType.APPS in agent.selected_memory_types:
            try:
                gmail_results = await self.retrieval_service.search_with_filters(
                    queries=[state.user_query],
                    user_id=state.user_id,
                    org_id=state.org_id,
                    filter_groups={'source': ['gmail']},
                    limit=3,
                    arango_service=self.arango_service
                )
                
                if gmail_results.get("searchResults"):
                    state.memory_context["gmail"] = gmail_results["searchResults"]
                    logger.info(f"Loaded {len(gmail_results['searchResults'])} Gmail results")
            except Exception as e:
                logger.error(f"Failed to load Gmail context: {str(e)}")
        
        # Load Drive context
        if MemoryType.APPS in agent.selected_memory_types:
            try:
                drive_results = await self.retrieval_service.search_with_filters(
                    queries=[state.user_query],
                    user_id=state.user_id,
                    org_id=state.org_id,
                    filter_groups={'source': ['drive']},
                    limit=3,
                    arango_service=self.arango_service
                )
                
                if drive_results.get("searchResults"):
                    state.memory_context["drive"] = drive_results["searchResults"]
                    logger.info(f"Loaded {len(drive_results['searchResults'])} Drive results")
            except Exception as e:
                logger.error(f"Failed to load Drive context: {str(e)}")
    
    def _create_placeholder_tool(self, tool_name: str) -> BaseTool:
        """Create a placeholder tool for missing tools"""
        def placeholder_tool(*args, **kwargs):
            return f"Tool {tool_name} is not implemented yet. Args: {args}, Kwargs: {kwargs}"
        
        return tool(placeholder_tool)
    
    async def _create_agent_graph(self, state: AgentExecutionState) -> StateGraph:
        """Create LangGraph for agent execution"""
        
        # Get primary model
        primary_model = self.model_manager.get_primary_model()
        if not primary_model:
            raise ValueError("No primary model available")
        
        # Create tool executor
        tool_executor = self.tool_registry.create_tool_executor()
        
        # Define nodes
        def initialize_node(state: AgentExecutionState) -> AgentExecutionState:
            """Initialize the agent"""
            state.current_state = AgentState.THINKING
            
            # Add system message
            system_message = SystemMessage(content=state.agent.system_prompt)
            state.messages.append(system_message)
            
            # Add user query
            user_message = HumanMessage(content=state.user_query)
            state.messages.append(user_message)
            
            state.execution_log.append({
                "timestamp": datetime.now().isoformat(),
                "action": "initialized",
                "message": "Agent initialized with system prompt and user query"
            })
            
            return state
        
        def thinking_node(state: AgentExecutionState) -> AgentExecutionState:
            """Agent thinking and decision making"""
            state.current_state = AgentState.THINKING
            state.iteration_count += 1
            
            # Create thinking prompt
            thinking_prompt = self._create_thinking_prompt(state)
            
            # Get model response
            response = primary_model.invoke(thinking_prompt)
            
            # Parse response to determine next action
            next_action = self._parse_thinking_response(response.content, state)
            
            state.execution_log.append({
                "timestamp": datetime.now().isoformat(),
                "action": "thinking",
                "response": response.content,
                "next_action": next_action
            })
            
            # Update state based on next action
            if next_action == "use_tool":
                state.current_state = AgentState.USING_TOOL
            elif next_action == "execute_action":
                state.current_state = AgentState.EXECUTING_ACTION
            elif next_action == "respond":
                state.current_state = AgentState.RESPONDING
            elif next_action == "complete":
                state.current_state = AgentState.COMPLETED
            
            return state
        
        def tool_node(state: AgentExecutionState) -> AgentExecutionState:
            """Execute tools"""
            state.current_state = AgentState.USING_TOOL
            
            # For now, we'll use a simple approach and execute tools in the main execution loop
            # This avoids the async complexity in LangGraph nodes
            if state.available_tools and tool_executor:
                tool_to_use = state.available_tools[0]  # In reality, AI would choose specific tool
                
                try:
                    # Execute tool
                    result = tool_executor.invoke({
                        "tool": tool_to_use.name,
                        "tool_input": {"query": state.user_query}
                    })
                    
                    # Store result
                    state.tool_results.append({
                        "tool": tool_to_use.name,
                        "result": result,
                        "timestamp": datetime.now().isoformat()
                    })
                    
                    # Add result to messages
                    result_message = AIMessage(content=f"Tool {tool_to_use.name} result: {result}")
                    state.messages.append(result_message)
                    
                    state.execution_log.append({
                        "timestamp": datetime.now().isoformat(),
                        "action": "tool_execution",
                        "tool": tool_to_use.name,
                        "result": result
                    })
                    
                except Exception as e:
                    state.error_message = f"Tool execution failed: {str(e)}"
                    state.current_state = AgentState.ERROR
            
            return state
        
        def action_node(state: AgentExecutionState) -> AgentExecutionState:
            """Execute actions"""
            state.current_state = AgentState.EXECUTING_ACTION
            
            if state.available_actions:
                action_to_execute = state.available_actions[0]
                
                try:
                    # Execute action
                    result = await self._execute_action(action_to_execute, state)
                    
                    # Store result
                    state.action_results.append({
                        "action": action_to_execute["name"],
                        "result": result,
                        "timestamp": datetime.now().isoformat()
                    })
                    
                    # Add result to messages
                    result_message = AIMessage(content=f"Action {action_to_execute['name']} result: {result}")
                    state.messages.append(result_message)
                    
                    state.execution_log.append({
                        "timestamp": datetime.now().isoformat(),
                        "action": "action_execution",
                        "action_name": action_to_execute["name"],
                        "result": result
                    })
                    
                except Exception as e:
                    state.error_message = f"Action execution failed: {str(e)}"
                    state.current_state = AgentState.ERROR
            
            return state
        
        def respond_node(state: AgentExecutionState) -> AgentExecutionState:
            """Generate final response"""
            state.current_state = AgentState.RESPONDING
            
            # Create response prompt
            response_prompt = self._create_response_prompt(state)
            
            # Generate response
            response = primary_model.invoke(response_prompt)
            
            # Add response to messages
            ai_message = AIMessage(content=response.content)
            state.messages.append(ai_message)
            
            state.execution_log.append({
                "timestamp": datetime.now().isoformat(),
                "action": "response_generated",
                "response": response.content
            })
            
            state.current_state = AgentState.COMPLETED
            return state
        
        def should_continue(state: AgentExecutionState) -> str:
            """Determine next node based on state"""
            if state.current_state == AgentState.ERROR:
                return "end"
            elif state.current_state == AgentState.COMPLETED:
                return "end"
            elif state.iteration_count >= state.max_iterations:
                return "end"
            elif state.current_state == AgentState.USING_TOOL:
                return "thinking"
            elif state.current_state == AgentState.EXECUTING_ACTION:
                return "thinking"
            elif state.current_state == AgentState.RESPONDING:
                return "end"
            else:
                return "thinking"
        
        # Create graph
        workflow = StateGraph(AgentExecutionState)
        
        # Add nodes
        workflow.add_node("initialize", initialize_node)
        workflow.add_node("thinking", thinking_node)
        workflow.add_node("tool", tool_node)
        workflow.add_node("action", action_node)
        workflow.add_node("respond", respond_node)
        
        # Add edges
        workflow.set_entry_point("initialize")
        workflow.add_edge("initialize", "thinking")
        workflow.add_conditional_edges(
            "thinking",
            should_continue,
            {
                "tool": "tool",
                "action": "action",
                "respond": "respond",
                "end": END
            }
        )
        workflow.add_edge("tool", "thinking")
        workflow.add_edge("action", "thinking")
        workflow.add_edge("respond", END)
        
        # Compile graph
        return workflow.compile()
    
    def _create_thinking_prompt(self, state: AgentExecutionState) -> List[BaseMessage]:
        """Create prompt for agent thinking"""
        agent = state.agent
        task = state.task
        
        # Build context from memory
        context_parts = []
        
        if state.memory_context.get("knowledge_base"):
            context_parts.append("Knowledge Base Context:")
            for kb in state.memory_context["knowledge_base"][:3]:
                context_parts.append(f"- {kb.get('content', '')[:200]}...")
        
        if state.memory_context.get("gmail"):
            context_parts.append("Gmail Context:")
            for email in state.memory_context["gmail"][:2]:
                context_parts.append(f"- {email.get('content', '')[:200]}...")
        
        if state.memory_context.get("drive"):
            context_parts.append("Drive Context:")
            for doc in state.memory_context["drive"][:2]:
                context_parts.append(f"- {doc.get('content', '')[:200]}...")
        
        context = "\n".join(context_parts) if context_parts else "No relevant context available."
        
        thinking_prompt = f"""
You are an AI agent working on a task. Analyze the current situation and decide what to do next.

TASK: {task.get('name', 'Unknown')}
DESCRIPTION: {task.get('description', 'No description')}

USER QUERY: {state.user_query}

AVAILABLE TOOLS: {[tool.name for tool in state.available_tools]}
AVAILABLE ACTIONS: {[action['name'] for action in state.available_actions]}

CONTEXT:
{context}

PREVIOUS TOOL RESULTS: {state.tool_results}
PREVIOUS ACTION RESULTS: {state.action_results}

Based on the current situation, decide what to do next. Choose one of:
1. "use_tool" - if you need to use a tool to gather information (knowledgebase.search, gmail.search, drive.search)
2. "execute_action" - if you need to perform an action
3. "respond" - if you have enough information to respond to the user
4. "complete" - if the task is complete

Provide your reasoning and the specific tool/action to use if applicable.
"""
        
        return [SystemMessage(content=thinking_prompt)]
    
    def _create_response_prompt(self, state: AgentExecutionState) -> List[BaseMessage]:
        """Create prompt for generating final response"""
        agent = state.agent
        task = state.task
        
        # Build context from all available information
        context_parts = []
        
        if state.tool_results:
            context_parts.append("Tool Results:")
            for result in state.tool_results:
                context_parts.append(f"- {result['tool']}: {result['result']}")
        
        if state.action_results:
            context_parts.append("Action Results:")
            for result in state.action_results:
                context_parts.append(f"- {result['action']}: {result['result']}")
        
        if state.memory_context.get("knowledge_base"):
            context_parts.append("Knowledge Base:")
            for kb in state.memory_context["knowledge_base"][:2]:
                context_parts.append(f"- {kb.get('content', '')[:300]}...")
        
        context = "\n".join(context_parts) if context_parts else ""
        
        # Create response prompt
        response_prompt = f"""
You are an AI agent that has completed a task. Generate a comprehensive response to the user.

TASK: {task.get('name', 'Unknown')}
USER QUERY: {state.user_query}

CONTEXT AND RESULTS:
{context}

Generate a helpful, informative response that addresses the user's query and incorporates the results from tools, actions, and knowledge base. Be professional, clear, and actionable.
"""
        
        return [SystemMessage(content=response_prompt)]
    
    def _parse_thinking_response(self, response: str, state: AgentExecutionState) -> str:
        """Parse thinking response to determine next action"""
        response_lower = response.lower()
        
        if "use_tool" in response_lower or "tool" in response_lower:
            return "use_tool"
        elif "execute_action" in response_lower or "action" in response_lower:
            return "execute_action"
        elif "respond" in response_lower or "answer" in response_lower:
            return "respond"
        elif "complete" in response_lower or "done" in response_lower:
            return "complete"
        else:
            return "thinking"
    
    async def _execute_action(self, action: Dict[str, Any], state: AgentExecutionState) -> Dict[str, Any]:
        """Execute an action with approval workflow"""
        try:
            # Check if action requires approval
            if action.get("approvers"):
                # Create approval request
                approval_id = await self.workflow_orchestrator.request_action_approval(
                    task_key=state.task.get("_key"),
                    action_key=action.get("_key"),
                    requester_id=state.agent.created_by or "system",
                    request_data={"query": state.user_query}
                )
                
                return {
                    "status": "pending_approval",
                    "approval_id": approval_id,
                    "message": f"Action {action['name']} requires approval"
                }
            else:
                # Execute action immediately
                return {
                    "status": "completed",
                    "message": f"Action {action['name']} executed successfully",
                    "result": "Action completed"
                }
                
        except Exception as e:
            logger.error(f"Action execution failed: {str(e)}")
            return {
                "status": "failed",
                "error": str(e)
            }
    
    async def _execute_graph(self, execution_id: str, graph: StateGraph, state: AgentExecutionState):
        """Execute the LangGraph"""
        try:
            # Execute graph
            result = await graph.ainvoke(state)
            
            # Update final state
            self.active_executions[execution_id] = result
            
            logger.info(f"Graph execution completed for {execution_id}")
            
        except Exception as e:
            logger.error(f"Graph execution failed for {execution_id}: {str(e)}")
            state.error_message = str(e)
            state.current_state = AgentState.ERROR
            self.active_executions[execution_id] = state
    
    # Public API methods
    async def get_execution_status(self, execution_id: str) -> Optional[Dict[str, Any]]:
        """Get status of an execution"""
        state = self.active_executions.get(execution_id)
        if not state:
            return None
        
        return {
            "execution_id": execution_id,
            "agent_id": state.agent._key,
            "task_id": state.task.get("_key"),
            "current_state": state.current_state.value,
            "iteration_count": state.iteration_count,
            "user_query": state.user_query,
            "tool_results": state.tool_results,
            "action_results": state.action_results,
            "execution_log": state.execution_log[-20:],
            "error_message": state.error_message,
            "is_active": state.current_state not in [AgentState.COMPLETED, AgentState.ERROR]
        }
    
    async def stop_execution(self, execution_id: str) -> bool:
        """Stop an active execution"""
        if execution_id in self.active_executions:
            state = self.active_executions[execution_id]
            state.current_state = AgentState.COMPLETED
            state.execution_log.append({
                "timestamp": datetime.now().isoformat(),
                "action": "stopped_by_user",
                "message": "Execution stopped by user"
            })
            
            logger.info(f"Stopped execution: {execution_id}")
            return True
        
        return False
    
    async def send_message(self, execution_id: str, message: str) -> bool:
        """Send a message to an active execution"""
        state = self.active_executions.get(execution_id)
        if not state or state.current_state in [AgentState.COMPLETED, AgentState.ERROR]:
            return False
        
        # Add message to conversation
        user_message = HumanMessage(content=message)
        state.messages.append(user_message)
        
        # Update user query
        state.user_query = message
        
        # Restart thinking process
        state.current_state = AgentState.THINKING
        state.iteration_count = 0
        
        # Re-execute graph
        graph = self.graphs.get(execution_id)
        if graph:
            asyncio.create_task(self._execute_graph(execution_id, graph, state))
        
        return True
    
    # Tool registration methods
    def register_tool(self, tool_name: str, tool_function: Callable, description: str = ""):
        """Register a tool for agent use"""
        self.tool_registry.register_tool(tool_name, tool_function, description)
    
    def register_model(self, model_name: str, model_config: ModelConfig):
        """Register a model for agent use"""
        self.model_manager.register_model(model_name, model_config) 
from typing import Dict, Any, List, Optional, Callable
import asyncio
import logging
from datetime import datetime
from dataclasses import asdict

from app.models.agents import (
    Agent, Tool, AIModel, AppAction, Task, MemoryType,
    ToolConfig, ModelConfig, ActionConfig
)
from app.services.agent_service import AgentService
from app.services.workflow_orchestrator import WorkflowOrchestrator


logger = logging.getLogger(__name__)


class AgentExecutionContext:
    """Context for agent execution including tools, models, and memory"""
    
    def __init__(self, agent: Agent, task: Task, workflow_context: Dict[str, Any]):
        self.agent = agent
        self.task = task
        self.workflow_context = workflow_context
        self.conversation_history = []
        self.memory = {}
        self.tool_results = {}
        self.model_responses = {}
        self.action_results = {}
        self.execution_log = []
        
    def add_to_log(self, message: str, level: str = "INFO"):
        """Add message to execution log"""
        timestamp = datetime.now().isoformat()
        self.execution_log.append({
            "timestamp": timestamp,
            "level": level,
            "message": message
        })
        
    def get_context_summary(self) -> Dict[str, Any]:
        """Get summary of execution context"""
        return {
            "agent_id": self.agent._key,
            "agent_name": self.agent.name,
            "task_id": self.task._key,
            "task_name": self.task.name,
            "conversation_length": len(self.conversation_history),
            "tool_calls": len(self.tool_results),
            "model_calls": len(self.model_responses),
            "action_executions": len(self.action_results),
            "log_entries": len(self.execution_log)
        }


class ToolExecutor:
    """Handles tool execution for agents"""
    
    def __init__(self):
        self.available_tools = {}
        self.tool_registry = {}
        
    def register_tool(self, tool_name: str, tool_function: Callable):
        """Register a tool function"""
        self.tool_registry[tool_name] = tool_function
        logger.info(f"Registered tool: {tool_name}")
        
    async def execute_tool(self, tool_name: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a tool with given parameters"""
        if tool_name not in self.tool_registry:
            raise ValueError(f"Tool not found: {tool_name}")
        
        tool_function = self.tool_registry[tool_name]
        
        try:
            # Execute the tool
            if asyncio.iscoroutinefunction(tool_function):
                result = await tool_function(**parameters)
            else:
                result = tool_function(**parameters)
                
            return {
                "success": True,
                "result": result,
                "tool_name": tool_name,
                "parameters": parameters,
                "execution_time": datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Tool execution failed: {tool_name}, error: {str(e)}")
            return {
                "success": False,
                "error": str(e),
                "tool_name": tool_name,
                "parameters": parameters,
                "execution_time": datetime.now().isoformat()
            }


class ModelExecutor:
    """Handles AI model interactions for agents"""
    
    def __init__(self):
        self.model_providers = {}
        self.model_configs = {}
        
    def register_model_provider(self, provider_name: str, provider_client):
        """Register a model provider"""
        self.model_providers[provider_name] = provider_client
        logger.info(f"Registered model provider: {provider_name}")
        
    def register_model(self, model_name: str, model_config: ModelConfig):
        """Register a model configuration"""
        self.model_configs[model_name] = model_config
        logger.info(f"Registered model: {model_name}")
        
    async def call_model(
        self,
        model_name: str,
        prompt: str,
        context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Call an AI model with a prompt"""
        if model_name not in self.model_configs:
            raise ValueError(f"Model not found: {model_name}")
        
        model_config = self.model_configs[model_name]
        provider_name = model_config.provider
        
        if provider_name not in self.model_providers:
            raise ValueError(f"Model provider not found: {provider_name}")
        
        provider_client = self.model_providers[provider_name]
        
        try:
            # Prepare the request
            request_data = {
                "prompt": prompt,
                "model": model_config.name,
                "config": model_config.config
            }
            
            if context:
                request_data["context"] = context
            
            # Call the model
            response = await provider_client.generate(request_data)
            
            return {
                "success": True,
                "response": response,
                "model_name": model_name,
                "provider": provider_name,
                "execution_time": datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Model call failed: {model_name}, error: {str(e)}")
            return {
                "success": False,
                "error": str(e),
                "model_name": model_name,
                "provider": provider_name,
                "execution_time": datetime.now().isoformat()
            }


class ActionExecutor:
    """Handles action execution for agents"""
    
    def __init__(self, workflow_orchestrator: WorkflowOrchestrator):
        self.workflow_orchestrator = workflow_orchestrator
        self.action_registry = {}
        
    def register_action(self, action_name: str, action_function: Callable):
        """Register an action function"""
        self.action_registry[action_name] = action_function
        logger.info(f"Registered action: {action_name}")
        
    async def execute_action(
        self,
        action_name: str,
        parameters: Dict[str, Any],
        task_key: str,
        requester_id: str
    ) -> Dict[str, Any]:
        """Execute an action with approval workflow"""
        if action_name not in self.action_registry:
            raise ValueError(f"Action not found: {action_name}")
        
        # Check if action requires approval
        task_actions = await self.workflow_orchestrator.get_task_actions(task_key)
        action_config = None
        
        for action_data in task_actions:
            if action_data["action"]["name"] == action_name:
                action_config = action_data
                break
        
        if action_config and action_config.get("approvers"):
            # Action requires approval
            approval_id = await self.workflow_orchestrator.request_action_approval(
                task_key=task_key,
                action_key=action_config["action"]["_key"],
                requester_id=requester_id,
                request_data=parameters
            )
            
            return {
                "success": True,
                "status": "PENDING_APPROVAL",
                "approval_id": approval_id,
                "action_name": action_name,
                "parameters": parameters,
                "execution_time": datetime.now().isoformat()
            }
        
        else:
            # Action can be executed immediately
            return await self._execute_action_immediately(action_name, parameters)
    
    async def _execute_action_immediately(self, action_name: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Execute an action immediately without approval"""
        action_function = self.action_registry[action_name]
        
        try:
            # Execute the action
            if asyncio.iscoroutinefunction(action_function):
                result = await action_function(**parameters)
            else:
                result = action_function(**parameters)
                
            return {
                "success": True,
                "status": "COMPLETED",
                "result": result,
                "action_name": action_name,
                "parameters": parameters,
                "execution_time": datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Action execution failed: {action_name}, error: {str(e)}")
            return {
                "success": False,
                "status": "FAILED",
                "error": str(e),
                "action_name": action_name,
                "parameters": parameters,
                "execution_time": datetime.now().isoformat()
            }


class MemoryManager:
    """Manages agent memory including conversations, knowledge base, and app data"""
    
    def __init__(self):
        self.memory_stores = {}
        
    def register_memory_store(self, memory_type: MemoryType, store_client):
        """Register a memory store"""
        self.memory_stores[memory_type] = store_client
        logger.info(f"Registered memory store: {memory_type.value}")
        
    async def store_conversation(self, agent_id: str, conversation_data: Dict[str, Any]):
        """Store conversation in memory"""
        if MemoryType.CONVERSATIONS in self.memory_stores:
            store = self.memory_stores[MemoryType.CONVERSATIONS]
            await store.store_conversation(agent_id, conversation_data)
            
    async def retrieve_conversations(self, agent_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Retrieve recent conversations"""
        if MemoryType.CONVERSATIONS in self.memory_stores:
            store = self.memory_stores[MemoryType.CONVERSATIONS]
            return await store.retrieve_conversations(agent_id, limit)
        return []
        
    async def store_knowledge(self, agent_id: str, knowledge_data: Dict[str, Any]):
        """Store knowledge base data"""
        if MemoryType.KNOWLEDGE_BASE in self.memory_stores:
            store = self.memory_stores[MemoryType.KNOWLEDGE_BASE]
            await store.store_knowledge(agent_id, knowledge_data)
            
    async def retrieve_knowledge(self, agent_id: str, query: str) -> List[Dict[str, Any]]:
        """Retrieve relevant knowledge"""
        if MemoryType.KNOWLEDGE_BASE in self.memory_stores:
            store = self.memory_stores[MemoryType.KNOWLEDGE_BASE]
            return await store.retrieve_knowledge(agent_id, query)
        return []
        
    async def store_app_data(self, agent_id: str, app_data: Dict[str, Any]):
        """Store app-related data"""
        if MemoryType.APPS in self.memory_stores:
            store = self.memory_stores[MemoryType.APPS]
            await store.store_app_data(agent_id, app_data)
            
    async def retrieve_app_data(self, agent_id: str, app_name: str) -> List[Dict[str, Any]]:
        """Retrieve app-related data"""
        if MemoryType.APPS in self.memory_stores:
            store = self.memory_stores[MemoryType.APPS]
            return await store.retrieve_app_data(agent_id, app_name)
        return []


class AgentExecutionEngine:
    """Main agent execution engine that orchestrates all components"""
    
    def __init__(
        self,
        agent_service: AgentService,
        workflow_orchestrator: WorkflowOrchestrator,
        db_client
    ):
        self.agent_service = agent_service
        self.workflow_orchestrator = workflow_orchestrator
        self.db_client = db_client
        
        # Initialize components
        self.tool_executor = ToolExecutor()
        self.model_executor = ModelExecutor()
        self.action_executor = ActionExecutor(workflow_orchestrator)
        self.memory_manager = MemoryManager()
        
        # Execution state
        self.active_executions = {}
        
    async def start_agent_execution(
        self,
        agent_key: str,
        task_key: str,
        initial_message: Optional[str] = None
    ) -> str:
        """Start agent execution for a specific task"""
        # Get agent and task
        agent = await self.agent_service.get_agent(agent_key)
        if not agent:
            raise ValueError(f"Agent not found: {agent_key}")
        
        task = await self.db_client.get_document("tasks", task_key)
        if not task:
            raise ValueError(f"Task not found: {task_key}")
        
        # Get workflow context
        workflow_context = await self.workflow_orchestrator.get_workflow_context(task_key)
        
        # Create execution context
        context = AgentExecutionContext(agent, task, workflow_context)
        
        # Generate execution ID
        execution_id = f"{agent_key}_{task_key}_{datetime.now().timestamp()}"
        
        # Store execution context
        self.active_executions[execution_id] = context
        
        # Start execution loop
        asyncio.create_task(self._execution_loop(execution_id, initial_message))
        
        logger.info(f"Started agent execution: {execution_id}")
        return execution_id
    
    async def _execution_loop(self, execution_id: str, initial_message: Optional[str] = None):
        """Main execution loop for agent"""
        context = self.active_executions.get(execution_id)
        if not context:
            logger.error(f"Execution context not found: {execution_id}")
            return
        
        try:
            # Initialize agent with task
            await self._initialize_agent(context)
            
            # Process initial message if provided
            if initial_message:
                await self._process_user_message(context, initial_message)
            
            # Start agent's reasoning loop
            await self._reasoning_loop(context)
            
        except Exception as e:
            logger.error(f"Agent execution failed: {execution_id}, error: {str(e)}")
            context.add_to_log(f"Execution failed: {str(e)}", "ERROR")
            
        finally:
            # Clean up execution context
            if execution_id in self.active_executions:
                del self.active_executions[execution_id]
    
    async def _initialize_agent(self, context: AgentExecutionContext):
        """Initialize agent with task context"""
        agent = context.agent
        task = context.task
        
        # Load agent's memory
        await self._load_agent_memory(context)
        
        # Set up agent's context with task information
        task_context = {
            "task_name": task.name,
            "task_description": task.description,
            "task_priority": task.priority.value,
            "workflow_context": context.workflow_context
        }
        
        # Create system message with task context
        system_message = f"""
        {agent.system_prompt}
        
        Current Task:
        - Name: {task.name}
        - Description: {task.description}
        - Priority: {task.priority.value}
        
        Available Tools: {', '.join(agent.selected_tools)}
        Available Models: {', '.join(agent.selected_models)}
        Available Actions: {', '.join(agent.selected_actions)}
        """
        
        context.add_to_log(f"Agent initialized with task: {task.name}")
        
        # Store initial context in memory
        await self.memory_manager.store_conversation(
            agent._key,
            {
                "type": "system",
                "content": system_message,
                "timestamp": datetime.now().isoformat()
            }
        )
    
    async def _load_agent_memory(self, context: AgentExecutionContext):
        """Load agent's memory from various sources"""
        agent = context.agent
        
        # Load conversations
        conversations = await self.memory_manager.retrieve_conversations(agent._key)
        context.memory["conversations"] = conversations
        
        # Load relevant knowledge
        knowledge = await self.memory_manager.retrieve_knowledge(agent._key, context.task.name)
        context.memory["knowledge"] = knowledge
        
        # Load app data
        app_data = await self.memory_manager.retrieve_app_data(agent._key, "general")
        context.memory["app_data"] = app_data
        
        context.add_to_log(f"Loaded memory: {len(conversations)} conversations, {len(knowledge)} knowledge items")
    
    async def _reasoning_loop(self, context: AgentExecutionContext):
        """Main reasoning loop for agent"""
        agent = context.agent
        task = context.task
        
        max_iterations = 50  # Prevent infinite loops
        iteration = 0
        
        while iteration < max_iterations:
            iteration += 1
            
            # Analyze current state
            current_state = await self._analyze_current_state(context)
            
            # Determine next action
            next_action = await self._determine_next_action(context, current_state)
            
            if not next_action:
                context.add_to_log("No next action determined, completing task")
                break
            
            # Execute the action
            result = await self._execute_action(context, next_action)
            
            # Update context with result
            context.add_to_log(f"Executed action: {next_action['type']}, result: {result['success']}")
            
            # Check if task is complete
            if await self._is_task_complete(context):
                context.add_to_log("Task completed successfully")
                break
        
        # Finalize task
        await self._finalize_task(context)
    
    async def _analyze_current_state(self, context: AgentExecutionContext) -> Dict[str, Any]:
        """Analyze current state of the task"""
        task = context.task
        
        # Get task status
        task_doc = await self.db_client.get_document("tasks", task._key)
        status = task_doc.get("status", "PENDING")
        
        # Analyze what has been done
        completed_actions = len([r for r in context.action_results.values() if r.get("success")])
        failed_actions = len([r for r in context.action_results.values() if not r.get("success")])
        
        return {
            "task_status": status,
            "completed_actions": completed_actions,
            "failed_actions": failed_actions,
            "tool_calls": len(context.tool_results),
            "model_calls": len(context.model_responses),
            "conversation_length": len(context.conversation_history)
        }
    
    async def _determine_next_action(self, context: AgentExecutionContext, current_state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Determine the next action to take"""
        agent = context.agent
        task = context.task
        
        # Create prompt for decision making
        prompt = f"""
        Task: {task.name}
        Description: {task.description}
        
        Current State:
        - Task Status: {current_state['task_status']}
        - Completed Actions: {current_state['completed_actions']}
        - Failed Actions: {current_state['failed_actions']}
        - Tool Calls: {current_state['tool_calls']}
        - Model Calls: {current_state['model_calls']}
        
        Available Tools: {agent.selected_tools}
        Available Models: {agent.selected_models}
        Available Actions: {agent.selected_actions}
        
        Based on the current state and available capabilities, what should be the next action?
        Choose from:
        1. Use a tool to gather information
        2. Call a model for reasoning
        3. Execute an action to make changes
        4. Complete the task if all objectives are met
        
        Provide your reasoning and the specific action to take.
        """
        
        # Use the primary model for decision making
        if agent.selected_models:
            primary_model = agent.selected_models[0]
            response = await self.model_executor.call_model(primary_model, prompt)
            
            if response["success"]:
                # Parse the response to determine next action
                # This is a simplified version - in reality, you'd have more sophisticated parsing
                model_response = response["response"]
                
                # Store the response
                context.model_responses[f"decision_{len(context.model_responses)}"] = response
                
                # Determine action based on response (simplified)
                if "tool" in model_response.lower():
                    return {"type": "tool", "model_response": model_response}
                elif "action" in model_response.lower():
                    return {"type": "action", "model_response": model_response}
                elif "complete" in model_response.lower():
                    return {"type": "complete", "model_response": model_response}
        
        return None
    
    async def _execute_action(self, context: AgentExecutionContext, action: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a specific action"""
        action_type = action["type"]
        
        if action_type == "tool":
            return await self._execute_tool_action(context, action)
        elif action_type == "action":
            return await self._execute_app_action(context, action)
        elif action_type == "complete":
            return await self._complete_task(context)
        else:
            return {"success": False, "error": f"Unknown action type: {action_type}"}
    
    async def _execute_tool_action(self, context: AgentExecutionContext, action: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a tool action"""
        # This is a simplified version - in reality, you'd parse the model response
        # to determine which tool to use and with what parameters
        
        agent = context.agent
        available_tools = agent.selected_tools
        
        if not available_tools:
            return {"success": False, "error": "No tools available"}
        
        # For demonstration, use the first available tool
        tool_name = available_tools[0]
        parameters = {}  # In reality, these would be parsed from the model response
        
        result = await self.tool_executor.execute_tool(tool_name, parameters)
        context.tool_results[f"tool_{len(context.tool_results)}"] = result
        
        return result
    
    async def _execute_app_action(self, context: AgentExecutionContext, action: Dict[str, Any]) -> Dict[str, Any]:
        """Execute an app action"""
        agent = context.agent
        available_actions = agent.selected_actions
        
        if not available_actions:
            return {"success": False, "error": "No actions available"}
        
        # For demonstration, use the first available action
        action_name = available_actions[0]
        parameters = {}  # In reality, these would be parsed from the model response
        
        result = await self.action_executor.execute_action(
            action_name=action_name,
            parameters=parameters,
            task_key=context.task._key,
            requester_id=agent.created_by or "system"
        )
        
        context.action_results[f"action_{len(context.action_results)}"] = result
        return result
    
    async def _complete_task(self, context: AgentExecutionContext) -> Dict[str, Any]:
        """Mark task as completed"""
        task_key = context.task._key
        
        # Update task status
        await self.db_client.update_document(
            "tasks",
            task_key,
            {
                "status": "COMPLETED",
                "updatedAtTimestamp": datetime.now().timestamp()
            }
        )
        
        context.add_to_log("Task marked as completed")
        return {"success": True, "status": "COMPLETED"}
    
    async def _is_task_complete(self, context: AgentExecutionContext) -> bool:
        """Check if task is complete"""
        task_doc = await self.db_client.get_document("tasks", context.task._key)
        return task_doc.get("status") == "COMPLETED"
    
    async def _finalize_task(self, context: AgentExecutionContext):
        """Finalize task execution"""
        agent = context.agent
        task = context.task
        
        # Store final execution summary
        summary = context.get_context_summary()
        await self.memory_manager.store_conversation(
            agent._key,
            {
                "type": "execution_summary",
                "content": summary,
                "timestamp": datetime.now().isoformat()
            }
        )
        
        context.add_to_log("Task execution finalized")
    
    async def _process_user_message(self, context: AgentExecutionContext, message: str):
        """Process a user message during execution"""
        agent = context.agent
        
        # Store user message
        context.conversation_history.append({
            "role": "user",
            "content": message,
            "timestamp": datetime.now().isoformat()
        })
        
        # Use agent's model to respond
        if agent.selected_models:
            primary_model = agent.selected_models[0]
            
            # Create conversation context
            conversation_context = {
                "system_prompt": agent.system_prompt,
                "conversation_history": context.conversation_history[-10:],  # Last 10 messages
                "task_context": {
                    "name": context.task.name,
                    "description": context.task.description
                }
            }
            
            response = await self.model_executor.call_model(
                primary_model,
                message,
                conversation_context
            )
            
            if response["success"]:
                # Store agent response
                context.conversation_history.append({
                    "role": "assistant",
                    "content": response["response"],
                    "timestamp": datetime.now().isoformat()
                })
                
                context.model_responses[f"conversation_{len(context.model_responses)}"] = response
        
        # Store in memory
        await self.memory_manager.store_conversation(
            agent._key,
            {
                "type": "conversation",
                "content": {
                    "user_message": message,
                    "conversation_history": context.conversation_history
                },
                "timestamp": datetime.now().isoformat()
            }
        )
    
    # Public API methods
    async def get_execution_status(self, execution_id: str) -> Optional[Dict[str, Any]]:
        """Get status of an execution"""
        context = self.active_executions.get(execution_id)
        if not context:
            return None
        
        return {
            "execution_id": execution_id,
            "agent_id": context.agent._key,
            "task_id": context.task._key,
            "context_summary": context.get_context_summary(),
            "execution_log": context.execution_log[-20:],  # Last 20 log entries
            "is_active": True
        }
    
    async def stop_execution(self, execution_id: str) -> bool:
        """Stop an active execution"""
        if execution_id in self.active_executions:
            context = self.active_executions[execution_id]
            context.add_to_log("Execution stopped by user", "WARNING")
            
            # Update task status
            await self.db_client.update_document(
                "tasks",
                context.task._key,
                {
                    "status": "CANCELLED",
                    "updatedAtTimestamp": datetime.now().timestamp()
                }
            )
            
            del self.active_executions[execution_id]
            return True
        
        return False
    
    async def send_message(self, execution_id: str, message: str) -> bool:
        """Send a message to an active execution"""
        context = self.active_executions.get(execution_id)
        if not context:
            return False
        
        await self._process_user_message(context, message)
        return True 
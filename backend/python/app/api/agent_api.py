"""
Agent API endpoints for the agent system

This module provides RESTful API endpoints for:
- Agent templates and instances
- Agent execution with retrieval service integration
- Knowledge search agents
- Tool and model management
"""

import asyncio
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.models.agents import (
    AgentTemplate, Agent, Tool, AIModel, AppAction, Task, Workflow,
    MemoryType, UserRole, TaskPriority, ModelProvider, ModelType,
    ToolConfig, ModelConfig, ActionConfig, MemoryConfig, ApprovalConfig
)
from app.services.agent_service import AgentService
from app.services.workflow_orchestrator import WorkflowOrchestrator
from app.agent.core.langgraph_executor import LangGraphAgentExecutor
from app.modules.retrieval.retrieval_service import RetrievalService
from app.modules.retrieval.retrieval_arango import ArangoService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])

# Pydantic models for API requests/responses
class CreateAgentTemplateRequest(BaseModel):
    name: str
    description: str
    start_message: str
    system_prompt: str
    tools: List[ToolConfig]
    models: List[ModelConfig]
    actions: List[ActionConfig]
    memory_types: List[MemoryType]
    tags: List[str]
    org_id: Optional[str] = None

class CreateAgentRequest(BaseModel):
    name: str
    description: str
    template_key: str
    system_prompt: str
    starting_message: str
    selected_tools: List[str]
    selected_models: List[str]
    selected_actions: List[str]
    selected_memory_types: List[MemoryType]
    tags: List[str]
    org_id: Optional[str] = None

class ExecuteAgentRequest(BaseModel):
    agent_key: str
    user_query: str
    task_name: Optional[str] = None
    task_description: Optional[str] = None

class AgentResponse(BaseModel):
    key: str
    name: str
    description: str
    template_key: str
    system_prompt: str
    starting_message: str
    selected_tools: List[str]
    selected_models: List[str]
    selected_actions: List[str]
    selected_memory_types: List[str]
    tags: List[str]
    is_active: bool
    created_by: Optional[str]
    created_at: Optional[float]

class ExecutionResponse(BaseModel):
    execution_id: str
    task_id: str
    status: str
    message: str

class ExecutionStatusResponse(BaseModel):
    execution_id: str
    agent_id: str
    task_id: str
    current_state: str
    iteration_count: int
    user_query: str
    tool_results: List[Dict[str, Any]]
    action_results: List[Dict[str, Any]]
    execution_log: List[Dict[str, Any]]
    error_message: Optional[str]
    is_active: bool

# Dependency injection functions (placeholders)
def get_current_user_id(request: Request) -> str:
    """Get current user ID from request"""
    return request.state.user.get("userId", "default_user")

def get_current_org_id(request: Request) -> str:
    """Get current org ID from request"""
    return request.state.user.get("orgId", "default_org")

def get_agent_service() -> AgentService:
    """Get agent service instance"""
    # This would be injected via dependency injection container
    from app.containers.query import QueryAppContainer
    container = QueryAppContainer()
    return AgentService(container.db_client())

def get_workflow_orchestrator() -> WorkflowOrchestrator:
    """Get workflow orchestrator instance"""
    from app.containers.query import QueryAppContainer
    container = QueryAppContainer()
    agent_service = AgentService(container.db_client())
    return WorkflowOrchestrator(container.db_client(), agent_service)

def get_langgraph_executor() -> LangGraphAgentExecutor:
    """Get LangGraph executor instance with retrieval service integration"""
    from app.containers.query import QueryAppContainer
    container = QueryAppContainer()
    agent_service = AgentService(container.db_client())
    workflow_orchestrator = WorkflowOrchestrator(container.db_client(), agent_service)
    
    # Get retrieval service and arango service
    retrieval_service = container.retrieval_service()
    arango_service = container.arango_service()
    
    return LangGraphAgentExecutor(
        agent_service=agent_service,
        workflow_orchestrator=workflow_orchestrator,
        db_client=container.db_client(),
        retrieval_service=retrieval_service,
        arango_service=arango_service
    )

def get_retrieval_service() -> RetrievalService:
    """Get retrieval service instance"""
    from app.containers.query import QueryAppContainer
    container = QueryAppContainer()
    return container.retrieval_service()

def get_arango_service() -> ArangoService:
    """Get arango service instance"""
    from app.containers.query import QueryAppContainer
    container = QueryAppContainer()
    return container.arango_service()

# Agent Template endpoints
@router.post("/templates", response_model=Dict[str, Any])
async def create_agent_template(
    request: CreateAgentTemplateRequest,
    current_user_id: str = Depends(get_current_user_id),
    agent_service: AgentService = Depends(get_agent_service)
):
    """Create a new agent template"""
    try:
        template = await agent_service.create_agent_template(
            name=request.name,
            description=request.description,
            start_message=request.start_message,
            system_prompt=request.system_prompt,
            tools=request.tools,
            models=request.models,
            actions=request.actions,
            memory_types=request.memory_types,
            tags=request.tags,
            org_id=request.org_id or getattr(request, 'org_id', None),
            created_by=current_user_id
        )
        
        return {
            "success": True,
            "template": {
                "key": template._key,
                "name": template.name,
                "description": template.description,
                "tools": [tool.__dict__ for tool in template.tools],
                "models": [model.__dict__ for model in template.models],
                "actions": [action.__dict__ for action in template.actions],
                "memory_types": [mt.value for mt in template.memory.types]
            }
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to create agent template: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@router.get("/templates", response_model=List[Dict[str, Any]])
async def list_agent_templates(
    org_id: Optional[str] = None,
    agent_service: AgentService = Depends(get_agent_service)
):
    """List agent templates"""
    try:
        templates = await agent_service.list_agent_templates(org_id=org_id)
        return [
            {
                "key": template._key,
                "name": template.name,
                "description": template.description,
                "tools": [tool.__dict__ for tool in template.tools],
                "models": [model.__dict__ for model in template.models],
                "actions": [action.__dict__ for action in template.actions],
                "memory_types": [mt.value for mt in template.memory.types],
                "tags": template.tags
            }
            for template in templates
        ]
    except Exception as e:
        logger.error(f"Failed to list agent templates: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@router.get("/templates/{template_key}", response_model=Dict[str, Any])
async def get_agent_template(
    template_key: str,
    agent_service: AgentService = Depends(get_agent_service)
):
    """Get agent template by key"""
    try:
        template = await agent_service.get_agent_template(template_key)
        if not template:
            raise HTTPException(status_code=404, detail="Agent template not found")
        
        return {
            "key": template._key,
            "name": template.name,
            "description": template.description,
            "system_prompt": template.system_prompt,
            "start_message": template.start_message,
            "tools": [tool.__dict__ for tool in template.tools],
            "models": [model.__dict__ for model in template.models],
            "actions": [action.__dict__ for action in template.actions],
            "memory_types": [mt.value for mt in template.memory.types],
            "tags": template.tags,
            "org_id": template.org_id,
            "created_by": template.created_by
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get agent template: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

# Agent endpoints
@router.post("/", response_model=AgentResponse)
async def create_agent(
    request: CreateAgentRequest,
    current_user_id: str = Depends(get_current_user_id),
    agent_service: AgentService = Depends(get_agent_service)
):
    """Create a new agent"""
    try:
        agent, edge = await agent_service.create_agent(
            name=request.name,
            description=request.description,
            template_key=request.template_key,
            system_prompt=request.system_prompt,
            starting_message=request.starting_message,
            selected_tools=request.selected_tools,
            selected_models=request.selected_models,
            selected_actions=request.selected_actions,
            selected_memory_types=request.selected_memory_types,
            tags=request.tags,
            user_id=current_user_id,
            org_id=request.org_id,
            created_by=current_user_id
        )
        
        return AgentResponse(
            key=agent._key,
            name=agent.name,
            description=agent.description,
            template_key=agent.template_key,
            system_prompt=agent.system_prompt,
            starting_message=agent.starting_message,
            selected_tools=agent.selected_tools,
            selected_models=agent.selected_models,
            selected_actions=agent.selected_actions,
            selected_memory_types=[mt.value for mt in agent.selected_memory_types],
            tags=agent.tags,
            is_active=agent.is_active,
            created_by=agent.created_by,
            created_at=agent.created_at_timestamp
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to create agent: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@router.get("/", response_model=List[AgentResponse])
async def list_user_agents(
    current_user_id: str = Depends(get_current_user_id),
    agent_service: AgentService = Depends(get_agent_service)
):
    """List agents for current user"""
    try:
        agents = await agent_service.get_user_agents(current_user_id)
        return [
            AgentResponse(
                key=agent._key,
                name=agent.name,
                description=agent.description,
                template_key=agent.template_key,
                system_prompt=agent.system_prompt,
                starting_message=agent.starting_message,
                selected_tools=agent.selected_tools,
                selected_models=agent.selected_models,
                selected_actions=agent.selected_actions,
                selected_memory_types=[mt.value for mt in agent.selected_memory_types],
                tags=agent.tags,
                is_active=agent.is_active,
                created_by=agent.created_by,
                created_at=agent.created_at_timestamp
            )
            for agent in agents
        ]
    except Exception as e:
        logger.error(f"Failed to list user agents: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@router.get("/{agent_key}", response_model=AgentResponse)
async def get_agent(
    agent_key: str,
    agent_service: AgentService = Depends(get_agent_service)
):
    """Get agent by key"""
    try:
        agent = await agent_service.get_agent(agent_key)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        
        return AgentResponse(
            key=agent._key,
            name=agent.name,
            description=agent.description,
            template_key=agent.template_key,
            system_prompt=agent.system_prompt,
            starting_message=agent.starting_message,
            selected_tools=agent.selected_tools,
            selected_models=agent.selected_models,
            selected_actions=agent.selected_actions,
            selected_memory_types=[mt.value for mt in agent.selected_memory_types],
            tags=agent.tags,
            is_active=agent.is_active,
            created_by=agent.created_by,
            created_at=agent.created_at_timestamp
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get agent: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

# Agent Execution endpoints
@router.post("/execute", response_model=ExecutionResponse)
async def execute_agent(
    request: ExecuteAgentRequest,
    current_user_id: str = Depends(get_current_user_id),
    current_org_id: str = Depends(get_current_org_id),
    langgraph_executor: LangGraphAgentExecutor = Depends(get_langgraph_executor),
    workflow_orchestrator: WorkflowOrchestrator = Depends(get_workflow_orchestrator)
):
    """Execute an agent with a user query"""
    try:
        # Create task for this execution
        task_name = request.task_name or f"Query: {request.user_query[:50]}..."
        task_description = request.task_description or request.user_query
        
        task = await workflow_orchestrator.create_task(
            name=task_name,
            description=task_description,
            user_id=current_user_id,
            org_id=current_org_id
        )
        
        # Start agent execution
        execution_id = await langgraph_executor.start_agent_execution(
            agent_key=request.agent_key,
            task_key=task._key,
            user_query=request.user_query,
            user_id=current_user_id,
            org_id=current_org_id
        )
        
        return ExecutionResponse(
            execution_id=execution_id,
            task_id=task._key,
            status="started",
            message="Agent execution started successfully"
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to execute agent: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@router.get("/execute/{execution_id}/status", response_model=ExecutionStatusResponse)
async def get_execution_status(
    execution_id: str,
    langgraph_executor: LangGraphAgentExecutor = Depends(get_langgraph_executor)
):
    """Get execution status"""
    try:
        status = await langgraph_executor.get_execution_status(execution_id)
        if not status:
            raise HTTPException(status_code=404, detail="Execution not found")
        
        return ExecutionStatusResponse(**status)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get execution status: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@router.post("/execute/{execution_id}/stop")
async def stop_execution(
    execution_id: str,
    langgraph_executor: LangGraphAgentExecutor = Depends(get_langgraph_executor)
):
    """Stop an active execution"""
    try:
        success = await langgraph_executor.stop_execution(execution_id)
        if not success:
            raise HTTPException(status_code=404, detail="Execution not found")
        
        return {"success": True, "message": "Execution stopped successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to stop execution: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@router.post("/execute/{execution_id}/message")
async def send_message_to_agent(
    execution_id: str,
    message: str,
    langgraph_executor: LangGraphAgentExecutor = Depends(get_langgraph_executor)
):
    """Send a message to an active execution"""
    try:
        success = await langgraph_executor.send_message(execution_id, message)
        if not success:
            raise HTTPException(status_code=400, detail="Cannot send message to this execution")
        
        return {"success": True, "message": "Message sent successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to send message: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

# Knowledge Search Agent specific endpoints
@router.post("/knowledge-search/templates")
async def create_knowledge_search_template(
    current_user_id: str = Depends(get_current_user_id),
    agent_service: AgentService = Depends(get_agent_service)
):
    """Create a knowledge search agent template"""
    try:
        # Create knowledge search template
        template = await agent_service.create_agent_template(
            name="Knowledge Search Agent",
            description="An AI agent specialized in searching knowledge base, Gmail, and Drive",
            start_message="Hello! I'm your knowledge search assistant. I can help you find information from your knowledge base, emails, and documents. What would you like to search for?",
            system_prompt="""You are a knowledge search assistant AI agent. Your role is to:
1. Understand user queries and determine what information they need
2. Search knowledge base for relevant documents and information
3. Search Gmail for relevant emails and conversations
4. Search Google Drive for relevant documents and files
5. Synthesize information from multiple sources
6. Provide comprehensive, well-structured responses

Always be thorough, accurate, and professional in your work. Use the appropriate search tools based on the user's query.""",
            tools=[
                ToolConfig(name="knowledgebase.search", description="Search knowledge base for relevant information"),
                ToolConfig(name="gmail.search", description="Search Gmail for emails and conversations"),
                ToolConfig(name="drive.search", description="Search Google Drive for documents and files")
            ],
            models=[
                ModelConfig(
                    name="gpt-4",
                    role="primary",
                    provider="OPENAI",
                    config={"temperature": 0.7, "max_tokens": 4000}
                )
            ],
            actions=[
                ActionConfig(
                    name="respond_to_user",
                    approvers=[],
                    reviewers=[]
                )
            ],
            memory_types=[MemoryType.KNOWLEDGE_BASE, MemoryType.APPS],
            tags=["knowledge-search", "retrieval", "search"],
            org_id=None,
            created_by=current_user_id
        )
        
        return {
            "success": True,
            "template": {
                "key": template._key,
                "name": template.name,
                "description": template.description,
                "tools": [tool.__dict__ for tool in template.tools],
                "memory_types": [mt.value for mt in template.memory.types]
            }
        }
    except Exception as e:
        logger.error(f"Failed to create knowledge search template: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@router.post("/knowledge-search/agents")
async def create_knowledge_search_agent(
    name: str,
    description: str,
    template_key: str,
    current_user_id: str = Depends(get_current_user_id),
    agent_service: AgentService = Depends(get_agent_service)
):
    """Create a knowledge search agent instance"""
    try:
        agent, edge = await agent_service.create_agent(
            name=name,
            description=description,
            template_key=template_key,
            system_prompt="You are my personal knowledge search assistant. Help me find relevant information from my knowledge base, emails, and documents.",
            starting_message="Hi! I'm ready to help you search through your knowledge base, emails, and documents. What would you like to find?",
            selected_tools=["knowledgebase.search", "gmail.search", "drive.search"],
            selected_models=["gpt-4"],
            selected_actions=["respond_to_user"],
            selected_memory_types=[MemoryType.KNOWLEDGE_BASE, MemoryType.APPS],
            tags=["knowledge-search", "personal"],
            user_id=current_user_id,
            org_id=None,
            created_by=current_user_id
        )
        
        return AgentResponse(
            key=agent._key,
            name=agent.name,
            description=agent.description,
            template_key=agent.template_key,
            system_prompt=agent.system_prompt,
            starting_message=agent.starting_message,
            selected_tools=agent.selected_tools,
            selected_models=agent.selected_models,
            selected_actions=agent.selected_actions,
            selected_memory_types=[mt.value for mt in agent.selected_memory_types],
            tags=agent.tags,
            is_active=agent.is_active,
            created_by=agent.created_by,
            created_at=agent.created_at_timestamp
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to create knowledge search agent: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

# Tool and Model endpoints
@router.get("/tools", response_model=List[Dict[str, Any]])
async def list_tools(
    org_id: Optional[str] = None,
    agent_service: AgentService = Depends(get_agent_service)
):
    """List available tools"""
    try:
        tools = await agent_service.get_tools(org_id=org_id)
        return [
            {
                "key": tool._key,
                "name": tool.name,
                "vendor_name": tool.vendor_name,
                "description": tool.description,
                "is_active": tool.is_active
            }
            for tool in tools
        ]
    except Exception as e:
        logger.error(f"Failed to list tools: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@router.get("/models", response_model=List[Dict[str, Any]])
async def list_models(
    org_id: Optional[str] = None,
    agent_service: AgentService = Depends(get_agent_service)
):
    """List available AI models"""
    try:
        models = await agent_service.get_ai_models(org_id=org_id)
        return [
            {
                "key": model._key,
                "name": model.name,
                "description": model.description,
                "provider": model.provider.value,
                "model_type": model.model_type.value,
                "model_key": model.model_key
            }
            for model in models
        ]
    except Exception as e:
        logger.error(f"Failed to list models: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@router.get("/actions", response_model=List[Dict[str, Any]])
async def list_actions(
    org_id: Optional[str] = None,
    agent_service: AgentService = Depends(get_agent_service)
):
    """List available app actions"""
    try:
        actions = await agent_service.get_app_actions(org_id=org_id)
        return [
            {
                "key": action._key,
                "name": action.name,
                "description": action.description,
                "org_id": action.org_id
            }
            for action in actions
        ]
    except Exception as e:
        logger.error(f"Failed to list actions: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}") 

# Workflow Template endpoints
@router.post("/workflow-templates", response_model=Dict[str, Any])
async def create_workflow_template(
    request: Request,
    name: str,
    description: str,
    workflow_type: str,
    steps: List[Dict[str, Any]],
    triggers: List[str],
    conditions: Dict[str, Any],
    is_public: bool = False,
    current_user_id: str = Depends(get_current_user_id),
    workflow_orchestrator: WorkflowOrchestrator = Depends(get_workflow_orchestrator)
):
    """Create a new workflow template"""
    try:
        workflow = await workflow_orchestrator.create_workflow_template(
            name=name,
            description=description,
            workflow_type=workflow_type,
            steps=steps,
            triggers=triggers,
            conditions=conditions,
            org_id=request.state.user.get("orgId"),
            is_public=is_public,
            created_by=current_user_id
        )
        
        return {
            "success": True,
            "workflow": {
                "key": workflow._key,
                "name": workflow.name,
                "description": workflow.description,
                "workflow_type": workflow.workflow_type,
                "steps": workflow.steps,
                "triggers": workflow.triggers,
                "is_public": workflow.is_public
            }
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to create workflow template: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@router.get("/workflow-templates", response_model=List[Dict[str, Any]])
async def list_workflow_templates(
    workflow_type: Optional[str] = None,
    include_public: bool = True,
    workflow_orchestrator: WorkflowOrchestrator = Depends(get_workflow_orchestrator)
):
    """List available workflow templates"""
    try:
        workflows = await workflow_orchestrator.list_workflow_templates(
            workflow_type=workflow_type,
            include_public=include_public
        )
        
        return [
            {
                "key": workflow._key,
                "name": workflow.name,
                "description": workflow.description,
                "workflow_type": workflow.workflow_type,
                "steps": workflow.steps,
                "triggers": workflow.triggers,
                "conditions": workflow.conditions,
                "is_public": workflow.is_public,
                "created_by": workflow.created_by
            }
            for workflow in workflows
        ]
    except Exception as e:
        logger.error(f"Failed to list workflow templates: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@router.get("/workflow-templates/{workflow_key}", response_model=Dict[str, Any])
async def get_workflow_template(
    workflow_key: str,
    workflow_orchestrator: WorkflowOrchestrator = Depends(get_workflow_orchestrator)
):
    """Get workflow template by key"""
    try:
        workflow = await workflow_orchestrator.get_workflow_template(workflow_key)
        if not workflow:
            raise HTTPException(status_code=404, detail="Workflow template not found")
        
        return {
            "key": workflow._key,
            "name": workflow.name,
            "description": workflow.description,
            "workflow_type": workflow.workflow_type,
            "steps": workflow.steps,
            "triggers": workflow.triggers,
            "conditions": workflow.conditions,
            "is_public": workflow.is_public,
            "org_id": workflow.org_id,
            "created_by": workflow.created_by,
            "created_at": workflow.created_at_timestamp
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get workflow template: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@router.post("/workflow-templates/{workflow_key}/execute")
async def execute_workflow_template(
    workflow_key: str,
    agent_key: str,
    user_query: str,
    context: Optional[Dict[str, Any]] = None,
    current_user_id: str = Depends(get_current_user_id),
    current_org_id: str = Depends(get_current_org_id),
    workflow_orchestrator: WorkflowOrchestrator = Depends(get_workflow_orchestrator)
):
    """Execute a workflow template with an agent"""
    try:
        task_key = await workflow_orchestrator.execute_workflow_template(
            workflow_template_key=workflow_key,
            agent_key=agent_key,
            user_query=user_query,
            user_id=current_user_id,
            org_id=current_org_id,
            context=context
        )
        
        return {
            "success": True,
            "task_key": task_key,
            "message": "Workflow template execution started"
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to execute workflow template: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

# Predefined Workflow Templates
@router.post("/workflow-templates/ticketing-agent")
async def create_ticketing_agent_workflow(
    current_user_id: str = Depends(get_current_user_id),
    workflow_orchestrator: WorkflowOrchestrator = Depends(get_workflow_orchestrator)
):
    """Create a ticketing agent workflow template"""
    try:
        workflow = await workflow_orchestrator.create_workflow_template(
            name="Ticketing Agent Workflow",
            description="Standard workflow for ticket processing and resolution",
            workflow_type="ticketing",
            steps=[
                {
                    "step": 1,
                    "name": "Analyze Ticket",
                    "action_key": "analyze_ticket",
                    "description": "Analyze the ticket content and categorize it",
                    "approvers": [],
                    "reviewers": []
                },
                {
                    "step": 2,
                    "name": "Assign Priority",
                    "action_key": "assign_priority",
                    "description": "Assign priority level based on analysis",
                    "approvers": [],
                    "reviewers": []
                },
                {
                    "step": 3,
                    "name": "Route to Team",
                    "action_key": "route_ticket",
                    "description": "Route ticket to appropriate team",
                    "approvers": [{"user_ids": ["manager_1"], "order": 1}],
                    "reviewers": []
                },
                {
                    "step": 4,
                    "name": "Update Status",
                    "action_key": "update_status",
                    "description": "Update ticket status and notify stakeholders",
                    "approvers": [],
                    "reviewers": []
                }
            ],
            triggers=["new_ticket", "ticket_update", "escalation"],
            conditions={
                "requires_approval": True,
                "auto_assign": True,
                "notify_stakeholders": True
            },
            org_id=None,
            is_public=True,
            created_by=current_user_id
        )
        
        return {
            "success": True,
            "workflow": {
                "key": workflow._key,
                "name": workflow.name,
                "description": workflow.description,
                "workflow_type": workflow.workflow_type,
                "steps": workflow.steps,
                "triggers": workflow.triggers
            }
        }
    except Exception as e:
        logger.error(f"Failed to create ticketing workflow template: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@router.post("/workflow-templates/knowledge-search")
async def create_knowledge_search_workflow(
    current_user_id: str = Depends(get_current_user_id),
    workflow_orchestrator: WorkflowOrchestrator = Depends(get_workflow_orchestrator)
):
    """Create a knowledge search workflow template"""
    try:
        workflow = await workflow_orchestrator.create_workflow_template(
            name="Knowledge Search Workflow",
            description="Workflow for searching and retrieving information from multiple sources",
            workflow_type="knowledge_search",
            steps=[
                {
                    "step": 1,
                    "name": "Analyze Query",
                    "action_key": "analyze_query",
                    "description": "Analyze the user query to understand intent",
                    "approvers": [],
                    "reviewers": []
                },
                {
                    "step": 2,
                    "name": "Search Knowledge Base",
                    "action_key": "search_knowledge_base",
                    "description": "Search knowledge base for relevant information",
                    "approvers": [],
                    "reviewers": []
                },
                {
                    "step": 3,
                    "name": "Search Gmail",
                    "action_key": "search_gmail",
                    "description": "Search Gmail for relevant emails",
                    "approvers": [],
                    "reviewers": []
                },
                {
                    "step": 4,
                    "name": "Search Drive",
                    "action_key": "search_drive",
                    "description": "Search Google Drive for relevant documents",
                    "approvers": [],
                    "reviewers": []
                },
                {
                    "step": 5,
                    "name": "Synthesize Results",
                    "action_key": "synthesize_results",
                    "description": "Combine and synthesize information from all sources",
                    "approvers": [],
                    "reviewers": []
                },
                {
                    "step": 6,
                    "name": "Generate Response",
                    "action_key": "generate_response",
                    "description": "Generate comprehensive response for user",
                    "approvers": [],
                    "reviewers": []
                }
            ],
            triggers=["user_query", "information_request", "research_request"],
            conditions={
                "requires_approval": False,
                "auto_assign": True,
                "notify_stakeholders": False
            },
            org_id=None,
            is_public=True,
            created_by=current_user_id
        )
        
        return {
            "success": True,
            "workflow": {
                "key": workflow._key,
                "name": workflow.name,
                "description": workflow.description,
                "workflow_type": workflow.workflow_type,
                "steps": workflow.steps,
                "triggers": workflow.triggers
            }
        }
    except Exception as e:
        logger.error(f"Failed to create knowledge search workflow template: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@router.post("/workflow-templates/customer-support")
async def create_customer_support_workflow(
    current_user_id: str = Depends(get_current_user_id),
    workflow_orchestrator: WorkflowOrchestrator = Depends(get_workflow_orchestrator)
):
    """Create a customer support workflow template"""
    try:
        workflow = await workflow_orchestrator.create_workflow_template(
            name="Customer Support Workflow",
            description="Workflow for handling customer support requests",
            workflow_type="customer_support",
            steps=[
                {
                    "step": 1,
                    "name": "Classify Request",
                    "action_key": "classify_request",
                    "description": "Classify the customer request type",
                    "approvers": [],
                    "reviewers": []
                },
                {
                    "step": 2,
                    "name": "Check Knowledge Base",
                    "action_key": "check_knowledge_base",
                    "description": "Check knowledge base for existing solutions",
                    "approvers": [],
                    "reviewers": []
                },
                {
                    "step": 3,
                    "name": "Generate Response",
                    "action_key": "generate_response",
                    "description": "Generate appropriate response for customer",
                    "approvers": [],
                    "reviewers": []
                },
                {
                    "step": 4,
                    "name": "Escalate if Needed",
                    "action_key": "escalate_request",
                    "description": "Escalate to human agent if needed",
                    "approvers": [{"user_ids": ["support_lead"], "order": 1}],
                    "reviewers": []
                },
                {
                    "step": 5,
                    "name": "Update Ticket",
                    "action_key": "update_ticket",
                    "description": "Update ticket with resolution and feedback",
                    "approvers": [],
                    "reviewers": []
                }
            ],
            triggers=["customer_request", "support_ticket", "inquiry"],
            conditions={
                "requires_approval": True,
                "auto_assign": True,
                "notify_stakeholders": True
            },
            org_id=None,
            is_public=True,
            created_by=current_user_id
        )
        
        return {
            "success": True,
            "workflow": {
                "key": workflow._key,
                "name": workflow.name,
                "description": workflow.description,
                "workflow_type": workflow.workflow_type,
                "steps": workflow.steps,
                "triggers": workflow.triggers
            }
        }
    except Exception as e:
        logger.error(f"Failed to create customer support workflow template: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}") 
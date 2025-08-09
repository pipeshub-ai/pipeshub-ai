from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime
import uuid
import logging
from dataclasses import asdict

from app.models.agents import (
    AgentTemplate, Agent, Tool, AIModel, AppAction, Task, Workflow,
    AgentUserEdge, AgentToolEdge, AgentModelEdge, AgentWorkflowEdge,
    TaskAgentEdge, TaskActionEdge, WorkflowTaskEdge, AgentMemoryEdge,
    MemoryType, UserRole, TaskPriority, ModelProvider, ModelType,
    ToolConfig, ModelConfig, ActionConfig, MemoryConfig, ApprovalConfig
)
from app.models.users import User

logger = logging.getLogger(__name__)


class AgentService:
    """Service for managing agents, templates, tools, and workflows"""
    
    def __init__(self, db_client):
        self.db_client = db_client
        
    # Agent Template Management
    async def create_agent_template(
        self,
        name: str,
        description: str,
        start_message: str,
        system_prompt: str,
        tools: List[ToolConfig],
        models: List[ModelConfig],
        actions: List[ActionConfig],
        memory_types: List[MemoryType],
        tags: List[str],
        org_id: Optional[str] = None,
        created_by: Optional[str] = None
    ) -> AgentTemplate:
        """Create a new agent template"""
        template_key = str(uuid.uuid4())
        now = datetime.now().timestamp()
        
        template = AgentTemplate(
            _key=template_key,
            name=name,
            description=description,
            start_message=start_message,
            system_prompt=system_prompt,
            tools=tools,
            models=models,
            actions=actions,
            memory=MemoryConfig(types=memory_types),
            tags=tags,
            org_id=org_id,
            created_by=created_by,
            created_at_timestamp=now,
            updated_at_timestamp=now
        )
        
        if not template.validate():
            raise ValueError("Invalid agent template data")
            
        # Save to database
        await self.db_client.create_document("agent_templates", template.to_dict())
        logger.info(f"Created agent template: {template_key}")
        
        return template
    
    async def get_agent_template(self, template_key: str) -> Optional[AgentTemplate]:
        """Get agent template by key"""
        doc = await self.db_client.get_document("agent_templates", template_key)
        if not doc:
            return None
            
        return AgentTemplate(
            _key=doc.get("_key"),
            name=doc.get("name"),
            description=doc.get("description"),
            start_message=doc.get("startMessage"),
            system_prompt=doc.get("systemPrompt"),
            tools=[ToolConfig(**tool) for tool in doc.get("tools", [])],
            models=[ModelConfig(**model) for model in doc.get("models", [])],
            actions=[ActionConfig(**action) for action in doc.get("actions", [])],
            memory=MemoryConfig(types=[MemoryType(t) for t in doc.get("memory", {}).get("type", [])]),
            tags=doc.get("tags", []),
            org_id=doc.get("orgId"),
            is_active=doc.get("isActive", True),
            created_by=doc.get("createdBy"),
            updated_by_user_id=doc.get("updatedByUserId"),
            deleted_by_user_id=doc.get("deletedByUserId"),
            created_at_timestamp=doc.get("createdAtTimestamp"),
            updated_at_timestamp=doc.get("updatedAtTimestamp"),
            deleted_at_timestamp=doc.get("deletedAtTimestamp"),
            is_deleted=doc.get("isDeleted", False)
        )
    
    async def list_agent_templates(
        self,
        org_id: Optional[str] = None,
        is_active: bool = True
    ) -> List[AgentTemplate]:
        """List agent templates with optional filtering"""
        query = "FOR doc IN agent_templates FILTER doc.isDeleted == false"
        params = {}
        
        if org_id:
            query += " AND doc.orgId == @org_id"
            params["org_id"] = org_id
            
        if is_active is not None:
            query += " AND doc.isActive == @is_active"
            params["is_active"] = is_active
            
        query += " RETURN doc"
        
        docs = await self.db_client.execute_query(query, params)
        templates = []
        
        for doc in docs:
            template = AgentTemplate(
                _key=doc.get("_key"),
                name=doc.get("name"),
                description=doc.get("description"),
                start_message=doc.get("startMessage"),
                system_prompt=doc.get("systemPrompt"),
                tools=[ToolConfig(**tool) for tool in doc.get("tools", [])],
                models=[ModelConfig(**model) for model in doc.get("models", [])],
                actions=[ActionConfig(**action) for action in doc.get("actions", [])],
                memory=MemoryConfig(types=[MemoryType(t) for t in doc.get("memory", {}).get("type", [])]),
                tags=doc.get("tags", []),
                org_id=doc.get("orgId"),
                is_active=doc.get("isActive", True),
                created_by=doc.get("createdBy"),
                updated_by_user_id=doc.get("updatedByUserId"),
                deleted_by_user_id=doc.get("deletedByUserId"),
                created_at_timestamp=doc.get("createdAtTimestamp"),
                updated_at_timestamp=doc.get("updatedAtTimestamp"),
                deleted_at_timestamp=doc.get("deletedAtTimestamp"),
                is_deleted=doc.get("isDeleted", False)
            )
            templates.append(template)
            
        return templates
    
    # Agent Management
    async def create_agent(
        self,
        name: str,
        description: str,
        template_key: str,
        system_prompt: str,
        starting_message: str,
        selected_tools: List[str],
        selected_models: List[str],
        selected_actions: List[str],
        selected_memory_types: List[MemoryType],
        tags: List[str],
        user_id: str,
        org_id: Optional[str] = None,
        created_by: Optional[str] = None
    ) -> Tuple[Agent, AgentUserEdge]:
        """Create a new agent from template"""
        # Validate template exists
        template = await self.get_agent_template(template_key)
        if not template:
            raise ValueError(f"Agent template not found: {template_key}")
        
        # Validate selected tools, models, actions are available in template
        template_tool_names = [tool.name for tool in template.tools]
        template_model_names = [model.name for model in template.models]
        template_action_names = [action.name for action in template.actions]
        
        invalid_tools = [tool for tool in selected_tools if tool not in template_tool_names]
        invalid_models = [model for model in selected_models if model not in template_model_names]
        invalid_actions = [action for action in selected_actions if action not in template_action_names]
        
        if invalid_tools:
            raise ValueError(f"Invalid tools selected: {invalid_tools}")
        if invalid_models:
            raise ValueError(f"Invalid models selected: {invalid_models}")
        if invalid_actions:
            raise ValueError(f"Invalid actions selected: {invalid_actions}")
        
        agent_key = str(uuid.uuid4())
        now = datetime.now().timestamp()
        
        agent = Agent(
            _key=agent_key,
            name=name,
            description=description,
            template_key=template_key,
            system_prompt=system_prompt,
            starting_message=starting_message,
            selected_tools=selected_tools,
            selected_models=selected_models,
            selected_actions=selected_actions,
            selected_memory_types=selected_memory_types,
            tags=tags,
            org_id=org_id,
            created_by=created_by,
            created_at_timestamp=now,
            updated_at_timestamp=now
        )
        
        if not agent.validate():
            raise ValueError("Invalid agent data")
        
        # Create agent-user edge
        agent_user_edge = AgentUserEdge(
            _from=f"agents/{agent_key}",
            _to=f"users/{user_id}",
            role=UserRole.OWNER,
            created_at_timestamp=now
        )
        
        # Save to database
        await self.db_client.create_document("agents", agent.to_dict())
        await self.db_client.create_edge("agent_user_edges", agent_user_edge.to_dict())
        
        logger.info(f"Created agent: {agent_key} for user: {user_id}")
        
        return agent, agent_user_edge
    
    async def get_agent(self, agent_key: str) -> Optional[Agent]:
        """Get agent by key"""
        doc = await self.db_client.get_document("agents", agent_key)
        if not doc:
            return None
            
        return Agent(
            _key=doc.get("_key"),
            name=doc.get("name"),
            description=doc.get("description"),
            template_key=doc.get("templateKey"),
            system_prompt=doc.get("systemPrompt"),
            starting_message=doc.get("startingMessage"),
            selected_tools=doc.get("selectedTools", []),
            selected_models=doc.get("selectedModels", []),
            selected_actions=doc.get("selectedActions", []),
            selected_memory_types=[MemoryType(t) for t in doc.get("selectedMemoryTypes", [])],
            tags=doc.get("tags", []),
            org_id=doc.get("orgId"),
            is_active=doc.get("isActive", True),
            created_by=doc.get("createdBy"),
            updated_by_user_id=doc.get("updatedByUserId"),
            deleted_by_user_id=doc.get("deletedByUserId"),
            created_at_timestamp=doc.get("createdAtTimestamp"),
            updated_at_timestamp=doc.get("updatedAtTimestamp"),
            deleted_at_timestamp=doc.get("deletedAtTimestamp"),
            is_deleted=doc.get("isDeleted", False)
        )
    
    async def get_user_agents(self, user_id: str) -> List[Agent]:
        """Get all agents for a user"""
        query = """
        FOR edge IN agent_user_edges
        FILTER edge._to == @user_id
        FOR agent IN agents
        FILTER agent._key == edge._from
        AND agent.isDeleted == false
        RETURN agent
        """
        
        docs = await self.db_client.execute_query(query, {"user_id": f"users/{user_id}"})
        agents = []
        
        for doc in docs:
            agent = Agent(
                _key=doc.get("_key"),
                name=doc.get("name"),
                description=doc.get("description"),
                template_key=doc.get("templateKey"),
                system_prompt=doc.get("systemPrompt"),
                starting_message=doc.get("startingMessage"),
                selected_tools=doc.get("selectedTools", []),
                selected_models=doc.get("selectedModels", []),
                selected_actions=doc.get("selectedActions", []),
                selected_memory_types=[MemoryType(t) for t in doc.get("selectedMemoryTypes", [])],
                tags=doc.get("tags", []),
                org_id=doc.get("orgId"),
                is_active=doc.get("isActive", True),
                created_by=doc.get("createdBy"),
                updated_by_user_id=doc.get("updatedByUserId"),
                deleted_by_user_id=doc.get("deletedByUserId"),
                created_at_timestamp=doc.get("createdAtTimestamp"),
                updated_at_timestamp=doc.get("updatedAtTimestamp"),
                deleted_at_timestamp=doc.get("deletedAtTimestamp"),
                is_deleted=doc.get("isDeleted", False)
            )
            agents.append(agent)
            
        return agents
    
    async def share_agent(
        self,
        agent_key: str,
        user_id: str,
        role: UserRole = UserRole.MEMBER
    ) -> AgentUserEdge:
        """Share an agent with another user"""
        # Check if agent exists
        agent = await self.get_agent(agent_key)
        if not agent:
            raise ValueError(f"Agent not found: {agent_key}")
        
        # Check if user exists
        user = await self.db_client.get_document("users", user_id)
        if not user:
            raise ValueError(f"User not found: {user_id}")
        
        # Check if edge already exists
        query = """
        FOR edge IN agent_user_edges
        FILTER edge._from == @agent_id AND edge._to == @user_id
        RETURN edge
        """
        
        existing_edges = await self.db_client.execute_query(
            query,
            {
                "agent_id": f"agents/{agent_key}",
                "user_id": f"users/{user_id}"
            }
        )
        
        if existing_edges:
            raise ValueError(f"Agent already shared with user: {user_id}")
        
        # Create edge
        now = datetime.now().timestamp()
        edge = AgentUserEdge(
            _from=f"agents/{agent_key}",
            _to=f"users/{user_id}",
            role=role,
            created_at_timestamp=now
        )
        
        await self.db_client.create_edge("agent_user_edges", edge.to_dict())
        
        logger.info(f"Shared agent {agent_key} with user {user_id} as {role.value}")
        
        return edge 
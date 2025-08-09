"""
Workflow Orchestrator Service

This module provides workflow template management and task execution coordination.
Workflows are templates that define the structure and actions for agents.
"""

import logging
from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime
import uuid

from app.models.agents import (
    Workflow, Task, Agent, AppAction,
    WorkflowTaskEdge, TaskAgentEdge, TaskActionEdge,
    ApprovalConfig, TaskPriority
)
from app.services.agent_service import AgentService

logger = logging.getLogger(__name__)


class WorkflowOrchestrator:
    """Orchestrates workflow templates and task execution"""
    
    def __init__(self, db_client, agent_service: AgentService):
        self.db_client = db_client
        self.agent_service = agent_service
    
    # Workflow Template Management
    async def create_workflow_template(
        self,
        name: str,
        description: str,
        workflow_type: str,
        steps: List[Dict[str, Any]],
        triggers: List[str],
        conditions: Dict[str, Any],
        org_id: Optional[str] = None,
        is_public: bool = False,
        created_by: Optional[str] = None
    ) -> Workflow:
        """Create a new workflow template"""
        workflow_key = str(uuid.uuid4())
        now = datetime.now().timestamp()
        
        workflow = Workflow(
            _key=workflow_key,
            name=name,
            description=description,
            workflow_type=workflow_type,
            steps=steps,
            triggers=triggers,
            conditions=conditions,
            org_id=org_id,
            is_public=is_public,
            created_by=created_by,
            created_at_timestamp=now,
            updated_at_timestamp=now
        )
        
        if not workflow.validate():
            raise ValueError("Invalid workflow template data")
            
        # Save to database
        await self.db_client.create_document("workflows", workflow.to_dict())
        logger.info(f"Created workflow template: {workflow_key}")
        
        return workflow
    
    async def get_workflow_template(self, workflow_key: str) -> Optional[Workflow]:
        """Get workflow template by key"""
        doc = await self.db_client.get_document("workflows", workflow_key)
        if not doc:
            return None
            
        return Workflow(
            _key=doc.get("_key"),
            name=doc.get("name"),
            description=doc.get("description"),
            workflow_type=doc.get("workflowType"),
            steps=doc.get("steps", []),
            triggers=doc.get("triggers", []),
            conditions=doc.get("conditions", {}),
            org_id=doc.get("orgId"),
            is_public=doc.get("isPublic", False),
            created_by=doc.get("createdBy"),
            updated_by_user_id=doc.get("updatedByUserId"),
            deleted_by_user_id=doc.get("deletedByUserId"),
            created_at_timestamp=doc.get("createdAtTimestamp"),
            updated_at_timestamp=doc.get("updatedAtTimestamp"),
            deleted_at_timestamp=doc.get("deletedAtTimestamp"),
            is_deleted=doc.get("isDeleted", False)
        )
    
    async def list_workflow_templates(
        self,
        org_id: Optional[str] = None,
        workflow_type: Optional[str] = None,
        include_public: bool = True
    ) -> List[Workflow]:
        """List available workflow templates"""
        filters = {}
        if org_id:
            filters["orgId"] = org_id
        if workflow_type:
            filters["workflowType"] = workflow_type
        
        # Get org-specific workflows
        workflows = await self.db_client.list_documents("workflows", filters)
        
        # Add public workflows if requested
        if include_public:
            public_filters = {"isPublic": True}
            if workflow_type:
                public_filters["workflowType"] = workflow_type
            public_workflows = await self.db_client.list_documents("workflows", public_filters)
            workflows.extend(public_workflows)
        
        # Convert to Workflow objects
        workflow_objects = []
        for doc in workflows:
            if not doc.get("isDeleted", False):
                workflow = Workflow(
                    _key=doc.get("_key"),
                    name=doc.get("name"),
                    description=doc.get("description"),
                    workflow_type=doc.get("workflowType"),
                    steps=doc.get("steps", []),
                    triggers=doc.get("triggers", []),
                    conditions=doc.get("conditions", {}),
                    org_id=doc.get("orgId"),
                    is_public=doc.get("isPublic", False),
                    created_by=doc.get("createdBy"),
                    created_at_timestamp=doc.get("createdAtTimestamp")
                )
                workflow_objects.append(workflow)
        
        return workflow_objects
    
    # Task Management (Tasks are still stateful for execution tracking)
    async def create_task(
        self,
        name: str,
        description: str,
        workflow_template_key: Optional[str] = None,
        priority: TaskPriority = TaskPriority.MEDIUM,
        user_id: Optional[str] = None,
        org_id: Optional[str] = None
    ) -> Task:
        """Create a new task for execution"""
        task_key = str(uuid.uuid4())
        now = datetime.now().timestamp()
        
        task = Task(
            _key=task_key,
            name=name,
            description=description,
            priority=priority,
            org_id=org_id,
            created_at_timestamp=now,
            updated_at_timestamp=now
        )
        
        if not task.validate():
            raise ValueError("Invalid task data")
            
        # Save to database
        await self.db_client.create_document("tasks", task.to_dict())
        
        # Link to workflow template if provided
        if workflow_template_key:
            edge = WorkflowTaskEdge(
                _from=f"workflows/{workflow_template_key}",
                _to=f"tasks/{task_key}",
                created_at_timestamp=now
            )
            await self.db_client.create_document("workflow_task_edges", edge.to_dict())
        
        logger.info(f"Created task: {task_key}")
        return task
    
    async def get_task(self, task_key: str) -> Optional[Task]:
        """Get task by key"""
        doc = await self.db_client.get_document("tasks", task_key)
        if not doc:
            return None
            
        return Task(
            _key=doc.get("_key"),
            name=doc.get("name"),
            description=doc.get("description"),
            priority=TaskPriority(doc.get("priority", "MEDIUM")),
            org_id=doc.get("orgId"),
            created_at_timestamp=doc.get("createdAtTimestamp"),
            updated_at_timestamp=doc.get("updatedAtTimestamp"),
            deleted_at_timestamp=doc.get("deletedAtTimestamp"),
            is_deleted=doc.get("isDeleted", False)
        )
    
    async def assign_task_to_agent(
        self,
        task_key: str,
        agent_key: str
    ) -> TaskAgentEdge:
        """Assign a task to an agent"""
        now = datetime.now().timestamp()
        
        edge = TaskAgentEdge(
            _from=f"tasks/{task_key}",
            _to=f"agents/{agent_key}",
            created_at_timestamp=now
        )
        
        await self.db_client.create_document("task_agent_edges", edge.to_dict())
        logger.info(f"Assigned task {task_key} to agent {agent_key}")
        
        return edge
    
    async def assign_action_to_task(
        self,
        task_key: str,
        action_key: str,
        approvers: List[ApprovalConfig],
        reviewers: List[ApprovalConfig]
    ) -> TaskActionEdge:
        """Assign an action to a task with approval configuration"""
        now = datetime.now().timestamp()
        
        edge = TaskActionEdge(
            _from=f"tasks/{task_key}",
            _to=f"app_actions/{action_key}",
            created_at_timestamp=now,
            approvers=approvers,
            reviewers=reviewers
        )
        
        await self.db_client.create_document("task_action_edges", edge.to_dict())
        logger.info(f"Assigned action {action_key} to task {task_key}")
        
        return edge
    
    # Workflow Execution (using workflow templates)
    async def execute_workflow_template(
        self,
        workflow_template_key: str,
        agent_key: str,
        user_query: str,
        user_id: str,
        org_id: str,
        context: Dict[str, Any] = None
    ) -> str:
        """Execute a workflow template with an agent"""
        # Get workflow template
        workflow = await self.get_workflow_template(workflow_template_key)
        if not workflow:
            raise ValueError(f"Workflow template not found: {workflow_template_key}")
        
        # Create task for this execution
        task_name = f"Workflow: {workflow.name} - {user_query[:50]}..."
        task_description = f"Executing workflow '{workflow.name}' for query: {user_query}"
        
        task = await self.create_task(
            name=task_name,
            description=task_description,
            workflow_template_key=workflow_template_key,
            user_id=user_id,
            org_id=org_id
        )
        
        # Assign task to agent
        await self.assign_task_to_agent(task._key, agent_key)
        
        # Assign actions from workflow template
        for step in workflow.steps:
            if step.get("action_key"):
                approvers = [ApprovalConfig(**approver) for approver in step.get("approvers", [])]
                reviewers = [ApprovalConfig(**reviewer) for reviewer in step.get("reviewers", [])]
                
                await self.assign_action_to_task(
                    task_key=task._key,
                    action_key=step["action_key"],
                    approvers=approvers,
                    reviewers=reviewers
                )
        
        logger.info(f"Started workflow execution: {workflow.name} with task {task._key}")
        return task._key
    
    # Approval Workflow Management
    async def request_action_approval(
        self,
        task_key: str,
        action_key: str,
        requester_id: str,
        request_data: Dict[str, Any]
    ) -> str:
        """Request approval for an action execution"""
        approval_id = str(uuid.uuid4())
        now = datetime.now().timestamp()
        
        # Get task-action edge to find approvers
        edges = await self.db_client.list_documents("task_action_edges", {
            "_from": f"tasks/{task_key}",
            "_to": f"app_actions/{action_key}"
        })
        
        if not edges:
            raise ValueError(f"No action assignment found for task {task_key} and action {action_key}")
        
        edge = edges[0]
        approvers = edge.get("approvers", [])
        
        # Create approval request
        approval_request = {
            "_key": approval_id,
            "taskKey": task_key,
            "actionKey": action_key,
            "requesterId": requester_id,
            "requestData": request_data,
            "approvers": approvers,
            "status": "pending",
            "createdAtTimestamp": now,
            "updatedAtTimestamp": now
        }
        
        await self.db_client.create_document("approval_requests", approval_request)
        logger.info(f"Created approval request: {approval_id}")
        
        return approval_id
    
    async def approve_action(
        self,
        approval_id: str,
        approver_id: str,
        comments: Optional[str] = None
    ) -> bool:
        """Approve an action execution"""
        approval = await self.db_client.get_document("approval_requests", approval_id)
        if not approval:
            raise ValueError(f"Approval request not found: {approval_id}")
        
        if approval.get("status") != "pending":
            raise ValueError(f"Approval request is not pending: {approval_id}")
        
        # Update approval status
        approval["status"] = "approved"
        approval["approverId"] = approver_id
        approval["comments"] = comments
        approval["updatedAtTimestamp"] = datetime.now().timestamp()
        
        await self.db_client.update_document("approval_requests", approval_id, approval)
        logger.info(f"Approved action: {approval_id}")
        
        return True
    
    async def reject_action(
        self,
        approval_id: str,
        rejecter_id: str,
        reason: str
    ) -> bool:
        """Reject an action execution"""
        approval = await self.db_client.get_document("approval_requests", approval_id)
        if not approval:
            raise ValueError(f"Approval request not found: {approval_id}")
        
        if approval.get("status") != "pending":
            raise ValueError(f"Approval request is not pending: {approval_id}")
        
        # Update approval status
        approval["status"] = "rejected"
        approval["rejecterId"] = rejecter_id
        approval["reason"] = reason
        approval["updatedAtTimestamp"] = datetime.now().timestamp()
        
        await self.db_client.update_document("approval_requests", approval_id, approval)
        logger.info(f"Rejected action: {approval_id}")
        
        return True
    
    # Utility Methods
    async def get_workflow_templates_by_type(self, workflow_type: str) -> List[Workflow]:
        """Get workflow templates by type"""
        return await self.list_workflow_templates(workflow_type=workflow_type)
    
    async def get_public_workflow_templates(self) -> List[Workflow]:
        """Get all public workflow templates"""
        filters = {"isPublic": True}
        workflows = await self.db_client.list_documents("workflows", filters)
        
        workflow_objects = []
        for doc in workflows:
            if not doc.get("isDeleted", False):
                workflow = Workflow(
                    _key=doc.get("_key"),
                    name=doc.get("name"),
                    description=doc.get("description"),
                    workflow_type=doc.get("workflowType"),
                    steps=doc.get("steps", []),
                    triggers=doc.get("triggers", []),
                    conditions=doc.get("conditions", {}),
                    org_id=doc.get("orgId"),
                    is_public=doc.get("isPublic", False),
                    created_by=doc.get("createdBy"),
                    created_at_timestamp=doc.get("createdAtTimestamp")
                )
                workflow_objects.append(workflow)
        
        return workflow_objects 
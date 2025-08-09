"""
Basic Usage Example for Agent System

This example demonstrates how to:
1. Create an agent template
2. Create an agent from the template
3. Create a workflow with tasks
4. Execute the agent on a task
"""

import asyncio
import logging
from datetime import datetime
from typing import Dict, Any

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Mock database client for demonstration
class MockDBClient:
    def __init__(self):
        self.documents = {}
        self.edges = {}
    
    async def create_document(self, collection: str, data: Dict[str, Any]):
        doc_id = f"{collection}_{len(self.documents) + 1}"
        self.documents[doc_id] = data
        logger.info(f"Created document in {collection}: {doc_id}")
        return doc_id
    
    async def create_edge(self, collection: str, data: Dict[str, Any]):
        edge_id = f"{collection}_edge_{len(self.edges) + 1}"
        self.edges[edge_id] = data
        logger.info(f"Created edge in {collection}: {edge_id}")
        return edge_id
    
    async def get_document(self, collection: str, key: str):
        return self.documents.get(key)
    
    async def update_document(self, collection: str, key: str, data: Dict[str, Any]):
        if key in self.documents:
            self.documents[key].update(data)
            logger.info(f"Updated document {key}")
            return True
        return False
    
    async def execute_query(self, query: str, params: Dict[str, Any] = None):
        # Mock query execution
        logger.info(f"Executing query: {query}")
        return []


async def main():
    """Main example function"""
    logger.info("Starting Agent System Example")
    
    # Initialize mock database client
    db_client = MockDBClient()
    
    # Initialize services (in a real application, these would be properly injected)
    from app.services.agent_service import AgentService
    from app.services.workflow_orchestrator import WorkflowOrchestrator
    from app.agent.core.execution_engine import AgentExecutionEngine
    
    agent_service = AgentService(db_client)
    workflow_orchestrator = WorkflowOrchestrator(db_client, agent_service)
    execution_engine = AgentExecutionEngine(agent_service, workflow_orchestrator, db_client)
    
    try:
        # Step 1: Create an Agent Template
        logger.info("Step 1: Creating Agent Template")
        
        from app.models.agents import (
            ToolConfig, ModelConfig, ActionConfig, MemoryType,
            ApprovalConfig
        )
        
        # Create approval configuration
        approval_config = ApprovalConfig(
            user_ids=["manager1", "manager2"],
            user_group_ids=[],
            order=1
        )
        
        # Create agent template
        template = await agent_service.create_agent_template(
            name="Customer Support Agent",
            description="Handles customer inquiries and support tickets",
            start_message="Hello! I'm your customer support assistant. How can I help you today?",
            system_prompt="""You are a helpful customer support agent. Your role is to:
1. Listen to customer concerns
2. Search the knowledge base for relevant information
3. Provide accurate and helpful responses
4. Create support tickets when necessary
5. Escalate complex issues to human agents

Always be polite, professional, and empathetic.""",
            tools=[
                ToolConfig(
                    name="search_knowledge_base",
                    description="Search the knowledge base for relevant articles",
                    config={"max_results": 5}
                ),
                ToolConfig(
                    name="search_customer_data",
                    description="Search customer information and history",
                    config={"include_history": True}
                )
            ],
            models=[
                ModelConfig(
                    name="gpt-4",
                    role="primary",
                    provider="openai",
                    config={"temperature": 0.7, "max_tokens": 1000}
                )
            ],
            actions=[
                ActionConfig(
                    name="create_support_ticket",
                    approvers=[approval_config],
                    reviewers=[]
                ),
                ActionConfig(
                    name="escalate_to_human",
                    approvers=[],
                    reviewers=[]
                )
            ],
            memory_types=[MemoryType.CONVERSATIONS, MemoryType.KNOWLEDGE_BASE],
            tags=["support", "customer-service", "helpdesk"],
            org_id="org123",
            created_by="admin"
        )
        
        logger.info(f"Created template: {template._key}")
        
        # Step 2: Create an Agent from Template
        logger.info("Step 2: Creating Agent from Template")
        
        agent, edge = await agent_service.create_agent(
            name="My Customer Support Agent",
            description="Personal customer support agent for handling inquiries",
            template_key=template._key,
            system_prompt="""You are my personal customer support agent. You have access to:
- Knowledge base search
- Customer data search
- Support ticket creation
- Human escalation

Focus on providing quick, accurate responses and only escalate when necessary.""",
            starting_message="Hi! I'm your dedicated customer support assistant. What can I help you with today?",
            selected_tools=["search_knowledge_base", "search_customer_data"],
            selected_models=["gpt-4"],
            selected_actions=["create_support_ticket", "escalate_to_human"],
            selected_memory_types=[MemoryType.CONVERSATIONS],
            tags=["personal", "support"],
            user_id="user123",
            org_id="org123",
            created_by="user123"
        )
        
        logger.info(f"Created agent: {agent._key}")
        
        # Step 3: Create Tools
        logger.info("Step 3: Creating Tools")
        
        # Create knowledge base search tool
        kb_tool = await agent_service.create_tool(
            name="search_knowledge_base",
            vendor_name="Internal",
            description="Search the knowledge base for relevant articles",
            org_id="org123",
            created_by_user_id="admin"
        )
        
        # Create customer data search tool
        customer_tool = await agent_service.create_tool(
            name="search_customer_data",
            vendor_name="Internal",
            description="Search customer information and history",
            org_id="org123",
            created_by_user_id="admin"
        )
        
        logger.info(f"Created tools: {kb_tool._key}, {customer_tool._key}")
        
        # Step 4: Create AI Model
        logger.info("Step 4: Creating AI Model")
        
        from app.models.agents import ModelProvider, ModelType
        
        ai_model = await agent_service.create_ai_model(
            name="gpt-4",
            description="OpenAI GPT-4 for natural language processing",
            provider=ModelProvider.OPENAI,
            model_type=ModelType.LLM,
            model_key="gpt-4",
            org_id="org123"
        )
        
        logger.info(f"Created AI model: {ai_model._key}")
        
        # Step 5: Create App Actions
        logger.info("Step 5: Creating App Actions")
        
        ticket_action = await agent_service.create_app_action(
            name="create_support_ticket",
            description="Create a new support ticket",
            org_id="org123"
        )
        
        escalate_action = await agent_service.create_app_action(
            name="escalate_to_human",
            description="Escalate issue to human agent",
            org_id="org123"
        )
        
        logger.info(f"Created actions: {ticket_action._key}, {escalate_action._key}")
        
        # Step 6: Create Workflow
        logger.info("Step 6: Creating Workflow")
        
        workflow = await workflow_orchestrator.create_workflow(
            name="Customer Inquiry Resolution",
            description="Handle customer inquiries and resolve issues",
            org_id="org123",
            created_by="user123"
        )
        
        logger.info(f"Created workflow: {workflow._key}")
        
        # Step 7: Create Tasks
        logger.info("Step 7: Creating Tasks")
        
        from app.models.agents import TaskPriority
        
        # Create task for handling customer inquiry
        inquiry_task = await workflow_orchestrator.create_task(
            name="Handle Customer Inquiry",
            description="Process and respond to customer inquiry",
            priority=TaskPriority.HIGH,
            org_id="org123"
        )
        
        # Create task for follow-up
        followup_task = await workflow_orchestrator.create_task(
            name="Follow-up with Customer",
            description="Follow up with customer after initial response",
            priority=TaskPriority.MEDIUM,
            org_id="org123"
        )
        
        logger.info(f"Created tasks: {inquiry_task._key}, {followup_task._key}")
        
        # Step 8: Add Tasks to Workflow
        logger.info("Step 8: Adding Tasks to Workflow")
        
        await workflow_orchestrator.add_task_to_workflow(workflow._key, inquiry_task._key)
        await workflow_orchestrator.add_task_to_workflow(workflow._key, followup_task._key)
        
        logger.info("Added tasks to workflow")
        
        # Step 9: Assign Tasks to Agent
        logger.info("Step 9: Assigning Tasks to Agent")
        
        await workflow_orchestrator.assign_task_to_agent(inquiry_task._key, agent._key)
        await workflow_orchestrator.assign_task_to_agent(followup_task._key, agent._key)
        
        logger.info("Assigned tasks to agent")
        
        # Step 10: Assign Actions to Tasks
        logger.info("Step 10: Assigning Actions to Tasks")
        
        await workflow_orchestrator.assign_action_to_task(
            task_key=inquiry_task._key,
            action_key=ticket_action._key,
            approvers=[approval_config],
            reviewers=[]
        )
        
        await workflow_orchestrator.assign_action_to_task(
            task_key=inquiry_task._key,
            action_key=escalate_action._key,
            approvers=[],
            reviewers=[]
        )
        
        logger.info("Assigned actions to tasks")
        
        # Step 11: Start Workflow
        logger.info("Step 11: Starting Workflow")
        
        success = await workflow_orchestrator.start_workflow(workflow._key)
        
        if success:
            logger.info("Workflow started successfully")
        else:
            logger.error("Failed to start workflow")
        
        # Step 12: Start Agent Execution
        logger.info("Step 12: Starting Agent Execution")
        
        execution_id = await execution_engine.start_agent_execution(
            agent_key=agent._key,
            task_key=inquiry_task._key,
            initial_message="Customer John Doe is having trouble with their account login. Please help them resolve this issue."
        )
        
        logger.info(f"Started agent execution: {execution_id}")
        
        # Step 13: Monitor Execution
        logger.info("Step 13: Monitoring Execution")
        
        # Simulate some time passing
        await asyncio.sleep(2)
        
        status = await execution_engine.get_execution_status(execution_id)
        if status:
            logger.info(f"Execution status: {status['context_summary']}")
            logger.info(f"Log entries: {len(status['execution_log'])}")
        else:
            logger.warning("Could not get execution status")
        
        # Step 14: Send Message to Agent
        logger.info("Step 14: Sending Message to Agent")
        
        success = await execution_engine.send_message(
            execution_id,
            "The customer mentioned they're getting a 'password expired' error message."
        )
        
        if success:
            logger.info("Message sent successfully")
        else:
            logger.error("Failed to send message")
        
        # Step 15: Get Final Status
        logger.info("Step 15: Getting Final Status")
        
        await asyncio.sleep(1)
        
        final_status = await execution_engine.get_execution_status(execution_id)
        if final_status:
            logger.info("Final execution summary:")
            logger.info(f"  - Agent: {final_status['agent_id']}")
            logger.info(f"  - Task: {final_status['task_id']}")
            logger.info(f"  - Active: {final_status['is_active']}")
            logger.info(f"  - Log entries: {len(final_status['execution_log'])}")
            
            # Show last few log entries
            for entry in final_status['execution_log'][-3:]:
                logger.info(f"  - {entry['timestamp']}: {entry['message']}")
        
        # Step 16: Stop Execution
        logger.info("Step 16: Stopping Execution")
        
        success = await execution_engine.stop_execution(execution_id)
        if success:
            logger.info("Execution stopped successfully")
        else:
            logger.warning("Could not stop execution")
        
        logger.info("Example completed successfully!")
        
    except Exception as e:
        logger.error(f"Error in example: {str(e)}")
        raise


if __name__ == "__main__":
    # Run the example
    asyncio.run(main()) 
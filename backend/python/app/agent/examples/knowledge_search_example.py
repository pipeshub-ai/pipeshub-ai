"""
Knowledge Search Agent Example

This example demonstrates how to create and use a knowledge search agent
with retrieval service integration for Gmail, Drive, and Knowledge Base searches.
"""

import asyncio
import logging
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class MockDBClient:
    """Mock database client for demonstration"""
    
    def __init__(self):
        self.documents = {
            "agent_templates": {},
            "agents": {},
            "tasks": {},
            "workflows": {},
            "tools": {},
            "ai_models": {},
            "app_actions": {},
            "users": {},
            "conversations": {},
            "knowledge_base": {}
        }
        self.edges = {
            "agent_user_edges": {},
            "agent_tool_edges": {},
            "agent_model_edges": {},
            "agent_workflow_edges": {},
            "task_agent_edges": {},
            "task_action_edges": {},
            "workflow_task_edges": {},
            "agent_memory_edges": {}
        }
    
    async def create_document(self, collection: str, document: dict) -> dict:
        """Create a document in the specified collection"""
        doc_key = f"{collection}_{len(self.documents[collection]) + 1}"
        document["_key"] = doc_key
        document["_id"] = f"{collection}/{doc_key}"
        self.documents[collection][doc_key] = document
        logger.info(f"Created document in {collection}: {doc_key}")
        return document
    
    async def get_document(self, collection: str, key: str) -> dict:
        """Get a document from the specified collection"""
        return self.documents[collection].get(key)
    
    async def list_documents(self, collection: str, filters: dict = None) -> list:
        """List documents from the specified collection"""
        documents = list(self.documents[collection].values())
        if filters:
            filtered_docs = []
            for doc in documents:
                match = True
                for key, value in filters.items():
                    if doc.get(key) != value:
                        match = False
                        break
                if match:
                    filtered_docs.append(doc)
            return filtered_docs
        return documents


class MockRetrievalService:
    """Mock retrieval service for demonstration"""
    
    def __init__(self):
        self.mock_data = {
            "knowledge_base": [
                {
                    "content": "Project Alpha is a machine learning initiative focused on natural language processing.",
                    "score": 0.95,
                    "metadata": {"source": "kb", "title": "Project Alpha Overview"}
                },
                {
                    "content": "The Q4 budget allocation for AI projects is $500,000.",
                    "score": 0.87,
                    "metadata": {"source": "kb", "title": "Q4 Budget Report"}
                }
            ],
            "gmail": [
                {
                    "content": "Meeting scheduled for Project Alpha review on Friday at 2 PM.",
                    "score": 0.92,
                    "metadata": {"source": "gmail", "subject": "Project Alpha Meeting"}
                }
            ],
            "drive": [
                {
                    "content": "Project Alpha technical specifications and implementation roadmap.",
                    "score": 0.89,
                    "metadata": {"source": "drive", "filename": "Project_Alpha_Specs.pdf"}
                }
            ]
        }
    
    async def search_with_filters(self, queries: list, user_id: str, org_id: str, 
                                filter_groups: dict = None, limit: int = 10,
                                arango_service=None) -> dict:
        """Mock search with filters"""
        query = queries[0] if queries else ""
        
        # Determine search type based on filter groups
        if filter_groups and "source" in filter_groups:
            sources = filter_groups["source"]
            if "gmail" in sources:
                return {
                    "searchResults": self.mock_data["gmail"][:limit],
                    "records": [],
                    "status": "SUCCESS",
                    "status_code": 200,
                    "message": f"Found {len(self.mock_data['gmail'][:limit])} Gmail results"
                }
            elif "drive" in sources:
                return {
                    "searchResults": self.mock_data["drive"][:limit],
                    "records": [],
                    "status": "SUCCESS",
                    "status_code": 200,
                    "message": f"Found {len(self.mock_data['drive'][:limit])} Drive results"
                }
        
        # Default to knowledge base search
        return {
            "searchResults": self.mock_data["knowledge_base"][:limit],
            "records": [],
            "status": "SUCCESS",
            "status_code": 200,
            "message": f"Found {len(self.mock_data['knowledge_base'][:limit])} knowledge base results"
        }


class MockArangoService:
    """Mock Arango service for demonstration"""
    
    async def get_accessible_records(self, user_id: str, org_id: str, filters: dict = None) -> list:
        """Mock accessible records"""
        return [
            {"virtualRecordId": "record_1", "recordId": "kb_1"},
            {"virtualRecordId": "record_2", "recordId": "gmail_1"},
            {"virtualRecordId": "record_3", "recordId": "drive_1"}
        ]
    
    async def get_user_by_user_id(self, user_id: str) -> dict:
        """Mock user data"""
        return {"userId": user_id, "name": "Test User", "orgId": "org_1"}


async def main():
    """Main example function"""
    logger.info("🚀 Starting Knowledge Search Agent Example")
    
    # Initialize mock services
    db_client = MockDBClient()
    retrieval_service = MockRetrievalService()
    arango_service = MockArangoService()
    
    # Import services after setting up mocks
    from app.services.agent_service import AgentService
    from app.services.workflow_orchestrator import WorkflowOrchestrator
    from app.agent.core.langgraph_executor import LangGraphAgentExecutor
    from app.models.agents import (
        ToolConfig, ModelConfig, ActionConfig, MemoryType, 
        ModelProvider, ModelType
    )
    
    # Initialize services
    agent_service = AgentService(db_client)
    workflow_orchestrator = WorkflowOrchestrator(db_client, agent_service)
    
    # Initialize LangGraph executor with retrieval service
    langgraph_executor = LangGraphAgentExecutor(
        agent_service=agent_service,
        workflow_orchestrator=workflow_orchestrator,
        db_client=db_client,
        retrieval_service=retrieval_service,
        arango_service=arango_service
    )
    
    try:
        # Step 1: Create Knowledge Search Agent Template
        logger.info("📝 Step 1: Creating Knowledge Search Agent Template")
        
        template = await agent_service.create_agent_template(
            name="Knowledge Search Agent",
            description="An AI agent specialized in searching knowledge base, Gmail, and Drive",
            start_message="Hello! I'm your knowledge search assistant. What would you like to search for?",
            system_prompt="""You are a knowledge search assistant AI agent. Your role is to:
1. Understand user queries and determine what information they need
2. Search knowledge base for relevant documents and information
3. Search Gmail for relevant emails and conversations
4. Search Google Drive for relevant documents and files
5. Synthesize information from multiple sources
6. Provide comprehensive, well-structured responses""",
            tools=[
                ToolConfig(name="knowledgebase.search", description="Search knowledge base"),
                ToolConfig(name="gmail.search", description="Search Gmail"),
                ToolConfig(name="drive.search", description="Search Google Drive")
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
                ActionConfig(name="respond_to_user", approvers=[], reviewers=[])
            ],
            memory_types=[MemoryType.KNOWLEDGE_BASE, MemoryType.APPS],
            tags=["knowledge-search", "retrieval", "search"],
            org_id="org_1",
            created_by="user_1"
        )
        
        logger.info(f"✅ Created template: {template._key}")
        
        # Step 2: Create Knowledge Search Agent Instance
        logger.info("🤖 Step 2: Creating Knowledge Search Agent Instance")
        
        agent, edge = await agent_service.create_agent(
            name="My Personal Knowledge Assistant",
            description="Personal knowledge search agent",
            template_key=template._key,
            system_prompt="You are my personal knowledge search assistant.",
            starting_message="Hi! I'm ready to help you search. What would you like to find?",
            selected_tools=["knowledgebase.search", "gmail.search", "drive.search"],
            selected_models=["gpt-4"],
            selected_actions=["respond_to_user"],
            selected_memory_types=[MemoryType.KNOWLEDGE_BASE, MemoryType.APPS],
            tags=["knowledge-search", "personal"],
            user_id="user_1",
            org_id="org_1",
            created_by="user_1"
        )
        
        logger.info(f"✅ Created agent: {agent._key}")
        
        # Step 3: Create a Workflow Template
        logger.info("📋 Step 3: Creating Knowledge Search Workflow Template")
        
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
            org_id="org_1",
            is_public=True,
            created_by="user_1"
        )
        
        logger.info(f"✅ Created workflow template: {workflow._key}")
        
        # Step 4: Execute Workflow Template with Agent
        logger.info("🔍 Step 4: Executing Workflow Template with Agent")
        
        user_query = "Find all information about Project Alpha"
        
        # Execute the workflow template with the agent
        task_key = await workflow_orchestrator.execute_workflow_template(
            workflow_template_key=workflow._key,
            agent_key=agent._key,
            user_query=user_query,
            user_id="user_1",
            org_id="org_1"
        )
        
        logger.info(f"✅ Started workflow execution with task: {task_key}")
        
        # Step 5: Execute Agent with the Task
        logger.info("🤖 Step 5: Executing Agent with Task")
        
        execution_id = await langgraph_executor.start_agent_execution(
            agent_key=agent._key,
            task_key=task_key,
            user_query=user_query,
            user_id="user_1",
            org_id="org_1"
        )
        
        logger.info(f"✅ Started agent execution: {execution_id}")
        
        # Step 6: Monitor Execution
        logger.info("📊 Step 6: Monitoring Execution")
        
        for i in range(5):
            await asyncio.sleep(2)
            
            status = await langgraph_executor.get_execution_status(execution_id)
            if status:
                logger.info(f"Status: {status['current_state']} (Iteration {status['iteration_count']})")
                
                if not status['is_active']:
                    logger.info("✅ Execution completed!")
                    break
        
        # Step 7: Demonstrate Workflow Template Reusability
        logger.info("🔄 Step 7: Demonstrating Workflow Template Reusability")
        
        # Create another agent that uses the same workflow template
        agent2, edge2 = await agent_service.create_agent(
            name="Another Knowledge Assistant",
            description="Another agent using the same workflow template",
            template_key=template._key,
            system_prompt="You are another knowledge search assistant.",
            starting_message="Hi! I'm another assistant ready to help you search.",
            selected_tools=["knowledgebase.search", "gmail.search", "drive.search"],
            selected_models=["gpt-4"],
            selected_actions=["respond_to_user"],
            selected_memory_types=[MemoryType.KNOWLEDGE_BASE, MemoryType.APPS],
            tags=["knowledge-search", "reusable"],
            user_id="user_2",
            org_id="org_1",
            created_by="user_1"
        )
        
        logger.info(f"✅ Created second agent: {agent2._key}")
        
        # Execute the same workflow template with the second agent
        task_key2 = await workflow_orchestrator.execute_workflow_template(
            workflow_template_key=workflow._key,  # Same workflow template
            agent_key=agent2._key,  # Different agent
            user_query="Find information about Project Beta",
            user_id="user_2",
            org_id="org_1"
        )
        
        logger.info(f"✅ Second agent using same workflow template with task: {task_key2}")
        
        logger.info("🎉 Knowledge Search Agent Example with Workflow Templates Completed!")
        
        # Summary
        logger.info("📋 Summary:")
        logger.info(f"  - Created 1 workflow template: {workflow._key}")
        logger.info(f"  - Created 2 agents using the same template")
        logger.info(f"  - Both agents can use the same workflow template")
        logger.info(f"  - Workflow template is reusable across multiple agents and users")
        logger.info(f"  - No workflow state is maintained - only task execution state")
        
    except Exception as e:
        logger.error(f"❌ Error: {str(e)}")
        raise


if __name__ == "__main__":
    asyncio.run(main()) 
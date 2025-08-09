"""
LangGraph Agent System Example

This example demonstrates the complete LangGraph-based agent system with:
- Real tool execution
- AI model integration
- Workflow orchestration
- Memory management
- Action execution with approval workflows
"""

import asyncio
import logging
import os
from datetime import datetime
from typing import Dict, Any

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Mock database client for standalone execution
class MockDBClient:
    def __init__(self):
        self.documents = {
            "agent_templates": {},
            "agents": {},
            "tools": {},
            "ai_models": {},
            "app_actions": {},
            "tasks": {},
            "workflows": {},
            "conversations": {},
            "knowledge_base": {},
            "app_data": {}
        }
        self.edges = {
            "agent_user_edges": {},
            "agent_tool_edges": {},
            "agent_model_edges": {},
            "workflow_task_edges": {},
            "task_agent_edges": {},
            "task_action_edges": {}
        }
    
    async def create_document(self, collection: str, data: Dict[str, Any]) -> str:
        """Create a document in a collection"""
        doc_id = f"{collection}_{len(self.documents[collection]) + 1}"
        data["_key"] = doc_id
        data["_id"] = f"{collection}/{doc_id}"
        self.documents[collection][doc_id] = data
        return doc_id
    
    async def get_document(self, collection: str, key: str) -> Dict[str, Any]:
        """Get a document by key"""
        return self.documents[collection].get(key, {})
    
    async def update_document(self, collection: str, key: str, data: Dict[str, Any]):
        """Update a document"""
        if key in self.documents[collection]:
            self.documents[collection][key].update(data)
    
    async def execute_query(self, query: str, bind_vars: Dict[str, Any] = None) -> list:
        """Execute a query (simplified mock)"""
        # This is a simplified mock - in reality, this would execute ArangoDB queries
        return []


async def main():
    """Main example function demonstrating the LangGraph agent system"""
    logger.info("Starting LangGraph Agent System Example")
    
    # Initialize components
    db_client = MockDBClient()
    
    # Import services and components
    from app.services.agent_service import AgentService
    from app.services.workflow_orchestrator import WorkflowOrchestrator
    from app.agent.core.langgraph_executor import LangGraphAgentExecutor
    from app.agent.core.tool_registry import ToolRegistry, initialize_default_tools
    from app.agent.core.model_manager import ModelManager, initialize_default_models
    from app.models.agents import (
        AgentTemplate, Agent, Tool, AIModel, AppAction, Task, Workflow,
        MemoryType, ModelProvider, ModelType, TaskPriority, UserRole,
        ToolConfig, ModelConfig, ActionConfig, MemoryConfig, ApprovalConfig
    )
    
    # Initialize services
    agent_service = AgentService(db_client)
    workflow_orchestrator = WorkflowOrchestrator(db_client, agent_service)
    langgraph_executor = LangGraphAgentExecutor(agent_service, workflow_orchestrator, db_client)
    
    # Initialize tool registry and model manager
    tool_registry = ToolRegistry()
    model_manager = ModelManager()
    
    # Initialize with default tools and models
    initialize_default_tools(tool_registry)
    initialize_default_models(model_manager)
    
    # Register tools and models with the executor
    for tool_name, tool_obj in tool_registry.tools.items():
        langgraph_executor.register_tool(tool_name, tool_obj.func, tool_obj.description)
    
    for model_name, model_config in model_manager.model_configs.items():
        langgraph_executor.register_model(model_name, model_config)
    
    try:
        # Step 1: Create an Agent Template
        logger.info("Step 1: Creating Agent Template")
        
        template = AgentTemplate(
            name="Research Assistant",
            description="An AI agent specialized in research and data analysis",
            system_prompt="""You are a research assistant AI agent. Your role is to:
1. Analyze user queries and research requirements
2. Use appropriate tools to gather information
3. Process and synthesize data
4. Provide comprehensive, well-structured responses
5. Execute actions when needed to complete research tasks

Always be thorough, accurate, and professional in your work.""",
            start_message="Hello! I'm your research assistant. I can help you with data analysis, information gathering, and research tasks. What would you like to work on?",
            tools=[
                ToolConfig(name="web_search", description="Search the web for information"),
                ToolConfig(name="calculator", description="Perform mathematical calculations"),
                ToolConfig(name="file_reader", description="Read and analyze files"),
                ToolConfig(name="database_query", description="Query databases for information")
            ],
            models=[
                ModelConfig(
                    name="gpt-4",
                    provider=ModelProvider.OPENAI,
                    model_type=ModelType.CHAT,
                    config={"temperature": 0.7, "max_tokens": 4000}
                )
            ],
            actions=[
                ActionConfig(
                    name="create_report",
                    description="Create a research report",
                    app_name="document_manager",
                    config={"template": "research_report"},
                    approval_config=ApprovalConfig(
                        requires_approval=True,
                        approvers=["manager"],
                        auto_approve=False
                    )
                ),
                ActionConfig(
                    name="send_email",
                    description="Send research findings via email",
                    app_name="email_system",
                    config={"template": "research_summary"},
                    approval_config=ApprovalConfig(
                        requires_approval=True,
                        approvers=["supervisor"],
                        auto_approve=False
                    )
                )
            ],
            memory=MemoryConfig(
                types=[MemoryType.CONVERSATIONS, MemoryType.KNOWLEDGE_BASE, MemoryType.APPS]
            ),
            tags=["research", "analysis", "data"],
            org_id="org_1",
            created_by="admin"
        )
        
        template_key = await agent_service.create_agent_template(template)
        logger.info(f"Created agent template: {template_key}")
        
        # Step 2: Create an Agent from Template
        logger.info("Step 2: Creating Agent from Template")
        
        agent, agent_user_edge = await agent_service.create_agent(
            name="My Research Assistant",
            description="Personal research assistant for data analysis",
            template_key=template_key,
            system_prompt="You are my personal research assistant. Focus on providing accurate, well-researched information.",
            starting_message="Hi! I'm ready to help with your research needs.",
            selected_tools=["web_search", "calculator", "file_reader"],
            selected_models=["gpt-4"],
            selected_actions=["create_report", "send_email"],
            selected_memory_types=[MemoryType.CONVERSATIONS, MemoryType.KNOWLEDGE_BASE],
            tags=["personal", "research"],
            user_id="user_1",
            org_id="org_1",
            created_by="user_1"
        )
        
        logger.info(f"Created agent: {agent._key}")
        
        # Step 3: Create Tools
        logger.info("Step 3: Creating Tools")
        
        tools = [
            Tool(
                name="web_search",
                description="Search the web for information",
                config=ToolConfig(name="web_search", description="Search the web for information"),
                org_id="org_1",
                created_by="admin"
            ),
            Tool(
                name="calculator",
                description="Perform mathematical calculations",
                config=ToolConfig(name="calculator", description="Perform mathematical calculations"),
                org_id="org_1",
                created_by="admin"
            ),
            Tool(
                name="file_reader",
                description="Read and analyze files",
                config=ToolConfig(name="file_reader", description="Read and analyze files"),
                org_id="org_1",
                created_by="admin"
            )
        ]
        
        for tool in tools:
            tool_key = await agent_service.create_tool(tool)
            logger.info(f"Created tool: {tool_key}")
        
        # Step 4: Create AI Models
        logger.info("Step 4: Creating AI Models")
        
        models = [
            AIModel(
                name="gpt-4",
                provider=ModelProvider.OPENAI,
                model_type=ModelType.CHAT,
                config=ModelConfig(
                    name="gpt-4",
                    provider=ModelProvider.OPENAI,
                    model_type=ModelType.CHAT,
                    config={"temperature": 0.7, "max_tokens": 4000}
                ),
                org_id="org_1",
                created_by="admin"
            )
        ]
        
        for model in models:
            model_key = await agent_service.create_ai_model(model)
            logger.info(f"Created AI model: {model_key}")
        
        # Step 5: Create App Actions
        logger.info("Step 5: Creating App Actions")
        
        actions = [
            AppAction(
                name="create_report",
                description="Create a research report",
                app_name="document_manager",
                config=ActionConfig(
                    name="create_report",
                    description="Create a research report",
                    app_name="document_manager",
                    config={"template": "research_report"},
                    approval_config=ApprovalConfig(
                        requires_approval=True,
                        approvers=["manager"],
                        auto_approve=False
                    )
                ),
                org_id="org_1",
                created_by="admin"
            ),
            AppAction(
                name="send_email",
                description="Send research findings via email",
                app_name="email_system",
                config=ActionConfig(
                    name="send_email",
                    description="Send research findings via email",
                    app_name="email_system",
                    config={"template": "research_summary"},
                    approval_config=ApprovalConfig(
                        requires_approval=True,
                        approvers=["supervisor"],
                        auto_approve=False
                    )
                ),
                org_id="org_1",
                created_by="admin"
            )
        ]
        
        for action in actions:
            action_key = await agent_service.create_app_action(action)
            logger.info(f"Created app action: {action_key}")
        
        # Step 6: Create Workflow
        logger.info("Step 6: Creating Workflow")
        
        workflow = Workflow(
            name="Research Analysis Workflow",
            description="Complete workflow for research analysis and reporting",
            status="draft",
            priority=TaskPriority.MEDIUM,
            org_id="org_1",
            created_by="user_1"
        )
        
        workflow_key = await workflow_orchestrator.create_workflow(workflow)
        logger.info(f"Created workflow: {workflow_key}")
        
        # Step 7: Create Tasks
        logger.info("Step 7: Creating Tasks")
        
        tasks = [
            Task(
                name="Data Collection",
                description="Gather relevant data and information for the research topic",
                status="pending",
                priority=TaskPriority.HIGH,
                org_id="org_1",
                created_by="user_1"
            ),
            Task(
                name="Analysis and Synthesis",
                description="Analyze collected data and synthesize findings",
                status="pending",
                priority=TaskPriority.MEDIUM,
                org_id="org_1",
                created_by="user_1"
            ),
            Task(
                name="Report Generation",
                description="Generate comprehensive research report",
                status="pending",
                priority=TaskPriority.MEDIUM,
                org_id="org_1",
                created_by="user_1"
            )
        ]
        
        task_keys = []
        for task in tasks:
            task_key = await workflow_orchestrator.create_task(task)
            task_keys.append(task_key)
            logger.info(f"Created task: {task_key}")
        
        # Step 8: Add Tasks to Workflow
        logger.info("Step 8: Adding Tasks to Workflow")
        
        for task_key in task_keys:
            await workflow_orchestrator.add_task_to_workflow(workflow_key, task_key)
            logger.info(f"Added task {task_key} to workflow {workflow_key}")
        
        # Step 9: Assign Tasks to Agent
        logger.info("Step 9: Assigning Tasks to Agent")
        
        for task_key in task_keys:
            await workflow_orchestrator.assign_task_to_agent(task_key, agent._key, UserRole.EXECUTOR)
            logger.info(f"Assigned task {task_key} to agent {agent._key}")
        
        # Step 10: Assign Actions to Tasks
        logger.info("Step 10: Assigning Actions to Tasks")
        
        # Assign create_report action to Report Generation task
        await workflow_orchestrator.assign_action_to_task(
            task_keys[2],  # Report Generation task
            "create_report",
            approvers=["manager"],
            reviewers=["supervisor"]
        )
        
        # Assign send_email action to Analysis and Synthesis task
        await workflow_orchestrator.assign_action_to_task(
            task_keys[1],  # Analysis and Synthesis task
            "send_email",
            approvers=["supervisor"],
            reviewers=["manager"]
        )
        
        logger.info("Assigned actions to tasks")
        
        # Step 11: Start Workflow
        logger.info("Step 11: Starting Workflow")
        
        await workflow_orchestrator.start_workflow(workflow_key)
        logger.info(f"Started workflow: {workflow_key}")
        
        # Step 12: Start LangGraph Agent Execution
        logger.info("Step 12: Starting LangGraph Agent Execution")
        
        # Start execution for the first task (Data Collection)
        execution_id = await langgraph_executor.start_agent_execution(
            agent_key=agent._key,
            task_key=task_keys[0],
            user_query="Research the latest trends in artificial intelligence and machine learning for 2024. Focus on emerging technologies, market adoption, and key players in the industry."
        )
        
        logger.info(f"Started agent execution: {execution_id}")
        
        # Step 13: Monitor Execution
        logger.info("Step 13: Monitoring Execution")
        
        for i in range(10):  # Monitor for up to 10 iterations
            await asyncio.sleep(2)  # Wait 2 seconds between checks
            
            status = await langgraph_executor.get_execution_status(execution_id)
            if status:
                logger.info(f"Execution status: {status['current_state']}")
                logger.info(f"Iteration: {status['iteration_count']}")
                
                if status['tool_results']:
                    logger.info(f"Tool results: {status['tool_results']}")
                
                if status['action_results']:
                    logger.info(f"Action results: {status['action_results']}")
                
                if not status['is_active']:
                    logger.info("Execution completed")
                    break
            else:
                logger.warning("Could not get execution status")
                break
        
        # Step 14: Send Additional Message to Agent
        logger.info("Step 14: Sending Additional Message")
        
        success = await langgraph_executor.send_message(
            execution_id,
            "Can you also include information about the impact of AI on job markets and future employment trends?"
        )
        
        if success:
            logger.info("Sent additional message to agent")
            
            # Monitor the additional execution
            for i in range(5):
                await asyncio.sleep(2)
                status = await langgraph_executor.get_execution_status(execution_id)
                if status and not status['is_active']:
                    logger.info("Additional execution completed")
                    break
        
        # Step 15: Get Final Status
        logger.info("Step 15: Getting Final Status")
        
        final_status = await langgraph_executor.get_execution_status(execution_id)
        if final_status:
            logger.info("Final execution status:")
            logger.info(f"  State: {final_status['current_state']}")
            logger.info(f"  Iterations: {final_status['iteration_count']}")
            logger.info(f"  Tool Results: {len(final_status['tool_results'])}")
            logger.info(f"  Action Results: {len(final_status['action_results'])}")
            logger.info(f"  Error: {final_status['error_message']}")
            
            # Show execution log
            logger.info("Execution Log:")
            for log_entry in final_status['execution_log'][-5:]:  # Last 5 entries
                logger.info(f"  {log_entry['timestamp']}: {log_entry['action']} - {log_entry.get('message', '')}")
        
        # Step 16: Stop Execution
        logger.info("Step 16: Stopping Execution")
        
        await langgraph_executor.stop_execution(execution_id)
        logger.info("Stopped execution")
        
        # Step 17: Demonstrate Tool Registration
        logger.info("Step 17: Demonstrating Custom Tool Registration")
        
        # Register a custom tool
        def custom_research_tool(topic: str, depth: str = "basic") -> str:
            """Custom research tool for specific topics"""
            return f"Research results for {topic} at {depth} depth: [Custom research data would be here]"
        
        langgraph_executor.register_tool(
            "custom_research",
            custom_research_tool,
            "Perform custom research on specific topics"
        )
        
        logger.info("Registered custom research tool")
        
        # Step 18: Test Model Capabilities
        logger.info("Step 18: Testing Model Capabilities")
        
        model_capabilities = model_manager.get_model_capabilities("gpt-4")
        logger.info(f"GPT-4 capabilities: {model_capabilities}")
        
        # Test model
        test_result = model_manager.test_model("gpt-4", "Hello, how are you?")
        logger.info(f"Model test result: {test_result}")
        
        logger.info("LangGraph Agent System Example completed successfully!")
        
    except Exception as e:
        logger.error(f"Error in example: {str(e)}")
        raise


if __name__ == "__main__":
    # Set up environment variables for API keys (if needed)
    os.environ.setdefault("OPENAI_API_KEY", "your-openai-api-key-here")
    os.environ.setdefault("ANTHROPIC_API_KEY", "your-anthropic-api-key-here")
    os.environ.setdefault("GOOGLE_API_KEY", "your-google-api-key-here")
    
    # Run the example
    asyncio.run(main()) 
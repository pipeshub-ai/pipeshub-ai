# Agent System with Knowledge Search Capabilities

This agent system provides a comprehensive AI agent platform with specialized knowledge search capabilities using your existing retrieval service. The system supports creating agents that can search across knowledge bases, Gmail, and Google Drive using LangGraph for intelligent execution.

## 🎯 Key Features

### Knowledge Search Agent
- **Multi-Source Search**: Search across knowledge base, Gmail, and Google Drive
- **Retrieval Service Integration**: Uses your existing retrieval service for semantic search
- **Intelligent Context Loading**: Automatically loads relevant context from multiple sources
- **Permission-Aware**: Respects user permissions and access controls
- **Real-Time Execution**: LangGraph-based execution with real-time status updates

### Core Capabilities
- **Agent Templates**: Create reusable agent blueprints
- **Agent Instances**: Personalized agents per user
- **Tool Integration**: Extensible tool system with retrieval-based tools
- **Memory Management**: Context-aware memory loading
- **Workflow Integration**: Task-based execution with approval workflows
- **Real-Time Monitoring**: Live execution status and progress tracking

## 🏗️ Architecture

### System Components

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Frontend      │    │   API Layer     │    │   Agent Core    │
│   (React/Vue)   │◄──►│   (FastAPI)     │◄──►│   (LangGraph)   │
└─────────────────┘    └─────────────────┘    └─────────────────┘
                                │                       │
                                ▼                       ▼
                       ┌─────────────────┐    ┌─────────────────┐
                       │   Services      │    │   Retrieval     │
                       │   (Agent,       │    │   Service       │
                       │    Workflow)    │    │   (Gmail,       │
                       └─────────────────┘    │    Drive, KB)   │
                                │              └─────────────────┘
                                ▼
                       ┌─────────────────┐
                       │   Database      │
                       │   (ArangoDB)    │
                       └─────────────────┘
```

### Workflow Template Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Workflow Templates                       │
│  (Reusable, Stateless, Shared across agents and users)     │
├─────────────────────────────────────────────────────────────┤
│ • Ticketing Agent Workflow                                  │
│ • Knowledge Search Workflow                                 │
│ • Customer Support Workflow                                 │
│ • Custom Workflows                                          │
└─────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────┐
│                    Agent Instances                          │
│  (Personalized per user, use workflow templates)           │
├─────────────────────────────────────────────────────────────┤
│ • User A's Knowledge Agent                                  │
│ • User B's Ticketing Agent                                  │
│ • User C's Support Agent                                    │
└─────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────┐
│                    Task Execution                           │
│  (Stateful, tracks individual execution progress)          │
├─────────────────────────────────────────────────────────────┤
│ • Task 1: User A's query execution                         │
│ • Task 2: User B's ticket processing                       │
│ • Task 3: User C's support request                         │
└─────────────────────────────────────────────────────────────┘
```

### Knowledge Search Flow

1. **User Query**: User sends a query to the agent
2. **Workflow Template Selection**: Agent uses a workflow template (e.g., Knowledge Search Workflow)
3. **Task Creation**: A new task is created for this specific execution
4. **Context Loading**: Agent loads relevant context from knowledge base, Gmail, and Drive
5. **Intelligent Reasoning**: LangGraph determines which tools to use
6. **Tool Execution**: Agent executes retrieval-based searches
7. **Information Synthesis**: Agent combines results from multiple sources
8. **Response Generation**: Agent provides comprehensive response

## 🚀 Quick Start

### 1. Create Knowledge Search Agent Template

```python
# Create a knowledge search agent template
template = await agent_service.create_agent_template(
    name="Knowledge Search Agent",
    description="AI agent for searching knowledge base, Gmail, and Drive",
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
    models=[ModelConfig(name="gpt-4", role="primary", provider="OPENAI")],
    actions=[ActionConfig(name="respond_to_user")],
    memory_types=[MemoryType.KNOWLEDGE_BASE, MemoryType.APPS],
    tags=["knowledge-search", "retrieval"]
)
```

### 2. Create Workflow Template

```python
# Create a knowledge search workflow template
workflow = await workflow_orchestrator.create_workflow_template(
    name="Knowledge Search Workflow",
    description="Workflow for searching and retrieving information from multiple sources",
    workflow_type="knowledge_search",
    steps=[
        {
            "step": 1,
            "name": "Analyze Query",
            "action_key": "analyze_query",
            "description": "Analyze the user query to understand intent"
        },
        {
            "step": 2,
            "name": "Search Knowledge Base",
            "action_key": "search_knowledge_base",
            "description": "Search knowledge base for relevant information"
        },
        {
            "step": 3,
            "name": "Search Gmail",
            "action_key": "search_gmail",
            "description": "Search Gmail for relevant emails"
        },
        {
            "step": 4,
            "name": "Search Drive",
            "action_key": "search_drive",
            "description": "Search Google Drive for relevant documents"
        },
        {
            "step": 5,
            "name": "Synthesize Results",
            "action_key": "synthesize_results",
            "description": "Combine and synthesize information from all sources"
        },
        {
            "step": 6,
            "name": "Generate Response",
            "action_key": "generate_response",
            "description": "Generate comprehensive response for user"
        }
    ],
    triggers=["user_query", "information_request", "research_request"],
    conditions={
        "requires_approval": False,
        "auto_assign": True,
        "notify_stakeholders": False
    },
    is_public=True
)
```

### 3. Create Agent Instance

```python
# Create a personalized agent instance
agent, edge = await agent_service.create_agent(
    name="My Personal Knowledge Assistant",
    template_key=template._key,
    system_prompt="You are my personal knowledge search assistant.",
    selected_tools=["knowledgebase.search", "gmail.search", "drive.search"],
    selected_models=["gpt-4"],
    selected_memory_types=[MemoryType.KNOWLEDGE_BASE, MemoryType.APPS],
    user_id="user_1"
)
```

### 4. Execute Workflow Template with Agent

```python
# Execute the workflow template with the agent
task_key = await workflow_orchestrator.execute_workflow_template(
    workflow_template_key=workflow._key,
    agent_key=agent._key,
    user_query="Find all information about Project Alpha",
    user_id="user_1",
    org_id="org_1"
)

# Execute the agent with the task
execution_id = await langgraph_executor.start_agent_execution(
    agent_key=agent._key,
    task_key=task_key,
    user_query="Find all information about Project Alpha",
    user_id="user_1",
    org_id="org_1"
)

# Monitor execution status
status = await langgraph_executor.get_execution_status(execution_id)
print(f"Status: {status['current_state']}")
```

## 📋 API Endpoints

### Workflow Templates

```http
POST /api/v1/agents/workflow-templates
GET /api/v1/agents/workflow-templates
GET /api/v1/agents/workflow-templates/{workflow_key}
POST /api/v1/agents/workflow-templates/{workflow_key}/execute
```

### Predefined Workflow Templates

```http
POST /api/v1/agents/workflow-templates/ticketing-agent
POST /api/v1/agents/workflow-templates/knowledge-search
POST /api/v1/agents/workflow-templates/customer-support
```

### Agent Templates

```http
POST /api/v1/agents/templates
GET /api/v1/agents/templates
GET /api/v1/agents/templates/{template_key}
```

### Agents

```http
POST /api/v1/agents/
GET /api/v1/agents/
GET /api/v1/agents/{agent_key}
```

### Knowledge Search Specific:

```http
POST /api/v1/agents/knowledge-search/templates
POST /api/v1/agents/knowledge-search/agents
```

### Agent Execution:

```http
POST /api/v1/agents/execute
GET /api/v1/agents/execute/{execution_id}/status
POST /api/v1/agents/execute/{execution_id}/stop
POST /api/v1/agents/execute/{execution_id}/message
```

## 🔧 Configuration

### Workflow Template Types

The system supports several predefined workflow template types:

#### 1. **Ticketing Agent Workflow**
- **Purpose**: Process and resolve tickets
- **Steps**: Analyze Ticket → Assign Priority → Route to Team → Update Status
- **Triggers**: new_ticket, ticket_update, escalation
- **Approval**: Required for routing decisions

#### 2. **Knowledge Search Workflow**
- **Purpose**: Search across multiple data sources
- **Steps**: Analyze Query → Search KB → Search Gmail → Search Drive → Synthesize → Generate Response
- **Triggers**: user_query, information_request, research_request
- **Approval**: Not required

#### 3. **Customer Support Workflow**
- **Purpose**: Handle customer support requests
- **Steps**: Classify Request → Check KB → Generate Response → Escalate if Needed → Update Ticket
- **Triggers**: customer_request, support_ticket, inquiry
- **Approval**: Required for escalations

### Workflow Template Reusability

```python
# Multiple agents can use the same workflow template
agent1 = await agent_service.create_agent(name="Agent 1", ...)
agent2 = await agent_service.create_agent(name="Agent 2", ...)

# Both agents use the same workflow template
task1 = await workflow_orchestrator.execute_workflow_template(
    workflow_template_key=workflow._key,
    agent_key=agent1._key,
    user_query="Query 1",
    user_id="user_1"
)

task2 = await workflow_orchestrator.execute_workflow_template(
    workflow_template_key=workflow._key,  # Same template
    agent_key=agent2._key,  # Different agent
    user_query="Query 2",
    user_id="user_2"
)
```

### Retrieval Service Integration

The system integrates with your existing retrieval service:

```python
# Initialize LangGraph executor with retrieval service
langgraph_executor = LangGraphAgentExecutor(
    agent_service=agent_service,
    workflow_orchestrator=workflow_orchestrator,
    db_client=db_client,
    retrieval_service=retrieval_service,  # Your existing service
    arango_service=arango_service         # Your existing service
)
```

### Tool Configuration

The system automatically registers retrieval-based tools:

- `knowledgebase.search`: Search knowledge base using retrieval service
- `gmail.search`: Search Gmail using retrieval service with source filter
- `drive.search`: Search Google Drive using retrieval service with source filter

### Memory Configuration

Agents can be configured with different memory types:

- `KNOWLEDGE_BASE`: Load context from knowledge base
- `APPS`: Load context from Gmail and Drive
- `CONVERSATIONS`: Store conversation history
- `ACTIVITIES`: Track agent activities

## 🧠 How Knowledge Search Works

### 1. Workflow Template Execution

When a user query is received, the system:

```python
# 1. Select appropriate workflow template
workflow = await workflow_orchestrator.get_workflow_template(workflow_key)

# 2. Create task for this specific execution
task = await workflow_orchestrator.create_task(
    name=f"Workflow: {workflow.name} - {user_query[:50]}...",
    workflow_template_key=workflow._key
)

# 3. Assign actions from workflow template
for step in workflow.steps:
    await workflow_orchestrator.assign_action_to_task(
        task_key=task._key,
        action_key=step["action_key"],
        approvers=step.get("approvers", []),
        reviewers=step.get("reviewers", [])
    )
```

### 2. Context Loading

When an agent starts execution, it automatically loads relevant context:

```python
# Load knowledge base context
if MemoryType.KNOWLEDGE_BASE in agent.selected_memory_types:
    knowledge_results = await retrieval_service.search_with_filters(
        queries=[user_query],
        user_id=user_id,
        org_id=org_id,
        filter_groups={'kb': []},  # Search all accessible KBs
        limit=5,
        arango_service=arango_service
    )
    state.memory_context["knowledge_base"] = knowledge_results["searchResults"]
```

### 3. Tool Execution

The agent can execute retrieval-based tools during reasoning:

```python
# Gmail search tool
async def gmail_search_tool(query: str, limit: int = 10) -> str:
    results = await retrieval_service.search_with_filters(
        queries=[query],
        user_id=user_id,
        org_id=org_id,
        filter_groups={'source': ['gmail']},
        limit=limit,
        arango_service=arango_service
    )
    return format_search_results(results)
```

### 4. Intelligent Reasoning

LangGraph handles the reasoning process:

```python
def thinking_node(state: AgentExecutionState) -> AgentExecutionState:
    # Create thinking prompt with context
    thinking_prompt = f"""
    TASK: {state.task.get('name')}
    USER QUERY: {state.user_query}
    
    CONTEXT:
    {format_memory_context(state.memory_context)}
    
    AVAILABLE TOOLS: {[tool.name for tool in state.available_tools]}
    
    Based on the current situation, decide what to do next:
    1. "use_tool" - if you need to search for more information
    2. "respond" - if you have enough information to respond
    """
    
    # Get AI model response and determine next action
    response = primary_model.invoke(thinking_prompt)
    next_action = parse_thinking_response(response.content, state)
    
    return state
```

## 📊 Execution Monitoring

### Real-Time Status

Monitor agent execution in real-time:

```python
status = await langgraph_executor.get_execution_status(execution_id)

# Status includes:
{
    "execution_id": "exec_123",
    "agent_id": "agent_456",
    "task_id": "task_789",
    "current_state": "thinking",
    "iteration_count": 3,
    "user_query": "Find Project Alpha information",
    "tool_results": [
        {
            "tool": "knowledgebase.search",
            "result": "Found 3 relevant documents...",
            "timestamp": "2024-01-15T10:30:00Z"
        }
    ],
    "action_results": [],
    "execution_log": [...],
    "is_active": true
}
```

### Execution States

- `initializing`: Agent is being initialized
- `thinking`: Agent is reasoning about next action
- `using_tool`: Agent is executing a tool
- `executing_action`: Agent is performing an action
- `responding`: Agent is generating final response
- `completed`: Execution is complete
- `error`: Execution encountered an error

## 🔒 Security & Permissions

### User Permissions

The system respects user permissions through the retrieval service:

```python
# Get accessible records for user
accessible_records = await arango_service.get_accessible_records(
    user_id=user_id,
    org_id=org_id,
    filters=arango_filters
)

# Build Qdrant filter with accessible records
qdrant_filter = build_qdrant_filter(org_id, accessible_virtual_record_ids)
```

### Organization Isolation

Agents and workflow templates are isolated by organization:

```python
# Workflow templates can be public or org-specific
workflow = await workflow_orchestrator.create_workflow_template(
    # ... other parameters
    org_id=org_id,  # None for public templates
    is_public=True  # Can be used by any org
)

# Agents are created with org_id
agent = await agent_service.create_agent(
    # ... other parameters
    org_id=org_id,
    user_id=user_id
)
```

## 🛠️ Development

### Running Examples

```bash
# Run the knowledge search example
cd backend/python
python -m app.agent.examples.knowledge_search_example
```

### Adding New Workflow Templates

```python
# Create a custom workflow template
workflow = await workflow_orchestrator.create_workflow_template(
    name="Custom Workflow",
    description="Custom workflow for specific use case",
    workflow_type="custom",
    steps=[
        {
            "step": 1,
            "name": "Custom Step 1",
            "action_key": "custom_action_1",
            "description": "First custom step"
        },
        {
            "step": 2,
            "name": "Custom Step 2",
            "action_key": "custom_action_2",
            "description": "Second custom step"
        }
    ],
    triggers=["custom_trigger"],
    conditions={
        "requires_approval": True,
        "auto_assign": False
    },
    is_public=False  # Org-specific
)
```

### Adding New Tools

```python
# Register a new tool
def custom_search_tool(query: str) -> str:
    # Your custom search logic
    return f"Custom search results for: {query}"

langgraph_executor.register_tool(
    "custom.search",
    custom_search_tool,
    "Custom search tool"
)
```

### Adding New Memory Types

```python
# Add new memory type to enum
class MemoryType(str, Enum):
    KNOWLEDGE_BASE = "KNOWLEDGE_BASE"
    APPS = "APPS"
    CUSTOM_SOURCE = "CUSTOM_SOURCE"  # New type

# Handle in memory loading
if MemoryType.CUSTOM_SOURCE in agent.selected_memory_types:
    # Load custom source context
    custom_results = await custom_service.search(user_query)
    state.memory_context["custom_source"] = custom_results
```

## 📈 Performance Considerations

### Caching

- Tool results are cached during execution
- Memory context is loaded once at startup
- Model responses are cached for repeated queries

### Parallel Execution

- Multiple tool executions can run in parallel
- Memory loading happens asynchronously
- Status updates are non-blocking

### Resource Management

- Execution timeouts prevent infinite loops
- Memory usage is monitored and limited
- Tool execution is rate-limited

## 🔍 Troubleshooting

### Common Issues

1. **Retrieval Service Not Available**
   - Check if retrieval service is properly initialized
   - Verify ArangoDB connection
   - Check user permissions

2. **Agent Execution Fails**
   - Check agent configuration
   - Verify tool availability
   - Check model configuration

3. **Memory Context Not Loading**
   - Verify memory types are correctly configured
   - Check retrieval service permissions
   - Verify filter groups are correct

4. **Workflow Template Not Found**
   - Check if workflow template exists
   - Verify workflow template is public or accessible to org
   - Check workflow template permissions

### Debug Mode

Enable debug logging:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

## 🤝 Contributing

1. Follow the existing code structure
2. Add comprehensive tests for new features
3. Update documentation for API changes
4. Ensure backward compatibility

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details. 
from logging import Logger
from typing import Any, Dict, List, Optional

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from typing_extensions import TypedDict

from app.config.configuration_service import ConfigurationService
from app.modules.reranker.reranker import RerankerService
from app.modules.retrieval.retrieval_service import RetrievalService
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider


class Document(TypedDict):
    page_content: str
    metadata: Dict[str, Any]

class ChatState(TypedDict):
    logger: Logger
    llm: BaseChatModel

    retrieval_service: RetrievalService
    graph_provider: IGraphDBProvider
    reranker_service: RerankerService
    config_service: ConfigurationService

    query: str
    limit: int # Number of chunks to retrieve from the vector database
    messages: List[BaseMessage]  # Changed to BaseMessage for tool calling
    previous_conversations: List[Dict[str, str]]
    quick_mode: bool  # Renamed from decompose_query to avoid conflict
    chat_mode: Optional[str]  # "quick", "standard", "analysis", "deep_research", "creative", "precise"
    filters: Optional[Dict[str, Any]]
    retrieval_mode: str

    # Query analysis results
    query_analysis: Optional[Dict[str, Any]]  # Results from query analysis

    # Original query processing (now optional)
    decomposed_queries: List[Dict[str, str]]
    rewritten_queries: List[str]
    expanded_queries: List[str]
    web_search_queries: List[str]  # Web search queries for tool calling

    # Search results (conditional)
    search_results: List[Document]
    final_results: List[Document]

    # User and org info
    user_info: Optional[Dict[str, Any]]
    org_info: Optional[Dict[str, Any]]
    response: Optional[str]
    error: Optional[Dict[str, Any]]
    org_id: str
    user_id: str
    user_email: str
    send_user_info: bool

    # Enhanced features
    system_prompt: Optional[str]  # User-defined system prompt
    instructions: Optional[str]  # Agent-specific instructions for the LLM
    timezone: Optional[str]  # User's timezone (e.g., "America/New_York")
    current_time: Optional[str]  # Current time in user's timezone (ISO 8601)
    apps: Optional[List[str]]  # List of app IDs to search in (extracted from knowledge array)
    kb: Optional[List[str]]  # List of KB record group IDs to search in (extracted from knowledge array filters)
    agent_knowledge: Optional[List[Dict[str, Any]]]
    # connector_instances: Deprecated - use toolsets instead
    tools: Optional[List[str]]  # List of tool names to enable for this agent
    output_file_path: Optional[str]  # Optional file path for saving responses
    tool_to_connector_map: Optional[Dict[str, str]]  # Mapping from app_name (tool) to connector instance ID

    # Tool calling specific fields - no ToolExecutor dependency
    pending_tool_calls: Optional[bool]  # Whether the agent has pending tool calls
    tool_results: Optional[List[Dict[str, Any]]]  # Results of current tool execution
    tool_records: Optional[List[Dict[str, Any]]]  # Full record data from tools (for citation normalization)

    # Toolset-based tool execution (NEW - replaces old connector-based system)
    tool_to_toolset_map: Optional[Dict[str, str]]  # Maps "slack.send_message" -> toolset_id
    toolset_configs: Optional[Dict[str, Dict]]  # Cached toolset auth configs from etcd: toolset_id -> config
    agent_toolsets: Optional[List[Dict]]  # Full toolset nodes loaded via graph traversal

    # Planner-based execution fields
    execution_plan: Optional[Dict[str, Any]]  # Planned execution from planner node
    planned_tool_calls: Optional[List[Dict[str, Any]]]  # List of planned tool calls to execute
    completion_data: Optional[Dict[str, Any]]  # Final completion data with citations

    # ⚡ PERFORMANCE: Cache fields (must be in TypedDict to persist between nodes!)
    _cached_agent_tools: Optional[List[Any]]  # Cached list of tool wrappers
    _cached_blocked_tools: Optional[Dict[str, int]]  # Cached dict of blocked tools
    _tool_instance_cache: Optional[Dict[str, Any]]  # Cached tool instances
    _performance_tracker: Optional[Any]  # Performance tracking object
    _cached_llm_with_tools: Optional[Any]  # ⚡ NUCLEAR: Cached LLM with bound tools (eliminates 1-2s overhead!)

    # Additional tracking fields
    successful_tool_count: Optional[int]  # Count of successful tools
    failed_tool_count: Optional[int]  # Count of failed tools
    tool_retry_count: Optional[Dict[str, int]]  # Retry count per tool
    available_tools: Optional[List[str]]  # List of available tool names
    performance_summary: Optional[Dict[str, Any]]  # Performance summary data
    all_tool_results: Optional[List[Dict[str, Any]]]  # All tool results for the session

    # Enhanced tool result tracking for better LLM context
    tool_execution_summary: Optional[Dict[str, Any]]  # Summary of what tools have been executed
    tool_data_available: Optional[Dict[str, Any]]  # What data is available from tool executions
    tool_repetition_warnings: Optional[List[str]]  # Warnings about repeated tool calls
    data_sufficiency: Optional[Dict[str, Any]]  # Analysis of whether we have sufficient data to answer the query

    # Loop detection and graceful handling
    force_final_response: Optional[bool]  # Flag to force final response instead of tool execution
    loop_detected: Optional[bool]  # Whether a loop was detected
    loop_reason: Optional[str]  # Reason for loop detection
    max_iterations: Optional[int]  # Maximum tool iteration limit

    # Web search specific fields
    web_search_results: Optional[List[Dict[str, Any]]]  # Stored web search results
    web_search_template_context: Optional[Dict[str, Any]]  # Template context for web search formatting

    # Pure registry integration - no executor
    available_tools: Optional[List[str]]  # List of all available tools from registry
    tool_configs: Optional[Dict[str, Any]]  # Tool configurations (Slack tokens, etc.)
    registry_tool_instances: Optional[Dict[str, Any]]  # Cached tool instances

    # Knowledge retrieval processing fields
    virtual_record_id_to_result: Optional[Dict[str, Dict[str, Any]]]  # Mapping for citations
    record_label_to_uuid_map: Optional[Dict[str, str]]  # Mapping from R-labels (e.g. "R1") to virtual_record_ids
    qna_message_content: Optional[Any]  # get_message_content() output (list of content items, same as chatbot)
    blob_store: Optional[Any]  # BlobStorage instance for processing results
    is_multimodal_llm: Optional[bool]  # Whether LLM supports multimodal content

    # Reflection and retry fields (for intelligent error recovery)
    reflection: Optional[Dict[str, Any]]  # Reflection analysis result from reflect_node
    reflection_decision: Optional[str]  # Decision: respond_success, respond_error, respond_clarify, retry_with_fix, continue_with_more_tools
    retry_count: int  # Current retry count (starts at 0)
    max_retries: int  # Maximum retries allowed (default 1 for speed)
    is_retry: bool  # Whether this is a retry iteration
    execution_errors: Optional[List[Dict[str, Any]]]  # Error details for retry context

    # Multi-step iteration tracking (separate from error retries)
    iteration_count: int  # Current iteration count for multi-step tasks (starts at 0)
    max_iterations: int  # Maximum iterations allowed for multi-step tasks (default 3)
    is_continue: bool  # Whether this is a continue iteration (multi-step task)
    tool_validation_retry_count: int  # Retry count for tool validation in planner

def _build_tool_to_toolset_map(toolsets: List[Dict[str, Any]]) -> Dict[str, str]:
    """
    Build a mapping from tool full name to synthetic toolset ID.

    Maps tool names like "slack.send_message" to their parent toolset ID
    in synthetic format: {user_id}_{normalized_toolset_type}

    This matches the etcd path structure: /services/toolsets/{user_id}/{normalized_toolset_type}

    Args:
        toolsets: List of toolset objects with tools array

    Returns:
        Dictionary mapping tool full name to synthetic toolset ID ({user_id}_{normalized_toolset_type})
    """
    if not toolsets:
        return {}

    # Import normalization function
    from app.agents.constants.toolset_constants import normalize_app_name

    tool_to_toolset = {}
    for toolset in toolsets:
        if isinstance(toolset, dict):
            toolset_name = toolset.get("name", "")
            user_id = toolset.get("userId", "")
            tools = toolset.get("tools", [])
            selected_tools = toolset.get("selectedTools", [])

            # Create synthetic toolset ID: {user_id}_{normalized_toolset_type}
            # Normalize to match etcd path structure
            if not user_id or not toolset_name:
                continue

            normalized_toolset_name = normalize_app_name(toolset_name)
            synthetic_toolset_id = f"{user_id}_{normalized_toolset_name}"

            # Map tools from expanded tools array
            for tool in tools:
                if isinstance(tool, dict):
                    full_name = tool.get("fullName") or f"{toolset_name}.{tool.get('name', '')}"
                    if full_name:
                        tool_to_toolset[full_name] = synthetic_toolset_id

            # Map tools from selectedTools if no expanded tools
            if not tools and selected_tools:
                for tool_name in selected_tools:
                    full_name = tool_name if "." in tool_name else f"{toolset_name}.{tool_name}"
                    tool_to_toolset[full_name] = synthetic_toolset_id

    return tool_to_toolset


def _extract_tools_from_toolsets(toolsets: List[Dict[str, Any]]) -> List[str]:
    """
    Extract list of tool full names from toolsets.

    Args:
        toolsets: List of toolset objects with tools array

    Returns:
        List of tool full names like ["slack.send_message", "jira.create_issue"]
    """
    if not toolsets:
        return []

    tools = []
    for toolset in toolsets:
        if isinstance(toolset, dict):
            toolset_name = toolset.get("name", "")
            toolset_tools = toolset.get("tools", [])
            selected_tools = toolset.get("selectedTools", [])

            # Extract from expanded tools array
            for tool in toolset_tools:
                if isinstance(tool, dict):
                    # Try fullName first, then construct from toolName (schema field), then fallback to 'name'
                    full_name = tool.get("fullName") or f"{toolset_name}.{tool.get('toolName') or tool.get('name', '')}"
                    if full_name:
                        tools.append(full_name)

            # Extract from selectedTools if no expanded tools
            if not toolset_tools and selected_tools:
                for tool_name in selected_tools:
                    full_name = tool_name if "." in tool_name else f"{toolset_name}.{tool_name}"
                    tools.append(full_name)

    return tools


def _extract_knowledge_connector_ids(knowledge: List[Dict[str, Any]]) -> List[str]:
    """
    Extract connector IDs from knowledge array for retrieval filtering.

    Args:
        knowledge: List of knowledge objects with connectorId and filters

    Returns:
        List of connector IDs to use for knowledge retrieval
    """
    if not knowledge:
        return []

    connector_ids = []
    for k in knowledge:
        if isinstance(k, dict):
            connector_id = k.get("connectorId")
            if connector_id:
                connector_ids.append(connector_id)

    return connector_ids


def _extract_kb_record_groups(knowledge: List[Dict[str, Any]]) -> List[str]:
    """
    Extract KB record group IDs from knowledge array filters.

    KBs are stored in knowledge array with filters containing recordGroups.
    This function extracts all record group IDs that represent KBs.

    Args:
        knowledge: List of knowledge objects with connectorId and filters

    Returns:
        List of KB record group IDs to use for retrieval filtering
    """
    if not knowledge:
        return []

    kb_record_groups = []
    for k in knowledge:
        if isinstance(k, dict):
            filters = k.get("filters", {})
            if isinstance(filters, str):
                try:
                    import json
                    filters = json.loads(filters)
                except (json.JSONDecodeError, ValueError):
                    filters = {}

            if isinstance(filters, dict):
                record_groups = filters.get("recordGroups", [])
                if record_groups and isinstance(record_groups, list):
                    kb_record_groups.extend(record_groups)

    return kb_record_groups


def cleanup_state_after_retrieval(state: ChatState) -> None:
    """
    Clean up state after retrieval phase to reduce memory pollution.
    Removes temporary fields that are no longer needed.
    """
    # Clean up intermediate query processing fields after retrieval
    if state.get("final_results") is not None:
        # These were only needed for retrieval phase
        state["decomposed_queries"] = []
        state["rewritten_queries"] = []
        state["expanded_queries"] = []
        state["web_search_queries"] = []
        state["search_results"] = []  # Keep only final_results

    # Clean up query analysis after it's been used
    if state.get("query_analysis") is not None:
        # Keep only essential info, remove verbose analysis
        analysis = state.get("query_analysis", {})
        state["query_analysis"] = {
            "intent": analysis.get("intent"),
            "complexity": analysis.get("complexity"),
            "needs_tools": analysis.get("needs_tools", False)
        }


def cleanup_old_tool_results(state: ChatState, keep_last_n: int = 10) -> None:
    """
    Clean up old tool results to prevent context pollution.
    Keeps only recent results that are relevant for current conversation.
    """
    all_results = state.get("all_tool_results", [])

    if len(all_results) > keep_last_n:
        # Keep only the last N tool results
        state["all_tool_results"] = all_results[-keep_last_n:]

        # Clear old summaries and warnings
        state["tool_execution_summary"] = {}
        state["tool_repetition_warnings"] = []


def build_initial_state(chat_query: Dict[str, Any], user_info: Dict[str, Any], llm: BaseChatModel,
                        logger: Logger, retrieval_service: RetrievalService, graph_provider: IGraphDBProvider,
                        reranker_service: RerankerService, config_service: ConfigurationService, org_info: Dict[str, Any] = None) -> ChatState:
    """
    Build the initial state from the chat query and user info.

    Uses the new graph-based toolsets and knowledge format:
    - toolsets: Array of toolset objects with nested tools
    - knowledge: Array of knowledge objects with connectorId and filters

    The tools list is extracted from toolsets for the planner.
    Knowledge connector IDs are used for retrieval filtering.
    """

    # Get user-defined system prompt or use default
    system_prompt = chat_query.get("systemPrompt", "You are an enterprise questions answering expert")
    instructions = chat_query.get("instructions", None)
    timezone = chat_query.get("timezone", None)
    current_time = chat_query.get("currentTime", None)
    output_file_path = chat_query.get("outputFilePath", None)

    # Get toolsets and knowledge from the new graph-based format
    toolsets = chat_query.get("toolsets", [])  # Array of toolset objects with tools
    knowledge = chat_query.get("knowledge", [])  # Array of knowledge objects with connectorId, filters

    # Extract tools list from toolsets for the planner
    tools = _extract_tools_from_toolsets(toolsets) if toolsets else chat_query.get("tools", None)

    # Build tool-to-toolset mapping for looking up configs during execution
    tool_to_toolset_map = _build_tool_to_toolset_map(toolsets) if toolsets else {}

    # Get toolset configs from the chat query (pre-loaded from etcd by the endpoint)
    toolset_configs = chat_query.get("toolsetConfigs", {})

    # Build filters based on knowledge and other criteria
    filters = chat_query.get("filters", {})

    # Extract apps (connector IDs) from knowledge for retrieval
    apps = _extract_knowledge_connector_ids(knowledge) if knowledge else filters.get("apps", None)
    # Extract KB record groups from knowledge array filters (new format)
    kb = _extract_kb_record_groups(knowledge) if knowledge else filters.get("kb", None)
    # Store the original knowledge array in state so tool_system and nodes can check it
    agent_knowledge = knowledge if knowledge else []

    logger.debug(f"toolsets: {len(toolsets)} loaded")
    logger.debug(f"knowledge: {len(knowledge)} sources")
    logger.debug(f"apps (from knowledge): {apps}")
    logger.debug(f"kb: {kb}")
    logger.debug(f"tools (from toolsets): {tools}")
    logger.debug(f"tool_to_toolset_map: {tool_to_toolset_map}")

    return {
        "query": chat_query.get("query", ""),
        "limit": chat_query.get("limit", 50),
        "messages": [],  # Will be populated in prepare_prompt_node
        "previous_conversations": chat_query.get("previous_conversations") or chat_query.get("previousConversations") or [],
        "quick_mode": chat_query.get("quickMode", False),
        "filters": filters,
        "retrieval_mode": chat_query.get("retrievalMode", "HYBRID"),
        "chat_mode": chat_query.get("chatMode", "standard"),

        # Query analysis (will be populated by analyze_query_node)
        "query_analysis": None,

        # Original query processing (now optional - may not be used)
        "decomposed_queries": [],
        "rewritten_queries": [],
        "expanded_queries": [],
        "web_search_queries": [],

        # Search results (conditional)
        "search_results": [],
        "final_results": [],

        # User and response data
        "user_info": user_info,
        "org_info": org_info or None,
        "response": None,
        "error": None,
        "org_id": user_info.get("orgId", ""),
        "user_id": user_info.get("userId", ""),
        "user_email": user_info.get("userEmail", ""),
        "send_user_info": user_info.get("sendUserInfo", True),
        "llm": llm,
        "logger": logger,
        "retrieval_service": retrieval_service,
        "graph_provider": graph_provider,
        "reranker_service": reranker_service,
        "config_service": config_service,

        # Enhanced features - using new graph-based format
        "system_prompt": system_prompt,
        "instructions": instructions,
        "timezone": timezone,
        "current_time": current_time,
        "apps": apps,  # Extracted from knowledge connector IDs
        "kb": kb,
        "agent_knowledge": agent_knowledge,
        # connector_instances: Deprecated - use toolsets instead
        "tools": tools,  # Extracted from toolsets
        "output_file_path": output_file_path,
        "tool_to_connector_map": None,  # Deprecated - use tool_to_toolset_map

        # Tool calling specific fields - direct execution
        "pending_tool_calls": False,
        "tool_results": None,
        "all_tool_results": [],

        # Planner-based execution fields
        "execution_plan": None,
        "planned_tool_calls": [],
        "completion_data": None,

        # Enhanced tool result tracking
        "tool_execution_summary": {},
        "tool_data_available": {},
        "tool_repetition_warnings": [],
        "data_sufficiency": {},

        # Loop detection and graceful handling
        "force_final_response": False,
        "loop_detected": False,
        "loop_reason": None,

        # Web search specific fields
        "web_search_results": None,
        "web_search_template_context": None,

        # Pure registry integration - no executor dependency
        "available_tools": None,
        "tool_configs": None,
        "registry_tool_instances": {},

        # Toolset-based tool execution (graph-based)
        "tool_to_toolset_map": tool_to_toolset_map,
        "toolset_configs": toolset_configs,
        "agent_toolsets": toolsets,

        # Knowledge retrieval processing fields
        "virtual_record_id_to_result": {},
        "record_label_to_uuid_map": {},
        "qna_message_content": None,
        "blob_store": None,
        "is_multimodal_llm": False,

        # Reflection and retry fields (for intelligent error recovery)
        "reflection": None,
        "reflection_decision": None,
        "retry_count": 0,
        "max_retries": 1,
        "is_retry": False,
        "execution_errors": [],

        # Multi-step iteration tracking
        "iteration_count": 0,
        "max_iterations": 3,
        "is_continue": False,
        "tool_validation_retry_count": 0,
    }

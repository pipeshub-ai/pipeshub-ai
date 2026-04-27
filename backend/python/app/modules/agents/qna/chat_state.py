from logging import Logger
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from typing_extensions import TypedDict

from app.utils.execute_query import agent_knowledge_has_sql_connector
from app.config.configuration_service import ConfigurationService
from app.modules.reranker.reranker import RerankerService
from app.modules.retrieval.retrieval_service import RetrievalService
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.aimodels import llm_likely_accepts_vision_content
from app.utils.chat_helpers import CitationRefMapper

# Default persona when the UI does not supply systemPrompt (keep in sync with API defaults).
DEFAULT_AGENT_SYSTEM_PROMPT = "You are an enterprise questions answering expert"


def is_custom_agent_system_prompt(system_prompt: str | None) -> bool:
    """True when the workspace supplied a persona distinct from the default placeholder."""
    s = (system_prompt or "").strip()
    return bool(s) and s != DEFAULT_AGENT_SYSTEM_PROMPT


class Document(TypedDict):
    page_content: str
    metadata: dict[str, Any]


class ChatState(TypedDict):
    logger: Logger
    llm: BaseChatModel

    retrieval_service: RetrievalService
    graph_provider: IGraphDBProvider
    reranker_service: RerankerService
    config_service: ConfigurationService

    model_name: str | None
    model_key: str | None

    query: str
    limit: int # Number of chunks to retrieve from the vector database
    messages: list[BaseMessage]  # Changed to BaseMessage for tool calling
    previous_conversations: list[dict[str, str]]
    quick_mode: bool  # Renamed from decompose_query to avoid conflict
    chat_mode: str | None  # "quick", "standard", "analysis", "deep_research", "creative", "precise"
    filters: dict[str, Any] | None
    retrieval_mode: str
    graph_type: str

    # Query analysis results
    query_analysis: dict[str, Any] | None  # Results from query analysis

    # Original query processing (now optional)
    decomposed_queries: list[dict[str, str]]
    rewritten_queries: list[str]
    expanded_queries: list[str]
    web_search_queries: list[str]  # Web search queries for tool calling

    # Search results (conditional)
    search_results: list[Document]
    final_results: list[Document]

    # User and org info
    user_info: dict[str, Any] | None
    org_info: dict[str, Any] | None
    response: str | None
    error: dict[str, Any] | None
    org_id: str
    user_id: str
    user_email: str
    send_user_info: bool
    conversation_id: str | None

    # Enhanced features
    system_prompt: str | None  # User-defined system prompt
    instructions: str | None  # Agent-specific instructions for the LLM
    timezone: str | None  # User's timezone (e.g., "America/New_York")
    current_time: str | None  # Current time in user's timezone (ISO 8601)
    apps: list[str] | None  # List of app IDs to search in (extracted from knowledge array)
    kb: list[str] | None  # List of KB record group IDs to search in (extracted from knowledge array filters)
    agent_knowledge: list[dict[str, Any]] | None
    connector_configs: dict[str, Any] | None  # Per-connector sync/indexing filter values from etcd (route pre-fetch)
    has_knowledge: bool  # Whether the agent has real knowledge sources configured (excludes NO_KB_SELECTED sentinel)
    # connector_instances: Deprecated - use toolsets instead
    tools: list[str] | None  # List of tool names to enable for this agent
    output_file_path: str | None  # Optional file path for saving responses
    tool_to_connector_map: dict[str, str] | None  # Mapping from app_name (tool) to connector instance ID

    # Tool calling specific fields - no ToolExecutor dependency
    pending_tool_calls: bool | None  # Whether the agent has pending tool calls
    tool_results: list[dict[str, Any]] | None  # Results of current tool execution
    tool_records: list[dict[str, Any]] | None  # Full record data from tools (for citation normalization)

    # Toolset-based tool execution (NEW - replaces old connector-based system)
    # SECURITY NOTE: These fields contain sensitive authentication data and should be handled carefully
    tool_to_toolset_map: dict[str, str] | None  # Maps "tool.name" -> instanceId (e.g. "slack.send_message" -> "uuid")
    toolset_configs: dict[str, dict] | None  # SENSITIVE: Auth configs keyed by instanceId (contains credentials)
    agent_toolsets: list[dict] | None  # Toolset metadata from graph (instanceId, name, tools) - NO userId stored

    # Planner-based execution fields
    execution_plan: dict[str, Any] | None  # Planned execution from planner node
    planned_tool_calls: list[dict[str, Any]] | None  # List of planned tool calls to execute
    executed_tool_names: list[str] | None  # Accumulates tool names executed across iterations
    completion_data: dict[str, Any] | None  # Final completion data with citations

    # ⚡ PERFORMANCE: Cache fields (must be in TypedDict to persist between nodes!)
    _cached_agent_tools: list[Any] | None  # Cached list of tool wrappers
    _cached_blocked_tools: dict[str, int] | None  # Cached dict of blocked tools
    _tool_instance_cache: dict[str, Any] | None  # Cached tool instances
    _performance_tracker: Any | None  # Performance tracking object
    _cached_llm_with_tools: Any | None  # ⚡ NUCLEAR: Cached LLM with bound tools (eliminates 1-2s overhead!)

    # Additional tracking fields
    successful_tool_count: int | None  # Count of successful tools
    failed_tool_count: int | None  # Count of failed tools
    tool_retry_count: dict[str, int] | None  # Retry count per tool
    available_tools: list[str] | None  # List of available tool names
    performance_summary: dict[str, Any] | None  # Performance summary data
    all_tool_results: list[dict[str, Any]] | None  # All tool results for the session

    # Enhanced tool result tracking for better LLM context
    tool_execution_summary: dict[str, Any] | None  # Summary of what tools have been executed
    tool_data_available: dict[str, Any] | None  # What data is available from tool executions
    tool_repetition_warnings: list[str] | None  # Warnings about repeated tool calls
    data_sufficiency: dict[str, Any] | None  # Analysis of whether we have sufficient data to answer the query

    # Loop detection and graceful handling
    force_final_response: bool | None  # Flag to force final response instead of tool execution
    loop_detected: bool | None  # Whether a loop was detected
    loop_reason: str | None  # Reason for loop detection
    max_iterations: int | None  # Maximum tool iteration limit

    # Web search specific fields
    web_search_results: list[dict[str, Any]] | None  # Stored web search results
    web_search_template_context: dict[str, Any] | None  # Template context for web search formatting

    # Pure registry integration - no executor
    available_tools: list[str] | None  # List of all available tools from registry
    tool_configs: dict[str, Any] | None  # Tool configurations (Slack tokens, etc.)
    registry_tool_instances: dict[str, Any] | None  # Cached tool instances

    # True when the request uses a service identity JWT (e.g. Slack bot). Knowledge
    # retrieval still applies normal graph permissions using the agent creator's userId
    # (set in agent chat routes), not unscoped org-wide record access.
    is_service_account: bool

    # Placeholder agent flag: when True, knowledge retrieval uses all configured connectors/KBs
    is_placeholder_agent: bool

    # Knowledge retrieval processing fields
    virtual_record_id_to_result: dict[str, dict[str, Any]] | None  # Mapping for citations
    record_label_to_uuid_map: dict[str, str] | None  # Mapping from R-labels (e.g. "R1") to virtual_record_ids
    qna_message_content: Any | None  # get_message_content() output (list of content items, same as chatbot)
    blob_store: Any | None  # BlobStorage instance for processing results
    is_multimodal_llm: bool | None  # Whether LLM supports multimodal content
    citation_ref_mapper: CitationRefMapper | None  # Bidirectional mapping between tiny refs (ref1, ref2) and full block web URLs

    # Reflection and retry fields (for intelligent error recovery)
    reflection: dict[str, Any] | None  # Reflection analysis result from reflect_node
    reflection_decision: str | None  # Decision: respond_success, respond_error, respond_clarify, retry_with_fix, continue_with_more_tools
    retry_count: int  # Current retry count (starts at 0)
    max_retries: int  # Maximum retries allowed (default 1 for speed)
    is_retry: bool  # Whether this is a retry iteration
    execution_errors: list[dict[str, Any]] | None  # Error details for retry context

    # Multi-step iteration tracking (separate from error retries)
    iteration_count: int  # Current iteration count for multi-step tasks (starts at 0)
    is_continue: bool  # Whether this is a continue iteration (multi-step task)
    tool_validation_retry_count: int  # Retry count for tool validation in planner
    has_sql_connector: bool  # True when org has at least one configured SQL connector
    has_sql_knowledge: bool  # True when agent_knowledge contains a SQL connector (POSTGRESQL/SNOWFLAKE/MARIADB)

def _build_tool_to_toolset_map(toolsets: list[dict[str, Any]]) -> dict[str, str]:
    """
    Build a mapping from tool full name to toolset instance ID.

    Maps tool names like "slack.send_message" to their parent toolset instance ID.
    The instance ID is used to look up toolset configs in the state.

    Security: The toolset nodes from the graph DB only contain instanceId, not userId.
    The userId always comes from the authenticated request context.

    Args:
        toolsets: List of toolset objects with tools array and instanceId

    Returns:
        Dictionary mapping tool full name to toolset instanceId
    """
    if not toolsets:
        return {}

    tool_to_toolset = {}
    for toolset in toolsets:
        if isinstance(toolset, dict):
            toolset_name = toolset.get("name", "")
            instance_id = toolset.get("instanceId", "")
            tools = toolset.get("tools", [])
            selected_tools = toolset.get("selectedTools", [])

            # Require instanceId - if missing, skip this toolset
            if not instance_id:
                continue

            # Map tools from expanded tools array
            for tool in tools:
                if isinstance(tool, dict):
                    full_name = tool.get("fullName") or f"{toolset_name}.{tool.get('name', '')}"
                    if full_name:
                        tool_to_toolset[full_name] = instance_id

            # Map tools from selectedTools if no expanded tools
            if not tools and selected_tools:
                for tool_name in selected_tools:
                    full_name = tool_name if "." in tool_name else f"{toolset_name}.{tool_name}"
                    tool_to_toolset[full_name] = instance_id

    return tool_to_toolset


def _extract_tools_from_toolsets(toolsets: list[dict[str, Any]]) -> list[str]:
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


def _extract_knowledge_connector_ids(knowledge: list[dict[str, Any]]) -> list[str]:
    """
    Extract connector IDs from knowledge array for retrieval filtering.

    Args:
        knowledge: List of knowledge objects with connectorId and filters

    Returns:
        List of connector IDs to use for knowledge retrieval
    """
    if not knowledge:
        return []

    connector_ids: list[str] = []
    seen: set[str] = set()
    for k in knowledge:
        if isinstance(k, dict):
            connector_id = k.get("connectorId")
            if (
                connector_id
                and isinstance(connector_id, str)
                and not connector_id.startswith("knowledgeBase_")
                and connector_id not in seen
            ):
                seen.add(connector_id)
                connector_ids.append(connector_id)

    return connector_ids


def _extract_kb_record_groups(knowledge: list[dict[str, Any]]) -> list[str]:
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


def build_initial_state(chat_query: dict[str, Any], user_info: dict[str, Any], llm: BaseChatModel,
                        logger: Logger, retrieval_service: RetrievalService, graph_provider: IGraphDBProvider,
                        reranker_service: RerankerService, config_service: ConfigurationService, model_name: str, model_key: str, org_info: dict[str, Any] = None, graph_type: str = "legacy", *, has_sql_connector: bool) -> ChatState:
    """
    Build the initial state from the chat query and user info.

    Uses the new graph-based toolsets and knowledge format:
    - toolsets: Array of toolset objects with nested tools
    - knowledge: Array of knowledge objects with connectorId and filters

    The tools list is extracted from toolsets for the planner.
    Knowledge connector IDs are used for retrieval filtering.

    has_sql_connector is a required keyword arg: callers must resolve it (via
    has_sql_connector_configured) before building state. This pairs with
    has_sql_knowledge to gate the execute_sql_query tool in tool_system.
    """

    # Get user-defined system prompt or use default
    system_prompt = chat_query.get("systemPrompt", DEFAULT_AGENT_SYSTEM_PROMPT)
    instructions = chat_query.get("instructions")
    timezone = chat_query.get("timezone")
    current_time = chat_query.get("currentTime")
    output_file_path = chat_query.get("outputFilePath")

    # Get toolsets and knowledge from the new graph-based format
    toolsets = chat_query.get("toolsets", [])  # Array of toolset objects with tools
    knowledge = chat_query.get("knowledge", [])  # Array of knowledge objects with connectorId, filters

    # Extract tools list from toolsets for the planner
    tools = _extract_tools_from_toolsets(toolsets) if toolsets else chat_query.get("tools")

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

    # Compute has_knowledge once — filters out the NO_KB_SELECTED sentinel
    real_kb = [k for k in (kb or []) if k and k != "NO_KB_SELECTED"]
    has_knowledge = bool(real_kb or apps or agent_knowledge)
    
    has_sql_knowledge = agent_knowledge_has_sql_connector(agent_knowledge)

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
        "graph_type": graph_type,

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
        "conversation_id": chat_query.get("conversationId"),
        "llm": llm,
        "logger": logger,
        "retrieval_service": retrieval_service,
        "graph_provider": graph_provider,
        "reranker_service": reranker_service,
        "config_service": config_service,
        "model_name": model_name,
        "model_key": model_key,

        # Enhanced features - using new graph-based format
        "system_prompt": system_prompt,
        "instructions": instructions,
        "timezone": timezone,
        "current_time": current_time,
        "apps": apps,  # Extracted from knowledge connector IDs
        "kb": kb,
        "agent_knowledge": agent_knowledge,
        "connector_configs": chat_query.get("connector_configs") or {},
        "has_knowledge": has_knowledge,
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

        # Service account flag
        "is_service_account": bool(chat_query.get("is_service_account", False)),
        "is_placeholder_agent": bool(
            chat_query.get("isPlaceholderAgent", False)
        ),

        # Knowledge retrieval processing fields
        "virtual_record_id_to_result": {},
        "record_label_to_uuid_map": {},
        "qna_message_content": None,
        "blob_store": None,
        "is_multimodal_llm": llm_likely_accepts_vision_content(llm),
        "citation_ref_mapper": None,

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
        "has_sql_connector": has_sql_connector,
        "has_sql_knowledge": has_sql_knowledge,
    }

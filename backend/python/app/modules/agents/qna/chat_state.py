from logging import Logger
from typing import Any, Dict, List, Optional

from langchain.chat_models.base import BaseChatModel
from langchain_core.messages import BaseMessage
from typing_extensions import TypedDict

from app.connectors.services.base_arango_service import BaseArangoService
from app.modules.reranker.reranker import RerankerService
from app.modules.retrieval.retrieval_service import RetrievalService


class Document(TypedDict):
    page_content: str
    metadata: Dict[str, Any]

class ChatState(TypedDict):
    logger: Logger
    llm: BaseChatModel

    retrieval_service: RetrievalService
    arango_service: BaseArangoService
    reranker_service: RerankerService

    query: str
    limit: int # Number of chunks to retrieve from the vector database
    messages: List[BaseMessage]  # Changed to BaseMessage for tool calling
    previous_conversations: List[Dict[str, str]]
    quick_mode: bool  # Renamed from decompose_query to avoid conflict
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
    apps: Optional[List[str]]  # List of app IDs to search in
    kb: Optional[List[str]]  # List of KB IDs to search in
    tools: Optional[List[str]]  # List of tool names to enable for this agent
    output_file_path: Optional[str]  # Optional file path for saving responses

    # Tool calling specific fields - no ToolExecutor dependency
    pending_tool_calls: Optional[bool]  # Whether the agent has pending tool calls
    tool_results: Optional[List[Dict[str, Any]]]  # Results of current tool execution
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
                        logger: Logger, retrieval_service: RetrievalService, arango_service: BaseArangoService,
                        reranker_service: RerankerService, org_info: Dict[str, Any] = None) -> ChatState:
    """Build the initial state from the chat query and user info"""

    # Get user-defined system prompt or use default
    system_prompt = chat_query.get("systemPrompt", "You are an enterprise questions answering expert")

    # Get tools configuration - no restrictions, let LLM decide
    tools = chat_query.get("tools", None)  # None means all tools available
    output_file_path = chat_query.get("outputFilePath", None)

    # Build filters based on allowed apps and knowledge bases
    filters = chat_query.get("filters", {})
    apps = filters.get("apps", None)
    kb = filters.get("kb", None)

    logger.debug(f"apps: {apps}")
    logger.debug(f"kb: {kb}")
    logger.debug(f"tools: {tools}")
    logger.debug(f"output_file_path: {output_file_path}")

    return {
        "query": chat_query.get("query", ""),
        "limit": chat_query.get("limit", 50),
        "messages": [],  # Will be populated in prepare_prompt_node
        "previous_conversations": chat_query.get("previousConversations", []),
        "quick_mode": chat_query.get("quickMode", False),  # Renamed
        "filters": filters,
        "retrieval_mode": chat_query.get("retrievalMode", "HYBRID"),

        # Query analysis (will be populated by analyze_query_node)
        "query_analysis": None,

        # Original query processing (now optional - may not be used)
        "decomposed_queries": [],
        "rewritten_queries": [],
        "expanded_queries": [],
        "web_search_queries": [], # Initialize web_search_queries

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
        "arango_service": arango_service,
        "reranker_service": reranker_service,

        # Enhanced features
        "system_prompt": system_prompt,
        "apps": apps,
        "kb": kb,
        "tools": tools,
        "output_file_path": output_file_path,

        # Tool calling specific fields - direct execution
        "pending_tool_calls": False,
        "tool_results": None,
        "all_tool_results": [],

        # Enhanced tool result tracking
        "tool_execution_summary": {},
        "tool_data_available": {},
        "tool_repetition_warnings": [],
        "data_sufficiency": {},

        # Loop detection and graceful handling
        "force_final_response": False,
        "max_iterations": 30,  # Maximum tool iteration limit
        "loop_detected": False,
        "loop_reason": None,

        # Web search specific fields
        "web_search_results": None,
        "web_search_template_context": None,

        # Pure registry integration - no executor dependency
        "available_tools": None,
        "tool_configs": None,
        "registry_tool_instances": {},  # Cache for tool instances
    }

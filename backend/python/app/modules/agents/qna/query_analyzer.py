"""
Query Analyzer Module

Analyzes user queries to determine:
1. Query complexity (simple vs complex)
2. Follow-up detection (using conversation memory)
3. Internal data requirements (knowledge base retrieval needs)

This analysis drives the agent's execution strategy.
"""

import logging
import re
from typing import Dict, List

from langgraph.types import StreamWriter

from app.modules.agents.qna.chat_state import ChatState
from app.modules.agents.qna.conversation_memory import ConversationMemory
from app.modules.agents.qna.performance_tracker import get_performance_tracker

logger = logging.getLogger(__name__)


# Pattern definitions for query analysis
FOLLOW_UP_PATTERNS = [
    "tell me more", "what about", "and the", "also", "additionally",
    "the second", "the first", "the third", "next one", "previous",
    "can you elaborate", "more details", "explain further", "what else",
    "continue", "go on", "expand on", "about that", "about it",
    "more info", "details on"
]

PRONOUN_PATTERNS = ["it", "that", "those", "these", "them", "this"]

COMPLEXITY_INDICATORS = {
    "multi_step": ["and then", "after that", "followed by", "once you", "first", "then", "finally", "next"],
    "conditional": ["if", "unless", "in case", "when", "should", "whether"],
    "comparison": ["compare", "vs", "versus", "difference between", "better than", "contrast"],
    "aggregation": ["all", "every", "each", "summarize", "total", "average", "list"],
    "creation": ["create", "make", "generate", "build", "draft", "compose"],
    "action": ["send", "email", "notify", "schedule", "update", "delete"]
}

INTERNAL_DATA_KEYWORDS = [
    "our", "my", "company", "organization", "internal",
    "knowledge base", "documents", "files", "emails",
    "data", "records", "slack", "drive", "confluence",
    "jira", "policy", "procedure", "team", "project"
]


def detect_follow_up(query: str, previous_conversations: List[Dict]) -> bool:
    """
    Detect if the query is a follow-up to previous conversation.

    Uses both intelligent conversation memory and pattern matching.

    Args:
        query: User query text
        previous_conversations: List of previous conversation turns

    Returns:
        True if query is a follow-up
    """
    if not previous_conversations:
        return False

    query_lower = query.lower()

    # Check using intelligent conversation memory
    is_contextual_follow_up = ConversationMemory.should_reuse_tool_results(
        query,  # Use original case
        previous_conversations
    )

    if is_contextual_follow_up:
        return True

    # Check for follow-up patterns
    if any(pattern in query_lower for pattern in FOLLOW_UP_PATTERNS):
        return True

    # Check for pronouns that suggest follow-ups
    has_pronoun = any(
        f" {p} " in f" {query_lower} " or query_lower.startswith(f"{p} ")
        for p in PRONOUN_PATTERNS
    )

    return has_pronoun


def detect_complexity(query: str) -> tuple[bool, List[str]]:
    """
    Detect query complexity and types.

    Args:
        query: User query text

    Returns:
        Tuple of (is_complex, complexity_types)
    """
    query_lower = query.lower()
    detected_types = []

    for complexity_type, indicators in COMPLEXITY_INDICATORS.items():
        if any(indicator in query_lower for indicator in indicators):
            detected_types.append(complexity_type)

    is_complex = len(detected_types) > 0

    return is_complex, detected_types


def needs_internal_data(
    query: str,
    is_follow_up: bool,
    previous_conversations: List[Dict],
    filters: Dict
) -> bool:
    """
    Determine if query needs internal knowledge base retrieval.

    Args:
        query: User query text
        is_follow_up: Whether this is a follow-up query
        previous_conversations: Previous conversation turns
        filters: Query filters (kb, apps, etc.)

    Returns:
        True if internal data retrieval is needed
    """
    query_lower = query.lower()

    # Check if explicit filters are set
    has_kb_filter = bool(filters.get("kb"))
    has_app_filter = bool(filters.get("apps"))

    if has_kb_filter or has_app_filter:
        return True

    # For follow-ups, check if previous turn had internal data
    if is_follow_up and previous_conversations:
        last_response = previous_conversations[-1].get("content", "")
        # If last response had citations, might not need new retrieval
        has_citations = bool(re.search(r'\s*\[\d+\]', last_response))

        if has_citations:
            logger.info("Follow-up detected with existing citations - skipping retrieval")
            return False

    # Check for internal data keywords
    return any(keyword in query_lower for keyword in INTERNAL_DATA_KEYWORDS)


async def analyze_query_node(state: ChatState, writer: StreamWriter) -> ChatState:
    """
    Analyze query to determine complexity, follow-up status, and data needs.

    This is the first node in the agent workflow. It examines the user's query
    and previous conversation history to determine:
    1. Is this a follow-up to a previous query?
    2. How complex is the query?
    3. Does it need internal knowledge base retrieval?

    The analysis drives the rest of the execution strategy.

    Args:
        state: Current chat state
        writer: Stream writer for status updates

    Returns:
        Updated chat state with query_analysis
    """
    try:
        logger_instance = state["logger"]

        # Track performance
        perf = get_performance_tracker(state)
        perf.start_step("analyze_query_node")

        writer({
            "event": "status",
            "data": {
                "status": "analyzing",
                "message": "Analyzing your request..."
            }
        })

        query = state["query"]
        previous_conversations = state.get("previous_conversations", [])
        filters = state.get("filters", {})

        # Detect follow-up queries
        is_follow_up = detect_follow_up(query, previous_conversations)

        # Detect complexity
        is_complex, complexity_types = detect_complexity(query)

        # Determine if internal data is needed
        needs_data = needs_internal_data(
            query,
            is_follow_up,
            previous_conversations,
            filters
        )

        # Store analysis results
        state["query_analysis"] = {
            "needs_internal_data": needs_data,
            "is_follow_up": is_follow_up,
            "is_complex": is_complex,
            "complexity_types": complexity_types,
            "requires_beautiful_formatting": True,
            "reasoning": f"Follow-up: {is_follow_up}, Complex: {is_complex}, Types: {complexity_types}"
        }

        # Log analysis results
        logger_instance.info(
            f"Query analysis: follow_up={is_follow_up}, "
            f"complex={is_complex}, data_needed={needs_data}"
        )
        logger_instance.info(
            f"Follow-up detection: memory={is_follow_up}, "
            f"query='{query}', history={len(previous_conversations)} turns"
        )

        if is_complex:
            logger_instance.info(
                f"Complexity indicators: {', '.join(complexity_types)}"
            )

        # Finish performance tracking
        duration = perf.finish_step(is_complex=is_complex, needs_data=needs_data)
        logger_instance.debug(f"analyze_query_node completed in {duration:.0f}ms")

        return state

    except Exception as e:
        logger.error(f"Error in query analysis: {str(e)}", exc_info=True)
        perf.finish_step(error=True)
        state["error"] = {"status_code": 400, "detail": str(e)}
        return state


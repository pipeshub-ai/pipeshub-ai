"""
Query Analyzer Module

Analyzes user queries using LLM to determine:
1. Follow-up detection
2. Query complexity
3. Internal data requirements

This analysis drives the agent's execution strategy.
"""

import json
import logging
import re
from typing import Dict, List, Tuple

from langgraph.types import StreamWriter

from app.modules.agents.qna.chat_state import ChatState
from app.modules.agents.qna.conversation_memory import ConversationMemory
from app.modules.agents.qna.performance_tracker import get_performance_tracker

logger = logging.getLogger(__name__)


def _detect_follow_up(query: str, previous_conversations: List[Dict]) -> bool:
    """
    Minimal fallback for follow-up detection when LLM fails.
    Uses conversation memory which is reliable.
    """
    if not previous_conversations:
        return False
    return ConversationMemory.should_reuse_tool_results(query, previous_conversations)


def _detect_internal_data(filters: Dict, is_follow_up: bool, previous_conversations: List[Dict]) -> bool:
    """
    Minimal fallback for internal data needs - only checks explicit filters.
    """
    if bool(filters.get("kb")) or bool(filters.get("apps")):
        return True
    if is_follow_up and previous_conversations:
        last_response = previous_conversations[-1].get("content", "")
        if re.search(r'\[R\d+-\d+\]|\[\d+\]', last_response):
            return False
    return False


# Constants for complexity detection
_MIN_ACTION_WORDS_FOR_COMPLEXITY = 2
_MAX_TOOLS_TO_SHOW_IN_PROMPT = 10

def _detect_complexity(query: str, is_follow_up: bool) -> Tuple[bool, List[str]]:
    """
    Minimal fallback for complexity - follow-ups are always complex.
    """
    if is_follow_up:
        return True, ["follow_up"]
    # Simple heuristic: queries with multiple clauses or action words
    query_lower = query.lower()
    action_words = ["and", "then", "if", "when", "compare", "create", "send", "update"]
    has_multiple_actions = sum(1 for word in action_words if word in query_lower) >= _MIN_ACTION_WORDS_FOR_COMPLEXITY
    return has_multiple_actions, ["multi_step"] if has_multiple_actions else []


async def _llm_based_intent_analysis(query: str, state: ChatState, filters: Dict, previous_conversations: List[Dict] = None) -> tuple[bool | None, bool | None, bool | None, List[str], str]:
    """
    Use LLM as PRIMARY method to analyze query intent, complexity, follow-up status, and internal data needs.

    IMPORTANT: This analysis is for PLANNING ONLY. The agent LLM will have access to the
    retrieval tool and will decide when to actually call it. This analysis just helps
    with initial planning and context preparation.

    Args:
        query: User query text
        state: Chat state (contains LLM)
        filters: Query filters (kb, apps, etc.)
        previous_conversations: Previous conversation turns for follow-up detection

    Returns:
        Tuple of (needs_internal_data: bool | None, is_complex: bool | None, is_follow_up: bool | None, complexity_types: List[str], reason: str)
    """
    try:
        llm = state.get("llm")
        if not llm:
            logger.warning("LLM not available for intent analysis, using fallback")
            # Return default values when LLM unavailable
            return (None, None, None, [], "LLM not available")

        # Prepare conversation history context (optimized)
        conversation_context = "No previous conversation history"
        if previous_conversations:
            recent_turns = previous_conversations[-3:]  # Last 3 turns
            conversation_parts = [
                f"{turn.get('role', 'unknown')}: {turn.get('content', '')[:200]}"
                for turn in recent_turns
            ]
            conversation_context = "\n".join(conversation_parts)

        # Get available tools (cached in state if available)
        available_tools = state.get("available_tools", [])
        if not available_tools:
            try:
                from app.modules.agents.qna.tool_registry import get_agent_tools
                tools = get_agent_tools(state)
                available_tools = [tool.name for tool in tools] if tools else []
            except Exception:
                available_tools = []

        # Build concise tools info
        tools_list = ", ".join(available_tools[:_MAX_TOOLS_TO_SHOW_IN_PROMPT]) if available_tools else "Will be loaded"
        if len(available_tools) > _MAX_TOOLS_TO_SHOW_IN_PROMPT:
            tools_list += f" (and {len(available_tools) - _MAX_TOOLS_TO_SHOW_IN_PROMPT} more)"

        tools_info = f"""
AVAILABLE TOOLS: {tools_list}
NOTE: retrieval.search_internal_knowledge is ALWAYS available as an essential tool.
The agent will decide when to use tools - this analysis is for PLANNING ONLY.
"""

        # Build concise, focused prompt
        has_filters = bool(filters.get("kb")) or bool(filters.get("apps"))
        filters_info = f"Filters: KB={filters.get('kb', [])}, Apps={filters.get('apps', [])}" if has_filters else "No filters"

        intent_prompt = f"""Analyze this query and return JSON with: is_follow_up, needs_internal_data, is_complex, complexity_types, reason.

QUERY: "{query}"
HISTORY: {conversation_context}
{filters_info}{tools_info}

ANALYSIS RULES:

1. FOLLOW-UP (is_follow_up):
   TRUE if: uses pronouns (it/that/this), very short (yes/no/ok), asks for more details, references previous conversation
   FALSE if: self-contained, new topic, makes sense without history

2. INTERNAL DATA (needs_internal_data):
   TRUE if: company-specific processes/docs, explicit filters, asks about company data
   FALSE if: general knowledge, calculations, conversational, can use other tools (Slack/Jira)

3. COMPLEXITY (is_complex, complexity_types):
   TRUE if: multiple steps, conditions, comparisons, aggregations, or follow-up
   Types: multi_step, conditional, comparison, aggregation, creation, action, follow_up

BE CONSERVATIVE: Default to FALSE for needs_internal_data unless clearly company-specific.

Return ONLY valid JSON (no markdown, no code blocks):
{{"is_follow_up": bool, "needs_internal_data": bool, "is_complex": bool, "complexity_types": ["type1"], "reason": "brief reason"}}"""

        from langchain_core.messages import HumanMessage
        # Optimize LLM call with max_tokens for faster response
        try:
            # Try to bind max_tokens if LLM supports it
            if hasattr(llm, 'bind'):
                llm_optimized = llm.bind(max_tokens=500)  # Limit response for speed
                response = await llm_optimized.ainvoke([HumanMessage(content=intent_prompt)])
            else:
                response = await llm.ainvoke([HumanMessage(content=intent_prompt)])
        except Exception:
            # Fallback to regular invoke if binding fails
            response = await llm.ainvoke([HumanMessage(content=intent_prompt)])

        # Extract and clean content
        content = response.content if hasattr(response, 'content') else str(response)
        content = content.strip()

        # Remove markdown code blocks if present
        if content.startswith("```"):
            content = re.sub(r'^```(?:json|JSON)?\s*\n?', '', content, flags=re.IGNORECASE)
            content = re.sub(r'\n?```\s*$', '', content).strip()

        # Parse JSON with fallback strategies
        result = None

        # Strategy 1: Direct JSON parsing
        try:
            result = json.loads(content)
        except json.JSONDecodeError:
            # Strategy 2: Extract JSON object from text
            json_match = re.search(r'\{[^{}]*(?:"is_follow_up"|"needs_internal_data"|"is_complex")[^}]*\}', content, re.DOTALL)
            if json_match:
                try:
                    result = json.loads(json_match.group())
                except json.JSONDecodeError:
                    pass

        # Handle wrapped responses (answer object)
        if isinstance(result, dict) and "answer" in result:
            if isinstance(result["answer"], dict):
                result = result["answer"]
            elif isinstance(result["answer"], str):
                try:
                    result = json.loads(result["answer"])
                except (json.JSONDecodeError, TypeError):
                    pass

        if result and isinstance(result, dict):
            is_follow_up = bool(result.get("is_follow_up", False))
            needs_data = bool(result.get("needs_internal_data", False))
            is_complex = bool(result.get("is_complex", False))
            complexity_types = result.get("complexity_types", [])
            if not isinstance(complexity_types, list):
                complexity_types = []
            reason = result.get("reason", "LLM analysis")
            logger.debug(f"✅ LLM analysis: follow_up={is_follow_up}, needs_data={needs_data}, complex={is_complex}, types={complexity_types}")
            return (needs_data, is_complex, is_follow_up, complexity_types, reason)

        # If parsing fails, return None to trigger fallback
        logger.warning(f"⚠️ LLM response unparseable, using fallback. Preview: {content[:200]}")
        return (None, None, None, [], "LLM response unparseable")

    except Exception as e:
        logger.warning(f"⚠️ LLM analysis failed: {e}, using fallback")
        return (None, None, None, [], f"LLM error: {str(e)}")


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

        # PRIMARY METHOD: Use LLM to analyze query (all aspects in one call)
        llm_result = await _llm_based_intent_analysis(query, state, filters, previous_conversations=previous_conversations)
        needs_data, llm_is_complex, llm_is_follow_up, llm_complexity_types, llm_reason = llm_result

        # Use LLM results with minimal fallbacks
        if llm_is_follow_up is not None:
            is_follow_up = llm_is_follow_up
        else:
            is_follow_up = _detect_follow_up(query, previous_conversations)

        if needs_data is not None:
            analysis_method = "LLM"
            analysis_reason = llm_reason
        else:
            needs_data = _detect_internal_data(filters, is_follow_up, previous_conversations)
            analysis_method = "LLM_partial"
            analysis_reason = f"{llm_reason} (fallback for internal_data)"

        if llm_is_complex is not None:
            is_complex = llm_is_complex
            complexity_types = llm_complexity_types
            # Ensure follow-ups are marked as complex
            if is_follow_up and "follow_up" not in complexity_types:
                complexity_types = complexity_types + ["follow_up"]
                is_complex = True
        else:
            is_complex, complexity_types = _detect_complexity(query, is_follow_up)
            analysis_method = "LLM_partial" if analysis_method == "LLM" else "pattern_fallback"
            analysis_reason = f"{llm_reason} (fallback for complexity)" if llm_reason else "Pattern fallback"

        # Store analysis results
        # Note: needs_internal_data is for PLANNING ONLY - the agent LLM will have the retrieval tool
        # available and will make the final decision on when to call it
        state["query_analysis"] = {
            "needs_internal_data": needs_data,
            "is_follow_up": is_follow_up,
            "is_complex": is_complex,
            "complexity_types": complexity_types,
            "requires_beautiful_formatting": True,
            "analysis_method": analysis_method,
            "analysis_reason": analysis_reason,
            "reasoning": f"Method: {analysis_method}, Reason: {analysis_reason}, Follow-up: {is_follow_up}, Complex: {is_complex}. NOTE: This is planning only - agent LLM will decide when to use retrieval tool."
        }

        # Log analysis results (concise)
        logger_instance.info(
            f"Query analysis: follow_up={is_follow_up}, complex={is_complex}, "
            f"data_needed={needs_data}, method={analysis_method}, reason={analysis_reason}"
        )
        if is_complex and complexity_types:
            logger_instance.debug(f"Complexity types: {', '.join(complexity_types)}")

        # Finish performance tracking
        duration = perf.finish_step(is_complex=is_complex, needs_data=needs_data)
        logger_instance.debug(f"analyze_query_node completed in {duration:.0f}ms")

        return state

    except Exception as e:
        logger.error(f"Error in query analysis: {str(e)}", exc_info=True)
        perf.finish_step(error=True)
        state["error"] = {"status_code": 400, "detail": str(e)}
        return state


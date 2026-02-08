"""
Agent Node Implementations - Refactored

Cleaner architecture with:
- Simplified planner prompt (removed redundant essential tools concept)
- Better error handling
- Reduced branching complexity
- Clearer tool result processing
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Literal

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import StreamWriter

from app.modules.agents.qna.chat_state import ChatState
from app.modules.agents.qna.stream_utils import safe_stream_write
from app.modules.qna.response_prompt import create_response_messages
from app.utils.streaming import stream_llm_response

# Constants
STREAMING_CHUNK_DELAY = 0.015
TOOL_RESULT_TUPLE_LENGTH = 2
DESCRIPTION_MAX_LENGTH = 80
USER_QUERY_MAX_LENGTH = 300
BOT_RESPONSE_MAX_LENGTH = 800

logger = logging.getLogger(__name__)

# Opik tracer
_opik_tracer = None
_opik_api_key = os.getenv("OPIK_API_KEY")
_opik_workspace = os.getenv("OPIK_WORKSPACE")
if _opik_api_key and _opik_workspace:
    try:
        from opik.integrations.langchain import OpikTracer
        _opik_tracer = OpikTracer()
        logger.info("Opik tracer initialized")
    except Exception as e:
        logger.warning(f"Failed to initialize Opik tracer: {e}")


# ============================================================================
# Configuration
# ============================================================================

class NodeConfig:
    """Node behavior configuration"""
    MAX_PARALLEL_TOOLS: int = 10
    TOOL_TIMEOUT_SECONDS: float = 60.0
    PLANNER_TIMEOUT_SECONDS: float = 30.0
    STREAM_CHUNK_SIZE: int = 3
    STREAM_DELAY: float = 0.01


# ============================================================================
# Result Cleaner (same as before, proven logic)
# ============================================================================

REMOVE_FIELDS = {
    "self", "_links", "_embedded", "_meta", "_metadata",
    "expand", "expansions", "schema", "$schema",
    "avatarUrls", "avatarUrl", "iconUrl", "iconUri", "thumbnailUrl",
    "avatar", "icon", "thumbnail", "profilePicture",
    "nextPageToken", "prevPageToken", "pageToken",
    "cursor", "offset", "pagination",
    "startAt", "maxResults", "total",
    "trace", "traceId", "requestId", "correlationId",
    "debug", "debugInfo", "stack", "stackTrace",
    "headers", "cookies", "request", "response",
    "httpVersion", "protocol", "encoding",
    "timeZone", "timezone", "locale", "language",
    "accountType", "active", "properties",
    "hierarchyLevel", "subtask", "avatarId",
    "watches", "votes", "watchers", "voters",
    "changelog", "history", "worklog", "worklogs",
}

KEEP_FIELDS = {
    "id", "key", "name", "title", "slug",
    "content", "body", "text", "message", "description", "summary",
    "value", "data", "result", "results", "items", "issues", "files",
    "status", "state", "type", "kind", "category",
    "issuetype", "priority", "resolution",
    "assignee", "reporter", "author", "creator", "owner", "user",
    "accountId", "displayName", "emailAddress", "email",
    "created", "updated", "modified", "createdAt", "updatedAt",
    "date", "timestamp", "time", "dueDate",
    "url", "link", "href", "webUrl", "permalink",
    "count", "size", "length",
    "labels", "tags", "components",
    "parent", "children", "project", "workspace", "channel", "folder",
}


def clean_tool_result(result: object) -> object:
    """Clean tool result by removing verbose fields"""
    if isinstance(result, tuple) and len(result) == TOOL_RESULT_TUPLE_LENGTH:
        success, data = result
        return (success, clean_tool_result(data))

    if isinstance(result, str):
        try:
            parsed = json.loads(result)
            cleaned = clean_tool_result(parsed)
            return json.dumps(cleaned, indent=2, ensure_ascii=False)
        except (json.JSONDecodeError, TypeError):
            return result

    if isinstance(result, dict):
        return _clean_dict(result)

    if isinstance(result, list):
        return [clean_tool_result(item) for item in result]

    return result


def _clean_dict(data: Dict) -> Dict:
    """Clean dictionary"""
    cleaned = {}

    for key, value in data.items():
        key_lower = key.lower()

        if key in REMOVE_FIELDS or key_lower in REMOVE_FIELDS:
            continue

        if key.startswith("_") or key.startswith("$"):
            continue

        if key_lower in ("self", "resource", "api", "endpoint"):
            continue

        if isinstance(value, dict):
            cleaned_value = _clean_dict(value)
            if cleaned_value:
                cleaned[key] = cleaned_value
        elif isinstance(value, list):
            cleaned[key] = [clean_tool_result(item) for item in value]
        else:
            cleaned[key] = value

    return cleaned


def format_result_for_llm(result: object, tool_name: str = "") -> str:
    """Format result for LLM"""
    if isinstance(result, tuple) and len(result) == TOOL_RESULT_TUPLE_LENGTH:
        success, data = result
        status = "✅ Success" if success else "❌ Failed"
        content = format_result_for_llm(data, tool_name)
        return f"{status}\n{content}"

    if isinstance(result, (dict, list)):
        try:
            return json.dumps(result, indent=2, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return str(result)

    return str(result)


# ============================================================================
# Planner Prompts - Simplified (no essential tools concept)
# ============================================================================

JIRA_GUIDANCE = r"""
## JIRA-Specific Guidance

### Never Fabricate Data
- ❌ NEVER invent emails, accountIds, or user identifiers
- ✅ User email is in the prompt - use `jira.search_users(query="[USER_EMAIL]")` to get accountIds
- ✅ Use project keys from Reference Data

### JQL Syntax Rules
1. Unresolved: `resolution IS EMPTY` (NOT `resolution = Unresolved`)
2. Current user: `currentUser()` with parentheses
3. Empty fields: `IS EMPTY` or `IS NULL`
4. Text values: Use quotes: `status = "Open"`
5. Assignee: Get accountId from `jira.search_users()`, then use in JQL
6. Project: Use KEY (e.g., "PA") not name or ID

### ⚠️ CRITICAL: Unbounded Query Error

**THE FIX**: Add time filter to EVERY JQL query:
- ✅ `project = "PA" AND assignee = currentUser() AND resolution IS EMPTY AND updated >= -30d`
- ❌ `project = "PA" AND assignee = currentUser() AND resolution IS EMPTY` (UNBOUNDED!)

**Time ranges**:
- Last week: `updated >= -7d`
- Last month: `updated >= -30d`
- Last 3 months: `updated >= -90d`
- This year: `updated >= startOfYear()`

### Common Patterns (ALL with time filters)
- "My tickets": `jira.search_users(query="[EMAIL]")` → `assignee = "[accountId]" AND resolution IS EMPTY AND updated >= -30d"`
- "Recent tickets": `assignee = currentUser() AND updated >= -7d ORDER BY updated DESC`
- "Unresolved in project": `project = "[KEY]" AND resolution IS EMPTY AND updated >= -30d`
"""

PLANNER_SYSTEM_PROMPT = """You are an intelligent task planner for an enterprise AI assistant.

## Available Tools
{available_tools}

## CRITICAL - Parameter Rules
Only use parameters listed above. DO NOT invent parameters.
- Tools show exact parameters: `param_name (type, required/optional)`
- "Parameters: none" → empty args: {{"args": {{}}}}
- NEVER add unlisted parameters

## Planning Rules
1. **Internal Knowledge**: Company data, documents, policies → `retrieval.search_internal_knowledge`
2. **API Tools**: Project management, tickets → use appropriate tools (jira.*, slack.*, etc.)
3. **Direct Answer**: Greetings, simple math, general knowledge, user info queries → `can_answer_directly: true`
4. **Query Understanding**: Extract key entities, dates, context
5. **Conversation Context**: Use previous messages, reuse data (IDs, keys, etc.)

## IMPORTANT - Context Awareness
- "try again", "retry" → look at previous conversation
- "that project", "the first one" → extract IDs/keys from Reference Data
- Reuse data from previous responses
{jira_guidance}

## Slack-Specific Guidance
- ✅ Use emails: `slack.get_user_info(user="user@company.com")`
- ✅ Use IDs: `slack.get_user_info(user="U123ABC45")`
- ❌ NEVER use 24-char database IDs

## Error Recovery
1. First failure: Fix based on error (e.g., add time filter)
2. Second failure (same error): Ask user for help
3. Permission error: Inform user immediately
- ❌ DON'T retry same thing 3+ times

## When to Ask Clarification
If query is ambiguous or missing critical info, set `needs_clarification: true`.

**Need clarification**:
- "Get tickets" (no project, time, assignee AND no Reference Data)
- "Send message" (to whom? what channel?)
- "Find document" (which document? what topic?)

**Can proceed**:
- "my tickets" → Use `assignee = currentUser() AND updated >= -30d`
- "tickets for PA project" → Use project key directly
- "give info about me" (with user info in prompt) → `can_answer_directly: true`

## Output Format (JSON only)
{{
  "intent": "Brief description",
  "reasoning": "Why these tools",
  "can_answer_directly": false,
  "needs_clarification": false,
  "clarifying_question": "",
  "tools": [
    {{"name": "tool.name", "args": {{"param": "value"}}}}
  ]
}}

If `needs_clarification: true`, set `tools: []` and provide `clarifying_question`.

## Examples
- "Q4 results" → retrieval.search_internal_knowledge with {{"query": "Q4 results"}}
- "My Jira projects" → jira.get_projects with {{}}
- "My tickets in PA" → jira.search_issues with {{"jql": "project = \\"PA\\" AND assignee = currentUser() AND resolution IS EMPTY AND updated >= -30d"}}
- "Hello!" → can_answer_directly: true, tools: []
- "my name" (user info in prompt) → can_answer_directly: true, tools: []"""

PLANNER_USER_TEMPLATE = """Query: {query}

Plan the tools. Return only valid JSON."""

PLANNER_USER_TEMPLATE_WITH_CONTEXT = """## Conversation History
{conversation_history}

## Current Query
{query}

Plan the tools using conversation context. Return only valid JSON."""

# Reflection prompt (unchanged, proven logic)
REFLECT_PROMPT = """Analyze tool execution results and decide next action.

## Results
{execution_summary}

## Query
{query}

## Retry Status
Attempt: {retry_count}/{max_retries}

## Decision Options
1. **respond_success** - Tools worked
2. **respond_error** - Unrecoverable error
3. **respond_clarify** - Need user clarification
4. **retry_with_fix** - Fixable error

## Common Fixes
- "Unbounded JQL" → Add `updated >= -30d`
- "User not found" → Search users first
- "Invalid syntax" → Fix query format

## Output (JSON only)
{{
  "decision": "respond_success|respond_error|respond_clarify|retry_with_fix",
  "reasoning": "Brief explanation",
  "fix_instruction": "For retry: what to change",
  "clarifying_question": "For clarify: what to ask",
  "error_context": "For error: user-friendly explanation"
}}"""


# ============================================================================
# Helper Functions
# ============================================================================

def _has_jira_tools(state: ChatState) -> bool:
    """Check if Jira tools available"""
    agent_toolsets = state.get("agent_toolsets", [])
    for toolset in agent_toolsets:
        if isinstance(toolset, dict) and "jira" in toolset.get("name", "").lower():
            return True
    return False


def _format_user_context(state: ChatState) -> str:
    """Format user context for planner"""
    user_info = state.get("user_info", {})
    org_info = state.get("org_info", {})

    user_email = state.get("user_email") or user_info.get("userEmail") or user_info.get("email") or ""
    user_name = (
        user_info.get("fullName") or
        user_info.get("name") or
        user_info.get("displayName") or
        (f"{user_info.get('firstName', '')} {user_info.get('lastName', '')}".strip()
         if user_info.get("firstName") or user_info.get("lastName") else "")
    )

    if not user_email and not user_name:
        return ""

    parts = ["## Current User Information", ""]

    if user_name:
        parts.append(f"- **Name**: {user_name}")
    if user_email:
        parts.append(f"- **Email**: {user_email}")

    if org_info.get("accountType"):
        parts.append(f"- **Account Type**: {org_info['accountType']}")

    if user_email or user_name:
        parts.append("")
        parts.append("### How to Use:")
        parts.append("")

        if _has_jira_tools(state):
            parts.append("**For Jira (current user):**")
            parts.append("- ✅ Use `currentUser()` in JQL: `assignee = currentUser()`")
            parts.append("- ❌ DON'T call `jira.search_users` for yourself")
            parts.append("")
            parts.append("**For Jira (other users):**")
            parts.append("- Use `jira.search_users(query=\"name_or_email\")` to get accountId")
            parts.append("")

        parts.append("**General:**")
        if user_email:
            parts.append(f"- Use email ({user_email}) for user lookups")
        if user_name:
            parts.append(f"- User's name: {user_name}")
        parts.append("- **When user asks about themselves** ('my name', 'who am I', 'my info'): use this info DIRECTLY with `can_answer_directly: true` - DO NOT call tools")
        parts.append("")

    result = "\n".join(parts)
    return result if len(result.strip()) > len("## Current User Information") else ""


# ============================================================================
# Node 1: Planner
# ============================================================================

async def planner_node(
    state: ChatState,
    config: RunnableConfig,
    writer: StreamWriter
) -> ChatState:
    """LLM-driven planner"""
    start_time = time.perf_counter()
    log = state.get("logger", logger)
    llm = state.get("llm")
    query = state.get("query", "")
    previous_conversations = state.get("previous_conversations", [])

    safe_stream_write(writer, {
        "event": "status",
        "data": {"status": "planning", "message": "Planning..."}
    }, config)

    # Build prompts
    tool_descriptions = _get_cached_tool_descriptions(state, log)
    jira_guidance = JIRA_GUIDANCE if _has_jira_tools(state) else ""

    system_prompt = PLANNER_SYSTEM_PROMPT.format(
        available_tools=tool_descriptions,
        jira_guidance=jira_guidance
    )

    # User prompt with context
    if previous_conversations:
        conversation_history = _format_conversation_history(previous_conversations, log)
        user_prompt = PLANNER_USER_TEMPLATE_WITH_CONTEXT.format(
            conversation_history=conversation_history,
            query=query
        )
        log.debug(f"Using {len(previous_conversations)} previous messages")
    else:
        user_prompt = PLANNER_USER_TEMPLATE.format(query=query)

    # Add user context
    user_context = _format_user_context(state)
    if user_context:
        user_prompt = user_prompt + "\n\n" + user_context

    # Add retry context if needed
    if state.get("is_retry") and state.get("execution_errors"):
        user_prompt = _build_retry_context(state) + user_prompt
        state["is_retry"] = False
        log.info("Retry mode active")

    try:
        invoke_config = {"callbacks": [_opik_tracer]} if _opik_tracer else {}

        response = await asyncio.wait_for(
            llm.ainvoke(
                [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)],
                config=invoke_config
            ),
            timeout=20.0
        )

        plan = _parse_planner_response(
            response.content if hasattr(response, 'content') else str(response),
            log
        )
        log.info(f"Plan: {plan.get('intent', 'N/A')[:50]}, {len(plan.get('tools', []))} tools")

    except asyncio.TimeoutError:
        log.warning("Planner timeout")
        plan = _create_fallback_plan(query)
    except Exception as e:
        log.error(f"Planner error: {e}")
        plan = _create_fallback_plan(query)

    # Store plan
    state["execution_plan"] = plan
    state["planned_tool_calls"] = plan.get("tools", [])
    state["pending_tool_calls"] = bool(plan.get("tools"))
    state["query_analysis"] = {
        "intent": plan.get("intent", ""),
        "reasoning": plan.get("reasoning", ""),
        "can_answer_directly": plan.get("can_answer_directly", False),
    }

    # Handle clarification
    if plan.get("needs_clarification"):
        state["reflection_decision"] = "respond_clarify"
        state["reflection"] = {
            "decision": "respond_clarify",
            "reasoning": "Planner needs clarification",
            "clarifying_question": plan.get("clarifying_question", "Could you provide more details?")
        }
        log.info(f"Requesting clarification: {plan.get('clarifying_question', '')[:50]}...")

    duration_ms = (time.perf_counter() - start_time) * 1000
    log.info(f"⚡ Planner: {duration_ms:.0f}ms")

    return state


def _build_retry_context(state: ChatState) -> str:
    """Build retry context from previous failure"""
    errors = state.get("execution_errors", [])
    reflection = state.get("reflection", {})
    fix_instruction = reflection.get("fix_instruction", "")

    error_summary = errors[0] if errors else {}
    failed_args = error_summary.get("args", {})
    failed_args_str = json.dumps(failed_args, indent=2) if failed_args else "No args"

    return f"""## 🔴 RETRY MODE - PREVIOUS ATTEMPT FAILED

**Failed Tool**: {error_summary.get('tool_name', 'unknown')}
**Error**: {error_summary.get('error', 'unknown')[:300]}

**Previous Args That Failed**:
```json
{failed_args_str}
```

**FIX INSTRUCTION**:
{fix_instruction}

Apply the fix instruction! Don't repeat the same args.

"""


_tool_description_cache: Dict[str, str] = {}


def _get_cached_tool_descriptions(state: ChatState, log: logging.Logger) -> str:
    """Get tool descriptions with caching"""
    org_id = state.get("org_id", "default")
    agent_toolsets = state.get("agent_toolsets", [])

    # Build cache key from toolsets
    toolset_names = sorted([ts.get("name", "") for ts in agent_toolsets if isinstance(ts, dict)])
    cache_key = f"{org_id}_{hash(tuple(toolset_names))}_internal"

    if cache_key in _tool_description_cache:
        return _tool_description_cache[cache_key]

    try:
        from app.modules.agents.qna.tool_system import get_agent_tools
        tools = get_agent_tools(state)

        if not tools:
            return "- retrieval.search_internal_knowledge: Search internal knowledge base\n  Parameters: query (string, required)"

        descriptions = []
        for tool in tools[:20]:
            name = getattr(tool, 'name', str(tool))
            desc = getattr(tool, 'description', '')

            short_desc = desc[:DESCRIPTION_MAX_LENGTH] + "..." if len(desc) > DESCRIPTION_MAX_LENGTH else desc

            tool_entry = f"- {name}"
            if short_desc:
                tool_entry += f": {short_desc}"

            # Extract parameters
            params = []
            registry_tool = getattr(tool, 'registry_tool', None)
            if registry_tool and hasattr(registry_tool, 'parameters') and registry_tool.parameters:
                for param in registry_tool.parameters:
                    param_name = getattr(param, 'name', 'unknown')
                    param_type = getattr(getattr(param, 'type', None), 'name', 'string')
                    param_required = getattr(param, 'required', False)
                    req_str = "required" if param_required else "optional"
                    params.append(f"{param_name} ({param_type}, {req_str})")

            if params:
                tool_entry += f"\n  Parameters: {', '.join(params)}"
            else:
                tool_entry += "\n  Parameters: none"

            descriptions.append(tool_entry)

        result = "\n".join(descriptions)
        _tool_description_cache[cache_key] = result
        return result

    except Exception as e:
        log.warning(f"Tool load failed: {e}")
        return "- retrieval.search_internal_knowledge: Search internal knowledge base\n  Parameters: query (string, required)"


def _format_conversation_history(conversations: List[Dict], log: logging.Logger) -> str:
    """Format conversation history"""
    if not conversations:
        return ""

    recent = conversations[-5:]
    lines = []
    all_reference_data = []

    for conv in recent:
        role = conv.get("role", "")
        content = conv.get("content", "")

        if role == "user_query":
            content = content[:USER_QUERY_MAX_LENGTH] if len(content) > USER_QUERY_MAX_LENGTH else content
            lines.append(f"User: {content}")
        elif role == "bot_response":
            content = content[:BOT_RESPONSE_MAX_LENGTH] if len(content) > BOT_RESPONSE_MAX_LENGTH else content
            lines.append(f"Assistant: {content}")

            ref_data = conv.get("referenceData", [])
            if ref_data:
                all_reference_data.extend(ref_data)

    result = "\n".join(lines)

    if all_reference_data:
        result += "\n\n## Reference Data (IDs/Keys from previous responses):\n"
        for item in all_reference_data[:15]:
            name = item.get("name", "Unknown")
            item_id = item.get("id", "")
            item_key = item.get("key", "")
            item_type = item.get("type", "")
            account_id = item.get("accountId", "")

            if item_id or item_key:
                ref_parts = [f"{name} ({item_type})"]
                if item_key:
                    ref_parts.append(f"key=`{item_key}`")
                if item_id:
                    ref_parts.append(f"id=`{item_id}`")
                if account_id:
                    ref_parts.append(f"accountId=`{account_id}`")
                result += f"- {' | '.join(ref_parts)}\n"

        log.debug(f"Included {len(all_reference_data)} reference items")

    return result


def _parse_planner_response(content: str, log: logging.Logger) -> Dict[str, Any]:
    """Parse planner JSON response"""
    content = content.strip()

    if "```json" in content:
        match = re.search(r'```json\s*(.*?)\s*```', content, re.DOTALL)
        if match:
            content = match.group(1)
    elif content.startswith("```"):
        content = re.sub(r'^```\s*\n?', '', content)
        content = re.sub(r'\n?```\s*$', '', content)

    try:
        plan = json.loads(content)

        if isinstance(plan, dict):
            plan.setdefault("intent", "")
            plan.setdefault("reasoning", "")
            plan.setdefault("can_answer_directly", False)
            plan.setdefault("needs_clarification", False)
            plan.setdefault("clarifying_question", "")
            plan.setdefault("tools", [])

            # Normalize tools
            normalized_tools = []
            for tool in plan.get("tools", []):
                if isinstance(tool, dict) and "name" in tool:
                    normalized_tools.append({
                        "name": tool["name"],
                        "args": tool.get("args", {})
                    })
            plan["tools"] = normalized_tools

            return plan

    except json.JSONDecodeError as e:
        log.warning(f"Failed to parse planner response: {e}")

    return {
        "intent": "Parse failed",
        "reasoning": "Using fallback",
        "can_answer_directly": False,
        "needs_clarification": False,
        "clarifying_question": "",
        "tools": [{"name": "retrieval.search_internal_knowledge", "args": {"query": ""}}]
    }


def _create_fallback_plan(query: str) -> Dict[str, Any]:
    """Create fallback plan"""
    return {
        "intent": "Fallback: Search internal knowledge",
        "reasoning": "Planner failed, using fallback",
        "can_answer_directly": False,
        "needs_clarification": False,
        "clarifying_question": "",
        "tools": [{"name": "retrieval.search_internal_knowledge", "args": {"query": query}}]
    }


# ============================================================================
# Node 2: Execute
# ============================================================================

async def execute_node(
    state: ChatState,
    config: RunnableConfig,
    writer: StreamWriter
) -> ChatState:
    """Execute all planned tools in parallel"""
    start_time = time.perf_counter()
    log = state.get("logger", logger)

    planned_tools = state.get("planned_tool_calls", [])

    if not planned_tools:
        log.info("No tools to execute")
        state["pending_tool_calls"] = False
        return state

    safe_stream_write(writer, {
        "event": "status",
        "data": {"status": "executing", "message": f"Executing {len(planned_tools)} tool(s)..."}
    }, config)

    # Get tools
    try:
        from app.modules.agents.qna.tool_system import get_agent_tools
        tools = get_agent_tools(state)
        tools_by_name = {t.name: t for t in tools}
    except Exception as e:
        log.error(f"Failed to get tools: {e}")
        tools_by_name = {}

    # Execute in parallel
    tasks = []
    for i, tool_call in enumerate(planned_tools[:NodeConfig.MAX_PARALLEL_TOOLS]):
        tool_name = tool_call.get("name", "")
        tool_args = tool_call.get("args", {})
        tool_id = f"call_{i}_{tool_name}"

        log.debug(f"Planning {tool_name} with {json.dumps(tool_args, default=str)[:100]}...")

        if tool_name in tools_by_name:
            tasks.append(_execute_single_tool(
                tool=tools_by_name[tool_name],
                tool_name=tool_name,
                tool_args=tool_args,
                tool_id=tool_id,
                state=state,
                log=log
            ))
        else:
            log.warning(f"Tool not found: {tool_name}")
            tasks.append(_create_error_result(tool_name, tool_id, f"Tool '{tool_name}' not found"))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Process results
    tool_results = []
    tool_messages = []
    success_count = 0
    failed_count = 0

    for result in results:
        if isinstance(result, Exception):
            log.error(f"Tool execution exception: {result}")
            continue

        if isinstance(result, dict):
            tool_result = result.get("tool_result", {})
            tool_results.append(tool_result)

            if tool_result.get("status") == "success":
                success_count += 1
            elif tool_result.get("status") == "error":
                failed_count += 1

            if "tool_message" in result:
                tool_messages.append(result["tool_message"])

    # Update state
    state["tool_results"] = tool_results
    state["all_tool_results"] = tool_results

    if not state.get("messages"):
        state["messages"] = []
    state["messages"].extend(tool_messages)

    state["pending_tool_calls"] = False

    duration_ms = (time.perf_counter() - start_time) * 1000
    log.info(f"✅ Executed {len(tool_results)} tools in {duration_ms:.0f}ms ({success_count} succeeded)")

    return state


async def _execute_single_tool(
    tool: object,
    tool_name: str,
    tool_args: Dict,
    tool_id: str,
    state: ChatState,
    log: logging.Logger
) -> Dict[str, Any]:
    """Execute single tool with timeout"""
    start_time = time.perf_counter()

    try:
        # Normalize args
        if isinstance(tool_args, dict) and "kwargs" in tool_args and len(tool_args) == 1:
            tool_args = tool_args["kwargs"]

        log.debug(f"Executing {tool_name} with {json.dumps(tool_args, default=str)[:100]}...")

        # Execute
        async def run_tool() -> object:
            if hasattr(tool, 'arun'):
                return await tool.arun(tool_args)
            elif hasattr(tool, '_run'):
                loop = asyncio.get_running_loop()
                return await loop.run_in_executor(None, functools.partial(tool._run, **tool_args))
            else:
                loop = asyncio.get_running_loop()
                return await loop.run_in_executor(None, functools.partial(tool.run, **tool_args))

        result = await asyncio.wait_for(run_tool(), timeout=NodeConfig.TOOL_TIMEOUT_SECONDS)
        is_success = _detect_tool_success(result)

        # Handle retrieval output
        content = result
        if "retrieval" in tool_name.lower():
            content = _process_retrieval_output(result, state, log)
        else:
            content = clean_tool_result(result)

        duration_ms = (time.perf_counter() - start_time) * 1000
        status = "success" if is_success else "error"

        log.info(f"{'✅' if is_success else '❌'} {tool_name}: {duration_ms:.0f}ms")

        content_str = format_result_for_llm(content, tool_name)

        return {
            "tool_result": {
                "tool_name": tool_name,
                "result": content,
                "status": status,
                "tool_id": tool_id,
                "args": tool_args,
                "duration_ms": duration_ms
            },
            "tool_message": ToolMessage(content=content_str, tool_call_id=tool_id)
        }

    except asyncio.TimeoutError:
        duration_ms = (time.perf_counter() - start_time) * 1000
        error_msg = f"Timeout after {duration_ms:.0f}ms"
        log.error(f"❌ {tool_name} timed out")
        return _create_error_result_sync(tool_name, tool_id, error_msg)

    except Exception as e:
        duration_ms = (time.perf_counter() - start_time) * 1000
        log.error(f"❌ {tool_name} failed after {duration_ms:.0f}ms: {e}")
        return _create_error_result_sync(tool_name, tool_id, f"{type(e).__name__}: {str(e)}")


async def _create_error_result(tool_name: str, tool_id: str, error: str) -> Dict:
    """Create error result"""
    return _create_error_result_sync(tool_name, tool_id, error)


def _create_error_result_sync(tool_name: str, tool_id: str, error: str) -> Dict:
    """Create error result (sync)"""
    return {
        "tool_result": {
            "tool_name": tool_name,
            "result": f"Error: {error}",
            "status": "error",
            "tool_id": tool_id
        },
        "tool_message": ToolMessage(content=f"Error: {error}", tool_call_id=tool_id)
    }


def _detect_tool_success(result: object) -> bool:
    """Detect if tool succeeded"""
    if result is None:
        return False

    if isinstance(result, tuple) and len(result) >= 1:
        if isinstance(result[0], bool):
            return result[0]

    if isinstance(result, dict):
        if "success" in result and isinstance(result["success"], bool):
            return result["success"]
        if "error" in result and result["error"] not in (None, "", "null"):
            return False
        if "ok" in result and isinstance(result["ok"], bool):
            return result["ok"]

    result_str = str(result).lower()
    error_indicators = [
        "error:", '"error": "', "'error': '",
        "failed", "failure", "exception",
        "traceback", "status_code: 4", "status_code: 5"
    ]

    if '"error": null' in result_str or "'error': none" in result_str:
        return not any(ind in result_str for ind in error_indicators)

    return not any(ind in result_str for ind in error_indicators)


def _process_retrieval_output(result: object, state: ChatState, log: logging.Logger) -> str:
    """Process retrieval tool output"""
    try:
        from app.agents.actions.retrieval.retrieval import RetrievalToolOutput

        retrieval_output = None

        if isinstance(result, dict) and "content" in result and "final_results" in result:
            retrieval_output = RetrievalToolOutput(**result)
        elif isinstance(result, str):
            try:
                data = json.loads(result)
                if isinstance(data, dict) and "content" in data and "final_results" in data:
                    retrieval_output = RetrievalToolOutput(**data)
            except (json.JSONDecodeError, TypeError):
                pass

        if retrieval_output:
            state["final_results"] = retrieval_output.final_results
            state["virtual_record_id_to_result"] = retrieval_output.virtual_record_id_to_result

            if retrieval_output.virtual_record_id_to_result:
                state["tool_records"] = list(retrieval_output.virtual_record_id_to_result.values())

            log.info(f"Retrieved {len(retrieval_output.final_results)} knowledge blocks")
            return retrieval_output.content

    except Exception as e:
        log.warning(f"Could not process retrieval output: {e}")

    return str(result)


# ============================================================================
# Node 3: Reflect
# ============================================================================

async def reflect_node(
    state: ChatState,
    config: RunnableConfig,
    writer: StreamWriter
) -> ChatState:
    """Analyze results and decide next action"""
    start_time = time.perf_counter()
    log = state.get("logger", logger)

    tool_results = state.get("all_tool_results", [])
    retry_count = state.get("retry_count", 0)
    max_retries = state.get("max_retries", 1)

    # Fast path: all succeeded
    failed = [r for r in tool_results if r.get("status") == "error"]

    if not failed:
        state["reflection_decision"] = "respond_success"
        state["reflection"] = {"decision": "respond_success", "reasoning": "All succeeded"}
        log.info("Reflect: respond_success (fast path)")
        return state

    # Fast path: unrecoverable errors
    error_text = " ".join(str(r.get("result", "")) for r in failed).lower()

    unrecoverable = [
        "permission", "unauthorized", "forbidden", "403",
        "not found", "does not exist", "404",
        "authentication", "auth failed", "invalid token",
        "rate limit", "quota exceeded"
    ]

    if any(pattern in error_text for pattern in unrecoverable):
        error_context = "Permission or access issue"
        if "not found" in error_text or "does not exist" in error_text:
            error_context = "Resource not found"
        elif "rate limit" in error_text or "quota" in error_text:
            error_context = "Rate limit reached"

        state["reflection_decision"] = "respond_error"
        state["reflection"] = {
            "decision": "respond_error",
            "reasoning": "Unrecoverable error",
            "error_context": error_context
        }
        log.info("Reflect: respond_error (fast path)")
        return state

    # Fast path: unbounded JQL
    if "unbounded" in error_text:
        if retry_count < max_retries:
            state["reflection_decision"] = "retry_with_fix"
            state["reflection"] = {
                "decision": "retry_with_fix",
                "reasoning": "Unbounded JQL",
                "fix_instruction": "Add time filter: `AND updated >= -30d`"
            }
            log.info("Reflect: retry_with_fix (unbounded)")
            return state
        else:
            state["reflection_decision"] = "respond_clarify"
            state["reflection"] = {
                "decision": "respond_clarify",
                "reasoning": "Unbounded JQL after retry",
                "clarifying_question": "I need a time range. What period? (last 7 days, 30 days, 3 months, or specific dates?)"
            }
            log.info("Reflect: respond_clarify (unbounded after retry)")
            return state

    # Fast path: other recoverable errors
    if retry_count < max_retries:
        if any(x in error_text for x in ["syntax", "invalid", "malformed", "parse error"]):
            state["reflection_decision"] = "retry_with_fix"
            state["reflection"] = {
                "decision": "retry_with_fix",
                "reasoning": "Syntax error",
                "fix_instruction": "Fix query syntax based on error"
            }
            log.info("Reflect: retry_with_fix (syntax)")
            return state

        if "user" in error_text and ("not found" in error_text or "no user" in error_text):
            state["reflection_decision"] = "retry_with_fix"
            state["reflection"] = {
                "decision": "retry_with_fix",
                "reasoning": "User not found",
                "fix_instruction": "Search users first to get correct ID"
            }
            log.info("Reflect: retry_with_fix (user not found)")
            return state

    # Slow path: LLM reflection
    llm = state.get("llm")

    summary_parts = []
    for r in tool_results:
        status = "SUCCESS" if r.get("status") == "success" else "FAILED"
        tool_name = r.get("tool_name", "unknown")
        result_str = str(r.get("result", ""))[:300]
        summary_parts.append(f"[{status}] {tool_name}: {result_str}")

    prompt = REFLECT_PROMPT.format(
        execution_summary="\n".join(summary_parts),
        query=state.get("query", ""),
        retry_count=retry_count,
        max_retries=max_retries
    )

    try:
        safe_stream_write(writer, {
            "event": "status",
            "data": {"status": "analyzing", "message": "Analyzing results..."}
        }, config)

        response = await asyncio.wait_for(
            llm.ainvoke([
                SystemMessage(content=prompt),
                HumanMessage(content="Analyze and decide.")
            ]),
            timeout=8.0
        )

        reflection = _parse_reflection_response(response.content, log)

    except asyncio.TimeoutError:
        log.warning("Reflect timeout")
        reflection = {
            "decision": "respond_error",
            "reasoning": "Analysis timeout",
            "error_context": "Unable to complete request"
        }
    except Exception as e:
        log.error(f"Reflection failed: {e}")
        reflection = {
            "decision": "respond_error",
            "reasoning": str(e),
            "error_context": "Error processing request"
        }

    state["reflection"] = reflection
    state["reflection_decision"] = reflection.get("decision", "respond_error")

    duration_ms = (time.perf_counter() - start_time) * 1000
    log.info(f"Reflect: {state['reflection_decision']} (LLM, {duration_ms:.0f}ms)")

    return state


def _parse_reflection_response(content: str, log: logging.Logger) -> Dict[str, Any]:
    """Parse reflection JSON"""
    content = content.strip()

    if "```json" in content:
        match = re.search(r'```json\s*(.*?)\s*```', content, re.DOTALL)
        if match:
            content = match.group(1)
    elif content.startswith("```"):
        content = re.sub(r'^```\s*\n?', '', content)
        content = re.sub(r'\n?```\s*$', '', content)

    try:
        reflection = json.loads(content)

        if isinstance(reflection, dict):
            reflection.setdefault("decision", "respond_error")
            reflection.setdefault("reasoning", "")
            reflection.setdefault("fix_instruction", "")
            reflection.setdefault("clarifying_question", "")
            reflection.setdefault("error_context", "")
            return reflection

    except json.JSONDecodeError as e:
        log.warning(f"Failed to parse reflection: {e}")

    return {
        "decision": "respond_error",
        "reasoning": "Parse failed",
        "error_context": "Unable to process request"
    }


# ============================================================================
# Node 4: Prepare Retry
# ============================================================================

async def prepare_retry_node(
    state: ChatState,
    config: RunnableConfig,
    writer: StreamWriter
) -> ChatState:
    """Prepare for retry"""
    log = state.get("logger", logger)

    state["retry_count"] = state.get("retry_count", 0) + 1
    state["is_retry"] = True

    # Extract errors
    tool_results = state.get("all_tool_results", [])
    errors = []
    for r in tool_results:
        if r.get("status") == "error":
            errors.append({
                "tool_name": r.get("tool_name", "unknown"),
                "args": r.get("args", {}),
                "error": str(r.get("result", ""))[:300]
            })

    state["execution_errors"] = errors

    # Clear old results
    state["all_tool_results"] = []
    state["tool_results"] = []

    safe_stream_write(writer, {
        "event": "status",
        "data": {"status": "retrying", "message": "Retrying..."}
    }, config)

    log.info(f"Prepare retry {state['retry_count']}/{state.get('max_retries', 1)}: {len(errors)} errors")

    return state


def route_after_reflect(state: ChatState) -> Literal["prepare_retry", "respond"]:
    """Route based on reflection"""
    decision = state.get("reflection_decision", "respond_success")
    retry_count = state.get("retry_count", 0)
    max_retries = state.get("max_retries", 1)

    if decision == "retry_with_fix" and retry_count < max_retries:
        return "prepare_retry"

    return "respond"


# ============================================================================
# Node 5: Respond
# ============================================================================

async def respond_node(
    state: ChatState,
    config: RunnableConfig,
    writer: StreamWriter
) -> ChatState:
    """Generate final response with streaming"""
    start_time = time.perf_counter()
    log = state.get("logger", logger)
    llm = state.get("llm")

    safe_stream_write(writer, {
        "event": "status",
        "data": {"status": "generating", "message": "Generating response..."}
    }, config)

    # Handle error state
    if state.get("error"):
        error = state["error"]
        error_msg = error.get("message", error.get("detail", "An error occurred"))
        error_response = {
            "answer": error_msg,
            "citations": [],
            "confidence": "Low",
            "answerMatchType": "Error"
        }
        safe_stream_write(writer, {
            "event": "answer_chunk",
            "data": {"chunk": error_msg, "accumulated": error_msg, "citations": []}
        }, config)
        safe_stream_write(writer, {"event": "complete", "data": error_response}, config)
        state["response"] = error_msg
        state["completion_data"] = error_response
        return state

    # Check if direct answer
    execution_plan = state.get("execution_plan", {})
    tool_results = state.get("all_tool_results", [])

    if execution_plan.get("can_answer_directly") and not tool_results:
        response = await _generate_direct_response_streaming(state, llm, log, writer, config)
        completion = {
            "answer": response,
            "citations": [],
            "confidence": "High",
            "answerMatchType": "Direct Response"
        }
        safe_stream_write(writer, {"event": "complete", "data": completion}, config)
        state["response"] = response
        state["completion_data"] = completion
        return state

    # Get citation data
    final_results = state.get("final_results", [])
    if not isinstance(final_results, list):
        final_results = []

    virtual_record_map = state.get("virtual_record_id_to_result", {})
    tool_records = state.get("tool_records", [])

    log.info(f"Citation data: {len(final_results)} results, {len(virtual_record_map)} records")

    # Check tool outcomes
    successful_count = sum(1 for r in tool_results if r.get("status") == "success")
    failed_count = sum(1 for r in tool_results if r.get("status") == "error")

    log.info(f"Tool execution: {successful_count} succeeded, {failed_count} failed")

    # Handle reflection decisions
    reflection_decision = state.get("reflection_decision", "respond_success")
    reflection = state.get("reflection", {})

    # Clarification
    if reflection_decision == "respond_clarify":
        clarifying_question = reflection.get("clarifying_question", "Could you provide more details?")
        clarify_response = {
            "answer": clarifying_question,
            "citations": [],
            "confidence": "Medium",
            "answerMatchType": "Clarification Needed"
        }
        safe_stream_write(writer, {
            "event": "answer_chunk",
            "data": {"chunk": clarifying_question, "accumulated": clarifying_question, "citations": []}
        }, config)
        safe_stream_write(writer, {"event": "complete", "data": clarify_response}, config)
        state["response"] = clarifying_question
        state["completion_data"] = clarify_response
        return state

    # Error
    has_failed = failed_count > 0
    has_no_success = successful_count == 0 and not final_results
    all_failed = has_failed and successful_count == 0 and len(tool_results) > 0

    if reflection_decision == "respond_error" or has_no_success or all_failed:
        error_context = reflection.get("error_context", "")

        failed_tool_errors = []
        for r in tool_results:
            if r.get("status") == "error":
                tool_name = r.get("tool_name", "unknown")
                error_result = r.get("result", "Unknown error")
                if isinstance(error_result, dict):
                    error_msg = error_result.get("error", str(error_result))
                else:
                    error_msg = str(error_result)
                if isinstance(error_msg, tuple):
                    error_msg = str(error_msg[0]) if len(error_msg) > 0 else str(error_msg)
                failed_tool_errors.append(f"{tool_name}: {error_msg[:150]}")

        if error_context:
            error_msg = f"I wasn't able to complete that request. {error_context}\n\nPlease try again."
        elif failed_tool_errors:
            error_details = "\n".join(failed_tool_errors[:2])
            error_msg = f"I encountered an error. {error_details}\n\nPlease check settings or try again."
        else:
            error_msg = "I wasn't able to complete that request. Please try again."

        error_response = {
            "answer": error_msg,
            "citations": [],
            "confidence": "Low",
            "answerMatchType": "Tool Execution Failed",
            "reason": f"{failed_count} tool(s) failed" if failed_count > 0 else "Unable to process"
        }

        safe_stream_write(writer, {
            "event": "answer_chunk",
            "data": {"chunk": error_msg, "accumulated": error_msg, "citations": []}
        }, config)
        safe_stream_write(writer, {"event": "complete", "data": error_response}, config)

        state["response"] = error_msg
        state["completion_data"] = error_response
        return state

    # Success - generate response
    messages = create_response_messages(state)

    if tool_results or final_results:
        context = _build_tool_results_context(tool_results, final_results)
        if context.strip():
            if messages and isinstance(messages[-1], HumanMessage):
                messages[-1].content += context
            else:
                messages.append(HumanMessage(content=context))

    try:
        log.info("Using stream_llm_response...")

        answer_text = ""
        citations = []
        reason = None
        confidence = None

        async for stream_event in stream_llm_response(
            llm=llm,
            messages=messages,
            final_results=final_results,
            logger=log,
            target_words_per_chunk=1,
            mode="json",
            virtual_record_id_to_result=virtual_record_map,
            records=tool_records,
        ):
            event_type = stream_event.get("event")
            event_data = stream_event.get("data", {})

            safe_stream_write(writer, {"event": event_type, "data": event_data}, config)

            if event_type == "complete":
                answer_text = event_data.get("answer", "")
                citations = event_data.get("citations", [])
                reason = event_data.get("reason")
                confidence = event_data.get("confidence")
                reference_data = event_data.get("referenceData", [])

        if not answer_text or len(answer_text.strip()) == 0:
            log.warning("Empty response, using fallback")
            answer_text = "I wasn't able to generate a response. Please try rephrasing."

            fallback_response = {
                "answer": answer_text,
                "citations": [],
                "confidence": "Low",
                "answerMatchType": "Fallback Response"
            }

            safe_stream_write(writer, {
                "event": "answer_chunk",
                "data": {"chunk": answer_text, "accumulated": answer_text, "citations": []}
            }, config)
            safe_stream_write(writer, {"event": "complete", "data": fallback_response}, config)

            state["response"] = answer_text
            state["completion_data"] = fallback_response
        else:
            completion_data = {
                "answer": answer_text,
                "citations": citations,
                "reason": reason,
                "confidence": confidence,
            }
            if reference_data:
                completion_data["referenceData"] = reference_data
                log.debug(f"Stored {len(reference_data)} reference items")

            state["response"] = answer_text
            state["completion_data"] = completion_data

        log.info(f"Generated response: {len(answer_text)} chars, {len(citations)} citations")

    except Exception as e:
        log.error(f"Response generation failed: {e}", exc_info=True)
        error_msg = "I encountered an issue. Please try again."
        error_response = {
            "answer": error_msg,
            "citations": [],
            "confidence": "Low",
            "answerMatchType": "Error"
        }
        safe_stream_write(writer, {
            "event": "answer_chunk",
            "data": {"chunk": error_msg, "accumulated": error_msg, "citations": []}
        }, config)
        safe_stream_write(writer, {"event": "complete", "data": error_response}, config)
        state["response"] = error_msg
        state["completion_data"] = error_response

    duration_ms = (time.perf_counter() - start_time) * 1000
    log.info(f"✅ respond_node: {duration_ms:.0f}ms")

    return state


async def _generate_direct_response_streaming(
    state: ChatState,
    llm: object,
    log: logging.Logger,
    writer: StreamWriter,
    config: RunnableConfig
) -> str:
    """Generate direct response with streaming"""
    query = state.get("query", "")
    previous = state.get("previous_conversations", [])

    context_lines = []
    for conv in previous[-3:]:
        role = conv.get("role", "")
        content = conv.get("content", "")[:200]
        if role == "user_query":
            context_lines.append(f"User: {content}")
        elif role == "bot_response":
            context_lines.append(f"Assistant: {content}...")

    context = "\n".join(context_lines) if context_lines else ""

    user_context = _format_user_context(state)
    user_info_section = f"\n\n{user_context}" if user_context else ""

    system_content = "You are a helpful, friendly AI assistant. Respond naturally and concisely."
    if user_context:
        system_content += "\n\nIMPORTANT: User information is provided. When user asks about themselves ('my name', 'who am I'), use the info DIRECTLY."

    user_content = query
    if context:
        user_content = f"{context}\n\nUser: {query}"
    if user_context:
        user_content += user_info_section

    messages = [
        SystemMessage(content=system_content),
        HumanMessage(content=user_content)
    ]

    try:
        full_content = ""

        if hasattr(llm, 'astream'):
            async for chunk in llm.astream(messages):
                if not chunk:
                    continue

                chunk_text = ""
                if hasattr(chunk, 'content'):
                    content = chunk.content
                    if isinstance(content, str):
                        chunk_text = content
                    elif isinstance(content, list):
                        for part in content:
                            if isinstance(part, dict) and part.get("type") == "text":
                                chunk_text += part.get("text", "")
                            elif isinstance(part, str):
                                chunk_text += part

                if chunk_text:
                    full_content += chunk_text
                    safe_stream_write(writer, {
                        "event": "answer_chunk",
                        "data": {"chunk": chunk_text, "accumulated": full_content, "citations": []}
                    }, config)
        else:
            response = await llm.ainvoke(messages)
            full_content = response.content if hasattr(response, 'content') else str(response)
            safe_stream_write(writer, {
                "event": "answer_chunk",
                "data": {"chunk": full_content, "accumulated": full_content, "citations": []}
            }, config)

        return full_content
    except Exception as e:
        log.error(f"Direct response failed: {e}")
        fallback = "I'm here to help! How can I assist you today?"
        safe_stream_write(writer, {
            "event": "answer_chunk",
            "data": {"chunk": fallback, "accumulated": fallback, "citations": []}
        }, config)
        return fallback


def _build_tool_results_context(tool_results: List[Dict], final_results: List[Dict]) -> str:
    """Build context from tool results"""
    successful = [r for r in tool_results if r.get("status") == "success"]
    failed = [r for r in tool_results if r.get("status") == "error"]
    has_retrieval = bool(final_results)
    non_retrieval = [r for r in successful if "retrieval" not in r.get("tool_name", "").lower()]

    parts = []

    # All failed
    if failed and not successful:
        parts.append("\n## ⚠️ Tools Failed\n")
        for r in failed[:3]:
            err = r.get("result", "Unknown error")
            if isinstance(err, dict):
                err = err.get("error", str(err))
            err_str = str(err)
            if isinstance(err, tuple):
                err_str = str(err[0]) if len(err) > 0 else str(err)
            parts.append(f"- {r.get('tool_name', 'unknown')}: {err_str[:200]}\n")
        parts.append("\nCRITICAL: DO NOT fabricate data. Acknowledge failure and explain error.\n")
        return "".join(parts)

    # Empty results
    def _is_empty_result(result: object) -> bool:
        if result is None:
            return True
        if isinstance(result, list) and len(result) == 0:
            return True
        if isinstance(result, dict):
            for key in ["issues", "items", "results", "data", "records", "values"]:
                if key in result and isinstance(result[key], list) and len(result[key]) == 0:
                    return True
            for key in ["total", "count", "size"]:
                if key in result and result[key] == 0:
                    return True
        return False

    empty_results = [r for r in successful if _is_empty_result(r.get("result"))]
    if empty_results and len(empty_results) == len(successful):
        parts.append("\n## 📭 No Results Found\n\n")
        parts.append("Search completed but found zero items.\n\n")
        parts.append("Explain what was searched and suggest ways to modify the query.\n")
        return "".join(parts)

    # Has data
    if has_retrieval:
        parts.append("\n## ⚠️ Internal Knowledge Available\n\n")
        parts.append(f"You have {len(final_results)} knowledge blocks in the context above.\n")
        parts.append("Cite IMMEDIATELY after facts: [R1-1], [R2-3]\n\n")

    if non_retrieval:
        parts.append("\n## API Tool Results\n\n")
        parts.append("Transform data into professional markdown.\n")
        parts.append("DO NOT show raw IDs - store in referenceData.\n\n")

        for r in non_retrieval[:5]:
            tool_name = r.get('tool_name', 'unknown')
            content = r.get("result", "")

            if isinstance(content, (dict, list)):
                content_str = json.dumps(content, indent=2, default=str)
            else:
                content_str = str(content)

            parts.append(f"### {tool_name}\n")
            parts.append(f"```json\n{content_str}\n```\n\n")

    parts.append("\n---\n## SYNTHESIS INSTRUCTIONS\n\n")

    if has_retrieval and non_retrieval:
        # Both internal knowledge and API data
        parts.append("""**COMBINED RESPONSE REQUIRED**:
You have both internal knowledge (cite with [R1-X]) and API data (no citations needed).

1. Use internal knowledge with inline citations
2. Transform API data into professional markdown
3. Store technical IDs/keys in referenceData for follow-up queries
4. If API data is empty, explain and suggest query modifications

**JSON FORMAT**:
```json
{
  "answer": "Answer with [R1-1] citations for internal knowledge. API data formatted nicely.",
  "reason": "How you derived the answer",
  "confidence": "High",
  "answerMatchType": "Derived From Blocks",
  "blockNumbers": ["R1-1", "R1-2"],
  "referenceData": [{"name": "Item", "id": "123", "key": "ABC", "type": "jira_project"}]
}
```
""")
    elif has_retrieval:
        # Only internal knowledge
        parts.append("""**INTERNAL KNOWLEDGE RESPONSE**:
Use the knowledge blocks from the system context to answer comprehensively.

**CRITICAL - Citation Rules**:
1. Put citation IMMEDIATELY after each fact: "Revenue grew 29% [R1-1]."
2. One citation per bracket: [R1-1][R2-3] NOT [R1-1, R2-3]
3. Include ALL cited blocks in blockNumbers array
4. Do NOT put citations at end of paragraph - inline after each fact

**JSON FORMAT**:
```json
{
  "answer": "Detailed answer with [R1-1] citations inline after each fact.",
  "reason": "How you derived the answer from the blocks",
  "confidence": "High",
  "answerMatchType": "Derived From Blocks",
  "blockNumbers": ["R1-1", "R1-2"]
}
```
""")
    else:
        # Only API data
        parts.append("""**API DATA RESPONSE**:
Transform the tool results above into professional, user-friendly markdown.

**WHAT TO SHOW vs HIDE (CRITICAL)**:

✅ **ALWAYS SHOW** (User-facing identifiers):
- **Jira ticket keys** (PA-123, ESP-456) - users NEED these to reference tickets!
- **Jira project keys** (PA, ESP) - short identifiers users work with
- Names, summaries, descriptions, status, priority, assignee names, dates

❌ **NEVER SHOW** (Internal technical IDs):
- Internal numeric IDs (10039, 16446)
- UUIDs/accountIds (712020:2c136d9b-19dd-472b-ba99-091bec4a987b)
- Database hashes, file system IDs

**JIRA TICKETS EXAMPLE**:
```markdown
| Ticket | Summary | Status | Priority | Assignee |
|--------|---------|--------|----------|----------|
| PA-123 | Fix login bug | In Progress | High | John Smith |
| PA-124 | Add dark mode | Open | Medium | Jane Doe |
```
Notice: PA-123 (ticket key) is shown, but internal ID "16446" is NOT shown.

**HANDLING EMPTY RESULTS**:
If tool results show zero items or empty data:
- DO NOT just say "no results found"
- Explain what was searched and why it may be empty
- Suggest specific ways to modify the query (different time range, different filters, etc.)
- Ask the user for clarification if needed

**MARKDOWN FORMATTING** (MUST FOLLOW):
- Use headers: # for title, ## for sections, ### for subsections
- Use **bold** for emphasis and important terms
- Use bullet points (-) or numbered lists (1.) for lists
- For tabular data, use PROPERLY FORMATTED markdown tables:
  ```
  | Name | Type | Description |
  |------|------|-------------|
  | Item 1 | Type A | Some description |
  ```
  IMPORTANT: Each row must have the SAME number of | separators. No extra |.
- Add horizontal rules (---) to separate sections
- Include a summary at the end

**JSON FORMAT**:
```json
{
  "answer": "# Jira Tickets\\n\\n| Ticket | Summary | Status |\\n|--------|---------|--------|\\n| PA-123 | Fix bug | Open |",
  "confidence": "High",
  "answerMatchType": "Derived From Tool Execution",
  "referenceData": [
    {"name": "Fix bug", "id": "16446", "key": "PA-123", "type": "jira_issue"}
  ]
}
```

**referenceData** - CRITICAL for follow-up queries:
- For Jira issues: include "key" (e.g., "PA-123"), "id", "summary"
- For Jira projects: include "key" (e.g., "PA"), "id", "name"
- For users: include "accountId", "displayName"
""")

    parts.append("\nReturn ONLY the JSON object, no markdown wrapping.\n")

    return "".join(parts)


# ============================================================================
# Routing Functions
# ============================================================================

def should_execute_tools(state: ChatState) -> Literal["execute", "respond"]:
    """Route to execute or respond"""
    planned_tools = state.get("planned_tool_calls", [])
    execution_plan = state.get("execution_plan", {})

    if execution_plan.get("needs_clarification"):
        return "respond"

    if not planned_tools or execution_plan.get("can_answer_directly"):
        return "respond"

    return "execute"


def check_for_error(state: ChatState) -> Literal["error", "continue"]:
    """Check for errors"""
    return "error" if state.get("error") else "continue"


# ============================================================================
# Exports
# ============================================================================

__all__ = [
    "planner_node",
    "execute_node",
    "respond_node",
    "reflect_node",
    "prepare_retry_node",
    "should_execute_tools",
    "route_after_reflect",
    "check_for_error",
    "NodeConfig",
    "clean_tool_result",
    "format_result_for_llm",
]

"""
Agent Node Implementations

This module contains all node implementations for the agent graph.
The architecture is LLM-driven without heuristics for deterministic behavior.

Architecture:
    Planner (LLM) → Execute (parallel) → Respond (LLM)

The planner node makes ALL decisions including:
- Query analysis and intent detection
- Tool selection (including retrieval)
- Execution planning

This ensures deterministic, accurate behavior driven by the LLM.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Literal, Optional

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import StreamWriter

from app.modules.agents.qna.chat_state import ChatState
from app.modules.agents.qna.stream_utils import safe_stream_write
from app.modules.qna.response_prompt import (
    create_response_messages,
    detect_response_mode,
)
from app.utils.citations import normalize_citations_and_chunks_for_agent
from app.utils.streaming import stream_llm_response

# Streaming delay constants (match chatbot for consistent UX)
STREAMING_CHUNK_DELAY = 0.015  # 15ms between word chunks

# Content length constants for truncation
TOOL_RESULT_TUPLE_LENGTH = 2  # Expected length for (success, data) tuples
DESCRIPTION_MAX_LENGTH = 80  # Max chars for tool description preview
USER_QUERY_MAX_LENGTH = 300  # Max chars for user query in history
BOT_RESPONSE_MAX_LENGTH = 800  # Max chars for bot response in history

logger = logging.getLogger(__name__)

# Initialize Opik tracer for LLM call tracing
_opik_tracer = None
_opik_api_key = os.getenv("OPIK_API_KEY")
_opik_workspace = os.getenv("OPIK_WORKSPACE")
if _opik_api_key and _opik_workspace:
    try:
        from opik.integrations.langchain import OpikTracer
        _opik_tracer = OpikTracer()
        logger.info("Opik tracer initialized for agent nodes")
    except Exception as e:
        logger.warning(f"Failed to initialize Opik tracer: {e}")


# =============================================================================
# CONFIGURATION
# =============================================================================

class NodeConfig:
    """Configuration for node behavior."""

    # Execution settings
    MAX_PARALLEL_TOOLS: int = 10
    TOOL_TIMEOUT_SECONDS: float = 60.0
    PLANNER_TIMEOUT_SECONDS: float = 30.0

    # Streaming settings
    STREAM_CHUNK_SIZE: int = 3  # words per chunk
    STREAM_DELAY: float = 0.01


# =============================================================================
# RESULT CLEANER (Same pattern as ResponseTransformer in tools)
# =============================================================================

# Fields to ALWAYS remove - these are verbose/internal and waste tokens
REMOVE_FIELDS = {
    # Internal/metadata fields
    "self", "_links", "_embedded", "_meta", "_metadata",
    "expand", "expansions", "schema", "$schema",

    # Avatar/icon URLs (never needed for LLM)
    "avatarUrls", "avatarUrl", "iconUrl", "iconUri", "thumbnailUrl",
    "avatar", "icon", "thumbnail", "profilePicture",

    # Verbose API metadata
    "nextPageToken", "prevPageToken", "pageToken",
    "cursor", "offset", "pagination",
    "startAt", "maxResults", "total",  # Keep total count separately if needed

    # Debug/trace fields
    "trace", "traceId", "requestId", "correlationId",
    "debug", "debugInfo", "stack", "stackTrace",

    # HTTP/request metadata
    "headers", "cookies", "request", "response",
    "httpVersion", "protocol", "encoding",

    # Timezone/locale (rarely needed)
    "timeZone", "timezone", "locale", "language",

    # Internal account fields
    "accountType", "active", "properties",

    # Nested hierarchy markers
    "hierarchyLevel", "subtask", "avatarId",

    # Watch/vote counters (verbose)
    "watches", "votes", "watchers", "voters",

    # Changelog/history (very verbose)
    "changelog", "history", "worklog", "worklogs",
}

# Fields to ALWAYS keep - these are essential for the LLM
KEEP_FIELDS = {
    # Identifiers
    "id", "key", "name", "title", "slug",

    # Content
    "content", "body", "text", "message", "description", "summary",
    "value", "data", "result", "results", "items", "issues", "files",

    # Status/type
    "status", "state", "type", "kind", "category",
    "issuetype", "priority", "resolution",

    # People
    "assignee", "reporter", "author", "creator", "owner", "user",
    "accountId", "displayName", "emailAddress", "email",

    # Timestamps
    "created", "updated", "modified", "createdAt", "updatedAt",
    "date", "timestamp", "time", "dueDate",

    # URLs (only direct resource URLs)
    "url", "link", "href", "webUrl", "permalink",

    # Counts that matter
    "count", "size", "length",

    # Labels/tags
    "labels", "tags", "components",

    # Relations
    "parent", "children", "project", "workspace", "channel", "folder",
}


def clean_tool_result(result: object) -> object:
    """
    Clean tool result by removing verbose fields and keeping essential ones.

    This follows the same pattern as ResponseTransformer used in tools like Jira.
    It removes metadata/internal fields while keeping all data needed for accuracy.

    Args:
        result: Raw tool result

    Returns:
        Cleaned result with verbose fields removed
    """
    # Handle tuple format (success, data) common in tools
    if isinstance(result, tuple) and len(result) == TOOL_RESULT_TUPLE_LENGTH:
        success, data = result
        return (success, clean_tool_result(data))

    # Handle string - try to parse as JSON
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
            cleaned = clean_tool_result(parsed)
            return json.dumps(cleaned, indent=2, ensure_ascii=False)
        except (json.JSONDecodeError, TypeError):
            return result

    # Handle dict
    if isinstance(result, dict):
        return _clean_dict(result)

    # Handle list
    if isinstance(result, list):
        return [clean_tool_result(item) for item in result]

    # Primitives pass through
    return result


def _clean_dict(data: Dict) -> Dict:
    """Clean a dictionary by removing verbose fields."""
    cleaned = {}

    for key, value in data.items():
        key_lower = key.lower()

        # Skip fields that should be removed
        if key in REMOVE_FIELDS or key_lower in REMOVE_FIELDS:
            continue

        # Skip private/internal fields
        if key.startswith("_") or key.startswith("$"):
            continue

        # Skip URL fields that look like API endpoints (not user-facing)
        if key_lower in ("self", "resource", "api", "endpoint"):
            continue

        # Recursively clean nested structures
        if isinstance(value, dict):
            cleaned_value = _clean_dict(value)
            if cleaned_value:  # Only add if not empty after cleaning
                cleaned[key] = cleaned_value
        elif isinstance(value, list):
            cleaned[key] = [clean_tool_result(item) for item in value]
        else:
            cleaned[key] = value

    return cleaned


def format_result_for_llm(result: object, tool_name: str = "") -> str:
    """
    Format a tool result as a clean string for LLM consumption.

    Args:
        result: Tool result (already cleaned)
        tool_name: Name of the tool

    Returns:
        Clean, formatted string
    """
    # Handle tuple format
    if isinstance(result, tuple) and len(result) == TOOL_RESULT_TUPLE_LENGTH:
        success, data = result
        status = "✅ Success" if success else "❌ Failed"
        content = format_result_for_llm(data, tool_name)
        return f"{status}\n{content}"

    # Handle dict/list - format as JSON
    if isinstance(result, (dict, list)):
        try:
            return json.dumps(result, indent=2, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return str(result)

    return str(result)


# =============================================================================
# PLANNER PROMPTS (Balanced for speed + accuracy)
# =============================================================================

# JIRA-specific guidance (only included when Jira tools are available)
JIRA_GUIDANCE = r"""
## JIRA-Specific Guidance (CRITICAL - READ CAREFULLY)

### Never Fabricate User Data
- ❌ NEVER invent emails like "john.doe@example.com"
- ❌ NEVER guess accountIds or user identifiers
- ❌ NEVER use placeholder values like "YOUR_ID", "EXAMPLE_ID"
- ✅ **User email is provided in the prompt** - use it for `jira.search_users(query="[USER_EMAIL]")` to get real accountIds
- ✅ Use project keys from Reference Data when available
- ✅ Use the actual user information provided to make decisions on their behalf

### Understanding JIRA Fields
- **reporter** = Who CREATED the ticket
- **assignee** = Who is ASSIGNED to work on it
- **watchers** = Who is monitoring the ticket
- **resolution** = How the ticket was resolved (empty if unresolved)
- **status** = Current state of the ticket

### JQL Syntax Rules (CRITICAL - MEMORIZE THESE)
1. **For unresolved issues**: Use `resolution IS EMPTY` NOT `resolution = Unresolved` ❌
2. **For current user**: Use `currentUser()` with parentheses, NOT `currentUser` ❌
3. **For empty/null fields**: Use `IS EMPTY` or `IS NULL`, NOT `=` operator ❌
4. **For text values**: Use quotes: `status = "Open"` NOT `status = Open` ❌
5. **For assignee**:
   - ✅ **PREFERRED**: Use accountId from `jira.search_users(query="[USER_EMAIL]")` - user email is provided in the prompt
   - ✅ **FALLBACK**: Use `currentUser()` only if accountId lookup fails
   - ❌ **NEVER** use `currentUser()` if you have user email - always get accountId first for better reliability
6. **For project**: Use the project KEY (e.g., "PA") not the project name or ID

### ⚠️⚠️⚠️ JIRA "Unbounded Query" Error - THIS IS CRITICAL ⚠️⚠️⚠️

**WHAT "Unbounded" MEANS**: JIRA Cloud won't let you scan all tickets without time/date limits.

**THE REAL FIX** (you MUST add a TIME filter):
- ❌ BAD: `project = "PA" AND assignee = currentUser() AND resolution IS EMPTY`
  → This scans ALL tickets ever created! Unbounded!
- ❌ STILL BAD: `project = "PA" AND assignee = currentUser() AND resolution IS EMPTY AND status IN ("Open")`
  → Status filter doesn't help! Still unbounded!
- ✅ GOOD: `project = "PA" AND assignee = currentUser() AND resolution IS EMPTY AND updated >= -30d`
  → Time filter limits scope to last 30 days!
- ✅ GOOD: `project = "PA" AND assignee = currentUser() AND resolution IS EMPTY AND created >= -90d`
  → Time filter limits scope to last 90 days!

**SOLUTION FOR "Unbounded" ERROR**:
1. **Add a time/date filter**: `updated >= -30d` OR `created >= -90d` OR `updated >= startOfMonth()`
2. **OR narrow the scope**: Use single project instead of `IN (ESP, PA)` → try `project = "PA"`

**Common time ranges**:
- Last week: `updated >= -7d`
- Last month: `updated >= -30d`
- Last 3 months: `updated >= -90d`
- This month: `updated >= startOfMonth()`
- This year: `updated >= startOfYear()`

### Common JQL Patterns (CORRECT SYNTAX WITH TIME FILTERS)
**⚠️ CRITICAL**: Notice how EVERY query includes a time filter! This prevents "Unbounded" errors.

- "Tickets I created" →
  **STEP 1**: `jira.search_users(query="[USER_EMAIL_FROM_PROMPT]")` to get accountId
  **STEP 2**: `reporter = "[accountId]" AND resolution IS EMPTY AND created >= -30d`
  (Fallback to `reporter = currentUser()` only if accountId lookup fails)
- "Tickets assigned to me" / "My tickets" →
  **STEP 1**: `jira.search_users(query="[USER_EMAIL_FROM_PROMPT]")` to get accountId
  **STEP 2**: `assignee = "[accountId]" AND resolution IS EMPTY AND updated >= -30d`
  **NOTE**: User email is provided in the prompt - use it directly!
  (Fallback to `assignee = currentUser()` only if accountId lookup fails)
- "My unresolved tickets" → Same as above - use accountId from user email
- "My tickets in [X] project" →
  **STEP 1**: `jira.search_users(query="[USER_EMAIL_FROM_PROMPT]")` to get accountId
  **STEP 2**: `project = "[PROJECT_KEY]" AND assignee = "[accountId]" AND resolution IS EMPTY AND updated >= -30d`
- "Tickets assigned to [person]" → FIRST `jira.search_users(query="person")` to get accountId, THEN `assignee = "[accountId]" AND resolution IS EMPTY AND updated >= -30d`
- "Open tickets" → `status IN ("Open", "In Progress", "To Do") AND updated >= -30d`
- "Unresolved tickets in project" → `project = "[PROJECT_KEY]" AND resolution IS EMPTY AND updated >= -30d`
- "Recent tickets" →
  **STEP 1**: `jira.search_users(query="[USER_EMAIL_FROM_PROMPT]")` to get accountId
  **STEP 2**: `assignee = "[accountId]" AND resolution IS EMPTY AND updated >= -7d ORDER BY updated DESC`
  (Fallback to `assignee = currentUser()` only if accountId lookup fails)

### Smart Parameter Extraction
Extract values from the user's query AND Reference Data section:

**From User's Query**:
- "PA project" or "project PA" → project key is "PA"
- "my tickets" or "assigned to me" → Use `assignee = currentUser()` (the function, with parentheses!)
- "last week" → updated >= -7d
- "last month" or "recent" → updated >= -30d
- "unresolved" or "open" → resolution IS EMPTY

**For "my tickets" / "assigned to me" queries (CRITICAL):**
- ✅ ALWAYS use `assignee = currentUser()` - it's a JQL function that returns the authenticated user
- ✅ Example: `project = "PA" AND assignee = currentUser() AND resolution IS EMPTY AND updated >= -30d`
- ❌ DO NOT call `jira.search_users` to find yourself - `currentUser()` works automatically

**From Reference Data** (CRITICAL for follow-ups):
- If Reference Data shows: `PipesHub AI (jira_project) | key=\`PA\` | id=\`10039\``
- User says "tickets for that project" or "PipesHub AI tickets"
- → Use `project = "PA"` (the KEY, not the name or ID!)

**Smart Inference** (when safe):
- "recent" usually means last 7-30 days
- "unresolved" means `resolution IS EMPTY`
- "my" / "me" / "I" → use `currentUser()` function in JQL (NOT search_users!)

### WRONG JQL Examples (DO NOT USE)
- ❌ `resolution = Unresolved` → ✅ Use `resolution IS EMPTY`
- ❌ `assignee = currentUser` → ✅ Use `assignee = currentUser()` (WITH parentheses!)
- ❌ `assignee = "[accountId]"` placeholder → ✅ Use `assignee = currentUser()` for current user
- ❌ `status = Open` → ✅ Use `status = "Open"` (with quotes) or `status IN ("Open", "In Progress")`
- ❌ `resolution = null` → ✅ Use `resolution IS EMPTY` or `resolution IS NULL`
- ❌ Unbounded query: `project = "PA" AND assignee = currentUser()` → ✅ Add time filter: `AND updated >= -30d`
- ❌ Unbounded query: `project IN (ESP, PA) AND assignee = currentUser()` → ✅ Add time filter: `AND updated >= -30d`
- ❌ `project = "PipesHub AI"` (project name) → ✅ Use project KEY: `project = "PA"` (check Reference Data)

### When to use search_users vs currentUser()
- ✅ `currentUser()` → For the authenticated user's own tickets (no need to look up accountId)
- ✅ `jira.search_users(query="john")` → To find another person's accountId for queries about them

### Always Use Real Project Keys
- ✅ Check Reference Data for project keys (key=`PA`)
- ✅ If no Reference Data, call `jira.get_projects()` first to see available projects
- ❌ Don't guess: "PROJECT", "PROJ", "TEST" (might not exist!)
- **Use project keys from Reference Data** (e.g., key=`PA`) for JQL queries instead of project names
"""

PLANNER_SYSTEM_PROMPT = """You are an intelligent task planner for an enterprise AI assistant.

## Available Tools (with EXACT parameters)
{available_tools}

## CRITICAL - Parameter Rules
**ONLY use the parameters listed above for each tool. DO NOT invent or add any parameters that are not listed.**
- Each tool shows its exact parameters in the format: `param_name (type, required/optional)`
- If a tool has "Parameters: none" → pass an empty args object: {{"args": {{}}}}
- If a tool has required parameters → you MUST provide them
- NEVER add arbitrary parameters like 'includeItemsFromAllDrives', 'maxResults', etc. unless they are explicitly listed

## Planning Rules
1. **Internal Knowledge**: For questions about company data, documents, policies, reports → use `retrieval.search_internal_knowledge`
2. **API Tools**: For project management, tickets, issues → use appropriate tools (jira.*, slack.*, etc.) if available
3. **Direct Answer**: For greetings, simple math, general knowledge, AND **user information queries** → set can_answer_directly: true
   - **CRITICAL**: If user asks about themselves ("my name", "who am I", "give info about me", "my email", etc.) AND user information is provided in the prompt → set `can_answer_directly: true` with `tools: []`
   - The user information is provided in the "Current User Information" section - use it directly!
4. **Query Understanding**: Extract key entities, dates, and context from the query
5. **Conversation Context**: Use previous conversation to understand follow-up queries and reuse data (IDs, names, keys, etc.)

## IMPORTANT - Context Awareness
- If user says "try again", "do that again", "retry" → look at previous conversation to understand what to retry
- If user references something from earlier (e.g., "that project", "the first one", "those files") → extract the relevant IDs/keys from Reference Data section
- Reuse data from previous responses to avoid redundant tool calls
{jira_guidance}

## Slack-Specific Guidance
- ✅ Use email addresses: `slack.get_user_info(user="user@company.com")`
- ✅ Use Slack user IDs: `slack.get_user_info(user="U123ABC45")`
- ❌ NEVER use database IDs (24-char hex like "692d40c1585831c0f395f48a")

## Error Recovery Rules
1. **First failure**: Fix based on error message (e.g., add time filter for unbounded JQL)
2. **Second failure (same error)**: Stop and ask user for help
3. **Permission error**: Can't fix - inform user immediately
- ❌ DON'T retry the same thing 3+ times

## When to Ask for Clarification (IMPORTANT)
If the user's query is **ambiguous** or **missing critical information**, set `needs_clarification: true` instead of making tool calls.

**Examples requiring clarification:**
- "Get tickets" (no project, no time range, no assignee specified AND no Reference Data available)
- "Send a message" (to whom? what channel?)
- "Find the document" (which document? what topic?)

**Examples you CAN proceed with:**
- "my tickets" → Use `assignee = currentUser() AND updated >= -30d` (default to 30 days)
- "tickets for PA project" → Use project key directly
- "show tickets for PipesHub AI" AND Reference Data has `key=\\`PA\\`` → Use project key from Reference Data
- **"give info about me" / "my name" / "who am I"** → If "Current User Information" is provided in the prompt → set `can_answer_directly: true` with `tools: []` (use the information from the prompt!)

Only ask for clarification when you truly cannot proceed without more info.

## Output Format (JSON only, no markdown)
{{
  "intent": "Brief description of what user wants",
  "reasoning": "Why these tools are needed (include any context reused from Reference Data)",
  "can_answer_directly": false,
  "needs_clarification": false,
  "clarifying_question": "",
  "tools": [
    {{"name": "tool.name", "args": {{"param": "value"}}}}
  ]
}}

**If needs_clarification is true**: Set `tools: []` and provide a clear `clarifying_question`.

## Examples
- "Tell me about Q4 results" → retrieval.search_internal_knowledge with args: {{"query": "Q4 results"}}
- "What are my Jira projects?" → jira.get_projects with args: {{}}
- "My tickets in PA" → jira.search_issues with args: {{"jql": "project = \\"PA\\" AND assignee = currentUser() AND resolution IS EMPTY AND updated >= -30d"}}
- "tickets for PipesHub AI" (with Reference Data showing key=`PA`) → jira.search_issues with args: {{"jql": "project = \\"PA\\" AND resolution IS EMPTY AND updated >= -30d"}}
- "List files" → drive.get_files_list with args: {{}}
- "Hello!" → can_answer_directly: true, tools: []
- **"give info about me" / "my name" / "who am I"** (when "Current User Information" is in prompt) → can_answer_directly: true, tools: [] (use the user info from the prompt!)
- **"what is my email"** (when user email is in "Current User Information") → can_answer_directly: true, tools: [] (use the email from the prompt!)"""

PLANNER_USER_TEMPLATE = """Query: {query}

Plan the tools needed to answer this query. Return only valid JSON."""

PLANNER_USER_TEMPLATE_WITH_CONTEXT = """## Conversation History
{conversation_history}

## Current Query
{query}

Plan the tools needed to answer this query. Use context from the conversation history when relevant. Return only valid JSON."""


# =============================================================================
# REFLECT PROMPT (Compact for fast LLM decisions)
# =============================================================================

REFLECT_PROMPT = """Analyze tool execution results and decide the best next action.

## Results
{execution_summary}

## Query
{query}

## Retry Status
Attempt: {retry_count}/{max_retries}

## Decision Options
1. **respond_success** - Tools worked, respond with data
2. **respond_error** - Unrecoverable error (permission denied, not found, auth failed)
3. **respond_clarify** - Need more info from user (ambiguous query, missing required params)
4. **retry_with_fix** - Fixable error (bad syntax, unbounded query, wrong format)

## Common Fixes for retry_with_fix
- "Unbounded JQL" → Add time filter: `updated >= -30d`
- "User not found" → Search users first to get real ID
- "Invalid syntax" → Fix query format based on error

## Output (JSON only, no markdown)
{{
  "decision": "respond_success|respond_error|respond_clarify|retry_with_fix",
  "reasoning": "Brief explanation",
  "fix_instruction": "For retry: what to change",
  "clarifying_question": "For clarify: what to ask user",
  "error_context": "For error: user-friendly explanation"
}}"""


# =============================================================================
# HELPER: Check if Jira Tools Are Available
# =============================================================================

def _has_jira_tools(state: ChatState) -> bool:
    """
    Check if Jira tools are available in the current state.
    This helps conditionally include Jira-specific instructions.
    """
    # Check connector instances
    connector_instances = state.get("connector_instances", [])
    if connector_instances:
        for instance in connector_instances:
            if isinstance(instance, dict):
                connector_type = instance.get("type", "").lower()
                if "jira" in connector_type:
                    return True

    # Check tools list
    tools = state.get("tools", [])
    if tools:
        for tool in tools:
            if isinstance(tool, str) and tool.startswith("jira."):
                return True

    # Check tool descriptions (if already cached)
    try:
        tool_descriptions = _get_cached_tool_descriptions(state, state.get("logger", logger))
        if "jira" in tool_descriptions.lower():
            return True
    except Exception:
        pass

    return False


# =============================================================================
# HELPER: Format User Context for Planner
# =============================================================================

def _format_user_context(state: ChatState) -> str:
    """
    Format user and org information for the planner prompt.
    This helps the LLM make informed decisions on behalf of the user.

    NOTE: Only includes user-facing information (name, email). Excludes internal IDs
    (user_id, org_id) to avoid confusion about which ID to use in tool calls.
    """
    user_info = state.get("user_info", {})
    org_info = state.get("org_info", {})

    # Get user details from multiple possible sources
    user_email = state.get("user_email") or user_info.get("userEmail") or user_info.get("email") or ""

    # Get user name - check multiple possible fields
    user_name = (
        user_info.get("fullName") or
        user_info.get("name") or
        user_info.get("displayName") or
        (f"{user_info.get('firstName', '')} {user_info.get('lastName', '')}".strip() if user_info.get("firstName") or user_info.get("lastName") else "")
    )

    # If we have no user information at all, return empty
    if not user_email and not user_name and not user_info:
        return ""

    parts = ["## Current User Information"]
    parts.append("")

    # User details - only include user-facing information (name, email)
    # DO NOT include user_id or org_id as they might confuse the LLM about which to use
    if user_name:
        parts.append(f"- **Name**: {user_name}")
    if user_email:
        parts.append(f"- **Email**: {user_email}")

    # Org details (account type is user-facing info)
    if org_info:
        account_type = org_info.get("accountType")
        if account_type:
            parts.append(f"- **Account Type**: {account_type}")

    # If we have at least some user info, add guidance
    if user_email or user_name:
        parts.append("")
        parts.append("### How to Use This Information:")
        parts.append("")

        # Jira-specific guidance (only if Jira tools are available)
        if _has_jira_tools(state):
            parts.append("**For Jira queries about the current user (my tickets, assigned to me, etc.):**")
            parts.append("- ✅ Use `currentUser()` function directly in JQL: `assignee = currentUser()`")
            parts.append("- ✅ Example: `project = \"PA\" AND assignee = currentUser() AND resolution IS EMPTY`")
            parts.append("- ❌ DO NOT call `jira.search_users` to find yourself - `currentUser()` is faster and always works")
            parts.append("")
            parts.append("**For Jira queries about OTHER users:**")
            parts.append("- Use `jira.search_users(query=\"name_or_email\")` to get their accountId")
            parts.append("- Then use the accountId in JQL: `assignee = \"accountId_value\"`")
            parts.append("")

        parts.append("**General guidance:**")
        if user_email:
            parts.append(f"- Use user email ({user_email}) for user lookups in tools (Slack, Jira, etc.)")
        if user_name:
            parts.append(f"- User's name is: {user_name}")
        parts.append("- Make decisions based on the user's context and permissions")
        parts.append("- **CRITICAL**: When user asks about themselves (e.g., 'my name', 'my info', 'who am I', 'give info about me', 'what is my name'), use the information provided above DIRECTLY - DO NOT call any tools, just answer using this information with `can_answer_directly: true`")
        parts.append("")

    result = "\n".join(parts)
    # Only return if we have meaningful content (more than just the header)
    if len(result.strip()) > len("## Current User Information"):
        return result
    return ""


# =============================================================================
# NODE 1: PLANNER (LLM-Driven, Optimized for Speed)
# =============================================================================

async def planner_node(
    state: ChatState,
    config: RunnableConfig,
    writer: StreamWriter
) -> ChatState:
    """
    Fast LLM-driven planner that creates execution plans.

    Features:
    - Context-aware: uses conversation history for follow-up queries
    - Reuses data from previous responses (IDs, names, etc.)
    - Compact prompt (~60% fewer tokens)
    - Cached tool descriptions
    - 20s timeout for fast failure
    """
    start_time = time.perf_counter()
    log = state.get("logger", logger)
    llm = state.get("llm")
    query = state.get("query", "")
    previous_conversations = state.get("previous_conversations", [])

    safe_stream_write(writer, {
        "event": "status",
        "data": {"status": "planning", "message": "Planning ..."}
    }, config)

    # Build ultra-minimal prompts for speed
    tool_descriptions = _get_cached_tool_descriptions(state, log)

    # Conditionally include Jira guidance only if Jira tools are available
    jira_guidance = JIRA_GUIDANCE if _has_jira_tools(state) else ""

    system_prompt = PLANNER_SYSTEM_PROMPT.format(
        available_tools=tool_descriptions,
        jira_guidance=jira_guidance
    )

    # Build user prompt with or without conversation context
    if previous_conversations:
        conversation_history = _format_conversation_history(previous_conversations, log)
        user_prompt = PLANNER_USER_TEMPLATE_WITH_CONTEXT.format(
            conversation_history=conversation_history,
            query=query
        )
        log.debug(f"📝 Planner using {len(previous_conversations)} previous messages for context")
    else:
        user_prompt = PLANNER_USER_TEMPLATE.format(query=query)

    # Add user context for informed decision-making (CRITICAL)
    user_context = _format_user_context(state)
    if user_context:
        user_prompt = user_prompt + "\n\n" + user_context
    else:
        user_info = state.get("user_info", {})
        user_email = state.get("user_email") or user_info.get("userEmail") or user_info.get("email")
        user_id = state.get("user_id") or user_info.get("userId")
        log.warning(f"⚠️ No user context available - user_info: {bool(user_info)}, user_email: {bool(user_email)}, user_id: {bool(user_id)}")
        if user_info:
            log.debug(f"⚠️ user_info keys: {list(user_info.keys())}")

    # Add retry context if this is a retry attempt
    if state.get("is_retry") and state.get("execution_errors"):
        errors = state["execution_errors"]
        reflection = state.get("reflection", {})
        fix_instruction = reflection.get("fix_instruction", "")

        # Build detailed retry context with the actual failed arguments
        error_summary = errors[0] if errors else {"tool_name": "unknown", "error": "unknown", "args": {}}
        failed_args = error_summary.get("args", {})
        failed_args_str = json.dumps(failed_args, indent=2) if failed_args else "No args provided"

        retry_context = f"""## 🔴 RETRY MODE - YOUR PREVIOUS ATTEMPT FAILED

**Failed Tool**: {error_summary.get('tool_name', 'unknown')}
**Error Message**: {error_summary.get('error', 'unknown')[:300]}

**Your Previous Args That Failed**:
```json
{failed_args_str}
```

**FIX INSTRUCTION**:
{fix_instruction}

**YOU MUST**:
1. Read the fix instruction carefully
2. Modify the args to fix the error
3. If the error was "Unbounded JQL" → your jql parameter MUST include `AND updated >= -30d`

Example for Unbounded JQL fix:
- Your failed jql: "project = PA AND assignee = currentUser()"
- Fixed jql: "project = PA AND assignee = currentUser() AND updated >= -30d"

DO NOT repeat the same args. Apply the fix instruction!

"""
        user_prompt = retry_context + user_prompt
        state["is_retry"] = False  # Reset after using
        log.info(f"🔄 Planner retry mode: fixing {error_summary.get('tool_name')}")

    try:
        # Build config with Opik tracer for visibility
        invoke_config = {"callbacks": [_opik_tracer]} if _opik_tracer else {}

        response = await asyncio.wait_for(
            llm.ainvoke(
                [
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=user_prompt)
                ],
                config=invoke_config
            ),
            timeout=20.0  # Allow 20s for planning
        )

        plan = _parse_planner_response(
            response.content if hasattr(response, 'content') else str(response),
            log
        )
        log.info(f"📋 Plan: intent='{plan.get('intent', 'N/A')[:50]}', tools={len(plan.get('tools', []))}")

    except asyncio.TimeoutError:
        log.warning("⚠️ Planner timeout - using fallback")
        plan = _create_fallback_plan(query, state.get("filters", {}))
    except Exception as e:
        log.error(f"Planner error: {e}")
        plan = _create_fallback_plan(query, state.get("filters", {}))

    # Store plan
    state["execution_plan"] = plan
    state["planned_tool_calls"] = plan.get("tools", [])
    state["pending_tool_calls"] = bool(plan.get("tools"))
    state["query_analysis"] = {
        "intent": plan.get("intent", ""),
        "reasoning": plan.get("reasoning", ""),
        "can_answer_directly": plan.get("can_answer_directly", False),
    }

    # Handle planner requesting clarification (route directly to respond)
    if plan.get("needs_clarification"):
        state["reflection_decision"] = "respond_clarify"
        state["reflection"] = {
            "decision": "respond_clarify",
            "reasoning": "Planner determined clarification is needed",
            "clarifying_question": plan.get("clarifying_question", "Could you please provide more details?")
        }
        log.info(f"📋 Planner requesting clarification: {plan.get('clarifying_question', '')[:50]}...")

    duration_ms = (time.perf_counter() - start_time) * 1000
    log.info(f"⚡ Planner: {duration_ms:.0f}ms")

    return state


# Tool description cache (module-level for persistence across requests)
_tool_description_cache: Dict[str, str] = {}


def _get_cached_tool_descriptions(state: ChatState, log: logging.Logger) -> str:
    """
    Get tool descriptions with caching for planning accuracy.

    Provides name, description, and EXACT parameters for each tool
    to help LLM make accurate tool selection and argument decisions.
    """
    org_id = state.get("org_id", "default")
    tools_list = state.get("tools", [])
    cache_key = f"{org_id}_{len(tools_list)}"

    if cache_key in _tool_description_cache:
        return _tool_description_cache[cache_key]

    try:
        from app.modules.agents.qna.tool_system import get_agent_tools
        tools = get_agent_tools(state)

        if not tools:
            return "- retrieval.search_internal_knowledge: Search internal knowledge base\n  Parameters: query (string, required)"

        # Include name, description, and PARAMETERS for accurate planning
        descriptions = []
        for tool in tools[:20]:  # Max 20 tools
            name = getattr(tool, 'name', str(tool))
            desc = getattr(tool, 'description', '')

            # Truncate description for overview
            short_desc = desc[:DESCRIPTION_MAX_LENGTH] + "..." if desc and len(desc) > DESCRIPTION_MAX_LENGTH else desc

            # Build tool entry with parameters
            tool_entry = f"- {name}"
            if short_desc:
                tool_entry += f": {short_desc}"

            # Extract parameters from the tool - CRITICAL for correct argument passing
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
    """
    Format previous conversations for the planner.

    This provides context for:
    - Follow-up queries ("try again", "do that again")
    - Reference resolution ("that project", "the first one")
    - Data reuse (project IDs, file names, etc. from previous responses)

    Args:
        conversations: List of conversation dicts with 'role' and 'content'
        log: Logger instance

    Returns:
        Formatted conversation history string with reference data
    """
    if not conversations:
        return ""

    # Take last 5 messages to keep context manageable but meaningful
    recent = conversations[-5:]

    lines = []
    all_reference_data = []

    for conv in recent:
        role = conv.get("role", "")
        content = conv.get("content", "")

        # Truncate very long responses but keep enough for context
        # For bot responses, keep more content as they may contain IDs, data, etc.
        if role == "user_query":
            content = content[:USER_QUERY_MAX_LENGTH] if len(content) > USER_QUERY_MAX_LENGTH else content
            lines.append(f"User: {content}")
        elif role == "bot_response":
            # Keep more of bot responses as they contain useful data
            content = content[:BOT_RESPONSE_MAX_LENGTH] if len(content) > BOT_RESPONSE_MAX_LENGTH else content
            lines.append(f"Assistant: {content}")

            # Extract referenceData if present (IDs for follow-up queries)
            ref_data = conv.get("referenceData", [])
            if ref_data:
                all_reference_data.extend(ref_data)

    result = "\n".join(lines)

    # Append reference data section if we have items from previous responses
    # This allows the LLM to use IDs when user refers to items from earlier
    if all_reference_data:
        result += "\n\n## Reference Data (IDs/Keys from previous responses - use for follow-up queries):\n"
        for item in all_reference_data[:15]:  # Limit to 15 items
            name = item.get("name", "Unknown")
            item_id = item.get("id", "")
            item_key = item.get("key", "")  # Important for Jira projects/issues
            item_type = item.get("type", "")
            account_id = item.get("accountId", "")  # For Jira users

            if item_id or item_key:
                # Build reference line with all available identifiers
                ref_parts = [f"{name} ({item_type})"]
                if item_key:
                    ref_parts.append(f"key=`{item_key}`")  # Most important for Jira JQL
                if item_id:
                    ref_parts.append(f"id=`{item_id}`")
                if account_id:
                    ref_parts.append(f"accountId=`{account_id}`")
                result += f"- {' | '.join(ref_parts)}\n"
        log.debug(f"📋 Included {len(all_reference_data)} reference items in conversation context")

    return result


def _parse_planner_response(content: str, log: logging.Logger) -> Dict[str, Any]:
    """Parse LLM planner response into execution plan."""
    content = content.strip()

    # Remove markdown code blocks if present
    if "```json" in content:
        match = re.search(r'```json\s*(.*?)\s*```', content, re.DOTALL)
        if match:
            content = match.group(1)
    elif content.startswith("```"):
        content = re.sub(r'^```\s*\n?', '', content)
        content = re.sub(r'\n?```\s*$', '', content)

    try:
        plan = json.loads(content)

        # Validate structure
        if isinstance(plan, dict):
            # Ensure required fields
            plan.setdefault("intent", "")
            plan.setdefault("reasoning", "")
            plan.setdefault("can_answer_directly", False)
            plan.setdefault("needs_clarification", False)
            plan.setdefault("clarifying_question", "")
            plan.setdefault("tools", [])

            # Normalize tool format
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

    # Return default plan if parsing fails
    return {
        "intent": "Unable to parse plan",
        "reasoning": "Parsing failed, using fallback",
        "can_answer_directly": False,
        "needs_clarification": False,
        "clarifying_question": "",
        "tools": [{"name": "retrieval.search_internal_knowledge", "args": {"query": ""}}]
    }


def _create_fallback_plan(query: str, filters: Dict) -> Dict[str, Any]:
    """Create fallback plan when LLM planning fails."""
    # If filters suggest internal data, use retrieval
    if filters.get("kb") or filters.get("apps"):
        return {
            "intent": "Fallback: Search internal knowledge",
            "reasoning": "Planner failed, using fallback with retrieval",
            "can_answer_directly": False,
            "needs_clarification": False,
            "clarifying_question": "",
            "tools": [{"name": "retrieval.search_internal_knowledge", "args": {"query": query}}]
        }

    # Default to direct answer for simple fallback
    return {
        "intent": "Fallback: Direct response",
        "reasoning": "Planner failed, attempting direct response",
        "can_answer_directly": True,
        "needs_clarification": False,
        "clarifying_question": "",
        "tools": []
    }


# =============================================================================
# NODE 2: EXECUTE (Parallel Tool Execution)
# =============================================================================

async def execute_node(
    state: ChatState,
    config: RunnableConfig,
    writer: StreamWriter
) -> ChatState:
    """
    Execute all planned tools in parallel.

    This node:
    1. Takes the planned tools from planner
    2. Executes them all in parallel using asyncio.gather
    3. Collects results and handles retrieval output specially for citations
    Args:
        state: Current chat state with planned_tool_calls
        config: Runnable configuration
        writer: Stream writer

    Returns:
        Updated state with tool results
    """
    start_time = time.perf_counter()
    log = state.get("logger", logger)

    planned_tools = state.get("planned_tool_calls", [])

    if not planned_tools:
        log.info("No tools to execute, skipping execute node")
        state["pending_tool_calls"] = False
        return state

    safe_stream_write(writer, {
        "event": "status",
        "data": {
            "status": "executing",
            "message": f"Executing {len(planned_tools)} tool(s)..."
        }
    }, config)

    # Get tool instances
    try:
        from app.modules.agents.qna.tool_system import get_agent_tools
        tools = get_agent_tools(state)
        tools_by_name = {t.name: t for t in tools} if tools else {}
    except Exception as e:
        log.error(f"Failed to get tool instances: {e}")
        tools_by_name = {}

    # Create execution tasks
    tasks = []
    for i, tool_call in enumerate(planned_tools[:NodeConfig.MAX_PARALLEL_TOOLS]):
        tool_name = tool_call.get("name", "")
        tool_args = tool_call.get("args", {})
        tool_id = f"call_{i}_{tool_name}"

        # Debug: Log tool call details before execution
        log.debug(f"🔧 Planning to execute {tool_name} with args: {json.dumps(tool_args, indent=2, default=str)}")

        # Normalize tool name (support both underscore and dot formats)
        normalized_name = _normalize_tool_name(tool_name, tools_by_name)

        if normalized_name and normalized_name in tools_by_name:
            tasks.append(_execute_single_tool(
                tool=tools_by_name[normalized_name],
                tool_name=normalized_name,
                tool_args=tool_args,
                tool_id=tool_id,
                state=state,
                log=log
            ))
        else:
            available_tools = list(tools_by_name.keys())[:10]  # Show first 10 available tools
            log.warning(f"❌ Tool not found: {tool_name} (tried normalized: {normalized_name})")
            log.debug(f"❌ Tool '{tool_name}' not found - available tools (first 10): {available_tools}")
            log.debug(f"❌ Tool '{tool_name}' not found - requested args: {json.dumps(tool_args, indent=2, default=str)}")
            tasks.append(_create_error_result(tool_name, tool_id, f"Tool '{tool_name}' not found in available tools"))

    # Execute all tools in parallel
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Process results
    tool_results = []
    tool_messages = []
    success_count = 0
    failed_count = 0

    for result in results:
        if isinstance(result, Exception):
            log.error(f"Tool execution exception: {result}")
            import traceback
            log.debug(f"Tool execution exception traceback:\n{traceback.format_exc()}")
            continue

        if isinstance(result, dict):
            tool_result = result.get("tool_result", {})
            tool_results.append(tool_result)

            status = tool_result.get("status", "unknown")
            tool_name = tool_result.get("tool_name", "unknown")

            if status == "success":
                success_count += 1
                # Debug: Log successful tool result summary
                result_preview = str(tool_result.get("result", ""))[:200]
                log.debug(f"✅ {tool_name} succeeded - result preview: {result_preview}")
            elif status == "error":
                failed_count += 1
                # Debug: Log failed tool result details
                error_result = tool_result.get("result", "Unknown error")
                error_args = tool_result.get("args", {})
                log.debug(f"❌ {tool_name} failed:")
                log.debug(f"   - Error result: {error_result}")
                log.debug(f"   - Failed args: {json.dumps(error_args, indent=2, default=str)}")

            if "tool_message" in result:
                tool_messages.append(result["tool_message"])

    # Debug: Log aggregated tool results summary
    log.debug(f"📊 Tool execution summary: {len(tool_results)} total, {success_count} succeeded, {failed_count} failed")
    if failed_count > 0:
        failed_tools = [r for r in tool_results if r.get("status") == "error"]
        log.debug("❌ Failed tools details:")
        for failed_tool in failed_tools:
            tool_name = failed_tool.get("tool_name", "unknown")
            error_result = failed_tool.get("result", "Unknown error")
            error_args = failed_tool.get("args", {})
            log.debug(f"   - {tool_name}: {error_result}")
            log.debug(f"     Args: {json.dumps(error_args, indent=2, default=str)}")

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
    """Execute a single tool with error handling and timeout."""
    start_time = time.perf_counter()

    try:
        # Normalize args
        if isinstance(tool_args, dict) and "kwargs" in tool_args and len(tool_args) == 1:
            tool_args = tool_args["kwargs"]

        # Debug: Log tool arguments before execution
        log.debug(f"🔧 Executing {tool_name} with args: {json.dumps(tool_args, indent=2, default=str)}")

        # Execute tool
        async def run_tool() -> object:
            if hasattr(tool, 'arun'):
                return await tool.arun(tool_args)  # type: ignore[union-attr]
            elif hasattr(tool, '_run'):
                loop = asyncio.get_running_loop()
                return await loop.run_in_executor(
                    None, functools.partial(tool._run, **tool_args)  # type: ignore[union-attr]
                )
            else:
                loop = asyncio.get_running_loop()
                return await loop.run_in_executor(
                    None, functools.partial(tool.run, **tool_args)  # type: ignore[union-attr]
                )

        result = await asyncio.wait_for(run_tool(), timeout=NodeConfig.TOOL_TIMEOUT_SECONDS)
        is_success = _detect_tool_success(result)

        # Debug: Log raw tool result
        result_preview = str(result)[:500] if result else "None"
        log.debug(f"📦 {tool_name} raw result (first 500 chars): {result_preview}")
        if isinstance(result, (dict, list)):
            try:
                result_json = json.dumps(result, indent=2, default=str)[:1000]
                log.debug(f"📦 {tool_name} result (JSON, first 1000 chars):\n{result_json}")
            except Exception:
                pass

        # Special handling for retrieval tool - extract citation data
        content = result
        if "retrieval" in tool_name.lower() or "search_internal_knowledge" in tool_name:
            content = _process_retrieval_output(result, state, log)
        else:
            # Clean tool results - remove verbose fields, keep essential data
            content = clean_tool_result(result)

        duration_ms = (time.perf_counter() - start_time) * 1000
        status = "success" if is_success else "error"

        # Log result size for monitoring
        original_size = len(str(result))
        cleaned_size = len(str(content))
        reduction = ((original_size - cleaned_size) / max(original_size, 1)) * 100
        log.info(f"{'✅' if is_success else '❌'} {tool_name}: {duration_ms:.0f}ms | {original_size}→{cleaned_size} chars ({reduction:.0f}% cleaned)")

        # Debug: Log cleaned result for success cases
        if is_success:
            content_preview = str(content)[:500] if content else "None"
            log.debug(f"✅ {tool_name} cleaned result (first 500 chars): {content_preview}")
        else:
            # For errors, log the full error details
            error_details = str(result) if result else "Unknown error"
            log.debug(f"❌ {tool_name} error details: {error_details}")
            if isinstance(result, dict):
                error_json = json.dumps(result, indent=2, default=str)
                log.debug(f"❌ {tool_name} error (JSON):\n{error_json}")

        # Format content for LLM consumption
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
        error_msg = f"Tool execution timed out after {duration_ms:.0f}ms (timeout: {NodeConfig.TOOL_TIMEOUT_SECONDS}s)"
        log.error(f"❌ {tool_name} timed out after {duration_ms:.0f}ms")
        log.debug(f"❌ {tool_name} timeout details: args={json.dumps(tool_args, indent=2, default=str)}")
        return _create_error_result_sync(tool_name, tool_id, error_msg)

    except Exception as e:
        duration_ms = (time.perf_counter() - start_time) * 1000
        error_type = type(e).__name__
        error_msg = str(e)

        # Log full error details
        log.error(f"❌ {tool_name} failed after {duration_ms:.0f}ms: {error_type}: {error_msg}")
        log.debug(f"❌ {tool_name} error details:")
        log.debug(f"   - Error type: {error_type}")
        log.debug(f"   - Error message: {error_msg}")
        log.debug(f"   - Tool args: {json.dumps(tool_args, indent=2, default=str)}")

        # Log full traceback for debugging
        import traceback
        tb_str = traceback.format_exc()
        log.debug(f"   - Traceback:\n{tb_str}")

        # Try to extract more error details if it's a structured error
        if hasattr(e, '__dict__'):
            try:
                error_dict = {k: str(v) for k, v in e.__dict__.items()}
                log.debug(f"   - Error attributes: {json.dumps(error_dict, indent=2, default=str)}")
            except Exception:
                pass

        return _create_error_result_sync(tool_name, tool_id, f"{error_type}: {error_msg}")


async def _create_error_result(tool_name: str, tool_id: str, error: str) -> Dict:
    """Create async error result."""
    return _create_error_result_sync(tool_name, tool_id, error)


def _create_error_result_sync(tool_name: str, tool_id: str, error: str) -> Dict:
    """Create error result for failed tool."""
    return {
        "tool_result": {
                    "tool_name": tool_name,
            "result": f"Error: {error}",
                    "status": "error",
            "tool_id": tool_id
        },
        "tool_message": ToolMessage(content=f"Error: {error}", tool_call_id=tool_id)
    }


def _normalize_tool_name(tool_name: str, tools_by_name: Dict[str, Any]) -> Optional[str]:
    """
    Normalize tool name to match registry format.

    Handles both formats:
    - dot format: retrieval.search_internal_knowledge (correct)
    - underscore format: retrieval_search_internal_knowledge (legacy)

    Args:
        tool_name: Tool name from planner
        tools_by_name: Available tools dictionary

    Returns:
        Normalized tool name or None if not found
    """
    # Direct match
    if tool_name in tools_by_name:
        return tool_name

    # Try converting underscore to dot (first underscore only)
    if "_" in tool_name and "." not in tool_name:
        parts = tool_name.split("_", 1)
        if len(parts) == TOOL_RESULT_TUPLE_LENGTH:  # Expecting exactly 2 parts
            dot_name = f"{parts[0]}.{parts[1]}"
            if dot_name in tools_by_name:
                return dot_name

    # Try partial match on tool function name
    for name in tools_by_name:
        # Match if the tool function name matches (after the dot)
        if "." in name:
            _, func_name = name.split(".", 1)
            if func_name == tool_name or tool_name.endswith(func_name):
                return name

    return None


def _detect_tool_success(result: object) -> bool:
    """Detect if tool execution was successful."""
    if result is None:
        return False

    result_str = str(result).lower()
    error_indicators = [
        "error:", '"error"', "'error'",
        "failed", "failure", "exception",
        "traceback", "status_code: 4", "status_code: 5"
    ]
    return not any(ind in result_str for ind in error_indicators)


def _process_retrieval_output(result: object, state: ChatState, log: logging.Logger) -> str:
    """
    Process retrieval tool output and extract citation data.

    The retrieval tool returns a RetrievalToolOutput with:
    - content: Formatted content for LLM
    - final_results: Results for citation generation
    - virtual_record_id_to_result: Mapping for citations

    We extract these and store in state for the respond node.
    """
    try:
        from app.agents.actions.retrieval.retrieval import RetrievalToolOutput

        retrieval_output = None

        # Handle different result formats
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
            # Store citation data in state
            state["final_results"] = retrieval_output.final_results
            state["virtual_record_id_to_result"] = retrieval_output.virtual_record_id_to_result

            # Also populate tool_records from virtual_record_id_to_result for citation normalization
            # This provides the full record data for better citations
            if retrieval_output.virtual_record_id_to_result:
                state["tool_records"] = list(retrieval_output.virtual_record_id_to_result.values())

            log.info(f"📚 Retrieved {len(retrieval_output.final_results)} knowledge blocks for citations")

            return retrieval_output.content

    except Exception as e:
        log.warning(f"Could not process retrieval output: {e}")

    return str(result)


# =============================================================================
# NODE 3: REFLECT (Intelligent Error Analysis and Recovery)
# =============================================================================

async def reflect_node(
    state: ChatState,
    config: RunnableConfig,
    writer: StreamWriter
) -> ChatState:
    """
    Analyze tool execution results and decide next action.

    Uses fast-path pattern matching for common cases to avoid LLM calls:
    - All succeeded -> respond_success (0ms)
    - Permission errors -> respond_error (0ms)
    - Unbounded JQL -> retry_with_fix (0ms)

    Only calls LLM for ambiguous cases (~3s).
    """
    start_time = time.perf_counter()
    log = state.get("logger", logger)

    tool_results = state.get("all_tool_results", [])
    retry_count = state.get("retry_count", 0)
    max_retries = state.get("max_retries", 1)

    # =========================================================================
    # FAST PATH 1: All tools succeeded -> respond_success (0ms, no LLM)
    # =========================================================================
    failed = [r for r in tool_results if r.get("status") == "error"]

    if not failed:
        state["reflection_decision"] = "respond_success"
        state["reflection"] = {"decision": "respond_success", "reasoning": "All tools succeeded"}
        duration_ms = (time.perf_counter() - start_time) * 1000
        log.info(f"🪞 Reflect: respond_success (fast-path, {duration_ms:.0f}ms)")
        return state

    # =========================================================================
    # FAST PATH 2: Pattern-match common errors (0ms, no LLM)
    # =========================================================================
    error_text = " ".join(str(r.get("result", "")) for r in failed).lower()

    # Unrecoverable errors - don't retry, go to respond_error
    unrecoverable_patterns = [
        "permission", "unauthorized", "forbidden", "403",
        "not found", "does not exist", "404",
        "authentication", "auth failed", "invalid token",
        "rate limit", "quota exceeded"
    ]
    if any(pattern in error_text for pattern in unrecoverable_patterns):
        # Extract user-friendly error context
        error_context = "Permission or access issue"
        if "not found" in error_text or "does not exist" in error_text:
            error_context = "The requested resource could not be found"
        elif "rate limit" in error_text or "quota" in error_text:
            error_context = "Service rate limit reached, please try again later"

        state["reflection_decision"] = "respond_error"
        state["reflection"] = {
            "decision": "respond_error",
            "reasoning": "Unrecoverable error detected",
            "error_context": error_context
        }
        duration_ms = (time.perf_counter() - start_time) * 1000
        log.info(f"🪞 Reflect: respond_error (fast-path, {duration_ms:.0f}ms)")
        return state

    # =========================================================================
    # FAST PATH 3: Recoverable errors with known fixes (0ms, no LLM)
    # =========================================================================

    # JQL unbounded query error - handle specially
    if "unbounded" in error_text:
        if retry_count < max_retries:
            # First attempt: try to fix with a time filter
            state["reflection_decision"] = "retry_with_fix"
            state["reflection"] = {
                "decision": "retry_with_fix",
                "reasoning": "JQL query is unbounded, need to add time filter",
                "fix_instruction": """CRITICAL FIX REQUIRED - The JQL query MUST include a time filter.

Your previous JQL query was rejected because it has no time bounds.
You MUST modify the jql parameter to include: updated >= -30d

Example fixes:
- If query was: "project = PA AND assignee = currentUser()"
- Fix it to: "project = PA AND assignee = currentUser() AND updated >= -30d"

IMPORTANT: Keep all original filters (project, assignee, etc.) and ADD the time filter."""
            }
            duration_ms = (time.perf_counter() - start_time) * 1000
            log.info(f"🪞 Reflect: retry_with_fix (unbounded JQL, {duration_ms:.0f}ms)")
            return state
        else:
            # After retry failed, ask user for time range clarification
            state["reflection_decision"] = "respond_clarify"
            state["reflection"] = {
                "decision": "respond_clarify",
                "reasoning": "JQL query requires a time bound but we couldn't determine the right one",
                "clarifying_question": "I need to narrow down the search. What time period would you like me to search? For example:\n- Last 7 days\n- Last 30 days\n- Last 3 months\n- A specific date range\n\nPlease let me know and I'll fetch the tickets for you."
            }
            duration_ms = (time.perf_counter() - start_time) * 1000
            log.info(f"🪞 Reflect: respond_clarify (unbounded JQL after retry, {duration_ms:.0f}ms)")
            return state

    if retry_count < max_retries:
        # Syntax or format errors
        if any(x in error_text for x in ["syntax", "invalid", "malformed", "parse error"]):
            state["reflection_decision"] = "retry_with_fix"
            state["reflection"] = {
                "decision": "retry_with_fix",
                "reasoning": "Query syntax error, need to fix format",
                "fix_instruction": "Fix query syntax based on the error message. Check for typos, missing quotes, or invalid field names."
            }
            duration_ms = (time.perf_counter() - start_time) * 1000
            log.info(f"🪞 Reflect: retry_with_fix (syntax error, {duration_ms:.0f}ms)")
            return state

        # User not found errors
        if "user" in error_text and ("not found" in error_text or "no user" in error_text):
            state["reflection_decision"] = "retry_with_fix"
            state["reflection"] = {
                "decision": "retry_with_fix",
                "reasoning": "User not found, need to search for user first",
                "fix_instruction": "Use search_users tool first to find the correct user ID, then use that ID in your query."
            }
            duration_ms = (time.perf_counter() - start_time) * 1000
            log.info(f"🪞 Reflect: retry_with_fix (user not found, {duration_ms:.0f}ms)")
            return state

    # =========================================================================
    # SLOW PATH: LLM for ambiguous cases (~3s with 8s timeout)
    # =========================================================================
    llm = state.get("llm")

    # Build execution summary
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
                HumanMessage(content="Analyze and decide the best action.")
            ]),
            timeout=8.0  # Fast timeout for reflection
        )

        reflection = _parse_reflection_response(response.content, log)

    except asyncio.TimeoutError:
        log.warning("⚠️ Reflect LLM timeout, defaulting to respond_error")
        reflection = {
            "decision": "respond_error",
            "reasoning": "Analysis timed out",
            "error_context": "Unable to complete the request. Please try again."
        }
    except Exception as e:
        log.error(f"Reflection failed: {e}")
        reflection = {
            "decision": "respond_error",
            "reasoning": str(e),
            "error_context": "An error occurred while processing your request."
        }

    state["reflection"] = reflection
    state["reflection_decision"] = reflection.get("decision", "respond_error")

    duration_ms = (time.perf_counter() - start_time) * 1000
    log.info(f"🪞 Reflect: {state['reflection_decision']} (LLM, {duration_ms:.0f}ms)")

    return state


def _parse_reflection_response(content: str, log: logging.Logger) -> Dict[str, Any]:
    """Parse LLM reflection response into structured data."""
    content = content.strip()

    # Remove markdown code blocks if present
    if "```json" in content:
        match = re.search(r'```json\s*(.*?)\s*```', content, re.DOTALL)
        if match:
            content = match.group(1)
    elif content.startswith("```"):
        content = re.sub(r'^```\s*\n?', '', content)
        content = re.sub(r'\n?```\s*$', '', content)

    try:
        reflection = json.loads(content)

        # Validate structure
        if isinstance(reflection, dict):
            reflection.setdefault("decision", "respond_error")
            reflection.setdefault("reasoning", "")
            reflection.setdefault("fix_instruction", "")
            reflection.setdefault("clarifying_question", "")
            reflection.setdefault("error_context", "")
            return reflection

    except json.JSONDecodeError as e:
        log.warning(f"Failed to parse reflection response: {e}")

    # Default fallback
    return {
        "decision": "respond_error",
        "reasoning": "Failed to parse reflection",
        "error_context": "Unable to process the request. Please try again."
    }


# =============================================================================
# NODE 4: PREPARE RETRY (Set up state for retry attempt)
# =============================================================================

async def prepare_retry_node(
    state: ChatState,
    config: RunnableConfig,
    writer: StreamWriter
) -> ChatState:
    """
    Prepare state for retry with error context.

    This node:
    1. Increments retry counter
    2. Extracts error details for planner
    3. Clears old tool results for fresh retry
    """
    log = state.get("logger", logger)

    # Increment retry counter
    state["retry_count"] = state.get("retry_count", 0) + 1
    state["is_retry"] = True

    # Extract error details for planner
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

    # Clear old tool results for fresh retry
    state["all_tool_results"] = []
    state["tool_results"] = []

    safe_stream_write(writer, {
        "event": "status",
        "data": {"status": "retrying", "message": "Retrying with adjusted approach..."}
    }, config)

    log.info(f"🔄 Prepare retry {state['retry_count']}/{state.get('max_retries', 1)}: {len(errors)} errors to fix")

    return state


def route_after_reflect(state: ChatState) -> Literal["prepare_retry", "respond"]:
    """
    Route based on reflection decision.

    Returns:
        "prepare_retry" if we should retry with a fix
        "respond" for all other cases (success, error, clarify)
    """
    decision = state.get("reflection_decision", "respond_success")
    retry_count = state.get("retry_count", 0)
    max_retries = state.get("max_retries", 1)

    if decision == "retry_with_fix" and retry_count < max_retries:
        return "prepare_retry"

    return "respond"


# =============================================================================
# NODE 5: RESPOND (Final Response with Real-time Streaming like Chatbot)
# =============================================================================

async def respond_node(
    state: ChatState,
    config: RunnableConfig,
    writer: StreamWriter
) -> ChatState:
    """
    Generate final response with REAL-TIME streaming like chatbot.

    This streams tokens directly as they arrive from the LLM,
    providing smooth visual feedback like ChatGPT/Claude.
    """
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
        safe_stream_write(writer, {"event": "answer_chunk", "data": {"chunk": error_msg, "accumulated": error_msg, "citations": []}}, config)
        safe_stream_write(writer, {"event": "complete", "data": error_response}, config)
        state["response"] = error_msg
        state["completion_data"] = error_response
        return state

    # Check if we can answer directly (no tools were used)
    execution_plan = state.get("execution_plan", {})
    tool_results = state.get("all_tool_results", [])

    if execution_plan.get("can_answer_directly") and not tool_results:
        response = await _generate_direct_response_streaming(state, llm, log, writer, config)
        completion = {"answer": response, "citations": [], "confidence": "High", "answerMatchType": "Direct Response"}
        safe_stream_write(writer, {"event": "complete", "data": completion}, config)
        state["response"] = response
        state["completion_data"] = completion
        return state

    # Get final_results and ensure it's a list
    final_results = state.get("final_results", [])
    if not isinstance(final_results, list):
        if isinstance(final_results, str):
            try:
                final_results = json.loads(final_results)
            except (json.JSONDecodeError, TypeError):
                log.warning(f"final_results is not valid JSON: {type(final_results)}")
                final_results = []
        else:
            log.warning(f"final_results is not a list: {type(final_results)}")
            final_results = []

    virtual_record_map = state.get("virtual_record_id_to_result", {})
    tool_records = state.get("tool_records", [])

    log.info(f"📊 Citation data: {len(final_results)} results, {len(virtual_record_map)} records")

    # Analyze tool execution outcomes
    successful_count = sum(1 for r in tool_results if r.get("status") == "success")
    failed_count = sum(1 for r in tool_results if r.get("status") == "error")
    log.info(f"📊 Tool execution: {successful_count} succeeded, {failed_count} failed")

    # =========================================================================
    # Handle reflection decisions (clarify, error, or continue to success)
    # =========================================================================
    reflection_decision = state.get("reflection_decision", "respond_success")
    reflection = state.get("reflection", {})

    # Case 1: Clarification needed - ask user for more info
    if reflection_decision == "respond_clarify":
        clarifying_question = reflection.get("clarifying_question", "Could you please provide more details about your request?")

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

        duration_ms = (time.perf_counter() - start_time) * 1000
        log.info(f"✅ respond_node completed (clarification) in {duration_ms:.0f}ms")
        return state

    # Case 2: Unrecoverable error - give user-friendly error message
    if reflection_decision == "respond_error" or (failed_count > 0 and successful_count == 0 and not final_results):
        error_context = reflection.get("error_context", "")

        # Use reflection context if available, otherwise generate friendly message
        if error_context:
            error_msg = f"I wasn't able to complete that request. {error_context}\n\nPlease try again in a moment, or rephrase your question."
        else:
            error_msg = "I wasn't able to complete that request right now. This could be due to a temporary connection issue or service unavailability. Please try again in a moment, or rephrase your question."

        error_response = {
            "answer": error_msg,
            "citations": [],
            "confidence": "Low",
            "answerMatchType": "Tool Execution Failed",
            "reason": f"{failed_count} tool(s) failed to execute" if failed_count > 0 else "Unable to process request"
        }

        safe_stream_write(writer, {
            "event": "answer_chunk",
            "data": {"chunk": error_msg, "accumulated": error_msg, "citations": []}
        }, config)
        safe_stream_write(writer, {"event": "complete", "data": error_response}, config)

        state["response"] = error_msg
        state["completion_data"] = error_response

        duration_ms = (time.perf_counter() - start_time) * 1000
        log.info(f"✅ respond_node completed (error) in {duration_ms:.0f}ms")
        return state

    # Case 3: Success - continue to generate response with tool results
    # (reflect_decision == "respond_success" or some tools succeeded)

    # Build messages using response synthesis prompt system
    messages = create_response_messages(state)

    # Add tool results context
    if tool_results or final_results:
        context = _build_tool_results_context(tool_results, final_results)
        if context.strip():
            if messages and isinstance(messages[-1], HumanMessage):
                messages[-1].content += context
            else:
                messages.append(HumanMessage(content=context))

    # Use the EXACT same streaming function as chatbot.py - stream_llm_response from streaming.py
    # This is the proven, working implementation
    try:
        log.info("📡 Using stream_llm_response from streaming.py (same as chatbot)...")

        answer_text = ""
        citations = []
        reason = None
        confidence = None

        # Call stream_llm_response exactly like chatbot does
        # See chatbot.py lines 668-688 and streaming.py stream_llm_response
        async for stream_event in stream_llm_response(
            llm=llm,
            messages=messages,
            final_results=final_results,
            logger=log,
            target_words_per_chunk=1,
            mode="json",  # Use JSON mode like chatbot
            virtual_record_id_to_result=virtual_record_map,
            records=tool_records,
        ):
            event_type = stream_event.get("event")
            event_data = stream_event.get("data", {})

            # Forward events to the stream writer (same as chatbot yields SSE events)
            safe_stream_write(writer, {"event": event_type, "data": event_data}, config)

            # Capture final data from complete event
            if event_type == "complete":
                answer_text = event_data.get("answer", "")
                citations = event_data.get("citations", [])
                reason = event_data.get("reason")
                confidence = event_data.get("confidence")
                # Capture referenceData for follow-up queries (IDs, keys stored but not shown to user)
                reference_data = event_data.get("referenceData", [])

        # Check for empty response and provide fallback
        if not answer_text or len(answer_text.strip()) == 0:
            log.warning("⚠️ LLM returned empty response, using fallback")
            answer_text = "I wasn't able to generate a response for that request. Please try rephrasing your question or try again."

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
            # Store in state normally - include referenceData for follow-up context
            completion_data = {
                "answer": answer_text,
                "citations": citations,
                "reason": reason,
                "confidence": confidence,
            }
            # Store referenceData if present (IDs/keys for follow-up queries)
            if reference_data:
                completion_data["referenceData"] = reference_data
                log.debug(f"📋 Stored {len(reference_data)} reference items for follow-up queries")

            state["response"] = answer_text
            state["completion_data"] = completion_data

        log.info(f"✅ Generated response: {len(answer_text)} chars, {len(citations)} citations")

    except Exception as e:
        log.error(f"Response generation failed: {e}", exc_info=True)
        error_msg = "I encountered an issue processing your request. Please try again."
        error_response = {"answer": error_msg, "citations": [], "confidence": "Low", "answerMatchType": "Error"}
        safe_stream_write(writer, {"event": "answer_chunk", "data": {"chunk": error_msg, "accumulated": error_msg, "citations": []}}, config)
        safe_stream_write(writer, {"event": "complete", "data": error_response}, config)
        state["response"] = error_msg
        state["completion_data"] = error_response

    duration_ms = (time.perf_counter() - start_time) * 1000
    log.info(f"✅ respond_node completed in {duration_ms:.0f}ms")

    return state


def _extract_answer_from_response(content: str, log: logging.Logger) -> tuple:
    """
    Extract answer from LLM response, handling various formats.

    Handles:
    - Pure JSON: {"answer": "...", ...}
    - JSON with text before: "Planned query: ... { "answer": "..." }"
    - JSON in code blocks: ```json {...} ```
    - Plain text (no JSON)

    Args:
        content: Raw LLM response
        log: Logger instance

    Returns:
        Tuple of (answer_text, parsed_dict)
    """
    content = content.strip()

    # Try to extract JSON from response
    json_match = None

    # Pattern 1: JSON in code block
    code_block_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
    if code_block_match:
        json_match = code_block_match.group(1)

    # Pattern 2: Find JSON object anywhere in the text
    if not json_match:
        # Find the first { and match to closing }
        brace_match = re.search(r'\{[^{}]*"answer"[^{}]*\}|\{.*?"answer".*?\}', content, re.DOTALL)
        if brace_match:
            json_match = brace_match.group(0)
        else:
            # Try to find any JSON object
            start = content.find('{')
            if start != -1:
                # Find matching closing brace
                depth = 0
                for i, char in enumerate(content[start:], start):
                    if char == '{':
                        depth += 1
                    elif char == '}':
                        depth -= 1
                        if depth == 0:
                            json_match = content[start:i+1]
                            break

    # Try to parse JSON
    if json_match:
        try:
            parsed = json.loads(json_match)
            if isinstance(parsed, dict) and "answer" in parsed:
                answer = parsed.get("answer", "")
                log.debug(f"Extracted answer from JSON ({len(answer)} chars)")
                return answer, parsed
        except json.JSONDecodeError as e:
            log.debug(f"Failed to parse JSON: {e}")

    # Fallback: try detect_response_mode
    try:
        mode, parsed = detect_response_mode(content)
        if isinstance(parsed, dict) and "answer" in parsed:
            return parsed.get("answer", content), parsed
    except Exception:
        pass

    # No JSON found - return content as-is
    return content, {}


async def _generate_direct_response_streaming(
    state: ChatState,
    llm: object,
    log: logging.Logger,
    writer: StreamWriter,
    config: RunnableConfig
) -> str:
    """Generate a direct response for simple queries with real streaming."""
    query = state.get("query", "")
    previous = state.get("previous_conversations", [])

    # Build context from history
    context_lines = []
    for conv in previous[-3:]:
        role = conv.get("role", "")
        content = conv.get("content", "")[:200]
        if role == "user_query":
            context_lines.append(f"User: {content}")
        elif role == "bot_response":
            context_lines.append(f"Assistant: {content}...")

    context = "\n".join(context_lines) if context_lines else ""

    # Include user information if available (CRITICAL for queries like "give info about me")
    user_context = _format_user_context(state)
    user_info_section = f"\n\n{user_context}" if user_context else ""

    # Build system message with user context guidance
    system_content = "You are a helpful, friendly AI assistant. Respond naturally and concisely."
    if user_context:
        system_content += "\n\nIMPORTANT: User information is provided below. When the user asks about themselves (e.g., 'my name', 'my email', 'who am I', 'give info about me'), use the information provided to answer directly."

    # Build user message with query and user context
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
    """
    Build context from tool results for response generation.

    NOTE: Internal knowledge is already formatted in the system prompt via
    build_internal_context_for_response. This function adds:
    - API tool results (non-retrieval) with formatting
    - Synthesis instructions
    - Error handling context
    """
    # Analyze outcomes
    successful = [r for r in tool_results if r.get("status") == "success"]
    failed = [r for r in tool_results if r.get("status") == "error"]
    has_retrieval = bool(final_results)
    non_retrieval = [r for r in successful if "retrieval" not in r.get("tool_name", "").lower()]

    parts = []

    # Scenario 1: All failed
    if failed and not successful:
        parts.append("\n## ⚠️ Tools Failed\n")
        for r in failed[:3]:
            err = r.get("result", "Unknown error")
            if isinstance(err, dict):
                err = err.get("error", str(err))
            parts.append(f"- {r.get('tool_name', 'unknown')}: {str(err)[:200]}\n")
        parts.append("\n**INSTRUCTIONS**: Acknowledge the error in a friendly way. Explain what went wrong and ask for clarification if needed.\n")
        return "".join(parts)

    # Scenario 2: Tools succeeded but returned EMPTY results
    def _is_empty_result(result: object) -> bool:
        """Check if a result is empty (no data found)."""
        if result is None:
            return True
        if isinstance(result, (list, dict)):
            if isinstance(result, list) and len(result) == 0:
                return True
            if isinstance(result, dict):
                # Check for common patterns: {"issues": [], "total": 0}, etc.
                for key in ["issues", "items", "results", "data", "records", "values"]:
                    if key in result and isinstance(result[key], list) and len(result[key]) == 0:
                        return True
                # Check for total/count = 0
                for key in ["total", "count", "size"]:
                    if key in result and result[key] == 0:
                        return True
        return False

    empty_results = [r for r in successful if _is_empty_result(r.get("result"))]
    if empty_results and len(empty_results) == len(successful):
        parts.append("\n## 📭 No Results Found\n\n")
        parts.append("The search completed successfully but found **zero matching items**.\n\n")
        parts.append("**Tools that returned empty**:\n")
        for r in empty_results:
            tool_name = r.get("tool_name", "unknown")
            args = r.get("args", {})
            args_str = ", ".join(f"{k}={v}" for k, v in args.items()) if args else "no filters"
            parts.append(f"- `{tool_name}` (searched with: {args_str})\n")

        parts.append("""
**YOUR RESPONSE MUST**:
1. Clearly explain that no results were found for the specific search
2. Explain what was searched (mention the project, time range, filters used)
3. Suggest SPECIFIC ways to modify the query, such as:
   - Try a different time range (e.g., "last 3 months" instead of "last 30 days")
   - Check if the project name/key is correct
   - Try broader search criteria
   - Check if the user has access to the data
4. Ask a clarifying question to help refine the search

Example response:
"I searched for tickets assigned to you in the **PipesHub AI** project (last 30 days) but found no matches.

This could mean:
- No tickets are currently assigned to you in this project
- There are tickets, but they were last updated more than 30 days ago

Would you like me to:
- Search for a longer time period (last 3 months)?
- Search across all projects?
- Look for tickets you created instead?"
""")
        return "".join(parts)

    # Scenario 3: Has data - build response instructions

    # For retrieval results - they're already in the system prompt with full formatting
    # Just add synthesis reminder here
    if has_retrieval:
        parts.append("\n## ⚠️ REMINDER: Internal Knowledge Available\n\n")
        parts.append(f"You have access to **{len(final_results)} knowledge blocks** in the system context above.\n")
        parts.append("**YOU MUST**:\n")
        parts.append("1. Answer using the knowledge blocks from the context above\n")
        parts.append("2. Cite IMMEDIATELY after each fact: [R1-1], [R2-3]\n")
        parts.append("3. Example: \"Revenue grew 29% [R1-1]. Cash improved $142M [R1-2].\"\n")
        parts.append("4. Include ALL cited block numbers in the blockNumbers array\n\n")

    # For API tool results - include the actual data here
    if non_retrieval:
        parts.append("\n## API Tool Results\n\n")
        parts.append("Transform this data into professional, user-friendly markdown.\n")
        parts.append("**DO NOT show raw IDs** - store them in referenceData for follow-up queries.\n\n")

        for r in non_retrieval[:5]:
            tool_name = r.get('tool_name', 'unknown')
            content = r.get("result", "")

            # Format the tool result
            if isinstance(content, (dict, list)):
                content_str = json.dumps(content, indent=2, default=str)
            else:
                content_str = str(content)

            parts.append(f"### {tool_name}\n")
            parts.append(f"```json\n{content_str}\n```\n\n")

    # Synthesis instructions
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


async def _stream_answer_smoothly(
    writer: StreamWriter,
    config: RunnableConfig,
    text: str,
    citations: List,
    final_results: List = None,
    virtual_record_map: Dict = None,
    tool_records: List = None
) -> None:
    """
    Stream answer text word-by-word with progressive citation processing.

    Matches chatbot behavior:
    - Word by word streaming for smooth typing effect
    - Progressive citation normalization
    - Citations included in each chunk
    """
    words = re.findall(r'\S+', text)
    if not words:
        return

    accumulated = ""
    prev_normalized_len = 0
    target_words_per_chunk = 1
    delay = 0.015  # 15ms between chunks

    for i in range(0, len(words), target_words_per_chunk):
        chunk_words = words[i:i + target_words_per_chunk]
        chunk_text = ' '.join(chunk_words)

        # Build accumulated string incrementally
        if accumulated:
            accumulated = accumulated + ' ' + chunk_text
        else:
            accumulated = chunk_text

        # Normalize citations progressively if we have final_results
        if final_results:
            normalized, current_citations = normalize_citations_and_chunks_for_agent(
                accumulated, final_results, virtual_record_map or {}, records=tool_records or []
            )
            new_chunk = normalized[prev_normalized_len:]
            prev_normalized_len = len(normalized)
        else:
            normalized = accumulated
            current_citations = citations
            new_chunk = chunk_text

        safe_stream_write(writer, {
            "event": "answer_chunk",
            "data": {
                "chunk": new_chunk,
                "accumulated": normalized,
                "citations": current_citations
            }
        }, config)

        await asyncio.sleep(delay)


# =============================================================================
# ROUTING FUNCTIONS
# =============================================================================

def should_execute_tools(state: ChatState) -> Literal["execute", "respond"]:
    """Determine if we should execute tools or respond directly."""
    planned_tools = state.get("planned_tool_calls", [])
    execution_plan = state.get("execution_plan", {})

    # If planner requested clarification, go directly to respond
    if execution_plan.get("needs_clarification"):
        return "respond"

    # If no tools planned or can answer directly, go to respond
    if not planned_tools or execution_plan.get("can_answer_directly"):
        return "respond"

    return "execute"


def check_for_error(state: ChatState) -> Literal["error", "continue"]:
    """Check if an error occurred."""
    return "error" if state.get("error") else "continue"


# =============================================================================
# EXPORTS
# =============================================================================

__all__ = [
    # Nodes
    "planner_node",
    "execute_node",
    "respond_node",

    # Routing
    "should_execute_tools",
    "check_for_error",

    # Config
    "NodeConfig",

    # Utilities
    "clean_tool_result",
    "format_result_for_llm",
]

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
from typing import Any, Dict, List, Literal, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
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
MAX_AVAILABLE_TOOLS_DISPLAY = 20  # Maximum number of tools to display in error messages
MAX_TOOL_RESULT_PREVIEW_LENGTH = 500  # Maximum length for tool result preview

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

CONFLUENCE_GUIDANCE = r"""
## Confluence-Specific Guidance

### Tool Selection
- CREATE page → use `confluence.create_page` (NOT get_spaces or get_pages_in_space)
- SEARCH/FIND page → use `confluence.search_pages`
- GET/READ pages → use `confluence.get_pages_in_space` or `confluence.get_page_content`

### Space ID Resolution for create_page
1. **Check Reference Data first** - if `type: "confluence_space"` exists, use its `id` field
2. **If not in Reference Data**:
   - If space_id is a key (non-numeric): Call `confluence.get_spaces`, find matching space, extract numeric `id`
   - If space_id is numeric: Use directly
3. **CRITICAL**: API requires numeric space IDs. Always use `id` field, never `key` field.
"""

PLANNER_SYSTEM_PROMPT = """You are an intelligent task planner for an enterprise AI assistant.

## Decision Tree (Follow in order)
1. **Is it a greeting/simple question?** → `can_answer_directly: true`
2. **Does user mention a service name?** (Drive, Jira, Slack, Gmail, Calendar, Confluence, etc.) → Use THAT service's tools
3. **Is it a question without service mention?** (what/how/why/find info) → Use `retrieval.search_internal_knowledge`
4. **Is it an action?** (create/update/delete) → Use appropriate API tool

## Correct Examples (Learn from these)
- "Hello!" → ✓ `can_answer_directly: true` | ✗ calling tools
- "What is our vacation policy?" → ✓ `retrieval.search_internal_knowledge` | ✗ `drive.search_files` (no Drive mention)
- "Show me my Drive files" → ✓ `drive.get_files_list` | ✗ `retrieval.search_internal_knowledge` (Drive mentioned)
- "Find information about Q4 results" → ✓ `retrieval.search_internal_knowledge` | ✗ `drive.search_files` (no service mention)
- "Create a Jira ticket" → ✓ `jira.create_issue` | ✗ `retrieval.search_internal_knowledge` (action + Jira mentioned)
- "Help me prepare for tomorrow's meeting about AI" → ✓ `retrieval.search_internal_knowledge` + `calendar.get_events` (mixed query)

## Available Tools
{available_tools}

**Note**: Tool parameters are shown above for reference. Use the exact parameter names shown. Function calling schemas will validate the parameters automatically.

## Planning Rules
1. **Internal Knowledge Search**: ONLY for searching company knowledge bases, indexed documents, policies, or internal documentation → `retrieval.search_internal_knowledge`
   - ❌ DO NOT use for: accessing external APIs (Drive, Jira, Slack, etc.), getting files from cloud storage, managing tickets, sending messages
   - ✅ Use for: "find information about X", "search company docs", "what's our policy on Y"
2. **API/External Tools**: For accessing external services and managing data in those services
   - **Drive/Cloud Storage**: Getting files, folders, permissions → use `drive.*` tools
   - **Project Management**: Tickets, projects → use `jira.*` tools
   - **Communication**: Messages, users → use `slack.*` tools
   - **Other Services**: Use the appropriate service-specific tools
3. **Direct Answer**: Greetings, simple math, general knowledge, user info queries → `can_answer_directly: true`
4. **Query Understanding**: Extract key entities, dates, context, and service names (Drive, Jira, Slack, etc.)
5. **Conversation Context**: Use previous messages, reuse data (IDs, keys, etc.)

## Planning Rules for Retrieval
1. **Limit Retrieval Queries**: Maximum 2-3 retrieval calls per request

## Cascading Tool Calls (IMPORTANT)

When a task requires multiple steps where one tool's output is needed as input to another:

1. **Use placeholder syntax**: `{{{{TOOL_NAME.field}}}}` to reference previous tool results
2. **Plan tools in order**: First tool that generates data, then tools that use it
3. **Common patterns**:
   - Create then comment: `jira.create_issue` → `jira.add_comment` with `{{{{jira.create_issue.data.key}}}}`
   - Search then update: `jira.search_issues` → `jira.update_issue` with `{{{{jira.search_issues.key}}}}`
   - Get then delete: `drive.get_files_list` → `drive.delete_file` with `{{{{drive.get_files_list.id}}}}`

**Example cascading plan**:
```json
{{
  "tools": [
    {{"name": "jira.create_issue", "args": {{"project_key": "PA", "summary": "New task"}}}},
    {{"name": "jira.add_comment", "args": {{"issue_key": "{{{{jira.create_issue.data.key}}}}", "comment": "Starting work"}}}}
  ]
}}
```

**Placeholder format**: `{{{{tool_name.field}}}}` or `{{{{tool_name.data.field}}}}` where:
- `tool_name` is the tool that runs first (e.g., `jira.create_issue`)
- `field` is the field to extract (e.g., `key`, `id`, `data.key`)

**IMPORTANT - Array Results**:
- If a tool returns an array (e.g., `confluence.get_spaces` returns `data.results` array):
  - Use `{{{{tool_name.data.results.id}}}}` to get `id` from first item
  - Or use `{{{{tool_name.data.results[0].id}}}}` for explicit first item
  - The system will automatically handle arrays and extract from the first item

**Examples**:
- `{{{{jira.create_issue.data.key}}}}` → "PA-690" (direct field)
- `{{{{confluence.get_spaces.data.results.id}}}}` → "65708" (from first item in results array)
- `{{{{jira.search_issues.data.results[0].key}}}}` → "PA-691" (explicit array index)

## IMPORTANT - Context Awareness
- "try again", "retry" → look at previous conversation
- "that project", "the first one" → extract IDs/keys from Reference Data
- Reuse data from previous responses

## Reference Data Usage

**Always check Reference Data from previous responses before calling tools:**
- If needed ID/key exists in Reference Data → use it directly
- Reference Data items have: `name`, `id`, `key` (optional), `type`
- Use `id` for Confluence spaces, `key` for Jira projects/issues
- **DO NOT** call tools to fetch data already in Reference Data

{jira_guidance}
{confluence_guidance}

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

## Additional Examples
- "Q4 results" (no service mentioned) → `retrieval.search_internal_knowledge` with {{"query": "Q4 results"}}
- "Get my Drive files" → `drive.get_files_list` with {{}}
- "Search for files in Drive" → `drive.search_files` with {{"query": "..."}}
- "My Jira projects" → `jira.get_projects` with {{}}
- "My tickets in PA" → `jira.search_issues` with {{"jql": "project = \\"PA\\" AND assignee = currentUser() AND resolution IS EMPTY AND updated >= -30d"}}
- "Create a Confluence page" → Check Reference Data for space_id, or call get_spaces first, then create_page with numeric ID
- "Find a page" → `confluence.search_pages` with {{"title": "..."}}
- "my name" (user info in prompt) → `can_answer_directly: true`, tools: []

**IMPORTANT**: When user mentions a specific service (Drive, Jira, Slack, etc.), use that service's tools, NOT `retrieval.search_internal_knowledge`."""

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

## Iteration Status
Iteration: {iteration_count}/{max_iterations}

## Decision Options
1. **respond_success** - Tools worked AND task is complete
2. **respond_error** - Unrecoverable error
3. **respond_clarify** - Need user clarification
4. **retry_with_fix** - Fixable error, retry with fix
5. **continue_with_more_tools** - Tools worked but task needs more steps (e.g., got data but need to update/create)

## Task Completion Check
- If user asked to "edit/update/modify" but you only "got/read" data → continue_with_more_tools
- If user asked to "create/make" but you only "searched/found" → continue_with_more_tools
- If user asked to "get/list" and you got the data → respond_success
- If all required actions are done → respond_success

## Common Fixes
- "Unbounded JQL" → Add `updated >= -30d`
- "User not found" → Search users first
- "Invalid syntax" → Fix query format

## Output (JSON only)
{{
  "decision": "respond_success|respond_error|respond_clarify|retry_with_fix|continue_with_more_tools",
  "reasoning": "Brief explanation",
  "fix_instruction": "For retry: what to change",
  "clarifying_question": "For clarify: what to ask",
  "error_context": "For error: user-friendly explanation",
  "task_complete": true/false,
  "needs_more_tools": "What tools are needed next (if continue_with_more_tools)"
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


def _has_confluence_tools(state: ChatState) -> bool:
    """Check if Confluence tools available"""
    agent_toolsets = state.get("agent_toolsets", [])
    for toolset in agent_toolsets:
        if isinstance(toolset, dict) and "confluence" in toolset.get("name", "").lower():
            return True
    return False


def _check_primary_tool_success(query: str, successful: List[Dict], log: logging.Logger) -> bool:
    """
    Check if the primary/critical tool succeeded based on user intent.

    Examples:
    - "Create a Jira ticket and add comment" -> primary is create_issue
    - "Search and update" -> primary is search
    - "Get and delete" -> primary is get

    Returns True if the primary tool for the user's request succeeded.
    """
    query_lower = query.lower()
    successful_tools = [r.get("tool_name", "").lower() for r in successful]

    # Map intent keywords to primary tool patterns
    intent_to_primary = {
        "create": ["create", "make", "add", "new"],
        "update": ["update", "modify", "change", "edit"],
        "delete": ["delete", "remove", "clear"],
        "search": ["search", "find", "get", "list"],
    }

    # Detect primary intent from query
    primary_intent = None
    for intent, keywords in intent_to_primary.items():
        if any(keyword in query_lower for keyword in keywords):
            primary_intent = intent
            break

    if not primary_intent:
        # If can't determine intent, assume first successful tool is primary
        return len(successful) > 0

    # Check if any successful tool matches primary intent
    for tool in successful_tools:
        if primary_intent in tool:
            log.debug(f"Primary tool for '{primary_intent}' intent succeeded: {tool}")
            return True

    # Fallback: if any tool succeeded, consider it partial success
    return len(successful) > 0


def _check_if_task_needs_continue(
    query: str,
    executed_tools: List[str],
    tool_results: List[Dict[str, Any]],
    log: logging.Logger
) -> bool:
    """
    Check if task needs more steps by comparing user intent vs what was done.

    Returns True if task is incomplete and needs more tools.
    """
    query_lower = query.lower()
    executed_tools_lower = [t.lower() for t in executed_tools]

    # Pattern matching for common incomplete scenarios
    # User wants to edit/update but only got/read data
    if any(word in query_lower for word in ["edit", "update", "modify", "change", "alter"]):
        if any("get" in t or "read" in t or "fetch" in t or "search" in t or "find" in t
               for t in executed_tools_lower):
            if not any("update" in t or "edit" in t or "modify" in t or "change" in t or "send" in t
                      for t in executed_tools_lower):
                log.debug("Task incomplete: user wants to edit/update but only read data")
                return True

    # User wants to create/make but only searched/found
    if any(word in query_lower for word in ["create", "make", "new", "add", "post", "send"]):
        if any("search" in t or "find" in t or "get" in t or "fetch" in t
               for t in executed_tools_lower):
            if not any("create" in t or "make" in t or "add" in t or "post" in t or "send" in t
                      for t in executed_tools_lower):
                log.debug("Task incomplete: user wants to create but only searched")
                return True

    # User wants to delete/remove but only got data
    if any(word in query_lower for word in ["delete", "remove", "clear"]):
        if any("get" in t or "read" in t or "fetch" in t
               for t in executed_tools_lower):
            if not any("delete" in t or "remove" in t or "clear" in t
                      for t in executed_tools_lower):
                log.debug("Task incomplete: user wants to delete but only read data")
                return True

    # If query has multiple actions (e.g., "get and update"), check if all are done
    action_words = ["get", "fetch", "find", "search", "read", "list",
                   "create", "make", "add", "post", "send",
                   "update", "edit", "modify", "change",
                   "delete", "remove", "clear"]

    query_actions = [word for word in action_words if word in query_lower]
    executed_actions = []
    for tool in executed_tools_lower:
        for action in action_words:
            if action in tool:
                executed_actions.append(action)
                break

    # If query has multiple distinct actions and not all are executed
    if len(query_actions) > 1:
        missing_actions = set(query_actions) - set(executed_actions)
        if missing_actions:
            log.debug(f"Task incomplete: missing actions {missing_actions}")
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
# Phase 4: Query Decomposition
# ============================================================================

async def _decompose_query_if_needed(query: str, llm, log: logging.Logger) -> Optional[List[Dict[str, str]]]:
    """
    Detect vague queries that might need multiple tools and decompose them.

    Returns:
        List of sub-tasks with their queries, or None if decomposition not needed
    """
    query_lower = query.lower()

    # Indicators of vague/multi-intent queries:
    # 1. Multiple "and" conjunctions suggesting multiple intents
    # 2. No specific service mentioned but multiple action words
    # 3. Questions combined with action requests

    # Simple heuristics first (fast path)
    and_count = query_lower.count(' and ')

    # Check for multiple distinct intents
    question_words = ['what', 'how', 'why', 'when', 'where', 'find', 'search', 'get', 'show']
    action_words = ['create', 'make', 'add', 'update', 'delete', 'send', 'post']
    service_mentions = ['drive', 'jira', 'slack', 'gmail', 'calendar', 'confluence']

    has_question = any(word in query_lower for word in question_words)
    has_action = any(word in query_lower for word in action_words)
    has_service = any(service in query_lower for service in service_mentions)

    # Decompose if:
    # - Multiple "and" conjunctions (likely multiple intents)
    # - Both question and action intents
    # - No service mentioned but multiple action/question words
    needs_decomposition = (
        (and_count >= 2) or
        (and_count >= 1 and (has_question and has_action)) or
        (not has_service and (query_lower.count(' ') >= 8 and (has_question or has_action)))
    )

    if not needs_decomposition:
        return None

    # Use LLM to decompose the query
    try:
        decomposition_prompt = f"""Analyze this query and break it into distinct sub-tasks if it requires multiple tools.

Query: "{query}"

RULES:
1. Maximum 3 sub-tasks total
2. For retrieval/search tasks
3. Avoid overly specific queries with many details

If this query can be answered with a single tool, return: {{"needs_decomposition": false}}

If this query needs multiple tools (e.g., "What are Q4 results and my Drive files?"), return:
{{
  "needs_decomposition": true,
  "subtasks": [
    {{"query": "Q4 results", "intent": "knowledge_search"}},
    {{"query": "my Drive files", "intent": "file_list"}}
  ]
}}

Return only valid JSON."""

        response = await asyncio.wait_for(
            llm.ainvoke([SystemMessage(content="You are a query analysis expert. Return only valid JSON."),
                        HumanMessage(content=decomposition_prompt)]),
            timeout=5.0
        )

        result = json.loads(response.content if hasattr(response, 'content') else str(response))

        if result.get("needs_decomposition") and result.get("subtasks"):
            return result["subtasks"]
        return None

    except Exception as e:
        log.warning(f"Query decomposition failed: {e}")
        return None


async def _plan_with_decomposition(
    state: ChatState,
    config: RunnableConfig,
    writer: StreamWriter,
    subtasks: List[Dict[str, str]],
    start_time: float,
    log: logging.Logger
) -> ChatState:
    """
    Plan tools for decomposed query sub-tasks and combine into single plan.
    """
    llm = state.get("llm")
    all_tools = []
    combined_intent = []
    combined_reasoning = []

    # Plan each sub-task
    for i, subtask in enumerate(subtasks):
        sub_query = subtask.get("query", "")

        log.info(f"Planning sub-task {i+1}/{len(subtasks)}: {sub_query[:50]}")

        # Build planner prompt for this sub-task
        tool_descriptions = _get_cached_tool_descriptions(state, log)
        jira_guidance = JIRA_GUIDANCE if _has_jira_tools(state) else ""
        confluence_guidance = CONFLUENCE_GUIDANCE if _has_confluence_tools(state) else ""

        system_prompt = PLANNER_SYSTEM_PROMPT.format(
            available_tools=tool_descriptions,
            jira_guidance=jira_guidance,
            confluence_guidance=confluence_guidance
        )

        user_prompt = f"""Sub-task {i+1} of {len(subtasks)}: {sub_query}

Plan tools for this sub-task only. Return JSON with tools needed for this specific sub-task."""

        try:
            response = await asyncio.wait_for(
                llm.ainvoke([SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]),
                timeout=15.0
            )

            plan = _parse_planner_response(
                response.content if hasattr(response, 'content') else str(response),
                log
            )

            # Collect tools from this sub-task
            sub_tools = plan.get("tools", [])
            all_tools.extend(sub_tools)
            combined_intent.append(f"Sub-task {i+1}: {plan.get('intent', sub_query[:30])}")
            combined_reasoning.append(f"Sub-task {i+1}: {plan.get('reasoning', '')}")

        except Exception as e:
            log.warning(f"Failed to plan sub-task {i+1}: {e}")
            continue

    # Combine into single plan
    combined_plan = {
        "intent": " | ".join(combined_intent),
        "reasoning": "Decomposed query into multiple sub-tasks. " + " | ".join(combined_reasoning),
        "can_answer_directly": False,
        "needs_clarification": False,
        "tools": all_tools
    }

    # Validate tools
    tools = combined_plan.get('tools', [])
    is_valid, invalid_tools, available_tool_names = _validate_planned_tools(tools, state, log)

    if not is_valid and invalid_tools:
        log.warning(f"Invalid tools in decomposed plan: {invalid_tools}")
        # Remove invalid tools
        combined_plan["tools"] = [t for t in tools if t.get('name', '') not in invalid_tools]

    # Store plan
    state["execution_plan"] = combined_plan
    state["planned_tool_calls"] = combined_plan.get("tools", [])
    state["pending_tool_calls"] = bool(combined_plan.get("tools"))
    state["query_analysis"] = {
        "intent": combined_plan.get("intent", ""),
        "reasoning": combined_plan.get("reasoning", ""),
        "can_answer_directly": combined_plan.get("can_answer_directly", False),
    }

    duration_ms = (time.perf_counter() - start_time) * 1000
    log.info(f"⚡ Planner (decomposed): {duration_ms:.0f}ms, {len(combined_plan.get('tools', []))} tools")

    return state


# ============================================================================
# Node 1: Planner
# ============================================================================

async def planner_node(
    state: ChatState,
    config: RunnableConfig,
    writer: StreamWriter
) -> ChatState:
    """LLM-driven planner with query decomposition for vague queries"""
    start_time = time.perf_counter()
    log = state.get("logger", logger)
    llm = state.get("llm")
    query = state.get("query", "")
    previous_conversations = state.get("previous_conversations", [])

    # Phase 4: Query Decomposition - Check if query needs decomposition
    decomposed_subtasks = await _decompose_query_if_needed(query, llm, log)
    if decomposed_subtasks:
        log.info(f"Query decomposed into {len(decomposed_subtasks)} sub-tasks")
        # Plan tools for each sub-task and combine
        return await _plan_with_decomposition(state, config, writer, decomposed_subtasks, start_time, log)

    safe_stream_write(writer, {
        "event": "status",
        "data": {"status": "planning", "message": "Planning..."}
    }, config)

    # Build prompts
    tool_descriptions = _get_cached_tool_descriptions(state, log)
    jira_guidance = JIRA_GUIDANCE if _has_jira_tools(state) else ""
    confluence_guidance = CONFLUENCE_GUIDANCE if _has_confluence_tools(state) else ""

    system_prompt = PLANNER_SYSTEM_PROMPT.format(
        available_tools=tool_descriptions,
        jira_guidance=jira_guidance,
        confluence_guidance=confluence_guidance
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

    # Add continue context if needed (tool results from previous iteration)
    if state.get("is_continue") and state.get("all_tool_results"):
        continue_context = _build_continue_context(state, log)
        if continue_context:
            user_prompt = continue_context + "\n\n" + user_prompt
        state["is_continue"] = False
        log.info("Continue mode active - using previous tool results")

    # Tool validation retry loop
    validation_retry_count = state.get("tool_validation_retry_count", 0)
    max_validation_retries = 2
    plan = None

    try:
        invoke_config = {"callbacks": [_opik_tracer]} if _opik_tracer else {}

        while validation_retry_count <= max_validation_retries:
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

            # Validate tool names against available tools
            tools = plan.get('tools', [])
            is_valid, invalid_tools, available_tool_names = _validate_planned_tools(tools, state, log)

            if is_valid or validation_retry_count >= max_validation_retries:
                # Valid or max retries reached
                if not is_valid:
                    log.error(f"⚠️ Invalid tools after {max_validation_retries} retries: {invalid_tools}. Removing invalid tools.")
                    # Remove invalid tools from plan
                    plan["tools"] = [t for t in tools if isinstance(t, dict) and t.get('name', '') not in invalid_tools]
                # Reset validation retry count on success or final attempt
                state["tool_validation_retry_count"] = 0
                break
            else:
                # Invalid tools found, retry with error message
                validation_retry_count += 1
                state["tool_validation_retry_count"] = validation_retry_count
                log.warning(f"⚠️ Invalid tools planned: {invalid_tools}. Retrying with available tools list (attempt {validation_retry_count}/{max_validation_retries})")

                # Build error message with available tools
                available_tools_list = ", ".join(sorted(available_tool_names)[:MAX_AVAILABLE_TOOLS_DISPLAY])  # Limit to first 20 for prompt size
                if len(available_tool_names) > MAX_AVAILABLE_TOOLS_DISPLAY:
                    available_tools_list += f" (and {len(available_tool_names) - MAX_AVAILABLE_TOOLS_DISPLAY} more)"

                error_message = f"""ERROR: The following tools do not exist: {', '.join(invalid_tools)}

Available tools: {available_tools_list}

Please choose tools ONLY from the available tools list above. Do not invent tool names.

Original query: {query}
"""
                user_prompt = error_message + "\n\n" + user_prompt

        # Validate plan matches intent
        intent = plan.get('intent', '').lower()
        tool_names = [t.get('name', '') for t in tools if isinstance(t, dict)]

        # Log validation warnings
        if 'create' in intent or 'make' in intent or 'new' in intent:
            if not any('create' in name.lower() for name in tool_names):
                log.warning(f"⚠️ Intent suggests CREATE but no create tool planned. Intent: {intent[:50]}, Tools: {tool_names}")
        elif 'search' in intent or 'find' in intent or 'get' in intent:
            if any('create' in name.lower() for name in tool_names):
                log.warning(f"⚠️ Intent suggests READ/SEARCH but create tool planned. Intent: {intent[:50]}, Tools: {tool_names}")

        log.info(f"Plan: {plan.get('intent', 'N/A')[:50]}, {len(tools)} tools: {[t.get('name', '') for t in tools[:3]]}")

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


def _build_continue_context(state: ChatState, log: logging.Logger) -> str:
    """Build continue context from previous tool results"""
    tool_results = state.get("all_tool_results", [])
    if not tool_results:
        return ""

    # Format tool results for planner context
    result_parts = []
    for result in tool_results[-5:]:  # Last 5 results
        tool_name = result.get("tool_name", "unknown")
        status = result.get("status", "unknown")
        result_data = result.get("result", "")

        # Extract key information (IDs, timestamps, etc.)
        result_str = str(result_data)[:500]  # Limit length

        result_parts.append(f"- {tool_name} ({status}): {result_str}")

    return f"""## Previous Tool Results (use this data for next steps)

{chr(10).join(result_parts)}

**IMPORTANT**: Use the data above to plan your next steps. Extract IDs, timestamps, and other key information from the results.
"""


def _build_retry_context(state: ChatState) -> str:
    """Build retry context from previous failure"""
    errors = state.get("execution_errors", [])
    reflection = state.get("reflection", {})
    fix_instruction = reflection.get("fix_instruction", "")

    error_summary = errors[0] if errors else {}
    failed_tool = error_summary.get('tool_name', 'unknown')
    failed_args = error_summary.get("args", {})
    error_msg = error_summary.get('error', 'unknown')[:500]
    failed_args_str = json.dumps(failed_args, indent=2) if failed_args else "No args"

    # Extract key information from error
    error_lower = error_msg.lower()
    needs_space_resolution = (
        "spaceid" in error_lower or "space_id" in error_lower
    ) and ("not the correct type" in error_lower or "expected type" in error_lower)

    # Build specific guidance
    specific_guidance = ""
    if needs_space_resolution and "create_page" in failed_tool.lower():
        specific_guidance = """
**Space ID Resolution**:
1. Check Reference Data for `type: "confluence_space"` - if found, use its `id` field
2. If not in Reference Data: Call `confluence.get_spaces`, find matching space, extract numeric `id`
3. Call `confluence.create_page` with numeric `id` (not key) in `space_id` parameter
4. DO NOT switch to get_spaces as final tool - you still need to CREATE the page
"""

    return f"""## 🔴 RETRY MODE - PREVIOUS ATTEMPT FAILED

**Failed Tool**: {failed_tool}
**Error**: {error_msg}

**Previous Args That Failed**:
```json
{failed_args_str}
```

**FIX INSTRUCTION**:
{fix_instruction}
{specific_guidance}

**IMPORTANT**:
- If the user asked to CREATE something, you MUST still use the CREATE tool after fixing the issue
- Don't switch to a different tool type (READ/SEARCH) when the user wants to CREATE
- Fix the parameters and retry the SAME tool with corrected values

"""


_tool_description_cache: Dict[str, str] = {}


def _get_cached_tool_descriptions(state: ChatState, log: logging.Logger) -> str:
    """Get tool descriptions with caching"""
    org_id = state.get("org_id", "default")
    agent_toolsets = state.get("agent_toolsets", [])

    # Get LLM to determine sanitization requirements for cache key
    llm = state.get("llm")
    from app.modules.agents.qna.tool_system import (
        _requires_sanitized_tool_names,
        get_agent_tools_with_schemas,
    )
    llm_type = "anthropic" if llm and _requires_sanitized_tool_names(llm) else "other"

    # Build cache key from toolsets and LLM type (for sanitization)
    toolset_names = sorted([ts.get("name", "") for ts in agent_toolsets if isinstance(ts, dict)])
    cache_key = f"{org_id}_{hash(tuple(toolset_names))}_{llm_type}_clean"

    if cache_key in _tool_description_cache:
        return _tool_description_cache[cache_key]

    try:
        tools = get_agent_tools_with_schemas(state)

        if not tools:
            # Use sanitized name in fallback if needed
            fallback_name = "retrieval_search_internal_knowledge" if (llm and _requires_sanitized_tool_names(llm)) else "retrieval.search_internal_knowledge"
            return f"### {fallback_name}\n  ✅ Use: Questions without service mention; policies; general info\n  ❌ Don't: Service mentioned (Drive/Jira/Gmail); action intent"

        # Use clean formatting function (NO parameters - they're in schemas)
        result = _format_clean_tool_descriptions(tools, log)
        _tool_description_cache[cache_key] = result
        return result

    except Exception as e:
        log.warning(f"Tool load failed: {e}")
        # Use sanitized name in fallback if needed (llm already retrieved above)
        fallback_name = "retrieval_search_internal_knowledge" if (llm and _requires_sanitized_tool_names(llm)) else "retrieval.search_internal_knowledge"
        return f"### {fallback_name}\n  ✅ Use: Questions without service mention; policies; general info\n  ❌ Don't: Service mentioned (Drive/Jira/Gmail); action intent"


def _format_clean_tool_descriptions(tools: List, log: logging.Logger) -> str:
    """
    Format tools for planner prompt - includes parameters for better LLM guidance.

    Format:
    ### [CATEGORY] tool.name
      ✅ Use: scenario1; scenario2
      ❌ Don't: anti-pattern1; anti-pattern2
      📋 Parameters: param1 (type), param2 (type, optional)
    """
    import re

    from app.agents.tools.config import ToolCategory

    # Group tools by category for better organization
    tools_by_category: Dict[str, List] = {}

    for tool in tools[:30]:  # Limit to prevent prompt bloat
        name = getattr(tool, 'name', str(tool))

        # Get category from metadata via global registry
        category = ToolCategory.UTILITY  # default
        try:
            from app.agents.tools.registry import _global_tools_registry
            metadata = _global_tools_registry.get_metadata(name)
            if metadata:
                category = metadata.category
        except Exception:
            pass

        category_name = category.value if hasattr(category, 'value') else str(category)

        # Extract when_to_use and when_not_to_use
        when_use = []
        when_not = []

        # Try to get from Tool object
        when_use = getattr(tool, 'when_to_use', [])
        when_not = getattr(tool, 'when_not_to_use', [])

        # If not found, try parsing from llm_description
        if not when_use and not when_not:
            llm_desc = getattr(tool, 'llm_description', None)
            if llm_desc and isinstance(llm_desc, str):
                # Parse structured format
                when_match = re.search(r'\*\*WHEN TO USE\*\*:?\s*\n(.*?)(?:\*\*WHEN NOT|\Z)', llm_desc, re.DOTALL)
                if when_match:
                    when_text = when_match.group(1).strip()
                    when_use = [line.strip('- ').strip() for line in when_text.split('\n') if line.strip().startswith('-')]

                not_match = re.search(r'\*\*WHEN NOT TO USE\*\*:?\s*\n(.*?)(?:\*\*|\Z)', llm_desc, re.DOTALL)
                if not_match:
                    not_text = not_match.group(1).strip()
                    when_not = [line.strip('- ').strip() for line in not_text.split('\n') if line.strip().startswith('-')]

        # Extract parameter information - check Pydantic schema first, then legacy parameters
        param_info = []
        # Check for Pydantic schema (preferred - used by modern tools)
        args_schema = getattr(tool, 'args_schema', None)
        if args_schema:
            # Check if it's a Pydantic BaseModel class (v2)
            try:
                from typing import Union, get_args, get_origin

                from pydantic import BaseModel

                # Handle both class and instance
                schema_class = args_schema if isinstance(args_schema, type) else args_schema.__class__

                # Check if it's a Pydantic v2 model
                if issubclass(schema_class, BaseModel) and hasattr(schema_class, 'model_fields'):
                    for field_name, field_info in schema_class.model_fields.items():
                        # Determine type - handle Optional, Union, etc.
                        field_type = field_info.annotation
                        param_type = "string"  # default

                        # Handle Optional types (Union[T, None])
                        origin = get_origin(field_type) if hasattr(field_type, '__origin__') or hasattr(field_type, '__args__') else None
                        if origin is Union:
                            args = get_args(field_type)
                            # Filter out None type
                            non_none_args = [arg for arg in args if arg is not type(None)]
                            if non_none_args:
                                field_type = non_none_args[0]
                                origin = get_origin(field_type) if hasattr(field_type, '__origin__') else None

                        # Determine base type
                        if field_type is int:
                            param_type = "integer"
                        elif field_type is float:
                            param_type = "number"
                        elif field_type is bool:
                            param_type = "boolean"
                        elif origin is list:
                            param_type = "array"
                        elif origin is dict:
                            param_type = "object"

                        # Check if required (not Optional and no default)
                        param_required = field_info.is_required() and field_info.default is ...
                        req_str = "required" if param_required else "optional"
                        param_info.append(f"{field_name} ({param_type}, {req_str})")
            except Exception as e:
                log.debug(f"Could not extract params from Pydantic schema for {name}: {e}")

        # Fallback to legacy ToolParameter list if no schema found
        if not param_info:
            registry_tool = getattr(tool, 'registry_tool', None)
            if registry_tool and hasattr(registry_tool, 'parameters') and registry_tool.parameters:
                for param in registry_tool.parameters:
                    param_name = getattr(param, 'name', 'unknown')
                    param_type = getattr(getattr(param, 'type', None), 'name', 'string')
                    param_required = getattr(param, 'required', False)
                    req_str = "required" if param_required else "optional"
                    param_info.append(f"{param_name} ({param_type}, {req_str})")

        # Group by category
        if category_name not in tools_by_category:
            tools_by_category[category_name] = []

        # Format entry with parameters
        tool_lines = [f"\n### [{category_name.upper()}] {name}"]

        if when_use:
            # Join first 3 use cases with semicolons
            use_summary = '; '.join(when_use[:3])
            tool_lines.append(f"  ✅ Use: {use_summary}")

        if when_not:
            # Join first 2 anti-patterns with semicolons
            not_summary = '; '.join(when_not[:2])
            tool_lines.append(f"  ❌ Don't: {not_summary}")

        # Add parameter information (limit to first 5 to keep prompt concise)
        if param_info:
            params_str = ', '.join(param_info)
            tool_lines.append(f"  📋 Parameters: {params_str}")
        else :
            tool_lines.append("  📋 Parameters: none")
        tools_by_category[category_name].append('\n'.join(tool_lines))

    # Format by category
    result_lines = []
    # Order categories: SEARCH first (retrieval), then others
    category_order = ['search', 'communication', 'project_management', 'file_storage', 'calendar', 'documentation', 'utility']
    for cat in category_order:
        if cat in tools_by_category:
            result_lines.extend(tools_by_category[cat])
            del tools_by_category[cat]

    # Add remaining categories
    for cat, tool_list in sorted(tools_by_category.items()):
        result_lines.extend(tool_list)

    return "\n".join(result_lines) if result_lines else ""


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
        result += "\n\n## Reference Data (from previous responses - use these IDs/keys directly):\n"

        # Group by type
        spaces = [item for item in all_reference_data if item.get("type") == "confluence_space"]
        projects = [item for item in all_reference_data if item.get("type") == "jira_project"]
        issues = [item for item in all_reference_data if item.get("type") == "jira_issue"]
        others = [item for item in all_reference_data if item.get("type") not in ["confluence_space", "jira_project", "jira_issue"]]

        if spaces:
            result += "Confluence Spaces (use `id` for space_id): "
            result += ", ".join([f"{item.get('name', '?')} (id={item.get('id', '?')})" for item in spaces[:5]])
            result += "\n"

        if projects:
            result += "Jira Projects (use `key`): "
            result += ", ".join([f"{item.get('name', '?')} (key={item.get('key', '?')})" for item in projects[:5]])
            result += "\n"

        if issues:
            result += "Jira Issues (use `key`): "
            result += ", ".join([f"{item.get('key', '?')}" for item in issues[:5]])
            result += "\n"

        if others:
            for item in others[:5]:
                name = item.get("name", "Unknown")
                item_id = item.get("id", "")
                item_key = item.get("key", "")
                item_type = item.get("type", "")
                parts = [f"{name} ({item_type})"]
                if item_key:
                    parts.append(f"key={item_key}")
                if item_id:
                    parts.append(f"id={item_id}")
                result += f"- {' | '.join(parts)}\n"

        log.debug(f"Included {len(all_reference_data)} reference items")

    return result


def _validate_planned_tools(
    planned_tools: List[Dict[str, Any]],
    state: ChatState,
    log: logging.Logger
) -> tuple[bool, List[str], List[str]]:
    """
    Validate planned tool names against available tools.

    Handles both sanitized (underscores) and original (dots) tool names for compatibility.

    Returns:
        (is_valid, invalid_tools, available_tool_names)
    """
    try:
        from app.modules.agents.qna.tool_system import (
            _sanitize_tool_name_if_needed,
            get_agent_tools_with_schemas,
        )
        tools = get_agent_tools_with_schemas(state)
        llm = state.get("llm")

        # Get available tool names (already sanitized if needed by get_agent_tools_with_schemas)
        available_tool_names = {getattr(tool, 'name', str(tool)) for tool in tools}

        # Also create a mapping from original to sanitized names for lookup
        original_to_sanitized = {}
        for tool in tools:
            sanitized_name = getattr(tool, 'name', str(tool))
            original_name = getattr(tool, '_original_name', sanitized_name)
            if original_name != sanitized_name:
                original_to_sanitized[original_name] = sanitized_name

        invalid_tools = []
        for tool_call in planned_tools:
            if isinstance(tool_call, dict):
                tool_name = tool_call.get('name', '')
                # Normalize planned tool name to match available tools
                normalized_name = _sanitize_tool_name_if_needed(tool_name, llm) if llm else tool_name

                # Check both sanitized and original name
                if normalized_name not in available_tool_names and tool_name not in available_tool_names:
                    invalid_tools.append(tool_name)

        is_valid = len(invalid_tools) == 0
        return is_valid, invalid_tools, list(available_tool_names)
    except Exception as e:
        log.warning(f"Tool validation failed: {e}")
        # If validation fails, assume valid to avoid blocking execution
        return True, [], []


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

            # Validate and clean retrieval queries
            retrieval_tools = [t for t in normalized_tools if "retrieval" in t.get("name", "").lower()]

            if len(retrieval_tools) > 3:
                log.warning(f"Too many retrieval queries ({len(retrieval_tools)}), limiting to 3")
                # Keep only first 3
                other_tools = [t for t in normalized_tools if "retrieval" not in t.get("name", "").lower()]
                normalized_tools = retrieval_tools[:3] + other_tools

            # Trim overly long queries
            for tool in normalized_tools:
                if "retrieval" in tool.get("name", "").lower():
                    query = tool.get("args", {}).get("query", "")
                    if len(query) > 100:
                        # Extract first few meaningful words
                        words = query.split()[:8]
                        trimmed = " ".join(words)
                        log.warning(f"Trimmed long query: '{query[:50]}...' -> '{trimmed}'")
                        tool["args"]["query"] = trimmed

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
    """Execute all planned tools in parallel OR sequentially if cascading"""
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
        from app.modules.agents.qna.tool_system import (
            get_agent_tools_with_schemas,
        )
        tools = get_agent_tools_with_schemas(state)
        llm = state.get("llm")

        # Build mapping - tools already have sanitized names if needed
        tools_by_name = {}
        for t in tools:
            sanitized_name = getattr(t, 'name', str(t))
            tools_by_name[sanitized_name] = t
            # Also map original name if different (for backward compatibility)
            original_name = getattr(t, '_original_name', sanitized_name)
            if original_name != sanitized_name:
                tools_by_name[original_name] = t
    except Exception as e:
        log.error(f"Failed to get tools: {e}")
        tools_by_name = {}

    # NEW: Detect cascading tools (tools with placeholders like {{...}})
    has_cascading = _detect_cascading_tools(planned_tools, log)

    if has_cascading:
        # Execute sequentially to handle dependencies
        log.info("Detected cascading tools - executing sequentially")
        tool_results = await _execute_tools_sequentially(
            planned_tools, tools_by_name, llm, state, log, writer, config
        )
    else:
        # Execute in parallel (original behavior)
        log.info("No cascading detected - executing in parallel")
        tool_results = await _execute_tools_in_parallel(
            planned_tools, tools_by_name, llm, state, log
        )

    # Update state
    tool_messages = []
    success_count = sum(1 for r in tool_results if r.get("status") == "success")
    failed_count = sum(1 for r in tool_results if r.get("status") == "error")

    # Build tool messages
    for result in tool_results:
        if result.get("tool_id"):
            content_str = format_result_for_llm(result.get("result", ""), result.get("tool_name", ""))
            tool_messages.append(ToolMessage(
                content=content_str,
                tool_call_id=result.get("tool_id", "")
            ))

    state["tool_results"] = tool_results
    state["all_tool_results"] = tool_results

    if not state.get("messages"):
        state["messages"] = []
    state["messages"].extend(tool_messages)

    state["pending_tool_calls"] = False

    duration_ms = (time.perf_counter() - start_time) * 1000
    executed_tool_names = [r.get("tool_name", "") for r in tool_results]
    log.info(f"✅ Executed {len(tool_results)} tools in {duration_ms:.0f}ms ({success_count} succeeded, {failed_count} failed)")

    # Validate executed tools match planned tools
    planned_tool_names = [t.get("name", "") for t in planned_tools]
    if planned_tool_names != executed_tool_names:
        log.warning(f"⚠️ Tool mismatch! Planned: {planned_tool_names}, Executed: {executed_tool_names}")

    return state


def _detect_cascading_tools(planned_tools: List[Dict[str, Any]], log: logging.Logger) -> bool:
    """
    Detect if tools have dependencies (placeholders like {{...}} in args).

    Returns True if any tool has placeholder references to previous tool results.
    """
    placeholder_pattern = re.compile(r'\{\{[^}]+\}\}')

    for tool in planned_tools:
        args = tool.get("args", {})
        # Check all arg values for placeholders
        args_str = json.dumps(args, default=str)
        if placeholder_pattern.search(args_str):
            log.info(f"Found cascading tool: {tool.get('name')} has placeholder args")
            return True

    return False


async def _execute_tools_sequentially(
    planned_tools: List[Dict[str, Any]],
    tools_by_name: Dict[str, Any],
    llm: Any,
    state: ChatState,
    log: logging.Logger,
    writer: StreamWriter,
    config: RunnableConfig
) -> List[Dict[str, Any]]:
    """
    Execute tools sequentially, resolving placeholders from previous results.

    This enables cascading tool calls where one tool's output is used as input to the next.
    """
    from app.modules.agents.qna.tool_system import _sanitize_tool_name_if_needed

    tool_results = []
    # Store results by tool name for placeholder resolution
    results_by_tool = {}

    for i, tool_call in enumerate(planned_tools):
        tool_name = tool_call.get("name", "")
        normalized_name = _sanitize_tool_name_if_needed(tool_name, llm) if llm else tool_name
        actual_tool_name = normalized_name if normalized_name in tools_by_name else tool_name

        if actual_tool_name not in tools_by_name:
            log.warning(f"Tool not found: {tool_name}")
            error_result = {
                "tool_name": tool_name,
                "result": f"Error: Tool '{tool_name}' not found",
                "status": "error",
                "tool_id": f"call_{i}_{tool_name}"
            }
            tool_results.append(error_result)
            continue

        # Resolve placeholders in args using previous results
        tool_args = tool_call.get("args", {})
        resolved_args = _resolve_placeholders(tool_args, results_by_tool, log)

        # Validate that all placeholders were resolved
        unresolved_placeholders = []
        for key, value in resolved_args.items():
            if isinstance(value, str) and '{{' in value:
                unresolved_placeholders.append(f"{key}={value}")

        if unresolved_placeholders:
            log.error(f"Unresolved placeholders in {actual_tool_name}: {unresolved_placeholders}")
            # Don't execute tool with unresolved placeholders - it will fail
            error_result = {
                "tool_name": actual_tool_name,
                "result": f"Error: Could not resolve placeholders: {', '.join(unresolved_placeholders)}. Available tools: {list(results_by_tool.keys())}",
                "status": "error",
                "tool_id": f"call_{i}_{actual_tool_name}"
            }
            tool_results.append(error_result)
            log.warning(f"Skipping {actual_tool_name} due to unresolved placeholders")
            continue

        tool_id = f"call_{i}_{actual_tool_name}"

        log.debug(f"Executing {actual_tool_name} sequentially with {json.dumps(resolved_args, default=str)[:100]}...")

        # Stream status update
        safe_stream_write(writer, {
            "event": "status",
            "data": {"status": "executing", "message": f"Executing {actual_tool_name}..."}
        }, config)

        # Execute single tool
        result = await _execute_single_tool(
            tool=tools_by_name[actual_tool_name],
            tool_name=actual_tool_name,
            tool_args=resolved_args,
            tool_id=tool_id,
            state=state,
            log=log
        )

        if isinstance(result, dict) and "tool_result" in result:
            tool_result = result["tool_result"]
            tool_results.append(tool_result)

            # CRITICAL: Only store successful results for placeholder resolution
            # Failed tools should not be available for placeholder resolution
            if tool_result.get("status") != "success":
                log.debug(f"Skipping failed tool {actual_tool_name} from placeholder resolution (status: {tool_result.get('status')})")
                log.info(f"Sequential execution: {actual_tool_name} failed")
                continue  # Skip storing this result

            # Store result for placeholder resolution (only for successful tools)
            result_data = tool_result.get("result", {})

            # If result is a tuple (success, data), extract data
            if isinstance(result_data, tuple) and len(result_data) == 2:
                success, data = result_data
                # Always try to parse JSON strings to dict for easier navigation
                if isinstance(data, str):
                    try:
                        parsed = json.loads(data)
                        if isinstance(parsed, dict):
                            # Store parsed dict for easy navigation
                            results_by_tool[actual_tool_name] = parsed
                            log.debug(f"Stored parsed result for {actual_tool_name}: {list(parsed.keys())}")
                        else:
                            results_by_tool[actual_tool_name] = data
                    except (json.JSONDecodeError, TypeError):
                        # If not JSON, store as-is
                        results_by_tool[actual_tool_name] = data
                else:
                    results_by_tool[actual_tool_name] = data
            else:
                # Not a tuple, store directly but parse if it's a JSON string
                if isinstance(result_data, str):
                    try:
                        parsed = json.loads(result_data)
                        if isinstance(parsed, dict):
                            results_by_tool[actual_tool_name] = parsed
                            log.debug(f"Stored parsed result for {actual_tool_name}: {list(parsed.keys())}")
                        else:
                            results_by_tool[actual_tool_name] = result_data
                    except (json.JSONDecodeError, TypeError):
                        results_by_tool[actual_tool_name] = result_data
                else:
                    results_by_tool[actual_tool_name] = result_data

            log.info(f"Sequential execution: {actual_tool_name} succeeded")
            # Debug: log what we stored for placeholder resolution
            if actual_tool_name in results_by_tool:
                stored = results_by_tool[actual_tool_name]
                if isinstance(stored, dict):
                    log.debug(f"Stored result keys for {actual_tool_name}: {list(stored.keys())}")
                else:
                    log.debug(f"Stored result type for {actual_tool_name}: {type(stored).__name__}")
        else:
            log.error(f"Unexpected result format from {actual_tool_name}")

    return tool_results


def _navigate_field_path(value: Any, field_path: List[str], log: logging.Logger) -> Optional[Any]:
    """
    Navigate through a field path, handling arrays intelligently.
    
    Handles:
    - Direct dict fields: data.id
    - Array results: data.results.id (gets from first item)
    - Array indices: data.results[0].id
    - Nested structures: data.results[0].nested.field
    
    Args:
        value: Starting value (usually a dict)
        field_path: List of field names to navigate
        log: Logger for debugging
        
    Returns:
        Extracted value or None if path doesn't exist
    """
    current_value = value
    
    i = 0
    while i < len(field_path):
        if current_value is None:
            break
            
        field = field_path[i]
        
        # Handle dict
        if isinstance(current_value, dict):
            current_value = current_value.get(field)
            
            # If we got an array, handle it intelligently
            if isinstance(current_value, list) and len(current_value) > 0:
                # Check if there are more fields to navigate
                if i + 1 < len(field_path):
                    # Try to get the next field from the first array item
                    next_field = field_path[i + 1]
                    first_item = current_value[0]
                    
                    if isinstance(first_item, dict) and next_field in first_item:
                        # Found the field in first item, navigate there
                        current_value = first_item.get(next_field)
                        i += 2  # Skip both current field (array) and next field
                        continue
                    else:
                        # Field not in first item, try parsing as index
                        try:
                            index = int(next_field)
                            if 0 <= index < len(current_value):
                                current_value = current_value[index]
                                i += 2  # Skip array field and index
                                continue
                        except ValueError:
                            pass
                
                # No more fields or couldn't navigate further, use first item
                current_value = current_value[0]
                
        # Handle array (direct array access)
        elif isinstance(current_value, list):
            if len(current_value) > 0:
                # Try to parse field as index
                try:
                    index = int(field)
                    if 0 <= index < len(current_value):
                        current_value = current_value[index]
                    else:
                        return None
                except ValueError:
                    # Not a numeric index, try to get field from first item
                    first_item = current_value[0]
                    if isinstance(first_item, dict) and field in first_item:
                        current_value = first_item.get(field)
                    else:
                        # Try to find field in any item
                        for item in current_value:
                            if isinstance(item, dict) and field in item:
                                current_value = item.get(field)
                                break
                        else:
                            return None
            else:
                return None
                
        # Handle string (try parsing JSON)
        elif isinstance(current_value, str):
            try:
                parsed = json.loads(current_value)
                if isinstance(parsed, dict):
                    current_value = parsed.get(field)
                elif isinstance(parsed, list) and len(parsed) > 0:
                    current_value = parsed[0]
                    if isinstance(current_value, dict):
                        current_value = current_value.get(field)
                else:
                    return None
            except (json.JSONDecodeError, TypeError):
                return None
        else:
            return None
        
        i += 1
    
    return current_value


def _parse_placeholder(placeholder: str, results_by_tool: Dict[str, Any], log: logging.Logger) -> tuple[Optional[str], List[str]]:
    """
    Parse a placeholder like "jira.create_issue.data.key" into tool_ref and field_path.
    
    Returns:
        (tool_ref, field_path) or (None, []) if parsing fails
    """
    # Get available tool names (sorted by length, longest first for better matching)
    available_tool_names = sorted(results_by_tool.keys(), key=len, reverse=True)
    
    # Try to find matching tool name at the start of the placeholder
    for tool_name in available_tool_names:
        # Check if placeholder starts with tool name
        if placeholder.startswith(tool_name + '.'):
            # Extract field path after tool name
            remaining = placeholder[len(tool_name) + 1:]  # +1 for the dot
            field_path = remaining.split('.') if remaining else []
            return tool_name, field_path
    
    # If no match found, fall back to splitting by first dot (legacy format)
    parts = placeholder.split('.', 1)  # Split only on first dot
    if len(parts) < 2:
        log.warning(f"Invalid placeholder format: {placeholder}")
        return None, []
    
    tool_ref = parts[0]
    field_path = parts[1].split('.') if parts[1] else []
    return tool_ref, field_path


def _resolve_placeholders(
    args: Dict[str, Any],
    results_by_tool: Dict[str, Any],
    log: logging.Logger
) -> Dict[str, Any]:
    """
    Resolve placeholders like {{CREATE_ISSUE_RESULT.key}} with actual values from previous tool results.

    Supported placeholder formats:
    - {{TOOL_NAME.field}} - extracts field from tool result
    - {{TOOL_NAME_data.field}} - extracts field from result.data

    Example:
    - {{jira.create_issue.data.key}} -> "PA-690"
    - {{CREATE_ISSUE_RESULT.key}} -> "PA-690" (if result has key)
    """
    # Recursively resolve placeholders in the args dict
    resolved_args = {}

    for key, value in args.items():
        if isinstance(value, str):
            # Check if value contains placeholders
            placeholder_pattern = re.compile(r'\{\{([^}]+)\}\}')
            matches = placeholder_pattern.findall(value)

            if matches:
                # Has placeholders - resolve them
                resolved_value = value
                for match in matches:
                    # Parse placeholder using helper function
                    tool_ref, field_path = _parse_placeholder(match, results_by_tool, log)
                    if tool_ref is None:
                        continue

                    # Try to find matching tool result
                    matched_value = None

                    # First try exact match (most common case)
                    if tool_ref in results_by_tool:
                        tool_result = results_by_tool[tool_ref]
                        try:
                            matched_value = _navigate_field_path(tool_result, field_path, log)
                            if matched_value is not None:
                                log.info(f"Resolved placeholder {{{{{match}}}}} -> {matched_value} (exact match)")
                        except Exception as e:
                            log.warning(f"Error extracting field from placeholder {{{{{match}}}}}: {e}")

                    # If exact match failed, try fuzzy matching
                    if matched_value is None:
                        for tool_name, tool_result in results_by_tool.items():
                            # Check if tool_ref matches tool_name (case-insensitive, ignore dots/underscores)
                            normalized_ref = tool_ref.lower().replace('_', '').replace('.', '').replace('result', '').replace('data', '')
                            normalized_tool = tool_name.lower().replace('_', '').replace('.', '')

                            # Match if normalized versions are similar
                            if normalized_ref == normalized_tool or normalized_ref in normalized_tool or normalized_tool in normalized_ref:
                                try:
                                    matched_value = _navigate_field_path(tool_result, field_path, log)
                                    if matched_value is not None:
                                        log.info(f"Resolved placeholder {{{{{match}}}}} -> {matched_value} (fuzzy match: {tool_name})")
                                        break
                                except Exception as e:
                                    log.warning(f"Error extracting field from placeholder {{{{{match}}}}}: {e}")

                    # If still not resolved, try alternative paths for array results
                    if matched_value is None and len(field_path) >= 2:
                        # Try alternative: if path is "data.id" but data.results exists, try "data.results[0].id"
                        if tool_ref in results_by_tool:
                            tool_result = results_by_tool[tool_ref]
                            try:
                                # Check if we have data.results structure
                                if isinstance(tool_result, dict) and "data" in tool_result:
                                    data = tool_result["data"]
                                    if isinstance(data, dict) and "results" in data:
                                        results = data["results"]
                                        if isinstance(results, list) and len(results) > 0:
                                            # Try to get the field from first result item
                                            first_result = results[0]
                                            if isinstance(first_result, dict):
                                                # Try different field names from the path
                                                for field_name in field_path[-1:]:  # Try last field name
                                                    if field_name in first_result:
                                                        matched_value = first_result.get(field_name)
                                                        log.info(f"Resolved placeholder {{{{{match}}}}} -> {matched_value} (array fallback: data.results[0].{field_name})")
                                                        break
                            except Exception as e:
                                log.debug(f"Array fallback failed for {{{{{match}}}}}: {e}")

                    if matched_value is not None:
                        # Replace placeholder with actual value
                        placeholder_full = f"{{{{{match}}}}}"
                        resolved_value = resolved_value.replace(placeholder_full, str(matched_value))
                    else:
                        # Debug: log available tools for troubleshooting
                        available_tools = list(results_by_tool.keys())
                        log.warning(f"Could not resolve placeholder: {{{{{match}}}}}. Available tools: {available_tools}")
                        log.debug(f"Looking for tool_ref: '{tool_ref}', field_path: {field_path}")

                resolved_args[key] = resolved_value
            else:
                # No placeholders, keep as is
                resolved_args[key] = value
        elif isinstance(value, dict):
            # Recursively resolve placeholders in nested dicts
            resolved_args[key] = _resolve_placeholders(value, results_by_tool, log)
        elif isinstance(value, list):
            # Handle lists - resolve placeholders in each item
            resolved_list = []
            for item in value:
                if isinstance(item, dict):
                    resolved_list.append(_resolve_placeholders(item, results_by_tool, log))
                elif isinstance(item, str) and '{{' in item:
                    # Resolve placeholders in list items
                    placeholder_pattern = re.compile(r'\{\{([^}]+)\}\}')
                    matches = placeholder_pattern.findall(item)
                    resolved_item = item
                    for match in matches:
                        # Parse placeholder using helper function
                        tool_ref, field_path = _parse_placeholder(match, results_by_tool, log)
                        if tool_ref is None:
                            continue
                        
                        matched_value = None

                        # First try exact match
                        if tool_ref in results_by_tool:
                            tool_result = results_by_tool[tool_ref]
                            try:
                                value_to_extract = tool_result
                                for field in field_path:
                                    if isinstance(value_to_extract, dict):
                                        value_to_extract = value_to_extract.get(field)
                                    elif isinstance(value_to_extract, str):
                                        try:
                                            parsed = json.loads(value_to_extract)
                                            if isinstance(parsed, dict):
                                                value_to_extract = parsed.get(field)
                                            else:
                                                break
                                        except (json.JSONDecodeError, TypeError):
                                            break
                                    else:
                                        break

                                if value_to_extract is not None:
                                    matched_value = value_to_extract
                            except Exception:
                                pass

                        # If exact match failed, try fuzzy matching
                        if matched_value is None:
                            for tool_name, tool_result in results_by_tool.items():
                                normalized_ref = tool_ref.lower().replace('_', '').replace('.', '').replace('result', '').replace('data', '')
                                normalized_tool = tool_name.lower().replace('_', '').replace('.', '')

                                if normalized_ref == normalized_tool or normalized_ref in normalized_tool or normalized_tool in normalized_ref:
                                    try:
                                        value_to_extract = tool_result
                                        for field in field_path:
                                            if isinstance(value_to_extract, dict):
                                                value_to_extract = value_to_extract.get(field)
                                            elif isinstance(value_to_extract, str):
                                                try:
                                                    parsed = json.loads(value_to_extract)
                                                    if isinstance(parsed, dict):
                                                        value_to_extract = parsed.get(field)
                                                    else:
                                                        break
                                                except (json.JSONDecodeError, TypeError):
                                                    break
                                            else:
                                                break

                                        if value_to_extract is not None:
                                            matched_value = value_to_extract
                                            break
                                    except Exception:
                                        pass

                            if matched_value is not None:
                                placeholder_full = f"{{{{{match}}}}}"
                                resolved_item = resolved_item.replace(placeholder_full, str(matched_value))

                    resolved_list.append(resolved_item)
                else:
                    resolved_list.append(item)
            resolved_args[key] = resolved_list
        else:
            # Other types, keep as is
            resolved_args[key] = value

    return resolved_args


async def _execute_tools_in_parallel(
    planned_tools: List[Dict[str, Any]],
    tools_by_name: Dict[str, Any],
    llm: Any,
    state: ChatState,
    log: logging.Logger
) -> List[Dict[str, Any]]:
    """Execute tools in parallel (original behavior)"""
    from app.modules.agents.qna.tool_system import _sanitize_tool_name_if_needed

    tasks = []

    for i, tool_call in enumerate(planned_tools[:NodeConfig.MAX_PARALLEL_TOOLS]):
        tool_name = tool_call.get("name", "")
        normalized_name = _sanitize_tool_name_if_needed(tool_name, llm) if llm else tool_name
        actual_tool_name = normalized_name if normalized_name in tools_by_name else tool_name

        if actual_tool_name not in tools_by_name:
            log.warning(f"Tool not found: {tool_name}")
            tasks.append(_create_error_result(tool_name, f"call_{i}_{tool_name}", f"Tool '{tool_name}' not found"))
            continue

        tool_args = tool_call.get("args", {})
        tool_id = f"call_{i}_{actual_tool_name}"

        tasks.append(_execute_single_tool(
            tool=tools_by_name[actual_tool_name],
            tool_name=actual_tool_name,
            tool_args=tool_args,
            tool_id=tool_id,
            state=state,
            log=log
        ))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Extract tool_result from each result
    tool_results = []
    for result in results:
        if isinstance(result, Exception):
            log.error(f"Tool execution exception: {result}")
            continue
        if isinstance(result, dict) and "tool_result" in result:
            tool_results.append(result["tool_result"])

    return tool_results


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
        # Normalize args structure
        if isinstance(tool_args, dict) and "kwargs" in tool_args and len(tool_args) == 1:
            tool_args = tool_args["kwargs"]

        log.debug(f"Executing {tool_name} with {json.dumps(tool_args, default=str)[:100]}...")

        # CRITICAL: Validate and normalize args using Pydantic schema BEFORE calling tool
        # This ensures model_validator normalizes field names (e.g., issueKey -> issue_key)
        validated_args = tool_args
        args_schema = None

        # Try to get schema from StructuredTool first (if it's a LangChain tool)
        if hasattr(tool, 'args_schema'):
            args_schema = tool.args_schema
        elif hasattr(tool, 'args') and hasattr(tool.args, 'schema'):
            # LangChain StructuredTool stores schema in tool.args.schema
            try:
                args_schema = tool.args.schema
            except AttributeError:
                pass

        # Fallback: Get schema from registry
        if args_schema is None:
            try:
                from app.agents.tools.registry import _global_tools_registry
                tool_metadata = _global_tools_registry.get_metadata(tool_name)
                if tool_metadata and hasattr(tool_metadata, 'tool') and hasattr(tool_metadata.tool, 'args_schema'):
                    args_schema = tool_metadata.tool.args_schema
            except Exception as e:
                log.debug(f"Could not get schema from registry for {tool_name}: {e}")

        # Validate and normalize using Pydantic schema if available
        if args_schema:
            try:
                from pydantic import ValidationError
                # Validate and normalize - this triggers model_validator(mode='before')
                validated_model = args_schema.model_validate(tool_args)
                # Convert validated Pydantic model to dict for kwargs
                validated_args = validated_model.model_dump(exclude_unset=True)
                log.debug(f"✅ Validated/normalized args for {tool_name}: {json.dumps(validated_args, default=str)[:100]}")
            except ValidationError as e:
                duration_ms = int((time.perf_counter() - start_time) * 1000)
                error_details = {
                    "status": "error",
                    "message": f"Validation error for {tool_name}: {str(e)}",
                    "tool": tool_name,
                    "args": tool_args,
                    "validation_errors": e.errors() if hasattr(e, 'errors') else []
                }
                log.error(f"❌ Pydantic validation failed for {tool_name} after {duration_ms}ms: {e}")
                return {
                    "tool_result": {
                        "tool_name": tool_name,
                        "result": json.dumps(error_details, indent=2),
                        "status": "error",
                        "tool_id": tool_id,
                        "args": tool_args,
                        "duration_ms": duration_ms
                    },
                    "tool_message": ToolMessage(
                        content=json.dumps({
                            "status": "error",
                            "message": f"Validation error for {tool_name}: {str(e)}",
                            "tool": tool_name
                        }, indent=2),
                        tool_call_id=tool_id
                    )
                }
            except Exception as validation_error:
                log.warning(f"⚠️ Unexpected error during validation for {tool_name}: {validation_error}")
                # Continue with original args if validation fails unexpectedly

        # Execute with validated/normalized args
        async def run_tool() -> object:
            if hasattr(tool, 'arun'):
                # For StructuredTool, pass validated_args as dict
                # LangChain will handle it (but we've already validated/normalized)
                return await tool.arun(validated_args)
            elif hasattr(tool, '_run'):
                loop = asyncio.get_running_loop()
                return await loop.run_in_executor(None, functools.partial(tool._run, **validated_args))
            else:
                loop = asyncio.get_running_loop()
                return await loop.run_in_executor(None, functools.partial(tool.run, **validated_args))

        result = await asyncio.wait_for(run_tool(), timeout=NodeConfig.TOOL_TIMEOUT_SECONDS)

        # Log before success detection
        log.info(f"🔍 Tool {tool_name} result before success detection - type: {type(result).__name__}")
        if isinstance(result, tuple):
            log.info(f"🔍 Tool {tool_name} result tuple length: {len(result)}, first element: {result[0] if len(result) > 0 else 'N/A'}")

        is_success = _detect_tool_success(result)
        log.info(f"🔍 Tool {tool_name} success detection result: {is_success}")

        # Log raw tool result
        log.info(f"🔍 Tool {tool_name} raw result type: {type(result).__name__}")
        if isinstance(result, tuple):
            log.debug(f"🔍 Tool {tool_name} raw result tuple: success={result[0] if len(result) > 0 else 'N/A'}, data_type={type(result[1]).__name__ if len(result) > 1 else 'N/A'}")
            if len(result) > 1:
                result_data = result[1]
                if isinstance(result_data, str):
                    log.debug(f"🔍 Tool {tool_name} raw result data (first 500 chars): {result_data[:500]}")
                else:
                    log.debug(f"🔍 Tool {tool_name} raw result data: {json.dumps(result_data, default=str, indent=2)[:1000]}")
        else:
            if isinstance(result, str):
                log.debug(f"🔍 Tool {tool_name} raw result (first 500 chars): {result[:500]}")
            else:
                log.debug(f"🔍 Tool {tool_name} raw result: {json.dumps(result, default=str, indent=2)[:1000]}")

        # Handle retrieval output
        content = result
        if "retrieval" in tool_name.lower():
            content = _process_retrieval_output(result, state, log)
        else:
            content = clean_tool_result(result)

        # Log cleaned content
        log.debug(f"🔍 Tool {tool_name} cleaned content type: {type(content).__name__}")
        if isinstance(content, str):
            log.debug(f"🔍 Tool {tool_name} cleaned content (first 500 chars): {content[:500]}")
        else:
            log.debug(f"🔍 Tool {tool_name} cleaned content: {json.dumps(content, default=str, indent=2)[:1000]}")

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

        # Special handling for retrieval timeouts
        if "retrieval" in tool_name.lower():
            error_msg = f"Search timed out after {duration_ms:.0f}ms - query may be too complex. Try a simpler query."
        else:
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

    # Count successes and failures
    successful = [r for r in tool_results if r.get("status") == "success"]
    failed = [r for r in tool_results if r.get("status") == "error"]
    iteration_count = state.get("iteration_count", 0)
    max_iterations = state.get("max_iterations", 3)

    log.info(f"Tool execution summary: {len(successful)} succeeded, {len(failed)} failed")

    # NEW: Detailed logging for debugging
    for r in successful:
        log.debug(f"✅ Success: {r.get('tool_name')} - {str(r.get('result', ''))[:100]}")
    for r in failed:
        log.debug(f"❌ Failed: {r.get('tool_name')} - {str(r.get('result', ''))[:100]}")

    # Check if we have retrieval results (internal knowledge)
    has_retrieval_success = any(
        "retrieval" in r.get("tool_name", "").lower()
        for r in successful
    )

    # CRITICAL FIX: If we have ANY successful results, proceed to respond
    # Don't treat partial success as complete failure
    # Exception: If ALL tools failed, then it's an error
    if len(successful) > 0 and len(failed) > 0:
        # Partial success - some tools succeeded, some failed
        log.info(f"Partial success: {len(successful)} succeeded, {len(failed)} failed")

        # Check if the failed tools are critical or secondary
        # For cascading tools, if primary tool (e.g., create_issue) succeeded
        # but secondary tool (e.g., add_comment) failed, still proceed
        query = state.get("query", "").lower()
        primary_succeeded = _check_primary_tool_success(query, successful, log)

        if primary_succeeded or has_retrieval_success:
            log.info("Primary tool or retrieval succeeded - proceeding despite failures")
            state["reflection_decision"] = "respond_success"
            state["reflection"] = {
                "decision": "respond_success",
                "reasoning": f"Primary task completed: {len(successful)} tools succeeded (ignoring {len(failed)} secondary failures)",
                "task_complete": True
            }
            log.info("Reflect: respond_success (partial success - primary completed)")
            return state

    # EXISTING: Partial success with retrieval
    if has_retrieval_success and len(successful) > 0:
        log.info(f"Partial success: {len(successful)} succeeded, {len(failed)} failed (has retrieval data)")
        state["reflection_decision"] = "respond_success"
        state["reflection"] = {
            "decision": "respond_success",
            "reasoning": f"Proceeding with {len(successful)} successful results despite {len(failed)} timeouts",
            "task_complete": True
        }
        log.info("Reflect: respond_success (partial success with retrieval data)")
        return state

    # Fast path: all succeeded - but check if task is complete
    if not failed:
        # All tools succeeded - check if task is complete
        query = state.get("query", "").lower()
        executed_tools = [r.get("tool_name", "") for r in tool_results]

        # Fast path pattern matching for task completion
        needs_continue = _check_if_task_needs_continue(query, executed_tools, tool_results, log)

        if needs_continue and iteration_count < max_iterations:
            state["reflection_decision"] = "continue_with_more_tools"
            state["reflection"] = {
                "decision": "continue_with_more_tools",
                "reasoning": "Tools succeeded but task needs more steps",
                "task_complete": False
            }
            log.info(f"Reflect: continue_with_more_tools (task incomplete, iteration {iteration_count + 1}/{max_iterations})")
            return state
        else:
            state["reflection_decision"] = "respond_success"
            state["reflection"] = {
                "decision": "respond_success",
                "reasoning": "All succeeded" if not needs_continue else "Max iterations reached",
                "task_complete": not needs_continue
            }
            log.info("Reflect: respond_success (fast path)")
            return state

    # Check if primary task succeeded (e.g., create succeeded even if add_comment failed)
    # The first planned tool is usually the primary action
    planned_tools = state.get("planned_tool_calls", [])
    primary_action_succeeded = False

    if planned_tools and len(planned_tools) > 0:
        primary_tool_name = planned_tools[0].get("name", "").lower()

        # Check if the primary tool succeeded
        for result in successful:
            tool_name = result.get("tool_name", "").lower()
            # Normalize tool names (handle sanitized names with underscores)
            normalized_primary = primary_tool_name.replace('.', '_')
            normalized_tool = tool_name.replace('.', '_')

            if tool_name == primary_tool_name or normalized_tool == normalized_primary:
                primary_action_succeeded = True
                log.info(f"Primary action tool succeeded: {tool_name} (was first planned tool: {primary_tool_name})")
                break

    # If primary action succeeded, don't treat dependent failures as unrecoverable
    if primary_action_succeeded:
        log.info(f"Primary action succeeded despite {len(failed)} dependent tool failure(s) - proceeding with success")
        state["reflection_decision"] = "respond_success"
        state["reflection"] = {
            "decision": "respond_success",
            "reasoning": f"Primary action succeeded, {len(failed)} dependent tool(s) failed but task is complete",
            "task_complete": True
        }
        log.info("Reflect: respond_success (primary action succeeded)")
        return state

    # Fast path: unrecoverable errors (only if primary action didn't succeed)
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

        # Handle parameter type errors (e.g., space_id should be numeric ID but got key)
        if "not the correct type" in error_text or "expected type" in error_text or "expected type is long" in error_text.lower():
            # Check if it's a Confluence space_id issue
            if "spaceid" in error_text.lower() or "space_id" in error_text.lower() or ("space" in error_text.lower() and "long" in error_text.lower()):
                # Check if the failed tool was create_page
                failed_tool = failed[0].get("tool_name", "") if failed else ""
                if "create_page" in failed_tool.lower():
                    state["reflection_decision"] = "retry_with_fix"
                    state["reflection"] = {
                        "decision": "retry_with_fix",
                        "reasoning": "Space ID type error - need to resolve space key to numeric ID",
                        "fix_instruction": "The space_id is a key but API needs numeric ID. Check Reference Data for confluence_space with matching name/key and use its 'id' field. If not found, call get_spaces to get the numeric ID, then call create_page with that ID. DO NOT switch to get_spaces as final tool - you still need to CREATE the page."
                    }
                    log.info("Reflect: retry_with_fix (space_id resolution needed)")
                    return state
                elif "search_pages" in failed_tool.lower():
                    state["reflection_decision"] = "retry_with_fix"
                    state["reflection"] = {
                        "decision": "retry_with_fix",
                        "reasoning": "Space ID type error in search",
                        "fix_instruction": "For confluence.search_pages, space_id is optional. Remove the space_id parameter and search without it, or first call confluence.get_spaces to get the numeric ID."
                    }
                    log.info("Reflect: retry_with_fix (search space_id)")
                    return state

            # Generic parameter type error
            state["reflection_decision"] = "retry_with_fix"
            state["reflection"] = {
                "decision": "retry_with_fix",
                "reasoning": "Parameter type error",
                "fix_instruction": "Check parameter types. If API expects a different type, check tool documentation and adjust the parameter value accordingly."
            }
            log.info("Reflect: retry_with_fix (parameter type)")
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
        max_retries=max_retries,
        iteration_count=iteration_count,
        max_iterations=max_iterations
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
            reflection.setdefault("task_complete", True)
            reflection.setdefault("needs_more_tools", "")
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


# ============================================================================
# Node 5: Prepare Continue
# ============================================================================

async def prepare_continue_node(
    state: ChatState,
    config: RunnableConfig,
    writer: StreamWriter
) -> ChatState:
    """Prepare for continue with more tools (multi-step task)"""
    log = state.get("logger", logger)

    # Increment iteration count (separate from retry_count)
    state["iteration_count"] = state.get("iteration_count", 0) + 1
    state["is_continue"] = True

    # Keep tool results for next planning step (don't clear them)
    # The planner will use these results to plan next steps

    safe_stream_write(writer, {
        "event": "status",
        "data": {"status": "continuing", "message": "Continuing with next steps..."}
    }, config)

    max_iterations = state.get("max_iterations", 3)
    log.info(f"Prepare continue {state['iteration_count']}/{max_iterations}: task needs more steps")

    return state


def route_after_reflect(state: ChatState) -> Literal["prepare_retry", "prepare_continue", "respond"]:
    """Route based on reflection"""
    decision = state.get("reflection_decision", "respond_success")
    retry_count = state.get("retry_count", 0)
    max_retries = state.get("max_retries", 1)
    iteration_count = state.get("iteration_count", 0)
    max_iterations = state.get("max_iterations", 3)

    if decision == "retry_with_fix" and retry_count < max_retries:
        return "prepare_retry"

    if decision == "continue_with_more_tools" and iteration_count < max_iterations:
        return "prepare_continue"

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

    # Error - only treat as error if reflection explicitly says so AND no tools succeeded
    # If we have successful tools, reflection should have set respond_success
    has_failed = failed_count > 0
    has_no_success = successful_count == 0 and not final_results
    all_failed = has_failed and successful_count == 0 and len(tool_results) > 0

    # Only treat as error if:
    # 1. Reflection explicitly says respond_error AND no tools succeeded, OR
    # 2. All tools failed (no success at all)
    if (reflection_decision == "respond_error" and has_no_success) or all_failed:
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


            # Extract data from tuple if it's a (bool, str) tuple
            if isinstance(content, tuple) and len(content) == TOOL_RESULT_TUPLE_LENGTH:
                success, data = content
                content = data  # Use just the data part, not the tuple

            if isinstance(content, (dict, list)):
                content_str = json.dumps(content, indent=2, default=str)
            elif isinstance(content, str):
                # If it's a JSON string, try to parse and format it nicely
                try:
                    parsed = json.loads(content)
                    content_str = json.dumps(parsed, indent=2, default=str)
                except (json.JSONDecodeError, TypeError):
                    content_str = content
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

    context = "".join(parts)
    return context


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
# Modern ReAct Agent Node (with Cascading Tool Support)
# ============================================================================

async def react_agent_node(
    state: ChatState,
    config: RunnableConfig,
    writer: StreamWriter
) -> ChatState:
    """
    ReAct agent node with cascading tool execution support.

    This node uses LangGraph's create_react_agent which naturally handles
    cascading tool calls - one tool's output can be used as input to the next.

    The ReAct agent automatically:
    - Observes tool results
    - Decides if more tools are needed
    - Uses results as inputs to next tool
    - Repeats until task complete
    """
    start_time = time.perf_counter()
    log = state.get("logger", logger)
    llm = state.get("llm")
    query = state.get("query", "")

    try:
        from langchain.agents import create_agent
        from langchain_core.messages import HumanMessage, ToolMessage

        from app.modules.agents.qna.tool_system import get_agent_tools_with_schemas

        safe_stream_write(writer, {
            "event": "status",
            "data": {"status": "thinking", "message": "Thinking..."}
        }, config)

        # Get tools with Pydantic schemas
        tools = get_agent_tools_with_schemas(state)
        log.info(f"ReAct agent loaded {len(tools)} tools with schemas")

        # Build system prompt
        system_prompt = _build_react_system_prompt(state, log)

        # Create ReAct agent - automatically handles cascading tool calls
        agent = create_agent(
            model=llm,
            tools=tools,
        )

        # Build messages with system prompt
        from langchain_core.messages import SystemMessage
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=query)
        ]

        # Execute agent - handles cascading automatically
        final_messages = []
        tool_results = []

        async for chunk in agent.astream(
            {"messages": messages},
            config=config
        ):
            # Process chunk for streaming
            chunk_messages = chunk.get("messages", [])
            final_messages = chunk_messages

            # Stream tool calls and responses
            _process_react_chunk(chunk, writer, config, state, log)

            # Extract tool results as we go
            for msg in chunk_messages:
                if isinstance(msg, ToolMessage):
                    tool_name = msg.name if hasattr(msg, 'name') else "unknown"
                    result_content = msg.content

                    # Process retrieval tool results to extract final_results
                    # Retrieval tool returns JSON string with RetrievalToolOutput structure
                    if "retrieval" in tool_name.lower():
                        try:
                            # Parse JSON string to dict
                            if isinstance(result_content, str):
                                parsed = json.loads(result_content)
                                _process_retrieval_output(parsed, state, log)
                            elif isinstance(result_content, dict):
                                _process_retrieval_output(result_content, state, log)
                            else:
                                # Try to convert to string and parse
                                _process_retrieval_output(str(result_content), state, log)
                        except Exception as e:
                            log.warning(f"Failed to process retrieval output: {e}")
                            # Try direct processing as fallback
                            _process_retrieval_output(result_content, state, log)

                    tool_results.append({
                        "tool_name": tool_name,
                        "status": "success",
                        "result": result_content,
                        "tool_call_id": msg.tool_call_id if hasattr(msg, 'tool_call_id') else None
                    })

        # Get retrieval results (internal knowledge) - may have been populated by retrieval tool
        final_results = state.get("final_results", [])
        virtual_record_map = state.get("virtual_record_id_to_result", {})
        tool_records = state.get("tool_records", [])
        has_retrieval = bool(final_results)

        # Separate retrieval tools from API tools
        # retrieval_tools = [r for r in tool_results if "retrieval" in r.get("tool_name", "").lower()]
        api_tools = [r for r in tool_results if "retrieval" not in r.get("tool_name", "").lower()]

        # Extract final response from messages
        response = _extract_final_response(final_messages, log)

        # If we have retrieval results, we need to use stream_llm_response for proper citation handling
        # This matches how the old system (respond_node) handles responses with citations
        # The ReAct agent may have generated a response, but we need to ensure citations are properly formatted
        if final_results or tool_results:
            # Build messages for response generation (similar to respond_node)
            # This ensures proper citation formatting like the old system
            messages = create_response_messages(state)

            # Add tool results context (same as old system)
            if tool_results or final_results:
                context = _build_tool_results_context(tool_results, final_results)
                if context.strip():
                    if messages and isinstance(messages[-1], HumanMessage):
                        messages[-1].content += context
                    else:
                        messages.append(HumanMessage(content=context))

            # Use stream_llm_response for proper citation handling (same as old system)
            # This ensures citations are properly extracted and formatted
            try:
                answer_text = ""
                citations = []
                reason = None
                confidence = None
                reference_data = []
                block_numbers = []

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

                    # Stream events to writer
                    safe_stream_write(writer, {"event": event_type, "data": event_data}, config)

                    if event_type == "complete":
                        answer_text = event_data.get("answer", "")
                        citations = event_data.get("citations", [])
                        reason = event_data.get("reason")
                        confidence = event_data.get("confidence")
                        reference_data = event_data.get("referenceData", [])
                        block_numbers = event_data.get("blockNumbers", [])

                # Use the response from stream_llm_response (has proper citations)
                # Fallback to ReAct agent response if stream_llm_response didn't provide one
                if answer_text:
                    response = answer_text
                elif not response:
                    # If ReAct agent didn't generate a response either, use fallback
                    response = "I completed the task, but couldn't generate a response."

                # Determine answerMatchType
                if has_retrieval and api_tools:
                    answer_match_type = "Derived From Blocks"  # Mixed: retrieval + API
                elif has_retrieval:
                    answer_match_type = "Derived From Blocks"  # Only retrieval
                elif api_tools:
                    answer_match_type = "Derived From Tool Execution"  # Only API tools
                else:
                    answer_match_type = "Direct Answer"  # No tools used

                # Build completion data with proper response type (same format as old system)
                completion_data = {
                    "answer": response,
                    "citations": citations,
                    "confidence": confidence or "High",
                    "answerMatchType": answer_match_type
                }

                if block_numbers:
                    completion_data["blockNumbers"] = block_numbers
                if reference_data:
                    completion_data["referenceData"] = reference_data
                if reason:
                    completion_data["reason"] = reason

                state["completion_data"] = completion_data

            except Exception as e:
                log.error(f"Response generation with citations failed: {e}", exc_info=True)
                # Fallback: extract citations from ReAct agent response
                citations, block_numbers, reference_data, answer_match_type = _extract_citations_and_metadata(
                    tool_results, final_results, has_retrieval, api_tools, response, log, virtual_record_map
                )
                state["completion_data"] = {
                    "answer": response,
                    "citations": citations,
                    "blockNumbers": block_numbers,
                    "referenceData": reference_data,
                    "confidence": "High",
                    "answerMatchType": answer_match_type
                }
        else:
            # No tool results or retrieval - use extracted response directly
            citations, block_numbers, reference_data, answer_match_type = _extract_citations_and_metadata(
                tool_results, final_results, has_retrieval, api_tools, response, log, virtual_record_map
            )
            state["completion_data"] = {
                "answer": response,
                "citations": citations,
                "blockNumbers": block_numbers,
                "referenceData": reference_data,
                "confidence": "High",
                "answerMatchType": answer_match_type
            }

        # Update state
        state["response"] = response
        state["tool_results"] = tool_results
        state["all_tool_results"] = tool_results

        duration_ms = (time.perf_counter() - start_time) * 1000
        log.info(f"⚡ ReAct Agent: {duration_ms:.0f}ms, {len(tool_results)} tool calls")

    except ImportError as e:
        log.error(f"ReAct agent dependencies not available: {e}")
        # Fallback to direct response
        state["response"] = "ReAct agent is not available. Please use the standard agent."
        state["completion_data"] = {
            "answer": state["response"],
            "confidence": "Low",
            "answerMatchType": "Error"
        }
    except Exception as e:
        log.error(f"ReAct agent error: {e}", exc_info=True)
        state["response"] = f"I encountered an error: {str(e)}"
        state["completion_data"] = {
            "answer": state["response"],
            "confidence": "Low",
            "answerMatchType": "Error"
        }

    return state


def _build_react_system_prompt(state: ChatState, log: logging.Logger) -> str:
    """Build system prompt for ReAct agent with citation support"""
    base_prompt = """You are an intelligent AI assistant that can use tools to help users.

## Tool Usage Guidelines

1. **Cascading Tool Calls**: You can call multiple tools in sequence. Use results from one tool as inputs to the next.
   - Example: First call `confluence.get_spaces()` to find a space ID, then use that ID in `confluence.create_page()`

2. **Tool Selection**: Choose the right tool based on user intent:
   - "create"/"make"/"new" → CREATE tools
   - "get"/"find"/"search"/"list" → READ/SEARCH tools
   - "update"/"modify"/"change" → UPDATE tools
   - "delete"/"remove" → DELETE tools

3. **Parameter Validation**: All tool parameters are validated by Pydantic schemas. Use exact parameter names and types.

4. **Error Handling**: If a tool fails, analyze the error and try a different approach or ask for clarification.

5. **Task Completion**: Continue calling tools until the user's request is fully satisfied.

6. **Response Format**: When you have tool results, format your response clearly:
   - For API tool results: Transform data into professional markdown
   - For retrieval/internal knowledge: Include inline citations like [R1-1] after facts
   - Store technical IDs in referenceData for follow-up queries
"""

    # Check for retrieval results and API tools
    final_results = state.get("final_results", [])
    has_retrieval = bool(final_results)

    # Add citation instructions if retrieval results exist
    if has_retrieval:
        base_prompt += """
## Citation Rules (CRITICAL)

When you have internal knowledge from retrieval tools:
1. Put citation IMMEDIATELY after each fact: "Revenue grew 29% [R1-1]."
2. One citation per bracket: [R1-1][R2-3] NOT [R1-1, R2-3]
3. Include ALL cited blocks in your response
4. Do NOT put citations at end of paragraph - inline after each fact
"""

    # Add tool-specific guidance
    if _has_jira_tools(state):
        base_prompt += "\n" + JIRA_GUIDANCE

    if _has_confluence_tools(state):
        base_prompt += "\n" + CONFLUENCE_GUIDANCE

    # Add user context
    user_context = _format_user_context(state)
    if user_context:
        base_prompt += "\n\n" + user_context

    return base_prompt


def _process_react_chunk(
    chunk: Dict,
    writer: StreamWriter,
    config: RunnableConfig,
    state: ChatState,
    log: logging.Logger
) -> None:
    """Process ReAct agent chunk for streaming"""
    try:
        messages = chunk.get("messages", [])

        for msg in messages:
            # Stream tool calls
            if hasattr(msg, 'tool_calls') and msg.tool_calls:
                for tool_call in msg.tool_calls:
                    safe_stream_write(writer, {
                        "event": "tool_call",
                        "data": {
                            "tool": tool_call.get("name", "unknown"),
                            "args": tool_call.get("args", {}),
                            "id": tool_call.get("id", "")
                        }
                    }, config)

            # Stream tool results
            if isinstance(msg, ToolMessage):
                safe_stream_write(writer, {
                    "event": "tool_result",
                    "data": {
                        "tool": getattr(msg, 'name', 'unknown'),
                        "result": msg.content[:MAX_TOOL_RESULT_PREVIEW_LENGTH] if len(msg.content) > MAX_TOOL_RESULT_PREVIEW_LENGTH else msg.content,
                        "status": "success"
                    }
                }, config)

            # Stream AI responses
            if isinstance(msg, AIMessage) and msg.content:
                # Stream content in chunks
                content = msg.content
                chunk_size = 10
                for i in range(0, len(content), chunk_size):
                    chunk_text = content[i:i + chunk_size]
                    safe_stream_write(writer, {
                        "event": "content",
                        "data": {"text": chunk_text}
                    }, config)
    except Exception as e:
        log.warning(f"Error processing ReAct chunk: {e}")


def _extract_final_response(messages: List, log: logging.Logger) -> str:
    """Extract final response from agent messages"""
    # Find last AIMessage with content
    for msg in reversed(messages):
        if hasattr(msg, 'content') and msg.content and not hasattr(msg, 'tool_calls'):
            return str(msg.content)

    # Fallback: find any message with content
    for msg in reversed(messages):
        if hasattr(msg, 'content') and msg.content:
            return str(msg.content)

    log.warning("No response found in ReAct agent messages")
    return "I completed the task, but couldn't generate a response."


def _extract_citations_and_metadata(
    tool_results: List[Dict],
    final_results: List[Dict],
    has_retrieval: bool,
    api_tools: List[Dict],
    response: str,
    log: logging.Logger,
    virtual_record_map: Optional[Dict[str, Dict[str, Any]]] = None
) -> tuple[List[Dict], List[str], List[Dict], str]:
    """
    Extract citations, block numbers, reference data, and determine answerMatchType.

    Returns:
        Tuple of (citations, block_numbers, reference_data, answer_match_type)
    """
    citations = []
    block_numbers = []
    reference_data = []

    # Extract block numbers from response (e.g., [R1-1], [R2-3])
    import re
    block_pattern = re.compile(r'\[R(\d+)-(\d+)\]')
    matches = block_pattern.findall(response)
    for match in matches:
        block_num = f"R{match[0]}-{match[1]}"
        if block_num not in block_numbers:
            block_numbers.append(block_num)

    # Extract citations from retrieval results (same as old system)
    if has_retrieval and final_results:
        # Use block_number from results if available (set by retrieval tool)
        for result in final_results:
            block_num = result.get("block_number")
            if not block_num:
                # Fallback: generate block number from virtual_record_id
                virtual_id = result.get("virtual_record_id", "")
                if virtual_id and virtual_record_map:
                    # Try to get record number from virtual_record_map
                    record_data = virtual_record_map.get(virtual_id, {})
                    record_num = record_data.get("record_number", 1)
                    block_index = result.get("block_index", 0)
                    block_num = f"R{record_num}-{block_index}"
                else:
                    # Last resort: use index
                    idx = final_results.index(result) if result in final_results else 0
                    block_num = f"R1-{idx+1}"

            citations.append({
                "source": result.get("source", "internal_knowledge"),
                "type": "retrieval",
                "content": str(result.get("content", ""))[:200],
                "virtual_id": result.get("virtual_record_id", ""),
                "block_id": block_num
            })

            # Add to block_numbers if not already there
            if block_num and block_num not in block_numbers:
                block_numbers.append(block_num)

    # Extract reference data from API tool results
    for tool_result in api_tools:
        if tool_result.get("status") == "success":
            result = tool_result.get("result", "")
            ref_data = _extract_reference_data_from_result(result, tool_result.get("tool_name", ""))
            if ref_data:
                reference_data.extend(ref_data)

    # Determine answerMatchType
    if has_retrieval and api_tools:
        answer_match_type = "Derived From Blocks"  # Mixed: retrieval + API
    elif has_retrieval:
        answer_match_type = "Derived From Blocks"  # Only retrieval
    elif api_tools:
        answer_match_type = "Derived From Tool Execution"  # Only API tools
    else:
        answer_match_type = "Direct Answer"  # No tools used

    return citations, block_numbers, reference_data, answer_match_type


def _extract_reference_data_from_result(result: object, tool_name: str) -> List[Dict]:
    """Extract reference data (IDs, keys) from tool results for follow-up queries"""
    reference_data = []

    try:
        # Parse result if it's a string
        if isinstance(result, str):
            import json
            try:
                result = json.loads(result)
            except (json.JSONDecodeError, ValueError):
                return reference_data

        # Extract from common structures
        if isinstance(result, dict):
            # Jira issues
            if "data" in result and isinstance(result["data"], dict):
                issues = result["data"].get("issues", [])
                if isinstance(issues, list):
                    for issue in issues:
                        if isinstance(issue, dict):
                            issue_id = issue.get("id", "")
                            issue_key = issue.get("key", "")
                            # Only add if we have at least key or id
                            if issue_key or issue_id:
                                ref_data = {
                                    "name": issue.get("summary", ""),
                                    "key": issue_key,
                                    "type": "jira_issue"
                                }
                                # Only add id if it exists and is not empty
                                if issue_id:
                                    ref_data["id"] = issue_id
                                reference_data.append(ref_data)

            # Jira projects
            if "data" in result and isinstance(result["data"], list):
                for project in result["data"]:
                    if isinstance(project, dict):
                        project_id = project.get("id", "")
                        project_key = project.get("key", "")
                        # Only add if we have at least key or id
                        if project_key or project_id:
                            ref_data = {
                                "name": project.get("name", ""),
                                "key": project_key,
                                "type": "jira_project"
                            }
                            # Only add id if it exists and is not empty
                            if project_id:
                                ref_data["id"] = project_id
                            reference_data.append(ref_data)

            # Direct issue/project
            if "key" in result:
                item_id = result.get("id", "")
                item_key = result.get("key", "")
                if item_key or item_id:
                    ref_data = {
                        "name": result.get("summary") or result.get("name", ""),
                        "key": item_key,
                        "type": "jira_issue" if "summary" in result else "jira_project"
                    }
                    # Only add id if it exists and is not empty
                    if item_id:
                        ref_data["id"] = item_id
                    reference_data.append(ref_data)

        elif isinstance(result, list):
            for item in result:
                if isinstance(item, dict) and "key" in item:
                    item_id = item.get("id", "")
                    item_key = item.get("key", "")
                    if item_key or item_id:
                        ref_data = {
                            "name": item.get("summary") or item.get("name", ""),
                            "key": item_key,
                            "type": "jira_issue" if "summary" in item else "jira_project"
                        }
                        # Only add id if it exists and is not empty
                        if item_id:
                            ref_data["id"] = item_id
                        reference_data.append(ref_data)

    except Exception as e:
        logger.debug(f"Error extracting reference data: {e}")

    return reference_data


# ============================================================================
# Exports
# ============================================================================

__all__ = [
    "planner_node",
    "execute_node",
    "respond_node",
    "reflect_node",
    "prepare_retry_node",
    "react_agent_node",  # NEW: ReAct agent node
    "should_execute_tools",
    "route_after_reflect",
    "check_for_error",
    "NodeConfig",
    "clean_tool_result",
    "format_result_for_llm",
]

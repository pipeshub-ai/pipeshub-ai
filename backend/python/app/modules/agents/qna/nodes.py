"""
Agent Node Implementations

Enterprise-grade agent system with:
- Reliable cascading tool execution
- Smart placeholder resolution
- Robust error handling and recovery
- Accurate tool result processing
- Context-aware conversation handling
- Comprehensive logging and debugging
"""


import asyncio
import contextlib
import json
import logging
import os
import re
import time
from typing import Any, Dict, Literal, Union
from uuid import UUID

from pydantic import ValidationError

from app.config.constants.service import config_node_constants
from app.config.configuration_service import ConfigurationService
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.runnables.config import var_child_runnable_config
from langgraph.types import StreamWriter

from app.modules.agents.capability_summary import build_capability_summary
from app.modules.agents.context.connector_detection import (
    _has_clickup_tools,
    _has_confluence_tools,
    _has_github_tools,
    _has_jira_tools,
    _has_mariadb_tools,
    _has_onedrive_tools,
    _has_outlook_tools,
    _has_redshift_tools,
    _has_salesforce_tools,
    _has_sharepoint_tools,
    _has_slack_tools,
    _has_teams_tools,
    _has_zoom_tools,
    derive_active_connectors,
    has_connector,
)
from app.modules.agents.context.knowledge_context import _build_knowledge_context
from app.modules.agents.context.retry_context import (
    _build_continue_context,
    _build_retry_context,
    _extract_invalid_params_from_args,
    _extract_missing_params_from_error,
)
from app.modules.agents.context.tool_descriptions import (
    _extract_parameters_from_schema,
    _format_tool_descriptions,
    _get_cached_tool_descriptions,
    _get_field_type_name,
    _get_field_type_name_v1,
    _tool_description_cache,
)
from app.modules.agents.context.tool_result_extractor import ToolResultExtractor
from app.modules.agents.context.tool_results_context import _build_tool_results_context
from app.modules.agents.context.user_context import _format_user_context
from app.modules.agents.context.workflow_patterns import _build_workflow_patterns
from app.modules.agents.prompts.connector_guidance import (
    CLICKUP_GUIDANCE,
    CONFLUENCE_GUIDANCE,
    GITHUB_GUIDANCE,
    GUIDANCE_MAP,
    JIRA_GUIDANCE,
    MARIADB_GUIDANCE,
    ONEDRIVE_GUIDANCE,
    OUTLOOK_GUIDANCE,
    REDSHIFT_GUIDANCE,
    SALESFORCE_GUIDANCE,
    SHAREPOINT_GUIDANCE,
    SLACK_GUIDANCE,
    TEAMS_GUIDANCE,
    ZOOM_GUIDANCE,
)
from app.modules.agents.prompts.planner import (
    PLANNER_SYSTEM_PROMPT,
    PLANNER_USER_TEMPLATE,
    PLANNER_USER_TEMPLATE_WITH_CONTEXT,
    REFLECT_PROMPT,
)
from app.modules.agents.prompts.react import REACT_BASE_PROMPT
from app.modules.agents.qna.chat_state import ChatState, is_custom_agent_system_prompt
from app.modules.agents.qna.reference_data import (
    format_reference_data,
    generate_field_instructions,
    normalize_reference_data_items,
)
from app.modules.agents.qna.stream_utils import safe_stream_write, send_keepalive
from app.modules.qna.response_prompt import (
    build_direct_answer_time_context,
    create_response_messages,
)
from app.utils.aimodels import coerce_message_content_to_text
from app.utils.streaming import stream_llm_response, stream_llm_response_with_tools
from app.utils.time_conversion import build_llm_time_context

# ============================================================================
# CONSTANTS & CONFIGURATION
# ============================================================================

# Logging
logger = logging.getLogger(__name__)

# Tool execution constants
TOOL_RESULT_TUPLE_LENGTH = 2
MAX_PARALLEL_TOOLS = 10
TOOL_TIMEOUT_SECONDS = 60.0
RETRIEVAL_TIMEOUT_SECONDS = 45.0  # Faster timeout for retrieval
# Max chars of Pydantic validation detail embedded in tool results for the LLM planner
_MAX_VALIDATION_ERROR_LLM_CHARS = 2000
_MAX_TOOL_VALIDATION_RESULT_CHARS = 12000

# Response formatting constants
# NOTE: Truncation limits are set high to preserve context. Only truncate if absolutely necessary.
USER_QUERY_MAX_LENGTH = 10000  # Increased significantly to preserve full user queries
BOT_RESPONSE_MAX_LENGTH = 20000  # Increased significantly to preserve full bot responses
MAX_TOOL_RESULT_PREVIEW_LENGTH = 500
MAX_AVAILABLE_TOOLS_DISPLAY = 20
MAX_CONVERSATION_HISTORY = 20  # Number of user+bot message pairs to include (sliding window)

# Truncation / display limits
_RAW_DATA_SIZE_LIMIT = 8000
_TOOL_LOG_LIMIT = 5
_PARAM_DESC_TRUNCATE = 60
_REASONING_DISPLAY_LEN = 200

# Orchestration status taxonomy (metadata fields on tool result dicts)
ORCHESTRATION_STATUS_RESOLVED = "resolved"
ORCHESTRATION_STATUS_PARTIAL = "partial_failure"
ORCHESTRATION_STATUS_CASCADE_BROKEN = "cascade_broken"

# Content detection constants
MIN_CONTENT_LENGTH_FOR_REUSE = 500  # Minimum chars for content to be considered reusable
MIN_PLACEHOLDER_PARTS = 2  # Minimum parts in placeholder for fuzzy matching
MAX_TOOL_DESCRIPTION_LENGTH = 200  # Maximum length for tool descriptions in prompts

# Opik tracer initialization
_opik_tracer = None
_opik_api_key = os.getenv("OPIK_API_KEY")
_opik_workspace = os.getenv("OPIK_WORKSPACE")
if _opik_api_key and _opik_workspace:
    try:
        from opik.integrations.langchain import OpikTracer
        _opik_tracer = OpikTracer()
        logger.info("✅ Opik tracer initialized")
    except Exception as e:
        logger.warning(f"⚠️ Failed to initialize Opik tracer: {e}")


# ============================================================================
# CONFIGURATION CLASS
# ============================================================================

class NodeConfig:
    """Centralized node behavior configuration"""
    MAX_PARALLEL_TOOLS: int = 10
    TOOL_TIMEOUT_SECONDS: float = 60.0
    RETRIEVAL_TIMEOUT_SECONDS: float = 60.0  # Faster timeout for retrieval
    # Generative image models can easily take 1-3 minutes per call, and
    # multi-image requests (n > 1) stack that up further. Give them plenty
    # of headroom.
    IMAGE_GENERATION_TIMEOUT_SECONDS: float = 300.0
    PLANNER_TIMEOUT_SECONDS: float = 45.0
    REFLECTION_TIMEOUT_SECONDS: float = 8.0

    # Retry & iteration limits
    MAX_RETRIES: int = 1
    MAX_ITERATIONS: int = 3
    MAX_VALIDATION_RETRIES: int = 2

    # Query limits
    MAX_RETRIEVAL_QUERIES: int = 6
    MAX_QUERY_LENGTH: int = 100
    MAX_QUERY_WORDS: int = 8


# ============================================================================
# RESULT CLEANING UTILITIES
# ============================================================================

REMOVE_FIELDS = {
    "self", "_links", "_embedded", "_meta", "_metadata",
    "expand", "expansions", "schema", "$schema",
    "avatarUrls", "avatarUrl", "iconUrl", "iconUri", "thumbnailUrl",
    "avatar", "icon", "thumbnail", "profilePicture",
    # KEEP pagination fields for pagination awareness:
    # "nextPageToken", "prevPageToken", "pageToken",  # Keep these!
    # "cursor", "offset",  # Keep for pagination context
    # "pagination",  # Keep pagination info
    # "startAt", "maxResults", "total",  # Keep for pagination awareness
    # "isLast",  # Keep for pagination awareness
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
        cleaned = {}
        for key, value in result.items():
            if key in REMOVE_FIELDS or key.lower() in REMOVE_FIELDS:
                continue
            if key.startswith(("_", "$")):
                continue

            if isinstance(value, dict):
                cleaned_value = clean_tool_result(value)
                if cleaned_value:
                    cleaned[key] = cleaned_value
            elif isinstance(value, list):
                cleaned[key] = [clean_tool_result(item) for item in value]
            else:
                cleaned[key] = value
        return cleaned

    if isinstance(result, list):
        return [clean_tool_result(item) for item in result]

    return result


def format_result_for_llm(result: object, tool_name: str = "") -> str:
    """Format result for LLM consumption"""
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
# TOOL RESULT PROCESSING - RELIABLE EXTRACTION
# ============================================================================



def _is_semantically_empty(result: object) -> bool:
    """Check if a tool result succeeded but contains no meaningful data.

    Used for cascading chain analysis — an empty source tool means
    downstream tools will receive no useful input.
    """
    if result is None:
        return True

    data = ToolResultExtractor.extract_data_from_result(result)
    if data is None:
        return True

    if isinstance(data, dict):
        inner = data.get("data")
        if isinstance(inner, dict):
            for key in ("results", "items", "values"):
                lst = inner.get(key)
                if isinstance(lst, list) and len(lst) == 0:
                    return True
        elif isinstance(inner, list) and len(inner) == 0:
            return True
        for key in ("results", "items", "values", "records"):
            if key in data and isinstance(data[key], list) and len(data[key]) == 0:
                return True

    if isinstance(data, list) and len(data) == 0:
        return True

    return False


def _underscore_to_dotted(name: str) -> str:
    """Convert a sanitized tool name back to its most likely dotted form.

    'jira_search_users' → 'jira.search_users'
    'confluence_get_page_content' → 'confluence.get_page_content'
    'knowledgehub_list_files' → 'knowledgehub.list_files'

    Tool names follow 'app.tool_name' format.  The first underscore
    that corresponds to the app/tool separator is replaced with a dot.

    If the name already contains a dot, return it as-is (don't create invalid names).
    """
    # If name already has a dot, don't convert (avoid creating invalid names like calculator.calculate.single_operand)
    if '.' in name:
        return name

    parts = name.split('_')
    if len(parts) >= 2:
        # First underscore is the app.tool separator
        return parts[0] + '.' + '_'.join(parts[1:])
    return name


# ============================================================================
# PLACEHOLDER RESOLUTION - SIMPLIFIED & RELIABLE
# ============================================================================

class PlaceholderResolver:
    """
    Simplified placeholder resolution for cascading tools.

    Supports formats:
    - {{tool_name.field}} → single field
    - {{tool_name.data.key}} → nested field
    - {{tool_name.results.0.id}} → array index
    """

    PLACEHOLDER_PATTERN = re.compile(r'\{\{([^}]+)\}\}')

    @classmethod
    def has_placeholders(cls, args: dict[str, Any]) -> bool:
        """Check if args contain any placeholders"""
        args_str = json.dumps(args, default=str)
        return bool(cls.PLACEHOLDER_PATTERN.search(args_str))

    @classmethod
    def strip_unresolved(
        cls,
        args: dict[str, Any]
    ) -> tuple[dict[str, Any], list[str]]:
        """
        Replace any remaining unresolved {{...}} placeholders with None.

        This allows tool calls to proceed when only *optional* fields have
        unresolved placeholders.  Required fields that are None will be caught
        by Pydantic validation in _validate_and_normalize_args (returns ``(None, detail)``).

        Returns:
            (cleaned_args, list_of_placeholder_names_that_were_stripped)
        """
        stripped: list[str] = []

        def clean_value(value: object) -> object:
            if isinstance(value, str):
                matches = cls.PLACEHOLDER_PATTERN.findall(value)
                if not matches:
                    return value
                # If the whole value is exactly one placeholder, replace with None
                if value.strip() == f"{{{{{matches[0]}}}}}":
                    stripped.extend(matches)
                    return None
                # Partial placeholder inside a longer string – remove the token
                result = value
                for match in matches:
                    stripped.append(match)
                    result = result.replace(f"{{{{{match}}}}}", "")
                return result.strip() or None
            elif isinstance(value, dict):
                return {k: clean_value(v) for k, v in value.items()}
            elif isinstance(value, list):
                return [clean_value(item) for item in value]
            return value

        cleaned = {k: clean_value(v) for k, v in args.items()}
        return cleaned, stripped

    @classmethod
    def resolve_all(
        cls,
        args: dict[str, Any],
        results_by_tool: dict[str, Any],
        log: logging.Logger
    ) -> dict[str, Any]:
        """
        Resolve all placeholders in args using results from previous tools.

        Returns:
            New dict with all placeholders resolved
        """
        resolved = {}

        for key, value in args.items():
            if isinstance(value, str) and '{{' in value:
                matches = cls.PLACEHOLDER_PATTERN.findall(value)
                # If the entire value is exactly one placeholder, preserve the native
                # type of the resolved data (list, dict, int, etc.) instead of
                # coercing it to str via str(replacement).
                if len(matches) == 1 and value.strip() == f"{{{{{matches[0]}}}}}":
                    raw = cls._resolve_single_placeholder(matches[0], results_by_tool, log)
                    resolved[key] = raw if raw is not None else value
                else:
                    resolved[key] = cls._resolve_string_value(value, results_by_tool, log)
            elif isinstance(value, dict):
                resolved[key] = cls.resolve_all(value, results_by_tool, log)
            elif isinstance(value, list):
                resolved[key] = [
                    cls.resolve_all(item, results_by_tool, log) if isinstance(item, dict)
                    else cls._resolve_string_value(item, results_by_tool, log) if isinstance(item, str) and '{{' in item
                    else item
                    for item in value
                ]
            else:
                resolved[key] = value

        return resolved

    @classmethod
    def _resolve_string_value(
        cls,
        value: str,
        results_by_tool: dict[str, Any],
        log: logging.Logger
    ) -> str:
        """Resolve all placeholders in a string value"""
        matches = cls.PLACEHOLDER_PATTERN.findall(value)
        resolved_value = value

        for match in matches:
            replacement = cls._resolve_single_placeholder(match, results_by_tool, log)
            if replacement is not None:
                placeholder_full = f"{{{{{match}}}}}"
                resolved_value = resolved_value.replace(placeholder_full, str(replacement))
            else:
                log.warning(f"⚠️ Could not resolve placeholder: {{{{{match}}}}}")

        return resolved_value

    @classmethod
    def _resolve_single_placeholder(
        cls,
        placeholder: str,
        results_by_tool: dict[str, Any],
        log: logging.Logger
    ) -> dict[str, Any] | list[Any] | str | int | float | bool | None:
        """
        Resolve a single placeholder to its value.

        Args:
            placeholder: e.g., "jira.create_issue.data.key"
            results_by_tool: {"jira.create_issue": {...}}

        Returns:
            Extracted value or None if not found
        """
        # Parse placeholder into tool_name and field_path
        tool_name, field_path = cls._parse_placeholder(placeholder, results_by_tool)

        if not tool_name or tool_name not in results_by_tool:
            log.debug(f"Tool not found for placeholder: {placeholder}")
            return None

        tool_data = results_by_tool[tool_name]

        # ✅ SPECIAL CASE: Retrieval tool returns a plain-text string (not JSON).
        # No matter what field path the LLM tried to access (e.g. .data.results[0].title),
        # we always return the full retrieved text.  This prevents None-substitution which
        # would strip the field from the downstream tool call and cause the LLM to
        # hallucinate content instead of using the actually-retrieved knowledge.
        if "retrieval" in tool_name.lower() and isinstance(tool_data, str):
            log.info(
                f"✅ Resolved {{{{{placeholder}}}}} → [full retrieval text, {len(tool_data)} chars]"
                + (f" (field path '{field_path}' ignored — retrieval is plain text)" if field_path and field_path != ['data'] else "")
            )
            return tool_data

        # Extract data using field path (for structured results)
        extracted = ToolResultExtractor.extract_field_from_data(tool_data, field_path)

        if extracted is not None:
            log.info(f"✅ Resolved {{{{{placeholder}}}}} → {str(extracted)[:50]}...")
        else:
            log.warning(f"❌ Could not extract field from placeholder: {{{{{placeholder}}}}}")
            log.warning(f"  Field path used: {field_path}")
            # If the LLM used a JSONPath predicate, call it out explicitly
            if '[?' in placeholder or '[*]' in placeholder:
                log.warning(
                    "  ⚠️ Placeholder contained a JSONPath filter/wildcard expression "
                    "(e.g. [?(@.key=='value')]) — normalised to [0]. "
                    "The LLM must use simple numeric indices like [0] in placeholders."
                )
            log.debug(f"Available data: {str(tool_data)[:200]}")
            # Try to show the structure for debugging
            if isinstance(tool_data, dict):
                log.debug(f"Top-level keys: {list(tool_data.keys())}")
                if "data" in tool_data and isinstance(tool_data["data"], dict):
                    log.debug(f"Data keys: {list(tool_data['data'].keys())}")
                    if "results" in tool_data["data"]:
                        results = tool_data["data"]["results"]
                        if isinstance(results, list):
                            if len(results) == 0:
                                log.warning("⚠️ Search returned empty results - cannot access index [0]")
                            elif len(results) > 0:
                                log.debug(f"First result keys: {list(results[0].keys()) if isinstance(results[0], dict) else 'not a dict'}")

        return extracted

    @classmethod
    def _parse_placeholder(
        cls,
        placeholder: str,
        results_by_tool: dict[str, Any]
    ) -> tuple[str | None, list[str]]:
        """
        Parse placeholder into tool_name and field_path.

        Examples:
        - "jira.create_issue.data.key" → ("jira.create_issue", ["data", "key"])
        - "jira.search_users.data[0].accountId" → ("jira.search_users", ["data", "0", "accountId"])
        - "jira.search_users.data.results[0].accountId" → ("jira.search_users", ["data", "0", "accountId"]) (removes .results)
        - "create_issue.key" → ("jira.create_issue", ["key"]) if fuzzy match

        Returns:
            (tool_name, field_path) or (None, []) if can't parse
        """
        # Helper function to parse field path with array indices
        def parse_field_path(path_str: str) -> list[str]:
            """Parse field path handling array indices like [0], [1], [?], [*].

            Non-numeric indices ([?], [*], etc.) are normalised to '0' so that
            LLM-generated placeholders like results[?].id still resolve to the
            first element instead of failing entirely.
            """
            if not path_str:
                return []

            # Split by '.' but preserve array indices
            parts = []
            current = ""
            i = 0
            while i < len(path_str):
                if path_str[i] == '.':
                    if current:
                        parts.append(current)
                        current = ""
                elif path_str[i] == '[':
                    # Found array index
                    if current:
                        parts.append(current)
                        current = ""
                    # Extract index content
                    i += 1
                    index = ""
                    while i < len(path_str) and path_str[i] != ']':
                        index += path_str[i]
                        i += 1
                    if index:
                        # Normalise non-numeric content to first element (index 0).
                        # Covers: [?], [*], [?(@.key=='value')], [?(@.id==123)], any predicate.
                        stripped = index.strip()
                        if stripped and stripped.lstrip('-').isdigit():
                            parts.append(stripped)
                        else:
                            # Any non-numeric token (wildcard / JSONPath predicate) → [0]
                            parts.append('0')
                    # Skip closing ']'
                    if i < len(path_str) and path_str[i] == ']':
                        i += 1
                    continue
                else:
                    current += path_str[i]
                i += 1
            if current:
                parts.append(current)
            return parts

        # Try exact match first (longest tool names first)
        sorted_tools = sorted(results_by_tool.keys(), key=len, reverse=True)

        for tool_name in sorted_tools:
            # Original exact match
            if placeholder.startswith(tool_name + '.'):
                remaining = placeholder[len(tool_name) + 1:]
                field_path = parse_field_path(remaining)
                return tool_name, field_path

            # Auto-resolve dotted ↔ underscore tool names.
            # Planner generates dotted names (jira.search_users) but results
            # may be stored under sanitized names (jira_search_users), or
            # vice versa.  Try both forms to avoid false mismatches.

            # Try dotted form if stored name uses underscores
            # e.g., stored: "jira_search_users" → try matching "jira.search_users."
            dotted_form = _underscore_to_dotted(tool_name)
            if dotted_form != tool_name and placeholder.startswith(dotted_form + '.'):
                remaining = placeholder[len(dotted_form) + 1:]
                field_path = parse_field_path(remaining)
                return tool_name, field_path  # Return the ACTUAL stored key

            # Try underscore form if stored name uses dots
            # e.g., stored: "jira.search_users" → try matching "jira_search_users."
            underscore_form = tool_name.replace('.', '_')
            if underscore_form != tool_name and placeholder.startswith(underscore_form + '.'):
                remaining = placeholder[len(underscore_form) + 1:]
                field_path = parse_field_path(remaining)
                return tool_name, field_path

        # Fuzzy match: try progressively longer dot-prefixes (3, 2, 1 segments)
        # This avoids the old single-segment prefix bug where e.g. "jira" matched
        # "jira_search_users" but leaked "search_users" into the field path.
        parts = placeholder.split('.')
        if len(parts) >= MIN_PLACEHOLDER_PARTS:
            for prefix_len in range(min(len(parts) - 1, 3), 0, -1):
                prefix_candidate = '.'.join(parts[:prefix_len])
                remaining = '.'.join(parts[prefix_len:])
                field_path = parse_field_path(remaining)

                for tool_name in sorted_tools:
                    normalized_tool = tool_name.lower().replace('_', '').replace('.', '')
                    normalized_prefix = prefix_candidate.lower().replace('_', '').replace('.', '')

                    if normalized_prefix == normalized_tool:
                        return tool_name, field_path

        return None, []

    @classmethod
    def _extract_source_tool_name(cls, placeholder: str) -> str | None:
        """Extract the source tool name from a placeholder string.

        'jira.search_users.data.results[0].accountId' -> 'jira.search_users'
        'jira_search_users.data.results[0].accountId' -> 'jira_search_users'
        """
        parts = placeholder.split(".")
        if len(parts) >= 2:
            return f"{parts[0]}.{parts[1]}"
        return parts[0] if parts else None


# ============================================================================
# TOOL EXECUTION - SEQUENTIAL WITH CASCADING SUPPORT
# ============================================================================

class ToolExecutor:
    """Handles tool execution with cascading support"""

    @staticmethod
    def _format_args_preview(args: dict[str, Any], max_len: int = 220) -> str:
        """Return a compact JSON preview for tool args in logs."""
        try:
            preview = json.dumps(args, default=str, ensure_ascii=False)
        except Exception:
            preview = str(args)
        if len(preview) > max_len:
            return preview[:max_len] + "..."
        return preview

    @staticmethod
    async def execute_tools(
        planned_tools: list[dict[str, Any]],
        tools_by_name: dict[str, Any],
        llm: BaseChatModel,
        state: ChatState,
        log: logging.Logger,
        writer: StreamWriter,
        config: RunnableConfig
    ) -> list[dict[str, Any]]:
        """
        Execute tools - sequentially if cascading, parallel otherwise.

        Returns:
            List of tool results with status, result, tool_name, etc.
        """
        # Detect if we need sequential execution (cascading)
        has_cascading = PlaceholderResolver.has_placeholders(
            {"tools": planned_tools}
        )

        if has_cascading:
            log.info("🔗 Cascading detected - executing sequentially")
            return await ToolExecutor._execute_sequential(
                planned_tools, tools_by_name, llm, state, log, writer, config
            )
        else:
            log.info("⚡ No cascading - executing in parallel")
            return await ToolExecutor._execute_parallel(
                planned_tools, tools_by_name, llm, state, log
            )

    @staticmethod
    async def _execute_sequential(
        planned_tools: list[dict[str, Any]],
        tools_by_name: dict[str, Any],
        llm: BaseChatModel,
        state: ChatState,
        log: logging.Logger,
        writer: StreamWriter,
        config: RunnableConfig
    ) -> list[dict[str, Any]]:
        """Execute tools sequentially with placeholder resolution"""
        from app.modules.agents.qna.tool_system import _sanitize_tool_name_if_needed

        tool_results = []
        results_by_tool = {}  # Store successful results for placeholder resolution
        tool_invocation_counts = {}  # Track how many times each tool has been called

        for i, tool_call in enumerate(planned_tools):
            tool_name = tool_call.get("name", "")
            tool_args = tool_call.get("args", {})

            # Resolve tool name: tools_by_name contains both sanitized (underscore)
            # and original (dot) names, so a direct lookup covers the common cases.
            # Fall back to sanitizing the LLM name (replaces dots→underscores) in
            # case the LLM used the dotted form but only the sanitized key is stored.
            actual_tool_name = None
            if tool_name in tools_by_name:
                actual_tool_name = tool_name
            else:
                normalized_name = _sanitize_tool_name_if_needed(tool_name, llm, state) if llm else tool_name
                if normalized_name in tools_by_name:
                    actual_tool_name = normalized_name

            if actual_tool_name is None:
                log.warning(f"❌ Tool not found: {tool_name}")
                tool_results.append({
                    "tool_name": tool_name,
                    "result": f"Error: Tool '{tool_name}' not found",
                    "status": "error",
                    "tool_id": f"call_{i}_{tool_name}"
                })
                continue

            # Resolve placeholders
            resolved_args = PlaceholderResolver.resolve_all(tool_args, results_by_tool, log)

            # Check for unresolved placeholders
            if PlaceholderResolver.has_placeholders(resolved_args):
                unresolved = PlaceholderResolver.PLACEHOLDER_PATTERN.findall(json.dumps(resolved_args))
                log.warning(f"⚠️ Unresolved placeholders in {actual_tool_name}: {unresolved} — stripping to None and proceeding (Pydantic will catch required fields)")

                # Strip unresolved placeholders to None so optional fields are
                # simply omitted and the tool can still run.  Required fields
                # that end up as None will be rejected by Pydantic validation.
                resolved_args, stripped_placeholders = PlaceholderResolver.strip_unresolved(resolved_args)
                log.debug(f"  Stripped {len(stripped_placeholders)} placeholder(s): {stripped_placeholders}")

                # Cascading dependency check: if any stripped placeholder
                # references a tool in this execution chain, it is NOT optional
                # — it means the cascading chain broke.  Skip execution.
                if stripped_placeholders:
                    cascade_failures = []
                    planned_tool_names = {t.get("name", "") for t in planned_tools}
                    planned_tool_names_sanitized = {n.replace(".", "_") for n in planned_tool_names}
                    all_known = planned_tool_names | planned_tool_names_sanitized | set(results_by_tool.keys())

                    for ph in stripped_placeholders:
                        source = PlaceholderResolver._extract_source_tool_name(ph)
                        if source and (source in all_known or source.replace(".", "_") in all_known):
                            cascade_failures.append(ph)

                    if cascade_failures:
                        log.error(
                            f"CASCADE FAILURE: {actual_tool_name} depends on unresolved "
                            f"cascading placeholders: {cascade_failures}. Skipping execution."
                        )
                        tool_results.append({
                            "tool_name": actual_tool_name,
                            "result": f"Cascade failure: dependent data not available from {cascade_failures}",
                            "status": "cascade_error",
                            "tool_id": f"call_{i}_{actual_tool_name}",
                            "orchestration_status": ORCHESTRATION_STATUS_CASCADE_BROKEN,
                        })
                        continue  # Skip execution — result would be meaningless

                # If placeholders still remain after stripping (shouldn't happen),
                # something is structurally wrong – fail the tool call.
                if PlaceholderResolver.has_placeholders(resolved_args):
                    still_unresolved = PlaceholderResolver.PLACEHOLDER_PATTERN.findall(json.dumps(resolved_args))
                    log.error(f"❌ Could not strip all placeholders in {actual_tool_name}: {still_unresolved}")

                    # Check if this is due to empty search results - provide helpful error
                    error_msg = f"Error: Unresolved placeholders: {', '.join(set(still_unresolved))}"
                    for placeholder in still_unresolved:
                        # Check if placeholder references a search that returned empty
                        if "search" in placeholder.lower() and "results[0]" in placeholder:
                            for tn in results_by_tool:
                                if tn in placeholder or placeholder.startswith(tn.split('.')[-1]):
                                    tool_data = results_by_tool[tn]
                                    if isinstance(tool_data, dict) and "data" in tool_data:
                                        data = tool_data["data"]
                                        if isinstance(data, dict) and "results" in data:
                                            results = data["results"]
                                            if isinstance(results, list) and len(results) == 0:
                                                error_msg += f" (Search '{tn}' returned empty results - check conversation history for page_id instead)"
                                                break

                    tool_results.append({
                        "tool_name": actual_tool_name,
                        "result": error_msg,
                        "status": "error",
                        "tool_id": f"call_{i}_{actual_tool_name}"
                    })
                    continue

            # Stream detailed status with context
            tool_display_name = actual_tool_name.replace("_", " ").title()
            # Extract meaningful info from tool name
            if "retrieval" in actual_tool_name.lower():
                status_msg = "Searching knowledge base for relevant information..."
            elif "confluence" in actual_tool_name.lower():
                if "create" in actual_tool_name.lower():
                    status_msg = "Creating Confluence page..."
                elif "update" in actual_tool_name.lower():
                    status_msg = "Updating Confluence page..."
                elif "get" in actual_tool_name.lower() or "search" in actual_tool_name.lower():
                    status_msg = "Retrieving Confluence content..."
                else:
                    status_msg = f"Working with Confluence: {tool_display_name}..."
            elif "jira" in actual_tool_name.lower():
                if "create" in actual_tool_name.lower():
                    status_msg = "Creating Jira issue..."
                elif "update" in actual_tool_name.lower():
                    status_msg = "Updating Jira issue..."
                elif "search" in actual_tool_name.lower() or "get" in actual_tool_name.lower():
                    status_msg = "Searching Jira for information..."
                else:
                    status_msg = f"Working with Jira: {tool_display_name}..."
            else:
                status_msg = f"Executing {tool_display_name}..."

            safe_stream_write(writer, {
                "event": "status",
                "data": {"status": "executing", "message": status_msg}
            }, config)

            log.info(
                "🛠️ Tool call [%d/%d]: %s | args=%s",
                i + 1,
                len(planned_tools),
                actual_tool_name,
                ToolExecutor._format_args_preview(resolved_args),
            )

            # Execute tool
            result_dict = await ToolExecutor._execute_single_tool(
                tool=tools_by_name[actual_tool_name],
                tool_name=actual_tool_name,
                tool_args=resolved_args,
                tool_id=f"call_{i}_{actual_tool_name}",
                state=state,
                log=log
            )

            tool_results.append(result_dict)

            # Emit ask_user_question event immediately when the tool result is
            # ready so the frontend receives it before any answer_chunk tokens
            # and can decide whether to suppress the LLM text response.
            if actual_tool_name in _ASK_USER_QUESTION_TOOL_NAMES:
                client_name = (config.get("configurable") or {}).get("client_name")
                if client_name:
                    raw_result = result_dict.get("result", "")
                    try:
                        payload = json.loads(raw_result) if isinstance(raw_result, str) else raw_result
                    except (json.JSONDecodeError, TypeError):
                        payload = raw_result
                    safe_stream_write(writer, {
                        "event": "ask_user_question",
                        "data": {
                            "status": result_dict.get("status"),
                            "toolData": payload,
                        },
                    }, config)
                    state["ask_user_question_emitted"] = True

            log.info(
                "📌 Tool status [%d/%d]: %s | status=%s | duration_ms=%.0f",
                i + 1,
                len(planned_tools),
                actual_tool_name,
                result_dict.get("status", "unknown"),
                float(result_dict.get("duration_ms", 0.0)),
            )

            # Send completion status
            if result_dict.get("status") == "success":
                if "retrieval" in actual_tool_name.lower():
                    completion_msg = "Knowledge base search completed"
                elif "confluence" in actual_tool_name.lower():
                    if "create" in actual_tool_name.lower():
                        completion_msg = "Confluence page created successfully"
                    elif "update" in actual_tool_name.lower():
                        completion_msg = "Confluence page updated successfully"
                    else:
                        completion_msg = "Confluence operation completed"
                elif "jira" in actual_tool_name.lower():
                    if "create" in actual_tool_name.lower():
                        completion_msg = "Jira issue created successfully"
                    elif "update" in actual_tool_name.lower():
                        completion_msg = "Jira issue updated successfully"
                    else:
                        completion_msg = "Jira operation completed"
                else:
                    completion_msg = f"{actual_tool_name.replace('_', ' ').title()} completed"

                safe_stream_write(writer, {
                    "event": "status",
                    "data": {"status": "executing", "message": completion_msg}
                }, config)
            else:
                # Tool failed - send error status
                error_msg = result_dict.get("error", "Unknown error")
                safe_stream_write(writer, {
                    "event": "status",
                    "data": {"status": "error", "message": f"Operation failed: {error_msg[:100]}"}
                }, config)

            # Set default orchestration status
            if "orchestration_status" not in result_dict:
                result_dict["orchestration_status"] = ORCHESTRATION_STATUS_RESOLVED

            # Store successful results for next placeholder resolution
            if result_dict.get("status") == "success":
                # Extract clean data for placeholder resolution
                result_data = ToolResultExtractor.extract_data_from_result(
                    result_dict.get("result")
                )

                # Track tool invocation count for multiple calls to the same tool
                if actual_tool_name not in tool_invocation_counts:
                    tool_invocation_counts[actual_tool_name] = 0
                    # Store first invocation without suffix
                    results_by_tool[actual_tool_name] = result_data
                    log.debug(f"✅ Stored result for {actual_tool_name} (keys: {list(result_data.keys()) if isinstance(result_data, dict) else type(result_data).__name__})")
                else:
                    # For subsequent invocations, store with suffix
                    tool_invocation_counts[actual_tool_name] += 1
                    suffix_number = tool_invocation_counts[actual_tool_name] + 1
                    storage_key = f"{actual_tool_name}_{suffix_number}"
                    results_by_tool[storage_key] = result_data
                    log.debug(f"✅ Stored result for {storage_key} (keys: {list(result_data.keys()) if isinstance(result_data, dict) else type(result_data).__name__})")

                # Detect empty cascade sources: if this tool returned empty
                # results and a downstream tool depends on its output via
                # placeholder, mark as a broken cascade source.
                if _is_semantically_empty(result_data):
                    dotted_name = tool_call.get("name", "")
                    remaining_tools = planned_tools[i + 1:]
                    for downstream in remaining_tools:
                        args_str = json.dumps(downstream.get("args", {}), default=str)
                        if actual_tool_name in args_str or dotted_name in args_str:
                            log.warning(
                                f"SEMANTIC FAILURE: {actual_tool_name} returned empty "
                                f"results but downstream tool depends on its output"
                            )
                            result_dict["orchestration_status"] = "empty_cascade_source"
                            break
            else:
                log.debug(f"❌ Skipped storing failed tool: {actual_tool_name}")

        return tool_results

    @staticmethod
    async def _execute_parallel(
        planned_tools: list[dict[str, Any]],
        tools_by_name: dict[str, Any],
        llm: BaseChatModel,
        state: ChatState,
        log: logging.Logger
    ) -> list[dict[str, Any]]:
        """Execute tools in parallel"""
        from app.modules.agents.qna.tool_system import _sanitize_tool_name_if_needed

        tasks = []

        for i, tool_call in enumerate(planned_tools[:NodeConfig.MAX_PARALLEL_TOOLS]):
            tool_name = tool_call.get("name", "")
            tool_args = tool_call.get("args", {})

            # Resolve tool name: same 2-step strategy as sequential executor.
            actual_tool_name = None
            if tool_name in tools_by_name:
                actual_tool_name = tool_name
            else:
                normalized_name = _sanitize_tool_name_if_needed(tool_name, llm, state) if llm else tool_name
                if normalized_name in tools_by_name:
                    actual_tool_name = normalized_name

            if actual_tool_name is None:
                log.warning(f"❌ Tool not found: {tool_name}")
                # Create error result directly
                tasks.append(asyncio.create_task(asyncio.sleep(0, result={
                    "tool_name": tool_name,
                    "result": f"Error: Tool '{tool_name}' not found",
                    "status": "error",
                    "tool_id": f"call_{i}_{tool_name}"
                })))
                continue

            log.info(
                "🛠️ Parallel tool queued [%d/%d]: %s | args=%s",
                i + 1,
                min(len(planned_tools), NodeConfig.MAX_PARALLEL_TOOLS),
                actual_tool_name,
                ToolExecutor._format_args_preview(tool_args),
            )

            # Execute tool directly (content generation happens in planner)
            tasks.append(
                ToolExecutor._execute_single_tool(
                    tool=tools_by_name[actual_tool_name],
                    tool_name=actual_tool_name,
                    tool_args=tool_args,
                    tool_id=f"call_{i}_{actual_tool_name}",
                    state=state,
                    log=log
                )
            )

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Filter and process results
        tool_results = []
        for result in results:
            if isinstance(result, Exception):
                log.error(f"❌ Tool execution exception: {result}")
                continue
            if isinstance(result, dict):
                tool_results.append(result)
                log.info(
                    "📌 Parallel tool status: %s | status=%s | duration_ms=%.0f",
                    result.get("tool_name", "unknown"),
                    result.get("status", "unknown"),
                    float(result.get("duration_ms", 0.0)),
                )

        return tool_results

    @staticmethod
    async def _execute_single_tool(
        tool: object,
        tool_name: str,
        tool_args: dict[str, Any],
        tool_id: str,
        state: ChatState,
        log: logging.Logger
    ) -> dict[str, Any]:
        """
        Execute a single tool with proper timeout and error handling.

        Returns:
            Dict with: tool_name, result, status, tool_id, args, duration_ms
        """
        start_time = time.perf_counter()

        try:
            # Normalize args
            if isinstance(tool_args, dict) and "kwargs" in tool_args and len(tool_args) == 1:
                tool_args = tool_args["kwargs"]

            log.debug(f"⚙️ Executing {tool_name} with args: {json.dumps(tool_args, default=str)[:150]}...")

            # Validate args using Pydantic schema
            validated_args, validation_error_detail = await ToolExecutor._validate_and_normalize_args(
                tool, tool_name, tool_args, log
            )

            if validated_args is None:
                # Validation failed - error already logged; surface detail for LLM self-correction
                duration_ms = (time.perf_counter() - start_time) * 1000
                detail = (validation_error_detail or "Argument validation failed").strip()
                result_msg = f"Error: Invalid tool arguments for {tool_name}. {detail}"
                if len(result_msg) > _MAX_TOOL_VALIDATION_RESULT_CHARS:
                    result_msg = (
                        result_msg[:_MAX_TOOL_VALIDATION_RESULT_CHARS] + "...(truncated)"
                    )
                return {
                    "tool_name": tool_name,
                    "result": result_msg,
                    "status": "error",
                    "tool_id": tool_id,
                    "args": tool_args,
                    "duration_ms": duration_ms
                }

            # Determine timeout based on tool type
            timeout = NodeConfig.TOOL_TIMEOUT_SECONDS
            tool_name_lower = tool_name.lower()
            if "retrieval" in tool_name_lower:
                timeout = NodeConfig.RETRIEVAL_TIMEOUT_SECONDS
            elif "image_generator" in tool_name_lower or "generate_image" in tool_name_lower:
                timeout = NodeConfig.IMAGE_GENERATION_TIMEOUT_SECONDS

            # Execute tool
            result = await asyncio.wait_for(
                ToolExecutor._run_tool(tool, validated_args),
                timeout=timeout
            )

            # Process result
            success = ToolResultExtractor.extract_success_status(result)

            # Handle retrieval output
            if "retrieval" in tool_name.lower():
                content = ToolExecutor._process_retrieval_output(result, state, log)
                if content :
                    success = True
            else:
                content = clean_tool_result(result)

            duration_ms = (time.perf_counter() - start_time) * 1000
            status = "success" if success else "error"

            log.info(f"{'✅' if success else '❌'} {tool_name}: {duration_ms:.0f}ms")

            return {
                "tool_name": tool_name,
                "result": content,
                "status": status,
                "tool_id": tool_id,
                "args": tool_args,
                "duration_ms": duration_ms
            }

        except asyncio.TimeoutError:
            duration_ms = (time.perf_counter() - start_time) * 1000
            error_msg = f"Timeout after {duration_ms:.0f}ms"
            if "retrieval" in tool_name.lower():
                error_msg = "Search timed out - query may be too complex. Try simpler query."

            log.error(f"⏱️ {tool_name} timed out after {duration_ms:.0f}ms")
            return {
                "tool_name": tool_name,
                "result": f"Error: {error_msg}",
                "status": "error",
                "tool_id": tool_id,
                "args": tool_args,
                "duration_ms": duration_ms
            }

        except Exception as e:
            duration_ms = (time.perf_counter() - start_time) * 1000
            log.error(f"💥 {tool_name} failed: {e}", exc_info=True)
            return {
                "tool_name": tool_name,
                "result": f"Error: {type(e).__name__}: {str(e)}",
                "status": "error",
                "tool_id": tool_id,
                "args": tool_args,
                "duration_ms": duration_ms
            }

    @staticmethod
    def _format_validation_errors_for_llm(exc: ValidationError) -> str:
        """Serialize Pydantic errors for inclusion in tool results (truncated)."""
        try:
            raw = json.dumps(exc.errors(), default=str)
        except Exception:
            raw = str(exc)
        if len(raw) > _MAX_VALIDATION_ERROR_LLM_CHARS:
            return raw[:_MAX_VALIDATION_ERROR_LLM_CHARS] + "...(truncated)"
        return raw

    @staticmethod
    async def _validate_and_normalize_args(
        tool: object,
        tool_name: str,
        tool_args: dict[str, Any],
        log: logging.Logger
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Validate and normalize tool args using Pydantic schema.

        Returns:
            ``(normalized_args, None)`` on success, or when the tool has no ``args_schema``
            (first element is the dict to pass to the tool).
            ``(None, detail)`` on failure; ``detail`` is safe to show to the LLM in a tool result.
        """
        try:
            # Get schema
            args_schema = getattr(tool, 'args_schema', None)
            if not args_schema:
                return tool_args, None  # No validation available

            # Validate
            validated_model = args_schema.model_validate(tool_args)
            validated_args = validated_model.model_dump(exclude_unset=True)

            log.debug(f"✅ Validated args for {tool_name}")
            return validated_args, None

        except ValidationError as e:
            log.error(f"❌ Validation failed for {tool_name}: {e}")
            return None, ToolExecutor._format_validation_errors_for_llm(e)

        except Exception as e:
            log.error(f"❌ Validation failed for {tool_name}: {e}")
            detail = f"{type(e).__name__}: {str(e)}"
            if len(detail) > _MAX_VALIDATION_ERROR_LLM_CHARS:
                detail = detail[:_MAX_VALIDATION_ERROR_LLM_CHARS] + "...(truncated)"
            return None, detail

    @staticmethod
    async def _run_tool(tool: object, args: dict[str, Any]) -> dict[str, Any] | str | tuple[bool, str] | list[Any] | None:
        """Run tool using appropriate method - all tools run in the same event loop as FastAPI"""
        if hasattr(tool, 'arun'):
            # Tool has async arun() - use it directly (no thread executor)
            return await tool.arun(args)
        elif hasattr(tool, '_run'):
            # Sync _run() - call directly (shouldn't happen if tools are properly async)
            # This is a fallback for backwards compatibility
            return tool._run(**args)
        else:
            # Fallback to run() method
            return tool.run(**args)

    @staticmethod
    def _process_retrieval_output(result: dict[str, Any] | str | tuple[bool, str] | list[Any] | None, state: ChatState, log: logging.Logger) -> str:
        """Process retrieval tool output and update state (accumulates results from multiple retrieval calls)"""
        try:
            # Fast path: tool already wrote to state and returned pre-formatted content
            if isinstance(result, str) and "<record>" in result:
                log.info("📚 Retrieval returned pre-formatted content (state already updated by tool)")
                return result

            from app.agents.actions.retrieval.retrieval import RetrievalToolOutput

            # Legacy/fallback path: parse JSON and extract data
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
                # Accumulate final_results instead of overwriting (for parallel retrieval calls)
                existing_final_results = state.get("final_results", [])
                if not isinstance(existing_final_results, list):
                    existing_final_results = []

                # Combine new results with existing ones
                new_final_results = retrieval_output.final_results or []
                combined_final_results = existing_final_results + new_final_results
                state["final_results"] = combined_final_results

                # Accumulate virtual_record_id_to_result
                existing_virtual_map = state.get("virtual_record_id_to_result", {})
                if not isinstance(existing_virtual_map, dict):
                    existing_virtual_map = {}

                new_virtual_map = retrieval_output.virtual_record_id_to_result or {}
                combined_virtual_map = {**existing_virtual_map, **new_virtual_map}
                state["virtual_record_id_to_result"] = combined_virtual_map

                # Accumulate tool_records
                if retrieval_output.virtual_record_id_to_result:
                    existing_tool_records = state.get("tool_records", [])
                    if not isinstance(existing_tool_records, list):
                        existing_tool_records = []

                    new_tool_records = list(retrieval_output.virtual_record_id_to_result.values())
                    # Avoid duplicates by checking record IDs
                    existing_record_ids = {rec.get("_id") for rec in existing_tool_records if isinstance(rec, dict) and "_id" in rec}
                    unique_new_records = [
                        rec for rec in new_tool_records
                        if not (isinstance(rec, dict) and rec.get("_id") in existing_record_ids)
                    ]
                    combined_tool_records = existing_tool_records + unique_new_records
                    state["tool_records"] = combined_tool_records

                log.info(f"📚 Retrieved {len(new_final_results)} knowledge blocks (total: {len(combined_final_results)})")
                return retrieval_output.content

        except Exception as e:
            log.warning(f"⚠️ Could not process retrieval output: {e}")

        return str(result)


# ============================================================================
# PART 2: PLANNER NODE + REFLECTION + HELPER FUNCTIONS
# ============================================================================

# ============================================================================
# PLANNER PROMPTS - IMPROVED FOR ACCURACY
# ============================================================================



# ============================================================================
# JIRA GUIDANCE - CONDENSED
# ============================================================================




# ============================================================================
# REFLECTION PROMPT - IMPROVED DECISION MAKING
# ============================================================================



# ============================================================================
# PLANNER NODE - IMPROVED ACCURACY
# ============================================================================

async def planner_node(
    state: ChatState,
    config: RunnableConfig,
    writer: StreamWriter
) -> ChatState:
    """
    LLM-driven planner with improved accuracy and error handling.

    Features:
    - Smart tool validation with retry
    - Better prompt construction
    - Cascading tool support
    - Context-aware planning
    """
    start_time = time.perf_counter()
    log = state.get("logger", logger)
    llm = state.get("llm")
    query = state.get("query", "")

    # Send initial planning status
    safe_stream_write(writer, {
        "event": "status",
        "data": {"status": "planning", "message": "Analyzing your request and planning actions..."}
    }, config)

    # Build system prompt with tool descriptions
    tool_descriptions = _get_cached_tool_descriptions(state, log)
    jira_guidance = JIRA_GUIDANCE if _has_jira_tools(state) else ""
    confluence_guidance = CONFLUENCE_GUIDANCE if _has_confluence_tools(state) else ""
    slack_guidance = SLACK_GUIDANCE if _has_slack_tools(state) else ""
    onedrive_guidance = ONEDRIVE_GUIDANCE if _has_onedrive_tools(state) else ""
    outlook_guidance = OUTLOOK_GUIDANCE if _has_outlook_tools(state) else ""
    teams_guidance = TEAMS_GUIDANCE if _has_teams_tools(state) else ""
    github_guidance = GITHUB_GUIDANCE if _has_github_tools(state) else ""
    clickup_guidance = CLICKUP_GUIDANCE if _has_clickup_tools(state) else ""
    mariadb_guidance = MARIADB_GUIDANCE if _has_mariadb_tools(state) else ""
    redshift_guidance = REDSHIFT_GUIDANCE if _has_redshift_tools(state) else ""
    zoom_guidance = ZOOM_GUIDANCE if _has_zoom_tools(state) else ""
    salesforce_guidance = SALESFORCE_GUIDANCE if _has_salesforce_tools(state) else ""
    sharepoint_guidance = SHAREPOINT_GUIDANCE if _has_sharepoint_tools(state) else ""
    has_web_search = bool(state.get("web_search_config"))
    web_search_decision_rule = (
        "7. **User references an EXTERNAL WEBSITE or URL?** (mentions a public website by name — "
        "Wikipedia, Stack Overflow, GitHub docs, MDN, Medium, Reddit, any \"*.com/org/io\" domain, "
        "or includes a URL like \"https://...\") → Use `fetch_url` (if a specific URL is given) "
        "or `web_search` (to find the page first)."
    ) if has_web_search else ""
    web_search_tools_guidance = (
        "**Use WEB TOOLS (`web_search` / `fetch_url`) when:**\n"
        "- User mentions a **specific external/public website** by name (Wikipedia, Stack Overflow, GitHub, MDN, Reddit, Medium, any public site)\n"
        "- User provides or references a **URL** (https://...)\n"
        "- User asks to summarize, read, or get content from an **external page/site/article**\n"
        "- User wants information that is explicitly **from the web** or **from a specific public source**\n"
        "- Query is about **general/public knowledge** unlikely to exist in internal org documents — "
        "e.g. product reviews, consumer recommendations, health/medical info, market comparisons, "
        "\"best X\", travel, recipes, public news, scientific research, technology comparisons\n"
        "- Query asks for **recommendations, rankings, or comparisons** of products, services, or brands "
        "(e.g. \"best probiotic supplement\", \"top laptops 2026\", \"cheapest flight to X\")\n"
        "- Query requires **current/real-time data** — prices, availability, weather, sports scores, stock prices, release dates\n"
        "\n"
        "**⚠️ IMPORTANT — web_search + retrieval in parallel:** When a query could have BOTH internal AND external relevance, "
        "plan BOTH `retrieval.search_internal_knowledge` AND `web_search` in parallel.\n"
    ) if has_web_search else ""

    system_prompt = PLANNER_SYSTEM_PROMPT.format(
        available_tools=tool_descriptions,
        jira_guidance=jira_guidance,
        confluence_guidance=confluence_guidance,
        slack_guidance=slack_guidance,
        onedrive_guidance=onedrive_guidance,
        outlook_guidance=outlook_guidance,
        teams_guidance=teams_guidance,
        github_guidance=github_guidance,
        clickup_guidance=clickup_guidance,
        mariadb_guidance=mariadb_guidance,
        redshift_guidance=redshift_guidance,
        zoom_guidance=zoom_guidance,
        salesforce_guidance=salesforce_guidance,
        sharepoint_guidance=sharepoint_guidance,
        web_search_decision_rule=web_search_decision_rule,
        web_search_tools_guidance=web_search_tools_guidance,
    )

    # Add capability summary so LLM can answer "what can you do?" questions
    capability_summary = build_capability_summary(state)
    system_prompt += f"\n\n{capability_summary}"

    # If no knowledge sources are configured, explicitly tell the LLM not to use retrieval
    agent_tools = state.get("tools", []) or []
    has_user_tools = bool(agent_tools)
    has_knowledge = state.get("has_knowledge", False)

    if not has_knowledge:
        web_search_note = ""
        if has_web_search:
            web_search_note = (
                "- ✅ **`web_search` and `fetch_url` ARE available.**\n"
                "- ✅ Prefer `web_search` over your training data for anything that may have changed: "
                "news, prices, weather, sports, stocks, software versions, docs, regulations, current events, etc.\n"
                "- ✅ Also prefer `web_search` when user asks for \"latest\", \"current\", or \"up-to-date\" info.\n"
                "- ✅ Use training data only for timeless knowledge (math, science, core programming concepts).\n"
                "- ✅ When in doubt, prefer `web_search` over answering from training data.\n"
                "- ✅ Use `fetch_url` to read a specific URL or to get full content from a `web_search` result link.\n"
                "- ✅ **Cascading:** `web_search` → `fetch_url` via `{{web_search.web_results[0].link}}`.\n"
            )

        if not has_user_tools:
            no_retrieval_note = (
                "\n\n## ⚠️ CRITICAL: This Agent Has No Knowledge Base and No Service Tools Configured\n"
                "- `retrieval.search_internal_knowledge` is NOT available (no knowledge sources configured).\n"
                "- There are NO connected service tools available beyond the built-in calculator.\n"
                "- ❌ NEVER plan `retrieval.search_internal_knowledge` or any service tool calls.\n"
                "- ❌ NEVER set `needs_clarification: true` for questions about org-specific topics — instead, answer directly and guide the user.\n"
                f"{web_search_note}"
                "- ✅ For conversational or general questions answerable from your training knowledge: set `can_answer_directly: true` and answer.\n"
                "- ✅ For questions about org-specific content (documents, policies, licenses, people, data): set `can_answer_directly: true` and tell the user:\n"
                "  1. This agent currently has no knowledge sources configured.\n"
                "  2. To answer questions from org documents/wikis, the agent admin must add knowledge sources to this agent.\n"
                "  3. To take actions (calendar, email, tickets, etc.), the agent admin must connect service toolsets.\n"
                "- ✅ You may still answer general factual questions from your own training knowledge.\n"
            )
        else:
            # Has service tools but no knowledge base — service tools are the primary search surface.
            # Mirrors the ReAct "Service-Tool Search Strategy" branch in _build_react_system_prompt
            # so quick mode and verification mode behave consistently when no KB is configured.
            # Generic by design: no per-app names — routing is delegated to each tool's own
            # `when_to_use` description, so adding a new connector requires zero prompt changes.
            no_retrieval_note = (
                "\n\n## ⚠️ CRITICAL: No Knowledge Base — Service Tools Are Your Search Surface\n"
                "`retrieval.search_internal_knowledge` is **NOT available** (no knowledge sources configured), "
                "but this agent has live service search tools. Treat those tools as your primary search surface "
                "for ANY topic, information, or org-knowledge query.\n"
                "- ❌ NEVER plan `retrieval.search_internal_knowledge` — it does not exist and will cause an error.\n"
                "- ✅ **For ANY topic / information / org-knowledge query**: plan the matching service search "
                "tool(s) on the FIRST turn — call them in PARALLEL when multiple tools could plausibly contain "
                "the answer. Pick tools by matching the query against each tool's `when_to_use` description in "
                "the Available Tools section.\n"
                "- ❌ NEVER require the user to mention an app by name. A query about org-knowledge is "
                "implicitly a search query — the tool's `when_to_use` description determines applicability, "
                "not whether the user typed the app name.\n"
                "- ❌ NEVER set `can_answer_directly: true` for org-knowledge / topic queries when service "
                "search tools are available — you MUST plan a search first.\n"
                "- ❌ NEVER set `needs_clarification: true` to ask which app/source the user means — search "
                "the available tools first; clarify only if the search results are ambiguous.\n"
                f"{web_search_note}"
                "- ✅ Skip search ONLY for: pure greetings, simple arithmetic / date calculations, the user's "
                "own identity / profile, or write actions where you already have all required parameters — "
                "those may set `can_answer_directly: true`.\n"
                "- ✅ If after planning a search across all relevant tools the results come back empty, the "
                "response stage will tell the user — do NOT pre-empt that here by setting `can_answer_directly: true`.\n"
            )
        system_prompt += no_retrieval_note

    if has_web_search and has_knowledge:
        system_prompt += (
            "\n\n## Web Search vs Internal Knowledge\n"
            "- **`web_search`**: Prefer for current/changing info — news, prices, weather, software versions, latest docs, regulations, current events. "
            "Also when user asks for \"latest\"/\"current\"/\"up-to-date\" info.\n"
            "- **`web_search`**: ALSO use for general/public knowledge queries — product recommendations, comparisons, reviews, "
            "health/medical info, consumer advice, market research, scientific topics, travel, recipes, \"best X\" queries. "
            "- **Internal knowledge**: Prefer for org-specific documents, policies, company data, internal wikis.\n"
            "- **Both in parallel**: When the query could have both internal AND external relevance, plan BOTH "
            "`retrieval.search_internal_knowledge` AND `web_search`.\n"
            "- **Training data**: Only for timeless knowledge (math, science, core concepts). When in doubt, prefer `web_search`.\n"
            "- **Cascading:** `web_search` → `fetch_url` via `{{web_search.web_results[0].link}}`.\n"
        )

    # Inject knowledge context so the LLM knows what is indexed vs. what is live API
    knowledge_context = _build_knowledge_context(state, log)
    if knowledge_context:
        system_prompt = system_prompt + knowledge_context

    persona = state.get("system_prompt")
    if is_custom_agent_system_prompt(persona):
        system_prompt = f"{persona.strip()}\n\n{system_prompt}"

    # Prepend agent instructions if provided
    instructions = state.get("instructions")
    if instructions and instructions.strip():
        system_prompt = f"## Agent Instructions\n{instructions.strip()}\n\n{system_prompt}"

    # Add timezone / current time context if provided
    time_block = build_llm_time_context(
        current_time=state.get("current_time"),
        time_zone=state.get("timezone"),
    )
    if time_block:
        system_prompt = f"{system_prompt}\n\n{time_block}"

    # Build messages with conversation context (using LangChain message format for better context awareness)
    messages = await _build_planner_messages(state, query, log, from_planner=True)

    # Add retry/continue context if needed
    if state.get("is_retry"):
        retry_context = _build_retry_context(state)
        # Prepend retry context to the last HumanMessage
        if messages and isinstance(messages[-1], HumanMessage):
            existing = messages[-1].content
            if isinstance(existing, list):
                messages[-1].content = existing + [{"type": "text", "text": "\n\n" + retry_context}]
            else:
                messages[-1].content = existing + "\n\n" + retry_context
        else:
            messages.append(HumanMessage(content=retry_context))
        state["is_retry"] = False
        log.info("🔄 Retry mode active")

    if state.get("is_continue"):
        continue_context = _build_continue_context(state, log)
        # Prepend continue context to the last HumanMessage
        if messages and isinstance(messages[-1], HumanMessage):
            existing = messages[-1].content
            if isinstance(existing, list):
                messages[-1].content = existing + [{"type": "text", "text": "\n\n" + continue_context}]
            else:
                messages[-1].content = existing + "\n\n" + continue_context
        else:
            messages.append(HumanMessage(content=continue_context))
        state["is_continue"] = False

        # Send informative continue mode status
        iteration_count = state.get("iteration_count", 0)
        max_iterations = state.get("max_iterations", NodeConfig.MAX_ITERATIONS)
        executed_tools = state.get("executed_tool_names", [])

        if executed_tools:
            last_tool = executed_tools[-1] if executed_tools else "previous steps"
            if "retrieval" in last_tool.lower():
                action_desc = "gathered information"
            elif "create" in last_tool.lower():
                action_desc = "created resources"
            elif "update" in last_tool.lower():
                action_desc = "updated resources"
            elif "search" in last_tool.lower() or "get" in last_tool.lower():
                action_desc = "retrieved information"
            else:
                action_desc = "completed previous steps"

            status_msg = f"Step {iteration_count + 1}/{max_iterations}: Planning next actions after we {action_desc}..."
        else:
            status_msg = f"Step {iteration_count + 1}/{max_iterations}: Planning next steps to complete your request..."

        safe_stream_write(writer, {
            "event": "status",
            "data": {"status": "planning", "message": status_msg}
        }, config)

        log.info("➡️ Continue mode active")

    # Resolve attachments once (first LLM node) and inject into the query message
    attachment_blocks = await _ensure_attachment_blocks(state, log)
    _inject_attachment_blocks(messages, attachment_blocks)

    # Plan with validation retry loop
    plan = await _plan_with_validation_retry(
        llm, system_prompt, messages, state, log, query, writer, config
    )

    # Post-processing: if the agent has NO user tools AND NO knowledge:
    # 1. If the plan still set needs_clarification (despite the prompt), override it.
    # 2. Set agent_not_configured_hint so _generate_direct_response knows to guide
    #    the user to configure the agent when they ask org-specific questions.
    #    Skip the hint when web_search is available and the planner selected tools
    #    (the agent can meaningfully answer via web search).
    if not has_user_tools and not has_knowledge:
        if plan.get("needs_clarification") and not plan.get("can_answer_directly") and not plan.get("tools"):
            log.info("🔧 No tools/knowledge configured — overriding clarification with agent setup guidance")
            plan["needs_clarification"] = False
            plan["can_answer_directly"] = True
            plan["tools"] = []
        planned_tools = plan.get("tools", [])
        if not (has_web_search and planned_tools):
            state["agent_not_configured_hint"] = True

    # Store plan in state
    state["execution_plan"] = plan
    state["planned_tool_calls"] = plan.get("tools", [])
    state["pending_tool_calls"] = bool(plan.get("tools"))
    state["query_analysis"] = {
        "intent": plan.get("intent", ""),
        "reasoning": plan.get("reasoning", ""),
        "can_answer_directly": plan.get("can_answer_directly", False),
    }

    # Handle clarification request
    if plan.get("needs_clarification"):
        state["reflection_decision"] = "respond_clarify"
        state["reflection"] = {
            "decision": "respond_clarify",
            "reasoning": "Planner needs clarification",
            "clarifying_question": plan.get("clarifying_question", "Could you provide more details?")
        }
        log.info(f"❓ Requesting clarification: {plan.get('clarifying_question', '')[:50]}...")

    duration_ms = (time.perf_counter() - start_time) * 1000
    log.info(f"⚡ Planner: {duration_ms:.0f}ms - {len(plan.get('tools', []))} tools")

    return state

async def _build_conversation_messages(
    conversations: list[dict],
    log: logging.Logger,
    *,
    is_multimodal_llm: bool = False,
    blob_store: Any = None,
    org_id: str = "",
    ref_mapper: Any = None,
    out_records: dict[str, dict] | None = None,
) -> list[HumanMessage | AIMessage]:
    """Convert conversation history to LangChain messages with sliding window

    Uses a sliding window of MAX_CONVERSATION_HISTORY user+bot pairs (40 messages total),
    but ALWAYS includes ALL reference data from the entire conversation history.

    When *is_multimodal_llm* is True, image attachments on previous user_query
    messages are fetched from blob storage and included as ``image_url`` content
    blocks alongside the text, preserving chronological order.

    PDF attachments on previous user_query messages are resolved from blob
    storage via ``record_to_message_content`` and appended to the same user
    message under an "Attached PDF documents:" label.

    Args:
        conversations: List of conversation dicts with role and content
        log: Logger instance
        is_multimodal_llm: Whether the LLM supports multimodal content
        blob_store: BlobStorage instance for fetching image and PDF attachments
        org_id: Organisation ID for blob storage lookups
        ref_mapper: Shared CitationRefMapper so historical PDF citation IDs
            are consistent with those used for retrieval results and current
            attachments.  Pass ``state["citation_ref_mapper"]``; a fresh one
            is created if not provided.
        out_records: If provided, historical PDF records fetched from blob
            storage are stored here keyed by virtualRecordId so callers can
            populate ``virtual_record_id_to_result`` for citation resolution.

    Returns:
        List of HumanMessage and AIMessage objects
    """
    if not conversations:
        return []

    messages = []
    all_reference_data = []

    # First pass: Collect ALL reference data from entire history (no limit)
    for conv in conversations:
        if conv.get("role") == "bot_response":
            ref_data = conv.get("referenceData", [])
            if ref_data:
                all_reference_data.extend(ref_data)

    # Second pass: Apply sliding window to conversation messages
    # Count user+bot pairs (each pair = 2 messages)
    user_bot_pairs = []
    current_pair = []

    for conv in conversations:
        role = conv.get("role", "")
        if role == "user_query":
            if current_pair:  # Start new pair
                user_bot_pairs.append(current_pair)
                current_pair = [conv]
            else:
                current_pair = [conv]
        elif role == "bot_response":
            if current_pair:
                current_pair.append(conv)
                user_bot_pairs.append(current_pair)
                current_pair = []
            else:
                # Bot response without user query (shouldn't happen, but handle it)
                user_bot_pairs.append([conv])

    # Add any remaining pair
    if current_pair:
        user_bot_pairs.append(current_pair)

    # Apply sliding window: keep last MAX_CONVERSATION_HISTORY pairs
    if len(user_bot_pairs) > MAX_CONVERSATION_HISTORY:
        user_bot_pairs = user_bot_pairs[-MAX_CONVERSATION_HISTORY:]
        log.debug(f"Applied sliding window: kept last {MAX_CONVERSATION_HISTORY} user+bot pairs from {len(conversations)} total conversations")
    else:
        log.debug(f"Using all {len(user_bot_pairs)} user+bot pairs (within limit of {MAX_CONVERSATION_HISTORY})")

    # Convert pairs to messages
    for pair in user_bot_pairs:
        for conv in pair:
            role = conv.get("role", "")
            content = conv.get("content", "")

            if role == "user_query":
                attachments = conv.get("attachments") or []
                if is_multimodal_llm and attachments and blob_store and org_id:
                    from app.utils.chat_helpers import build_multimodal_user_content
                    content = await build_multimodal_user_content(
                        content, attachments, blob_store, org_id,
                    )
                _DOC_MIME_TYPES = {"application/pdf", "text/plain", "text/markdown", "text/mdx"}
                doc_attachments = [
                    att for att in attachments
                    if isinstance(att, dict)
                    and (att.get("mimeType") or "").lower() in _DOC_MIME_TYPES
                ]
                if doc_attachments and blob_store and org_id:
                    from app.utils.chat_helpers import record_to_message_content, CitationRefMapper
                    if ref_mapper is None:
                        ref_mapper = CitationRefMapper()
                    doc_blocks: list = []
                    for att in doc_attachments:
                        vrid = att.get("virtualRecordId") or ""
                        if not vrid:
                            continue
                        try:
                            record = await blob_store.get_record_from_storage(vrid, org_id)
                            if not record:
                                continue
                            if out_records is not None and vrid not in out_records:
                                out_records[vrid] = record
                            blocks, ref_mapper = record_to_message_content(record, ref_mapper=ref_mapper, is_multimodal_llm=is_multimodal_llm)
                            doc_blocks.extend(blocks)
                        except Exception as exc:
                            log.warning("Failed to resolve historical attachment vrid=%s: %s", vrid, exc)
                    if doc_blocks:
                        parts: list = list(content) if isinstance(content, list) else (
                            [{"type": "text", "text": content}] if content else []
                        )
                        parts.append({"type": "text", "text": "Attached documents:"})
                        parts.extend(doc_blocks)
                        content = parts
                messages.append(HumanMessage(content=content))
            elif role == "bot_response":
                messages.append(AIMessage(content=content))

    # ALWAYS add ALL reference data (from entire history, not just window)
    if all_reference_data:
        ref_data_text = format_reference_data(
            all_reference_data,
            header="## Reference Data (use these IDs/keys directly - do NOT fetch them again):",
            log=log,
        )
        # Append reference data to the last AI message if exists, otherwise create a new message
        if messages and isinstance(messages[-1], AIMessage):
            existing = messages[-1].content
            if isinstance(existing, list):
                messages[-1].content = existing + [{"type": "text", "text": "\n\n" + ref_data_text}]
            else:
                messages[-1].content = existing + "\n\n" + ref_data_text
        else:
            messages.append(AIMessage(content=ref_data_text))
        log.debug(f"📎 Included {len(all_reference_data)} reference items from entire conversation history")

    return messages





async def _build_planner_messages(state: ChatState, query: str, log: logging.Logger,from_planner: bool = False) -> list[HumanMessage | AIMessage | SystemMessage]:
    """Build LangChain messages for planner with conversation context - using message format for better context awareness

    Returns:
        List of messages: [SystemMessage (optional), ...conversation messages..., HumanMessage (current query + context)]
    """
    previous_conversations = state.get("previous_conversations", [])
    messages = []

    # Convert conversation history to LangChain messages (with sliding window)
    if previous_conversations:
        _ensure_blob_store(state, log)
        if state.get("citation_ref_mapper") is None:
            from app.utils.chat_helpers import CitationRefMapper
            state["citation_ref_mapper"] = CitationRefMapper()
        out_records = {}
        conversation_messages = await _build_conversation_messages(
            previous_conversations, log,
            is_multimodal_llm=state.get("is_multimodal_llm", False),
            blob_store=state.get("blob_store"),
            org_id=state.get("org_id", ""),
            ref_mapper=state.get("citation_ref_mapper"),
            out_records=out_records,
        )
        messages.extend(conversation_messages)
        log.debug(f"Using {len(conversation_messages)} messages from {len(previous_conversations)} conversations (sliding window applied)")
        if out_records:
            vrmap = state.get("virtual_record_id_to_result")
            if not isinstance(vrmap, dict):
                vrmap = {}
                state["virtual_record_id_to_result"] = vrmap
            for vrid, rec in out_records.items():
                if vrid not in vrmap:
                    vrmap[vrid] = rec

    # Build current query message with explicit planner framing
    user_context = _format_user_context(state)
    parts = [f"## User Query\n{query}"]
    if user_context:
        parts.append(user_context)
    if from_planner:
        parts.append(
            "## Planner Step\n"
            "This request is being routed through the planning stage. "
            "The expected output for this step is a single JSON object "
            "matching the tool execution plan schema described in the system prompt."
        )
    query_content = "\n\n".join(parts)

    # Add current query as HumanMessage
    messages.append(HumanMessage(content=query_content))

    return messages


def _ensure_blob_store(state: ChatState, log: logging.Logger) -> Any:
    """Ensure ``state["blob_store"]`` is initialised, creating one if needed.

    Returns the BlobStorage instance (may be ``None`` if config/graph
    providers are unavailable, but callers already guard on truthiness).
    """
    blob_store = state.get("blob_store")
    if blob_store is not None:
        return blob_store
    try:
        from app.modules.transformers.blob_storage import BlobStorage
        blob_store = BlobStorage(
            logger=log,
            config_service=state.get("config_service"),
            graph_provider=state.get("graph_provider"),
        )
        state["blob_store"] = blob_store
    except Exception:
        log.debug("Could not initialise BlobStorage for conversation history", exc_info=True)
    return blob_store


async def _ensure_attachment_blocks(state: ChatState, log: logging.Logger) -> list:
    """Lazily resolve user attachments into multimodal content blocks.

    Fetches image data for each attachment and caches the result on
    ``state["resolved_attachment_blocks"]`` so subsequent nodes can reuse
    the blocks without re-fetching.  Returns the list of blocks (may be empty).
    """
    if state.get("resolved_attachment_blocks") is not None:
        return state["resolved_attachment_blocks"]

    raw_attachments = state.get("attachments") or []
    if not raw_attachments:
        state["resolved_attachment_blocks"] = []
        return []

    from app.utils.attachment_utils import resolve_attachments

    try:
        blob_store = _ensure_blob_store(state, log)

        ref_mapper = state.get("citation_ref_mapper")
        if ref_mapper is None:
            from app.utils.chat_helpers import CitationRefMapper
            ref_mapper = CitationRefMapper()
            state["citation_ref_mapper"] = ref_mapper

        attachment_records: dict[str, dict[str, Any]] = {}
        blocks = await resolve_attachments(
            attachments=raw_attachments,
            blob_store=blob_store,
            org_id=state.get("org_id", ""),
            is_multimodal_llm=state.get("is_multimodal_llm", False),
            logger=log,
            ref_mapper=ref_mapper,
            out_records=attachment_records,
        )

        if attachment_records:
            vrmap = state.get("virtual_record_id_to_result")
            if not isinstance(vrmap, dict):
                vrmap = {}
                state["virtual_record_id_to_result"] = vrmap
            for vrid, rec in attachment_records.items():
                if vrid not in vrmap:
                    vrmap[vrid] = rec
    except Exception as exc:
        log.warning("Failed to resolve attachments: %s", exc, exc_info=True)
        blocks = []

    state["resolved_attachment_blocks"] = blocks
    return blocks


def _inject_attachment_blocks(messages: list, attachment_blocks: list) -> None:
    """Mutate the last HumanMessage in *messages* to include attachment blocks.

    Handles both plain-text content (string) and already-multimodal content
    (list of dicts).  No-ops when *attachment_blocks* is empty or the last
    message is not a HumanMessage.
    """
    if not attachment_blocks or not messages:
        return
    last = messages[-1]
    if not isinstance(last, HumanMessage):
        return
    if isinstance(last.content, list):
        last.content.append(
            {"type": "text", "text": "\n\nAttached files from the user:\n"}
        )
        last.content.extend(attachment_blocks)
    else:
        from app.utils.attachment_utils import build_multimodal_content
        text = last.content if isinstance(last.content, str) else str(last.content)
        messages[-1] = HumanMessage(content=build_multimodal_content(text, attachment_blocks))








async def _plan_with_validation_retry(
    llm: BaseChatModel,
    system_prompt: str,
    messages: list[HumanMessage | AIMessage | SystemMessage],
    state: ChatState,
    log: logging.Logger,
    query: str,
    writer: StreamWriter | None = None,
    config: RunnableConfig | None = None
) -> dict[str, Any]:
    """
    Plan with tool validation retry loop.

    Uses raw JSON parsing of the LLM response.
    If planner suggests invalid tools, retry with error message showing available tools.
    """
    validation_retry_count = state.get("tool_validation_retry_count", 0)
    max_retries = NodeConfig.MAX_VALIDATION_RETRIES

    invoke_config = {"callbacks": [_opik_tracer]} if _opik_tracer else {}

    # Always use raw JSON parsing — structured output's additionalProperties
    structured_llm = llm
    using_structured = False
    log.info("🔧 Planner using raw JSON parsing")

    while validation_retry_count <= max_retries:
        try:
            # Build message list: SystemMessage + conversation history + current query
            llm_messages = [SystemMessage(content=system_prompt), *messages]

            # Keepalive prevents SSE timeout during LLM planning call
            keepalive_task = asyncio.create_task(
                send_keepalive(writer, config, "Planning actions...")
            )
            try:
                response = await asyncio.wait_for(
                    structured_llm.ainvoke(llm_messages, config=invoke_config),
                    timeout=NodeConfig.PLANNER_TIMEOUT_SECONDS
                )
            finally:
                keepalive_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await keepalive_task

            # Parse response — structured output returns a Pydantic model or dict;
            # raw LLM returns an AIMessage with text content.
            plan = _parse_planner_response_from_llm(response, log, using_structured)

            # Validate tools
            tools = plan.get('tools', [])

            # Fix empty retrieval queries in fallback plans
            for tool in tools:
                if "retrieval" in tool.get("name", "").lower() and not tool.get("args", {}).get("query", "").strip():
                    tool["args"]["query"] = query
                    log.info(f"🔧 Fixed empty retrieval query with user query: {query[:50]}")

            is_valid, invalid_tools, available_tool_names = _validate_planned_tools(tools, state, log)

            if is_valid or validation_retry_count >= max_retries:
                if not is_valid:
                    log.error(f"⚠️ Invalid tools after {max_retries} retries: {invalid_tools}. Removing them.")
                    plan["tools"] = [t for t in tools if isinstance(t, dict) and t.get('name', '') not in invalid_tools]

                state["tool_validation_retry_count"] = 0
                return plan
            else:
                validation_retry_count += 1
                state["tool_validation_retry_count"] = validation_retry_count
                log.warning(f"⚠️ Invalid tools: {invalid_tools}. Retry {validation_retry_count}/{max_retries}")

                available_list = ", ".join(sorted(available_tool_names)[:MAX_AVAILABLE_TOOLS_DISPLAY])
                if len(available_tool_names) > MAX_AVAILABLE_TOOLS_DISPLAY:
                    available_list += f" (and {len(available_tool_names) - MAX_AVAILABLE_TOOLS_DISPLAY} more)"

                error_message = f"""❌ ERROR: Invalid tools: {', '.join(invalid_tools)}

**Available tools**: {available_list}

Choose tools ONLY from the available list above.

**Original query**: {query}
"""
                if messages and isinstance(messages[-1], HumanMessage):
                    existing = messages[-1].content
                    if isinstance(existing, list):
                        messages[-1].content = existing + [{"type": "text", "text": "\n\n" + error_message}]
                    else:
                        messages[-1].content = existing + "\n\n" + error_message
                else:
                    messages.append(HumanMessage(content=error_message))

        except asyncio.TimeoutError:
            log.warning("⏱️ Planner timeout")
            fallback = _create_fallback_plan(query, state)
            fallback_tools = fallback.get('tools', [])
            is_valid, invalid_tools, _ = _validate_planned_tools(fallback_tools, state, log)
            if not is_valid:
                log.warning(f"⚠️ Fallback tools unavailable: {invalid_tools}. Switching to direct answer.")
                fallback['tools'] = [t for t in fallback_tools if isinstance(t, dict) and t.get('name', '') not in invalid_tools]
                if not fallback['tools']:
                    fallback['can_answer_directly'] = True
            return fallback
        except Exception as e:
            log.error(f"💥 Planner error: {e}")
            fallback = _create_fallback_plan(query, state)
            fallback_tools = fallback.get('tools', [])
            is_valid, invalid_tools, _ = _validate_planned_tools(fallback_tools, state, log)
            if not is_valid:
                log.warning(f"⚠️ Fallback tools unavailable: {invalid_tools}. Switching to direct answer.")
                fallback['tools'] = [t for t in fallback_tools if isinstance(t, dict) and t.get('name', '') not in invalid_tools]
                if not fallback['tools']:
                    fallback['can_answer_directly'] = True
            return fallback

    # Should never reach here
    fallback = _create_fallback_plan(query, state)
    fallback_tools = fallback.get('tools', [])
    is_valid, invalid_tools, _ = _validate_planned_tools(fallback_tools, state, log)
    if not is_valid:
        fallback['tools'] = [t for t in fallback_tools if isinstance(t, dict) and t.get('name', '') not in invalid_tools]
        if not fallback['tools']:
            fallback['can_answer_directly'] = True
    return fallback


def _parse_planner_response_from_llm(response: Any, log: logging.Logger, using_structured: bool) -> dict[str, Any]:
    """Convert an LLM response (raw) into a normalised plan dict.

    Parses JSON from the LLM text response. Also handles the case where
    a provider returns a raw dict.
    """
    # Dict (some providers return a raw dict)
    if isinstance(response, dict):
        plan = dict(response)
        plan.setdefault("intent", "")
        plan.setdefault("reasoning", "")
        plan.setdefault("can_answer_directly", False)
        plan.setdefault("needs_clarification", False)
        plan.setdefault("clarifying_question", "")
        plan.setdefault("tools", [])
        plan["tools"] = [
            {"name": t["name"], "args": t.get("args", {})}
            for t in plan.get("tools", [])
            if isinstance(t, dict) and "name" in t
        ]
        log.info("✅ Planner response parsed via structured output (dict)")
        return plan

    # AIMessage or other object — extract text content and parse JSON
    raw_content = response.content if hasattr(response, 'content') else str(response)
    return _parse_planner_response(
        coerce_message_content_to_text(raw_content),
        log
    )


def _parse_planner_response(content: str, log: logging.Logger) -> dict[str, Any]:
    """Parse planner JSON response with error handling"""
    content = content.strip()

    # Remove markdown code blocks
    if "```json" in content:
        match = re.search(r'```json\s*(.*?)\s*```', content, re.DOTALL)
        if match:
            content = match.group(1)
    elif content.startswith("```"):
        content = re.sub(r'^```\s*\n?', '', content)
        content = re.sub(r'\n?```\s*$', '', content)

    # Handle multiple JSON objects - extract the first complete one
    # Sometimes LLM outputs multiple JSON objects concatenated (e.g., {"a":1}\n{"b":2})
    if content.count('{') > 1:
        # Try to find the first complete JSON object
        brace_count = 0
        start_idx = -1
        found_valid = False
        for i, char in enumerate(content):
            if char == '{':
                if brace_count == 0:
                    start_idx = i
                brace_count += 1
            elif char == '}':
                brace_count -= 1
                if brace_count == 0 and start_idx >= 0:
                    # Found a complete JSON object
                    try:
                        json_str = content[start_idx:i+1]
                        test_plan = json.loads(json_str)
                        if isinstance(test_plan, dict) and (
                            # Prefer objects with "tools" field, but accept any valid dict
                            "tools" in test_plan or not found_valid
                        ):
                            content = json_str  # Use this JSON object
                            found_valid = True
                            log.debug(f"Extracted first complete JSON object from multiple JSON responses (length: {len(json_str)})")
                            if "tools" in test_plan:
                                break  # Found one with tools, use it
                    except json.JSONDecodeError:
                        continue
        # If we found a valid one, content is already updated
        if not found_valid:
            log.warning("Multiple JSON objects detected but none were valid, trying original content")

    try:
        plan = json.loads(content)

        if isinstance(plan, dict):
            # Set defaults
            plan.setdefault("intent", "")
            plan.setdefault("reasoning", "")
            plan.setdefault("can_answer_directly", False)
            plan.setdefault("needs_clarification", False)
            plan.setdefault("clarifying_question", "")
            plan.setdefault("tools", [])

            # Normalize tools
            normalized_tools = [
                {"name": tool["name"], "args": tool.get("args", {})}
                for tool in plan.get("tools", [])
                if isinstance(tool, dict) and "name" in tool
            ]

            # Limit retrieval queries
            retrieval_tools = [t for t in normalized_tools if "retrieval" in t.get("name", "").lower()]
            if len(retrieval_tools) > NodeConfig.MAX_RETRIEVAL_QUERIES:
                log.warning(f"Too many retrieval queries ({len(retrieval_tools)}), limiting to {NodeConfig.MAX_RETRIEVAL_QUERIES}")
                other_tools = [t for t in normalized_tools if "retrieval" not in t.get("name", "").lower()]
                normalized_tools = retrieval_tools[:NodeConfig.MAX_RETRIEVAL_QUERIES] + other_tools

            # Trim overly long queries
            for tool in normalized_tools:
                if "retrieval" in tool.get("name", "").lower():
                    query = tool.get("args", {}).get("query", "")
                    if len(query) > NodeConfig.MAX_QUERY_LENGTH:
                        words = query.split()[:NodeConfig.MAX_QUERY_WORDS]
                        trimmed = " ".join(words)
                        log.warning(f"Trimmed query: '{query[:50]}...' → '{trimmed}'")
                        tool["args"]["query"] = trimmed

            plan["tools"] = normalized_tools
            return plan

    except json.JSONDecodeError as e:
        log.warning(f"Failed to parse planner response: {e}")

    return _create_fallback_plan("")


def _create_fallback_plan(query: str, state: "ChatState | None" = None) -> dict[str, Any]:
    """Create a context-aware fallback plan when the planner times out or fails.

    Decision tree:
    1. If retrieval was already executed this turn AND action tools are available
       → plan those action tools (don't repeat retrieval endlessly).
    2. If retrieval was already executed but no action tools match the query intent
       → can_answer_directly so the LLM at least responds with retrieved knowledge.
    3. If retrieval has NOT been executed yet and knowledge is configured
       → default to retrieval (original behaviour).
    4. No knowledge, no state → can_answer_directly.
    """
    # ── 1. Identify what was already executed this turn ──────────────────────
    all_tool_results = []
    has_knowledge = False
    if state:
        all_tool_results = state.get("all_tool_results", []) or []
        has_knowledge = state.get("has_knowledge", False)

    executed_names = {
        r.get("tool_name", "") for r in all_tool_results if isinstance(r, dict)
    }
    retrieval_done = any(
        "retrieval" in name or "search_internal_knowledge" in name
        for name in executed_names
    )

    # ── 2. After retrieval: try to plan action tools ──────────────────────────
    if retrieval_done and state:
        try:
            from app.modules.agents.qna.tool_system import get_agent_tools_with_schemas
            available = {getattr(t, "name", "") for t in get_agent_tools_with_schemas(state)}
        except Exception:
            available = set()

        query_lower = (query or "").lower()
        fallback_tools = []

        # Email intent
        wants_email = any(w in query_lower for w in ["email", "mail", "send", "reply"])
        if wants_email:
            for candidate in ["gmail.reply", "gmail.send_email", "gmail.draft_email"]:
                if candidate in available:
                    fallback_tools.append({
                        "name": candidate,
                        "args": {
                            "to": "{{previous_email_sender}}",
                            "subject": "Re: {{previous_email_subject}}",
                            "body": "{{detailed_content_from_knowledge}}"
                        }
                    })
                    break

        # Ticket/Jira intent
        wants_ticket = any(w in query_lower for w in ["ticket", "jira", "issue", "update", "comment"])
        if wants_ticket:
            for candidate in ["jira.update_issue", "jira.add_comment"]:
                if candidate in available:
                    fallback_tools.append({
                        "name": candidate,
                        "args": {
                            "issue_key": "{{jira_issue_key_from_context}}",
                            "description": "{{detailed_content_from_knowledge}}"
                        }
                    })
                    break

        if fallback_tools:
            return {
                "intent": "Fallback: Execute action after retrieval",
                "reasoning": "Retrieval complete; timeout fallback proceeding with write actions",
                "can_answer_directly": False,
                "needs_clarification": False,
                "clarifying_question": "",
                "tools": fallback_tools,
            }

        # Retrieval done but no matching action tools found → respond with knowledge
        return {
            "intent": "Fallback: Respond with retrieved knowledge",
            "reasoning": "Retrieval complete; planner timeout — responding directly",
            "can_answer_directly": True,
            "needs_clarification": False,
            "clarifying_question": "",
            "tools": [],
        }

    # ── 3. Retrieval not yet done — use it if knowledge is configured ─────────
    if has_knowledge:
        return {
            "intent": "Fallback: Search internal knowledge",
            "reasoning": "Planner failed; searching knowledge base",
            "can_answer_directly": False,
            "needs_clarification": False,
            "clarifying_question": "",
            "tools": [{"name": "retrieval.search_internal_knowledge", "args": {"query": query}}],
        }

    # ── 4. No knowledge, no context — answer directly ────────────────────────
    return {
        "intent": "Fallback: Direct answer",
        "reasoning": "Planner failed; no knowledge configured",
        "can_answer_directly": True,
        "needs_clarification": False,
        "clarifying_question": "",
        "tools": [],
    }


def _validate_planned_tools(
    planned_tools: list[dict[str, Any]],
    state: ChatState,
    log: logging.Logger
) -> tuple[bool, list[str], list[str]]:
    """
    Validate planned tool names against available tools.

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

        # Get available tool names (both sanitized and original, like tools_by_name in execution)
        available_tool_names = set()
        for tool in tools:
            sanitized_name = getattr(tool, 'name', str(tool))
            available_tool_names.add(sanitized_name)
            # Also add original name if different (like tools_by_name does)
            original_name = getattr(tool, '_original_name', sanitized_name)
            if original_name != sanitized_name:
                available_tool_names.add(original_name)

        # Check for invalid tools — same 2-step resolution as execution
        invalid_tools = []
        for tool_call in planned_tools:
            if isinstance(tool_call, dict):
                tool_name = tool_call.get('name', '')
                found = (
                    tool_name in available_tool_names
                    or (_sanitize_tool_name_if_needed(tool_name, llm, state) if llm else tool_name)
                    in available_tool_names
                )
                if not found:
                    invalid_tools.append(tool_name)

        is_valid = len(invalid_tools) == 0
        return is_valid, invalid_tools, list(available_tool_names)

    except Exception as e:
        log.warning(f"Tool validation failed: {e}")
        return True, [], []





# ============================================================================
# PART 3: EXECUTE, REFLECT, RESPOND NODES + COMPLETE SYSTEM
# ============================================================================

# ============================================================================
# EXECUTE NODE - WITH CASCADING SUPPORT
# ============================================================================

async def execute_node(
    state: ChatState,
    config: RunnableConfig,
    writer: StreamWriter
) -> ChatState:
    """
    Execute planned tools with cascading support.

    Features:
    - Automatic detection of cascading needs
    - Sequential execution with placeholder resolution
    - Parallel execution when no dependencies
    - Comprehensive error handling
    """
    start_time = time.perf_counter()
    log = state.get("logger", logger)

    planned_tools = state.get("planned_tool_calls", [])

    if not planned_tools:
        log.info("No tools to execute")
        state["pending_tool_calls"] = False
        return state

    # Build informative execution status
    tool_names = [tool.get("name", "") for tool in planned_tools]
    if len(tool_names) == 1:
        tool_name = tool_names[0]
        if "retrieval" in tool_name.lower():
            status_msg = "Searching knowledge base for relevant information..."
        elif "confluence" in tool_name.lower():
            if "create" in tool_name.lower():
                status_msg = "Creating Confluence page..."
            elif "update" in tool_name.lower():
                status_msg = "Updating Confluence page..."
            else:
                status_msg = "Accessing Confluence content..."
        elif "jira" in tool_name.lower():
            if "create" in tool_name.lower():
                status_msg = "Creating Jira issue..."
            elif "update" in tool_name.lower():
                status_msg = "Updating Jira issue..."
            else:
                status_msg = "Accessing Jira..."
        else:
            status_msg = f"Executing {tool_name.replace('_', ' ').title()}..."
    else:
        # Multiple tools - describe what we're doing
        has_retrieval = any("retrieval" in t.lower() for t in tool_names)
        has_confluence = any("confluence" in t.lower() for t in tool_names)
        has_jira = any("jira" in t.lower() for t in tool_names)

        actions = []
        if has_retrieval:
            actions.append("searching knowledge base")
        if has_confluence:
            actions.append("working with Confluence")
        if has_jira:
            actions.append("working with Jira")

        if actions:
            status_msg = f"Executing {len(planned_tools)} operations: {', '.join(actions)}..."
        else:
            status_msg = f"Executing {len(planned_tools)} operations in parallel..."

    safe_stream_write(writer, {
        "event": "status",
        "data": {"status": "executing", "message": status_msg}
    }, config)

    # Get available tools
    try:
        from app.modules.agents.qna.tool_system import get_agent_tools_with_schemas

        tools = get_agent_tools_with_schemas(state)
        llm = state.get("llm")

        # Build tool mapping: both sanitized (underscore) and original (dot) names.
        # _underscore_to_dotted is NOT applied here because it only replaces the
        # first underscore, which is wrong for multi-word app names like
        # knowledge_hub (knowledge_hub_list_files → knowledge.hub_list_files ✗).
        # The two-entry map (sanitized + original) is sufficient: the LLM outputs
        # either the sanitized or the original name and step-1 lookup always hits.
        tools_by_name = {}
        for t in tools:
            sanitized_name = getattr(t, 'name', str(t))
            original_name = getattr(t, '_original_name', sanitized_name)
            tools_by_name[sanitized_name] = t
            if original_name != sanitized_name:
                tools_by_name[original_name] = t

    except Exception as e:
        log.error(f"Failed to get tools: {e}")
        tools_by_name = {}

    # Execute tools (cascading detection handled internally)
    tool_results = await ToolExecutor.execute_tools(
        planned_tools, tools_by_name, llm, state, log, writer, config
    )

    # Build tool messages
    from app.utils.tool_handlers import ToolHandlerRegistry

    tool_messages = []
    ref_mapper = state.get("citation_ref_mapper")
    handler_context = {
        "ref_mapper": ref_mapper,
        "config_service": state.get("config_service"),
        "is_multimodal_llm": state.get("is_multimodal_llm", False),
    }
    for result in tool_results:
        if result.get("tool_id"):
            tool_result_data = result.get("result", "")
            if (
                isinstance(tool_result_data, dict)
                and tool_result_data.get("ok")
                and tool_result_data.get("result_type") in ("web_search", "url_content")
            ):
                handler = ToolHandlerRegistry.get_handler(tool_result_data)
                tool_msg_content = await handler.format_message(tool_result_data, handler_context)
                tool_messages.append(ToolMessage(
                    content=tool_msg_content,
                    tool_call_id=result.get("tool_id", ""),
                ))
            else:
                content_str = format_result_for_llm(tool_result_data, result.get("tool_name", ""))
                tool_messages.append(ToolMessage(
                    content=content_str,
                    tool_call_id=result.get("tool_id", ""),
                ))

    # Update state
    # IMPORTANT: accumulate across iterations so that retrieval results from
    # iteration 1 remain visible to the planner in iterations 2, 3, ...
    state["tool_results"] = tool_results
    existing_results = state.get("all_tool_results", []) or []
    state["all_tool_results"] = existing_results + tool_results

    # Track executed tool names for continue mode status messages
    executed_tool_names = [r.get("tool_name", "") for r in tool_results if r.get("tool_name")]
    if "executed_tool_names" in state:
        state["executed_tool_names"].extend(executed_tool_names)
    else:
        state["executed_tool_names"] = executed_tool_names

    if not state.get("messages"):
        state["messages"] = []
    state["messages"].extend(tool_messages)

    state["pending_tool_calls"] = False

    # Log summary
    success_count = sum(1 for r in tool_results if r.get("status") == "success")
    failed_count = sum(1 for r in tool_results if r.get("status") == "error")

    duration_ms = (time.perf_counter() - start_time) * 1000
    log.info(f"✅ Executed {len(tool_results)} tools in {duration_ms:.0f}ms ({success_count} ✓, {failed_count} ✗)")

    return state


# ============================================================================
# REFLECT NODE - SMART DECISION MAKING
# ============================================================================

async def reflect_node(
    state: ChatState,
    config: RunnableConfig,
    writer: StreamWriter
) -> ChatState:
    """
    Analyze tool results and decide next action.

    Features:
    - Partial success detection
    - Primary tool success detection
    - Smart error categorization
    - Context-aware retry decisions
    """
    start_time = time.perf_counter()
    log = state.get("logger", logger)

    tool_results = state.get("all_tool_results", [])
    retry_count = state.get("retry_count", 0)
    max_retries = state.get("max_retries", NodeConfig.MAX_RETRIES)
    iteration_count = state.get("iteration_count", 0)
    max_iterations = state.get("max_iterations", NodeConfig.MAX_ITERATIONS)

    # Count successes and failures
    successful = [r for r in tool_results if r.get("status") == "success"]
    failed = [r for r in tool_results if r.get("status") == "error"]
    cascade_errors = [r for r in tool_results if r.get("status") == "cascade_error"]

    log.info(f"📊 Tool results: {len(successful)} ✓, {len(failed)} ✗, {len(cascade_errors)} cascade")

    # Log details for debugging
    for r in successful:
        log.info(f"  ✅ {r.get('tool_name')}")
    for r in failed:
        log.info(f"  ❌ {r.get('tool_name')}: {str(r.get('result', ''))[:300]}")
    for r in cascade_errors:
        log.info(f"  🔗❌ {r.get('tool_name')}: cascade broken")

    # ========================================================================
    # PRE-CHECK: Orchestration failures override all other decisions
    # ========================================================================

    cascade_broken = [r for r in tool_results
                      if r.get("orchestration_status") == ORCHESTRATION_STATUS_CASCADE_BROKEN]
    empty_cascade_sources = [r for r in tool_results
                             if r.get("orchestration_status") == "empty_cascade_source"]

    if cascade_errors or cascade_broken:
        log.info(f"🔗 ORCHESTRATION FAILURE: {len(cascade_errors)} cascade errors detected")
        state["reflection_decision"] = "respond_error"
        state["reflection"] = {
            "decision": "respond_error",
            "reasoning": (
                f"Cascading tool chain broke: "
                f"{[r.get('tool_name') for r in (cascade_errors or cascade_broken)]}. "
                f"A multi-step operation failed because intermediate results were unavailable."
            ),
            "error_context": "cascade_broken",
            "task_complete": False,
        }
        duration_ms = (time.perf_counter() - start_time) * 1000
        log.info(f"⚡ Reflect: respond_error (cascade) - {duration_ms:.0f}ms")
        return state

    if empty_cascade_sources:
        log.info(f"🔗 EMPTY CASCADE SOURCE: {[r.get('tool_name') for r in empty_cascade_sources]}")
        # Don't hard-fail — mark state so downstream reflection knows
        state["_cascade_source_empty"] = True

    # ========================================================================
    # DECISION 1: Partial Success (some succeeded, some failed)
    # ========================================================================

    if len(successful) > 0 and len(failed) > 0:
        log.info("🔀 Partial success detected")

        # Check if primary tool succeeded
        query = state.get("query", "").lower()
        primary_succeeded = _check_primary_tool_success(query, successful, log)

        # Check if we have retrieval results
        has_retrieval = any("retrieval" in r.get("tool_name", "").lower() for r in successful)

        if primary_succeeded or has_retrieval:
            log.info("✅ Primary tool or retrieval succeeded - proceeding")
            state["reflection_decision"] = "respond_success"
            state["reflection"] = {
                "decision": "respond_success",
                "reasoning": f"Primary task completed ({len(successful)} succeeded, ignoring {len(failed)} secondary failures)",
                "task_complete": True
            }
            duration_ms = (time.perf_counter() - start_time) * 1000
            log.info(f"⚡ Reflect: respond_success (partial) - {duration_ms:.0f}ms")
            return state

    # ========================================================================
    # DECISION 2: All Succeeded - Check if Task Complete
    # ========================================================================

    if not failed:
        query = state.get("query", "").lower()
        executed_tools = [r.get("tool_name", "") for r in tool_results]

        # Check if task needs more steps
        needs_continue = _check_if_task_needs_continue(query, executed_tools, tool_results, log, state)

        if needs_continue and iteration_count < max_iterations:
            state["reflection_decision"] = "continue_with_more_tools"
            state["reflection"] = {
                "decision": "continue_with_more_tools",
                "reasoning": "Tools succeeded but task incomplete",
                "task_complete": False
            }
            log.info(f"➡️ Continue needed (iteration {iteration_count + 1}/{max_iterations})")
            duration_ms = (time.perf_counter() - start_time) * 1000
            log.info(f"⚡ Reflect: continue - {duration_ms:.0f}ms")
            return state
        else:
            state["reflection_decision"] = "respond_success"
            state["reflection"] = {
                "decision": "respond_success",
                "reasoning": "All succeeded" if not needs_continue else "Max iterations reached",
                "task_complete": not needs_continue
            }
            duration_ms = (time.perf_counter() - start_time) * 1000
            log.info(f"⚡ Reflect: respond_success (all done) - {duration_ms:.0f}ms")
            return state

    # ========================================================================
    # DECISION 3: Check Primary Tool Success (for cascading)
    # ========================================================================

    planned_tools = state.get("planned_tool_calls", [])
    if planned_tools and len(planned_tools) > 0 and len(successful) > 0:
        # GUARD: If this is a cascading chain, primary tool success alone
        # does NOT mean the task is complete.  Check the LAST tool instead.
        has_cascading = PlaceholderResolver.has_placeholders({"tools": planned_tools})

        if has_cascading:
            # For cascading chains, success = last tool succeeded with meaningful data
            last_result = tool_results[-1] if tool_results else None
            if (last_result
                    and last_result.get("status") == "success"
                    and not _is_semantically_empty(last_result.get("result"))):
                log.info("✅ Cascading chain completed: last tool has data")
                state["reflection_decision"] = "respond_success"
                state["reflection"] = {
                    "decision": "respond_success",
                    "reasoning": "Cascading chain completed — last tool returned meaningful data",
                    "task_complete": True
                }
                duration_ms = (time.perf_counter() - start_time) * 1000
                log.info(f"⚡ Reflect: respond_success (cascade complete) - {duration_ms:.0f}ms")
                return state
            else:
                log.info("🔗 Cascading chain: last tool empty/failed — skipping primary-success shortcut")
                # Fall through to error handling / LLM reflection
        else:
            # Non-cascading: original primary tool check
            primary_tool_name = planned_tools[0].get("name", "").lower()

            for result in successful:
                tool_name = result.get("tool_name", "").lower()
                normalized_primary = primary_tool_name.replace('.', '_')
                normalized_tool = tool_name.replace('.', '_')

                if tool_name == primary_tool_name or normalized_tool == normalized_primary:
                    log.info(f"✅ Primary action succeeded: {tool_name}")
                    state["reflection_decision"] = "respond_success"
                    state["reflection"] = {
                        "decision": "respond_success",
                        "reasoning": "Primary action succeeded (dependent tools failed but task complete)",
                        "task_complete": True
                    }
                    duration_ms = (time.perf_counter() - start_time) * 1000
                    log.info(f"⚡ Reflect: respond_success (primary) - {duration_ms:.0f}ms")
                    return state

    # ========================================================================
    # DECISION 4: Fast Path Error Detection
    # ========================================================================

    error_text = " ".join(str(r.get("result", "")) for r in failed).lower()

    # Unrecoverable errors
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

        actual_errors = [
            f"{r.get('tool_name', 'unknown')}: {str(r.get('result', ''))[:400]}"
            for r in failed
        ]

        state["reflection_decision"] = "respond_error"
        state["reflection"] = {
            "decision": "respond_error",
            "reasoning": "Unrecoverable error",
            "error_context": error_context,
            "actual_errors": actual_errors,
        }
        log.info(f"❌ Unrecoverable error: {error_context}")
        duration_ms = (time.perf_counter() - start_time) * 1000
        log.info(f"⚡ Reflect: respond_error - {duration_ms:.0f}ms")
        return state

    # ========================================================================
    # DECISION 5: Recoverable Errors (Retry Logic)
    # ========================================================================

    if retry_count < max_retries:
        # Unbounded JQL
        if "unbounded" in error_text:
            state["reflection_decision"] = "retry_with_fix"
            state["reflection"] = {
                "decision": "retry_with_fix",
                "reasoning": "Unbounded JQL",
                "fix_instruction": "Add time filter: `AND updated >= -30d`"
            }
            log.info("🔄 Retry: Unbounded JQL")
            duration_ms = (time.perf_counter() - start_time) * 1000
            log.info(f"⚡ Reflect: retry_with_fix - {duration_ms:.0f}ms")
            return state

        # Type errors
        if "not the correct type" in error_text or "expected type" in error_text:
            state["reflection_decision"] = "retry_with_fix"
            state["reflection"] = {
                "decision": "retry_with_fix",
                "reasoning": "Parameter type error",
                "fix_instruction": "Check parameter types and convert to correct format (e.g., numeric ID instead of string key)"
            }
            log.info("🔄 Retry: Type error")
            duration_ms = (time.perf_counter() - start_time) * 1000
            log.info(f"⚡ Reflect: retry_with_fix - {duration_ms:.0f}ms")
            return state

        # Syntax errors
        if any(x in error_text for x in ["syntax", "invalid", "malformed", "parse error"]):
            state["reflection_decision"] = "retry_with_fix"
            state["reflection"] = {
                "decision": "retry_with_fix",
                "reasoning": "Syntax error",
                "fix_instruction": "Fix query syntax based on error message"
            }
            log.info("🔄 Retry: Syntax error")
            duration_ms = (time.perf_counter() - start_time) * 1000
            log.info(f"⚡ Reflect: retry_with_fix - {duration_ms:.0f}ms")
            return state

    # ========================================================================
    # DECISION 6: LLM-Based Reflection (Complex Cases)
    # ========================================================================

    llm = state.get("llm")

    # Build summary
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

        keepalive_task = asyncio.create_task(
            send_keepalive(writer, config, "Analyzing results...")
        )
        try:
            response = await asyncio.wait_for(
                llm.ainvoke([
                    SystemMessage(content=prompt),
                    HumanMessage(content="Analyze and decide.")
                ]),
                timeout=NodeConfig.REFLECTION_TIMEOUT_SECONDS
            )
        finally:
            keepalive_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await keepalive_task

        reflection = _parse_reflection_response(coerce_message_content_to_text(response.content), log)

    except asyncio.TimeoutError:
        log.warning("⏱️ Reflect timeout")
        reflection = {
            "decision": "respond_error",
            "reasoning": "Analysis timeout",
            "error_context": "Unable to complete request"
        }
    except Exception as e:
        log.error(f"💥 Reflection failed: {e}")
        reflection = {
            "decision": "respond_error",
            "reasoning": str(e),
            "error_context": "Error processing request"
        }

    state["reflection"] = reflection
    state["reflection_decision"] = reflection.get("decision", "respond_error")

    duration_ms = (time.perf_counter() - start_time) * 1000
    log.info(f"⚡ Reflect: {state['reflection_decision']} (LLM) - {duration_ms:.0f}ms")

    return state


def _parse_reflection_response(content: str, log: logging.Logger) -> dict[str, Any]:
    """Parse reflection JSON response"""
    content = content.strip()

    # Remove markdown
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
            return reflection

    except json.JSONDecodeError as e:
        log.warning(f"Failed to parse reflection: {e}")

    return {
        "decision": "respond_error",
        "reasoning": "Parse failed",
        "error_context": "Unable to process request"
    }


def _check_primary_tool_success(query: str, successful: list[dict], log: logging.Logger) -> bool:
    """
    In a partial-success scenario (some tools succeeded, some failed), determine
    whether the *primary* / most important tool for the user's intent succeeded.

    If the primary tool succeeded we can proceed to respond even if secondary
    tools failed (e.g. an enrichment step or a non-critical side action).

    Strategy:
    1. Infer the primary intent from clear action verbs in the query.
    2. Check whether a tool whose name contains that intent verb succeeded.
    3. If we cannot match precisely, fall back to True if *any* tool succeeded
       (the respond node will surface partial results gracefully).

    Note: "make" is intentionally excluded — it is too ambiguous ("make a
    summary", "make a copy", etc.) and does not map reliably to a tool verb.
    """
    query_lower = (query or "").lower()
    successful_tools = [r.get("tool_name", "").lower() for r in successful]

    # Intent verb → tool-name segment that signals the primary action completed.
    # Order matters: more specific intents first.
    intent_to_tool_segment: list[tuple] = [
        # (query keywords that signal this intent, tool-name segment to look for)
        (["create", "new"],              "create"),
        (["update", "modify", "change", "edit"], "update"),
        (["delete", "remove"],           "delete"),
        (["add", "comment"],             "add"),
        (["send", "post", "notify"],     "send"),
        (["reply"],                      "reply"),
        (["assign"],                     "assign"),
        (["transition", "move"],         "transition"),
        (["publish"],                    "publish"),
        (["search", "find"],             "search"),
        (["get", "list", "fetch"],       "get"),
    ]

    for query_keywords, tool_segment in intent_to_tool_segment:
        if any(kw in query_lower for kw in query_keywords):
            # Check whether a tool with this segment succeeded
            for tool in successful_tools:
                # Match against the action part (after service prefix)
                action_part = tool.split(".", 1)[1] if "." in tool else tool
                if action_part.startswith(tool_segment + "_") or action_part == tool_segment:
                    log.debug(f"✅ Primary intent '{tool_segment}' matched succeeded tool: {tool}")
                    return True
            # Intent identified but no matching tool succeeded — stop here.
            # (The fallback below will still return True if anything succeeded.)
            break

    # Fallback: if any tool succeeded, treat the primary as done and let
    # the respond node explain partial results to the user.
    return len(successful) > 0


def _check_if_task_needs_continue(
    query: str,
    executed_tools: list[str],
    tool_results: list[dict[str, Any]],
    log: logging.Logger,
    state: dict[str, Any] | None = None
) -> bool:
    """
    Determine whether the agent needs another planning cycle.

    Returns True if there are planned tools that have not yet been executed.
    Returns False if all planned tools have been executed.
    """
    if not state:
        return False

    planned_tools = state.get("planned_tool_calls", [])
    if not planned_tools:
        return False

    # Normalize tool names for comparison (handle both dotted and underscored formats)
    planned_names = set()
    for tool in planned_tools:
        if isinstance(tool, dict):
            name = tool.get("name", "")
            planned_names.add(name)
            planned_names.add(name.replace(".", "_"))
            planned_names.add(name.replace("_", "."))

    executed_names = set(executed_tools)
    for tool_name in executed_tools:
        executed_names.add(tool_name.replace(".", "_"))
        executed_names.add(tool_name.replace("_", "."))

    if not planned_names.issubset(executed_names):
        missing = planned_names - executed_names
        log.debug(f"Some planned tools not yet executed: {missing}")
        return True

    return False


def _is_retrieval_tool(tool_name: str) -> bool:
    """Return True if the tool is an internal retrieval/search-knowledge tool."""
    name = (tool_name or "").lower()
    return "retrieval" in name or "search_internal_knowledge" in name


# ============================================================================
# PREPARE RETRY NODE
# ============================================================================

async def prepare_retry_node(
    state: ChatState,
    config: RunnableConfig,
    writer: StreamWriter
) -> ChatState:
    """Prepare for retry after fixable error"""
    log = state.get("logger", logger)

    state["retry_count"] = state.get("retry_count", 0) + 1
    state["is_retry"] = True

    # Extract errors
    tool_results = state.get("all_tool_results", [])
    errors = [
        {
            "tool_name": r.get("tool_name", "unknown"),
            "args": r.get("args", {}),
            "error": str(r.get("result", ""))[:300],
        }
        for r in tool_results
        if r.get("status") == "error"
    ]

    state["execution_errors"] = errors

    # Clear old results
    state["all_tool_results"] = []
    state["tool_results"] = []

    safe_stream_write(writer, {
        "event": "status",
        "data": {"status": "retrying", "message": "Retrying..."}
    }, config)

    log.info(f"🔄 Retry {state['retry_count']}/{state.get('max_retries', NodeConfig.MAX_RETRIES)}: {len(errors)} errors")

    return state


# ============================================================================
# PREPARE CONTINUE NODE
# ============================================================================

async def prepare_continue_node(
    state: ChatState,
    config: RunnableConfig,
    writer: StreamWriter
) -> ChatState:
    """Prepare to continue with more tools"""
    log = state.get("logger", logger)

    state["iteration_count"] = state.get("iteration_count", 0) + 1
    state["is_continue"] = True

    # Keep tool results for next planning

    # Get context about what we're continuing with
    query = state.get("query", "")
    previous_tools = state.get("executed_tool_names", [])
    iteration_count = state.get("iteration_count", 0)
    max_iterations = state.get("max_iterations", NodeConfig.MAX_ITERATIONS)

    # Build informative message based on what was done and what's needed
    query_lower = query.lower()
    if previous_tools:
        last_tool = previous_tools[-1] if previous_tools else ""
        if "retrieval" in last_tool.lower():
            action_desc = "gathered information"
            next_action = "taking action" if any(word in query_lower for word in ["create", "update", "make", "add", "edit"]) else "completing the task"
        elif "create" in last_tool.lower():
            action_desc = "created resources"
            next_action = "completing additional steps"
        elif "update" in last_tool.lower():
            action_desc = "updated resources"
            next_action = "completing additional steps"
        elif "search" in last_tool.lower() or "get" in last_tool.lower():
            action_desc = "retrieved information"
            next_action = "taking action based on the information"
        else:
            action_desc = "completed previous steps"
            next_action = "continuing with next steps"

        message = f"Step {iteration_count}/{max_iterations}: After we {action_desc}, now {next_action}..."
    else:
        message = f"Step {iteration_count}/{max_iterations}: Planning next steps to complete your request..."

    safe_stream_write(writer, {
        "event": "status",
        "data": {"status": "continuing", "message": message}
    }, config)

    max_iterations = state.get("max_iterations", NodeConfig.MAX_ITERATIONS)
    log.info(f"➡️ Continue {state['iteration_count']}/{max_iterations}")

    return state


# ============================================================================
# RESPOND NODE - FINAL RESPONSE GENERATION
# ============================================================================

async def respond_node(
    state: ChatState,
    config: RunnableConfig,
    writer: StreamWriter
) -> ChatState:
    """
    Generate final response with streaming.

    Features:
    - Streaming response generation
    - Citation extraction for retrieval results
    - Reference data extraction for API results
    - Proper error handling
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
        error_response.update(_tool_names_and_results_from_state(state))
        safe_stream_write(writer, {
            "event": "answer_chunk",
            "data": {"chunk": error_msg, "accumulated": error_msg, "citations": []}
        }, config)
        _emit_ask_user_question_tool_event(writer, state, config)
        safe_stream_write(writer, {"event": "complete", "data": error_response}, config)
        state["response"] = error_msg
        state["completion_data"] = error_response
        return state

    # Check if direct answer
    execution_plan = state.get("execution_plan", {})
    tool_results = state.get("all_tool_results", [])

    if execution_plan.get("can_answer_directly") and not tool_results:
        await _generate_direct_response(state, llm, log, writer, config)
        return state

    # Handle clarification
    reflection_decision = state.get("reflection_decision", "respond_success")
    reflection = state.get("reflection", {})

    if reflection_decision == "respond_clarify":
        clarifying_question = reflection.get("clarifying_question", "Could you provide more details?")
        clarify_response = {
            "answer": clarifying_question,
            "citations": [],
            "confidence": "Medium",
            "answerMatchType": "Clarification Needed"
        }
        clarify_response.update(_tool_names_and_results_from_state(state))
        _emit_ask_user_question_tool_event(writer, state, config)
        safe_stream_write(writer, {
            "event": "answer_chunk",
            "data": {"chunk": clarifying_question, "accumulated": clarifying_question, "citations": []}
        }, config)
        safe_stream_write(writer, {"event": "complete", "data": clarify_response}, config)
        state["response"] = clarifying_question
        state["completion_data"] = clarify_response
        return state

    # respond_error falls through to the normal LLM path so the LLM sees the actual
    # error details from tool_results via _build_tool_results_context (same as partial
    # success).

    # Generate success response
    final_results = state.get("final_results", [])
    virtual_record_map = state.get("virtual_record_id_to_result", {})
    query = state.get("query", "")
    org_id = state.get("org_id", "")

    # ================================================================
    # FAST PATH: API-only results with sub-agent analyses
    # When there are no retrieval results (no citations needed) and
    # sub-agents already produced analyses, use a lightweight LLM call
    # instead of the full stream_llm_response_with_tools pipeline.
    # This typically saves 20-30 seconds by avoiding redundant processing.
    #
    # For complex tasks (weekly summaries, reports), sub_agent_analyses
    # contain consolidated domain summaries and tool_results may be empty
    # (raw results replaced by summaries). The fast-path still applies.
    # ================================================================
    sub_agent_analyses = state.get("sub_agent_analyses", [])

    # Rebuild from completed_tasks if sub_agent_analyses is empty (safety net)
    if not sub_agent_analyses:
        completed_tasks = state.get("completed_tasks", [])
        for ct in completed_tasks:
            if ct.get("status") != "success":
                continue
            ct_id = ct.get("task_id", "unknown")
            ct_domains = ", ".join(ct.get("domains", []))
            ds = ct.get("domain_summary")
            if ds:
                sub_agent_analyses.append(f"[{ct_id} ({ct_domains})]: {ds}")
                continue
            ct_result = ct.get("result", {})
            if isinstance(ct_result, dict):
                rt = ct_result.get("response", "")
                if rt:
                    sub_agent_analyses.append(f"[{ct_id} ({ct_domains})]: {rt}")
        if sub_agent_analyses:
            log.info("Rebuilt %d sub_agent_analyses from completed_tasks", len(sub_agent_analyses))

    log.info(
        "Fast-path check: analyses=%d, final_results=%d, virtual_map=%d, tool_results=%d",
        len(sub_agent_analyses), len(final_results), len(virtual_record_map), len(tool_results),
    )
    _prior_web_records = _extract_web_records_from_tool_results(tool_results, org_id)

    if (
        sub_agent_analyses
        and not final_results
        and not virtual_record_map
        and not _prior_web_records
    ):
        log.info("⚡ Fast-path: API-only results with sub-agent analysis, using lightweight response")
        try:
            result = await _generate_fast_api_response(
                state, llm, query, tool_results, sub_agent_analyses, log, writer, config,
            )
            if result:
                duration_ms = (time.perf_counter() - start_time) * 1000
                log.info(f"⚡ respond_node (fast-path): {duration_ms:.0f}ms")
                return state
        except Exception as e:
            log.warning(f"Fast-path failed, falling back to standard: {e}")
            # Fall through to standard path


    log.info(f"📚 Citation data: {len(final_results)} results, {len(virtual_record_map)} records")

    # ================================================================
    # Use get_message_content() — the EXACT same function the chatbot
    # uses — to build the user message with knowledge context.
    # This ensures:
    #   • Consistent block indices and block web URLs
    #   • The same rich context_metadata per record
    #   • The same tool instructions (fetch_full_record with record IDs)
    #   • The same output-format instructions
    # The formatted content is stored in state["qna_message_content"]
    # and consumed by create_response_messages() below.
    # ================================================================
    if final_results and virtual_record_map:
        from app.utils.chat_helpers import get_message_content as _get_msg_content

        # Build user_data string (same logic as chatbot's askAIStream)
        user_data = ""
        user_info = state.get("user_info") or {}
        org_info = state.get("org_info") or {}
        if user_info:
            account_type = (org_info.get("accountType") or "") if org_info else ""
            if account_type in ("Enterprise", "Business"):
                user_data = (
                    "I am the user of the organization. "
                    f"My name is {user_info.get('fullName', 'a user')} "
                    f"({user_info.get('designation', '')}) "
                    f"from {org_info.get('name', 'the organization')}. "
                    "Please provide accurate and relevant information."
                )
            else:
                user_data = (
                    "I am the user. "
                    f"My name is {user_info.get('fullName', 'a user')} "
                    f"({user_info.get('designation', '')}). "
                    "Please provide accurate and relevant information."
                )

        from app.utils.chat_helpers import CitationRefMapper as _CitationRefMapper
        _ref_mapper = state.get("citation_ref_mapper") or _CitationRefMapper()
        qna_content, _ref_mapper = _get_msg_content(
            final_results, virtual_record_map, user_data, query, "json",is_multimodal_llm=state.get("is_multimodal_llm", False), ref_mapper=_ref_mapper, has_sql_connector=state.get("has_sql_connector", False) and state.get("has_sql_knowledge", False), has_slack_connector=state.get("has_slack_connector", False) and state.get("has_slack_knowledge", False)
        )
        state["citation_ref_mapper"] = _ref_mapper
        state["qna_message_content"] = qna_content
        log.debug("✅ Built qna_message_content via get_message_content() (chatbot-identical format)")
    else:
        state["qna_message_content"] = None

    # Build messages (create_response_messages uses qna_message_content as user msg)
    messages = await create_response_messages(state)

    # Ensure attachments are resolved (guard for react graph where respond_node
    # is reached after react_agent_node; cached if already resolved) then inject.
    attachment_blocks = await _ensure_attachment_blocks(state, log)
    _inject_attachment_blocks(messages, attachment_blocks)

    # Append non-retrieval tool results (API tools: Jira, Slack, etc.)
    # Retrieval results are already embedded in the user message via get_message_content().
    non_retrieval_results = [
        r for r in tool_results
        if r.get("status") == "success"
        and "retrieval" not in r.get("tool_name", "").lower()
    ]
    failed_results = [r for r in tool_results if r.get("status") == "error"]

    has_api_results = non_retrieval_results or (failed_results and not any(r.get("status") == "success" for r in tool_results))

    if has_api_results:
        # Build context for API tool results.
        # When qna_message_content is set, retrieval blocks are already embedded in the
        # user message — pass [] to avoid duplication but set has_retrieval_in_context=True
        # so the LLM is instructed to use MODE 3 (inline citations + referenceData).
        qna_has_retrieval = bool(state.get("qna_message_content"))
        context = (await _build_tool_results_context(
            tool_results,
            [] if qna_has_retrieval else final_results,
            has_retrieval_in_context=qna_has_retrieval,
            ref_mapper=state.get("citation_ref_mapper"),
            config_service=state.get("config_service"),
            is_multimodal_llm=state.get("is_multimodal_llm", False),
            has_attachments=bool(state.get("attachments")),
        )) if has_api_results else ""

        if context.strip():
            if messages and isinstance(messages[-1], HumanMessage):
                last_content = messages[-1].content
                if isinstance(last_content, list):
                    # qna_message_content is a list of content items — append as text item
                    last_content.append({"type": "text", "text": context})
                else:
                    messages[-1].content = last_content + context
            else:
                messages.append(HumanMessage(content=context))

    try:
        log.info("🎯 Using stream_llm_response_with_tools...")

        # Get required parameters from state
        retrieval_service = state.get("retrieval_service")
        user_id = state.get("user_id", "")
        org_id = state.get("org_id", "")
        graph_provider = state.get("graph_provider")
        is_multimodal_llm = state.get("is_multimodal_llm", False)
        is_service_account = bool(state.get("is_service_account", False))
        # Build agent-scoped filter_groups for the service-account fallback retrieval
        # (mirrors what retrieval.py builds from state["filters"])
        agent_filters = state.get("filters") or {}
        agent_filter_groups = {
            "apps": list(set(agent_filters.get("apps", []) or [])),
            "kb": [k for k in (agent_filters.get("kb", []) or []) if k and k != "NO_KB_SELECTED"],
        } if is_service_account else None

        # blob_store is not set by retrieval.py (which creates its own local instance);
        # create one here so stream_llm_response_with_tools can use it when the token
        # threshold is exceeded and a secondary retrieval is needed.
        blob_store = state.get("blob_store")
        if blob_store is None:
            try:
                from app.modules.transformers.blob_storage import BlobStorage
                config_svc = state.get("config_service")
                blob_store = BlobStorage(
                    logger=log,
                    config_service=config_svc,
                    graph_provider=graph_provider,
                )
                state["blob_store"] = blob_store
            except Exception as _bs_err:
                log.warning(f"Could not initialise BlobStorage in respond_node: {_bs_err}")

        # Get context_length from config or use default
        DEFAULT_CONTEXT_LENGTH = 128000
        config_service = state.get("config_service")
        context_length = DEFAULT_CONTEXT_LENGTH
        if config_service:
            with contextlib.suppress(Exception):
                # Try to get context length from LLM config if available
                # This is a fallback - ideally it should be stored in state
                context_length = DEFAULT_CONTEXT_LENGTH

        # Construct all_queries from state
        query = state.get("query", "")
        decomposed_queries = state.get("decomposed_queries", [])
        if decomposed_queries:
            all_queries = [q.get("query", query) for q in decomposed_queries if isinstance(q, dict) and q.get("query")]
            if not all_queries:
                all_queries = [query]
        else:
            all_queries = [query]

        # Create the agent-specific fetch_full_record tool (mirrors the chatbot
        # pipeline: returns raw record dicts so execute_tool_calls in streaming.py
        # formats them via record_to_message_content() — identical to chatbot).
        tools = []
        if virtual_record_map:
            from app.utils.fetch_full_record import (
                create_fetch_full_record_tool,
            )
            fetch_tool = create_fetch_full_record_tool(
                virtual_record_map,
                org_id=org_id,
                graph_provider=graph_provider,
            )
            tools = [fetch_tool]
            log.debug(
                f"Added agent fetch_full_record tool "
                f"({len(virtual_record_map)} records available, "
            )

        # Add web tools when agent has web search configured in the builder
        has_web_search_tool = False
        try:
            from app.modules.agents.qna.tool_system import _create_web_tools
            web_tools = _create_web_tools(state)
            tools.extend(web_tools)
            has_web_search_tool = any(
                getattr(t, 'name', '') == 'web_search' for t in web_tools
            )
            if web_tools:
                log.debug(f"Added {len(web_tools)} web tool(s) to respond_node")
        except Exception as e:
            log.warning(f"Failed to add web tools to respond_node: {e}")

        # Instruct the LLM to use web tools when retrieval results are insufficient.
        # This is the safety net: even if the planner didn't select web_search,
        # the respond-phase LLM can still call it when the provided context
        # clearly does not answer the user's question.
        if has_web_search_tool and messages:
            web_tool_hint = (
                "\n\n## Web Tools Available (CRITICAL — READ BEFORE RESPONDING)\n"
                "You have `web_search` and `fetch_url` tools available.\n\n"
                "**MANDATORY RULE**: If the retrieved knowledge blocks above do NOT contain "
                "sufficient information to answer the user's question, you MUST use "
                "`web_search` (and/or `fetch_url` for specific URLs) to find the answer "
                "from the web BEFORE responding. "
                "Always attempt a web search first.\n\n"
            )
            from langchain_core.messages import SystemMessage as _SysMsg
            if isinstance(messages[0], _SysMsg):
                existing = messages[0].content
                if isinstance(existing, list):
                    messages[0] = _SysMsg(content=existing + [{"type": "text", "text": web_tool_hint}])
                else:
                    messages[0] = _SysMsg(content=existing + web_tool_hint)
            else:
                messages.insert(0, _SysMsg(content=web_tool_hint))
        
      
      
        # Create tool_runtime_kwargs
        tool_runtime_kwargs = {
            "blob_store": blob_store,
            "graph_provider": graph_provider,
            "org_id": org_id,
            "conversation_id": state.get("conversation_id"),
            "config_service": config_service,
        }

        # Pre-seed web_records from prior tool execution so that web citations
        # are available even when the LLM does not re-invoke tools during streaming.
        if _prior_web_records:
            log.info("Pre-seeded %d web records from prior tool execution", len(_prior_web_records))

        answer_text = ""
        citations = []
        reason = None
        confidence = None
        reference_data = []
        _captured_web_records: list[dict] = list(_prior_web_records)
                # Log every tool name + result payload from state (planner / prior execution)
        _tw = _tool_names_and_results_from_state(state)
        _tool_rows = _tw.get("tool_results") or []
        log.debug(
            "respond_node tools before stream | succeeded=%s failed=%s n=%d",
            _tw.get("succeeded_tool_names"),
            _tw.get("failed_tool_names"),
            len(_tool_rows),
        )
        _TOOL_LOG_DATA_MAX = 12000
        for _r in _tool_rows:
            _tname = _r.get("tool_name", "unknown")
            _tdata = _r.get("result", _r)
            try:
                _data_str = json.dumps(_tdata, default=str, ensure_ascii=False)
            except (TypeError, ValueError):
                _data_str = str(_tdata)
            if len(_data_str) > _TOOL_LOG_DATA_MAX:
                _full_len = len(_data_str)
                _data_str = _data_str[:_TOOL_LOG_DATA_MAX] + f"... [truncated, total_len={_full_len}]"
            log.debug(
                "respond_node tool | name=%s status=%s toolData=%s",
                _tname,
                _r.get("status"),
                _data_str,
            )

        async for stream_event in stream_llm_response_with_tools(
            llm=llm,
            messages=messages,
            final_results=final_results,
            all_queries=all_queries,
            retrieval_service=retrieval_service,
            user_id=user_id,
            org_id=org_id,
            virtual_record_id_to_result=virtual_record_map,
            blob_store=blob_store,
            is_multimodal_llm=is_multimodal_llm,
            context_length=context_length,
            tools=tools,
            tool_runtime_kwargs=tool_runtime_kwargs,
            target_words_per_chunk=1,
            mode="json",
            conversation_id=state.get("conversation_id"),
            is_service_account=is_service_account,
            filter_groups=agent_filter_groups,
            ref_mapper=state.get("citation_ref_mapper"),
            initial_web_records=_prior_web_records,
        ):
            event_type = stream_event.get("event")
            event_data = stream_event.get("data", {})

            if event_type == "tool_execution_complete":
                _captured_web_records = event_data.get("web_records", []) or []

            # ── Agent-side citation enrichment ──────────────────────────────────
            # Second-pass fallback: if streaming.py returned empty citations on
            # the complete event, re-run extraction here with web_records support.
            if (
                event_type == "complete"
                and (final_results or _captured_web_records)
                and not event_data.get("citations")
            ):
                _raw_answer = event_data.get("answer", "")
                _enriched: list = []
                if _raw_answer:
                    try:
                        from app.utils.citations import (
                            normalize_citations_and_chunks_for_agent as _ncc_agent,
                        )
                        _ref_to_url = state.get("citation_ref_mapper")
                        _ref_to_url = _ref_to_url.ref_to_url if _ref_to_url else None
                        _, _enriched = _ncc_agent(
                            _raw_answer, final_results, virtual_record_map, [],
                            ref_to_url=_ref_to_url, web_records=_captured_web_records,
                        )
                        if _enriched:
                            log.info(
                                "Citation enrichment (respond_node): "
                                "extracted %d citations from inline markers",
                                len(_enriched),
                            )
                    except Exception as _ce:
                        log.debug("Citation enrichment error: %s", _ce)
                if _enriched:
                    event_data = {**event_data, "citations": _enriched}
            # ────────────────────────────────────────────────────────────────────

            if event_type == "complete" and event_data.get("referenceData"):
                event_data = {
                    **event_data,
                    "referenceData": normalize_reference_data_items(event_data["referenceData"]),
                }

            # if event_type == "complete":
            _emit_ask_user_question_tool_event(writer, state, config)
            safe_stream_write(writer, {"event": event_type, "data": event_data}, config)

            if event_type == "complete":
                answer_text = event_data.get("answer", "")
                citations = event_data.get("citations", [])
                reason = event_data.get("reason")
                confidence = event_data.get("confidence")
                reference_data = event_data.get("referenceData", []) or []

        if not answer_text or len(answer_text.strip()) == 0:
            log.warning("⚠️ Empty response, using fallback")
            answer_text = "I wasn't able to generate a response. Please try rephrasing."

            fallback_response = {
                "answer": answer_text,
                "citations": [],
                "confidence": "Low",
                "answerMatchType": "Fallback Response"
            }
            fallback_response.update(_tool_names_and_results_from_state(state))
            safe_stream_write(writer, {
                "event": "answer_chunk",
                "data": {"chunk": answer_text, "accumulated": answer_text, "citations": []}
            }, config)
            _emit_ask_user_question_tool_event(writer, state, config)
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
                log.debug(f"📎 Stored {len(reference_data)} reference items")
            completion_data.update(_tool_names_and_results_from_state(state))
            state["response"] = answer_text
            state["completion_data"] = completion_data

        log.info(f"✅ Generated response: {len(answer_text)} chars, {len(citations)} citations")

    except Exception as e:
        log.error(f"💥 Response generation failed: {e}", exc_info=True)
        error_msg = "I encountered an issue. Please try again."
        error_response = {
            "answer": error_msg,
            "citations": [],
            "confidence": "Low",
            "answerMatchType": "Error"
        }
        error_response.update(_tool_names_and_results_from_state(state))
        _emit_ask_user_question_tool_event(writer, state, config)
        safe_stream_write(writer, {
            "event": "answer_chunk",
            "data": {"chunk": error_msg, "accumulated": error_msg, "citations": []}
        }, config)
        safe_stream_write(writer, {"event": "complete", "data": error_response}, config)
        state["response"] = error_msg
        state["completion_data"] = error_response

    duration_ms = (time.perf_counter() - start_time) * 1000
    log.info(f"⚡ respond_node: {duration_ms:.0f}ms")

    return state


async def _generate_direct_response(
    state: ChatState,
    llm: BaseChatModel,
    log: logging.Logger,
    writer: StreamWriter,
    config: RunnableConfig,
) -> None:
    """Generate direct response with full conversation context.

    Streams the LLM response to the frontend, sends the completion event,
    and stores the result in state. Fully self-contained — the caller just
    needs to ``return state`` after this returns.

    When ``state["response"]`` is set by a prior node (e.g. ReAct), the full text is
    embedded in the user message; ``stream_llm_response`` still runs so citation
    normalization, chunking, and other streaming behavior stay unchanged.
    """

    query = state.get("query", "")
    previous = state.get("previous_conversations", [])

    _pr = state.get("response")
    prior_react: str | None = _pr if isinstance(_pr, str) and _pr.strip() else None

    # Build messages with full conversation history (same as planner)
    messages = []

    # System message
    user_context = _format_user_context(state)

    # Build instructions prefix if agent has configured instructions
    instructions_prefix = ""
    agent_instructions = state.get("instructions")
    if agent_instructions and agent_instructions.strip():
        instructions_prefix = f"## Agent Instructions\n{agent_instructions.strip()}\n\n"

    base_system_prompt = state.get("system_prompt", "")
    role_prefix = ""
    if is_custom_agent_system_prompt(base_system_prompt):
        role_prefix = f"{base_system_prompt.strip()}\n\n"

    # If the agent has no knowledge and no tools, use a specialized system prompt that
    # always steers the LLM to guide the user to configure the agent for org-specific queries.
    if state.get("agent_not_configured_hint"):
        system_content = (
            f"{instructions_prefix}{role_prefix}"
            "You are a helpful, friendly AI assistant.\n\n"
            "## ⚠️ IMPORTANT: This Agent Is Not Configured\n"
            "This agent has **no knowledge sources** and **no service tools** connected.\n\n"
            "### How to respond:\n"
            "1. **NEVER leak internal terms** like `can_answer_directly`, `needs_clarification`, JSON keys, or planning details into your response. Write naturally.\n"
            "2. **For greetings, math, or clearly general questions**: answer normally and briefly.\n"
            "3. **For ANY question about org-specific data** (licenses, documents, policies, project details, account info, internal systems, product-specific questions, etc.):\n"
            "   - Do NOT speculate, fabricate, or invent org-specific details.\n"
            "   - Do NOT make up license details, account information, or product internals you have no real knowledge of.\n"
            "   - Clearly tell the user: **this agent has no knowledge sources configured**, so you cannot look up their specific information.\n"
            "   - Then guide them: to get accurate answers about their org's data, the agent admin needs to:\n"
            "     * **Add Knowledge Sources** — upload documents, wikis, or connect data sources to this agent.\n"
            "     * **Connect Service Toolsets** — link apps (Google Workspace, Jira, Confluence, Slack, etc.) to enable live actions.\n"
            "   - You may add a brief general explanation from your training knowledge if helpful, but **always make clear it is general knowledge, not their specific data**.\n"
            "4. **Keep responses concise and user-friendly** — the user may not know the agent needs to be set up."
        )
        if user_context:
            system_content += "\n\nIMPORTANT: When user asks about themselves, use provided info DIRECTLY."
    else:
        system_content = (
            f"{instructions_prefix}{role_prefix}"
            "You are a helpful, friendly AI assistant. Respond naturally and concisely.\n\n"
            "⚠️ NEVER expose internal system terms (such as `can_answer_directly`, `needs_clarification`, "
            "`connector_ids`, `collection_ids`, JSON keys, tool names, or planning details) in your response. "
            "Write as if you are naturally conversing with the user.\n\n"
            "⚠️ NEVER ask clarifying questions or present a numbered menu of options. "
            "If the user sends a short topic or keyword, respond with what you know from context. "
            "Do not ask 'what do you mean?' — just respond helpfully."
        )
        if user_context:
            system_content += "\n\nWhen the user asks about themselves, use the provided user info directly."

    if prior_react:
        system_content += (
            "\n\nIf the user message includes a **draft / preliminary assistant response** from the ReAct step "
            "in this turn, your final answer must follow its substance and structure; adjust wording only."
        )

    _DOC_MIME_TYPES = {"application/pdf", "text/plain", "text/markdown", "text/mdx"}
    _has_prev_doc_attachments = any(
        isinstance(att, dict) and (att.get("mimeType") or "").lower() in _DOC_MIME_TYPES
        for conv in previous if conv.get("role") == "user_query"
        for att in (conv.get("attachments") or [])
    )
    if state.get("attachments") or _has_prev_doc_attachments:
        system_content += (
            "\n\n### Citations for Attached Files\n"
            "The attached files contain blocks, each labelled with a **Citation ID** (e.g., `ref1`, `ref2`). "
            "When your answer references specific content from an attached file, cite it by embedding "
            "the Citation ID as a markdown link immediately after the claim: `[source](ref1)`. "
            "Use EXACTLY the Citation ID shown next to each block — do NOT invent or number them yourself. "
            "Omit the citation only when you are unsure which block a fact came from."
        )

    # Add capability summary so direct responses can answer "what can you do?"
    capability_summary = build_capability_summary(state)
    system_content += f"\n\n{capability_summary}"
    system_content += f"\n\n{build_direct_answer_time_context(state)}"
    system_content += (
        "\n\nRender dates/times in human-readable form using the **Time zone** from the Time context "
        "(e.g., 'April 28, 2026 at 3:45 PM IST'). Convert any epoch/numeric or ISO timestamp fields "
        "(`ts`, `timestamp`, `created_at`, `updated_at`, etc.) — never output raw epoch numbers, ISO strings, or `ts`-style columns."
    )

    messages.append(SystemMessage(content=system_content))

    # Add conversation history as LangChain messages (with sliding window)
    _hist_pdf_records: dict[str, dict] = {}
    if previous:
        _ensure_blob_store(state, log)
        if state.get("citation_ref_mapper") is None:
            from app.utils.chat_helpers import CitationRefMapper
            state["citation_ref_mapper"] = CitationRefMapper()
        conversation_messages = await _build_conversation_messages(
            previous, log,
            is_multimodal_llm=state.get("is_multimodal_llm", False),
            blob_store=state.get("blob_store"),
            org_id=state.get("org_id", ""),
            ref_mapper=state.get("citation_ref_mapper"),
            out_records=_hist_pdf_records,
        )
        messages.extend(conversation_messages)
        log.debug(f"Using {len(conversation_messages)} messages from {len(previous)} conversations for direct response (sliding window applied)")

    # Merge historical PDF records into virtual_record_id_to_result so
    # citation normalization can resolve refs from previous-turn attachments.
    if _hist_pdf_records:
        vrmap = state.get("virtual_record_id_to_result")
        if not isinstance(vrmap, dict):
            vrmap = {}
            state["virtual_record_id_to_result"] = vrmap
        for vrid, rec in _hist_pdf_records.items():
            if vrid not in vrmap:
                vrmap[vrid] = rec

    # Current user turn (include full ReAct handoff in full when present; no truncation)
    user_content = query
    if user_context:
        user_content += f"\n\n{user_context}"
    if prior_react:
        user_content = (
            "## Draft output\n\n"
            f"{prior_react}\n\n"
            "---\n\n"
            "## User message\n\n"
            f"{user_content}"
        )
    messages.append(HumanMessage(content=user_content))

    # Ensure attachments resolved (may be called standalone; cache hit if already done)
    # then inject into the query message.
    attachment_blocks = await _ensure_attachment_blocks(state, log)
    _inject_attachment_blocks(messages, attachment_blocks)

    # Reinforce citation requirement at the end of the user message so smaller
    # models (e.g. gpt-5.4-mini) that under-follow system-prompt instructions
    # still produce citations for every claim drawn from the attached blocks.
    if attachment_blocks or _hist_pdf_records:
        last_msg = messages[-1]
        if isinstance(last_msg, HumanMessage):
            reminder = (
                "\n\n**Reminder**: For answer that comes from the "
                "attached blocks above, you MUST include citations using the exact Citation IDs "
                "shown for that block (e.g., `[source](ref1)`)."
            )
            if isinstance(last_msg.content, list):
                last_msg.content.append({"type": "text", "text": reminder})
            else:
                last_msg.content = str(last_msg.content) + reminder

    answer_text = ""
    citations: list = []

    _ref_mapper = state.get("citation_ref_mapper")
    _ref_to_url = _ref_mapper.ref_to_url if _ref_mapper is not None else None
    _vr_map = state.get("virtual_record_id_to_result") or {}

    try:
        async for stream_event in stream_llm_response(
            llm=llm,
            messages=messages,
            final_results=[],
            logger=log,
            target_words_per_chunk=1,
            virtual_record_id_to_result=_vr_map,
            ref_to_url=_ref_to_url,
        ):
            event_type = stream_event.get("event")
            event_data = stream_event.get("data", {})

            if event_type == "complete":
                _emit_ask_user_question_tool_event(writer, state, config)
            safe_stream_write(writer, {"event": event_type, "data": event_data}, config)

            if event_type == "complete":
                answer_text = event_data.get("answer", "")
                citations = event_data.get("citations", [])

    except Exception as e:
        log.error("Direct response failed: %s", e, exc_info=True)
        answer_text = "I'm here to help! How can I assist you today?"
        safe_stream_write(writer, {
            "event": "answer_chunk",
            "data": {"chunk": answer_text, "accumulated": answer_text, "citations": []},
        }, config)
        _emit_ask_user_question_tool_event(writer, state, config)
        safe_stream_write(writer, {
            "event": "complete",
            "data": {"answer": answer_text, "citations": [], "confidence": "Low"},
        }, config)

    answer = answer_text.strip() or "I'm here to help! How can I assist you today?"

    # If the LLM produced no text the first complete event above had answer="".
    # Re-emit with the fallback so Node.js validation passes. respond_node already
    # does this; _generate_direct_response must be consistent.
    if not answer_text.strip():
        safe_stream_write(writer, {
            "event": "complete",
            "data": {"answer": answer, "citations": [], "confidence": "Low"},
        }, config)

    state["response"] = answer
    state["completion_data"] = {
        "answer": answer,
        "citations": citations,
        "confidence": "High",
        "answerMatchType": "Direct Response",
    }
    state["completion_data"].update(_tool_names_and_results_from_state(state))


async def _generate_fast_api_response(
    state: ChatState,
    llm: BaseChatModel,
    query: str,
    tool_results: list[dict],
    sub_agent_analyses: list[str],
    log: logging.Logger,
    writer: StreamWriter,
    config: RunnableConfig,
) -> bool:
    """
    Fast-path response for API-only results with sub-agent analyses.

    Instead of the full stream_llm_response_with_tools pipeline (which includes
    tool execution capability, citation extraction, etc.), this uses a lightweight
    streaming LLM call that takes the sub-agent's pre-analyzed data and formats
    it as the final response.

    Returns True if response was generated successfully, False to fall back.
    """
    # Build a concise prompt with the sub-agent analysis and raw data
    analyses_text = "\n\n".join(sub_agent_analyses)

    # Include raw API data for reference

    non_retrieval = [
        r for r in tool_results
        if r.get("status") == "success"
        and "retrieval" not in r.get("tool_name", "").lower()
    ]
    ref_mapper = state.get("citation_ref_mapper")
    raw_data_parts = []
    for r in non_retrieval[:5]:
        tool_name = r.get("tool_name", "unknown")
        content = ToolResultExtractor.extract_data_from_result(r.get("result", ""))
        
        if isinstance(content, (dict, list)):
            content_str = json.dumps(content, indent=2, default=str)
        else:
            content_str = str(content)
        if len(content_str) > _RAW_DATA_SIZE_LIMIT:
            content_str = content_str[:_RAW_DATA_SIZE_LIMIT] + "\n... (truncated)"
        raw_data_parts.append(f"### {tool_name}\n```json\n{content_str}\n```")

    raw_data_text = "\n\n".join(raw_data_parts) if raw_data_parts else ""

    # Build instructions prefix if agent has configured instructions
    instructions_prefix = ""
    agent_instructions = state.get("instructions")
    if agent_instructions and agent_instructions.strip():
        instructions_prefix = f"## Agent Instructions\n{agent_instructions.strip()}\n\n"

    base_system_prompt = state.get("system_prompt", "")
    role_prefix = ""
    if is_custom_agent_system_prompt(base_system_prompt):
        role_prefix = f"{base_system_prompt.strip()}\n\n"

    system_prompt = (
        f"{instructions_prefix}{role_prefix}"
        "You are an expert data analyst producing comprehensive, detailed reports.\n\n"
        "You will receive:\n"
        "1. **Sub-Agent Analysis** — pre-analyzed, structured findings from specialized agents "
        "that have already studied the raw data in depth\n"
        "2. **Raw API Data** — the original data for cross-referencing and extracting additional "
        "details (links, exact values) that the analysis may reference\n\n"
        "## Objective\n"
        "Produce a thorough, detailed response — NOT a brief summary. The user expects "
        "deep analysis with every relevant data point preserved.\n\n"
        "## Quality Standards\n"
        "- **Comprehensive**: Include EVERY item, finding, and data point from the analysis. "
        "Do not drop, skip, or summarize away any items\n"
        "- **Accurate**: Cross-reference the sub-agent analysis with raw API data to verify "
        "details and extract additional information (exact timestamps, IDs, URLs, emails)\n"
        "- **Specific**: Use exact values — dates, times, names, emails, statuses, priorities, "
        "counts. Never use vague quantifiers ('several', 'multiple', 'some') when exact "
        "counts are available\n"
        "- **Linked**: Include ALL clickable URLs found in BOTH the analysis AND raw data. "
        "Format as `[Title](url)`. Links are mandatory for every item that has one\n"
        "- **Well-structured**: Use tables for list data, headers for sections, bullet points "
        "for details. Choose the format that best presents each type of data\n"
        "- **Actionable**: Surface critical items prominently (overdue, high-priority, errors, "
        "action required). Include follow-ups and recommendations where supported by data\n"
        "- **No fabrication**: Only use data that is explicitly provided\n"
        "- **Human-readable dates/times**: render every date/time using the **Time zone** from the Time context "
        "(e.g., 'April 28, 2026 at 3:45 PM IST'). Convert any epoch/numeric or ISO timestamp fields "
        "(`ts`, `timestamp`, `created_at`, `updated_at`, etc.) — never output raw epoch numbers, ISO strings, or `ts`-style columns\n"
        "- Output ONLY markdown — no JSON wrapper, no code fences around the whole response\n"
    )

    user_content = f"**User Query**: {query}\n\n"
    user_content += f"## Sub-Agent Analysis\n{analyses_text}\n\n"
    if raw_data_text:
        user_content += (
            f"## Raw API Data (for cross-referencing and extracting exact details)\n"
            f"{raw_data_text}\n\n"
        )
    user_content += (
        "Produce a comprehensive, detailed markdown response. Preserve ALL items and data points "
        "from the analysis. Cross-reference with raw data for accuracy and links. Do NOT wrap in JSON."
    )

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_content),
    ]

    full_content = ""
    reference_data = []
    virtual_record_id_to_result = state.get("virtual_record_id_to_result") or {}
    ref_to_url = state.get("citation_ref_mapper") and state.get("citation_ref_mapper").ref_to_url or {}
    try:
        async for stream_event in stream_llm_response(
            llm=llm,
            messages=messages,
            final_results=[],
            logger=log,
            target_words_per_chunk=1,
            virtual_record_id_to_result=virtual_record_id_to_result,
            ref_to_url=ref_to_url,
        ):
            event_type = stream_event.get("event")
            event_data = stream_event.get("data", {})

            if event_type == "complete":
                # Capture the full answer but don't forward — we emit our own complete event below
                full_content = event_data.get("answer", "")
            else:
                safe_stream_write(writer, {"event": event_type, "data": event_data}, config)

    except Exception as e:
        log.error("Fast API response generation failed: %s", e, exc_info=True)
        full_content = ""

    if not full_content.strip():
        return False

    # Extract reference data (links) from the raw tool results
    reference_data = []
    for r in non_retrieval:
        content = r.get("result", "")
        _extract_urls_for_reference_data(content, reference_data)

    answer_text = full_content.strip()
    conversation_id = state.get("conversation_id")
    if conversation_id:
        try:
            from app.utils.conversation_tasks import await_and_collect_results
            from app.utils.streaming import _append_task_markers
            task_results = await await_and_collect_results(conversation_id)
            answer_text = _append_task_markers(answer_text, task_results)
        except Exception as e:
            log.warning("Fast-path: conversation tasks failed: %s", e)

    completion_data = {
        "answer": answer_text,
        "citations": [],
        "confidence": "High",
        "answerMatchType": "Derived From Tool Execution",
    }
    if reference_data:
        completion_data["referenceData"] = reference_data
    completion_data.update(_tool_names_and_results_from_state(state))
    _emit_ask_user_question_tool_event(writer, state, config)
    safe_stream_write(writer, {"event": "complete", "data": completion_data}, config)
    state["response"] = answer_text
    state["completion_data"] = completion_data
    return True


def _extract_web_records_from_tool_results(
    tool_results: list[dict], org_id: str,
) -> list[dict]:
    """Build web_records from web_search / fetch_url tool results that were
    executed in the agent's execution phase (before respond_node).

    Delegates to ToolHandlerRegistry.extract_records — the same path that
    streaming.py's execute_tool_calls uses for live tool output — so citation
    URLs are generated identically (text-fragment URLs for fetch_url blocks,
    plain links for web_search snippets).
    """
    from app.utils.tool_handlers import ToolHandlerRegistry

    web_records: list[dict] = []
    for r in tool_results:
        if r.get("status") != "success":
            continue
        result = r.get("result")
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except (json.JSONDecodeError, ValueError):
                continue
        if not isinstance(result, dict):
            continue
        handler = ToolHandlerRegistry.get_handler(result)
        for rec in handler.extract_records(result, org_id=org_id):
            if rec.get("source_type") == "web":
                web_records.append(rec)
    return web_records


def _extract_urls_for_reference_data(content: object, reference_data: list[dict]) -> None:
    """Extract URLs from tool result content and add to referenceData list."""
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except (json.JSONDecodeError, ValueError):
            return

    if isinstance(content, dict):
        for key, value in content.items():
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                # Found a URL — add to reference data if not already present
                if not any(rd.get("webUrl") == value for rd in reference_data):
                    name = content.get("subject") or content.get("title") or content.get("name") or content.get("key") or key
                    reference_data.append({"name": str(name), "webUrl": value, "type": key})
            elif isinstance(value, (dict, list)):
                _extract_urls_for_reference_data(value, reference_data)
    elif isinstance(content, list):
        for item in content[:20]:  # Safety limit
            _extract_urls_for_reference_data(item, reference_data)

def _tool_names_and_results_from_state(state: ChatState) -> Dict[str, Any]:
    """Derive succeeded/failed tool names and full tool results from state (no separate state fields)."""
    results = state.get("all_tool_results") or state.get("tool_results") or []
    succeeded = [r.get("tool_name") for r in results if r.get("tool_name") and r.get("status") == "success"]
    failed = [r.get("tool_name") for r in results if r.get("tool_name") and r.get("status") == "error"]
    return {
        "succeeded_tool_names": succeeded,
        "failed_tool_names": failed,
        "tool_results": results,
    }

_ASK_USER_QUESTION_TOOL_NAMES = frozenset({
    "internaltools_ask_user_question",
    "internaltools.ask_user_question",
    # Legacy typo in older prompts / logs
    
})

def _emit_ask_user_question_tool_event(
    writer: StreamWriter,
    state: ChatState,
    config: RunnableConfig,
) -> None:
    """
    Emit a dedicated ``ask_user_question`` SSE event carrying the full structured
    payload (questions with UUIDs and option IDs) so the frontend can render
    interactive option cards. Called immediately before the final ``complete`` event.

    Only emitted when the request was made by a recognised client (i.e. the
    ``client-name`` header was present and forwarded via ``config["configurable"]``).
    This prevents the event from being emitted in programmatic / API-only callers
    that have no UI to render interactive cards.
    """
    client_name = (config.get("configurable") or {}).get("client_name")
    if not client_name:
        return

    # Already emitted eagerly from _execute_sequential — skip to avoid
    # sending the same event twice before the complete event.
    if state.get("ask_user_question_emitted"):
        return

    for row in _tool_names_and_results_from_state(state).get("tool_results") or []:
        tname = row.get("tool_name") or ""
        if tname not in _ASK_USER_QUESTION_TOOL_NAMES:
            continue
        raw_result = row.get("result", "")
        try:
            payload = json.loads(raw_result) if isinstance(raw_result, str) else raw_result
        except (json.JSONDecodeError, TypeError):
            payload = raw_result
        safe_stream_write(
            writer,
            {
                "event": "ask_user_question",
                "data": {
                    "status": row.get("status"),
                    "toolData": payload,
                },
            },
            config,
        )
        state["ask_user_question_emitted"] = True



# ============================================================================
# ROUTING FUNCTIONS
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


def route_after_reflect(state: ChatState) -> Literal["prepare_retry", "prepare_continue", "respond"]:
    """Route based on reflection decision"""
    decision = state.get("reflection_decision", "respond_success")
    retry_count = state.get("retry_count", 0)
    max_retries = state.get("max_retries", NodeConfig.MAX_RETRIES)
    iteration_count = state.get("iteration_count", 0)
    max_iterations = state.get("max_iterations", NodeConfig.MAX_ITERATIONS)

    if decision == "retry_with_fix" and retry_count < max_retries:
        return "prepare_retry"

    if decision == "continue_with_more_tools" and iteration_count < max_iterations:
        return "prepare_continue"

    return "respond"


def check_for_error(state: ChatState) -> Literal["error", "continue"]:
    """Check for errors"""
    return "error" if state.get("error") else "continue"

# ============================================================================
# Modern ReAct Agent Node (with Cascading Tool Support)
# ============================================================================

def _process_retrieval_output(result: object, state: ChatState, log: logging.Logger) -> str:
    """Process retrieval tool output (accumulates results from multiple retrieval calls)"""
    try:
        # Fast path: tool already wrote to state and returned pre-formatted content
        if isinstance(result, str) and "<record>" in result:
            log.info("Retrieval returned pre-formatted content (state already updated by tool)")
            return result

        from app.agents.actions.retrieval.retrieval import RetrievalToolOutput

        # Legacy/fallback path: parse JSON and extract data
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
            # Accumulate final_results instead of overwriting (for parallel retrieval calls)
            existing_final_results = state.get("final_results", [])
            if not isinstance(existing_final_results, list):
                existing_final_results = []

            # Combine new results with existing ones
            new_final_results = retrieval_output.final_results or []
            combined_final_results = existing_final_results + new_final_results
            state["final_results"] = combined_final_results

            # Accumulate virtual_record_id_to_result
            existing_virtual_map = state.get("virtual_record_id_to_result", {})
            if not isinstance(existing_virtual_map, dict):
                existing_virtual_map = {}

            new_virtual_map = retrieval_output.virtual_record_id_to_result or {}
            combined_virtual_map = {**existing_virtual_map, **new_virtual_map}
            state["virtual_record_id_to_result"] = combined_virtual_map

            # Accumulate tool_records
            if retrieval_output.virtual_record_id_to_result:
                existing_tool_records = state.get("tool_records", [])
                if not isinstance(existing_tool_records, list):
                    existing_tool_records = []

                new_tool_records = list(retrieval_output.virtual_record_id_to_result.values())
                # Avoid duplicates by checking record IDs
                existing_record_ids = {rec.get("_id") for rec in existing_tool_records if isinstance(rec, dict) and "_id" in rec}
                unique_new_records = [
                    rec for rec in new_tool_records
                    if not (isinstance(rec, dict) and rec.get("_id") in existing_record_ids)
                ]
                combined_tool_records = existing_tool_records + unique_new_records
                state["tool_records"] = combined_tool_records

            log.info(f"Retrieved {len(new_final_results)} knowledge blocks (total: {len(combined_final_results)})")
            return retrieval_output.content

    except Exception as e:
        log.warning(f"Could not process retrieval output: {e}")

    return str(result)


def _detect_tool_result_status(result_content: object) -> str:
    """
    Detect whether a tool result indicates success or error.

    Parses the tool result content (string or dict) and checks for
    error indicators. Returns "success" or "error".
    """
    try:
        # Parse JSON string to dict if needed
        parsed = result_content
        if isinstance(result_content, str):
            try:
                parsed = json.loads(result_content)
            except (json.JSONDecodeError, ValueError):
                # Not JSON — check for error keywords in raw string
                lower_content = result_content.lower()[:500]
                if any(marker in lower_content for marker in [
                    "error executing tool",
                    '"status": "error"',
                    "authentication failed",
                    "permission denied",
                    "unauthorized",
                    "403 forbidden",
                    "404 not found",
                    "500 internal server error",
                ]):
                    return "error"
                return "success"

        # Check dict-style results
        if isinstance(parsed, dict):
            # Check explicit status field
            status = parsed.get("status", "")
            if isinstance(status, str) and status.lower() == "error":
                return "error"

            # Check for error key
            if parsed.get("error"):
                return "error"

            # Check for success=False pattern (tuple-style result)
            if parsed.get("success") is False:
                return "error"

        # Check tuple-style: (False, "error message")
        if isinstance(parsed, (list, tuple)) and len(parsed) >= TOOL_RESULT_TUPLE_LENGTH and parsed[0] is False:
            return "error"

    except Exception:
        pass  # If we can't parse it, assume success

    return "success"


# =============================================================================
# ReAct Agent Streaming Callback
# =============================================================================

class _ToolStreamingCallback(AsyncCallbackHandler):
    """
    Callback handler that streams tool execution events to the frontend
    via the outer graph's StreamWriter.

    Used with agent.ainvoke() to stream real-time tool status without
    the context conflicts that occur with nested agent.astream() calls.
    """

    def __init__(self, writer: StreamWriter, config: RunnableConfig, log: logging.Logger) -> None:
        super().__init__()
        self.writer = writer
        self.config = config
        self.log = log
        self._tool_names: dict[str, str] = {}  # run_id -> tool_name

    def _write_event(self, event_data: dict[str, Any]) -> bool:
        """Write event to the outer graph's stream with context restoration."""
        token = var_child_runnable_config.set(self.config)
        try:
            self.writer(event_data)
            return True
        except Exception as e:
            self.log.debug(f"Stream callback write failed: {e}")
            return False
        finally:
            var_child_runnable_config.reset(token)

    async def on_tool_start(
        self, serialized: dict[str, Any], input_str: str, *, run_id: UUID, **kwargs: object
    ) -> None:
        tool_name = serialized.get("name", kwargs.get("name", "unknown"))
        self._tool_names[str(run_id)] = tool_name
        status_msg = _get_tool_status_message(tool_name)
        self.log.info(f"Streaming tool start: {tool_name} -> {status_msg}")
        self._write_event({
            "event": "status",
            "data": {"status": "executing", "message": status_msg}
        })

    async def on_tool_end(self, output: object, *, run_id: UUID, **kwargs: object) -> None:
        tool_name = self._tool_names.pop(str(run_id), kwargs.get("name", "unknown"))
        tool_status = _detect_tool_result_status(output)
        result_preview = str(output)[:MAX_TOOL_RESULT_PREVIEW_LENGTH]
        self.log.info(f"Streaming tool end: {tool_name} -> {tool_status}")

        if tool_status == "error":
            action_readable = tool_name.split(".", 1)[-1].replace("_", " ")
            self._write_event({
                "event": "status",
                "data": {
                    "status": "executing",
                    "message": f"Retrying {action_readable} with corrected parameters..."
                }
            })

        self._write_event({
            "event": "tool_result",
            "data": {
                "tool": tool_name,
                "result": result_preview,
                "status": tool_status,
            }
        })

    async def on_tool_error(self, error: BaseException, *, run_id: UUID, **kwargs: object) -> None:
        tool_name = self._tool_names.pop(str(run_id), kwargs.get("name", "unknown"))
        error_msg = str(error)[:200]
        self.log.info(f"Streaming tool error: {tool_name} -> {error_msg}")
        self._write_event({
            "event": "status",
            "data": {
                "status": "executing",
                "message": f"Error in {tool_name.replace('_', ' ')}: {error_msg}. Retrying..."
            }
        })


async def react_agent_node(
    state: ChatState,
    config: RunnableConfig,
    writer: StreamWriter
) -> ChatState:
    """
    ReAct agent node with cascading tool execution support.

    This node uses LangChain's create_agent which naturally handles
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
        from langchain_core.messages import ToolMessage

        from app.modules.agents.qna.tool_system import get_agent_tools_with_schemas

        safe_stream_write(writer, {
            "event": "status",
            "data": {"status": "planning", "message": "Analyzing your request and planning actions..."}
        }, config)

        # Get tools with Pydantic schemas
        tools = get_agent_tools_with_schemas(state)
        log.info(f"ReAct agent loaded {len(tools)} tools with schemas")

        # Stream tool count info
        tool_names_for_log = [getattr(t, 'name', str(t)) for t in tools[:_TOOL_LOG_LIMIT]]
        log.info(f"Available tools: {tool_names_for_log}{'...' if len(tools) > _TOOL_LOG_LIMIT else ''}")

        # Build system prompt
        system_prompt = _build_react_system_prompt(state, log)

        # ReAct agent via langchain.agents (create_react_agent moved here from langgraph.prebuilt)
        from langchain.agents import create_agent

        agent = create_agent(
            llm,
            tools,
            system_prompt=system_prompt,
        )

        # Build message history with conversation context (same as planner path).
        # This is critical for follow-ups like "yes execute" where parameters were
        # provided in previous turns.
        messages = await _build_planner_messages(state, query, log)

        # Resolve attachments (react graph entry point — planner_node does not run here)
        # and inject into the query message so the LLM has full visual context.
        attachment_blocks = await _ensure_attachment_blocks(state, log)
        _inject_attachment_blocks(messages, attachment_blocks)

        # Execute agent with callback-based streaming.
        # We use ainvoke() + AsyncCallbackHandler instead of astream() because
        # astream() creates a nested LangGraph execution context that conflicts
        # with the outer graph's StreamWriter, preventing tool status events from
        # reaching the frontend SSE stream. Callbacks are invoked during tool
        # execution and are orthogonal to the graph streaming mechanism.
        tool_results = []

        # Create streaming callback that writes tool events to the outer stream
        streaming_cb = _ToolStreamingCallback(writer, config, log)

        # Use a clean config for the inner agent to avoid context conflicts
        # with the outer graph. Only pass recursion_limit and callbacks.
        react_callbacks = [streaming_cb]
        if _opik_tracer:
            react_callbacks.append(_opik_tracer)
        agent_config = {
            "recursion_limit": 50,
            "callbacks": react_callbacks,
        }

        keepalive_task = asyncio.create_task(
            send_keepalive(writer, config, "Processing with tools...")
        )
        try:
            result = await agent.ainvoke(
                {"messages": messages},
                config=agent_config,
            )
        finally:
            keepalive_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await keepalive_task

        # Extract messages from the agent result
        final_messages = result.get("messages", [])
        log.debug(f"ReAct agent returned {len(final_messages)} messages")

        # Process tool results from final messages
        for msg in final_messages:
            if isinstance(msg, ToolMessage):
                tool_name = msg.name if hasattr(msg, 'name') else "unknown"
                result_content = msg.content

                # Parse JSON strings back to dicts so downstream code
                # (_build_tool_results_context, _extract_web_records_from_tool_results)
                # can access structured fields like result_type, blocks, web_results.
                if isinstance(result_content, str):
                    try:
                        result_content = json.loads(result_content)
                    except (json.JSONDecodeError, ValueError):
                        pass

                # Process retrieval tool results to extract final_results
                if "retrieval" in tool_name.lower():
                    _process_retrieval_output(result_content, state, log)

                # Detect actual tool success/failure from result content
                tool_status = _detect_tool_result_status(result_content)
                log.info("📌 ReAct tool status: %s | status=%s", tool_name, tool_status)

                tool_results.append({
                    "tool_name": tool_name,
                    "status": tool_status,
                    "result": result_content,
                    "tool_call_id": getattr(msg, 'tool_call_id', None),
                })

        # Stream analyzing status before response generation
        if tool_results:
            safe_stream_write(writer, {
                "event": "status",
                "data": {"status": "analyzing", "message": "Analyzing results and preparing response..."}
            }, config)

        # Get retrieval results (internal knowledge) - may have been populated by retrieval tool
        final_results = state.get("final_results", [])
        has_retrieval = bool(final_results)

        # Extract final response from messages
        response = _extract_final_response(final_messages, log)

        # Determine reflection decision based on tool results
        error_count = sum(1 for r in tool_results if r.get("status") == "error")
        success_count = sum(1 for r in tool_results if r.get("status") == "success")
        total_tools = len(tool_results)

        if total_tools == 0:
            # No tools called — direct answer
            reflection_decision = "respond_success"
            reflection_reasoning = "ReAct agent answered directly without tool calls."
        elif error_count == 0:
            reflection_decision = "respond_success"
            reflection_reasoning = f"All {success_count} tool call(s) succeeded."
        elif success_count > 0:
            reflection_decision = "respond_success"
            reflection_reasoning = f"{success_count}/{total_tools} tool calls succeeded, {error_count} failed. Partial results available."
        else:
            reflection_decision = "respond_error"
            reflection_reasoning = f"All {error_count} tool call(s) failed."

        # Hand off final formatting to respond_node so output matches chatbot format.
        # react_agent_node only performs tool orchestration and state preparation.
        state["response"] = response
        state["tool_results"] = tool_results
        state["all_tool_results"] = tool_results
        state["reflection_decision"] = reflection_decision
        state["reflection"] = {
            "decision": reflection_decision,
            "confidence": "High" if error_count == 0 else "Medium",
            "reasoning": reflection_reasoning,
        }

        execution_plan = state.get("execution_plan") or {}
        # When react agent answered directly without calling any tools (e.g.
        # capability questions, greetings), let respond_node use the
        # _generate_direct_response path which has the capability summary and
        # user context.  When tools WERE called, keep can_answer_directly=False
        # so the full synthesis pipeline runs with citations and tool results.
        execution_plan["can_answer_directly"] = (total_tools == 0)
        state["execution_plan"] = execution_plan

        duration_ms = (time.perf_counter() - start_time) * 1000
        log.info(
            f"⚡ ReAct Agent: {duration_ms:.0f}ms, {total_tools} tool calls "
            f"({success_count} success, {error_count} errors)"
        )
        log.info(
            "ReAct handoff -> respond_node | decision=%s | can_answer_directly=%s | has_retrieval=%s | response_len=%d",
            reflection_decision,
            execution_plan["can_answer_directly"],
            has_retrieval,
            len(response or ""),
        )

    except ImportError as e:
        log.error(f"ReAct agent dependencies not available: {e}")
        state["error"] = {
            "status": "error",
            "message": "ReAct agent is not available. Please use the standard agent.",
            "status_code": 500,
        }
    except Exception as e:
        log.error(f"ReAct agent error: {e}", exc_info=True)
        state["error"] = {
            "status": "error",
            "message": f"I encountered an error: {str(e)}",
            "status_code": 500,
        }

    return state


def _build_tool_schema_reference(state: ChatState, log: logging.Logger) -> str:
    """
    Build a concise tool schema reference for inclusion in the ReAct system prompt.

    This gives the LLM visibility into required vs optional parameters directly
    in the prompt text, enabling chain-of-thought reasoning about parameter
    validation before tool calls.
    """
    try:
        from app.modules.agents.qna.tool_system import get_agent_tools_with_schemas

        tools = get_agent_tools_with_schemas(state)
        if not tools:
            return ""

        lines = ["## Tool Schema Quick Reference\n"]
        lines.append("Use this reference to validate parameters before every tool call.\n")

        for tool in tools[:30]:  # Limit to prevent prompt bloat
            name = getattr(tool, 'name', str(tool))
            lines.append(f"### {name}")

            schema = getattr(tool, 'args_schema', None)
            if schema:
                params_info = _extract_parameters_from_schema(schema, log)
                if params_info:
                    required_parts = []
                    optional_parts = []
                    for pname, pinfo in params_info.items():
                        ptype = pinfo.get("type", "any")
                        desc = pinfo.get("description", "")
                        short_desc = (desc[:_PARAM_DESC_TRUNCATE] + "...") if len(desc) > _PARAM_DESC_TRUNCATE else desc
                        entry = f"`{pname}` ({ptype})"
                        if short_desc:
                            entry += f": {short_desc}"
                        if pinfo.get("required"):
                            required_parts.append(entry)
                        else:
                            optional_parts.append(entry)

                    if required_parts:
                        lines.append("  **Required**: " + "; ".join(required_parts))
                    if optional_parts:
                        lines.append("  **Optional**: " + "; ".join(optional_parts))
                else:
                    lines.append("  (no parameters)")
            else:
                lines.append("  (no schema available)")
            lines.append("")

        return "\n".join(lines)

    except Exception as e:
        log.warning(f"Failed to build tool schema reference: {e}")
        return ""




def _build_react_system_prompt(state: ChatState, log: logging.Logger) -> str:
    """Build system prompt for ReAct agent with enhanced reasoning and error recovery"""
    # Start with agent instructions if provided
    agent_instructions = state.get("instructions")
    instructions_prefix = ""
    if agent_instructions and agent_instructions.strip():
        instructions_prefix = f"## Agent Instructions\n{agent_instructions.strip()}\n\n"

    persona = state.get("system_prompt")
    role_prefix = ""
    if is_custom_agent_system_prompt(persona):
        role_prefix = f"{persona.strip()}\n\n"

    base_prompt = instructions_prefix + role_prefix + REACT_BASE_PROMPT

    # ── Build Available Tools section with full schemas ──────────────────────
    # This is the authoritative tool reference the LLM uses for parameter validation.
    # It mirrors what the planner gets via {available_tools} in PLANNER_SYSTEM_PROMPT.
    tool_descriptions = _get_cached_tool_descriptions(state, log)
    if tool_descriptions:
        base_prompt += "\n## Available Tools (VALIDATE EVERY WRITE TOOL CALL AGAINST THESE SCHEMAS)\n\n"
        base_prompt += (
            "Each tool below lists its **required** and **optional** parameters with types.\n"
            "Before calling any WRITE tool, find it here and verify ALL **required** parameters\n"
            "have concrete values from the user or context — not guesses, not placeholders.\n"
            "For READ tools, required params usually have reasonable defaults — proceed directly.\n\n"
        )
        base_prompt += tool_descriptions
    else:
        # Fallback: use the compact schema reference if full descriptions aren't available
        tool_schema_ref = _build_tool_schema_reference(state, log)
        if tool_schema_ref:
            base_prompt += "\n## Available Tools (VALIDATE EVERY WRITE TOOL CALL AGAINST THESE SCHEMAS)\n\n"
            base_prompt += (
                "Each tool below lists its **required** and **optional** parameters with types.\n"
                "Before calling any WRITE tool, find it here and verify ALL **required** parameters\n"
                "have concrete values from the user or context — not guesses, not placeholders.\n"
                "For READ tools, required params usually have reasonable defaults — proceed directly.\n\n"
            )
            base_prompt += tool_schema_ref

    # ── Check for retrieval results and add citation instructions ────────────
    final_results = state.get("final_results", [])
    has_retrieval = bool(final_results)
    has_attachments = bool(state.get("attachments"))

    has_web_search = bool(state.get("web_search_config"))

    if has_retrieval or has_attachments:
        base_prompt += """
## Citation Rules

When you have internal knowledge from retrieval tools:
1. Cite key facts inline: "Revenue grew 29% [source](ref5)." Focus on the most important claims — do NOT cite every sentence.
2. Use the EXACT Citation ID from the context as a markdown link: [source](ref1). Do NOT manually number citations — the system assigns numbers automatically.
3. One citation per markdown link. Do NOT club multiple Citation IDs in one link.
4. Limit to the most relevant citations overall.
5. Do NOT put citations at end of paragraph — inline after the specific fact
6. If you cannot find the Citation ID for a fact, omit the citation rather than guessing.
"""

    if has_web_search:
        base_prompt += """
## Web Search Rules

- Prefer `web_search` over training data for anything that may have changed: news, prices, weather, sports, stocks, software versions, docs, regulations, current events.
- Also prefer `web_search` when user asks for "latest", "current", or "up-to-date" info.
- Prefer `web_search` for general/public knowledge queries: product recommendations, comparisons, reviews, health/medical info, consumer advice, market research, "best X" queries, travel, recipes, scientific research.
- Use training data only for timeless knowledge (math, science, core concepts). When in doubt, prefer `web_search`.
- When a query could have BOTH internal AND external relevance, use BOTH `retrieval.search_internal_knowledge` AND `web_search` in parallel.
- **MANDATORY**: If the available context or retrieval results do NOT contain sufficient information to answer the user's question, you MUST use `web_search` to find relevant information BEFORE telling the user that you don't have enough information or context.
- Cite web results as [source](URL/citation id). Use EXACTLY the URL/citation id shown.
"""

    # ── Hybrid search strategy ──────────────────────────────────────────────
    has_knowledge = state.get("has_knowledge", False)
    has_service_tools = any([
        _has_jira_tools(state),
        _has_confluence_tools(state),
        _has_onedrive_tools(state),
        _has_outlook_tools(state),
        _has_slack_tools(state),
        _has_teams_tools(state),
        _has_github_tools(state),
        _has_clickup_tools(state),
    ])

    if has_knowledge and has_service_tools:
        base_prompt += """
## Hybrid Search Strategy (MANDATORY DEFAULT)

You have BOTH a knowledge base (`retrieval.search_internal_knowledge`) AND live service API tools.
**Default behavior for ANY topic / information query: call BOTH in PARALLEL on your first turn.**
This is not optional — indexed snapshots and live API data are complementary, and combining them
gives users both historical context and current state in one answer. Treat single-source answers
as a degraded fallback only used when one of the rules below explicitly applies.
"""
        base_prompt += """
### When to use BOTH retrieval + service tools (DEFAULT for topic queries):
- **Any topic about an indexed service** — e.g., "holiday policy", "Project X updates", "onboarding doc".
  Call `retrieval.search_internal_knowledge` AND the matching service search tool (e.g.
  `confluence.search_content`, `jira.search_issues`) IN PARALLEL.
- **Query mentions a service AND a topic** — e.g., "holidays from Confluence", "Jira tickets about login".
  Service mention narrows the API tool; it does NOT excuse you from also calling retrieval.
- **Benefit**: Indexed content covers historical and cross-service context; the live API has the most
  current data. The user gets the union.

**Live-only exceptions:** Slack, Outlook, Gmail, and Calendar are live-only services. Do NOT pair them with retrieval — for those, use the service tool alone (see the per-service rules later in this prompt: R-SLACK-1, R-OUT-1, etc.).

### When to use ONLY service tools (no retrieval):
- **Live data requests**: "Show my calendar for today", "List my unread emails", "Get my Jira tickets".
  Real-time-only data — retrieval has nothing to add.
- **Action requests**: "Create a page", "Send an email", "Update a ticket". Write operations.
- **Specific resource requests**: "Get page 12345", "Show event details for tomorrow's standup".

### When to use ONLY retrieval (no service tools):
- The agent has no service tool that matches the query's domain.
- Cross-service summaries where no single live API would have the full picture.
"""
        if has_web_search:
            base_prompt += """
### When to use `web_search`:
- Current/changing public info (news, prices, weather, software versions, regulations) or "latest"/"current" requests.
- When you suspect internal knowledge is incomplete on a public-knowledge question — combine with retrieval.
"""
        base_prompt += """
### How to merge hybrid results:
1. Call the appropriate tools (retrieval + service API"""
        if has_web_search:
            base_prompt += " + web_search as needed"
        base_prompt += """) — IN PARALLEL where possible.
2. Present a unified answer combining insights from all sources.
3. For internal knowledge (retrieval): cite as [source](ref1) using the Citation ID from the context blocks.
"""
        if has_web_search:
            base_prompt += (
                "4. For web search/fetch_url results: cite as [source](URL/citation id) using the URL/citation id.\n"
                "5. Clearly attribute live API data (e.g., \"According to your Outlook calendar...\" or \"From Confluence...\").\n"
            )
        else:
            base_prompt += "4. Clearly attribute live API data (e.g., \"According to your Outlook calendar...\" or \"From Confluence...\").\n"

    elif has_service_tools and not has_knowledge:
        base_prompt += """
## Service-Tool Search Strategy (MANDATORY DEFAULT)

This agent has live service search tools available but **no knowledge base** is configured
(`retrieval.search_internal_knowledge` is unavailable). Treat the available service search tools
as your **primary search surface** for any topic, information, or org-knowledge query.

### Default behavior for ANY topic / information / org-knowledge query:
- Call the matching service search tool(s) on your **first turn**. Do NOT ask the user which
  app or source — they typically don't know which system holds the answer, and you should
  search proactively. Pick tools by matching the query against each tool's `when_to_use`
  description in the Available Tools section.
- If multiple tools could plausibly contain the answer, call them **IN PARALLEL** in the same
  turn — the union gives the user the best result.

### Specifically forbidden when service search tools are available:
- ❌ Asking "which app / source / system did you mean?" before searching. Search first; ask
  for clarification ONLY after a search returns ambiguous or empty results.
- ❌ Concluding "I don't have that information" or "no knowledge base is configured" without
  first attempting a search with the available service tools.
- ❌ Requiring the user to mention an app by name. A query about org-knowledge is implicitly
  a search query — each tool's `when_to_use` description determines whether it applies, not
  whether the user typed the app name.

### Skip the search ONLY for:
- Pure greetings or thanks ("hi", "thanks").
- Simple arithmetic or date calculations.
- User asking about their own identity / profile.
- Write actions where you already have all required parameters.

If a search returns nothing useful, state that plainly and offer to broaden the query — do
not retreat to ambiguity-clarification.
"""

    # Add tool-specific guidance
    if _has_jira_tools(state):
        base_prompt += "\n" + JIRA_GUIDANCE

    if _has_confluence_tools(state):
        base_prompt += "\n" + CONFLUENCE_GUIDANCE

    if _has_onedrive_tools(state):
        base_prompt += "\n" + ONEDRIVE_GUIDANCE

    if _has_outlook_tools(state):
        base_prompt += "\n" + OUTLOOK_GUIDANCE

    if _has_zoom_tools(state):
        base_prompt += "\n" + ZOOM_GUIDANCE

    if _has_salesforce_tools(state):
        base_prompt += "\n" + SALESFORCE_GUIDANCE

    if _has_clickup_tools(state):
        base_prompt += "\n" + CLICKUP_GUIDANCE
    if _has_mariadb_tools(state):
        base_prompt += "\n" + MARIADB_GUIDANCE
    if _has_redshift_tools(state):
        base_prompt += "\n" + REDSHIFT_GUIDANCE

    # ── Multi-step workflow patterns ─────────────────────────────────────────
    workflow_patterns = _build_workflow_patterns(state)
    if workflow_patterns:
        base_prompt += "\n" + workflow_patterns

    # ── Knowledge context ────────────────────────────────────────────────────
    knowledge_context = _build_knowledge_context(state, log)
    if knowledge_context:
        base_prompt += knowledge_context

    # ── Timezone / current time context ──────────────────────────────────────
    if _has_teams_tools(state):
        base_prompt += "\n" + TEAMS_GUIDANCE
    if _has_sharepoint_tools(state):
        base_prompt += "\n" + SHAREPOINT_GUIDANCE

    # Add timezone / current time context if provided
    time_block = build_llm_time_context(
        current_time=state.get("current_time"),
        time_zone=state.get("timezone"),
    )
    if time_block:
        base_prompt += "\n\n" + time_block

    # ── Capability summary ────────────────────────────────────────────────────
    capability_summary = build_capability_summary(state)
    base_prompt += "\n\n" + capability_summary

    # ── User context ─────────────────────────────────────────────────────────
    user_context = _format_user_context(state)
    if user_context:
        base_prompt += "\n\n" + user_context

    return base_prompt


def _get_tool_status_message(tool_name: str) -> str:
    """
    Generate a human-readable status message for a tool call.

    Dynamically parses the tool name (e.g. "outlook.search_messages",
    "outlook_search_messages", "search_messages") into a readable string
    like "Outlook: search messages...".  No hardcoded per-tool mapping
    needed — works for any tool name.
    """
    name_lower = tool_name.lower()

    # Special case: retrieval / knowledge base tools
    if "retrieval" in name_lower or "search_internal" in name_lower:
        return "Searching knowledge base for relevant information..."

    # Split into app and action on the first "." or first "_"
    # Examples:
    #   "outlook.search_messages"        -> app="Outlook",  action="search messages"
    #   "outlook_search_messages"        -> app="Outlook",  action="search messages"
    #   "confluence.get_page_content"    -> app="Confluence", action="get page content"
    #   "search_messages"               -> app=None,        action="search messages"
    app_name = None
    action_part = tool_name

    if "." in tool_name:
        app_name, action_part = tool_name.split(".", 1)
    elif "_" in tool_name:
        # First segment before _ is the app name only when it looks like
        # a known short token (no underscores in it).  e.g. "outlook_send_email"
        # but NOT "search_messages" (no app prefix).
        first, rest = tool_name.split("_", 1)
        # Heuristic: app names are short single words; action verbs like
        # "search", "get", "create" indicate there is no app prefix.
        _ACTION_VERBS = {"get", "set", "search", "list", "create", "update",
                         "delete", "send", "reply", "forward", "fetch",
                         "find", "add", "remove", "post", "upload"}
        if first.lower() not in _ACTION_VERBS:
            app_name = first
            action_part = rest

    # Humanise: replace underscores with spaces, title-case the app
    action_readable = action_part.replace("_", " ").strip()

    if app_name:
        app_display = app_name.replace("_", " ").title()
        return f"{app_display}: {action_readable}..."

    # No app prefix — just capitalise first letter
    if action_readable:
        action_readable = action_readable[0].upper() + action_readable[1:]
    return f"{action_readable}..."


def _extract_final_response(messages: list, log: logging.Logger) -> str:
    """Extract final response from agent messages"""
    # Find last AIMessage with content that is NOT a tool-calling message
    # (tool-calling messages have non-empty tool_calls lists — their content is reasoning, not the answer)
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and msg.content:
            tool_calls = getattr(msg, 'tool_calls', None)
            if not tool_calls:  # No tool calls = this is the final response
                return str(msg.content)

    # Fallback: find any message with content
    for msg in reversed(messages):
        if hasattr(msg, 'content') and msg.content:
            if isinstance(msg.content, str):
                return str(msg.content)
            elif isinstance(msg.content, list):
                return "\n".join([part.get("text", "") for part in msg.content if part.get("type") == "text"])
            
    log.warning("No response found in ReAct agent messages")
    return "I completed the task, but couldn't generate a response."




# ============================================================================
# EXPORTS
# ============================================================================

__all__ = [
    "planner_node",
    "execute_node",
    "respond_node",
    "reflect_node",
    "prepare_retry_node",
    "prepare_continue_node",
    "should_execute_tools",
    "route_after_reflect",
    "check_for_error",
    "NodeConfig",
    "clean_tool_result",
    "format_result_for_llm",
    "ToolResultExtractor",
    "PlaceholderResolver",
    "ToolExecutor",
    "react_agent_node",
]

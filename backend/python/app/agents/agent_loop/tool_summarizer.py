"""`PipesHubToolSummarizer`: the concrete `ToolSummarizer` (`agent_loop_lib/
tools/summarizer.py`) implementation, injected onto `AgentRuntime.summarizer`
by `factory.py`.

This registry is now a FALLBACK layer, not the primary mechanism. Tools
defined via `@tool(...)` (every connector — Jira, Confluence, Slack, ...
— plus internal toolsets like retrieval/knowledge_hub) declare their own
`args_summary`/`result_summary` formatters colocated on the decorator (see
`agent_loop_lib/tools/decorators.py`); `agent/tool_loop.py` consults the
resolved `Tool.summarize_args`/`summarize_result` FIRST and only falls
back to this registry when a tool has no opinion (returns `None`) —
see `tool_loop.py`'s `_args_summary`/`_result_summary`.

What's left here is genuinely platform-owned:
    1. Tools that aren't defined via `@tool` at all — the dynamic,
       per-request adapters (`dynamic__web_search`, `dynamic__fetch_url`/
       `web_scrape`, `sql__execute_sql_query`, `slack__fetch_slack_thread`/
       `nearby_messages`, `dynamic_fetch_full_record`, wrapped as
       `PipesHubStructuredToolAdapter` in `tool_adapter.py`) and the
       agent_loop_lib builtins (`run_code`, `create_plan`, `spawn_agent`,
       `task_complete`, `list_toolsets`, `fetch_tools`, `search_tools`).
       A connector contributor never touches this file.
    2. The generic fallback for every tool — `@tool`-defined or not —
       that declares no formatter of its own.

Dispatch mirrors `app/utils/tool_handlers.py`'s `ToolHandlerRegistry`:
per-tool formatter functions register themselves via a decorator (`OCP` —
adding a summarizer for a new platform tool never touches this dispatch
code), with a generic fallback for every tool that has none. Keyed by the
LLM-facing `{app}__{tool}` name (see `agent_loop_lib/tools/decorators.py`'s
`get_tool_name`/`llm_name`), since that's the name `ToolCall.name` /
`AgentEvent.payload["tool"]` always carries.

Every formatter is failure-isolated at the registry level (`summarize_args`/
`summarize_result` catch and log, never raise) — a bug in one tool's
formatter must never break the tool loop or hide the raw preview the
frontend already falls back to.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.tools.summarizer import ArgsFormatter, ResultFormatter, ToolCallSummary
from app.agents.actions.util.tool_summaries import (
    as_text as _as_text,
    bullet_list as _bullet_list,
    domain_of as _domain,
    first_line as _first_line,
    parse_json_maybe as _parse_json_maybe,
)
from app.modules.agents.context.tool_result_extractor import ToolResultExtractor

if TYPE_CHECKING:
    from app.agent_loop_lib.core.types import ToolResult

__all__ = ["PipesHubToolSummarizer"]

logger = logging.getLogger(__name__)

_MAX_SUMMARY_ITEMS = 5
_MAX_ARG_PREVIEW_CHARS = 80
_MAX_ERROR_PREVIEW_CHARS = 200

# `ArgsFormatter`/`ResultFormatter` (used below for `_args_formatters`/
# `_result_formatters`) now live in `agent_loop_lib.tools.summarizer` so
# this registry and the `@tool(args_summary=..., result_summary=...)`
# decorator layer share one shape. `_as_text`/`_bullet_list`/`_domain`/
# `_first_line`/`_parse_json_maybe` are imported under their historical
# private names from their new canonical home, `app.agents.actions.util.
# tool_summaries` — connector action files import the same functions
# under their public names directly.


def _humanize_tool_name(tool_name: str) -> str:
    segment = tool_name.rsplit("__", 1)[-1] if "__" in tool_name else tool_name
    words = [w for w in segment.replace("-", "_").split("_") if w]
    return " ".join(w.capitalize() for w in words) if words else tool_name


# `Name`/`Web URL` are fixed-width-padded labels rendered by
# `Record.to_llm_context()` (`app/models/entities.py`) inside every
# `<record>...</record>` block `dynamic_fetch_full_record` returns — see
# that module for the producer side. (`retrieval__search_internal_
# knowledge` uses the same `<record>` format but is `@tool`-defined now,
# so its own name-only extractor lives next to it in `retrieval.py`.) A
# tolerant, line-based parse (vs. a full XML parser) because these blocks
# are LLM-facing text, not strict markup, and any future field
# reordering/addition must not break this into raising.
_RECORD_NAME_RE = re.compile(r"^Name\s*:\s*(.+)$", re.MULTILINE)
_RECORD_WEBURL_RE = re.compile(r"^Web URL\s*:\s*(.+)$", re.MULTILINE)


def _extract_record_summaries(text: str) -> list[tuple[str, str | None]]:
    summaries: list[tuple[str, str | None]] = []
    for block in text.split("<record>")[1:]:
        name_match = _RECORD_NAME_RE.search(block)
        if name_match is None:
            continue
        url_match = _RECORD_WEBURL_RE.search(block)
        summaries.append((name_match.group(1).strip(), url_match.group(1).strip() if url_match else None))
    return summaries


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class PipesHubToolSummarizer:
    """`ToolSummarizer` implementation — see module docstring. Registries
    are class-level (mirrors `ToolHandlerRegistry`): registration happens
    once at import time via the `register_args`/`register_result`
    decorators below, so every instance shares the same formatter tables."""

    _args_formatters: dict[str, ArgsFormatter] = {}
    _result_formatters: dict[str, ResultFormatter] = {}

    @classmethod
    def register_args(cls, *tool_names: str) -> Callable[[ArgsFormatter], ArgsFormatter]:
        def decorator(fn: ArgsFormatter) -> ArgsFormatter:
            for name in tool_names:
                cls._args_formatters[name] = fn
            return fn
        return decorator

    @classmethod
    def register_result(cls, *tool_names: str) -> Callable[[ResultFormatter], ResultFormatter]:
        def decorator(fn: ResultFormatter) -> ResultFormatter:
            for name in tool_names:
                cls._result_formatters[name] = fn
            return fn
        return decorator

    def summarize_args(self, tool_name: str, args: dict[str, Any]) -> str | None:
        formatter = self._args_formatters.get(tool_name)
        try:
            if formatter is not None:
                return formatter(args)
            return _generic_args_formatter(args, tool_name)
        except Exception:
            logger.warning("Tool args summarizer failed for %r", tool_name, exc_info=True)
            return None

    def summarize_result(
        self, tool_name: str, args: dict[str, Any], result: "ToolResult"
    ) -> ToolCallSummary:
        formatter = self._result_formatters.get(tool_name, _generic_result_formatter)
        try:
            return ToolCallSummary(result_summary=formatter(args, result))
        except Exception:
            logger.warning("Tool result summarizer failed for %r", tool_name, exc_info=True)
            return ToolCallSummary()


register_args = PipesHubToolSummarizer.register_args
register_result = PipesHubToolSummarizer.register_result


# ---------------------------------------------------------------------------
# web_search (dynamic__web_search / legacy web_search)
# ---------------------------------------------------------------------------


@register_args("dynamic__web_search", "web_search")
def _web_search_args(args: dict[str, Any]) -> str | None:
    query = args.get("query")
    return f'Searched the web for "{query.strip()}"' if isinstance(query, str) and query.strip() else None


@register_result("dynamic__web_search", "web_search")
def _web_search_result(args: dict[str, Any], result: "ToolResult") -> str | None:
    parsed = _parse_json_maybe(result.content)
    if not isinstance(parsed, dict):
        return None
    if parsed.get("ok") is False:
        return f"Web search failed: {parsed.get('error') or 'Unknown error'}"
    web_results = parsed.get("web_results")
    if not isinstance(web_results, list):
        return None
    if not web_results:
        return "No results found"
    lines = []
    for item in web_results[:_MAX_SUMMARY_ITEMS]:
        if not isinstance(item, dict):
            continue
        title = item.get("title") or item.get("link") or "Untitled"
        domain = _domain(item.get("link", ""))
        lines.append(f"{title} — {domain}" if domain else str(title))
    header = f"Found {len(web_results)} result{'s' if len(web_results) != 1 else ''}"
    if not lines:
        return header
    return header + "\n" + _bullet_list(lines, total=len(web_results))


# ---------------------------------------------------------------------------
# fetch_url (dynamic__fetch_url / legacy fetch_url / web_scrape)
# ---------------------------------------------------------------------------


@register_args("dynamic__fetch_url", "fetch_url", "web_scrape")
def _fetch_url_args(args: dict[str, Any]) -> str | None:
    url = args.get("url")
    return f"Reading {url.strip()}" if isinstance(url, str) and url.strip() else None


@register_result("dynamic__fetch_url", "fetch_url", "web_scrape")
def _fetch_url_result(args: dict[str, Any], result: "ToolResult") -> str | None:
    parsed = _parse_json_maybe(result.content)
    if not isinstance(parsed, dict):
        return None
    if parsed.get("ok") is False:
        return f"Failed to read: {parsed.get('error') or 'Unknown error'}"
    url = parsed.get("url") or args.get("url") or ""
    domain = _domain(url) if url else ""
    return f"Fetched content from {domain}" if domain else "Fetched page content"


# ---------------------------------------------------------------------------
# sql__execute_sql_query
# ---------------------------------------------------------------------------


@register_args("sql__execute_sql_query")
def _sql_args(args: dict[str, Any]) -> str | None:
    source_name = args.get("source_name")
    if isinstance(source_name, str) and source_name.strip():
        return f'Running SQL query on "{source_name.strip()}"'
    return "Running SQL query"


@register_result("sql__execute_sql_query")
def _sql_result(args: dict[str, Any], result: "ToolResult") -> str | None:
    parsed = _parse_json_maybe(result.content)
    if not isinstance(parsed, dict):
        return None
    if parsed.get("ok") is False:
        return f"Query failed: {parsed.get('error') or 'Unknown error'}"
    row_count, column_count = parsed.get("row_count"), parsed.get("column_count")
    if row_count is None or column_count is None:
        return None
    return f"Returned {row_count} row{'s' if row_count != 1 else ''}, {column_count} column{'s' if column_count != 1 else ''}"


# ---------------------------------------------------------------------------
# slack__fetch_slack_thread / slack__fetch_slack_nearby_messages
# ---------------------------------------------------------------------------


@register_args("slack__fetch_slack_thread")
def _slack_thread_args(args: dict[str, Any]) -> str | None:
    return "Fetching Slack thread"


@register_args("slack__fetch_slack_nearby_messages")
def _slack_nearby_args(args: dict[str, Any]) -> str | None:
    return "Fetching nearby Slack messages"


@register_result("slack__fetch_slack_thread", "slack__fetch_slack_nearby_messages")
def _slack_result(args: dict[str, Any], result: "ToolResult") -> str | None:
    parsed = _parse_json_maybe(result.content)
    if not isinstance(parsed, dict):
        return None
    if parsed.get("ok") is False:
        return f"Failed: {parsed.get('error') or 'Unknown error'}"
    count = parsed.get("record_count")
    if count is None:
        records = parsed.get("records")
        count = len(records) if isinstance(records, list) else None
    if count is None:
        return None
    return f"Retrieved {count} message{'s' if count != 1 else ''}"


# ---------------------------------------------------------------------------
# dynamic_fetch_full_record
# ---------------------------------------------------------------------------


@register_args("dynamic_fetch_full_record")
def _fetch_full_record_args(args: dict[str, Any]) -> str | None:
    record_ids = args.get("record_ids")
    count = len(record_ids) if isinstance(record_ids, list) else None
    return f"Fetching {count} document{'s' if count != 1 else ''}" if count is not None else "Fetching document"


@register_result("dynamic_fetch_full_record")
def _fetch_full_record_result(args: dict[str, Any], result: "ToolResult") -> str | None:
    text = _as_text(result.content)
    if not text:
        return None
    if result.is_error:
        return f"Fetch failed: {_first_line(text)[:_MAX_ERROR_PREVIEW_CHARS]}"
    names = [name for name, _ in _extract_record_summaries(text)]
    record_ids = args.get("record_ids")
    requested = len(record_ids) if isinstance(record_ids, list) else 0
    count = len(names) or requested
    header = f"Retrieved {count} document{'s' if count != 1 else ''}" if count else "Retrieved document(s)"
    if not names:
        return header
    return header + "\n" + _bullet_list(names, total=count)


# ---------------------------------------------------------------------------
# run_code / install_packages
# ---------------------------------------------------------------------------


@register_args("run_code")
def _run_code_args(args: dict[str, Any]) -> str | None:
    language = args.get("language")
    return f"Running {language} code" if isinstance(language, str) and language.strip() else "Running code"


@register_args("install_packages")
def _install_packages_args(args: dict[str, Any]) -> str | None:
    packages = args.get("packages")
    if isinstance(packages, list) and packages:
        return f"Installing {', '.join(str(p) for p in packages[:_MAX_SUMMARY_ITEMS])}"
    return "Installing packages"


@register_result("run_code", "install_packages")
def _sandbox_result(args: dict[str, Any], result: "ToolResult") -> str | None:
    if result.is_error:
        text = _as_text(result.content) or "Unknown error"
        return f"Failed: {_first_line(text)[:_MAX_ERROR_PREVIEW_CHARS]}"
    return "Executed successfully"


# ---------------------------------------------------------------------------
# create_plan / spawn_agent / task_complete
# ---------------------------------------------------------------------------


@register_args("create_plan")
def _create_plan_args(args: dict[str, Any]) -> str | None:
    steps = args.get("steps")
    if isinstance(steps, list) and steps:
        return f"Creating a plan with {len(steps)} step{'s' if len(steps) != 1 else ''}"
    return "Creating a plan"


@register_args("spawn_agent")
def _spawn_agent_args(args: dict[str, Any]) -> str | None:
    role = args.get("role")
    return f"Delegating to {role}" if isinstance(role, str) and role.strip() else "Delegating to a sub-agent"


@register_args("task_complete")
def _task_complete_args(args: dict[str, Any]) -> str | None:
    return "Marking task complete"


@register_result("create_plan")
def _create_plan_result(args: dict[str, Any], result: "ToolResult") -> str | None:
    return None if result.is_error else "Plan created"


@register_result("spawn_agent")
def _spawn_agent_result(args: dict[str, Any], result: "ToolResult") -> str | None:
    return None if result.is_error else "Sub-agent completed"


@register_result("task_complete")
def _task_complete_result(args: dict[str, Any], result: "ToolResult") -> str | None:
    return None if result.is_error else "Task marked complete"


# ---------------------------------------------------------------------------
# list_toolsets / fetch_tools / search_tools (lazy-toolset meta-tools)
# ---------------------------------------------------------------------------


@register_args("list_toolsets")
def _list_toolsets_args(args: dict[str, Any]) -> str | None:
    return "Listing available capabilities"


@register_args("fetch_tools")
def _fetch_tools_args(args: dict[str, Any]) -> str | None:
    toolset = args.get("toolset")
    return f'Loading tools for "{toolset}"' if isinstance(toolset, str) and toolset.strip() else "Loading tools"


@register_args("search_tools")
def _search_tools_args(args: dict[str, Any]) -> str | None:
    intent = args.get("intent") or args.get("query")
    return f'Searching for tools matching "{intent}"' if isinstance(intent, str) and intent.strip() else "Searching for tools"


@register_result("list_toolsets")
def _list_toolsets_result(args: dict[str, Any], result: "ToolResult") -> str | None:
    parsed = _parse_json_maybe(result.content)
    toolsets = parsed.get("toolsets") if isinstance(parsed, dict) else None
    if not isinstance(toolsets, list):
        return None
    return f"Found {len(toolsets)} categor{'y' if len(toolsets) == 1 else 'ies'}"


@register_result("fetch_tools")
def _fetch_tools_result(args: dict[str, Any], result: "ToolResult") -> str | None:
    parsed = _parse_json_maybe(result.content)
    tools = parsed.get("tools") if isinstance(parsed, dict) else None
    if not isinstance(tools, list):
        return None
    return f"Loaded {len(tools)} tool{'s' if len(tools) != 1 else ''}"


@register_result("search_tools")
def _search_tools_result(args: dict[str, Any], result: "ToolResult") -> str | None:
    parsed = _parse_json_maybe(result.content)
    matches = parsed.get("matches") if isinstance(parsed, dict) else None
    if not isinstance(matches, list):
        return None
    return f"Found {len(matches)} match{'es' if len(matches) != 1 else ''}"


# ---------------------------------------------------------------------------
# Generic fallback — every connector tool (`{app}__{method}`) without a
# dedicated formatter above lands here.
# ---------------------------------------------------------------------------

_GENERIC_STRING_ARG_KEYS = (
    "query", "text", "message", "url", "issue_key", "id", "path", "name", "channel", "reason",
)


def _generic_args_formatter(args: dict[str, Any], tool_name: str) -> str | None:
    label = _humanize_tool_name(tool_name)
    for key in _GENERIC_STRING_ARG_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            preview = value.strip()
            if len(preview) > _MAX_ARG_PREVIEW_CHARS:
                preview = preview[: _MAX_ARG_PREVIEW_CHARS - 1] + "…"
            return f'{label}: "{preview}"'
    return label


def _generic_result_formatter(args: dict[str, Any], result: "ToolResult") -> str | None:
    if result.is_error:
        parsed_error = _parse_json_maybe(result.content)
        if isinstance(parsed_error, dict):
            message = parsed_error.get("message") or parsed_error.get("error")
            if isinstance(message, str) and message.strip():
                return f"Failed: {message.strip()[:_MAX_ERROR_PREVIEW_CHARS]}"
        text = _as_text(result.content)
        message = _first_line(text) if text else "Unknown error"
        return f"Failed: {message[:_MAX_ERROR_PREVIEW_CHARS]}"

    if result.sources:
        labels = [src.title or src.url or src.file or src.query or "source" for src in result.sources]
        header = f"Found {len(result.sources)} source{'s' if len(result.sources) != 1 else ''}"
        return header + "\n" + _bullet_list(labels, total=len(result.sources))

    parsed = _parse_json_maybe(result.content)
    if isinstance(parsed, dict):
        if not ToolResultExtractor.extract_success_status(parsed):
            message = parsed.get("message") or parsed.get("error") or "Unknown error"
            return f"Failed: {message}"
        # Prefer the envelope's own confirmation ("Issues fetched
        # successfully") over a generic label — nearly every connector tool
        # (Jira, Confluence, GitHub, ...) sets this, per `{"message": ...,
        # "data": ...}` (see e.g. `Jira._handle_response`).
        message_field = parsed.get("message")
        label = message_field.strip() if isinstance(message_field, str) and message_field.strip() else "Completed successfully"
        for key in ("items", "results", "records", "data"):
            value = parsed.get(key)
            if isinstance(value, list):
                return f"{label} — {len(value)} item{'s' if len(value) != 1 else ''}"
        # Connector tools wrap list results one level deeper —
        # `{"message": ..., "data": {"issues": [...]}}` rather than a
        # top-level list — so `data` being a dict (not a list) needs its
        # own nested scan before giving up.
        data_value = parsed.get("data")
        if isinstance(data_value, dict):
            for nested_key in (
                "issues", "results", "pages", "users", "events", "members",
                "channels", "tasks", "files", "messages", "comments", "teams",
                "spaces", "repositories", "projects", "documents",
            ):
                nested = data_value.get(nested_key)
                if isinstance(nested, list):
                    return f"{label} — {len(nested)} item{'s' if len(nested) != 1 else ''}"
            # Single-entity `data` (create/get one thing) — surface its
            # identifier so a fetch/create isn't just a bare confirmation.
            entity_id = data_value.get("key") or data_value.get("id") or data_value.get("name")
            if isinstance(entity_id, str) and entity_id.strip():
                return f"{label}: {entity_id.strip()}"
        return label
    if isinstance(parsed, list):
        return f"Completed successfully — {len(parsed)} item{'s' if len(parsed) != 1 else ''}"

    # Non-JSON string content is typically already prose (e.g. tool_loop's
    # duplicate-call-skip short circuit) — surface it directly instead of a
    # generic "Completed successfully" that would bury the real message.
    text = _as_text(result.content)
    return text[:_MAX_ERROR_PREVIEW_CHARS] if text else "Completed successfully"

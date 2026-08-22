"""Envelope-aware `args_summary`/`result_summary` formatter factories for
connector tools (Jira, Confluence, GitHub, Slack, Google, ...).

Every connector action returns the same legacy `tuple[bool, str]` shape
(see `_normalize_legacy_output` in `agent_loop_lib/tools/decorators.py`),
and on success almost always serializes as
``{"message": "...", "data": <dict-or-list>}`` — see e.g. `Jira._handle_
response` or `Confluence.search_pages`. These factories understand that
one convention so a connector file declares a summarizer in one line on
its own `@tool(...)`, instead of hand-rolling JSON parsing per tool:

    @tool(
        path="/tools/jira/search_issues",
        ...,
        args_summary=args_template('Searching Jira issues: "{jql}"', "jql"),
        result_summary=list_summary("issues", lambda i: i.get("key", "?"), "issue"),
    )
    async def search_issues(self, jql: str, ...) -> tuple[bool, str]:
        ...

Every factory is failure-isolated by construction — a formatter it
produces never raises, it returns `None` (falls back to the generic
formatter in `app/agents/agent_loop/tool_summarizer.py`) on any
unexpected shape. `Tool.summarize_args`/`summarize_result` (via
`_safe_summarize_args`/`_safe_summarize_result` in `decorators.py`) is
already a second, independent safety net — these factories don't rely
on that alone, since a formatter raising partway through a bullet list
would otherwise waste the work already done.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from app.agent_loop_lib.tools.summarizer import ArgsFormatter, ResultFormatter

if TYPE_CHECKING:
    from app.agent_loop_lib.core.types import ToolResult

__all__ = [
    "args_template",
    "confirmation",
    "list_summary",
    "entity_summary",
    "as_text",
    "parse_json_maybe",
    "bullet_list",
    "domain_of",
    "first_line",
    "error_message",
]

_MAX_SUMMARY_ITEMS = 5
_MAX_ERROR_PREVIEW_CHARS = 200


# ---------------------------------------------------------------------------
# Shared parsing helpers — promoted to public names here (the connector
# action layer) so both new per-tool declarations and the platform-owned
# fallback in `app/agents/agent_loop/tool_summarizer.py` import the same
# implementation instead of maintaining duplicate copies.
# ---------------------------------------------------------------------------


def as_text(content: Any) -> str | None:
    """`None` for `None`, otherwise a string — tool results are typically
    already `str`, but a few non-`@tool` adapters hand back parsed
    dicts/lists directly (see `_parse_json_maybe`'s docstring)."""
    if content is None:
        return None
    if isinstance(content, str):
        return content
    return str(content)


def parse_json_maybe(content: Any) -> dict[str, Any] | list[Any] | None:
    """Best-effort structured view of a tool result. Never raises: a
    non-JSON string simply yields `None`, which every caller treats as
    "fall through to a different parse strategy / give up"."""
    if isinstance(content, (dict, list)):
        return content
    if isinstance(content, str):
        try:
            return json.loads(content)
        except (json.JSONDecodeError, ValueError):
            return None
    return None


def first_line(text: str) -> str:
    return text.strip().splitlines()[0] if text.strip() else text


def domain_of(url: str) -> str:
    try:
        return urlparse(url).netloc or url
    except ValueError:
        return url


def bullet_list(items: list[str], *, total: int | None = None) -> str:
    """Renders up to `_MAX_SUMMARY_ITEMS` items as a markdown bullet list,
    collapsing anything beyond that into a trailing "and N more" line so a
    tool call that touched 50 records doesn't turn into a 50-line card."""
    limited = items[:_MAX_SUMMARY_ITEMS]
    lines = [f"- {item}" for item in limited]
    remaining = (total if total is not None else len(items)) - len(limited)
    if remaining > 0:
        lines.append(f"- and {remaining} more")
    return "\n".join(lines)


def error_message(parsed: Any) -> str:
    """Every connector error envelope is `{"error": "...", ...}` (see e.g.
    `Jira._handle_response`'s error branch); a few ad hoc paths use
    `"message"` instead. Falls back to a generic label rather than `None`
    since this is only ever called once `result.is_error` is already
    known True — there must be SOME text to show."""
    if isinstance(parsed, dict):
        message = parsed.get("error") or parsed.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()[:_MAX_ERROR_PREVIEW_CHARS]
    return "Unknown error"


def _node_at(parsed: Any, path: tuple[str, ...]) -> Any:
    node = parsed
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


def args_template(template: str, *arg_keys: str) -> ArgsFormatter:
    """`args_template('Fetching Jira issue {issue_key}', 'issue_key')` —
    returns `None` (no opinion, falls back) when any listed key is
    missing/blank, so a tool called with only optional arguments doesn't
    render a template full of empty braces."""

    def _format(args: dict[str, Any]) -> str | None:
        values: dict[str, Any] = {}
        for key in arg_keys:
            value = args.get(key)
            if value is None or (isinstance(value, str) and not value.strip()):
                return None
            values[key] = value
        try:
            return template.format(**values)
        except (KeyError, IndexError):
            return None

    return _format


def confirmation(template: str, *arg_keys: str) -> ResultFormatter:
    """For write/action tools whose confirmation is best phrased from the
    call's OWN arguments (e.g. `"Comment added to {issue_key}"`) rather
    than the response body, since most connector write endpoints return
    only a bare acknowledgement with nothing more to summarize. Missing
    argument keys degrade to an empty string in the template rather than
    `None`, since an error result must still surface `error_message`."""

    def _format(args: dict[str, Any], result: "ToolResult") -> str | None:
        if result.is_error:
            return f"Failed: {error_message(parse_json_maybe(result.content))}"
        try:
            return template.format(**{key: args.get(key, "") for key in arg_keys})
        except (KeyError, IndexError):
            return None

    return _format


def list_summary(
    path: str | tuple[str, ...],
    item_label: Callable[[dict[str, Any]], str],
    noun: str,
) -> ResultFormatter:
    """Unwraps the connector envelope's list at `path` and renders
    `"Found N {noun}s"` plus a bullet list from `item_label` (up to 5,
    "and N more" beyond that).

    `path` is either the common-case shorthand — a bare key looked up
    under `data` (`"issues"` -> `("data", "issues")`, matching
    `{"message": ..., "data": {"issues": [...]}}`) — or an explicit tuple
    for connectors whose list isn't nested under `data` (e.g.
    Confluence's `search_content`, which returns `{"results": [...]}` at
    the top level -> `path=("results",)`).
    """
    key_path = ("data", path) if isinstance(path, str) else tuple(path)

    def _format(args: dict[str, Any], result: "ToolResult") -> str | None:
        if result.is_error:
            return f"Failed: {error_message(parse_json_maybe(result.content))}"
        parsed = parse_json_maybe(result.content)
        items = _node_at(parsed, key_path)
        if not isinstance(items, list):
            return None
        if not items:
            return f"No {noun}s found"
        labels: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                label = item_label(item)
            except Exception:
                continue
            if label:
                labels.append(label)
        header = f"Found {len(items)} {noun}{'s' if len(items) != 1 else ''}"
        if not labels:
            return header
        return header + "\n" + bullet_list(labels, total=len(items))

    return _format


def entity_summary(
    label: Callable[[dict[str, Any]], str],
    *,
    path: tuple[str, ...] = ("data",),
) -> ResultFormatter:
    """Unwraps a single-entity envelope at `path` (default `data`, i.e.
    `{"message": ..., "data": {...}}`) and renders one line from `label`
    — e.g. `"Created PA-123"` for a Jira `create_issue` response."""

    def _format(args: dict[str, Any], result: "ToolResult") -> str | None:
        if result.is_error:
            return f"Failed: {error_message(parse_json_maybe(result.content))}"
        parsed = parse_json_maybe(result.content)
        entity = _node_at(parsed, path)
        if not isinstance(entity, dict):
            return None
        try:
            return label(entity)
        except Exception:
            return None

    return _format

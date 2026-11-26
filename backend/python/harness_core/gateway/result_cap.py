"""How much of one tool result an engine's MCP client reads in its conversation (SPENDCONT).

A Codex child's 54 Jira JQL searches returned 51 KB a result at the median and 214 KB at most, its
77 single-issue reads 4 KB each; every result stayed in the engine's conversation and resent with each later model
call, so its requests grew to 257k tokens before the engine compacted and its 30 calls read 3.47M prompt tokens.

Other harnesses bound a tool result the same way: Claude Code saves an MCP result over 50,000 characters to a file
and gives the model its path; opencode keeps a preview of output over 50 KB or 2,000 lines and saves the rest to a
file; Codex keeps the head and tail of a tool's output past 10 KB. Here a result past ``ENGINE_RESULT_CAP_CHARS``
keeps its head and tail (``cap_text``, the loop's cut) and says how to get all of it into a file: the same call
through ``ph`` in the shell, which asks for the whole result (``FULL_RESULT_META``) and saves it under
``notes/tool-results/`` for the engine to read in parts (WP98-FU-4). The journal and the run's citations keep the whole result either way: only what the engine's
conversation holds is bounded.

A tool whose results the run keeps on a shelf (C3.22: knowledge results under ``/pipeshub/knowledge``) is not cut:
past the cap its whole result is written to a file in the sandbox (``ShelvesResults``), and the engine reads the
opening of it and the file's path, so nothing is lost and nothing has to be fetched again.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from dataclasses import replace
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from harness_core.context.results import cap_text
from harness_core.gateway.disclosure import CALL_TOOL, unwrap_call
from harness_core.workspace.sync.storage_classes import TOOL_RESULTS_DIR

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from harness_core.gateway.types import McpResult

# About 8k tokens of JSON (three characters a token): that child's median search result is cut, a single issue is not.
ENGINE_RESULT_CAP_CHARS = 24_000
# The MCP ``_meta`` key ``ph`` sets on ``tools/call``: the caller writes the result to a file, so it is never cut.
FULL_RESULT_META = "pipeshub/fullResult"
_MAX_COMMAND_ARGS = 600
# How much of a shelved result the engine reads inline: enough to see what it holds and where to look in the file.
SHELF_PREVIEW_CHARS = 6_000
_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")


@runtime_checkable
class ShelvesResults(Protocol):
    """An endpoint that keeps a tool's over-cap results in files the engine reads (C3.22)."""

    def shelves(self, tool: str) -> bool:
        """Whether ``tool``'s over-cap results go on the shelf."""
        ...

    async def shelve(self, name: str, text: str) -> str | None:
        """Writes ``text`` as ``name``; the path the engine reads it at, or None when it could not be written."""
        ...


@runtime_checkable
class CapsResults(Protocol):
    """An endpoint whose run lowered the inline cap (C4.10 ``toolResults.maxChars``)."""

    max_result_chars: int


def run_result_cap(policy_cap: int | None) -> int:
    """The engine's inline cap under the run's ``toolResults.maxChars``: lowered by it, never raised."""
    return ENGINE_RESULT_CAP_CHARS if policy_cap is None else min(ENGINE_RESULT_CAP_CHARS, policy_cap)


def endpoint_result_cap(endpoint: object) -> int:
    """The cap ``endpoint``'s run reads results under: its own when it carries one, else the release's."""
    return endpoint.max_result_chars if isinstance(endpoint, CapsResults) else ENGINE_RESULT_CAP_CHARS


def _hint(tool: str, arguments: Mapping[str, JsonValue], total: int) -> str:
    raw = json.dumps(dict(arguments), ensure_ascii=False, separators=(",", ":"))
    args = shlex.quote(raw) if len(raw) <= _MAX_COMMAND_ARGS else "'<the same arguments>'"
    return (
        f"\n[This result is {total} characters; the middle was cut to keep your context small. To work with all of "
        f"it, run the same call in the shell, which saves the whole result under {TOOL_RESULTS_DIR}/ and prints the "
        f"file's path: ph call {tool} --json {args}. Then read the parts you need. For many records, prefer one "
        "search that returns only the fields you need over fetching each record.]"
    )


def cap_engine_result(
    result: McpResult, *, tool: str, arguments: Mapping[str, JsonValue], max_chars: int = ENGINE_RESULT_CAP_CHARS
) -> McpResult:
    """``result`` as an engine's conversation holds it: whole up to ``max_chars``, else its head and tail and how to
    get the rest. Errors and refusals are short and pass whole."""
    text = result.text
    if result.is_error or len(text) <= max_chars:
        return result
    return replace(result, text=cap_text(text, max_chars) + _hint(tool, arguments, len(text)))


def shelf_name(tool: str, invocation_id: str | None, text: str) -> str:
    """The file a result is shelved as: its invocation's, so a replayed call names the same file."""
    key = invocation_id or hashlib.sha256(text.encode()).hexdigest()[:16]
    return _UNSAFE.sub("_", f"{tool.rpartition('__')[2]}-{key}")[:120]


def shelved_result(result: McpResult, path: str, *, preview_chars: int = SHELF_PREVIEW_CHARS) -> McpResult:
    """``result`` as the engine reads it once shelved: its opening, cut at a line, and where the whole of it is."""
    head = result.text[:preview_chars]
    cut = head.rfind("\n")
    head = head[:cut] if cut > preview_chars // 2 else head
    note = (
        f"\n[This result is {len(result.text)} characters; only its opening is shown. The whole result is in {path}. "
        "Read the parts you need from it (search it for a name, or read a range of lines) rather than all of it.]"
    )
    return replace(result, text=head + note)


async def bound_engine_result(
    endpoint: object,
    result: McpResult,
    *,
    tool: str,
    arguments: Mapping[str, JsonValue],
    max_chars: int = ENGINE_RESULT_CAP_CHARS,
) -> McpResult:
    """``result`` as an engine's conversation holds it: whole up to ``max_chars``; past it, shelved when the
    endpoint shelves the tool's results (a call through ``call_tool`` is the tool it calls), else cut."""
    if result.is_error or len(result.text) <= max_chars:
        return result
    if isinstance(endpoint, ShelvesResults):
        called = unwrap_call(arguments) if tool == CALL_TOOL else (tool, arguments)
        name = called[0] if isinstance(called, tuple) else tool
        if endpoint.shelves(name):
            path = await endpoint.shelve(shelf_name(name, result.invocation_id, result.text), result.text)
            if path is not None:
                return shelved_result(result, path)
    return cap_engine_result(result, tool=tool, arguments=arguments, max_chars=max_chars)


def wants_full_result(meta: Mapping[str, object] | None) -> bool:
    """Whether a ``tools/call`` asked for the whole result (``ph``, which writes it where the caller says)."""
    return bool(meta) and meta.get(FULL_RESULT_META) is True  # type: ignore[union-attr]


__all__ = [
    "ENGINE_RESULT_CAP_CHARS",
    "CapsResults",
    "FULL_RESULT_META",
    "SHELF_PREVIEW_CHARS",
    "ShelvesResults",
    "bound_engine_result",
    "endpoint_result_cap",
    "run_result_cap",
    "cap_engine_result",
    "shelf_name",
    "shelved_result",
    "wants_full_result",
]

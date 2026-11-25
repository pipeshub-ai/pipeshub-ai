"""What a tool step shows (WP-112): its typed facts, its one-line summaries and the tool's view of its result, built in
one place for the loop's steps and every engine's.

A step read "Read a page" or "Glob" and nothing else: an engine's events dropped the arguments, and the gateway's
result reached the chat as raw text. Here a step's facts are built from what the call asked and what it returned:

* ``StepFacts``: the action a person reads (``readPage``, ``webSearch``, ``findFiles``, ``searchFiles``,
  ``readFile``, ``listFiles``: the keys of ``toolNames`` in the chat's i18n and of the tool-display registry), its
  target (a page's host and link, a query, a pattern, a path) and its outcome (a page's title, a count). PipesHub's
  own web tools are named the same in the loop and behind the gateway; each engine's own tools by its own names
  (Claude Code, opencode, Gemini CLI, Qwen Code, Goose), and an ACP agent's other calls by the kind it gives them.
* ``args_summary``, ``result_summary``, ``result_view``: the tool's own summary first, then the summarizer's
  (``tool_loop``'s precedence), and the tool's own view of its full result. The loop's presenter and the engine's
  gateway calls (``GatewaySteps``) both read them here, so a page read says the same in either.
* ``GatewaySteps``: the Tool Gateway port's presentation hook. It keeps, per invocation, what the call's
  ``ToolResult`` said (its sources carry the titles the gateway's text no longer does), for the engine's presenter
  to show on the step the engine started.
"""

from __future__ import annotations

import json
import re
from collections import OrderedDict
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlparse

from harness_core.logs import HarnessLog

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from harness_core.journal.types import NewEvent
    from harness_core.kernel.core.messages import ToolCall
    from harness_core.kernel.core.types import ToolResult

StepAction = Literal["readPage", "webSearch", "findFiles", "searchFiles", "readFile", "listFiles"]

# The longest target a step names (a query, a pattern, a path); the full arguments stay behind "Details".
MAX_TARGET_CHARS = 300
MAX_TITLE_CHARS = 200
# The longest link a step names (``tool-step.schema.json``): a longer one is left out, never the step's event.
MAX_LINK_CHARS = 2048
# The links a step keeps of what it returned, for the answer's citations to point at it.
MAX_SOURCES = 50
# Invocations whose result the engine's presenter has not taken yet; past this the oldest are dropped.
MAX_KEPT = 256
WORKDIR = "/workspace"

# PipesHub's own web tools, by their bare names (after ``dynamic__``/``web__``): the loop's and the gateway's.
_PIPESHUB_WEB: Mapping[str, StepAction] = {"fetch_url": "readPage", "web_scrape": "readPage", "web_search": "webSearch"}
_WEB_PREFIXES = frozenset({"dynamic", "web"})
# Each engine's own tools, by the exact name it gives them. A connector's ``github__read_file`` is never one of them:
# a name with a toolset prefix is only ever PipesHub's.
_ENGINE_TOOLS: Mapping[str, StepAction] = {
    # Claude Code
    "WebFetch": "readPage",
    "WebSearch": "webSearch",
    "Glob": "findFiles",
    "Grep": "searchFiles",
    "Read": "readFile",
    "NotebookRead": "readFile",
    "LS": "listFiles",
    # opencode
    "webfetch": "readPage",
    "websearch": "webSearch",
    "glob": "findFiles",
    "grep": "searchFiles",
    "read": "readFile",
    "list": "listFiles",
    # Gemini CLI and Qwen Code (its fork), DeepSeek Harness where it keeps their names
    "web_fetch": "readPage",
    "google_web_search": "webSearch",
    "search_file_content": "searchFiles",
    "grep_search": "searchFiles",
    "read_file": "readFile",
    "read_many_files": "readFile",
    "list_directory": "listFiles",
}
# An ACP agent's call by its kind, when its name is none of the above.
_ACP_KINDS: Mapping[str, StepAction] = {"fetch": "readPage", "read": "readFile", "search": "searchFiles"}
_TARGET_KEYS: Mapping[StepAction, tuple[str, ...]] = {
    "readPage": ("url", "uri", "link"),
    "webSearch": ("query", "q", "search_query"),
    "findFiles": ("pattern", "glob", "file_pattern"),
    "searchFiles": ("pattern", "query", "regex"),
    "readFile": ("file_path", "filePath", "absolute_path", "path", "notebook_path"),
    "listFiles": ("path", "dir_path", "directory", "dir"),
}
# Goose's developer extension reads a file through its text editor's ``view``.
_GOOSE_EDITOR = "developer__text_editor"
_FOUND = re.compile(r"^Found (\d+) (?:files?|matches?|lines?)\b", re.IGNORECASE)
_NOTHING = re.compile(r"^(?:No (?:files|matches|results)\b|0 (?:files|matches)\b)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class StepFacts:
    """One step as a person reads it: what it did, on what, and what came of it."""

    action: StepAction
    target: str = ""
    href: str | None = None
    title: str | None = None
    count: int | None = None
    # The links the result offered (a search's hits, a page's cited blocks), without their text fragment.
    sources: tuple[str, ...] = ()

    def wire(self) -> dict[str, Any]:
        """The ``step`` object of an engine's progress frame (``engine-steps.ts`` reads it)."""
        out: dict[str, Any] = {"action": self.action}
        if self.target:
            out["target"] = self.target
        if self.href is not None:
            out["href"] = self.href
        if self.title:
            out["title"] = self.title
        if self.count is not None:
            out["count"] = self.count
        if self.sources:
            out["sources"] = list(self.sources)
        return out


def _bounded(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _relative(path: str) -> str:
    return path[len(WORKDIR) + 1 :] if path.startswith(WORKDIR + "/") else path


def _web_link(url: str) -> str | None:
    """A link a person may open: http(s) only, never a citation alias (``ref3.xyz``)."""
    from app.utils.citations import extract_tiny_ref

    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not parsed.netloc or len(url) > MAX_LINK_CHARS or extract_tiny_ref(url):
        return None
    return url


def _host(url: str) -> str:
    try:
        host = urlparse(url).netloc
    except ValueError:
        return ""
    return host.removeprefix("www.")


def _without_fragment(url: str) -> str:
    return url.split("#", 1)[0]


def action_of(tool: str, arguments: Mapping[str, Any] | None = None, *, kind: str = "") -> StepAction | None:
    """The step's action from the tool's name (and an ACP agent's kind): None for a tool that is none of these."""
    if "__" in tool:
        prefix, _, bare = tool.rpartition("__")
        if prefix.rpartition("__")[2] in _WEB_PREFIXES and bare in _PIPESHUB_WEB:
            return _PIPESHUB_WEB[bare]
        if tool == _GOOSE_EDITOR and (arguments or {}).get("command") == "view":
            return "readFile"
        return None
    return _PIPESHUB_WEB.get(tool) or _ENGINE_TOOLS.get(tool) or _ACP_KINDS.get(kind)


def call_facts(
    tool: str,
    arguments: Mapping[str, Any] | None,
    *,
    title: str = "",
    paths: Sequence[str] = (),
    kind: str = "",
) -> StepFacts | None:
    """What a call asked, as its step names it; None when the tool has no action of these."""
    args = arguments or {}
    action = action_of(tool, args, kind=kind)
    if action is None:
        return None
    raw = next((v for k in _TARGET_KEYS[action] if isinstance(v := args.get(k), str) and v.strip()), "")
    if not raw and action == "readFile" and isinstance(many := args.get("paths"), list) and many:
        raw = ", ".join(str(p) for p in many[:3])
    if not raw and paths and action in ("readFile", "listFiles", "searchFiles", "findFiles"):
        raw = paths[0]
    if not raw and title and title != tool:
        # An ACP agent's own words for the call ("Read notes/plan.md") when it sent no input.
        raw = title
    raw = raw.strip()
    if action == "readPage":
        href = _web_link(raw)
        return StepFacts(action, _host(raw) if href else "", href)
    if action in ("readFile", "listFiles"):
        raw = _relative(raw)
    return StepFacts(action, _bounded(raw, MAX_TARGET_CHARS))


def _listed(text: str) -> int | None:
    """How many files or matches an engine's own find or search listed: its "Found N" line, or one per line."""
    first = text.strip().splitlines()[0].strip() if text.strip() else ""
    if not first or _NOTHING.match(first):
        return 0
    found = _FOUND.match(first)
    if found is not None:
        return int(found.group(1))
    return sum(1 for line in text.splitlines() if line.strip())


def result_facts(
    facts: StepFacts,
    *,
    text: str | None = None,
    sources: Iterable[tuple[str | None, str | None]] = (),
    failed: bool = False,
) -> StepFacts:
    """``facts`` with what the call returned: a page's title, a search's hits, a find's files. ``sources``: the
    result's (url, title) pairs, from its ``ToolResult`` or the gateway's citations."""
    if failed:
        return facts
    pairs = [(url, title) for url, title in sources if url]
    links = tuple(
        dict.fromkeys(link for url, _ in pairs if url and 0 < len(link := _without_fragment(url)) <= MAX_LINK_CHARS)
    )[:MAX_SOURCES]
    match facts.action:
        case "readPage":
            title = next((t for _, t in pairs if t and t.strip()), None)
            href = facts.href or next((_web_link(link) for link in links if _web_link(link)), None)
            target = facts.target or (_host(href) if href else "")
            return replace(
                facts, target=target, href=href, title=_bounded(title, MAX_TITLE_CHARS) if title else None, sources=links
            )
        case "webSearch":
            count = len(links) if pairs or text is None else _search_hits(text)
            return replace(facts, count=count, sources=links)
        case "findFiles" | "searchFiles" | "listFiles":
            return replace(facts, count=_listed(text) if text is not None else None)
        case _:
            return facts


def step_of(name: str, arguments: Mapping[str, Any] | None, result: ToolResult | None = None) -> StepFacts | None:
    """A PipesHub tool call's step: what it asked and, given its ``ToolResult``, what came of it. The loop's presenter
    and the engine's gateway calls (``GatewaySteps``) both describe a call here."""
    facts = call_facts(name, arguments)
    if facts is None or result is None:
        return facts
    return result_facts(
        facts,
        text=result.content if isinstance(result.content, str) else None,
        sources=((s.url, s.title) for s in result.sources),
        failed=result.is_error,
    )


def _search_hits(text: str) -> int | None:
    """A search's hits in the text PipesHub's web search returns (``WebSearchHandler``): one ``title:`` per hit."""
    if "Blocks retrieved from web search" not in text:
        return None
    return sum(1 for line in text.splitlines() if line.startswith("title: "))


# ---- the summaries (``tool_loop``'s precedence: the tool's own, then the summarizer's) ----


def args_summary(tool: Any, summarizer: Any, name: str, args: dict[str, Any], log: HarnessLog) -> str | None:  # noqa: ANN401
    if tool is not None:
        try:
            summary = tool.summarize_args(args)
            if summary is not None:
                return str(summary)
        except Exception as exc:
            log.debug("presentation.summary_failed", stage="args", errorType=type(exc).__name__)
    try:
        summary = summarizer.summarize_args(name, args)
    except Exception as exc:
        log.debug("presentation.summary_failed", stage="args", errorType=type(exc).__name__)
        return None
    return str(summary) if summary is not None else None


def result_summary(
    tool: Any,  # noqa: ANN401
    summarizer: Any,  # noqa: ANN401
    name: str,
    args: dict[str, Any],
    result: ToolResult,
    log: HarnessLog,
) -> str | None:
    if tool is not None:
        try:
            summary = tool.summarize_result(args, result)
            if summary is not None:
                return str(summary)
        except Exception as exc:
            log.debug("presentation.summary_failed", stage="result", errorType=type(exc).__name__)
    try:
        summary = summarizer.summarize_result(name, args, result).result_summary
    except Exception as exc:
        log.debug("presentation.summary_failed", stage="result", errorType=type(exc).__name__)
        return None
    return str(summary) if summary is not None else None


def result_view(tool: Any, args: dict[str, Any], result: ToolResult) -> Any:  # noqa: ANN401
    """The tool's own view of the full result (``tool_loop._result_view``); a failure only loses the view."""
    if tool is None:
        return None
    try:
        return tool.result_view(dict(args), result)
    except Exception:
        return None


@dataclass(frozen=True, slots=True)
class ShownResult:
    """What one gateway call's ``ToolResult`` said, for the step the engine started."""

    args_summary: str | None
    result_summary: str | None
    result_view: Any
    facts: StepFacts | None


class GatewaySteps:
    """The Tool Gateway port's presentation (``RegistryToolPort(present=...)``) for an engine run: nothing journaled
    of its own (the engine's step is the call's line), the call's summaries and facts kept by invocation id."""

    def __init__(self, registry: Any, summarizer: Any = None, *, log: HarnessLog | None = None) -> None:  # noqa: ANN401
        from app.agents.agent_loop.protocol.result_views import ResultViewBudget
        from app.agents.agent_loop.tool_summarizer import PipesHubToolSummarizer

        self._registry = registry
        self._summarizer = summarizer or PipesHubToolSummarizer()
        self._log = log or HarnessLog.named("app.harness.step_display")
        self._kept: OrderedDict[str, ShownResult] = OrderedDict()
        # A run's result views share one budget, as a reply's do in the loop.
        self._views = ResultViewBudget()

    def _tool(self, name: str) -> Any:  # noqa: ANN401
        try:
            return self._registry.resolve_by_name(name)
        except Exception:
            return None

    async def present(self, call: ToolCall, result: ToolResult) -> tuple[NewEvent, ...]:
        """Never a reason for the call to fail: a step that cannot be described keeps its plain line."""
        try:
            self._keep(call, result)
        except Exception as exc:
            self._log.warning("step_display.failed", errorType=type(exc).__name__)
        return ()

    def _keep(self, call: ToolCall, result: ToolResult) -> None:
        args = dict(call.arguments)
        tool = self._tool(call.name)
        failed = result.is_error
        facts = step_of(call.name, args, result)
        view = None if failed else self._views.admit(result_view(tool, args, result))
        self._kept[call.id] = ShownResult(
            args_summary(tool, self._summarizer, call.name, args, self._log),
            None if failed else result_summary(tool, self._summarizer, call.name, args, result, self._log),
            view,
            facts,
        )
        while len(self._kept) > MAX_KEPT:
            _ = self._kept.popitem(last=False)

    def take(self, invocation_id: str | None) -> ShownResult | None:
        return self._kept.pop(invocation_id, None) if invocation_id is not None else None


def shown_arguments(arguments: Mapping[str, Any] | None, limit: int) -> str | None:
    """A call's arguments for "Details", bounded; None for none."""
    if not arguments:
        return None
    text = json.dumps(dict(arguments), default=str, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit]


__all__ = [
    "GatewaySteps",
    "ShownResult",
    "StepAction",
    "StepFacts",
    "action_of",
    "args_summary",
    "call_facts",
    "result_facts",
    "result_summary",
    "result_view",
    "shown_arguments",
    "step_of",
]

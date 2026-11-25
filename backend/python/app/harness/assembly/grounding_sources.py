"""ANSWER-CONTRACT-SOURCES (ADR-RUN-03 D1): the grounding check searches every source the person can reach.

Searching PipesHub knowledge alone, the check missed a person's connected Atlassian Rovo, where the
subject may well live. A run's connected sources are its MCP servers and toolsets; the ones that can answer "is this
subject here?" are picked by what their tools declare, never by vendor name:

* read-only: the tool's kind (MCP ``readOnlyHint``, a toolset tool's own kind) is ``read``, and the person's or the
  instance's approval rule lets the call run without asking (``tool_approvals.decide`` with nobody to ask);
* a search: a word of its name is a search word (``search``, ``query``, ``find``);
* called with the subject alone: one text argument takes it (its only required one, or a query-named one), and no
  other argument is required.

A server listed at first use (ADR-OWD-07 D-LAZY-4) has no built tools when the check runs: its searches are picked
from what is stored of it (``mcp_known``: the tool's hints, its input schema, the person's rule), and only those are
built, through the run's own first-use path (``LazyMcpServers.prepare``: the server listed once and pinned). What
the listing serves is checked again before any call, since a stored catalog can be stale. A server granted whole with
nothing stored is listed to learn its tools.

Each is called once, in the run's own tool scope, with a time bound, and its result is read as hits: the items of a
JSON result, else the text's paragraphs; a result that says it found nothing is no hits. A search that fails counts
as not run. What the model sees is unchanged: these calls are the gate's, as its knowledge probe is.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, cast

from app.harness.grounding import (
    HIT_TEXT_MAX_CHARS,
    PLACES_MAX,
    Hit,
    is_knowledge_search,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from contextlib import AbstractContextManager

    from app.agents.agent_loop.mcp_lazy import LazyMcpServers
    from harness_core.logs import HarnessLog
    from harness_core.tools.registry import ToolRegistry

SEARCH_WORDS: Final = frozenset({"search", "query", "find"})
# Argument names a search takes its text by, when it requires none.
TEXT_ARGUMENTS: Final = ("query", "q", "text", "search", "searchtext", "searchquery", "keywords", "keyword", "terms")
SOURCE_TIMEOUT_S: Final = 15.0
ITEMS_MAX: Final = 20
_TITLE_KEYS: Final = ("title", "name", "summary", "subject", "key")
_NO_RESULTS = re.compile(
    r"\b(?:no (?:results?|matches|documents|items|issues|pages|records)|nothing (?:was )?found|0 results)\b",
    re.IGNORECASE,
)
_WORD = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+")


@dataclass(frozen=True, slots=True)
class SourceSearch:
    """A connected source's search the gate may call: its registry name, its place's name, the tool's own name and
    the argument the subject goes in."""

    name: str
    place: str
    tool: str
    argument: str


@dataclass(frozen=True, slots=True)
class Searched:
    """What one source's search showed: its hits, or ``None`` when it could not run."""

    search: SourceSearch
    hits: list[Hit] | None


def _words(name: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(name)}


def _argument(tool: Any) -> str | None:  # noqa: ANN401 - a registry Tool
    """The one text argument the subject goes in; ``None`` when the call needs anything else."""
    return _argument_of(list(getattr(tool, "parameters", None) or []))


def _argument_of(params: Sequence[Any]) -> str | None:
    required = [p for p in params if getattr(p, "required", False)]
    text = [p for p in params if str(getattr(getattr(p, "type", None), "value", getattr(p, "type", ""))) == "string"]
    if len(required) == 1 and required[0] in text:
        return str(required[0].name)
    if required:
        return None
    named = [p for p in text if str(p.name).lower().replace("_", "") in TEXT_ARGUMENTS]
    return str(named[0].name) if named else None


def _target(context: Any, registry: ToolRegistry, name: str, tool: Any) -> tuple[str, str, str, Any] | None:  # noqa: ANN401
    """The place's name, the tool's own name, its kind and its approval target (``None``: no rules for it)."""
    from app.agents.agent_loop.tool_approvals import (
        _target as approval_target,  # pyright: ignore[reportPrivateUsage]
    )

    path = registry.path_for_name(name) if hasattr(registry, "path_for_name") else None
    target = approval_target(context, path) if path else None
    if target is not None:
        return target.owner, target.tool_name, target.kind, target
    server = getattr(tool, "server", None)
    kind = getattr(tool, "kind", None)
    if server is not None and isinstance(kind, str):
        return str(getattr(server, "display_name", "") or ""), str(getattr(tool, "tool_name", name)), kind, None
    return None


def source_searches(context: Any, registry: ToolRegistry | None, offered: Sequence[str]) -> list[SourceSearch]:  # noqa: ANN401
    """The run's connected sources' searches, read-only and called with text alone, at most ``PLACES_MAX`` places."""
    if registry is None:
        return []
    found: list[SourceSearch] = []
    places: set[str] = set()
    for name in offered:
        if is_knowledge_search(name) or not registry.has(name):
            continue
        try:
            tool = registry.resolve_by_name(name)
        except Exception:
            continue
        described = _target(context, registry, name, tool)
        if described is None:
            continue
        place, own, kind, _ = described
        if kind != "read" or not place or not (_words(own) & SEARCH_WORDS):
            continue
        argument = _argument(tool)
        if argument is None or (place not in places and len(places) >= PLACES_MAX):
            continue
        places.add(place)
        found.append(SourceSearch(name=name, place=place, tool=own, argument=argument))
    return found


async def runs_without_asking(context: Any, registry: ToolRegistry, search: SourceSearch) -> bool:  # noqa: ANN401
    """Whether the person's or the instance's rule lets this read run with nobody asked: never a call that asks."""
    tool = registry.resolve_by_name(search.name)
    described = _target(context, registry, search.name, tool)
    if described is None:
        return False
    _, own, kind, target = described
    if target is None:
        return await _unasked(context, kind=kind)
    return await _unasked(
        context, kind=target.kind, own=own, source=target.source, instance_id=target.instance_id,
        personal=target.personal, defaults=target.defaults,
    )


async def _unasked(
    context: Any,  # noqa: ANN401 - AgentContext
    *,
    kind: str,
    own: str | None = None,
    source: str = "mcp",
    instance_id: str | None = None,
    personal: bool = False,
    defaults: str | None = None,
) -> bool:
    """The rule of one call with nobody to ask is ``allow``; without an instance, the tool's starting rule."""
    from app.agents.agent_loop.tool_approvals import (
        _rules_for,  # pyright: ignore[reportPrivateUsage]
        decide,
        starting_rule,
    )

    if instance_id is None or own is None:
        return starting_rule(kind=cast("Any", kind), source="mcp") == "allow"
    rules = await _rules_for(context, cast("Any", source), instance_id, personal=personal)
    if rules.unavailable:
        return False
    verdict = decide(
        own=rules.rules.tools.get(own), kind=cast("Any", kind), source=cast("Any", defaults or source), watched=False,
    )
    return verdict.rule == "allow"


async def first_use_searches(context: Any, registry: ToolRegistry, lazy: LazyMcpServers) -> None:  # noqa: ANN401
    """The searches of servers not listed yet built through their first use, picked from what is stored of them: a
    read whose schema takes the subject alone and whose rule runs it unasked, at most ``PLACES_MAX`` places. A tool
    nothing is stored of is picked by its name, and the listing decides. No other server is listed."""
    from app.agents.agent_loop.tool_adapter import _params_from_schema  # pyright: ignore[reportPrivateUsage]
    from app.agents.agent_loop.tool_approvals import enabled
    from app.agents.mcp.tool_kind import tool_kind

    await lazy.name_unnamed()
    picked: list[str] = []
    places: set[str] = set()
    for call in lazy.unlisted():
        if not (_words(call.tool) & SEARCH_WORDS):
            continue
        if call.stored is not None:
            kind = tool_kind(call.tool, call.stored.annotations)[0]
            if kind != "read" or _argument_of(_params_from_schema(call.stored.input_schema, call.name)) is None:
                continue
            rule_of = call.instance_id if enabled("mcp") else None
            if not await _unasked(context, kind=kind, own=call.tool, instance_id=rule_of):
                continue
        if call.server not in places and len(places) >= PLACES_MAX:
            continue
        places.add(call.server)
        picked.append(call.name)
    if picked:
        await lazy.prepare(registry, picked)


def hits_of(data: Any, place: str) -> list[Hit]:  # noqa: ANN401 - a tool's result
    """A search result as hits: each item of a JSON result (its title, its text), else each paragraph of its text;
    none when it says it found nothing."""
    if isinstance(data, str):
        with contextlib.suppress(ValueError):
            data = json.loads(data)
    if isinstance(data, (dict, list)):
        items = _items(data)
        return [_hit(item, place) for item in items[:ITEMS_MAX]]
    text = str(data or "").strip()
    if not text or _NO_RESULTS.search(text[:400]):
        return []
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    return [Hit(name=f"{place}: {p.splitlines()[0][:80]}", text=p[:HIT_TEXT_MAX_CHARS]) for p in paragraphs[:ITEMS_MAX]]


def _items(data: Any) -> list[Any]:  # noqa: ANN401
    """The longest list in a JSON result (its results; an empty one is no results), else the result itself as one
    item."""
    best: list[Any] | None = None
    stack: list[Any] = [data]
    while stack:
        value = stack.pop()
        if isinstance(value, list):
            items = cast("list[Any]", value)
            if best is None or len(items) > len(best):
                best = items
            stack.extend(items)
        elif isinstance(value, dict):
            stack.extend(cast("dict[str, Any]", value).values())
    if best is not None:
        return best
    return [data] if isinstance(data, dict) and data else []


def _hit(item: Any, place: str) -> Hit:  # noqa: ANN401
    if isinstance(item, dict):
        record = cast("dict[str, Any]", item)
        title = next((str(record[k]) for k in _TITLE_KEYS if isinstance(record.get(k), (str, int)) and record.get(k)), "")
        text = json.dumps(record, ensure_ascii=False, default=str)
    else:
        title, text = str(item)[:80], str(item)
    return Hit(name=f"{place}: {title[:80]}" if title else place, text=text[:HIT_TEXT_MAX_CHARS])


async def search_sources(
    searches: Sequence[SourceSearch],
    subject: str,
    *,
    registry: ToolRegistry,
    scope: Callable[[], AbstractContextManager[object]] | None,
    log: HarnessLog,
    timeout_s: float = SOURCE_TIMEOUT_S,
) -> list[Searched]:
    """Each search called once with the subject, in the run's tool scope; a failed or slow one shows no hits."""

    async def one(search: SourceSearch) -> Searched:
        try:
            tool = registry.resolve_by_name(search.name)
            with scope() if scope is not None else contextlib.nullcontext():
                out = await asyncio.wait_for(tool.execute(**{search.argument: subject}), timeout_s)
        except Exception as exc:
            log.warning("grounding.source_failed", place=search.place, error=type(exc).__name__)
            return Searched(search, None)
        if not getattr(out, "success", False):
            return Searched(search, None)
        return Searched(search, hits_of(getattr(out, "data", None), search.place))

    return [await one(s) for s in searches]


__all__ = [
    "SEARCH_WORDS",
    "first_use_searches",
    "SourceSearch",
    "Searched",
    "hits_of",
    "runs_without_asking",
    "search_sources",
    "source_searches",
]

"""QA-27: the knowledge read gate matrix, held once and asserted on every surface, path and backend.

A cell is a record's ownership (seen by the reader, alice) crossed with a publication of its container. ``access``
decides what alice may do with it, written from the documented rules (P2.13 §4 and §6, the trash rule of the
record-visibility work), not from the code; ``expected`` says what each knowledge surface then shows. The unit
level seeds the cells into ``InMemoryRecordAccess``; the integration level into a real Neo4j and a real ArangoDB.

``RunTools`` calls a run's knowledge tools the way each path dispatches them and answers the text the model (or
the engine) reads, so a cell's answer is compared as the reader would see it:

* ``loop``: today's agent loop, ``ToolExecutor`` over the registry, the full-record read registered as its
  citation hook registers it;
* ``durable``: the durable kernel's ``RegistryToolPort`` over the run's catalog, the read granted late as
  ``LATE_TOOLS`` grants it;
* ``engine``: ``RunToolEndpoint`` over the engine's grant (``engine_grant``, ``with_record_fetch``), as an engine's
  MCP calls reach it through the tool gateway.
"""

from __future__ import annotations

import functools
import itertools
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Literal, cast

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable, Mapping

    from pydantic import JsonValue

    from harness_core.effects.types import ToolEffect
    from harness_core.tools.registry import ToolRegistry

Ownership = Literal["own", "group", "org", "other", "trashed", "missing"]
Publication = Literal["none", "full", "answer_only", "revoked", "expired", "other_audience"]
Access = Literal["own", "published_full", "published_answer_only", "none"]
Outcome = Literal["served", "answer_only", "hidden"]
PathName = Literal["loop", "durable", "engine"]
Reader = Literal["alice", "carol"]
Surface = Literal[
    "search", "fetch", "fetch_surfaced", "lookup_id", "lookup_external", "lookup_url", "navigate", "list", "slack_thread",
    "slack_nearby", "projection",
]

OWNERSHIPS: Final[tuple[Ownership, ...]] = ("own", "group", "org", "other", "trashed", "missing")
PUBLICATIONS: Final[tuple[Publication, ...]] = ("none", "full", "answer_only", "revoked", "expired", "other_audience")
PATHS: Final[tuple[PathName, ...]] = ("loop", "durable", "engine")
# A surface whose answer names the record it was asked about: a refusal must read alike for every hidden cell.
BY_IDENTIFIER: Final[frozenset[Surface]] = frozenset(
    {"fetch", "fetch_surfaced", "lookup_id", "lookup_external", "lookup_url", "navigate", "slack_thread", "slack_nearby", "projection"}
)


@dataclass(frozen=True)
class Cell:
    ownership: Ownership
    publication: Publication

    @property
    def key(self) -> str:
        return f"{self.ownership}-{self.publication}".replace("_", "-")


# A missing id is in no container, so it has no publication column.
CELLS: Final[tuple[Cell, ...]] = (
    *(Cell(o, p) for o, p in itertools.product(OWNERSHIPS, PUBLICATIONS) if o != "missing"),
    Cell("missing", "none"),
)


def access(cell: Cell, reader: Reader = "alice") -> Access:
    """Own access wins whatever is published; another user's live record is disclosed only by a live publication
    whose audience holds the reader, in its mode; the trash and an id that does not exist disclose nothing.

    Alice owns the ``own`` records and is in the group; every user of the org has the ``org`` ones. The ``full``
    and ``answer_only`` publications are to alice, ``other_audience`` (``full``) to carol."""
    if cell.ownership in ("trashed", "missing"):
        return "none"
    if cell.ownership == "org" or (reader == "alice" and cell.ownership in ("own", "group")):
        return "own"
    if reader == "carol":
        return "published_full" if cell.publication == "other_audience" else "none"
    if cell.publication == "full":
        return "published_full"
    if cell.publication == "answer_only":
        return "published_answer_only"
    return "none"


# Publications disclose a record to retrieval (search, the prefetch), to the full read and to a shared view's
# citations (P2.13 §4, §6); navigation and lookup show only what the reader may open themselves, and the live
# Slack history only around a message the reader may read themselves.
_SHOWN: Final[dict[Surface, frozenset[Access]]] = {
    "search": frozenset({"own", "published_full", "published_answer_only"}),
    "fetch": frozenset({"own", "published_full"}),
    "fetch_surfaced": frozenset({"own", "published_full"}),
    "lookup_id": frozenset({"own"}),
    "lookup_external": frozenset({"own"}),
    "lookup_url": frozenset({"own"}),
    "navigate": frozenset({"own"}),
    "list": frozenset({"own"}),
    "slack_thread": frozenset({"own", "published_full"}),
    "slack_nearby": frozenset({"own"}),
    "projection": frozenset({"own", "published_full", "published_answer_only"}),
}


def expected(surface: Surface, cell: Cell, reader: Reader = "alice") -> Outcome:
    decided = access(cell, reader)
    if decided in _SHOWN[surface]:
        return "served"
    if surface in ("fetch", "fetch_surfaced") and decided == "published_answer_only":
        return "answer_only"
    return "hidden"


# What an answer says when nothing was shown, besides an empty result.
ANSWER_ONLY_NOTE: Final = "published for answers only"
NEARBY_REFUSAL: Final = "is not an indexed Slack message available to you"
UNGRANTED: Final = "not granted to this run"
# Surfaces that show a record by its name and id; the others by its content.
NAMED: Final[frozenset[Surface]] = frozenset({"lookup_id", "lookup_external", "lookup_url", "navigate", "list"})


def expected_on(surface: Surface, path: PathName, cell: Cell) -> Outcome:
    """``expected`` as each path serves it: an engine reads in full only what its own calls surfaced, and nothing
    has before a run searches."""
    if surface == "fetch" and path == "engine":
        return "hidden"
    return expected(surface, cell)


def outcome(surface: Surface, text: str, *, content: str, name: str) -> Outcome:
    """What an answer showed of a record: its content or its name (served), the answer-only refusal, or nothing."""
    if surface == "slack_nearby":
        return "hidden" if NEARBY_REFUSAL in text or UNGRANTED in text else "served"
    if (name if surface in NAMED else content) in text:
        return "served"
    if surface in ("fetch", "fetch_surfaced") and ANSWER_ONLY_NOTE in text:
        return "answer_only"
    return "hidden"


def normalized(text: str, names: Iterable[str]) -> str:
    """``text`` with each of the cell's own names (its ids, link, label) replaced: what stays must not differ."""
    for name in sorted({n for n in names if n}, key=len, reverse=True):
        text = text.replace(name, "<ID>")
    return re.sub(r"inv_[0-9a-f-]+_[0-9]+", "<INV>", text)


# id recovery's last hint names the listed record spelled most like the requested id. The requested id is the
# cell's own name and the record named is one of the list above it, which stays compared, so the hint can tell
# nothing the list does not.
_CLOSEST_ID = re.compile(r"^The closest to (?:the id you used|'[^'\n]*') is .*\n", re.MULTILINE)


def without_closest_id(text: str) -> str:
    """``text`` without id recovery's closest-id hint."""
    return _CLOSEST_ID.sub("", text)


def assert_one_refusal(surface: Surface, path: str, answers: Mapping[Cell, str]) -> None:
    """Every hidden cell's answer, its own names set aside, is one text: nothing tells the reader a record exists."""
    distinct = {text: cell for cell, text in answers.items()}
    assert len(distinct) == 1, (surface, path, {cell.key: text[:300] for text, cell in distinct.items()})


@dataclass
class RunTools:
    """One run's tools on one path: ``call(name, arguments)`` answers the text the model (or engine) reads."""

    path: PathName
    registry: ToolRegistry
    call: Callable[[str, dict[str, JsonValue]], Awaitable[str]]


def _text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(getattr(p, "text", "") or (p.get("text", "") if isinstance(p, dict) else "")) for p in cast("list[object]", content))
    return str(content)


async def run_tools(
    path: PathName, context: Any, registry: ToolRegistry, effects: Mapping[str, ToolEffect]  # noqa: ANN401 - the legacy AgentContext
) -> RunTools:
    from app.agents.agent_loop.hooks.citations import ensure_fetch_full_record_available
    from app.harness.assembly.assembler import late_effect
    from app.harness.citations import EVENT, CitationJournal
    from app.harness.engines.durable import engine_grant, with_record_fetch
    from app.harness.gateway.citations import JournalCitationSink
    from harness_core.effects import EffectRunner, RequestEffectLog
    from harness_core.effects.types import ToolContext
    from harness_core.gateway import RunToolEndpoint
    from harness_core.invoker import InvocationScope
    from harness_core.journal.types import NewEvent
    from harness_core.kernel.core.messages import ToolCall
    from harness_core.kernel.core.tool_content import tool_message_content
    from harness_core.logs import HarnessLog
    from harness_core.testing.contracts.tool_endpoint import BINDING, claims_for
    from harness_core.testing.ids import SeededIds
    from harness_core.tools.durable_port import RegistryToolPort
    from harness_core.tools.executor import ToolExecutor

    calls = itertools.count(1)
    if path == "loop":
        ensure_fetch_full_record_available(context, registry=registry)
        executor = ToolExecutor(registry)

        async def loop(name: str, arguments: dict[str, JsonValue]) -> str:
            result = await executor.call_tool(ToolCall(id=f"call-{next(calls)}", name=name, arguments=arguments))
            # As the loop hands a result to the model: a dict or a model as JSON (KN2), never its Python repr.
            return _text(tool_message_content(result.content))

        return RunTools(path, registry, loop)
    if path == "durable":
        ensure_fetch_full_record_available(context, registry=registry)
        port = RegistryToolPort(registry, effects, late=functools.partial(late_effect, registry))

        async def durable(name: str, arguments: dict[str, JsonValue]) -> str:
            n = next(calls)
            ctx = ToolContext(BINDING.run_id, BINDING.tenant, f"t{n}.0", BINDING.attempt_id, f"key-{n}")
            return _text((await port.execute(ToolCall(id=f"t{n}.0", name=name, arguments=arguments), ctx)).content)

        return RunTools(path, registry, durable)
    log = HarnessLog.named("tests.qa27.engine")

    def reads(effect: ToolEffect) -> bool:
        return effect.effect == "read"

    granted = with_record_fetch(registry, engine_grant(registry, dict(effects), reads), context, reads, log)
    citations = CitationJournal(context, registry=registry, log=log)

    def citations_for(invocation_id: str) -> NewEvent | None:
        delta = citations.delta(invocation_id)
        return NewEvent(EVENT, delta, critical=False) if delta is not None else None

    endpoint = RunToolEndpoint(
        binding=BINDING,
        catalog=RegistryToolPort(registry, granted),
        effects=EffectRunner(RequestEffectLog()),
        scope=InvocationScope(BINDING.run_id, BINDING.tenant, BINDING.attempt_id),
        ids=SeededIds(),
        citations=JournalCitationSink(context.tool_state, citations_for),
    )

    async def engine(name: str, arguments: dict[str, JsonValue]) -> str:
        return (await endpoint.call_tool(claims_for(BINDING), name, arguments)).text

    return RunTools(path, registry, engine)


__all__ = [
    "ANSWER_ONLY_NOTE",
    "BY_IDENTIFIER",
    "CELLS",
    "NAMED",
    "NEARBY_REFUSAL",
    "OWNERSHIPS",
    "PATHS",
    "PUBLICATIONS",
    "UNGRANTED",
    "Access",
    "Cell",
    "Outcome",
    "Ownership",
    "PathName",
    "Publication",
    "Reader",
    "RunTools",
    "Surface",
    "access",
    "assert_one_refusal",
    "expected",
    "expected_on",
    "normalized",
    "outcome",
    "run_tools",
    "without_closest_id",
]

"""C7.14: a run's own connector reads, asked again with the same arguments, are answered from the first answer.

The rule (WP-91, ``harness_core/tools/read_cache.py``): only reads a connector's tool declares (effect
``read``, idempotent, sink ``self``, ``CONNECTOR_RULE``); keyed by org, user, run, tool and canonical arguments, never
shared between people or runs; a short TTL and bounds; a call of the toolset that is not a read drops its entries; a
hit carries the first answer's integrity and provenance unchanged (ADR-RUN-01 D3); failures are never kept.

Both seams: the loop's ``RegistryToolPort`` and the Tool Gateway's catalog (``RunToolEndpoint``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.effects import EffectRunner, RequestEffectLog
from harness_core.effects.types import ToolContext, ToolEffect, ToolExecution
from harness_core.gateway.endpoint import (  # pyright: ignore[reportPrivateUsage]
    RunToolEndpoint,
    _CitingCatalog,
)
from harness_core.invoker import InvocationScope
from harness_core.journal.types import NewEvent
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.durable import classify_tool_effect
from harness_core.provenance.rules import (
    CONNECTOR_RULE,
    MCP_RULE,
    RECORDS_RULE,
    WEB_RULE,
)
from harness_core.provenance.stamp import PROVENANCE_EVENT, ProvenanceStamp
from harness_core.provenance.types import ConnectorItem, ResultProvenance
from harness_core.testing.contracts.tool_endpoint import BINDING, claims_for
from harness_core.testing.ids import SeededIds
from harness_core.tools.base import ParameterType, Tag, ToolParameter
from harness_core.tools.decorators import tool
from harness_core.tools.durable_port import RegistryToolPort
from harness_core.tools.read_cache import (
    MAX_ENTRY_BYTES,
    TTL_S,
    ReadScope,
    RunReadCache,
    cacheable_reads,
)
from harness_core.tools.registry import ToolRegistry
from harness_core.tools.toolset import ToolsetBuilder

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic import JsonValue

    from harness_core.kernel.core.types import ToolResult

ORG, USER, RUN = "org1", "u-alice", "run1"
CTX = ToolContext(RUN, ORG, "t1.0", "a1", f"{ORG}:{RUN}:t1.0")
_Q = [ToolParameter(name="q", type=ParameterType.STRING, description="Query", required=True)]
CITED = "x.test.cited"


class Crm:
    """A connector: every call is counted, a read names the item it read (as a citation delta would)."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.cited: list[str] = []
        self.fail = False
        self.big = False

    @tool(path="/tools/crm/search", short_description="Search", description="Search the CRM.", parameters=_Q)
    async def search(self, q: str) -> str:
        self.calls.append(f"search:{q}")
        if self.fail:
            raise RuntimeError("crm unavailable")
        self.cited.append(q)
        return ("x" * (MAX_ENTRY_BYTES + 10)) if self.big else f"found {q} ({len(self.calls)})"

    @tool(path="/tools/crm/update", short_description="Update", description="Update a record.", parameters=_Q)
    async def update(self, q: str) -> str:
        self.calls.append(f"update:{q}")
        return f"updated {q}"


class Notes:
    @tool(path="/tools/notes/write", short_description="Write", description="Write a note.", parameters=_Q)
    async def write(self, q: str) -> str:
        return f"noted {q}"


def _effect(name: str, kind: str, toolset: str) -> ToolEffect:
    base = classify_tool_effect(name, [Tag("category", kind)])
    return ToolEffect(f"{toolset}/{name}", base.effect, base.idempotent, base.sink)


EFFECTS = {
    "crm__search": _effect("crm__search", "read", "crm@inst-1"),
    "crm__update": _effect("crm__update", "write", "crm@inst-1"),
    "notes__write": _effect("notes__write", "write", "notes@inst-2"),
}
RULES = dict.fromkeys(EFFECTS, CONNECTOR_RULE)


def _registry(crm: Crm) -> ToolRegistry:
    registry = ToolRegistry()
    ToolsetBuilder(crm, name="crm", description="CRM", path_prefix="/tools/crm").register_into(registry)
    ToolsetBuilder(Notes(), name="notes", description="Notes", path_prefix="/tools/notes").register_into(registry)
    return registry


def _sources(events: Sequence[NewEvent]) -> ResultProvenance | None:
    items = [e.data["item"] for e in events if e.type == CITED]
    if not items:
        return None
    return ResultProvenance("principals", "first_party", tuple(ConnectorItem("crm", str(i)) for i in items))


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _cache(user: str = USER, run: str = RUN, clock: _Clock | None = None, **kw: int) -> RunReadCache[ToolResult]:
    return RunReadCache(ReadScope(ORG, user, run), cacheable_reads(EFFECTS, RULES), clock=clock or _Clock(), **kw)


def _port(crm: Crm, cache: RunReadCache[ToolResult] | None) -> RegistryToolPort:
    async def present(call: ToolCall, result: ToolResult) -> tuple[NewEvent, ...]:
        # A citation delta: what changed since the last one, so a read whose items are already cited names none.
        fresh = [q for q in crm.cited if q not in seen]
        seen.update(fresh)
        return tuple(NewEvent(CITED, {"item": q}, critical=False) for q in fresh)

    seen: set[str] = set()
    stamp = ProvenanceStamp.for_tools(EFFECTS, RULES, journal=True, sources_of=_sources, clock=lambda: "2026-11-06T00:00:00Z")
    return RegistryToolPort(
        _registry(crm), EFFECTS, provenance=stamp, present=present, read_cache=cache,
        rate=lambda events: "external" if any(e.type == CITED for e in events) else None,
    )


def _call(name: str, q: str, call_id: str = "c1") -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments={"q": q})


def _provenance(execution: ToolExecution) -> JsonValue:
    [record] = [e for e in execution.events if e.type == PROVENANCE_EVENT]
    return record.data["provenance"]


def test_wp91_tcache_1_only_declared_connector_reads_are_cacheable() -> None:
    """WP91-TCACHE-1: a connector tool that is a read, idempotent, with the person's own access; nothing else."""
    sends = ToolEffect("crm@inst-1/crm__lookup", "read", True, SinkDescriptor(egress="external", args_leave_boundary=True))
    effects = {**EFFECTS, "crm__lookup": sends, "web__fetch": EFFECTS["crm__search"], "mcp__x": EFFECTS["crm__search"],
               "knowledge__search": EFFECTS["crm__search"]}
    rules = {**RULES, "crm__lookup": CONNECTOR_RULE, "web__fetch": WEB_RULE, "mcp__x": MCP_RULE,
             "knowledge__search": RECORDS_RULE}
    assert cacheable_reads(effects, rules) == frozenset({"crm__search"})


async def test_wp91_tcache_2_the_same_read_is_answered_once_with_the_same_facts() -> None:
    """WP91-TCACHE-2: the second identical read does not reach the connector, and answers with the first answer's
    content, integrity and provenance (its connector item), though its own presentation names no new citation."""
    crm = Crm()
    cache = _cache()
    port = _port(crm, cache)
    first = await port.execute(_call("crm__search", "acme"), CTX)
    second = await port.execute(_call("crm__search", "acme", "c2"), ToolContext(RUN, ORG, "t2.0", "a1", "k2"))
    assert crm.calls == ["search:acme"]
    assert second.content == first.content == "found acme (1)"
    assert second.integrity == first.integrity == "external"
    assert _provenance(second) == _provenance(first)
    assert _provenance(first)["sources"] == [{"kind": "connector", "connector": "crm", "externalRecordId": "acme"}]  # type: ignore[index]
    assert not any(e.type == CITED for e in second.events)
    assert (cache.hits, cache.misses) == (1, 1)


async def test_wp91_tcache_3_never_shared_across_arguments_people_or_runs() -> None:
    """WP91-TCACHE-3: other arguments, another person's run or another run each read the connector again, and a call
    from outside the cache's run is never answered from it."""
    crm = Crm()
    port = _port(crm, _cache())
    _ = await port.execute(_call("crm__search", "acme"), CTX)
    _ = await port.execute(_call("crm__search", "globex"), CTX)
    _ = await _port(crm, _cache(user="u-bob")).execute(_call("crm__search", "acme"), CTX)
    _ = await _port(crm, _cache(run="run2")).execute(_call("crm__search", "acme"), ToolContext("run2", ORG, "i", "a", "k"))
    _ = await port.execute(_call("crm__search", "acme"), ToolContext("run9", ORG, "i", "a", "k"))
    assert crm.calls == ["search:acme", "search:globex", "search:acme", "search:acme", "search:acme"]


async def test_wp91_tcache_4_a_write_to_the_toolset_drops_its_reads_and_only_its_own() -> None:
    """WP91-TCACHE-4: a call of the toolset that is not a read drops that toolset's entries before it runs; a write to
    another toolset leaves them."""
    crm = Crm()
    cache = _cache()
    port = _port(crm, cache)
    _ = await port.execute(_call("crm__search", "acme"), CTX)
    _ = await port.execute(_call("notes__write", "n"), CTX)
    _ = await port.execute(_call("crm__search", "acme"), CTX)
    assert crm.calls == ["search:acme"]
    _ = await port.execute(_call("crm__update", "acme"), CTX)
    after = await port.execute(_call("crm__search", "acme"), CTX)
    assert crm.calls == ["search:acme", "update:acme", "search:acme"]
    assert after.content == "found acme (3)"


async def test_wp91_tcache_5_failures_are_never_kept_and_entries_expire_and_are_bounded() -> None:
    """WP91-TCACHE-5: a failed read is read again; an entry past ``TTL_S`` is read again; the cache holds at most its
    entry bound (oldest first out) and never keeps an answer over ``MAX_ENTRY_BYTES``."""
    crm = Crm()
    clock = _Clock()
    cache = _cache(clock=clock, max_entries=2)
    port = _port(crm, cache)
    crm.fail = True
    failed = await port.execute(_call("crm__search", "acme"), CTX)
    assert failed.status == "error" and len(cache) == 0
    crm.fail = False
    _ = await port.execute(_call("crm__search", "acme"), CTX)
    clock.now += TTL_S + 1
    _ = await port.execute(_call("crm__search", "acme"), CTX)
    assert crm.calls == ["search:acme"] * 3
    for q in ("b", "c"):
        _ = await port.execute(_call("crm__search", q), CTX)
    assert len(cache) == 2
    _ = await port.execute(_call("crm__search", "acme"), CTX)
    assert crm.calls[-1] == "search:acme"
    crm.big = True
    _ = await port.execute(_call("crm__search", "huge"), CTX)
    _ = await port.execute(_call("crm__search", "huge"), CTX)
    assert crm.calls[-2:] == ["search:huge", "search:huge"]


async def test_wp91_tcache_6_a_run_with_no_person_caches_nothing() -> None:
    """WP91-TCACHE-6: without a user the ACL scope is unknown: every read reaches the connector."""
    crm = Crm()
    port = _port(crm, _cache(user=""))
    for _ in range(2):
        _ = await port.execute(_call("crm__search", "acme"), CTX)
    assert crm.calls == ["search:acme", "search:acme"]


class _Sink:
    """A citation sink whose delta names the items the connector read, and calls them outsider-written (KN-08)."""

    def __init__(self, crm: Crm) -> None:
        self._crm = crm
        self._reported = 0

    def ref_for(self, url: str) -> str:
        return url

    def cite(self, text: str) -> tuple[()]:
        return ()

    def record(self, invocation_id: str) -> tuple[NewEvent, ...]:
        fresh = self._crm.cited[self._reported :]
        self._reported = len(self._crm.cited)
        return tuple(NewEvent(CITED, {"item": q}, critical=False) for q in fresh)

    def untrusted_sources(self, events: Sequence[NewEvent]) -> tuple[str, ...]:
        return tuple(f"crm:{e.data['item']}" for e in events if e.type == CITED)


def _gateway(crm: Crm) -> tuple[_CitingCatalog, RunReadCache[ToolExecution]]:
    cache: RunReadCache[ToolExecution] = RunReadCache(ReadScope(ORG, USER, RUN), cacheable_reads(EFFECTS, RULES))
    stamp = ProvenanceStamp.for_tools(EFFECTS, RULES, journal=True, sources_of=_sources)
    return _CitingCatalog(RegistryToolPort(_registry(crm), EFFECTS), _Sink(crm), stamp, cache), cache


async def test_wp91_tcache_7_the_gateway_answers_a_repeated_read_with_the_same_facts() -> None:
    """WP91-TCACHE-7: through the Tool Gateway's catalog the repeated read is answered from the first answer: its
    provenance, its integrity and its untrusted sources, with no citation delta riding it again."""
    crm = Crm()
    catalog, cache = _gateway(crm)
    first = await catalog.execute(_call("crm__search", "acme"), CTX)
    second = await catalog.execute(_call("crm__search", "acme", "c2"), ToolContext(RUN, ORG, "i2", "a1", "k2"))
    assert crm.calls == ["search:acme"]
    assert second.content == first.content and second.integrity == first.integrity == "external"
    assert _provenance(second) == _provenance(first)
    assert catalog.untrusted["i2"] == catalog.untrusted["t1.0"] == ("crm:acme",)
    assert not any(e.type == CITED for e in second.events)
    _ = await catalog.execute(_call("crm__update", "acme"), CTX)
    _ = await catalog.execute(_call("crm__search", "acme"), CTX)
    assert crm.calls == ["search:acme", "update:acme", "search:acme"]
    assert cache.hits == 1


async def test_wp91_tcache_8_through_the_endpoint_an_engine_reads_once() -> None:
    """WP91-TCACHE-8: an engine calling the same read twice over MCP reaches the connector once and reads the same
    answer both times; the effect log still records both calls."""
    crm = Crm()
    log = RequestEffectLog()
    endpoint = RunToolEndpoint(
        binding=BINDING,
        catalog=RegistryToolPort(_registry(crm), EFFECTS),
        effects=EffectRunner(log),
        scope=InvocationScope(BINDING.run_id, BINDING.tenant, BINDING.attempt_id),
        ids=SeededIds(),
        citations=_Sink(crm),
        read_cache=RunReadCache(ReadScope(BINDING.tenant, USER, BINDING.run_id), cacheable_reads(EFFECTS, RULES)),
    )
    one = await endpoint.call_tool(claims_for(BINDING), "crm__search", {"q": "acme"})
    two = await endpoint.call_tool(claims_for(BINDING), "crm__search", {"q": "acme"})
    assert (one.is_error, two.is_error) == (False, False)
    # Both read as outsider-written (KN-08): the hit keeps the first answer's untrusted sources.
    assert one.text == two.text and one.text.endswith("\nfound acme (1)") and "crm:acme" in two.text
    assert crm.calls == ["search:acme"]


async def test_wp91_fu1_each_lookup_is_counted_as_a_hit_a_miss_or_an_expired_entry() -> None:
    """WP91-FU-1: the cache's hits reached only logs and in-process counters. Each lookup is now an observation on
    ``ReadCacheMetrics``, labelled by executor and outcome only (no tool, org or run)."""
    from harness_core.telemetry.metrics import (
        ReadCacheObserved,
        RecordingReadCacheMetrics,
    )

    crm = Crm()
    clock = _Clock()
    metrics = RecordingReadCacheMetrics()
    cache = RunReadCache(
        ReadScope(ORG, USER, RUN), cacheable_reads(EFFECTS, RULES), clock=clock, metrics=metrics, executor="engine"
    )
    port = _port(crm, cache)
    _ = await port.execute(_call("crm__search", "acme"), CTX)
    _ = await port.execute(_call("crm__search", "acme", "c2"), CTX)
    clock.now += TTL_S + 1
    _ = await port.execute(_call("crm__search", "acme", "c3"), CTX)
    assert [(o.executor, o.outcome) for o in metrics.lookups] == [
        ("engine", "miss"), ("engine", "hit"), ("engine", "expired"),
    ]
    assert (cache.hits, cache.misses) == (1, 2)
    assert metrics.lookups[0] == ReadCacheObserved("engine", "miss")


def test_wp91_fu1_both_executors_count_their_lookups_on_the_process_metrics() -> None:
    """The loop's tool port and the engine's Tool Gateway each build their cache with the process's counter."""
    import inspect

    from app.harness.assembly import assembler
    from app.harness.engines import durable

    for module, executor in ((assembler, "loop"), (durable, "engine")):
        source = inspect.getsource(module)
        call = source[source.index("read_cache=RunReadCache(") :]
        call = call[: call.index("\n        ),") if executor == "loop" else call.index("\n            ),")]
        assert "metrics=READ_CACHE_METRICS" in call and f'executor="{executor}"' in call, module.__name__

"""A run's cache of its own connector reads (C7.14): the same read with the same arguments, asked again within the
run, is answered from the first answer.

The rules (WP-91):

- **Only declared reads.** A tool is cached only when its declaration says so: a connector's tool (``CONNECTOR_RULE``)
  whose effect is ``read``, ``idempotent``, and reads the connector with the person's own access (sink ``self``).
  Knowledge tools, web tools, MCP servers, platform tools and anything with a per-call write are never cached. Nothing
  is guessed from a name.
- **One person, one run.** The key is (org, user, run, tool, canonical arguments). A connector's access is the
  caller's own credential at the outside service, and the harness cannot prove two people's access equal there, so an
  answer is never shared between people. It is never shared between runs either: a new run reads the service again.
- **Fresh enough.** An entry lives ``TTL_S`` at most, the cache holds ``MAX_ENTRIES`` and ``MAX_BYTES``, and a call in
  the run that may change the toolset (any call of it that is not a read) drops that toolset's entries before it runs.
- **Same facts (ADR-RUN-01 D3).** A hit answers with the first answer's content, integrity and ``ResultProvenance``
  (its sources and their fetched-at) unchanged, so citations, the flow guard and the provenance ledger see what the
  read produced. Only the logs and the lookup counter say it was a hit. A failure, a question, a terminal answer, an
  artifact, a receipt or a dry run's shadow is never kept.
- **Counted.** Each lookup is a hit, a miss or an expired entry on ``ReadCacheMetrics`` (WP91-FU-1), by executor only.

The two places a run executes tools hold one each: the loop's tool port and the Tool Gateway's catalog.
"""

from __future__ import annotations

import json
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from harness_core.contracts import canonical_digest
from harness_core.logs import HarnessLog
from harness_core.provenance.rules import CONNECTOR_RULE
from harness_core.telemetry.metrics import NOOP_READ_CACHE_METRICS, ReadCacheObserved

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from pydantic import JsonValue

    from harness_core.effects.types import (
        EffectKind,
        ToolContext,
        ToolEffect,
        ToolExecution,
    )
    from harness_core.provenance.rules import ProvenanceRule
    from harness_core.provenance.types import ResultProvenance
    from harness_core.telemetry.metrics import (
        ReadCacheExecutor,
        ReadCacheMetrics,
        ReadCacheOutcome,
    )

# Long enough for a run that asks the same thing again a few steps later; short enough that a change the person makes
# at the service meanwhile is read within minutes.
TTL_S: Final = 120.0
MAX_ENTRIES: Final = 128
MAX_ENTRY_BYTES: Final = 512 * 1024
MAX_BYTES: Final = 8 * 1024 * 1024


def toolset_of(tool_id: str) -> str:
    """``slack@inst-1/search`` -> ``slack@inst-1``: the connector instance a tool reads or writes."""
    return tool_id.split("/", 1)[0]


def cacheable_reads(effects: Mapping[str, ToolEffect], rules: Mapping[str, ProvenanceRule]) -> frozenset[str]:
    """The tools a run may cache, by their declarations alone."""
    return frozenset(
        name
        for name, effect in effects.items()
        if rules.get(name) is CONNECTOR_RULE
        and effect.effect == "read"
        and effect.idempotent
        and effect.sink.egress == "self"
    )


@dataclass(frozen=True, slots=True)
class ReadScope:
    """Whose reads these are. An empty user is no one's: nothing is cached."""

    org_id: str
    user_id: str
    run_id: str


@dataclass(frozen=True, slots=True)
class ReadSlot:
    """Where one call's answer is kept: its key, and its tool."""

    key: str
    toolset: str
    tool_id: str


@dataclass(frozen=True, slots=True)
class CachedRead[P]:
    """The first answer: what the seam replays (``payload``), and the facts it was recorded with."""

    payload: P
    provenance: ResultProvenance | None
    integrity: str
    untrusted: tuple[str, ...]
    stored_at: float
    size: int


def keeps(execution: ToolExecution) -> bool:
    """Whether a read's outcome may be answered again: a plain, successful result and nothing else."""
    return (
        execution.status == "ok"
        and not execution.needs_input
        and execution.answer is None
        and execution.receipt is None
        and execution.shadow is None
        and execution.meta is None
        and not execution.slots
    )


def _size(content: JsonValue) -> int:
    return len(json.dumps(content, default=str, separators=(",", ":")).encode())


class RunReadCache[P]:
    def __init__(
        self,
        scope: ReadScope,
        cacheable: frozenset[str],
        *,
        ttl_s: float = TTL_S,
        max_entries: int = MAX_ENTRIES,
        max_entry_bytes: int = MAX_ENTRY_BYTES,
        max_bytes: int = MAX_BYTES,
        clock: Callable[[], float] = time.monotonic,
        log: HarnessLog | None = None,
        metrics: ReadCacheMetrics = NOOP_READ_CACHE_METRICS,
        executor: ReadCacheExecutor = "loop",
    ) -> None:
        self._scope = scope
        self._metrics = metrics
        self._executor: ReadCacheExecutor = executor
        self._cacheable: frozenset[str] = cacheable if scope.user_id else frozenset[str]()
        self._ttl_s = ttl_s
        self._max_entries = max_entries
        self._max_entry_bytes = max_entry_bytes
        self._max_bytes = max_bytes
        self._clock = clock
        self._log = log or HarnessLog.named("harness_core.tools.read_cache")
        self._entries: OrderedDict[str, tuple[str, CachedRead[P]]] = OrderedDict()
        self._bytes = 0
        self.hits = 0
        self.misses = 0

    def slot(
        self, ctx: ToolContext, name: str, effect: ToolEffect, kind: EffectKind, arguments: Mapping[str, object]
    ) -> ReadSlot | None:
        """The call's slot when its answer may be kept; a call that may change its toolset drops that toolset's
        entries first. A call outside this cache's org or run is never cached."""
        toolset = toolset_of(effect.tool_id)
        if kind != "read":
            self.invalidate(toolset)
            return None
        if name not in self._cacheable or (ctx.org_id, ctx.run_id) != (self._scope.org_id, self._scope.run_id):
            return None
        key = canonical_digest({
            "org": self._scope.org_id,
            "user": self._scope.user_id,
            "run": self._scope.run_id,
            "tool": effect.tool_id,
            "name": name,
            "args": json.loads(json.dumps(dict(arguments), default=str)),
        })
        return ReadSlot(key, toolset, effect.tool_id)

    def hit(self, slot: ReadSlot) -> CachedRead[P] | None:
        found = self._entries.get(slot.key)
        if found is None:
            self._count("miss")
            return None
        _, entry = found
        age = self._clock() - entry.stored_at
        if age > self._ttl_s:
            self._drop(slot.key)
            self._count("expired")
            return None
        self._entries.move_to_end(slot.key)
        self._count("hit")
        self._log.info("tool.read_cache_hit", toolId=slot.tool_id[:200], ageMs=int(age * 1000))
        return entry

    def keep(
        self,
        slot: ReadSlot,
        payload: P,
        execution: ToolExecution,
        *,
        provenance: ResultProvenance | None,
        untrusted: tuple[str, ...] = (),
    ) -> None:
        if not keeps(execution):
            return
        size = _size(execution.content)
        if size > self._max_entry_bytes:
            return
        self._drop(slot.key)
        entry = CachedRead(payload, provenance, execution.integrity, untrusted, self._clock(), size)
        self._entries[slot.key] = (slot.toolset, entry)
        self._bytes += size
        while len(self._entries) > self._max_entries or self._bytes > self._max_bytes:
            self._drop(next(iter(self._entries)))

    def invalidate(self, toolset: str) -> None:
        stale = [key for key, (held, _) in self._entries.items() if held == toolset]
        for key in stale:
            self._drop(key)
        if stale:
            self._log.info("tool.read_cache_invalidated", toolset=toolset[:200], entries=len(stale))

    def __len__(self) -> int:
        return len(self._entries)

    def _count(self, outcome: ReadCacheOutcome) -> None:
        if outcome == "hit":
            self.hits += 1
        else:
            self.misses += 1
        self._metrics.lookup(ReadCacheObserved(self._executor, outcome))

    def _drop(self, key: str) -> None:
        found = self._entries.pop(key, None)
        if found is not None:
            self._bytes -= found[1].size


__all__ = [
    "MAX_BYTES",
    "MAX_ENTRIES",
    "MAX_ENTRY_BYTES",
    "TTL_S",
    "CachedRead",
    "ReadScope",
    "ReadSlot",
    "RunReadCache",
    "cacheable_reads",
    "keeps",
    "toolset_of",
]

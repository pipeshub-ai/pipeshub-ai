"""``ProvenanceStamp``: each tool result recorded with its ``ResultProvenance`` (ADR-RUN-01 D3).

The stamp sits where a call's result and what the call journaled with it (its citation delta) are both known: the
loop's tool port and the Tool Gateway's catalog. It asks the tool's declared rule, joins what the call's own events
name (``sources_of``: the host's reading of its citation state), and journals the record as
``x.pipeshub.provenance.recorded`` with the outcome, which the session's ``ProvenanceLedger`` folds. A journal whose
claim does not name ``provenance_records`` (an older Node) gets no record; the ledger's replay reads that run's
outcomes as it always did.

Built with ``ProvenanceStamp.for_tools``: a tool served without a rule is refused at assembly
(``UndeclaredProvenance``). A tool registered mid-run that names no rule is recorded as unknown and logged.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from harness_core.journal.types import NewEvent
from harness_core.logs import HarnessLog
from harness_core.provenance.rules import ProvenanceRule, ResultFacts, require_rules
from harness_core.provenance.types import ResultProvenance, WebPage

if TYPE_CHECKING:
    from pydantic import JsonValue

    from harness_core.effects.types import ToolExecution

PROVENANCE_EVENT: Final = "x.pipeshub.provenance.recorded"
# A result of a tool nobody declared (registered mid-run with no rule): fail closed.
UNDECLARED: Final = ResultProvenance("unknown", "first_party", ())

EventSources = Callable[[Sequence[NewEvent]], "ResultProvenance | None"]
# A host's look at a result before its provenance is decided (WP83-FU-3): it may set the host's claim
# (``ToolExecution.provenance``) that a ``HostClaim`` rule reads, such as a page from an address only the org reaches.
ResultReview = Callable[[str, "ToolExecution"], Awaitable["ToolExecution"]]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _dated(provenance: ResultProvenance, when: str) -> ResultProvenance:
    if not any(isinstance(s, WebPage) and not s.fetched_at for s in provenance.sources):
        return provenance
    sources = tuple(
        WebPage(s.url, when) if isinstance(s, WebPage) and not s.fetched_at else s for s in provenance.sources
    )
    return ResultProvenance(provenance.readers, provenance.integrity, sources)


class ProvenanceStamp:
    def __init__(
        self,
        rules: Mapping[str, ProvenanceRule],
        *,
        journal: bool,
        sources_of: EventSources | None = None,
        clock: Callable[[], str] = _now,
        log: HarnessLog | None = None,
        review: ResultReview | None = None,
        late_rule: Callable[[str], ProvenanceRule | None] | None = None,
    ) -> None:
        self._rules = dict(rules)
        self._late_rule = late_rule
        self._journal = journal
        self._sources_of = sources_of
        self._review = review
        self._clock = clock
        self._log = log or HarnessLog.named("harness_core.provenance")
        self._warned: set[str] = set()

    @classmethod
    def for_tools(
        cls,
        names: Iterable[str],
        rules: Mapping[str, ProvenanceRule | None],
        *,
        journal: bool,
        sources_of: EventSources | None = None,
        clock: Callable[[], str] = _now,
        log: HarnessLog | None = None,
        review: ResultReview | None = None,
        late_rule: Callable[[str], ProvenanceRule | None] | None = None,
    ) -> ProvenanceStamp:
        """Raises ``UndeclaredProvenance`` when any of ``names`` has no rule. ``late_rule``: the rule of a tool granted
        mid-run (an MCP server granted whole names its tools at its first listing, D-LAZY-2)."""
        return cls(
            require_rules(names, rules), journal=journal, sources_of=sources_of, clock=clock, log=log, review=review,
            late_rule=late_rule,
        )

    def declares(self, name: str) -> bool:
        return self._rule(name) is not None

    def _rule(self, name: str) -> ProvenanceRule | None:
        rule = self._rules.get(name)
        if rule is None and self._late_rule is not None:
            rule = self._late_rule(name)
            if rule is not None:
                self._rules[name] = rule
        return rule

    def with_rules(self, rules: Mapping[str, ProvenanceRule]) -> ProvenanceStamp:
        """This stamp with more declared tools (the host tools an engine's catalog adds beside the grant)."""
        return ProvenanceStamp(
            {**self._rules, **rules}, journal=self._journal, sources_of=self._sources_of, clock=self._clock,
            log=self._log, review=self._review, late_rule=self._late_rule,
        )

    async def reviewed(self, name: str, execution: ToolExecution) -> ToolExecution:
        """``execution`` after the host's review, before anything decides or caches its provenance."""
        return execution if self._review is None else await self._review(name, execution)

    def provenance(self, name: str, execution: ToolExecution) -> ResultProvenance:
        rule = self._rule(name)
        if rule is None:
            if name not in self._warned:
                self._warned.add(name)
                self._log.warning("tools.provenance_undeclared", tool=name[:128])
            decided = UNDECLARED
        else:
            decided = rule.provenance(ResultFacts(execution.status, execution.provenance))
        named = self._sources_of(execution.events) if self._sources_of is not None and execution.events else None
        joined = decided.join(named) if named is not None else decided
        return _dated(joined, self._clock())

    def record(
        self,
        name: str,
        tool_id: str,
        invocation_id: str,
        execution: ToolExecution,
        provenance: ResultProvenance | None = None,
    ) -> NewEvent:
        """``provenance``: what an earlier call decided for this same result (a run's cached read, C7.14), recorded
        unchanged instead of decided again."""
        rule = self._rule(name)
        decided = provenance if provenance is not None else self.provenance(name, execution)
        data: dict[str, JsonValue] = {
            "invocationId": invocation_id,
            "toolId": tool_id,
            "rule": rule.name if rule is not None else "undeclared",
            "provenance": decided.to_wire(),
        }
        return NewEvent(PROVENANCE_EVENT, data, critical=False)

    def stamp(
        self,
        name: str,
        tool_id: str,
        invocation_id: str,
        execution: ToolExecution,
        provenance: ResultProvenance | None = None,
    ) -> ToolExecution:
        """``execution`` with its provenance record among the events journaled with its outcome."""
        if not self._journal:
            return execution
        event = self.record(name, tool_id, invocation_id, execution, provenance)
        return dataclasses.replace(execution, events=(*execution.events, event))


__all__ = ["PROVENANCE_EVENT", "UNDECLARED", "EventSources", "ProvenanceStamp", "ResultReview"]

"""``ItemLabelLedger``: the reference ``SourceLedger`` over the core item labels (P2.5a).

A tool outcome names its sources in ``labels.confidentiality.sources`` (REPORT
6.5.1). The ledger is session-wide: an engine run's workspace may hold what any
earlier turn of the session put there. An outcome the model saw without that
field, or a session that imported history from before labels, leaves the answer
unknown. A refusal or a cancellation is the harness's own text and names nothing.
A host whose tools record their sources elsewhere provides its own ledger.

An outcome labelled ``integrity: external`` (an outside sender, the web, an MCP server) marks the session as having
read untrusted content (``ReadsUntrusted``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from harness_core.security.labels import label_of

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pydantic import JsonValue

    from harness_core.journal.types import JournalEvent

EXTERNAL = "external"
# Outcomes whose content the harness wrote, not a tool.
HARNESS_OUTCOMES = frozenset({"cancelled"})


class ItemLabelLedger:
    def __init__(self) -> None:
        self._seq = 0
        self._sources: set[str] = set()
        self._unknown = False
        self._refused: set[tuple[str | None, str]] = set()
        self._untrusted = False

    def apply(self, events: Sequence[JournalEvent]) -> None:
        for event in events:
            if event.seq <= self._seq:
                continue
            self._seq = event.seq
            data = event.data
            match event.type:
                case "session.imported":
                    self._unknown = True
                case "tool.denied":
                    self._refused.add((event.run_id, str(data.get("invocationId"))))
                case "tool.outcome":
                    self._outcome(event.run_id, data)
                case _:
                    pass

    def _outcome(self, run_id: str | None, data: Mapping[str, JsonValue]) -> None:
        if data.get("status") in HARNESS_OUTCOMES or (run_id, str(data.get("invocationId"))) in self._refused:
            return
        labels = data.get("labels")
        if isinstance(labels, dict) and labels.get("integrity") == EXTERNAL:
            self._untrusted = True
        confidentiality = labels.get("confidentiality") if isinstance(labels, dict) else None
        sources = confidentiality.get("sources") if isinstance(confidentiality, dict) else None
        if not isinstance(sources, list):
            self._unknown = True
            return
        self._sources.update(str(s) for s in cast("list[JsonValue]", sources))

    def source_records(self, run_id: str) -> frozenset[str] | None:
        del run_id  # session-wide: any earlier run's work may be in the workspace
        label = label_of(self._sources, complete=not self._unknown)
        return label.sources if label.complete else None

    def untrusted_read(self, run_id: str) -> bool:
        del run_id  # session-wide, as the sources
        return self._untrusted


__all__ = ["EXTERNAL", "HARNESS_OUTCOMES", "ItemLabelLedger"]

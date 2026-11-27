"""Citation sources journaled with the calls that produced them (PA-8).

Legacy tools keep what citations are built from in the request's
``tool_state``: the reference numbers the model saw (``citation_ref_mapper``),
record short ids (``record_id_shortener``), the records and blocks
(``virtual_record_id_to_result``, ``final_results``, ``tool_records``), the
record ids navigation surfaced (``known_record_ids``, and in order with their
names ``known_record_names``, which an unresolved fetch shows), the records
fetched with selection and web records; an opted-in run's auxiliary model calls
(the ``usage_ledger`` its ``run_usage`` frame counts) and the MCP servers the
answer's sign-in card lists (``mcp_sign_in_needed``) ride along. That
state lives in one process. A durable run journals what each call added, as
``x.pipeshub.citations.recorded`` with the call's outcome (and what the
pre-run steps added, with the pre-run commit), and a resumed attempt on
another worker replays those deltas before it runs anything, so the answer's
citations, the numbers the model already cites and the ids it passes to
tools resolve as in an uninterrupted run.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

from app.web.addresses import WEB_ADDRESSES_KEY, WebAddresses, web_addresses
from harness_core.contracts import canonical_digest
from harness_core.kernel.core.usage_source import MAIN_SOURCE
from harness_core.kernel.durable.driver import (
    _journal_usage,  # pyright: ignore[reportPrivateUsage]
)
from harness_core.logs import HarnessLog

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from pydantic import JsonValue

    from harness_core.journal.types import JournalEvent

EVENT = "x.pipeshub.citations.recorded"
# A child run's citation sources in its result document (a fresh journal's first delta), adopted by its parent.
CHILD_SOURCES_KEY = "x.pipeshub.citations"
# The opt-in measurement frames (``includeRetrievalContext``) as the presenter journals them.
TELEMETRY_EVENT = "x.pipeshub.telemetry.recorded"


# One delta as journaled. Tool output fills it (a page's blocks, a tool's records), and a journal write over the content
# store's 8 MiB is refused (WP-62); past this the newest blocks are not recorded, and cannot be cited after a resume.
DELTA_MAX_BYTES = 4 * 1024 * 1024
_BOUNDED_FIELDS = ("webRecords", "toolRecords", "finalResults", "webLinks")


def _bound(data: dict[str, Any]) -> int:
    """Drops the last blocks of ``data``'s block lists until it fits ``DELTA_MAX_BYTES``; how many were dropped."""
    from harness_core.contracts import canonical_json

    size = len(canonical_json(data))
    if size <= DELTA_MAX_BYTES:
        return 0
    dropped = 0
    for field in _BOUNDED_FIELDS:
        items = data.get(field)
        while isinstance(items, list) and items and size > DELTA_MAX_BYTES:
            size -= len(canonical_json(items.pop()))
            dropped += 1
    return dropped


def bound_delta(data: dict[str, Any], *, log: HarnessLog, call_id: str | None = None) -> None:
    """``data`` held to ``DELTA_MAX_BYTES`` in place (``_bound``), logged when blocks were dropped."""
    dropped = _bound(data)
    if dropped:
        log.warning("citations.delta_bounded", callId=call_id, dropped=dropped, maxBytes=DELTA_MAX_BYTES)


def _plain(value: object) -> Any:  # noqa: ANN401 - JSON-safe copy of tool state
    return json.loads(json.dumps(value, default=str))


class CitationJournal:
    """The run's citation state: what to journal after a call, and its restore."""

    def __init__(
        self,
        context: Any,  # noqa: ANN401 - the legacy AgentContext the tools run in
        *,
        registry: Any = None,  # noqa: ANN401 - the run's ToolRegistry
        log: HarnessLog | None = None,
    ) -> None:
        self._context = context
        self._registry = registry
        self._log = log or HarnessLog.named("app.harness.citations")
        self._refs = 0
        self._shorts = 0
        self._records: dict[str, str] = {}
        self._blocks = 0
        self._tool_records = 0
        self._known: set[str] = set()
        self._web = 0
        self._web_links = 0
        self._names: list[tuple[str, str]] = []
        self._selected: set[str] = set()
        self._usage = 0
        self._sign_in: list[dict[str, Any]] = []
        self._skill_labels = 0

    @property
    def _ledger(self) -> Any:  # noqa: ANN401 - a UsageLedger
        """An opted-in run's model calls (what its ``run_usage`` frame counts); None otherwise."""
        if getattr(self._context, "include_retrieval_context", False) is not True:
            return None
        return getattr(self._context, "usage_ledger", None)

    @property
    def _state(self) -> dict[str, Any]:
        return self._context.tool_state

    # ---- recording ----

    def delta(self, call_id: str | None = None) -> dict[str, JsonValue] | None:
        """What changed since the last delta, held to ``DELTA_MAX_BYTES``; ``None`` when nothing did."""
        return self._delta(call_id, bounded=True)

    def cited(self, narrow: Callable[[dict[str, JsonValue]], dict[str, JsonValue]]) -> dict[str, JsonValue] | None:
        """The state since the last delta narrowed by ``narrow`` to what an answer cites, then bounded: narrowing comes
        before the bound by construction (WP-81: a long research run's child read 6.8 MB of pages, the bound dropped the
        newest blocks before the narrowing ran, and 21 of the 32 sources its answer cited lost their page)."""
        whole = self._delta(None, bounded=False)
        if not whole:
            return None
        narrowed = narrow(whole)
        bound_delta(cast("dict[str, Any]", narrowed), log=self._log)
        return narrowed

    def _delta(self, call_id: str | None, *, bounded: bool) -> dict[str, JsonValue] | None:
        state = self._state
        data: dict[str, Any] = {}
        mapper = state.get("citation_ref_mapper")
        if mapper is not None:
            pairs = list(mapper.url_to_ref.items())
            if len(pairs) > self._refs:
                data["refs"] = [{"url": url, "ref": ref} for url, ref in pairs[self._refs :]]
                self._refs = len(pairs)
        shortener = state.get("record_id_shortener")
        if shortener is not None:
            entries = shortener.entries()
            if len(entries) > self._shorts:
                data["shortIds"] = [{"full": f, "short": s} for f, s in entries[self._shorts :]]
                self._shorts = len(entries)
        records = state.get("virtual_record_id_to_result")
        if isinstance(records, dict) and records:
            changed: dict[str, Any] = {}
            for vrid, record in records.items():
                plain = _plain(record)
                digest = canonical_digest(plain)
                if self._records.get(str(vrid)) != digest:
                    self._records[str(vrid)] = digest
                    changed[str(vrid)] = plain
            if changed:
                data["records"] = changed
        for key, field, attr in (
            ("final_results", "finalResults", "_blocks"),
            ("tool_records", "toolRecords", "_tool_records"),
            ("web_records", "webRecords", "_web"),
        ):
            items = state.get(key)
            seen = getattr(self, attr)
            if isinstance(items, list) and len(items) > seen:
                data[field] = [_plain(item) for item in items[seen:]]
                setattr(self, attr, len(items))
        given = state.get(WEB_ADDRESSES_KEY)
        if isinstance(given, WebAddresses) and len(given.recorded) > self._web_links:
            data["webLinks"] = [_plain(entry) for entry in given.recorded[self._web_links :]]
            self._web_links = len(given.recorded)
        known = state.get("known_record_ids")
        if isinstance(known, set) and known - self._known:
            data["knownRecordIds"] = sorted(str(k) for k in known - self._known)
            self._known |= {str(k) for k in known}
        self._request_state_delta(state, data)
        if not data:
            return None
        if call_id is not None:
            data["callId"] = call_id
        if bounded:
            bound_delta(data, log=self._log, call_id=call_id)
        self._log.debug(
            "citations.recorded",
            callId=call_id,
            **{field: len(value) for field, value in data.items() if field != "callId"},
        )
        return cast("dict[str, JsonValue]", data)

    def _request_state_delta(self, state: dict[str, Any], data: dict[str, Any]) -> None:
        """Per-request state the tools read back and the model sees: the id-recovery list (re-ordered as
        records are re-shown, so journaled whole when it changes) and the records fetched with selection."""
        from app.agents.actions.knowledge_graph.ops.fetch import (
            FETCH_SELECTED_RECORDS_KEY,
        )
        from app.agents.agent_loop.skills.labelled_manager import SKILL_LABELS_STATE

        names = state.get("known_record_names")
        if isinstance(names, dict):
            current = [(str(rid), str(name or "")) for rid, name in names.items()]
            if current != self._names:
                data["knownRecordNames"] = [{"id": rid, "name": name} for rid, name in current]
                self._names = current
        selected = state.get(FETCH_SELECTED_RECORDS_KEY)
        if isinstance(selected, set) and selected - self._selected:
            data["fetchSelectedRecords"] = sorted(str(r) for r in selected - self._selected)
            self._selected |= {str(r) for r in selected}
        ledger = self._ledger
        if ledger is not None:
            entries = ledger.entries
            # The main loop's calls are journaled as model responses already.
            calls = [
                {"source": source, "tokens": _journal_usage(usage)}
                for source, usage in entries[self._usage :]
                if source != MAIN_SOURCE
            ]
            self._usage = len(entries)
            if calls:
                data["usage"] = calls
        sign_in = getattr(self._context, "mcp_sign_in_needed", None)
        if sign_in and sign_in != self._sign_in:
            data["mcpSignIn"] = _plain(sign_in)
            self._sign_in = _plain(sign_in)
        skill_labels = state.get(SKILL_LABELS_STATE)
        if isinstance(skill_labels, list) and len(skill_labels) > self._skill_labels:
            # The labels of agent-written skill content the call handed the model (FOLLOWUPS1-1): label evidence,
            # nothing to restore.
            data["skillLabels"] = _plain(skill_labels[self._skill_labels :])
            self._skill_labels = len(skill_labels)

    # ---- restoring ----

    async def restore(self, events: Sequence[JournalEvent]) -> None:
        """Replays the run's journaled deltas into this process's state."""
        from app.agents.actions.knowledge_graph.ops.fetch import (
            FETCH_SELECTED_RECORDS_KEY,
        )
        from app.agents.actions.knowledge_graph.ops.results import (
            dedupe_append_final_results,
        )
        from app.agents.agent_loop.hooks.citations import (
            ensure_fetch_full_record_available,
        )
        from app.agents.agent_loop.hooks.completion_gate import restore_gate_state
        from app.agents.agent_loop.mcp_sign_in import merge_sign_in
        from app.utils.chat_helpers import (
            CitationRefMapper,
            get_record_id_shortener_if_enabled,
        )
        from harness_core.kernel.core.responses import TokenUsage

        restore_gate_state(self._context, events)
        recorded = [e for e in events if e.type == EVENT]
        if not recorded:
            self._resume_telemetry(events)
            return
        state = self._state
        ledger = self._ledger
        counts = {"events": len(recorded), "refs": 0, "shortIds": 0, "records": 0, "blocks": 0, "toolRecords": 0}
        for event in recorded:
            data = cast("dict[str, Any]", event.data)
            if data.get("refs"):
                mapper = state.get("citation_ref_mapper")
                if mapper is None:
                    mapper = state["citation_ref_mapper"] = CitationRefMapper()
                pairs = [(r["url"], r["ref"]) for r in data["refs"]]
                mapper.restore(pairs)
                counts["refs"] += len(pairs)
            if data.get("shortIds"):
                shortener = state.get("record_id_shortener") or get_record_id_shortener_if_enabled(state)
                if shortener is not None:
                    shortener.restore([(s["full"], s["short"]) for s in data["shortIds"]])
                    counts["shortIds"] += len(data["shortIds"])
            if data.get("records"):
                existing = state.get("virtual_record_id_to_result")
                state["virtual_record_id_to_result"] = {
                    **(existing if isinstance(existing, dict) else {}),
                    **data["records"],
                }
                counts["records"] += len(data["records"])
            if data.get("finalResults"):
                state["final_results"] = dedupe_append_final_results(
                    state.get("final_results") or [], list(data["finalResults"])
                )
                counts["blocks"] += len(data["finalResults"])
            if data.get("toolRecords"):
                existing_records = state.get("tool_records")
                state["tool_records"] = [
                    *(existing_records if isinstance(existing_records, list) else []),
                    *data["toolRecords"],
                ]
                counts["toolRecords"] += len(data["toolRecords"])
            if data.get("knownRecordIds"):
                known = state.get("known_record_ids")
                state["known_record_ids"] = (known if isinstance(known, set) else set()) | set(
                    data["knownRecordIds"]
                )
            if data.get("webRecords"):
                existing_web = state.get("web_records")
                state["web_records"] = [
                    *(existing_web if isinstance(existing_web, list) else []),
                    *data["webRecords"],
                ]
            for entry in data.get("webLinks") or []:
                if isinstance(entry, dict) and isinstance(entry.get("links"), list):
                    web_addresses(state).note_links(str(entry.get("page") or ""), entry["links"])
            if data.get("knownRecordNames"):
                state["known_record_names"] = {n["id"]: n["name"] for n in data["knownRecordNames"]}
            if data.get("fetchSelectedRecords"):
                state.setdefault(FETCH_SELECTED_RECORDS_KEY, set()).update(data["fetchSelectedRecords"])
            if data.get("usage") and ledger is not None:
                for call in data["usage"]:
                    tokens = call["tokens"]
                    ledger.add(call["source"], TokenUsage(
                        input_tokens=tokens["inputTokens"], output_tokens=tokens["outputTokens"],
                        cache_read_tokens=tokens.get("cacheReadTokens", 0),
                        cache_write_tokens=tokens.get("cacheWriteTokens", 0),
                    ))
            # Merged into what this attempt's MCP discovery noted already.
            for entry in data.get("mcpSignIn") or []:
                merge_sign_in(self._context, entry)
        # What was restored is already journaled: the next delta starts after it.
        _ = self.delta()
        self._resume_telemetry(events)
        if state.get("virtual_record_id_to_result") or state.get("known_record_ids"):
            ensure_fetch_full_record_available(self._context, registry=self._registry)
        self._log.info("citations.rebuilt", **counts)

    def _resume_telemetry(self, events: Sequence[JournalEvent]) -> None:
        """An opted-in run's ``retrieval_context`` frames are journaled (SYNC2-F1): the restored sources are
        reported already, so a resumed attempt's next frame carries only what is new."""
        if getattr(self._context, "include_retrieval_context", False) is not True:
            return
        reported = sum(
            1 for e in events if e.type == TELEMETRY_EVENT and (e.data or {}).get("name") == "retrieval_context"
        )
        self._context.retrieval_ledger.resume(self._state, reported)


__all__ = ["CHILD_SOURCES_KEY", "EVENT", "TELEMETRY_EVENT", "CitationJournal", "bound_delta"]

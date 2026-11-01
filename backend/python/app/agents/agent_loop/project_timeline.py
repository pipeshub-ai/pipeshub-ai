"""A project's or policy's records, each dated, newest first (PROJECT_TIMELINE).

Records about one project or policy are written over weeks or months and
disagree where a decision changed, but an uploaded export is stored with its
upload time, so the model cannot tell the later record from the earlier one.
Before the first turn of an enterprise-search chat, one call on the
organisation's ``query``-role model (``entity_search.verifier_model``) names
the question's subject when it is a project or a policy. One search on that
name, with the user's permissions and the turn's filters, collects up to
``MAX_RECORDS`` of its records. Each is dated by its source system, by the date
its content states, or by what its own text states
(``named_entities.stated_dates``), and the first turn gets them newest first
with a rule for reconciling versions.

``knowledgegraph__search_timeline`` (``ops/timeline``) is a different thing: a
tool the model calls to search a span of time period by period, placing records
by their stored dates. This list is one subject's records whatever their dates,
built before the first turn so the answer can tell which version governs.

Off unless PROJECT_TIMELINE is "true"; HIGH_ACCURACY_MODE does not turn it on.
A failure, or a build slower than ``BUILD_TIMEOUT_S``, leaves the turn as it
would have been without it.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from app.agents.actions.knowledge_graph.ops.entity_search import verifier_model
from app.agents.actions.knowledge_graph.ops.fetch import FETCH_RECORD_TOOL_NAME
from app.agents.agent_loop.protocol.run_usage import (
    PROJECT_TIMELINE_SOURCE,
    llm_usage_reporter,
)
from app.agents.agent_loop.retrieval_ledger import emit_retrieval_context
from app.core.org_context import current_org_id, spawn_in_request
from app.models.source_dates import RecordDates, SourceTimestampOrigin
from app.modules.agents.qna.chat_state import remember_record_ids
from app.modules.named_entities.stated_dates import stated_date
from app.modules.retrieval.provenance import note_retrieved_by
from app.utils.aimodels import coerce_message_content_to_text
from app.utils.chat_helpers import get_record_id_shortener_if_enabled
from app.utils.env_utils import env_bool

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.agents.agent_loop.context import AgentContext
    from app.utils.chat_helpers import RecordIdShortener

logger = logging.getLogger(__name__)

PROJECT_TIMELINE_ENV_VAR = "PROJECT_TIMELINE"
MAX_RECORDS = 30
# The subject call, the search and the reads together; past it the turn goes on without a timeline.
BUILD_TIMEOUT_S = 30.0
_SEARCH_LIMIT = 80
_READ_CONCURRENCY = 8
_FIRST_LINE_CHARS = 110
_NAME_CHARS = 120
_SUBJECT_CHARS = 120
_FETCH_IDS_SHOWN = 8
_TRACE_NAME = "project_timeline"

_SUBJECT_KINDS = (
    "- project: a single piece of work that runs over time and leaves a trail of records, such as an initiative, "
    "a migration, a launch, an incident with its follow-up, a customer deal or escalation, or a workstream.\n"
    "- policy: a single body of rules that gets drafted, revised and approved, such as a standard, a "
    "specification, a contract, a set of requirements, a runbook, or an agreed process, format or naming "
    "convention.\n"
)
_SUBJECT_REPLY = (
    'Answer with JSON only: {"kind": "project" | "policy" | "none", "subject": "<the name the question uses for '
    'it, or a short noun phrase>", "searches": ["<up to two other short phrasings to search for it>"]}'
)
# Most questions are answered by one record, and a list of thirty records only distracts from it.
_SUBJECT_PROMPT = (
    "You are given one question about a company's internal records. Say whether a good answer has to weigh many "
    "records about one project or one policy that were written at different times.\n"
    f"{_SUBJECT_KINDS}"
    "- none: everything else, including a question a single record answers (one limit, setting, name, date, "
    "owner or figure) even if that record is part of a project or policy, and questions about a person, a "
    "definition, or a count or comparison across unrelated things.\n"
    f"{_SUBJECT_REPLY}"
)

_RECONCILE_RULE = (
    "These records come from different times and can disagree. When two of them give different values for "
    "something the question asks about, the later record that settles it governs: an approved or published "
    "document, a merged change or a recorded decision. Call the earlier value superseded and give both dates. A "
    "later discussion, draft or proposal does not replace an approved document unless it records that the "
    "decision changed. Treat the list as leads: read a record before you rely on it."
)

_BASIS_LABELS = {
    "source": "source system",
    "content": "document date",
    "message header": "last message header",
    "log entry": "last log entry",
    "labelled date": "labelled date",
    "file name": "file name",
    "latest date mentioned": "latest date it mentions",
}


def project_timeline_enabled() -> bool:
    return env_bool(PROJECT_TIMELINE_ENV_VAR, False)


@dataclass(frozen=True)
class TimelineSubject:
    kind: str
    name: str
    searches: tuple[str, ...] = ()


@dataclass(frozen=True)
class TimelineEntry:
    virtual_record_id: str
    record_id: str
    name: str
    first_line: str
    day: str | None
    basis: str | None


@dataclass(frozen=True)
class Timeline:
    subject: TimelineSubject
    entries: tuple[TimelineEntry, ...]


def parse_subject(reply: Mapping[str, Any] | None) -> TimelineSubject | None:
    """The subject a reply names, or None for none or a malformed reply."""
    if not isinstance(reply, dict) or reply.get("kind") not in ("project", "policy"):
        return None
    name = reply.get("subject")
    if not isinstance(name, str) or not name.strip():
        return None
    searches = reply.get("searches") if isinstance(reply.get("searches"), list) else []
    extra = tuple(dict.fromkeys(
        text.strip()[:_SUBJECT_CHARS] for text in searches
        if isinstance(text, str) and text.strip() and text.strip().lower() != name.strip().lower()
    ))[:2]
    return TimelineSubject(str(reply["kind"]), name.strip()[:_SUBJECT_CHARS], extra)


async def detect_subject(context: AgentContext, question: str) -> TimelineSubject | None:
    """The question's project or policy, or None when it names neither."""
    # Imported here: app.utils.streaming loads the whole retrieval stack.
    from app.utils.streaming import extract_json_from_string

    llm, _model = await verifier_model(context.tool_state)
    if llm is None:
        return None
    reply = await llm.ainvoke([SystemMessage(content=_SUBJECT_PROMPT), HumanMessage(content=f"Question: {question}")])
    if not isinstance(reply, BaseMessage):
        return None
    report_usage = llm_usage_reporter(context.tool_state, PROJECT_TIMELINE_SOURCE)
    if report_usage is not None:
        report_usage(reply)
    try:
        parsed = extract_json_from_string(coerce_message_content_to_text(reply.content))
    except ValueError:
        logger.info("project_timeline: the subject reply was not a JSON object")
        return None
    return parse_subject(parsed)


def _source_day(graph_record: Mapping[str, Any]) -> tuple[str, str] | None:
    """The day the source system or the content's own date gives, when the stored dates are one of those."""
    dates = RecordDates.from_graph(graph_record)
    if dates.origin is None:
        basis = "source"
    elif dates.origin is SourceTimestampOrigin.CONTENT:
        basis = "content"
    else:
        return None
    ms = dates.modified_ms or dates.created_ms
    return (datetime.fromtimestamp(ms / 1000, UTC).date().isoformat(), basis) if ms else None


def _stored_text(stored: Mapping[str, Any] | None) -> str:
    """The text blocks and table rows of a stored record, in order."""
    containers = (stored or {}).get("block_containers") or {}
    blocks = containers.get("blocks") if isinstance(containers, dict) else None
    parts: list[str] = []
    for block in blocks or ():
        if not isinstance(block, dict):
            continue
        data = block.get("data")
        if block.get("type") == "table_row" and isinstance(data, dict):
            data = data.get("row_natural_language_text")
        elif block.get("type") != "text":
            continue
        if isinstance(data, str) and data.strip():
            parts.append(data)
    return "\n".join(parts)


def date_entry(
    virtual_record_id: str, graph_record: Mapping[str, Any], stored: Mapping[str, Any] | None,
) -> TimelineEntry | None:
    record_id = graph_record.get("_key") or graph_record.get("id")
    if not record_id:
        return None
    name = str(graph_record.get("recordName") or record_id)
    text = _stored_text(stored)
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    day_basis = _source_day(graph_record)
    if day_basis is None and (found := stated_date(name, text)) is not None:
        day_basis = (found.day.isoformat(), found.basis)
    day, basis = day_basis if day_basis else (None, None)
    return TimelineEntry(virtual_record_id, str(record_id), name, first_line[:_FIRST_LINE_CHARS], day, basis)


def order_entries(entries: list[TimelineEntry]) -> list[TimelineEntry]:
    """Dated newest first, then the undated in the order the search ranked them."""
    dated = sorted((entry for entry in entries if entry.day), key=lambda entry: entry.day or "", reverse=True)
    return dated + [entry for entry in entries if not entry.day]


async def collect_timeline(state: Mapping[str, Any], subject: TimelineSubject) -> Timeline | None:
    """The subject's records from one search with the user's permissions and the turn's filters, dated; None
    when the search finds nothing or fails."""
    from app.modules.artifacts.reader import record_artifact_reader

    retrieval_service = state.get("retrieval_service")
    blob_store = state.get("blob_store")
    if retrieval_service is None or blob_store is None:
        return None
    org_id = current_org_id()
    result = await retrieval_service.search_with_filters(
        queries=[subject.name, *subject.searches],
        org_id=org_id,
        user_id=state.get("user_id", ""),
        limit=_SEARCH_LIMIT,
        filter_groups=state.get("filters") or {},
    )
    if not isinstance(result, dict) or result.get("status_code", 200) != 200:
        return None
    hits = sorted(
        (hit for hit in result.get("searchResults") or [] if isinstance(hit, dict)),
        key=lambda hit: -float(hit.get("score") or 0.0),
    )
    graph_records = result.get("virtual_to_record_map") or {}
    order = [
        vrid for vrid in dict.fromkeys((hit.get("metadata") or {}).get("virtualRecordId") for hit in hits)
        if vrid and isinstance(graph_records.get(vrid), dict)
    ][:MAX_RECORDS]
    if not order:
        return None
    reader = record_artifact_reader(blob_store)
    lookups = await reader.lookup_many(order)
    gate = asyncio.Semaphore(_READ_CONCURRENCY)

    async def _dated(vrid: str) -> TimelineEntry | None:
        async with gate:
            try:
                stored = await reader.read_record(vrid, org_id, lookup=lookups.get(vrid))
            except Exception:
                logger.info("project_timeline: could not read %s", vrid, exc_info=True)
                stored = None
            # The date patterns scan the whole text, which can be long.
            return await asyncio.to_thread(date_entry, vrid, graph_records[vrid], stored)

    entries = [entry for entry in await asyncio.gather(*map(_dated, order)) if entry is not None]
    return Timeline(subject, tuple(order_entries(entries))) if entries else None


def render_timeline(timeline: Timeline, shortener: RecordIdShortener | None = None) -> str:
    def _id(record_id: str) -> str:
        return shortener.get_or_create_short_id(record_id) if shortener is not None else record_id

    def _line(entry: TimelineEntry) -> str:
        head = f"{entry.day} ({_BASIS_LABELS.get(entry.basis or '', entry.basis)}) | " if entry.day else ""
        tail = f" | {entry.first_line}" if entry.first_line else ""
        return f"- {head}{entry.name[:_NAME_CHARS]} | record_id={_id(entry.record_id)}{tail}"

    dated = [entry for entry in timeline.entries if entry.day]
    undated = [entry for entry in timeline.entries if not entry.day]
    lines = [
        f'Records about the {timeline.subject.kind} "{timeline.subject.name}", newest first '
        f"({len(timeline.entries)} found by searching for it). Each date is the record's own, read as shown; "
        "records that state no date come after the dated ones.",
        *(_line(entry) for entry in dated),
    ]
    if undated:
        lines += ["No date stated:", *(_line(entry) for entry in undated)]
    ids = ", ".join(f'"{_id(entry.record_id)}"' for entry in timeline.entries[:_FETCH_IDS_SHOWN])
    lines += [f"Read them with {FETCH_RECORD_TOOL_NAME}(record_ids=[{ids}]).", "", _RECONCILE_RULE]
    return "\n".join(lines)


async def _build(context: AgentContext, question: str) -> Timeline | None:
    subject = await detect_subject(context, question)
    if subject is None:
        logger.info("project_timeline: no project or policy subject conversation=%s", context.conversation_id)
        return None
    timeline = await collect_timeline(context.tool_state, subject)
    logger.info(
        "project_timeline: kind=%s records=%d dated=%d conversation=%s",
        subject.kind, len(timeline.entries) if timeline else 0,
        sum(1 for entry in timeline.entries if entry.day) if timeline else 0, context.conversation_id,
    )
    return timeline


async def _build_or_none(context: AgentContext, question: str) -> Timeline | None:
    try:
        return await asyncio.wait_for(_build(context, question), timeout=BUILD_TIMEOUT_S)
    except Exception as exc:
        logger.warning("project_timeline: no timeline, going on without it: %r", exc)
        return None


def start_project_timeline(context: AgentContext, question: str) -> asyncio.Task[Timeline | None] | None:
    """The timeline build for this question, run beside the first-turn search; None while the switch is off."""
    if not project_timeline_enabled() or not question.strip():
        return None
    return spawn_in_request(_build_or_none(context, question), name="project-timeline")


async def finish_project_timeline(
    context: AgentContext, task: asyncio.Task[Timeline | None] | None,
) -> str | None:
    """The rendered timeline, its records made readable; None when there is none."""
    if task is None:
        return None
    timeline = await task
    if timeline is None:
        return None
    state = context.tool_state
    # The search that found them applied the user's access, so the model may read them.
    remember_record_ids(
        state, [entry.record_id for entry in timeline.entries],
        names={entry.record_id: entry.name for entry in timeline.entries},
    )
    note_retrieved_by(state, "project_timeline", [
        (entry.virtual_record_id, entry.record_id, entry.name) for entry in timeline.entries
    ])
    await emit_retrieval_context(context, source="tool", tool_name=_TRACE_NAME, tool_call_id=_TRACE_NAME)
    return render_timeline(timeline, get_record_id_shortener_if_enabled(state))


__all__ = [
    "BUILD_TIMEOUT_S",
    "MAX_RECORDS",
    "PROJECT_TIMELINE_ENV_VAR",
    "Timeline",
    "TimelineEntry",
    "TimelineSubject",
    "collect_timeline",
    "date_entry",
    "detect_subject",
    "finish_project_timeline",
    "order_entries",
    "parse_subject",
    "project_timeline_enabled",
    "render_timeline",
    "start_project_timeline",
]

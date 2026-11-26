"""What an engine child tells the run that handed it its task (CTL-6): its answer, the files it published and the
sources its answer cites.

The loop's ``code_task`` reads ``files`` back as this run's own artifact cards, so its answer names and links them.
The child's citation state lives in its own tool state, under refs of its own mapper; the parent adopts the sources
(``CHILD_SOURCES_KEY``) and renumbers the answer's refs into its own, as it does a spawn_agent child's (ENGCITE2).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, cast

from app.harness.artifacts.events import PRESENTED
from app.harness.engines.capability_limits import (
    LIMITS_KEY,
    CapabilityLimit,
    InputsAccepted,
    refused_clones,
    skipped_inputs,
    uncloned,
)
from app.harness.engines.repository_citations import NO_REPOSITORIES
from app.harness.grounding import (
    GROUNDING_KEY,
    Grounding,
    assess,
    hits_from_state,
    is_knowledge_search,
    is_web_read,
    request_subject,
)
from app.harness.repositories import TOOL_ID as REPOSITORY_TOOL_ID
from harness_core.logs import HarnessLog

# A citation in an answer: ``[label](refN)`` (``cited_by_ref``'s form).
_REF = re.compile(r"\bref\d+\b")

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

    from pydantic import JsonValue

    from app.harness.engines.repository_citations import CitableRepositories
    from app.harness.repositories import NamedRepository
    from harness_core.journal.types import JournalEvent

_FIELDS = (
    "artifactId",
    "fileName",
    "mimeType",
    "sizeBytes",
    "artifactType",
    "version",
    "documentId",
)


def with_published_files(
    document: dict[str, JsonValue], presented: Iterable[Mapping[str, Any]]
) -> dict[str, JsonValue]:
    """``document`` with ``files``: each artifact's last presented version, unless that version was discarded."""
    last: dict[str, Mapping[str, Any]] = {}
    for data in presented:
        artifact = data.get("artifactId")
        if isinstance(artifact, str) and artifact:
            last[artifact] = data
    files = [
        {key: data[key] for key in _FIELDS if data.get(key) not in (None, "")}
        for data in last.values()
        if data.get("lifecycle") != "discarded"
    ]
    return {**document, "files": cast("JsonValue", files)}


def engine_child_result(
    document: dict[str, JsonValue], events: Sequence[JournalEvent]
) -> dict[str, JsonValue]:
    """The run's published files from its own stream."""
    return with_published_files(
        document,
        (cast("Mapping[str, Any]", e.data) for e in events if e.type == PRESENTED),
    )


class EngineChildResult:
    """The ``AssembledEngineRun.child_result`` shaper: the published files, the answer with its citations as
    ``[label](refN)`` of blocks the run read (whatever form the engine wrote them in: a ``refN``, its old
    ``https://refN.xyz`` alias, the page's own link), and the sources those refs name."""

    def __init__(
        self,
        context: Any,  # noqa: ANN401 - AgentContext
        read: Callable[[], Sequence[str]],
        repositories: CitableRepositories = NO_REPOSITORIES,
        limits: tuple[CapabilityLimit, ...] = (),
        named: tuple[NamedRepository, ...] = (),
        *,
        typed: bool = True,
        accepted: InputsAccepted = InputsAccepted(),
    ) -> None:
        self._context = context
        self._read = read
        self._repositories = repositories
        self._limits = limits
        self._named = named
        self._typed = typed
        self._accepted = accepted

    def with_limits(
        self,
        limits: tuple[CapabilityLimit, ...],
        *,
        named: tuple[NamedRepository, ...] = (),
        typed: bool = True,
        accepted: InputsAccepted = InputsAccepted(),
    ) -> EngineChildResult:
        """The same shaper, carrying what the run could not do from its start (ADR-RUN-03 D5) and the repositories its
        task names (R41-CLONE), each one it never cloned said so; ``typed``: the claim's Node reads that kind;
        ``accepted``: how it reads the files the job went on without (R46FIX-2, R46FIX-FU-2)."""
        return EngineChildResult(
            self._context, self._read, self._repositories, limits, named, typed=typed, accepted=accepted
        )

    def __call__(self, document: dict[str, JsonValue], events: Sequence[JournalEvent]) -> dict[str, JsonValue]:
        from app.harness.citations import CHILD_SOURCES_KEY, CitationJournal
        from app.harness.engines.answer import cited_by_ref
        from app.harness.engines.repository_citations import cite_repository_links

        out = engine_child_result(document, events)
        # ADR-RUN-03 D2: what the job's searches found of the subject the request names, built from its own calls.
        out[GROUNDING_KEY] = job_grounding(self._context, events).to_json()
        # ADR-RUN-03 D5: what it could not do, from its journal and its run's limits, never from its words.
        refused = refused_clones(events, REPOSITORY_TOOL_ID)
        limits = (
            *skipped_inputs(events, accepted=self._accepted), *refused, *uncloned(events, self._named, refused, typed=self._typed), *self._limits,
        )
        if limits:
            out[LIMITS_KEY] = [limit.to_json() for limit in limits]
        if "needs_input" in out:
            asked = asked_question(events)
            if asked is not None:
                out[QUESTION_KEY] = asked
        output = out.get("output")
        if isinstance(output, str):
            # Links into a repository the session fetched are its files' sources (ADR-RUN-01 D3), adopted as pages are.
            linked = cite_repository_links(output, self._context, self._repositories)
            out["output"] = cited_by_ref(linked, self._context, self._read)
        # The run's citation state, from nothing (a fresh journal's first delta), narrowed to the web pages the answer
        # cites: the parent renumbers only those refs. One child carried every block it had read, two 4.2 MB tree
        # listings among them, and the store refused the 8.7 MB result (WP-62). ``cited`` narrows before it bounds.
        output = out.get("output")
        sources = CitationJournal(self._context, log=HarnessLog.named("app.harness.engines.child_result")).cited(
            lambda whole: cited_sources(whole, output)
        )
        if sources:
            out[CHILD_SOURCES_KEY] = cast("JsonValue", sources)
        return out


# The question a job asked through its gateway's ``ask_user`` (C3.70), which its parent asks in its own chat.
QUESTION_KEY = "question"
QUESTION_EVENT = "x.pipeshub.question.asked"
TOOL_INTENT = "tool.intent"


def job_grounding(context: Any, events: Sequence[JournalEvent]) -> Grounding:  # noqa: ANN401 - AgentContext
    """The job's grounding: the subject of the person's request (the head of its task), against the hits its
    knowledge searches showed (its citation state, restored on every attempt), counting the searches it journaled."""
    from app.harness.assembly.task_spec import request_of

    subject = request_subject(request_of(str(getattr(context, "query", "") or "")))
    tools = [str(cast("Mapping[str, Any]", e.data).get("toolId") or "") for e in events if e.type == TOOL_INTENT]
    searches = sum(1 for tool in tools if is_knowledge_search(tool))
    grounding = assess(subject, hits_from_state(getattr(context, "tool_state", {}) or {}), searches=searches)
    if grounding.not_found and any(is_web_read(tool) for tool in tools):
        # It read the web too: missing from the person's knowledge is not missing, and is not said to be.
        return Grounding("unchecked", subject, grounding.searches, grounding.hits)
    return grounding


def asked_question(events: Sequence[JournalEvent]) -> dict[str, JsonValue] | None:
    """The last question the job's ``ask_user`` put on its card: its words and its options' labels."""
    for event in reversed(events):
        if event.type != QUESTION_EVENT:
            continue
        data = cast("Mapping[str, Any]", event.data)
        payload = data.get("toolData")
        questions = payload.get("questions") if isinstance(payload, dict) else None
        if data.get("status") != "success" or not isinstance(questions, list) or not questions:
            continue
        first = questions[0] if isinstance(questions[0], dict) else {}
        text = first.get("question")
        if not isinstance(text, str) or not text.strip():
            continue
        options = [str(o.get("label")) for o in first.get("options") or [] if isinstance(o, dict) and o.get("label")]
        return {"question": text.strip(), "options": cast("JsonValue", options)}
    return None


def cited_sources(sources: dict[str, JsonValue], output: JsonValue) -> dict[str, JsonValue]:
    """``sources`` with only the refs ``output`` cites and the web blocks those refs name; the rest (knowledge records,
    which the parent's citation pass reads by record id) as it is."""
    cited = set(_REF.findall(output)) if isinstance(output, str) else set()
    refs = [
        pair
        for pair in cast("list[Any]", sources.get("refs") or [])
        if isinstance(pair, dict) and str(cast("dict[str, Any]", pair).get("ref")) in cited
    ]
    urls = {str(cast("dict[str, Any]", pair).get("url")) for pair in refs}
    narrowed = dict(sources)
    if "refs" in sources:
        narrowed["refs"] = cast("JsonValue", refs)
    if "webRecords" in sources:
        narrowed["webRecords"] = [
            record
            for record in cast("list[Any]", sources.get("webRecords") or [])
            if isinstance(record, dict) and str(cast("dict[str, Any]", record).get("url")) in urls
        ]
    return narrowed


__all__ = [
    "QUESTION_KEY",
    "EngineChildResult",
    "asked_question",
    "cited_sources",
    "engine_child_result",
    "job_grounding",
    "with_published_files",
]

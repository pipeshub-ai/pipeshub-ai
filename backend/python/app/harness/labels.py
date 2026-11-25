"""Confidentiality labels of PipesHub turns and engine work (P2.5a).

The evidence is the run's journal. Each tool call journals what it added to the
citation state with its outcome (``x.pipeshub.citations.recorded``, PA-8) and
the pre-run commit what the run started with, so the records and live items
whose content reached the model are on the stream, read or cited. A label is
complete only when every tool outcome the model saw is accounted for:

- a knowledge tool, whose results are drawn only from the records it puts in
  the citation state;
- a tool whose results carry no record content (asking the user, planning,
  tool disclosure, reading the agent's installed skills; a skill an agent wrote
  brings the label it was written under, journaled with the read as
  ``skillLabels``);
- a web search or page fetch, whose results are public web content (FG2:
  ``fetch_url`` is an unauthenticated GET of a public address, refused for any
  private one whatever the deployment's settings);
- a tool outcome labelled public with no sources (``labels.confidentiality``): a public repository the worker
  fetched with no credential from a public address (WP-83), which anyone may read, as a web page;
- a platform tool that declares its result derived from its own arguments
  (``TAG_OUTPUT_DERIVED``: the calculators, the image generator), which shows
  the model nothing the run had not read already (FLOWTRUST, D3);
- a refusal or a cancellation, which is the harness's own text;
- a child run, whose own label Node joins with this one.

Anything else (connector actions, MCP tools) reached the model
with content nobody can name, and leaves the label incomplete: such a turn is
shown to nobody but its actor. History imported into the session counts by the
label Node derived for it (``x.pipeshub.history.labelled``, UX2); an import
without one is unknown, as is any answer legacy wrote.
Web pages are public and never named. Who may read a source is decided when a
viewer asks, from the platform's permissions, never stored here.
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING, Final, Literal, cast

from app.config.constants.arangodb import Connectors, RecordTypes
from app.harness.citations import EVENT as CITATIONS_EVENT
from app.harness.classification import classification_port
from app.harness.knowledge.provenance import untrusted_in
from app.harness.memories import RECALLED_EVENT as MEMORY_RECALLED_EVENT
from app.models.citation_source import LIVE_SOURCE_KIND, is_live_record_id
from harness_core.effects.flow import ConversationGrants, grants_from_wire
from harness_core.engines.durable.ledger import HARNESS_OUTCOMES
from harness_core.kernel.fold import FoldIncompatible, fold_run
from harness_core.provenance.ledger import ProvenanceFact, ProvenanceLedger
from harness_core.provenance.types import (
    NO_ATTRIBUTES,
    NO_CONTENT,
    ConnectorItem,
    Integrity,
    RecordRef,
    RepositoryRef,
    ResultProvenance,
    SourceAttributes,
    WebPage,
    join_all,
)
from harness_core.security.labels import AskSource, SourceLabel, join_labels, label_of

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

    from pydantic import JsonValue

    from harness_core.contracts.generated.run_spec import RunSpec
    from harness_core.journal.types import JournalEvent, NewEvent
    from harness_core.kernel.fold import Invocation

ANSWER_EVENT = "x.pipeshub.answer.finalized"
# The repository fetch's record (``app.harness.repositories.FETCHED_EVENT``; that module imports this one).
REPOSITORY_FETCHED_EVENT = "x.pipeshub.repository.fetched"
HISTORY_LABELLED_EVENT = "x.pipeshub.history.labelled"
INHERITED_LABEL_KEY = "inheritedLabel"
INHERITED_GRANTS_KEY = "inheritedGrants"
SourceKind = Literal["indexed", "attachment", "generated", "live"]
ToolKind = Literal["records", "none", "public", "opaque"]


_GENERATING = frozenset(
    {Connectors.CODING_SANDBOX.value, Connectors.DATABASE_SANDBOX.value, Connectors.IMAGE_GENERATION.value}
)
_TERMINAL = frozenset({"run.completed", "run.failed", "run.cancelled"})


@dataclass(frozen=True, slots=True, order=True)
class LabelSource:
    """A source as a viewer's access is decided on (``PipeshubCitationSource`` without ``web``, C-18)."""

    kind: SourceKind
    record_id: str = ""
    connector: str = ""
    connector_id: str = ""
    external_record_id: str = ""
    #: The publication through which the record reached the turn (P2.13): only its audience may be shown it.
    publication_id: str = ""
    #: What the edition recorded of it when it was read (``ClassificationPort.record_attributes``); not part of who
    #: may read it, nor on the label's wire.
    attributes: SourceAttributes = dataclasses.field(default=NO_ATTRIBUTES, compare=False)

    def to_wire(self) -> dict[str, str]:
        if self.kind == LIVE_SOURCE_KIND:
            wire = {"kind": self.kind, "connector": self.connector, "externalRecordId": self.external_record_id}
            if self.connector_id:
                wire["connectorId"] = self.connector_id
            return wire
        wire = {"kind": self.kind, "recordId": self.record_id}
        if self.publication_id:
            wire["publicationId"] = self.publication_id
        return wire

    @classmethod
    def from_wire(cls, data: Mapping[str, JsonValue]) -> LabelSource | None:
        kind = data.get("kind")
        if kind == LIVE_SOURCE_KIND:
            connector, external = _text(data, "connector"), _text(data, "externalRecordId")
            if connector is None or external is None:
                return None
            return cls("live", connector=connector, connector_id=_text(data, "connectorId") or "", external_record_id=external)
        record = _text(data, "recordId")
        if kind in ("indexed", "attachment", "generated") and record is not None:
            return cls(cast("SourceKind", kind), record_id=record, publication_id=_text(data, "publicationId") or "")
        return None


TurnLabel = SourceLabel[LabelSource]


def unknown_label() -> TurnLabel:
    return SourceLabel[LabelSource](False, frozenset())


def _text(data: Mapping[str, object], *keys: str) -> str | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def classify_record(record: Mapping[str, object]) -> LabelSource | Literal["web"] | None:
    """A record or citation metadata (snake or camel case) as a source; ``None``: it names nothing checkable."""
    if record.get("sourceKind") == "web" or record.get("origin") == "WEB_SEARCH":
        return "web"
    record_id = _text(record, "recordId", "record_id", "id", "_key")
    connector = _text(record, "connector", "connector_name", "connectorName")
    attributes = classification_port().record_attributes(record, connector)
    if record.get("sourceKind") == LIVE_SOURCE_KIND or is_live_record_id(record_id):
        external = _text(record, "externalRecordId", "external_record_id")
        if connector is None or external is None:
            return None
        return LabelSource(
            "live",
            connector=connector,
            connector_id=_text(record, "connectorId", "connector_id") or "",
            external_record_id=external,
            attributes=attributes,
        )
    if record_id is None:
        return None
    if (connector or "").upper() == Connectors.ATTACHMENTS.value:
        return LabelSource("attachment", record_id=record_id)
    if _text(record, "recordType", "record_type") == RecordTypes.ARTIFACT.value or connector in _GENERATING:
        return LabelSource("generated", record_id=record_id)
    return LabelSource(
        "indexed",
        record_id=record_id,
        publication_id=_text(record, "publicationId", "publication_id") or "",
        attributes=attributes,
    )


def citation_sources(data: Mapping[str, JsonValue]) -> TurnLabel:
    """What one ``x.pipeshub.citations.recorded`` names; incomplete when a record names nothing checkable. A record
    named with attributes and again without them (a bare id) keeps them."""
    found: dict[LabelSource, LabelSource] = {}
    complete = True
    items: list[object] = []
    records = data.get("records")
    if isinstance(records, dict):
        items.extend(records.values())
    for field in ("toolRecords", "finalResults"):
        value = data.get(field)
        if isinstance(value, list):
            items.extend(value)
    known = data.get("knownRecordIds")
    if isinstance(known, list):
        items.extend({"id": k} for k in known)
    for item in items:
        if not isinstance(item, dict):
            complete = False
            continue
        entry = cast("dict[str, object]", item)
        metadata = entry.get("metadata")
        source = classify_record(cast("dict[str, object]", metadata) if isinstance(metadata, dict) else entry)
        if source is None:
            complete = False
        elif source != "web" and (source not in found or not found[source].attributes):
            found[source] = source
    own = label_of(_published_wins(set(found.values())), complete=complete)
    return join_labels([own, *skill_labels(data)])


def skill_labels(data: Mapping[str, JsonValue]) -> list[TurnLabel]:
    """The labels of agent-written skill content a call handed the model (FOLLOWUPS1-1): what the run that wrote
    it had read reached this one too, as a recalled memory's sources do. Unreadable: unknown."""
    entries = data.get("skillLabels")
    if not isinstance(entries, list):
        return []
    labels: list[TurnLabel] = []
    for entry in entries:
        label = label_from_wire(entry.get("label")) if isinstance(entry, dict) else None
        labels.append(label if label is not None else unknown_label())
    return labels


def _http(value: str | None) -> str | None:
    return value if value is not None and value.startswith(("https://", "http://")) else None


def described_sources(data: Mapping[str, JsonValue]) -> list[tuple[LabelSource, AskSource]]:
    """The records one ``x.pipeshub.citations.recorded`` names, titled, for the approval card (UX1). An indexed
    record links to its PipesHub page (the viewer's access is checked there); a live item to its own page."""
    found: list[tuple[LabelSource, AskSource]] = []
    records = data.get("records")
    items: list[object] = list(records.values()) if isinstance(records, dict) else []
    for field in ("toolRecords", "finalResults"):
        value = data.get(field)
        if isinstance(value, list):
            items.extend(value)
    for item in items:
        if not isinstance(item, dict):
            continue
        entry = cast("dict[str, object]", item)
        metadata = entry.get("metadata")
        record = cast("dict[str, object]", metadata) if isinstance(metadata, dict) else entry
        source = classify_record(record)
        if source is None or source == "web":
            continue
        title = _text(record, "recordName", "record_name", "title", "name")
        url = _http(_text(record, "webUrl", "web_url")) if source.kind == LIVE_SOURCE_KIND else None
        found.append((source, AskSource(title=title, record_id=source.record_id or None, url=url)))
    return found


def _published_wins(found: set[LabelSource]) -> set[LabelSource]:
    """A bare id of a record the same citation state names through a publication is the same content: kept once,
    with its publication, so the label never asks a viewer for more than the audience check (P2.13)."""
    published = {s.record_id for s in found if s.publication_id}
    return {s for s in found if s.publication_id or s.kind != "indexed" or s.record_id not in published}


@cache
def known_tools() -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    """(tools whose results are drawn only from records they put in the citation state, tools whose
    results carry no record content: the question to the user, planning over the model's own context,
    tool disclosure, the agent's skills, tools whose results are public web content). Read on first use: the idle worker
    never loads the agent stack (D-7)."""
    from app.agents.actions.knowledge_graph.ops.fetch import FETCH_RECORD_TOOL_NAME
    from app.agents.actions.knowledge_graph.ops.summarize import SUMMARIZE_TOOL_NAME
    from app.agents.agent_loop.hooks._tool_naming import (
        INTERNAL_SEARCH_DELEGATE_NAME,
        INTERNAL_SEARCH_TOOL_NAMES,
    )
    from app.agents.agent_loop.lazy_tools_wiring import META_TOOL_NAMES
    from app.agents.agent_loop.skills_wiring import DOMAIN_SHARED_SKILL_TOOL_NAMES
    from app.harness.assembly.deep import ROUTED_TOOLS
    from app.harness.assembly.disclosure import ROUTED
    from app.harness.assembly.plan import PLAN_TOOL
    from app.harness.assembly.tool_sources import ASKS_USER, WEB_TOOLS
    from harness_core.memories import REMEMBER_TOOL
    from harness_core.tools.call_tool import CALL_TOOL

    # The delegate is a child run: its own label is joined by Node.
    # The quarantined summary (G-58) is drawn only from the records its search put in the citation state.
    records = (INTERNAL_SEARCH_TOOL_NAMES - {INTERNAL_SEARCH_DELEGATE_NAME}) | {FETCH_RECORD_TOOL_NAME, SUMMARIZE_TOOL_NAME}
    # ``remember`` answers with an id and a status, never record content (P7.3).
    # The skill reads answer the agent's own configuration: the skills its owner installed, pinned at admission, the
    # same text the start hooks preload into the prompt and an engine mounts in its workspace. What an agent wrote
    # into a skill is journaled with the read as its writing run's label (``skill_labels``). The write surface,
    # ``skill_manage``, is not among them. ``call_tool`` is unwrapped into the call it makes.
    meta = frozenset(META_TOOL_NAMES) - {CALL_TOOL}
    no_content = (
        ASKS_USER | frozenset(ROUTED_TOOLS) | frozenset(ROUTED) | DOMAIN_SHARED_SKILL_TOOL_NAMES | meta
        | {REMEMBER_TOOL, PLAN_TOOL}
    )
    return frozenset(records), no_content, frozenset(WEB_TOOLS)


def tool_kind(name: str) -> ToolKind:
    """By the model-facing name, or a tool id's (``source@instance/name``)."""
    bare = name.rpartition("/")[2] if "@" in name else name
    records, no_content, public = known_tools()
    if bare in records:
        return "records"
    if bare in no_content:
        return "none"
    if bare in public:
        return "public"
    return "opaque"


@cache
def derived_output(tool_id: str) -> bool:
    """Whether ``tool_id`` is a platform tool (``pipeshub@<toolset>/<tool>``) whose registration in the release's
    toolset registry declares ``TAG_OUTPUT_DERIVED``. Only the platform's own tools get that id (``ToolsetSource``);
    a connector, MCP or web tool never does, whatever its name. Code, not configuration: the same journal gives the
    same label on every worker of a release."""
    from app.agents.registry.toolset_registry import get_toolset_registry
    from app.harness.assembly.tool_sources import PLATFORM
    from harness_core.tools.tags import TAG_OUTPUT_DERIVED

    source, _, rest = tool_id.partition("@")
    toolset, _, method = rest.partition("/")
    if source != PLATFORM or not toolset or not method:
        return False
    registry = get_toolset_registry()
    toolsets = registry.get_all_toolsets()
    if not toolsets:
        registry.auto_discover_toolsets()
        toolsets = registry.get_all_toolsets()
    declared = {"key": TAG_OUTPUT_DERIVED.key, "value": TAG_OUTPUT_DERIVED.value}
    # The registry's key for a toolset (``ToolsetRegistry._normalize_toolset_name``).
    tools = (toolsets.get(toolset.lower().replace(" ", "").replace("_", "")) or {}).get("tools") or []
    return any(t.get("name") == method and declared in (t.get("tags") or []) for t in tools)


def own_work_label(
    events: Iterable[JournalEvent], run_id: str, *, tool_names: Mapping[str, str] | None = None
) -> TurnLabel:
    """The run's own work: what its tool results and pre-run steps journaled (not the history it saw:
    Node joins that), read from the run's ``ProvenanceLedger`` (ADR-RUN-01 D3). ``tool_names``: tool id -> name for
    calls the loop's fold does not name (an engine's), which the replay of older outcomes needs."""
    return own_work(events, run_id, tool_names=tool_names)[0]


def own_work(
    events: Iterable[JournalEvent], run_id: str, *, tool_names: Mapping[str, str] | None = None
) -> tuple[TurnLabel, Integrity]:
    """``own_work_label`` and the run's integrity: ``external`` once any result an outsider could write reached it
    (the ``untrusted_content_read`` taint), which Node's memory settlement reads (SEC-B-05, ADR-MEM-01)."""
    ledger = ProvenanceLedger(PipeshubReplay(tool_names))
    ledger.apply([e for e in events if e.run_id == run_id])
    integrity: Integrity = "external" if ledger.untrusted(run_id) else "first_party"
    return turn_label(ledger.run_provenance(run_id)), integrity


def turn_label(provenance: ResultProvenance) -> TurnLabel:
    """A provenance as the turn label Node keeps (P2.5a): unknown readers is unknown; otherwise the records and
    connector items it names, which a viewer's access is checked on. Web pages and repositories are public and never
    named."""
    if provenance.readers == "unknown":
        return unknown_label()
    return label_of(_published_wins({_label_source(s) for s in provenance.checked}), complete=True)


def _label_source(source: RecordRef | ConnectorItem) -> LabelSource:
    if isinstance(source, ConnectorItem):
        return LabelSource(
            "live", connector=source.connector, connector_id=source.connector_id,
            external_record_id=source.external_record_id, attributes=source.attributes,
        )
    return LabelSource(
        source.record_kind, record_id=source.record_id, publication_id=source.publication_id,
        attributes=source.attributes,
    )


def _source_ref(source: LabelSource) -> RecordRef | ConnectorItem:
    if source.kind == LIVE_SOURCE_KIND:
        return ConnectorItem(
            source.connector, source.external_record_id, source.connector_id, attributes=source.attributes
        )
    return RecordRef(source.record_id, source.kind, source.publication_id, attributes=source.attributes)


def classified_source_id(source: LabelSource) -> str:
    """The id a conversation's classification ranks a turn label's source by (``classified_id`` of its D3 ref)."""
    from harness_core.provenance.types import classified_id

    return classified_id(_source_ref(source))


def label_provenance(label: TurnLabel | None, *, integrity: Integrity = "first_party") -> ResultProvenance:
    """A recorded turn label (a memory's, a skill's, imported history's) as a provenance; ``None`` is unknown."""
    if label is None or not label.complete:
        return ResultProvenance("unknown", integrity, ())
    sources = tuple(_source_ref(s) for s in sorted(label.sources))
    return ResultProvenance("principals" if sources else "public", integrity, sources)


def _web_pages(data: Mapping[str, JsonValue], fetched_at: str) -> tuple[WebPage, ...]:
    pages: dict[str, WebPage] = {}
    web = data.get("webRecords")
    for record in web if isinstance(web, list) else []:
        url = _http(_text(cast("dict[str, object]", record), "url")) if isinstance(record, dict) else None
        if url is not None:
            _ = pages.setdefault(url, WebPage(url, fetched_at))
    return tuple(pages.values())


def citation_provenance(data: Mapping[str, JsonValue], *, fetched_at: str = "") -> ResultProvenance:
    """What one ``x.pipeshub.citations.recorded`` names, as a provenance: its records and live items (readable by
    the principals their permissions name; unknown when one names nothing checkable, or an agent-written skill's
    label is unknown), its web pages, and ``external`` when an outsider wrote any of it (KN-08)."""
    label = citation_sources(data)
    integrity: Integrity = "external" if untrusted_in(data) else "first_party"
    named = label_provenance(label, integrity=integrity)
    pages = _web_pages(data, fetched_at)
    return named.join(ResultProvenance("public", integrity, pages)) if pages else named


def call_provenance(events: Sequence[NewEvent]) -> ResultProvenance | None:
    """``ProvenanceStamp``'s ``sources_of``: what a call's citation delta names; ``None`` when it journaled none."""
    found = [citation_provenance(e.data) for e in events if e.type == CITATIONS_EVENT]
    return join_all(found) if found else None


class PipeshubReplay:
    """The ``ProvenanceLedger``'s ``Replay`` for PipesHub runs: what a run's events say about every result the stamp
    did not record (an older worker's run, or a Node that does not keep the records), the pre-run steps (their
    citation delta), recalled memories, imported history and fetched repositories. An outcome counts as before
    ADR-RUN-01 D3:

    - a refusal, a cancellation, a child run (its own label is joined by Node) or a failed call of the platform's own
      tool (its error text, WP-62) carries nothing; an outside tool's error (an MCP server's) is unknown;
    - an outcome whose item labels say public readers and no sources is public (WP-83);
    - a knowledge tool, a tool with no record content, a web tool, and a platform tool declaring
      ``TAG_OUTPUT_DERIVED`` carry what their citation delta names; anything else is unknown.
    """

    def __init__(self, tool_names: Mapping[str, str] | None = None) -> None:
        self._tool_names = dict(tool_names or {})

    def facts(self, events: Sequence[JournalEvent], run_id: str, recorded: frozenset[str]) -> list[ProvenanceFact]:
        try:
            invocations = fold_run(events, run_id=run_id).invocations
        except FoldIncompatible:
            return [ProvenanceFact(run_id, _UNKNOWN, None, "", "incompatible", recorded=False)]
        denied: set[str] = set()
        intents: dict[str, str] = {}
        imports: list[str] = []
        history: dict[str, TurnLabel | None] = {}
        facts: list[ProvenanceFact] = []

        def fact(provenance: ResultProvenance, rule: str, invocation: str | None = None, tool_id: str = "") -> None:
            facts.append(ProvenanceFact(run_id, provenance, invocation, tool_id, rule, recorded=False))

        for event in events:
            data = event.data
            match event.type:
                case "session.imported":
                    imports.append(str(data.get("transcriptDigest")))
                case _ if event.type == HISTORY_LABELLED_EVENT and event.writer == "node":
                    history[str(data.get("transcriptDigest"))] = label_from_wire(data.get("label"))
                case "tool.denied":
                    denied.add(str(data.get("invocationId")))
                case "tool.intent":
                    intents[str(data.get("invocationId"))] = str(data.get("toolId") or "")
                case _ if event.type == CITATIONS_EVENT:
                    # A call the stamp recorded has its delta in its record already.
                    if data.get("callId") not in recorded:
                        fact(citation_provenance(data, fetched_at=event.ts), "citations")
                case _ if event.type == MEMORY_RECALLED_EVENT:
                    for label in recalled_labels(data):
                        fact(label_provenance(label), "memory")
                case _ if event.type == REPOSITORY_FETCHED_EVENT:
                    fetched = _fetched_repository(data)
                    if fetched is not None:
                        # The repository as a source only: who may read it is its outcome's to say.
                        fact(ResultProvenance("public", "external", (fetched,)), "repository")
                case _:
                    pass
        for digest in imports:
            fact(label_provenance(history.get(digest)), "history")
        for event in events:
            if event.type != "tool.outcome":
                continue
            invocation = str(event.data.get("invocationId"))
            if invocation in recorded:
                continue
            tool_id = intents.get(invocation, "")
            fact(self._outcome(event.data, denied=invocation in denied, loop=invocations.get(invocation), tool_id=tool_id), "replay",
                 invocation, tool_id)
        return facts

    def _outcome(
        self, data: Mapping[str, JsonValue], *, denied: bool, loop: Invocation | None, tool_id: str
    ) -> ResultProvenance:
        integrity: Integrity = "external" if _external(data) else "first_party"
        if denied or data.get("status") in HARNESS_OUTCOMES:
            return ResultProvenance("public", integrity, ())
        if data.get("status") == "error" and integrity != "external":
            # A failed call of the platform's own tool returns its own error text, no record content (WP-62: six
            # "Bad credentials" GitHub failures made a research run unknown, and 20 public web reads asked).
            return NO_CONTENT
        if public_result(data):
            # WP-83: two public clones in one run made the context unknown, and 23 public web reads asked.
            return ResultProvenance("public", integrity, ())
        name = loop.name if loop is not None else self._tool_names.get(tool_id, tool_id)
        if tool_kind(name) == "opaque" and not derived_output(tool_id):
            return ResultProvenance("unknown", integrity, ())
        return ResultProvenance("public", integrity, ())


_UNKNOWN = ResultProvenance("unknown", "first_party", ())


def _fetched_repository(data: Mapping[str, JsonValue]) -> RepositoryRef | None:
    host, repository, commit = data.get("host"), data.get("repository"), data.get("commit")
    if not (isinstance(host, str) and isinstance(repository, str) and isinstance(commit, str) and commit):
        return None
    return RepositoryRef(f"https://{host}/{repository}", commit)


def public_result(data: Mapping[str, JsonValue]) -> bool:
    """A tool outcome whose item labels say anyone may read its result and name no source (REPORT 6.5.1): what the
    host's own tool journals for a public repository fetched with no credential from a public address (WP-83)."""
    labels = data.get("labels")
    confidentiality = labels.get("confidentiality") if isinstance(labels, dict) else None
    if not isinstance(confidentiality, dict):
        return False
    return confidentiality.get("readers") == "public" and confidentiality.get("sources") in (None, [])


def _external(data: Mapping[str, JsonValue]) -> bool:
    """A tool outcome labelled ``integrity: external`` (an MCP server, the web, an outsider-written record)."""
    labels = data.get("labels")
    return isinstance(labels, dict) and labels.get("integrity") == "external"


def recalled_labels(data: Mapping[str, JsonValue]) -> list[TurnLabel]:
    """The labels of the memories a run was given (P7.3): their sources reached the model like a search's."""
    entries = data.get("entries")
    if not isinstance(entries, list):
        return [unknown_label()]
    labels: list[TurnLabel] = []
    for entry in entries:
        label = label_from_wire(entry.get("label")) if isinstance(entry, dict) else None
        labels.append(label if label is not None else unknown_label())
    return labels


def label_wire(label: TurnLabel, *, integrity: Integrity | None = None) -> dict[str, JsonValue]:
    """``PipeshubTurnLabel``: sources sorted, so the same label is the same document. ``integrity``: a turn's
    answer always says it (SEC-B-05); Node treats a label without it as possibly outsider-led."""
    wire: dict[str, JsonValue] = {
        "complete": label.complete,
        "sources": cast("list[JsonValue]", [s.to_wire() for s in sorted(label.sources)]),
    }
    if integrity is not None:
        wire["integrity"] = integrity
    return wire


def label_from_wire(value: object) -> TurnLabel | None:
    """A recorded label; ``None`` when absent or unreadable (unknown, never empty)."""
    if not isinstance(value, dict):
        return None
    data = cast("dict[str, JsonValue]", value)
    complete, sources = data.get("complete"), data.get("sources")
    if not isinstance(complete, bool) or not isinstance(sources, list):
        return None
    parsed = [LabelSource.from_wire(cast("dict[str, JsonValue]", s)) if isinstance(s, dict) else None for s in sources]
    if any(p is None for p in parsed):
        return None
    return label_of((p for p in parsed if p is not None), complete=complete)


def inherited_label(spec: RunSpec) -> TurnLabel | None:
    """A child's inherited context as its admission pinned it (``x.pipeshub.inheritedLabel``, UX2); ``None`` when
    absent or unreadable (the child's parent then counts as unknown)."""
    extension = (spec.extensions or {}).get("x.pipeshub")
    return label_from_wire(extension.get(INHERITED_LABEL_KEY)) if isinstance(extension, dict) else None


# The turn label's own fields; any other is an edition's (``x-extensible``), carried for its ports.
_LABEL_FIELDS: Final = frozenset({"complete", "sources", "integrity"})
_CARRIED = re.compile(r"^[A-Za-z][A-Za-z0-9]{0,63}$")
_CARRIED_VALUE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


def inherited_fields(spec: RunSpec) -> Mapping[str, str]:
    """The fields an edition adds to the label a child's admission pinned of its parent (``label_fields``), by
    name; empty for a root run or a parent that added none."""
    extension = (spec.extensions or {}).get("x.pipeshub")
    label = extension.get(INHERITED_LABEL_KEY) if isinstance(extension, dict) else None
    if not isinstance(label, dict):
        return {}
    return {
        key: value
        for key, value in cast("dict[str, JsonValue]", label).items()
        if key not in _LABEL_FIELDS
        and _CARRIED.fullmatch(key)
        and isinstance(value, str)
        and _CARRIED_VALUE.fullmatch(value)
    }


def inherited_grants(spec: RunSpec, *, enabled: bool = True) -> ConversationGrants:
    """The conversation grants a child's admission pinned (``x.pipeshub.inheritedGrants``, FLOWASK), which its own
    stream never holds; none for a root run or a child whose parent held none. ``enabled`` false (C4.10
    ``interactions.conversationGrants``): none, and none offered. A grant read later from the stream counts only when
    the run's actor gave it (R38-AUTHZ): the pin holds only what the parent's actor held, and a child runs as them."""
    extension = (spec.extensions or {}).get("x.pipeshub")
    pinned = extension.get(INHERITED_GRANTS_KEY) if isinstance(extension, dict) else None
    return ConversationGrants(grants_from_wire(pinned), enabled=enabled, principal=spec.principal_ref)


class SessionSourceLedger:
    """``SourceLedger`` for PipesHub runs: P3.5's conservative rule over the session's journal.

    A run's workspace may hold what any earlier turn of the session put there, and its goal comes from its
    parent's context. So its label joins every run of the session: a finished run by the label its answer
    recorded (none recorded: unknown), the run and its in-progress ancestors by their own work so far (imported
    history included, by the label Node derived for it), and any other run still in progress as unknown. A child
    run reads only its own stream: its parent's context is the label the parent passed at the spawn
    (``inherited``, UX2), and unknown when it passed none.
    """

    def __init__(
        self,
        *,
        tool_names: Mapping[str, str] | None = None,
        inherited: TurnLabel | None = None,
        inherited_fields: Mapping[str, str] | None = None,
    ) -> None:
        self._tool_names = dict(tool_names or {})
        self._inherited = inherited
        self._inherited_fields = dict(inherited_fields or {})
        # What an edition adds to the label a child this run asks for inherits (``bind_label_fields``).
        self._label_fields: Callable[[], Mapping[str, str]] | None = None
        self._seq = 0
        # The session's one record of what its results carried (ADR-RUN-01 D3): the runs' own work is read from it.
        self.provenance = ProvenanceLedger(PipeshubReplay(self._tool_names))
        self._parents: dict[str, str | None] = {}
        self._answers: dict[str, TurnLabel | None] = {}
        self._ended: set[str] = set()
        self._titled: dict[LabelSource, AskSource] = {}

    def apply(self, events: Sequence[JournalEvent]) -> None:
        self.provenance.apply(events)
        for event in events:
            if event.seq <= self._seq:
                continue
            self._seq = event.seq
            run = event.run_id
            if run is None:
                continue
            if event.type == "run.admitted":
                parent = event.data.get("parentRunId")
                self._parents[run] = parent if isinstance(parent, str) else None
            elif event.type == ANSWER_EVENT:
                self._answers[run] = label_from_wire(event.data.get("label"))
            elif event.type in _TERMINAL:
                self._ended.add(run)
            elif event.type == CITATIONS_EVENT:
                for source, described in described_sources(event.data):
                    _ = self._titled.setdefault(source, described)

    def label_for(self, run_id: str) -> TurnLabel:
        labels: list[TurnLabel | None] = []
        if self._parent_unseen(run_id):
            if self._inherited is None:
                return unknown_label()
            labels.append(self._inherited)
        working = {run_id, *self._ancestors(run_id)}
        for run in self.provenance.runs:
            if run in working:
                labels.append(turn_label(self.provenance.run_provenance(run)))
            else:
                labels.append(self._answers.get(run))
        return join_labels(labels)

    def describe_sources(self, run_id: str) -> tuple[AskSource, ...]:
        """The run's sources as the approval card names them (UX1), titled ones first, in the order the session met
        them. For a run whose context is unknown, every source the session cited: the card says the rest is unknown."""
        label = self.label_for(run_id)
        named = [s for s in self._titled if s in label.sources] if label.complete else list(self._titled)
        if label.complete:
            named += sorted(s for s in label.sources if s not in self._titled)
        described = [self._titled.get(s) or AskSource(record_id=s.record_id or None) for s in named]
        return tuple(sorted(described, key=lambda d: d.title is None))

    def context_label(self, run_id: str) -> dict[str, JsonValue]:
        """What a child this run asks for inherits (UX2): this run's label as it stands, in its wire form, with the
        fields an edition adds to it when bound (``bind_label_fields``)."""
        wire = label_wire(self.label_for(run_id))
        if self._label_fields is not None:
            wire.update({key: value for key, value in self._label_fields().items() if key not in _LABEL_FIELDS})
        return wire

    def bind_label_fields(self, fields: Callable[[], Mapping[str, str]]) -> None:
        """What an edition adds to the label a child this run asks for inherits, read when the child is asked for."""
        self._label_fields = fields

    def classified_sources(self, run_id: str) -> frozenset[str] | None:
        """``ClassifiedSources`` (OWD-15): the run's label's records and live items, by the id their labels are kept
        by; ``None`` when the label is unknown."""
        label = self.label_for(run_id)
        if not label.complete:
            return None
        return frozenset(classified_source_id(s) for s in label.sources)

    def inherited_field(self, name: str) -> str | None:
        return self._inherited_fields.get(name)

    def answer_label(self, run_id: str) -> dict[str, JsonValue]:
        """The label an engine run's answer records: ``label_for`` with the run's integrity (SEC-B-05), session-wide as
        ``untrusted_read``."""
        return label_wire(self.label_for(run_id), integrity="external" if self.untrusted_read(run_id) else "first_party")

    def untrusted_read(self, run_id: str) -> bool:
        """Session-wide, as the sources: what this stream shows. A child's inherited label names its parent's sources
        but not their integrity, so a child counts only what it read itself (its parent's sources still make its
        context private or unknown)."""
        del run_id
        return self.provenance.untrusted()

    def source_records(self, run_id: str) -> frozenset[str] | None:
        label = self.label_for(run_id)
        if not label.complete or any(s.kind == LIVE_SOURCE_KIND for s in label.sources):
            return None
        return frozenset(s.record_id for s in label.sources)

    def _parent_unseen(self, run_id: str) -> bool:
        """A child run's own stream holds none of its parent's work (the goal it wrote came from there)."""
        parent = self._parents.get(run_id)
        return parent is not None and parent not in self.provenance.runs

    def _ancestors(self, run_id: str) -> list[str]:
        chain: list[str] = []
        parent = self._parents.get(run_id)
        while parent is not None and parent not in chain and parent not in self._ended:
            chain.append(parent)
            parent = self._parents.get(parent)
        return chain


__all__ = [
    "LabelSource",
    "SessionSourceLedger",
    "TurnLabel",
    "citation_sources",
    "described_sources",
    "classify_record",
    "derived_output",
    "classified_source_id",
    "inherited_fields",
    "inherited_grants",
    "inherited_label",
    "known_tools",
    "label_from_wire",
    "label_wire",
    "PipeshubReplay",
    "call_provenance",
    "citation_provenance",
    "label_provenance",
    "own_work",
    "own_work_label",
    "public_result",
    "turn_label",
    "skill_labels",
    "tool_kind",
    "unknown_label",
]

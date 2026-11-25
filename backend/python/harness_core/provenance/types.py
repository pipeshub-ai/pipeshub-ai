"""What a tool result's content is and who may read it (ADR-RUN-01 D3): ``ResultProvenance`` and its ``SourceRef`` kinds.

Every tool result is recorded with a ``ResultProvenance``: who may read what it carries (``public``: anyone, a web page or
a public repository; ``principals``: the people its sources' permissions name, decided per viewer by the host;
``unknown``: content nobody can name), who wrote it (``first_party``: the organization or the harness;
``external``: an outsider, the web, an MCP server, a repository) and the sources it came from. The type has no
default: a producer that cannot say is a rule that answers ``unknown``, never a missing value.

Sources are typed references, never content: a web page (its address and when it was read), a repository file
(``url@commit:path``, the repository itself with no path), a record, a connector item, a memory or a skill.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal, cast

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pydantic import JsonValue

Readers = Literal["public", "principals", "unknown"]
Integrity = Literal["first_party", "external"]
RecordKind = Literal["indexed", "attachment", "generated"]

# Who may read, from the least held to the most: the join of two results is the more held.
_READERS: Final[dict[str, int]] = {"public": 0, "principals": 1, "unknown": 2}
# A result naming more is recorded as unknown: a bounded document, and fail closed (``MAX_LABEL_SOURCES``'s rule).
MAX_SOURCES: Final = 2000
# What an edition records about a checked source beside its identity (``RecordRef.attributes``): the item's extra
# wire fields, which an edition's schema overlay declares. Never part of a source's identity.
type SourceAttributes = Mapping[str, JsonValue]
NO_ATTRIBUTES: Final[SourceAttributes] = MappingProxyType({})
# The wire fields each checked kind holds itself; any other field of the item is an attribute.
_RECORD_FIELDS: Final = frozenset({"kind", "recordId", "recordKind", "publicationId"})
_CONNECTOR_FIELDS: Final = frozenset({"kind", "connector", "externalRecordId", "connectorId"})


def _attributes_from_wire(data: Mapping[str, object], own: frozenset[str]) -> SourceAttributes:
    extra = {k: cast("JsonValue", v) for k, v in data.items() if k not in own and v is not None}
    return MappingProxyType(extra) if extra else NO_ATTRIBUTES


def _with_attributes(wire: dict[str, JsonValue], attributes: SourceAttributes, own: frozenset[str]) -> None:
    wire.update({key: value for key, value in attributes.items() if key not in own})


@dataclass(frozen=True, slots=True, order=True)
class WebPage:
    url: str
    fetched_at: str = ""
    kind: Literal["web"] = "web"

    def to_wire(self) -> dict[str, JsonValue]:
        wire: dict[str, JsonValue] = {"kind": "web", "url": self.url}
        if self.fetched_at:
            wire["fetchedAt"] = self.fetched_at
        return wire

    @property
    def key(self) -> tuple[str, ...]:
        return ("web", self.url)


@dataclass(frozen=True, slots=True, order=True)
class RepositoryRef:
    """A file of a fetched repository at the fetched commit (``url@commit:path``); ``path`` empty: the repository."""

    url: str
    commit: str
    path: str = ""
    # The path is a folder at the commit: its page is the host's tree view, not a file's.
    folder: bool = False
    kind: Literal["repository"] = "repository"

    def to_wire(self) -> dict[str, JsonValue]:
        wire: dict[str, JsonValue] = {"kind": "repository", "url": self.url, "commit": self.commit}
        if self.path:
            wire["path"] = self.path
        if self.folder:
            wire["folder"] = True
        return wire

    @property
    def key(self) -> tuple[str, ...]:
        return ("repository", self.url, self.commit, self.path)

    def at(self, path: str, *, folder: bool = False) -> RepositoryRef:
        return RepositoryRef(self.url, self.commit, path.strip("/"), folder)


@dataclass(frozen=True, slots=True, order=True)
class RecordRef:
    """A record in the graph: indexed, an attachment or an artifact a run produced (``generated``)."""

    record_id: str
    record_kind: RecordKind = "indexed"
    publication_id: str = ""
    kind: Literal["record"] = "record"
    attributes: SourceAttributes = field(default=NO_ATTRIBUTES, compare=False)

    def to_wire(self) -> dict[str, JsonValue]:
        wire: dict[str, JsonValue] = {"kind": "record", "recordId": self.record_id, "recordKind": self.record_kind}
        if self.publication_id:
            wire["publicationId"] = self.publication_id
        _with_attributes(wire, self.attributes, _RECORD_FIELDS)
        return wire

    @property
    def key(self) -> tuple[str, ...]:
        return ("record", self.record_kind, self.record_id, self.publication_id)


@dataclass(frozen=True, slots=True, order=True)
class ConnectorItem:
    """An item a tool read straight from a connector with the run owner's credentials (a live item)."""

    connector: str
    external_record_id: str
    connector_id: str = ""
    kind: Literal["connector"] = "connector"
    attributes: SourceAttributes = field(default=NO_ATTRIBUTES, compare=False)

    def to_wire(self) -> dict[str, JsonValue]:
        wire: dict[str, JsonValue] = {
            "kind": "connector", "connector": self.connector, "externalRecordId": self.external_record_id,
        }
        if self.connector_id:
            wire["connectorId"] = self.connector_id
        _with_attributes(wire, self.attributes, _CONNECTOR_FIELDS)
        return wire

    @property
    def key(self) -> tuple[str, ...]:
        return ("connector", self.connector, self.external_record_id)


@dataclass(frozen=True, slots=True, order=True)
class MemoryRef:
    memory_id: str
    kind: Literal["memory"] = "memory"

    def to_wire(self) -> dict[str, JsonValue]:
        return {"kind": "memory", "memoryId": self.memory_id}

    @property
    def key(self) -> tuple[str, ...]:
        return ("memory", self.memory_id)


@dataclass(frozen=True, slots=True, order=True)
class SkillSourceRef:
    name: str
    kind: Literal["skill"] = "skill"

    def to_wire(self) -> dict[str, JsonValue]:
        return {"kind": "skill", "name": self.name}

    @property
    def key(self) -> tuple[str, ...]:
        return ("skill", self.name)


SourceRef = WebPage | RepositoryRef | RecordRef | ConnectorItem | MemoryRef | SkillSourceRef
# Sources anyone may read: what an answer cites by link (ADR-RUN-01 D3: web and repository sources alike).
CITABLE_KINDS: Final = frozenset({"web", "repository"})


def classified_id(source: RecordRef | ConnectorItem) -> str:
    """The id a conversation's classification ranks a checked source by (OWD-15): a record by its id, a live item by
    its source id, so the two never meet."""
    return source.record_id if isinstance(source, RecordRef) else source_id(source)


def attributed(sources: Iterable[SourceRef]) -> dict[str, SourceAttributes]:
    """Each checked source's attributes by ``classified_id``; a source read with and without them keeps them."""
    found: dict[str, SourceAttributes] = {}
    for source in sources:
        if isinstance(source, RecordRef | ConnectorItem) and source.attributes:
            _ = found.setdefault(classified_id(source), source.attributes)
    return found


def _attributed_wins(sources: dict[tuple[str, ...], SourceRef], source: SourceRef) -> None:
    """``sources`` with ``source`` once: the first sighting stays, unless only this one carries attributes (a record
    named by id before its content was read), so joining never loses them."""
    held = sources.get(source.key)
    if held is None:
        sources[source.key] = source
    elif isinstance(source, RecordRef | ConnectorItem) and source.attributes and not getattr(held, "attributes", None):
        sources[source.key] = source


def source_id(source: SourceRef) -> str:
    """A source's stable id as one string (its key): what the outcome ledger's ``SourceGathered.ref`` holds."""
    return "|".join(source.key)


def _text(data: Mapping[str, object], key: str) -> str | None:
    value = data.get(key)
    return value if isinstance(value, str) and value else None


def source_from_wire(data: object) -> SourceRef | None:
    """A recorded source; ``None`` when it is not one (the result it belongs to then counts as unknown)."""
    if not isinstance(data, dict):
        return None
    entry = cast("dict[str, object]", data)
    match entry.get("kind"):
        case "web":
            url = _text(entry, "url")
            return WebPage(url, _text(entry, "fetchedAt") or "") if url else None
        case "repository":
            url, commit = _text(entry, "url"), _text(entry, "commit")
            if not url or not commit:
                return None
            return RepositoryRef(url, commit, _text(entry, "path") or "", entry.get("folder") is True)
        case "record":
            record, kind = _text(entry, "recordId"), entry.get("recordKind", "indexed")
            if record is None or kind not in ("indexed", "attachment", "generated"):
                return None
            return RecordRef(
                record, kind, _text(entry, "publicationId") or "", attributes=_attributes_from_wire(entry, _RECORD_FIELDS)
            )
        case "connector":
            connector, external = _text(entry, "connector"), _text(entry, "externalRecordId")
            if not connector or not external:
                return None
            return ConnectorItem(
                connector, external, _text(entry, "connectorId") or "", attributes=_attributes_from_wire(entry, _CONNECTOR_FIELDS)
            )
        case "memory":
            memory = _text(entry, "memoryId")
            return MemoryRef(memory) if memory else None
        case "skill":
            name = _text(entry, "name")
            return SkillSourceRef(name) if name else None
        case _:
            return None


@dataclass(frozen=True, slots=True)
class ResultProvenance:
    """A result's provenance. No field has a default: whoever records a result says all three."""

    readers: Readers
    integrity: Integrity
    sources: tuple[SourceRef, ...]

    def join(self, *others: ResultProvenance) -> ResultProvenance:
        """The more held readers, ``external`` if either is, and both sources, first seen first."""
        readers: Readers = self.readers
        integrity: Integrity = self.integrity
        sources: dict[tuple[str, ...], SourceRef] = {}
        for source in self.sources:
            _attributed_wins(sources, source)
        for other in others:
            if _READERS[other.readers] > _READERS[readers]:
                readers = other.readers
            if other.integrity == "external":
                integrity = "external"
            for source in other.sources:
                _attributed_wins(sources, source)
        return bounded(readers, integrity, sources.values())

    @property
    def checked(self) -> tuple[RecordRef | ConnectorItem, ...]:
        """The sources whose readers the host decides per viewer (records and connector items)."""
        return tuple(s for s in self.sources if isinstance(s, RecordRef | ConnectorItem))

    @property
    def citable(self) -> tuple[WebPage | RepositoryRef, ...]:
        return tuple(s for s in self.sources if isinstance(s, WebPage | RepositoryRef))

    def to_wire(self) -> dict[str, JsonValue]:
        return {
            "readers": self.readers,
            "integrity": self.integrity,
            "sources": [s.to_wire() for s in self.sources],
        }


def bounded(readers: Readers, integrity: Integrity, sources: Iterable[SourceRef]) -> ResultProvenance:
    """A ``ResultProvenance`` held to ``MAX_SOURCES``: past it nobody can say whose content it is (unknown)."""
    found = tuple(sources)
    if len(found) > MAX_SOURCES:
        return ResultProvenance("unknown", integrity, ())
    return ResultProvenance(readers, integrity, found)


def provenance_from_wire(data: object) -> ResultProvenance | None:
    """A recorded provenance; ``None`` when it cannot be read (the caller counts that result as unknown)."""
    if not isinstance(data, dict):
        return None
    entry = cast("dict[str, object]", data)
    readers, integrity, sources = entry.get("readers"), entry.get("integrity"), entry.get("sources")
    if readers not in _READERS or integrity not in ("first_party", "external") or not isinstance(sources, list):
        return None
    parsed = [source_from_wire(s) for s in cast("list[object]", sources)]
    if any(p is None for p in parsed):
        return None
    return bounded(cast("Readers", readers), integrity, (p for p in parsed if p is not None))


# What a result that carries nothing anyone wrote is: the harness's own text, a receipt, a refusal.
NO_CONTENT: Final = ResultProvenance("public", "first_party", ())


def join_all(provenances: Iterable[ResultProvenance]) -> ResultProvenance:
    """The join of ``provenances``; nothing to join is ``NO_CONTENT``."""
    joined = NO_CONTENT
    for provenance in provenances:
        joined = joined.join(provenance)
    return joined


__all__ = [
    "CITABLE_KINDS",
    "MAX_SOURCES",
    "NO_ATTRIBUTES",
    "NO_CONTENT",
    "ConnectorItem",
    "Integrity",
    "MemoryRef",
    "ResultProvenance",
    "Readers",
    "RecordKind",
    "RecordRef",
    "RepositoryRef",
    "SkillSourceRef",
    "SourceAttributes",
    "SourceRef",
    "WebPage",
    "attributed",
    "bounded",
    "classified_id",
    "join_all",
    "provenance_from_wire",
    "source_from_wire",
    "source_id",
]

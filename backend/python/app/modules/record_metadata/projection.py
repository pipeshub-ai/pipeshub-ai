"""Projecting a record's property values onto the record itself.

A record's typed properties live in ``properties.json`` in blob storage, which a
graph query cannot filter on. Indexing therefore also writes the values that a
schema marks ``filterable``/``facetable`` onto the record node, flat as
``p_<name>`` on Neo4j and in a ``recordProps`` sub-document on ArangoDB, with
four controls beside them:

- ``propsSet``: 16-hex hash of the sorted names projected (the *union*, below);
- ``propsGen``: the highest schema version the union includes. Only grows;
- ``propsSrc``: the source stamp the values were taken at (``stamp_of``);
- ``propsVer``: an optimistic counter, +1 on every projection write.

The *union* of a scope is every name any version of its schema marks filterable.
Projecting the union, not the newest schema's names, means a flag flip or a
rollback to an older version needs no rewrite of stored values.

Known bound: a record counts as current while ``propsSrc >= stamp``, so a source stamp that goes
*backwards* (a corrected clock, a fallback stamp later replaced by an older real source time) is not
re-projected here, and the write fence would refuse it anyway. The sweep (9b) compares a content signal,
not only the stamp, and converges such a record.

One write (``project_properties``) is one conditional statement, so concurrent
writers (two index runs, a re-index and a sweep, two pods with different cached
unions) can never overwrite newer data with older: it applies only while the
counter is the one read (``propsVer``), the stored stamp is not newer
(``propsSrc``), the stored generation is not newer (``propsGen``) and, when the
run has a revision, the content revision is unchanged.
"""

import asyncio
import functools
import hashlib
import logging
import math
import os
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from app.config.constants.arangodb import (
    RECORD_TYPE_COLLECTION_MAPPING,
    CollectionNames,
    Connectors,
)
from app.models.entities import Record, TicketRecord
from app.models.record_metadata import (
    MAX_LIST_ITEMS,
    MetadataSchema,
    PropertyDefinition,
    PropertyType,
    RecordMetadata,
    string_fits,
)
from app.modules.record_metadata.builtin import builtin_properties, ticket_metadata
from app.modules.record_metadata.schemas import MetadataSchemaRegistry
from app.services.graph_db.common.record_visibility import is_live_record
from app.services.graph_db.conditions import EQ, IS_NULL, NULL_OR_LE, Cond
from app.telemetry.modules.record_storage_metrics import (
    record_property_projection,
    record_property_value_dropped,
)

PROJECTION_FLAG: Final = "RECORD_PROPERTY_PROJECTION"
#: ``ctx.settings`` key: the ``RecordMetadata`` (or None for none) a caller that pushed a record's values hands to indexing.
PROVIDED_PROPERTIES: Final = "provided_properties"

PROPS_SET: Final = "propsSet"
PROPS_GEN: Final = "propsGen"
PROPS_SRC: Final = "propsSrc"
PROPS_VER: Final = "propsVer"
RECORD_PROPS: Final = "recordProps"
#: Neo4j: the flat prefix of a projected value.
NEO4J_PREFIX: Final = "p_"

#: Generation of the properties shipped in code. Raise it with the built-in schema version.
BUILTIN_GEN: Final = 1
UNION_TTL_SECONDS: Final = 60.0
#: Tries of one write when another writer got in between (the counter moved).
MAX_ATTEMPTS: Final = 3
#: Upper bound on one projection inside ``index()``: it never holds an indexing slot longer.
PROJECTION_TIMEOUT_SECONDS: Final = 15.0
MAX_SAFE_INT: Final = 2**53 - 1

_RECORDS = CollectionNames.RECORDS.value
_TICKETS = CollectionNames.TICKETS.value
_ENABLED_VALUES = frozenset({"1", "true", "on", "yes"})

# Outcomes (metric label and ``Projection.status``).
DISABLED: Final = "disabled"
NO_UNION: Final = "no_union"
NO_STAMP: Final = "no_stamp"
CURRENT: Final = "current"
WRITTEN: Final = "written"
FAILED: Final = "failed"
FENCED_BY_GEN: Final = "fenced_by_generation"
SUPERSEDED: Final = "superseded"
CONTENT_CHANGED: Final = "content_changed"
CONTENTION: Final = "contention"


def projection_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """Whether indexing writes projections. Off unless ``RECORD_PROPERTY_PROJECTION`` is on."""
    value = (environ if environ is not None else os.environ).get(PROJECTION_FLAG, "")
    return value.strip().lower() in _ENABLED_VALUES


# ---- stamps ---------------------------------------------------------------------------------------


def _millis(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    if isinstance(value, float) and not math.isfinite(value):
        return 0
    return int(value) if value > 0 else 0


def stamp_of(source_last_modified: object, updated_at: object) -> int:
    """The version stamp of a record: the source's modified time when it gave one, else the time the
    platform last wrote the record (a knowledge-base upload or a push has no source clock).

    0 means "no usable stamp": zero, negative, missing, not a number. Such a stamp is never written,
    because it would compare as older than everything and could not order two writers.
    """
    return _millis(source_last_modified if source_last_modified is not None else updated_at)


def doc_stamp(doc: Mapping[str, Any]) -> int:
    return stamp_of(doc.get("sourceLastModifiedTimestamp"), doc.get("updatedAtTimestamp"))


# ---- the union -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class ScopeUnion:
    """Names projected for one scope (a connector instance or knowledge base), their types, and the generation."""

    types: Mapping[str, PropertyType]
    gen: int
    set_hash: str = field(default="")

    def __post_init__(self) -> None:
        names = ",".join(sorted(self.types))
        object.__setattr__(self, "set_hash", hashlib.sha256(names.encode()).hexdigest()[:16])

    @property
    def empty(self) -> bool:
        return not self.types


EMPTY_UNION: Final = ScopeUnion(types={}, gen=0)


def union_of(versions: Iterable[tuple[int, Iterable[PropertyDefinition]]]) -> ScopeUnion:
    """The union of schema versions given as ``(version, properties)``.

    A name is in when any version marks it filterable or facetable. Its type is the one in the highest
    version that has it: the current type wins over one a stale version declared. The generation is the
    highest version, whether or not that version added a name, so it orders unions of one scope.
    """
    types: dict[str, PropertyType] = {}
    gen = 0
    for version, properties in sorted(versions, key=lambda item: item[0]):
        gen = max(gen, version)
        for prop in properties:
            if prop.name in types:
                types[prop.name] = prop.type
            if prop.filterable or prop.facetable:
                types[prop.name] = prop.type
    return ScopeUnion(types=dict(sorted(types.items())), gen=gen)


@functools.cache
def _builtin_union(connector: Connectors) -> ScopeUnion:
    properties = builtin_properties(connector)
    return union_of([(BUILTIN_GEN, properties)]) if properties else EMPTY_UNION


class UnionUnavailable(Exception):
    """The schema registry could not be read and nothing was cached for the scope."""


@dataclass
class _Entry:
    versions: dict[int, tuple[PropertyDefinition, ...]] = field(default_factory=dict)
    checked_at: float = float("-inf")
    union: ScopeUnion = EMPTY_UNION
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class ScopeUnionCache:
    """Per-process cache of scope unions.

    Built-in schemas come from code: no reads. A knowledge base's versions are immutable and have no
    gaps, so each is cached for good. Once per TTL the cache probes version ``highest cached + 1`` (and
    onward until one is absent). It does not follow the registry's ``latest`` pointer, which is the
    *last registration* and so goes backwards when an old schema is registered again. The cost is one
    read per TTL per scope when nothing changed, plus one per new version.

    A local registration calls ``invalidate``; another process learns of it within the TTL. Nothing
    depends on that window: a write from an older union is refused by ``propsGen``.
    """

    def __init__(self, ttl: float = UNION_TTL_SECONDS, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl
        self._clock = clock
        self._entries: dict[tuple[str, str], _Entry] = {}

    def invalidate(self, org_id: str, connector_id: str) -> None:
        entry = self._entries.get((org_id, connector_id))
        if entry is not None:
            entry.checked_at = float("-inf")

    async def union_for(
        self, registry: MetadataSchemaRegistry, org_id: str, connector_id: str, connector: Connectors,
    ) -> ScopeUnion:
        builtin = builtin_properties(connector)
        if builtin is not None:
            return _builtin_union(connector)
        if connector is not Connectors.KNOWLEDGE_BASE:
            return EMPTY_UNION
        entry = self._entries.setdefault((org_id, connector_id), _Entry())
        if self._clock() - entry.checked_at < self._ttl:
            return entry.union
        async with entry.lock:
            if self._clock() - entry.checked_at < self._ttl:
                return entry.union
            try:
                await self._advance(registry, org_id, connector_id, entry)
            except Exception as exc:
                if not entry.versions and entry.union is EMPTY_UNION and entry.checked_at == float("-inf"):
                    raise UnionUnavailable(type(exc).__name__) from exc
                # Keep serving what is cached; try again after the TTL rather than on every record.
                entry.checked_at = self._clock()
                return entry.union
            entry.checked_at = self._clock()
            return entry.union

    @staticmethod
    async def _advance(registry: MetadataSchemaRegistry, org_id: str, connector_id: str, entry: _Entry) -> None:
        version = max(entry.versions, default=0)
        changed = False
        while True:
            version += 1
            schema: MetadataSchema | None = await registry.get(org_id, connector_id, version)
            if schema is None:
                break
            entry.versions[version] = schema.properties
            changed = True
        if changed:
            entry.union = union_of(entry.versions.items())


_DEFAULT_CACHE: Final = ScopeUnionCache()


def default_union_cache() -> ScopeUnionCache:
    """The process-wide cache indexing and the schema routes share."""
    return _DEFAULT_CACHE


# ---- encoding ------------------------------------------------------------------------------------


def encode_value(ptype: PropertyType, value: object) -> object | None:
    """``value`` in the one stored form of ``ptype``, or None when it does not fit (so it is not projected).

    STRING/ENUM raw text (case-sensitive, not normalised); INT within +/-(2^53-1) (a graph engine's float
    comparison is exact only there); FLOAT finite; BOOL; DATETIME epoch milliseconds; STRING_LIST at most
    ``MAX_LIST_ITEMS`` fitting strings.
    """
    if ptype in (PropertyType.STRING, PropertyType.ENUM):
        return value if string_fits(value) else None
    if ptype is PropertyType.BOOL:
        return value if isinstance(value, bool) else None
    if ptype in (PropertyType.INT, PropertyType.DATETIME):
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return value if abs(value) <= MAX_SAFE_INT else None
    if ptype is PropertyType.FLOAT:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value) if math.isfinite(value) else None
    if ptype is PropertyType.STRING_LIST:
        if not isinstance(value, list) or len(value) > MAX_LIST_ITEMS or not all(string_fits(v) for v in value):
            return None
        return list(value)
    return None


def encode_values(union: ScopeUnion, metadata: RecordMetadata) -> dict[str, object]:
    """The values of ``metadata`` the union projects, encoded; a value that does not fit is dropped (counted)."""
    out: dict[str, object] = {}
    for name, ptype in union.types.items():
        held = metadata.values.get(name)
        if held is None:
            continue
        encoded = encode_value(ptype, held.value)
        if encoded is None:
            record_property_value_dropped()
            continue
        out[name] = encoded
    return out


def existing_names(doc: Mapping[str, Any]) -> set[str]:
    """The property names a record document currently projects (either store's form)."""
    names = {key[len(NEO4J_PREFIX):] for key in doc if key.startswith(NEO4J_PREFIX) and len(key) > len(NEO4J_PREFIX)}
    held = doc.get(RECORD_PROPS)
    if isinstance(held, dict):
        names.update(held)
    return names


# ---- the tri-state builder -----------------------------------------------------------------------


@dataclass(frozen=True)
class Values:
    """The record's values, and the stamp of the object they came from."""

    metadata: RecordMetadata
    stamp: int


@dataclass(frozen=True)
class Absent:
    """The record has no values (an attachment, a ticket with every field empty): it is stamped, with none."""

    stamp: int


@dataclass(frozen=True)
class Failed:
    """The values could not be read or cannot be trusted now: nothing is written; the existing ones stay."""

    reason: str


ProjectionInput = Values | Absent | Failed


class _Retry(Exception):
    """The record changed between the two reads of one build; read again."""


@dataclass(frozen=True)
class Projection:
    status: str
    attempts: int = 0
    reason: str = ""


class _NotProvided:
    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "NOT_PROVIDED"


NOT_PROVIDED: Final = _NotProvided()


def _is_current(doc: Mapping[str, Any], union: ScopeUnion, stamp: int) -> bool:
    gen, src = doc.get(PROPS_GEN), doc.get(PROPS_SRC)
    return (
        doc.get(PROPS_SET) == union.set_hash
        and isinstance(gen, (int, float)) and not isinstance(gen, bool) and gen >= union.gen
        and isinstance(src, (int, float)) and not isinstance(src, bool) and src >= stamp
    )


def _is_ticket(doc: Mapping[str, Any]) -> bool:
    return RECORD_TYPE_COLLECTION_MAPPING.get(doc.get("recordType")) == _TICKETS


def _connector_of(doc: Mapping[str, Any]) -> Connectors:
    # A knowledge-base upload carries no connector name; the models read it as the knowledge base.
    try:
        return Connectors(doc["connectorName"]) if doc.get("connectorName") else Connectors.KNOWLEDGE_BASE
    except ValueError:
        return Connectors.KNOWLEDGE_BASE


async def _build_ticket(graph: Any, record_id: str) -> tuple[ProjectionInput, dict[str, Any] | None]:  # noqa: ANN401
    """Values of a ticket from its tickets document, with the stamp of the record they were read beside.

    Reads the record, the tickets document and the record again; when the stamp or counter moved in
    between, the values may belong to the other version, so ``_Retry``. The returned record document is
    the first read: its counter and names are what the write is conditional on.
    """
    try:
        first = await graph.get_document(record_id, _RECORDS, raise_on_error=True)
        if first is None or not is_live_record(first):
            return Failed("record_gone"), None
        stamp = doc_stamp(first)
        if stamp == 0:
            return Failed("no_stamp"), first
        ticket_doc = await graph.get_document(record_id, _TICKETS, raise_on_error=True)
        if ticket_doc is None:
            # A ticket-type record always has its tickets document; absent is not "no metadata".
            return Failed("tickets_document_missing"), first
        metadata = ticket_metadata(TicketRecord.from_arango_record(ticket_doc, first))
        again = await graph.get_document(record_id, _RECORDS, raise_on_error=True)
    except Exception as exc:
        return Failed(f"read_error:{type(exc).__name__}"), None
    if again is None or doc_stamp(again) != stamp or again.get(PROPS_VER) != first.get(PROPS_VER):
        raise _Retry
    return (Values(metadata, stamp) if metadata is not None else Absent(stamp)), first


def _build_provided(
    record: Record, doc: Mapping[str, Any], provided: object, *, registry_scope: bool,
) -> ProjectionInput:
    """Values for a record that is not a ticket: handed in by the caller that has them, else none to read.

    The values come from the snapshot (``record``), so the stamp does too; and a snapshot older than the
    record document read since is refused, never stamped with the newer document's stamp.
    """
    doc_s = doc_stamp(doc)
    if doc_s == 0:
        return Failed("no_stamp")
    snapshot = stamp_of(record.source_updated_at, record.updated_at)
    if not registry_scope:
        # A built-in scope's non-ticket records (a Jira attachment) have no properties: stamp them, none.
        return Absent(doc_s)
    if isinstance(provided, RecordMetadata):
        if snapshot == 0:
            return Failed("no_stamp")
        if doc_s > snapshot:
            return Failed("snapshot_older_than_record")
        return Values(provided, snapshot)
    if provided is None:
        if snapshot == 0:
            return Failed("no_stamp")
        if doc_s > snapshot:
            return Failed("snapshot_older_than_record")
        return Absent(snapshot)
    return Failed("values_unavailable")


async def project_properties(
    *,
    graph: Any,  # noqa: ANN401 - an IGraphDBProvider
    registry: MetadataSchemaRegistry,
    cache: ScopeUnionCache,
    record: Record,
    record_doc: dict[str, Any],
    provided: object = NOT_PROVIDED,
    logger: logging.Logger | None = None,
    enabled: Callable[[], bool] = projection_enabled,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> Projection:
    """Write the record's projected property values, once, behind the fence. Never raises for a write
    that is refused; the caller wraps unexpected errors.

    ``record_doc`` is the record document the caller read; ``record`` the in-memory snapshot the event
    carried (it can lag the document); ``provided`` the values the caller holds for a non-ticket record
    (``RecordMetadata``, or None for "none"), ``NOT_PROVIDED`` when it holds nothing.
    """
    log = logger or logging.getLogger(__name__)
    if not enabled():
        return Projection(DISABLED)
    # The org is the one the event's record carries (set from the org context at ingest), not read from a payload.
    org_id, connector_id = record.org_id, record_doc.get("connectorId") or record.connector_id
    if not org_id or not connector_id:
        return _done(FAILED, 0, "no_scope")
    connector = _connector_of(record_doc)
    try:
        union = await cache.union_for(registry, org_id, connector_id, connector)
    except UnionUnavailable:
        return _done(FAILED, 0, "union_unavailable")
    if union.empty:
        return Projection(NO_UNION)
    doc_s = doc_stamp(record_doc)
    if doc_s == 0:
        return _done(NO_STAMP, 0)
    if _is_current(record_doc, union, doc_s):
        return _done(CURRENT, 0)

    record_id = record_doc.get("_key") or record_doc.get("id") or record.id
    ticket = _is_ticket(record_doc) and connector is not Connectors.KNOWLEDGE_BASE
    doc: dict[str, Any] = record_doc
    for attempt in range(1, MAX_ATTEMPTS + 1):
        if ticket:
            try:
                built, fresh = await _build_ticket(graph, record_id)
            except _Retry:
                continue
            if fresh is not None:
                doc = fresh
        else:
            built = _build_provided(record, doc, provided, registry_scope=connector is Connectors.KNOWLEDGE_BASE)
        if isinstance(built, Failed):
            return _done(FAILED, attempt, built.reason)
        stamp = built.stamp
        values = encode_values(union, built.metadata) if isinstance(built, Values) else {}
        seen = doc.get(PROPS_VER)
        seen = int(seen) if isinstance(seen, (int, float)) and not isinstance(seen, bool) else None
        conditions = [
            Cond(PROPS_VER, IS_NULL) if seen is None else Cond(PROPS_VER, EQ, seen),
            Cond(PROPS_SRC, NULL_OR_LE, stamp),
            Cond(PROPS_GEN, NULL_OR_LE, union.gen),
        ]
        revision = doc.get("contentRev")
        if revision:
            conditions.append(Cond("contentRev", EQ, revision))
        applied = await graph.set_record_properties(
            record_id,
            values=values,
            previous_names=existing_names(doc),
            controls={PROPS_SET: union.set_hash, PROPS_GEN: union.gen, PROPS_SRC: stamp, PROPS_VER: (seen or 0) + 1},
            conditions=conditions,
        )
        if applied:
            return _done(WRITTEN, attempt)
        # Refused: say why from a fresh read, retrying only when the counter alone moved.
        try:
            doc = await graph.get_document(record_id, _RECORDS, raise_on_error=True) or {}
        except Exception as exc:
            return _done(FAILED, attempt, f"read_error:{type(exc).__name__}")
        if not doc or not is_live_record(doc):
            return _done(FAILED, attempt, "record_gone")
        stored_gen, stored_src = doc.get(PROPS_GEN), doc.get(PROPS_SRC)
        if isinstance(stored_gen, (int, float)) and stored_gen > union.gen:
            return _done(FENCED_BY_GEN, attempt)
        if isinstance(stored_src, (int, float)) and stored_src > stamp:
            return _done(SUPERSEDED, attempt)
        if revision and doc.get("contentRev") != revision:
            return _done(CONTENT_CHANGED, attempt)
        log.debug("Projection of record %s lost a race on its counter (attempt %d)", record_id, attempt)
    return _done(CONTENTION, MAX_ATTEMPTS)


def _done(status: str, attempts: int, reason: str = "") -> Projection:
    record_property_projection(f"{status}:{reason}" if reason and status == FAILED else status)
    return Projection(status, attempts, reason)

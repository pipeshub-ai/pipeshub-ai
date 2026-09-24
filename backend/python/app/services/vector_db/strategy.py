"""Collection strategy abstractions for the vector DB layer.

A ``CollectionStrategy`` decides which physical collection(s) a record
belongs to, which collections a query should fan out to, and how a
connector's data should be removed. The default (`SingleCollectionStrategy`)
keeps every deployment on today's single-collection behaviour; alternative
strategies (per-connector-type, per-embedding-model, per-org) plug in
through ``CollectionStrategyFactory`` without changing any call site.

Strategies are pure and synchronous: they compute names from the context
dataclasses below and never touch the graph, vector DB, or KV store. Any
data-dependent fact a strategy needs is computed by ``CollectionRegistry``
and passed in — ``is_last_writer_to_collection`` on the delete path, the
managed-collection list on the read path — which keeps every strategy
trivially unit-testable and stops one from growing hidden I/O.

Contexts are frozen. The dedup path resolves a candidate record's collection,
compares it against a duplicate's, and the write path resolves again later; a
context that could be mutated in between would let a record be skipped as a
duplicate of something living in a collection it was never written to.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any, ClassVar

from app.services.vector_db.collection_manifest import ManagedCollection
from app.services.vector_db.collections import CollectionType, sanitize_collection_name


class DeleteAction(Enum):
    DROP_COLLECTION = "drop_collection"
    FILTERED_DELETE = "filtered_delete"


class ContextAxis(Enum):
    """A ``RecordContext`` field that a strategy's naming can depend on."""

    ORG = "org_id"
    CONNECTOR_ID = "connector_id"
    CONNECTOR_NAME = "connector_name"
    EMBEDDING_MODEL = "embedding_model"


class IncompleteCollectionContext(Exception):
    """A context lacks a field the active strategy needs to name a collection.

    Raised rather than resolved, because the alternative is a plausible-looking
    name — ``_records`` for a null connector under a per-connector-type
    strategy — that reads and deletes would then silently miss.
    """


def _normalize_connector_name(value: Any) -> str | None:
    """Collapse the enum and string spellings of a connector type to one form.

    ``Record.connector_name`` is a ``Connectors`` enum; the same field on a
    graph document is its string value. Both reach collection resolution — the
    write path through the record, the dedup path through the document — and
    must normalize identically, or the two disagree about where a record's
    vectors live and the record is skipped as a duplicate of nothing.
    """
    if value is None:
        return None
    raw = value.value if hasattr(value, "value") else value
    text = str(raw).strip()
    return text or None


@dataclass(frozen=True)
class RecordContext:
    """Everything a strategy needs to resolve a record's write collection."""

    org_id: str
    collection_type: CollectionType = CollectionType.RECORDS
    connector_id: str | None = None
    connector_name: str | None = None  # enum value, e.g. "GOOGLE_DRIVE", "SLACK"
    embedding_model: str | None = None
    embedding_dimension: int | None = None

    @classmethod
    def from_graph_document(
        cls, doc: Mapping[str, Any], **overrides: Any
    ) -> "RecordContext":
        """Build a context from a RECORDS graph document (the dedup path)."""
        return cls(
            org_id=doc.get("orgId") or "",
            connector_id=doc.get("connectorId") or None,
            connector_name=_normalize_connector_name(doc.get("connectorName")),
            **overrides,
        )

    @classmethod
    def from_record(cls, record: Any, org_id: str, **overrides: Any) -> "RecordContext":
        """Build a context from a ``Record`` model instance (the write path)."""
        return cls(
            org_id=org_id,
            connector_id=getattr(record, "connector_id", None) or None,
            connector_name=_normalize_connector_name(
                getattr(record, "connector_name", None)
            ),
            **overrides,
        )


@dataclass(frozen=True)
class QueryContext:
    """Everything a strategy needs to resolve which collections to search."""

    org_id: str
    collection_type: CollectionType = CollectionType.RECORDS
    accessible_connector_ids: list[str] | None = None
    accessible_connector_names: list[str] | None = None  # enum values


@dataclass(frozen=True)
class DeleteContext:
    """Everything a strategy needs to resolve how to remove a connector's data."""

    org_id: str
    connector_id: str
    collection_type: CollectionType = CollectionType.RECORDS
    connector_name: str | None = None  # enum value
    # Supplied by CollectionRegistry (via a graph lookup), never computed by
    # the strategy itself: True when no other live connector maps to the
    # same collection. Lets a per-connector-type strategy choose between
    # DROP_COLLECTION and FILTERED_DELETE without doing its own I/O.
    is_last_writer_to_collection: bool | None = None


@dataclass(frozen=True)
class DeleteScope:
    """What CollectionRegistry should execute to remove a connector's data."""

    action: DeleteAction
    collection_names: list[str]
    filter_field: str | None = None
    filter_values: list[str] | None = None


class CollectionStrategy(ABC):
    """Resolves collection names for writes, reads, and deletes."""

    @abstractmethod
    def resolve_write_collection(self, ctx: RecordContext) -> str:
        """Which collection should this record be written to?"""

    @abstractmethod
    def resolve_read_collections(
        self, ctx: QueryContext, managed: Sequence[ManagedCollection]
    ) -> list[str]:
        """Which collections should be queried for this search?

        ``managed`` is every collection the registry knows about, supplied so a
        strategy that cannot narrow from ``ctx`` alone can return the full set
        for this collection type rather than inventing a name. Same principle
        as ``is_last_writer_to_collection``: the registry does the I/O, the
        strategy stays pure.
        """

    @abstractmethod
    def resolve_delete_scope(self, ctx: DeleteContext) -> DeleteScope:
        """How should this connector's data be removed?"""

    @abstractmethod
    def strategy_name(self) -> str:
        """Persist-safe identifier, e.g. 'single', 'per_connector_type'."""

    @property
    def required_axes(self) -> frozenset[ContextAxis]:
        """Context fields ``resolve_write_collection`` actually reads.

        The empty default means org-agnostic: a caller holding no record can
        still resolve a name, which is what lets the registry adopt a
        pre-manifest collection on upgrade. Declaring an axis buys two things —
        the registry refuses a context missing it, and call sites that only
        know about "the" collection fail loudly instead of quietly targeting
        one that does not exist.

        Declaring an axis you do not read is harmless. Reading one you do not
        declare is the bug this exists to prevent.
        """
        return frozenset()

    @property
    def read_narrowing_axes(self) -> frozenset[ContextAxis]:
        """Context that would let ``resolve_read_collections`` search fewer places.

        Gathering it costs a query the search would not otherwise make, so the
        caller needs to know whether it is worth paying for. Empty — the
        default, and ``single``'s answer — means the fan-out is already minimal
        and the caller should not bother.

        Distinct from ``required_axes``: that is what a *write* cannot proceed
        without, this is what a *read* would merely benefit from. A strategy
        may narrow on an axis it does not name collections by, and must stay
        correct when the caller supplies nothing.
        """
        return frozenset()


def resolve_write_collection_name(
    strategy: CollectionStrategy, ctx: RecordContext
) -> str:
    """The single way to turn a ``RecordContext`` into a physical name.

    Validate the strategy's declared axes, resolve, then sanitize — in that
    order, in one place. The dedup path and the write path both go through
    here, and a record is skipped as a duplicate on the strength of their
    answers matching, so two spellings of this sequence is one bug away from
    silently dropping a record's vectors.
    """
    missing = sorted(
        axis.value
        for axis in strategy.required_axes
        if not getattr(ctx, axis.value, None)
    )
    if missing:
        raise IncompleteCollectionContext(
            f"Strategy '{strategy.strategy_name()}' needs {missing} to name a "
            f"collection, but the context supplied none. Resolving anyway would "
            f"produce a name that reads and deletes then silently miss."
        )
    return sanitize_collection_name(strategy.resolve_write_collection(ctx))


class CollectionStrategyFactory:
    """String-keyed strategy registry.

    OSS ships only ``single``. Enterprise Edition (or future OSS) code
    registers additional strategies at import time, so the strategy
    resolver can select one by name without this module knowing it exists.
    """

    _builders: ClassVar[dict[str, Callable[..., CollectionStrategy]]] = {}

    @classmethod
    def register(cls, name: str, builder: Callable[..., CollectionStrategy]) -> None:
        cls._builders[name] = builder

    @classmethod
    def create(cls, name: str, **kwargs: object) -> CollectionStrategy:
        builder = cls._builders.get(name)
        if builder is None:
            available = sorted(cls._builders.keys())
            raise ValueError(
                f"Unknown collection strategy '{name}'. Registered strategies: {available}"
            )
        return builder(**kwargs)

    @classmethod
    def registered_names(cls) -> list[str]:
        return sorted(cls._builders.keys())

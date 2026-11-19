"""Extension points of the indexing service, each with a working default.

The default of every point is the behaviour the service has without any extension, so nothing here changes what an
installation does until something is installed. A package that wants to change a point installs its implementation
once at start-up with :func:`install`; code that needs a point reads it with :func:`current`. Nothing registers at import
time, and :func:`reset` restores the defaults (tests call it between cases).

The points:

* ``RecordOwnership``: whose connector an indexing record belongs to, and whether a record indexes on behalf of another.
* ``LongDocumentPolicy``: what to do with a PDF before it is inspected (see ``app.modules.indexing.page_limit``).
* ``IndexingLifecycle``: hooks around parsing, terminal statuses, stage settling and recovery.
* ``StageHooks``: optional replacements for what the classify and entities stages read and keep.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from app.exceptions.indexing_exceptions import IndexingError


class RecordOwnerUnknown(IndexingError):
    """A record that indexes on behalf of another, whose owner cannot be read now (it may be mid-create or mid-purge).

    It is an ``IndexingError``, which the message classifier treats as transient, so the work is retried."""


# --- long documents ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Unreadable:
    """pdfium says it cannot open the file."""


@dataclass(frozen=True)
class Unavailable:
    """The count could not be taken now: a timeout, a dead worker or a closed pool."""


PageCount = int | Unreadable | Unavailable


@dataclass
class LongDocument:
    """What a policy sees before the document is inspected. ``snapshot`` is the record document as read before any write."""

    record_id: str
    snapshot: Mapping[str, Any]
    org_id: str
    content: bytes | None
    content_rev: str | None
    md5: str | None
    extension: str | None
    mime_type: str | None
    event_type: str | None = None
    force_reindex: bool = False
    inspected: Any = None
    store_only: bool = False
    keep_eligible: bool = False


@dataclass(frozen=True)
class Proceed:
    """Index as usual. ``pages`` is the page count when known."""

    pages: int | None = None
    clear_note: bool = False


@dataclass(frozen=True)
class Keep:
    """The existing index stays. The status is restored to COMPLETED where the rules allow, and ``note`` is written."""

    note: str | None = None
    pages: int | None = None


@dataclass(frozen=True)
class Refuse:
    """Un-index the record, then write ``status`` with ``reason``."""

    status: str
    reason: str
    pages: int | None = None


@dataclass(frozen=True)
class Handled:
    """The policy indexes the document itself. ``events`` is drained in the caller's task, closed with ``aclosing``."""

    events: AsyncIterator[Any]


class LongDocumentPolicy(Protocol):
    async def check(self, doc: LongDocument) -> Proceed | Keep | Refuse | Handled: ...

    async def holds_stand_in(self, vrid: str | None, extension: str | None, mime_type: str | None) -> bool: ...

    def deferred_stages(self) -> frozenset[str]: ...


class DefaultLongDocumentPolicy:
    """No policy of its own: every document proceeds."""

    async def check(self, doc: LongDocument) -> Proceed | Keep | Refuse | Handled:
        return Proceed()

    async def holds_stand_in(self, vrid: str | None, extension: str | None, mime_type: str | None) -> bool:
        return False

    def deferred_stages(self) -> frozenset[str]:
        return frozenset()


# --- ownership --------------------------------------------------------------------------------------------------------


class RecordOwnership(Protocol):
    def owner_connector_id(self, record: object) -> str | None: ...

    def is_derived(self, record: object) -> bool: ...

    def has_lane(self, connector_id: str) -> bool: ...

    async def prime(self, records: list, graph: object) -> None: ...

    async def prime_readable(self, page: list, graph: object, skips: object, logger: object) -> list: ...

    def derived_pages(
        self, graph: object, connector_id: str, org_id: str, filters: Mapping[str, Any] | None, *, page_size: int
    ) -> AsyncIterator[list]: ...


def _field(record: object, name: str) -> object:
    if isinstance(record, Mapping):
        return record.get(name)
    return getattr(record, name, None)


class DefaultRecordOwnership:
    """A record belongs to its own connector and indexes on its own behalf."""

    def owner_connector_id(self, record: object) -> str | None:
        # A document names it connectorId; a record model, connector_id.
        if isinstance(record, Mapping):
            return record.get("connectorId") or record.get("connector_id") or None
        return getattr(record, "connector_id", None) or None

    def is_derived(self, record: object) -> bool:
        return False

    def has_lane(self, connector_id: str) -> bool:
        return True

    async def prime(self, records: list, graph: object) -> None:
        return None

    async def prime_readable(self, page: list, graph: object, skips: object, logger: object) -> list:
        return page

    async def derived_pages(
        self, graph: object, connector_id: str, org_id: str, filters: Mapping[str, Any] | None, *, page_size: int
    ) -> AsyncIterator[list]:
        return
        yield []  # pragma: no cover - makes this an (empty) async generator


# --- lifecycle --------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ReembedPlan:
    """How a re-embed from stored blocks proceeds. The default is the whole-file plan."""

    delete_whole: bool = True
    reproject_entities: bool = True
    settings: Mapping[str, object] = field(default_factory=dict)
    after: Callable[[], Awaitable[None]] | None = None

    @classmethod
    def whole(cls) -> ReembedPlan:
        return cls()


@dataclass(frozen=True)
class PdfParseHints:
    """How a PDF is to be parsed, when an admission wants to decide that: ``ocr`` True/False forces the OCR route on or
    off (None: the inspection decides), ``allow_docling`` False keeps the PDF off the layout service."""

    ocr: bool | None = None
    allow_docling: bool = True


@dataclass(frozen=True)
class Admit:
    """The record may be indexed. ``hints`` shape how a PDF is parsed (None: as the inspection decides)."""

    hints: PdfParseHints | None = None


@dataclass(frozen=True)
class Stop:
    status: str
    reason: str | None = None


class IndexingLifecycle(Protocol):
    async def admit(self, record: object, graph: object) -> Admit | Stop: ...

    async def after_parse(self, record: object, graph: object, logger: object) -> None: ...

    async def on_terminal(self, record: object, status: str) -> None: ...

    async def on_stage_settled(self, job: object) -> None: ...

    async def on_reextract_complete(self, record: object, extraction: object) -> bool: ...

    async def on_connector_purged(self, org_id: str, connector_id: str) -> None: ...

    async def recovery_pass(self) -> int: ...

    async def reembed_plan(self, record: object) -> ReembedPlan | None: ...

    async def skip_first_use(self, record: object) -> bool: ...


class DefaultIndexingLifecycle:
    async def admit(self, record: object, graph: object) -> Admit | Stop:
        return Admit()

    async def after_parse(self, record: object, graph: object, logger: object) -> None:
        return None

    async def on_terminal(self, record: object, status: str) -> None:
        return None

    async def on_stage_settled(self, job: object) -> None:
        return None

    async def on_reextract_complete(self, record: object, extraction: object) -> bool:
        return False

    async def on_connector_purged(self, org_id: str, connector_id: str) -> None:
        return None

    async def recovery_pass(self) -> int:
        return 0

    async def reembed_plan(self, record: object) -> ReembedPlan | None:
        return None

    async def skip_first_use(self, record: object) -> bool:
        return False


@dataclass(frozen=True)
class StageInput:
    """What a stage reads in place of the record's own stored blocks: their text digest, and the blocks.

    ``extraction`` is for the entities stage only: when set, it is the extraction of those blocks, taken as it is without
    running the model over them (the hooks computed or gathered it). Absent, the stage extracts as usual."""

    digest: str
    blocks: Any
    extraction: Any = None


@runtime_checkable
class StageHooks(Protocol):
    async def classify_input(self, job: object, io: object) -> StageInput | None: ...

    async def entities_input(self, job: object, io: object) -> StageInput | None: ...

    async def save_classification(self, job: object, io: object, owner: object, metadata: object) -> bool: ...

    async def keep_extraction(self, job: object, io: object, owner: object, extraction: object) -> bool: ...

    async def restore_classification(self, job: object, io: object, metadata: object) -> bool:
        """Put a kept classification back (a re-index of unchanged content rewrote the stored record without it). True when
        the hooks restored it themselves; False leaves the stage to write it as it always did."""
        ...


# --- the installed extension ------------------------------------------------------------------------------------------


@dataclass
class IndexingExtension:
    ownership: RecordOwnership = field(default_factory=DefaultRecordOwnership)
    policy: LongDocumentPolicy = field(default_factory=DefaultLongDocumentPolicy)
    lifecycle: IndexingLifecycle = field(default_factory=DefaultIndexingLifecycle)
    stage_hooks: StageHooks | None = None

    def deferred_stages(self) -> frozenset[str]:
        return self.policy.deferred_stages()


_installed: list[IndexingExtension] = [IndexingExtension()]


def current() -> IndexingExtension:
    return _installed[0]


def install(extension: IndexingExtension) -> None:
    _installed[0] = extension


def reset() -> None:
    _installed[0] = IndexingExtension()


async def notify_terminal(record: object, status: object) -> None:
    """Tell the lifecycle that ``record`` reached a terminal ``status``. Every writer of a terminal status calls this."""
    await current().lifecycle.on_terminal(record, getattr(status, "value", status))

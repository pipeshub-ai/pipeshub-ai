"""Read seam for a record's processed content.

Query-side readers go through ``record_artifact_reader`` rather than calling
``BlobStorage.get_record_from_storage`` directly, so the storage layout behind
a virtual record id can change without touching them.

Which store serves a read is decided per read from two platform flags (see
``artifact_read_mode``): the stored record (both off), the stored record
compared with the artifact set (``ENABLE_ARTIFACT_READ_SHADOW``), or the
artifact set with the stored record as the fallback (``ENABLE_ARTIFACT_READ``).
"""

import asyncio
import logging
import threading
import time
from typing import TYPE_CHECKING, Any, Protocol

from app.modules.artifacts.record_backend import (
    ArtifactRecordBackend,
    ArtifactUnavailable,
)
from app.services.featureflag.platform_settings import (
    ArtifactReadMode,
    artifact_read_mode,
)
from app.services.messaging.membership import reader_for
from app.telemetry.modules.record_storage_metrics import (
    record_artifact_read,
    record_artifact_shadow_read,
)

if TYPE_CHECKING:
    from app.modules.transformers.blob_storage import BlobStorage

#: How long a served read waits for its shadow artifact read once the stored record is in.
SHADOW_GRACE_SECONDS = 0.5
#: Shadow artifact reads in flight per process; past it, reads are served unshadowed.
SHADOW_MAX_IN_FLIGHT = 64
#: At most one mismatch line per this many seconds per process.
SHADOW_LOG_INTERVAL_SECONDS = 10.0

logger = logging.getLogger(__name__)


class RecordArtifactReader(Protocol):
    async def read_record(
        self,
        virtual_record_id: str,
        org_id: str,
        *,
        lookup: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """The record's processed content in the stored-record dict shape.

        ``lookup`` is the entry ``lookup_many`` returned for this id; without
        it the reader resolves the id itself. None when the record has no
        stored content.
        """
        ...

    async def lookup_many(self, virtual_record_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Resolve many ids in one batch, for ``read_record(lookup=...)``.

        Ids that do not resolve are absent from the result.
        """
        ...


class LegacyRecordArtifactReader:
    """Reads the ``record_{vrid}.json`` document that ``BlobStorage`` stores."""

    def __init__(self, blob_store: "BlobStorage") -> None:
        self._blob_store = blob_store

    async def read_record(
        self,
        virtual_record_id: str,
        org_id: str,
        *,
        lookup: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        return await self._blob_store.get_record_from_storage(
            virtual_record_id, org_id, lookup_result=lookup
        )

    async def lookup_many(self, virtual_record_ids: list[str]) -> dict[str, dict[str, Any]]:
        return await self._blob_store.get_document_ids_by_virtual_record_ids(virtual_record_ids)


def first_difference(served: Any, candidate: Any) -> tuple[str, str] | None:  # noqa: ANN401
    """Where ``candidate`` first differs from ``served``: ``(reason, where)``, or None.

    ``where`` names keys and indexes only, never content, so it can be logged.
    """
    if served == candidate:
        return None
    if not isinstance(served, dict) or not isinstance(candidate, dict):
        return ("legacy_missing", "") if served is None else ("record_field", "")

    def differing(a: dict[str, Any], b: dict[str, Any], skip: frozenset[str] = frozenset()) -> list[str]:
        return sorted(k for k in a.keys() | b.keys() if k not in skip and (k not in a or k not in b or a[k] != b[k]))

    containers = "block_containers"
    for key in differing(served, candidate, frozenset({containers})):
        return ("semantic_metadata" if key == "semantic_metadata" else "record_field", key)
    a, b = served.get(containers), candidate.get(containers)
    if not isinstance(a, dict) or not isinstance(b, dict):
        return ("container_field", containers)
    for key in differing(a, b, frozenset({"blocks", "block_groups"})):
        return ("container_field", f"{containers}.{key}")
    for name, count_reason, item_reason in (("blocks", "block_count", "block"), ("block_groups", "group_count", "group")):
        items_a, items_b = a.get(name) or [], b.get(name) or []
        if len(items_a) != len(items_b):
            return (count_reason, f"{name}: {len(items_a)} != {len(items_b)}")
        for index, (x, y) in enumerate(zip(items_a, items_b)):
            if x != y:
                keys = differing(x, y) if isinstance(x, dict) and isinstance(y, dict) else []
                return (item_reason, f"{name}[{index}].{','.join(keys[:5])}")
    return ("record_field", "")


class _MismatchLog:
    """Rate-limits mismatch lines: a widespread difference must not flood the log."""

    def __init__(self, interval: float) -> None:
        self._interval = interval
        self._lock = threading.Lock()
        self._next = 0.0
        self.suppressed = 0

    def take(self) -> int | None:
        """The count suppressed since the last line when one may be logged now, else None."""
        now = time.monotonic()
        with self._lock:
            if now < self._next:
                self.suppressed += 1
                return None
            self._next = now + self._interval
            suppressed, self.suppressed = self.suppressed, 0
            return suppressed


# Process-wide: shadow reads double storage reads, so their number is capped
# across every reader and event loop.
_SHADOW_SLOTS = threading.BoundedSemaphore(SHADOW_MAX_IN_FLIGHT)
_MISMATCH_LOG = _MismatchLog(SHADOW_LOG_INTERVAL_SECONDS)


def _consume_result(task: "asyncio.Task[Any]") -> None:
    # A shadow read the served read stopped waiting for must not log "never retrieved".
    if not task.cancelled():
        task.exception()


class ShadowRecordArtifactReader:
    """Serves the stored record and compares the artifact set with it.

    The artifact read runs beside the stored-record read and is waited for at
    most ``grace_seconds`` after the stored record is in, then cancelled: the
    comparison happens before the record is handed out (so it needs no copy of
    a record the caller may change), and the served read is never slower than
    that bound nor fails because of the artifact read.
    """

    def __init__(
        self,
        legacy: RecordArtifactReader,
        artifact: ArtifactRecordBackend,
        *,
        grace_seconds: float = SHADOW_GRACE_SECONDS,
        slots: threading.BoundedSemaphore | None = None,
    ) -> None:
        self._legacy = legacy
        self._artifact = artifact
        self._grace = grace_seconds
        self._slots = slots or _SHADOW_SLOTS

    async def read_record(
        self,
        virtual_record_id: str,
        org_id: str,
        *,
        lookup: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if not self._slots.acquire(blocking=False):
            record_artifact_shadow_read("skipped")
            return await self._legacy.read_record(virtual_record_id, org_id, lookup=lookup)
        shadow = asyncio.create_task(self._artifact.read(virtual_record_id, org_id, lookup=lookup))
        shadow.add_done_callback(_consume_result)
        try:
            try:
                served = await self._legacy.read_record(virtual_record_id, org_id, lookup=lookup)
            except Exception:
                record_artifact_shadow_read("legacy_failed")
                raise
            try:
                await self._compare(virtual_record_id, served, shadow)
            except Exception:
                logger.debug("Shadow comparison of %s failed", virtual_record_id, exc_info=True)
            return served
        finally:
            if not shadow.done():
                shadow.cancel()
            self._slots.release()

    async def _compare(
        self, virtual_record_id: str, served: dict[str, Any] | None, shadow: "asyncio.Task[dict[str, Any]]",
    ) -> None:
        done, _ = await asyncio.wait({shadow}, timeout=self._grace)
        if not done:
            record_artifact_shadow_read("timeout")
            return
        if shadow.cancelled() or shadow.exception() is not None:
            # ArtifactUnavailable, already counted by outcome in the read metric.
            record_artifact_shadow_read("unavailable")
            return
        difference = first_difference(served, shadow.result())
        if difference is None:
            record_artifact_shadow_read("match")
            return
        reason, where = difference
        record_artifact_shadow_read("mismatch", reason)
        suppressed = _MISMATCH_LOG.take()
        if suppressed is not None:
            logger.warning(
                "Artifact set of %s differs from its stored record: %s at %s (%d more not logged)",
                virtual_record_id, reason, where or "-", suppressed,
            )

    async def lookup_many(self, virtual_record_ids: list[str]) -> dict[str, dict[str, Any]]:
        return await self._legacy.lookup_many(virtual_record_ids)


class ArtifactFirstRecordArtifactReader:
    """Serves the artifact set; a record whose set cannot be served is read from the stored record."""

    def __init__(self, legacy: RecordArtifactReader, artifact: ArtifactRecordBackend) -> None:
        self._legacy = legacy
        self._artifact = artifact

    async def read_record(
        self,
        virtual_record_id: str,
        org_id: str,
        *,
        lookup: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        # An older indexing member may be writing in place without the stored-write protocol, or the
        # membership check cannot be seen: the stored record is the only safe answer.
        reader = reader_for(getattr(getattr(self._artifact, "_blob_store", None), "config_service", None))
        if reader is not None and not (await reader.verdict()).ok:
            record_artifact_read("membership_hold")
            return await self._legacy.read_record(virtual_record_id, org_id, lookup=lookup)
        try:
            return await self._artifact.read(virtual_record_id, org_id, lookup=lookup)
        except ArtifactUnavailable:
            return await self._legacy.read_record(virtual_record_id, org_id, lookup=lookup)

    async def lookup_many(self, virtual_record_ids: list[str]) -> dict[str, dict[str, Any]]:
        return await self._legacy.lookup_many(virtual_record_ids)


class ConfiguredRecordArtifactReader:
    """Picks the reader for each read from the platform flags (read through the config cache)."""

    def __init__(self, blob_store: "BlobStorage") -> None:
        self._blob_store = blob_store
        self.legacy = LegacyRecordArtifactReader(blob_store)
        self._readers: dict[ArtifactReadMode, RecordArtifactReader] = {"legacy": self.legacy}

    def reader(self, mode: ArtifactReadMode) -> RecordArtifactReader:
        if mode not in self._readers:
            backend = ArtifactRecordBackend(self._blob_store)
            self._readers[mode] = (
                ArtifactFirstRecordArtifactReader(self.legacy, backend)
                if mode == "artifact"
                else ShadowRecordArtifactReader(self.legacy, backend)
            )
        return self._readers[mode]

    async def read_record(
        self,
        virtual_record_id: str,
        org_id: str,
        *,
        lookup: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        mode = await artifact_read_mode(getattr(self._blob_store, "config_service", None))
        return await self.reader(mode).read_record(virtual_record_id, org_id, lookup=lookup)

    async def lookup_many(self, virtual_record_ids: list[str]) -> dict[str, dict[str, Any]]:
        return await self.legacy.lookup_many(virtual_record_ids)


def record_artifact_reader(blob_store: "BlobStorage") -> RecordArtifactReader:
    """The reader for ``blob_store``'s records. Cheap enough to call per request."""
    return ConfiguredRecordArtifactReader(blob_store)

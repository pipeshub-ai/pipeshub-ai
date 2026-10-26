"""Where indexed content is stored, checked against where it should be.

Indexing files each record's processed content as two storage documents,
``record_<vrid>`` and ``metadata_<vrid>``, in a folder that mirrors the record's
place in its source: ``records/<connectorId>/<groups>/<parents>/<name>``. On
disk each one is ``<folder>/<documentId>/<documentName>.json``. Pattern-match
search greps that tree, so a copy in the wrong folder is not just untidy: it is
found under the wrong name, or not found at all.

Three stores have to agree for that to hold. MongoDB names the folder, blob
storage holds the bytes, and the graph says where the record is now. The
probe reads all three; it never asks the API, which resolves documents through
MongoDB and so cannot see a copy that MongoDB has lost track of.

Paths here are relative to ``<orgId>/PipesHub/``, the way the move-tree route
and the indexer name them.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable

from helper.blob_store import BlobStoreProbe
from helper.mongo_store import MongoStoreProbe, is_within

logger = logging.getLogger("storage-layout")

_DEFAULT_TIMEOUT = 180
_POLL_INTERVAL = 2.0
KINDS = ("record", "metadata")
# Linux refuses a longer file name; S3/Azure keys stop at 1024 bytes, and Node
# adds "<orgId>/PipesHub/" before and "/<docId>/record_<vrid>.json" after.
MAX_SEGMENT_BYTES = 255
MAX_PATH_BYTES = 900


@dataclass(frozen=True, order=True)
class StoredCopy:
    kind: str
    virtual_record_id: str
    document_id: str
    path: str
    is_deleted: bool = False

    @property
    def file(self) -> str:
        return f"{self.path}/{self.document_id}/{self.kind}_{self.virtual_record_id}.json"


def org_root(org_id: str) -> str:
    return f"{org_id}/PipesHub"


def connector_root(connector_id: str) -> str:
    return f"records/{connector_id}"


def _relative(document_path: str, org_id: str) -> str:
    root = f"{org_root(org_id)}/"
    return document_path[len(root):] if document_path.startswith(root) else document_path


def _copy_from(doc: dict[str, Any], org_id: str) -> StoredCopy | None:
    kind, _, vrid = str(doc.get("documentName") or "").partition("_")
    if kind not in KINDS or not vrid:
        return None
    return StoredCopy(
        kind=kind,
        virtual_record_id=vrid,
        document_id=str(doc["_id"]),
        path=_relative(str(doc.get("documentPath") or ""), org_id),
        is_deleted=bool(doc.get("isDeleted")),
    )


_UNSAFE = re.compile(r'[/\\:*?"<>|\x00-\x1f\x7f]')


def expected_segment(name: str) -> str:
    """The folder name a record or group called *name* is stored under.

    Written out here from the rule rather than imported from the app, so a
    change to the rule fails these tests instead of moving with them: unsafe
    characters become ``_``; at most 100 characters; ``.``/``..`` and blank
    names become underscores; a name still over 255 bytes is cut at a
    character boundary and ends in ``~`` plus 16 hex of its SHA-256.
    """
    cleaned = _UNSAFE.sub("_", name.strip())[:100]
    if cleaned in (".", ".."):
        cleaned = "_" * len(cleaned)
    if not cleaned:
        return "_"
    if len(cleaned.encode("utf-8")) > MAX_SEGMENT_BYTES:
        suffix = "~" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]
        cleaned = cleaned.encode("utf-8")[: MAX_SEGMENT_BYTES - len(suffix)].decode("utf-8", "ignore") + suffix
    return cleaned


def expected_path(connector_id: str, *names: str) -> str:
    """``records/<connector>/<segment>/...`` for the given chain of names, root first.

    Only for chains under the 900-byte cap; past it the tail is folded into a
    hash, which ``assert_path_capped`` checks instead.
    """
    path = "/".join([connector_root(connector_id), *(expected_segment(n) for n in names)])
    assert len(path.encode("utf-8")) <= MAX_PATH_BYTES, "use assert_path_capped for paths over the cap"
    return path


def oversized_segments(path: str) -> list[str]:
    return [s for s in path.split("/") if len(s.encode("utf-8")) > MAX_SEGMENT_BYTES]


class StorageLayoutProbe:
    """Read-only questions about where one org's indexed content is stored."""

    def __init__(self, mongo: MongoStoreProbe, blob: BlobStoreProbe, org_id: str) -> None:
        self._mongo = mongo
        self._blob = blob
        self.org_id = org_id

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #

    async def copies_of(self, virtual_record_id: str, *, include_deleted: bool = False) -> list[StoredCopy]:
        docs = await self._mongo.stored_record_documents(
            self.org_id, virtual_record_id=virtual_record_id, include_deleted=include_deleted
        )
        return sorted(c for c in (_copy_from(d, self.org_id) for d in docs) if c)

    async def copies_under(self, path: str, *, include_deleted: bool = False) -> list[StoredCopy]:
        docs = await self._mongo.stored_record_documents(
            self.org_id, under=f"{org_root(self.org_id)}/{path}", include_deleted=include_deleted
        )
        return sorted(c for c in (_copy_from(d, self.org_id) for d in docs) if c)

    async def files_under(self, path: str) -> list[str]:
        """Stored files below *path*, relative to the org root."""
        marker = f"/{org_root(self.org_id)}/"
        files = await self._blob.files_under(f"{org_root(self.org_id)}/{path}")
        return sorted(f.split(marker, 1)[1] if marker in f else f for f in files)

    # ------------------------------------------------------------------ #
    # One record
    # ------------------------------------------------------------------ #

    async def _placement_problems(
        self, virtual_record_id: str, expected: set[str], kinds: Iterable[str]
    ) -> tuple[list[str], list[StoredCopy]]:
        copies = await self.copies_of(virtual_record_id)
        problems: list[str] = []
        for kind in kinds:
            mine = [c for c in copies if c.kind == kind]
            if len(mine) != 1:
                problems.append(f"{len(mine)} live {kind}_{virtual_record_id} documents (want 1)")
                continue
            if mine[0].path not in expected:
                problems.append(
                    f"{kind}_{virtual_record_id} is filed at {mine[0].path!r}, "
                    f"expected {' or '.join(sorted(map(repr, expected)))}"
                )
                continue
            if mine[0].file not in await self.files_under(mine[0].path):
                problems.append(f"MongoDB files {kind}_{virtual_record_id} at {mine[0].file!r} but no such blob")
        return problems, copies

    async def assert_stored_at(
        self,
        virtual_record_id: str,
        expected: str | Iterable[str],
        *,
        kinds: Iterable[str] = KINDS,
        timeout: int = _DEFAULT_TIMEOUT,
    ) -> list[StoredCopy]:
        """One live copy of each kind, filed at *expected*, with its blob on disk.

        Polls: moves and re-indexing finish after the API call that caused them
        returns. *expected* may name several folders when the content is shared
        and any holder's folder is acceptable.
        """
        wanted = {expected} if isinstance(expected, str) else set(expected)
        kinds = tuple(kinds)
        deadline = asyncio.get_event_loop().time() + timeout
        problems, copies = await self._placement_problems(virtual_record_id, wanted, kinds)
        while problems and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(_POLL_INTERVAL)
            problems, copies = await self._placement_problems(virtual_record_id, wanted, kinds)
        assert not problems, (
            f"Content {virtual_record_id} is not stored where it should be after {timeout}s:\n  "
            + "\n  ".join(problems)
            + "\nMongoDB holds:\n  "
            + ("\n  ".join(f"{c.kind} {c.document_id} at {c.path!r}" for c in copies) or "(nothing)")
        )
        return copies

    async def assert_nothing_under(self, path: str, *, timeout: int = _DEFAULT_TIMEOUT) -> None:
        """No live storage document and no file left at or below *path*."""
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            rows = await self.copies_under(path)
            files = await self.files_under(path)
            if not rows and not files:
                return
            if asyncio.get_event_loop().time() >= deadline:
                break
            await asyncio.sleep(_POLL_INTERVAL)
        raise AssertionError(
            f"{path!r} should be empty after {timeout}s, but still holds "
            f"{len(rows)} storage document(s) and {len(files)} file(s):\n  "
            + "\n  ".join([f"row {c.kind}_{c.virtual_record_id} at {c.path!r}" for c in rows] + files[:10])
        )

    # ------------------------------------------------------------------ #
    # A whole connector
    # ------------------------------------------------------------------ #

    async def connector_problems(self, graph_provider: Any, connector_id: str) -> list[str]:
        """Everything that disagrees between graph, MongoDB and blob for one connector.

        Each indexed record's content must be filed where the graph puts the
        record now (or, for content shared between records, where one of its
        holders is). Every stored document under the connector's folder must
        belong to a live record, and every file there to a stored document.
        """
        expected_paths = await expected_paths_for_connector(graph_provider, self.org_id, connector_id)
        root = connector_root(connector_id)
        rows = await self.copies_under(root)
        files = set(await self.files_under(root))
        problems: list[str] = []

        by_vrid: dict[str, set[str]] = {}
        for record_id, (vrid, path) in expected_paths.items():
            if path is None:
                problems.append(f"record {record_id}: the graph gives it no storage path")
                continue
            by_vrid.setdefault(vrid, set()).add(path)
        for vrid, paths in sorted(by_vrid.items()):
            holders = await _live_holders(graph_provider, vrid)
            for holder in holders - set(expected_paths):
                path = await expected_path_for_record(graph_provider, holder)
                if path is None:
                    problems.append(f"record {holder}: the graph gives it no storage path")
                else:
                    paths.add(path)
            found, _ = await self._placement_problems(vrid, paths, ("record",))
            problems.extend(found)

        live_vrids = set(by_vrid)
        for row in rows:
            if row.virtual_record_id not in live_vrids and not await _live_holders(graph_provider, row.virtual_record_id):
                problems.append(f"orphan {row.kind}_{row.virtual_record_id} at {row.path!r}: no live record holds it")
            if row.file not in files:
                problems.append(f"{row.kind}_{row.virtual_record_id} at {row.path!r} has no blob")
            for segment in oversized_segments(row.path):
                problems.append(f"{row.path!r}: segment over {MAX_SEGMENT_BYTES} bytes ({segment[:40]}...)")
            if len(row.path.encode("utf-8")) > MAX_PATH_BYTES:
                problems.append(f"{row.path[:80]!r}...: path over {MAX_PATH_BYTES} bytes")
        known = {r.file for r in await self.copies_under(root, include_deleted=True)}
        problems.extend(f"orphan file {f}: no storage document" for f in sorted(files - known))

        flat = [
            c for vrid in live_vrids for c in await self.copies_of(vrid)
            if not is_within(c.path, "records/") or c.path.count("/") == 1
        ]
        problems.extend(f"{c.kind}_{c.virtual_record_id} is still in the flat layout at {c.path!r}" for c in flat)
        return problems

    async def assert_connector_consistent(
        self, graph_provider: Any, connector_id: str, *, timeout: int = _DEFAULT_TIMEOUT
    ) -> None:
        deadline = asyncio.get_event_loop().time() + timeout
        problems = await self.connector_problems(graph_provider, connector_id)
        while problems and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(_POLL_INTERVAL * 5)
            problems = await self.connector_problems(graph_provider, connector_id)
        assert not problems, (
            f"Stored content of connector {connector_id} disagrees with the graph after {timeout}s "
            f"({len(problems)} problem(s)):\n  " + "\n  ".join(problems[:40])
        )


# ---------------------------------------------------------------------- #
# What the graph says
# ---------------------------------------------------------------------- #

async def expected_path_for_record(graph_provider: Any, record_id: str) -> str | None:
    """The folder indexing would file this record's content in, from the graph as it is now."""
    from app.utils.storage_path import build_hierarchical_storage_path

    # Not app.events.processor: importing it loads the parsers, which need libGL.
    record = await graph_provider.get_record_by_id(record_id)
    if record is None:
        return None
    return await build_hierarchical_storage_path(record, graph_provider)


async def expected_paths_for_connector(
    graph_provider: Any, org_id: str, connector_id: str
) -> dict[str, tuple[str, str | None]]:
    """``record id -> (virtual record id, expected folder)`` for every indexed record."""
    from app.utils.storage_path import build_hierarchical_storage_path

    out: dict[str, tuple[str, str | None]] = {}
    offset, page = 0, 500
    while True:
        records = await graph_provider.get_records_by_status(
            org_id, connector_id, ["COMPLETED"], limit=page, offset=offset
        )
        for record in records:
            if record.virtual_record_id:
                out[record.id] = (
                    record.virtual_record_id,
                    await build_hierarchical_storage_path(record, graph_provider),
                )
        if len(records) < page:
            return out
        offset += page


async def _live_holders(graph_provider: Any, virtual_record_id: str) -> set[str]:
    return set(await graph_provider.get_records_by_virtual_record_id(virtual_record_id, raise_on_error=True))


def decode_stored_record(raw: bytes) -> Any:
    """A stored ``record_<vrid>`` document as the indexer wrote it, decompressed if it was.

    Small records are plain JSON; larger ones are an envelope whose ``record``
    is base64 zstd-compressed msgpack (``BlobStorage._process_downloaded_record``).
    """
    import base64
    import json

    data = json.loads(raw)
    if isinstance(data, dict) and data.get("isCompressed"):
        import msgspec
        import zstandard

        return msgspec.msgpack.decode(
            zstandard.ZstdDecompressor().decompress(base64.b64decode(data["record"]))
        )
    return data

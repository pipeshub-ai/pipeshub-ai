"""An in-memory paid-result store that versions objects the way each real store does, for race models.

``etag_mode``: ``md5`` (S3, MinIO: the ETag is the content hash, so only a byte change moves it), ``unique`` (Azure
and local: every write gives a new one). ``stamp``: whole seconds (S3, Azure) or milliseconds (local). Every store
call first yields to ``before_op`` (a scheduler's point), so a test chooses the interleaving.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

from app.services.paid_cache.store import (
    BUMPED,
    CHANGED,
    GONE,
    ListedFile,
    VersionedBytes,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence


class VersionedStore:
    supports_bump = True

    def __init__(
        self, *, etag_mode: str = "md5", stamp_ms: int = 1000, now_ms: int = 1_700_000_000_000,
        copy_in_place_bump: bool = False, gone_is_412: bool = False, conditional_delete: bool = True,
    ) -> None:
        self.etag_mode = etag_mode
        #: False: the store ignores the condition on a delete (a MinIO build does) and the service says so.
        self.supports_conditional_delete = conditional_delete
        self.stamp_ms = stamp_ms
        self.now_ms = now_ms
        #: The round-1 bump (a copy that changes metadata only): the content-hash ETag does not move. A control.
        self.copy_in_place_bump = copy_in_place_bump
        #: A store that answers a conditional write of a missing key with 412 instead of 404.
        self.gone_is_412 = gone_is_412
        self.files: dict[tuple[str, str, str], list[Any]] = {}  # (org, rec, path) -> [bytes, etag, stamp]
        self._n = 0
        self.before_op: Callable[[str], Awaitable[None]] | None = None
        self.deleted_records: set[tuple[str, str]] = set()
        self.unreadable_marker = False
        self.ops: list[str] = []

    # -- model helpers ---------------------------------------------------------------------------

    def stamp(self) -> int:
        return self.now_ms - self.now_ms % self.stamp_ms

    def _etag(self, data: bytes) -> str:
        self._n += 1
        return hashlib.md5(data).hexdigest() if self.etag_mode == "md5" else f"u{self._n}"

    def write(self, org: str, rec: str, path: str, data: bytes, *, age_ms: int = 0) -> None:
        stamp = self.stamp() - age_ms
        self.files[(org, rec, path)] = [data, self._etag(data), stamp - stamp % self.stamp_ms]

    async def _op(self, name: str) -> None:
        self.ops.append(name)
        if self.before_op is not None:
            await self.before_op(name)

    # -- the store the sweep and the bump queue use ---------------------------------------------------

    async def get(self, org: str, rec: str, path: str) -> bytes | None:
        await self._op("get")
        entry = self.files.get((org, rec, path))
        return None if entry is None else entry[0]

    async def get_versioned(self, org: str, rec: str, path: str) -> VersionedBytes | None:
        await self._op("get")
        entry = self.files.get((org, rec, path))
        return None if entry is None else VersionedBytes(entry[0], entry[1], entry[2])

    async def list_files(self, org: str, rec: str, prefix: str = "") -> list[ListedFile]:
        await self._op("list")
        return sorted(
            (ListedFile(p, len(v[0]), v[2], v[1]) for (o, r, p), v in self.files.items()
             if o == org and r == rec and p.startswith(prefix) and not p.startswith("refreshed/")),
            key=lambda f: f.path,
        )

    async def delete_files(
        self, org: str, rec: str, paths: Sequence[str], etags: Mapping[str, str] | None = None,
    ) -> list[str]:
        await self._op("delete")
        changed: list[str] = []
        for p in paths:
            entry = self.files.get((org, rec, p))
            if self.supports_conditional_delete and entry is not None and etags and p in etags and etags[p] != entry[1]:
                changed.append(p)
                continue
            self.files.pop((org, rec, p), None)
        return changed

    async def put(self, org: str, rec: str, path: str, data: bytes) -> bool:
        await self._op("put")
        if (org, rec, path) in self.files:
            return False
        self.write(org, rec, path, data)
        return True

    async def replace(self, org: str, rec: str, path: str, data: bytes) -> None:
        await self._op("replace")
        self.write(org, rec, path, data)

    overwrite = replace

    async def presign(self, org: str, rec: str, files: Sequence[Any]) -> dict[tuple[str, str], Any]:
        await self._op("presign")
        return {(f[0], f[1]): (org, rec, f[0], f[2] if len(f) > 2 else None) for f in files}

    async def bump_to(self, entry: Any, data: bytes) -> tuple[str, str | None]:  # noqa: ANN401 - a JSON value or opaque signed-URL entry of any shape
        await self._op("bump_put")
        org, rec, path, etag = entry
        current = self.files.get((org, rec, path))
        if current is None:
            return (CHANGED if self.gone_is_412 else GONE), None
        if current[1] != etag:
            return CHANGED, None
        if self.copy_in_place_bump:
            current[2] = self.stamp()                      # new write time, same bytes: the md5 ETag stays
            return BUMPED, current[1]
        self.write(org, rec, path, data)
        return BUMPED, self.files[(org, rec, path)][1]

    async def fenced(self, org: str, rec: str) -> bool:
        return (org, rec) in self.deleted_records

    async def refresh_marker_ms(self, org: str, rec: str) -> int | None:
        await self._op("marker")
        if self.unreadable_marker:
            raise ConnectionError("marker unreadable")
        entry = self.files.get((org, rec, "refreshed/marker.json"))
        return None if entry is None else entry[2]

    # -- a refresh, as the storage service does it ---------------------------------------------------

    def begin_refresh(self, org: str, rec: str) -> None:
        self.write(org, rec, "refreshed/marker.json", b'{"startedMs":1}')

    def cutoff(self) -> int:
        """The sentinel's write time: the store's clock at its own granularity."""
        return self.stamp()

    async def purge_before(self, org: str, rec: str, before_ms: int) -> None:
        await self._op("purge_list")
        listed = [f for f in await self.list_files(org, rec) if f.last_modified_ms < before_ms]
        await self._op("purge_delete")
        await self.delete_files(org, rec, [f.path for f in listed], {f.path: f.etag for f in listed if f.etag})

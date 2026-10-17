"""SMB1/CIFS data source. Sequential: one pysmb connection, one asyncio lock."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from aiolimiter import AsyncLimiter

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.connectors.sources.network_share.entry import DirectoryEntry, ShareInfo
    from app.sources.client.cifs.cifs import CifsClient


class CifsDataSource:
    def __init__(self, client: CifsClient, *, rate_limiter: AsyncLimiter | None = None) -> None:
        self._client = client
        self._rate_limiter = rate_limiter or AsyncLimiter(10, 1)
        self._lock = asyncio.Lock()

    async def list_directory(self, share: str, path: str) -> list[DirectoryEntry]:
        async with self._lock, self._rate_limiter:
            return await asyncio.to_thread(self._client.list_directory, share, path)

    async def read_file(
        self, share: str, path: str, chunk_size: int = 1024 * 1024
    ) -> AsyncIterator[bytes]:
        # One permit per file. Each read_chunk opens, reads, and closes the file,
        # so an 8 KiB chunk under AsyncLimiter(10, 1) caps a stream at ~80 KiB/s.
        await self._rate_limiter.acquire()
        offset = 0
        while True:
            async with self._lock:
                chunk = await asyncio.to_thread(
                    self._client.read_chunk, share, path, offset, chunk_size
                )
            if not chunk:
                break
            yield chunk
            offset += len(chunk)

    async def list_shares(self) -> list[ShareInfo]:
        async with self._lock, self._rate_limiter:
            return await asyncio.to_thread(self._client.list_shares)

    async def stat(
        self, share: str, path: str, *, follow: bool = True
    ) -> DirectoryEntry | None:
        # pysmb QUERY_PATH_INFORMATION follows reparse points. The find response
        # still carries FILE_ATTRIBUTE_REPARSE_POINT on the directory entry.
        if not follow:
            return await self._entry_in_parent(share, path)
        async with self._lock, self._rate_limiter:
            return await asyncio.to_thread(self._client.stat, share, path)

    async def _entry_in_parent(self, share: str, path: str) -> DirectoryEntry | None:
        normalized = path.replace("\\", "/").strip("/")
        parent, _, name = normalized.rpartition("/")
        if not name:
            return None
        try:
            entries = await self.list_directory(share, parent)
        except FileNotFoundError:
            return None
        folded = name.casefold()
        for entry in entries:
            if entry.name.casefold() == folded:
                return entry
        return None

    async def close(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._client.close)

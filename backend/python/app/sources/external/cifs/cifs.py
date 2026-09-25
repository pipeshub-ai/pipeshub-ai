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
        self, share: str, path: str, chunk_size: int = 8192
    ) -> AsyncIterator[bytes]:
        offset = 0
        while True:
            async with self._lock, self._rate_limiter:
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

    async def stat(self, share: str, path: str) -> DirectoryEntry | None:
        async with self._lock, self._rate_limiter:
            return await asyncio.to_thread(self._client.stat, share, path)

    async def close(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._client.close)

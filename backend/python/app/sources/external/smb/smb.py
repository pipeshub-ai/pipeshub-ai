"""SMB 2/3 data source. Blocking smbclient calls run in a worker thread."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from aiolimiter import AsyncLimiter

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.connectors.sources.network_share.entry import DirectoryEntry, ShareInfo
    from app.sources.client.smb.smb import SmbClient


class SmbDataSource:
    def __init__(self, client: SmbClient, *, rate_limiter: AsyncLimiter | None = None) -> None:
        self._client = client
        self._rate_limiter = rate_limiter or AsyncLimiter(10, 1)

    async def list_directory(self, share: str, path: str) -> list[DirectoryEntry]:
        async with self._rate_limiter:
            return await asyncio.to_thread(self._client.list_directory, share, path)

    async def read_file(
        self, share: str, path: str, chunk_size: int = 8192
    ) -> AsyncIterator[bytes]:
        async with self._rate_limiter:
            handle = await asyncio.to_thread(self._client.open_file, share, path)
        try:
            while True:
                chunk = await asyncio.to_thread(handle.read, chunk_size)
                if not chunk:
                    break
                yield chunk
        finally:
            await asyncio.to_thread(handle.close)

    async def list_shares(self) -> list[ShareInfo]:
        async with self._rate_limiter:
            return await asyncio.to_thread(self._client.list_shares)

    async def stat(self, share: str, path: str) -> DirectoryEntry | None:
        async with self._rate_limiter:
            return await asyncio.to_thread(self._client.stat, share, path)

    async def close(self) -> None:
        await asyncio.to_thread(self._client.close)

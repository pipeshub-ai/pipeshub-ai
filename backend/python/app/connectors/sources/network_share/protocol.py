"""Structural interface both SMB and CIFS data sources implement."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.connectors.sources.network_share.entry import DirectoryEntry, ShareInfo


class INetworkShareDataSource(Protocol):
    async def list_directory(self, share: str, path: str) -> list[DirectoryEntry]:
        """List one directory. Raises DirectoryListingError or FileNotFoundError."""

    async def read_file(
        self, share: str, path: str, chunk_size: int = 8192
    ) -> AsyncIterator[bytes]:
        """Yield file bytes. Raises OSError / ConnectionError on failure."""
        ...

    async def list_shares(self) -> list[ShareInfo]:
        """List disk/print/ipc shares. Raises ShareListingError on failure."""

    async def stat(self, share: str, path: str) -> DirectoryEntry | None:
        """Return metadata for one path, or None if it does not exist."""

    async def close(self) -> None:
        """Release the session."""

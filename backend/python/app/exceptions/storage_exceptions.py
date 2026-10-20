"""Errors from the storage service that callers outside the storage client act on."""

from __future__ import annotations

import aiohttp


class StorageRequestRejectedError(aiohttp.ClientError):
    """The storage service refused the request itself, so repeating it cannot help.

    Carries ``status`` so the message consumer's error classifier treats it as
    terminal and fails the record on the first attempt, and ``too_large`` so the
    record's reason can say the file is too large rather than "temporary problem".
    Still an ``aiohttp.ClientError`` for callers that catch that.
    """

    def __init__(self, message: str, status: int, *, too_large: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.too_large = too_large

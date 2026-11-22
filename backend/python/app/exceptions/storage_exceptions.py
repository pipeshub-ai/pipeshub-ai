"""Errors from the storage service that callers outside the storage client act on."""

from __future__ import annotations

import aiohttp


class StorageRequestRejectedError(aiohttp.ClientError):
    """The storage service refused the request itself, so repeating it cannot help.

    Carries ``status`` so the message consumer's error classifier treats it as
    terminal and fails the record on the first attempt, and ``too_large`` so the
    record's reason can say the file is too large rather than "temporary problem".
    ``detail`` is the storage service's own reason when its reply had one, for a
    caller that must tell one refusal from another. Still an ``aiohttp.ClientError``
    for callers that catch that.
    """

    def __init__(
        self, message: str, status: int, *, too_large: bool = False, detail: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.too_large = too_large
        self.detail = detail


class StoredRecordTooLargeError(Exception):
    """A stored record decodes to more than the reader will hold in memory.

    Raised from the zstd frame header, before anything is decompressed. Readers
    already skip a record whose fetch fails, so the record drops out of that one
    request instead of risking the pod.
    """


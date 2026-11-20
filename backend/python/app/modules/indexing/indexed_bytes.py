"""The hex md5 of the bytes an event is indexing, handed to the write that marks the record indexed.

A connector sync overwrites a record's ``md5Checksum`` with the source's value before the update event arrives, so that
field cannot say which bytes the index was built from. The event handler remembers the md5 of the bytes it is about to
index here, and the one write that completes a full index stores it as ``indexedMd5``. Nothing else writes it: a re-embed
from stored blocks, a reused twin or a failed run leave it as it was.
"""

from __future__ import annotations

from contextvars import ContextVar

_MD5: ContextVar[str | None] = ContextVar("indexed_bytes_md5", default=None)


def remember(md5: str | None) -> None:
    _MD5.set(md5 or None)


def current() -> str | None:
    return _MD5.get()


def clear() -> None:
    _MD5.set(None)

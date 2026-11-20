"""Whether the reindex being handled was a user's Force reindex.

A user's Force reindex re-parses and re-indexes: every stored paid result is bypassed and paid for again. The sync-events
handler that walks the records sets this for the length of the walk, and the reindex events it publishes carry
``refreshPaidOutputs`` while it is set. Reindexes the platform starts itself never set it (they reuse what is stored).
"""
from __future__ import annotations

import contextlib
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

_user_forced: ContextVar[bool] = ContextVar("user_forced_reindex", default=False)


def user_forced_reindex() -> bool:
    return _user_forced.get()


@contextlib.contextmanager
def forced_by_user(*, forced: bool) -> Iterator[None]:
    token = _user_forced.set(bool(forced))
    try:
        yield
    finally:
        _user_forced.reset(token)

"""Whether the reindex being handled was a user's Force reindex.

A user's Force reindex re-parses and re-indexes: every stored paid result is bypassed and paid for again. The sync-events
handler that walks the records sets this for the length of the walk, and the reindex events it publishes carry
``refreshPaidOutputs`` while it is set. Reindexes the platform starts itself never set it (they reuse what is stored).
"""
from __future__ import annotations

import contextlib
import uuid
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

_user_forced: ContextVar[bool] = ContextVar("user_forced_reindex", default=False)
_intent_id: ContextVar[str | None] = ContextVar("user_forced_reindex_intent_id", default=None)


def user_forced_reindex() -> bool:
    return _user_forced.get()


def user_forced_intent_id() -> str | None:
    """The id of the Force reindex being walked (one per request, shared by its records), or None outside one.

    It names the refresh across deliveries: the walk stamps it on each record event and, behind
    ``REFRESH_INTENT_WRITE``, on the record itself (see ``app.modules.indexing.refresh_intent``).
    """
    return _intent_id.get()


@contextlib.contextmanager
def forced_by_user(*, forced: bool) -> Iterator[None]:
    token = _user_forced.set(bool(forced))
    id_token = _intent_id.set(uuid.uuid4().hex if forced else None)
    try:
        yield
    finally:
        _intent_id.reset(id_token)
        _user_forced.reset(token)

"""Whether a record is being streamed to indexing or to a person.

The two can deserve different treatment from a source's rate limit. Indexing
fetches records in bulk, and a refusal costs it nothing: it puts the record back
in line until the source's ``Retry-After``. A person opening a file asked for
that one request. ``stream_record_internal`` (the indexing service's route) sets
this for the length of its call into the connector.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Generator

_for_indexing: ContextVar[bool] = ContextVar("stream_for_indexing", default=False)


@contextmanager
def streaming_for_indexing() -> Generator[None, None, None]:
    token = _for_indexing.set(True)
    try:
        yield
    finally:
        _for_indexing.reset(token)


def is_streaming_for_indexing() -> bool:
    return _for_indexing.get()

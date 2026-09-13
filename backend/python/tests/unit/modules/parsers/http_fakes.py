"""A response body as aiohttp serves it: streamed in chunks, with a declared length."""

from collections.abc import AsyncIterator
from unittest.mock import MagicMock

_UNSET = -1


class _Stream:
    def __init__(self, body: bytes) -> None:
        self._body = body
        self.chunks_read = 0

    async def iter_chunked(self, size: int) -> AsyncIterator[bytes]:
        for start in range(0, len(self._body), size):
            self.chunks_read += 1
            yield self._body[start : start + size]


def with_body(response: MagicMock, body: bytes, *, declared: int | None = _UNSET) -> MagicMock:
    """Serve *body* from *response*; *declared* is its Content-Length (None when absent)."""
    response.content = _Stream(body)
    response.content_length = len(body) if declared == _UNSET else declared
    return response

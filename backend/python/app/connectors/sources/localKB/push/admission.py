"""Who may hold a push body in memory, and for how long.

A push body is read only after the caller's role on the KB is known. At most
``READ_SLOTS`` bodies are held per process (``READ_SLOTS`` x 16 MiB), a caller
has one push in flight, and a body must arrive at a minimum rate, so a slow
sender cannot hold a slot for long. The cap is this module's own constant: a
header claiming a larger limit is ignored.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from .contract import PushRefusal

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Mapping

MAX_BODY_BYTES = 16 << 20
#: A schema is at most 256 properties of short names and labels.
MAX_SCHEMA_BYTES = 1 << 20
#: Time to read it; 1 MiB needs far less, so a sender that pauses is cut.
SCHEMA_READ_TIMEOUT_S = 10.0
READ_SLOTS = 4
SLOT_WAIT_S = 5.0
MIN_RATE_BPS = 64 * 1024
GRACE_S = 5.0
MAX_READ_S = 300.0


def read_deadline_s(
    content_length: int | None,
    grace_s: float = GRACE_S,
    min_rate_bps: float = MIN_RATE_BPS,
    max_read_s: float = MAX_READ_S,
) -> float:
    if content_length is None:
        return max_read_s
    return min(max_read_s, grace_s + content_length / min_rate_bps)


class Admission:
    """Read slots, one push in flight per user, and the body read's deadline and minimum rate."""

    def __init__(
        self,
        read_slots: int = READ_SLOTS,
        slot_wait_s: float = SLOT_WAIT_S,
        clock: Callable[[], float] = time.monotonic,
        grace_s: float = GRACE_S,
        min_rate_bps: float = MIN_RATE_BPS,
        max_read_s: float = MAX_READ_S,
    ) -> None:
        self._slots = asyncio.Semaphore(read_slots)
        self._in_flight: set[str] = set()
        self.slot_wait_s = slot_wait_s
        self._clock = clock
        self.grace_s = grace_s
        self.min_rate_bps = min_rate_bps
        self.max_read_s = max_read_s

    def claim(self, user_key: str) -> None:
        """Mark a push of this user in flight, or refuse with 429. Held until ``release``, i.e. for
        the whole push (body read, validation, write), so a caller has one push at a time per process."""
        if user_key in self._in_flight:
            raise PushRefusal(429, "PUSH_BUSY", "You already have a push in flight.", extra={"retryAfter": 1})
        self._in_flight.add(user_key)

    def release(self, user_key: str) -> None:
        self._in_flight.discard(user_key)

    async def read_body(self, headers: Mapping[str, str], stream: AsyncIterator[bytes]) -> bytes:
        """The request body, or a ``PushRefusal``: 415, 413, 503 or 408."""
        content_type = (headers.get("content-type") or "").split(";")[0].strip().lower()
        if content_type != "application/json":
            raise PushRefusal(415, "UNSUPPORTED_MEDIA_TYPE", "The body must be application/json.")
        declared: int | None = None
        raw_length = headers.get("content-length")
        if raw_length is not None:
            try:
                declared = int(raw_length)
            except ValueError:
                raise PushRefusal(400, "INVALID_CONTENT_LENGTH", "Content-Length is not a number.") from None
            if declared < 0:
                raise PushRefusal(400, "INVALID_CONTENT_LENGTH", "Content-Length is negative.")
            if declared > MAX_BODY_BYTES:
                raise _too_large()
        try:
            await asyncio.wait_for(self._slots.acquire(), self.slot_wait_s)
        except TimeoutError:
            raise PushRefusal(503, "PUSH_BUSY", "The server is busy with other pushes.", extra={"retryAfter": 2}) from None
        try:
            return await self._read(stream, declared)
        finally:
            self._slots.release()

    def _wait_limit(self, received: int, elapsed: float, declared: int | None) -> float:
        """Seconds the next chunk may take: until the deadline, or until the average rate would fall
        under the minimum, whichever comes first. A timer on the wait, not a check when data arrives,
        so a sender that stops is cut at that moment."""
        deadline = read_deadline_s(declared, self.grace_s, self.min_rate_bps, self.max_read_s)
        rate_limit = max(self.grace_s, received / self.min_rate_bps)
        return min(deadline, rate_limit) - elapsed

    async def _read(self, stream: AsyncIterator[bytes], declared: int | None) -> bytes:
        started = self._clock()
        buf = bytearray()
        chunks = aiter(stream)
        while True:
            remaining = self._wait_limit(len(buf), self._clock() - started, declared)
            if remaining <= 0:
                raise PushRefusal(408, "BODY_TIMEOUT", "The body arrived too slowly.")
            try:
                chunk = await asyncio.wait_for(anext(chunks), remaining)
            except StopAsyncIteration:
                return bytes(buf)
            except TimeoutError:
                raise PushRefusal(408, "BODY_TIMEOUT", "The body arrived too slowly.") from None
            buf += chunk
            if len(buf) > MAX_BODY_BYTES:
                raise _too_large()


def _too_large() -> PushRefusal:
    return PushRefusal(413, "PAYLOAD_TOO_LARGE", "The body is larger than 16 MiB.", extra={"limit": "body", "limitBytes": MAX_BODY_BYTES})


async def read_bounded(headers: Mapping[str, str], stream: AsyncIterator[bytes], limit: int, timeout_s: float | None = None) -> bytes:
    """A small body read chunk by chunk: 413 as soon as it passes ``limit``, 408 if it takes longer than ``timeout_s``."""
    declared = headers.get("content-length")
    if declared is not None and declared.isascii() and declared.isdigit() and int(declared) > limit:
        raise _too_large_for(limit)
    buf = bytearray()
    try:
        async with asyncio.timeout(timeout_s):
            async for chunk in stream:
                buf += chunk
                if len(buf) > limit:
                    raise _too_large_for(limit)
    except TimeoutError:
        raise PushRefusal(408, "BODY_TIMEOUT", "The body arrived too slowly.") from None
    return bytes(buf)


def _too_large_for(limit: int) -> PushRefusal:
    return PushRefusal(413, "PAYLOAD_TOO_LARGE", f"The body is larger than {limit >> 10} KiB.", extra={"limit": "body", "limitBytes": limit})

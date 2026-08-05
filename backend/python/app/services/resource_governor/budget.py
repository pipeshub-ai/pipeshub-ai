"""Growing byte-cost admission for buffered downloads (plan section 1.3,
phase 2).

Gating parse/index concurrency cannot bound resident memory by itself,
because the record handler downloads a file **into memory before** any
parse slot is requested — peak RSS scales with
``MAX_CONCURRENT_INDEXING x ~2 x filesize`` independent of parse
concurrency (plan section 1.3). A ``BytesBudget`` reserves an estimate of
that peak against the ``DOWNLOAD_BYTES`` pool before the body is buffered,
and is held by the caller for as long as the bytes stay resident — not just
for the duration of the HTTP request.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from app.services.resource_governor.gate import AdmissionGate

# Applies when Content-Length is absent (or the server understates it) —
# large enough to admit a typical small/medium document without waiting,
# small enough that an unknown-size stream can't silently claim the whole
# budget on its first reservation.
DEFAULT_RESERVE_BYTES = 16 * 1024 * 1024

# Content-Length -> reservation: the in-memory buffer plus the one
# unavoidable copy needed to hand back an immutable ``bytes`` object to the
# rest of the pipeline (parsers, MD5 hashing, multipart upload all expect
# ``bytes``, not ``bytearray``), plus slack for chunk-boundary overhead.
CONTENT_LENGTH_RESERVE_MULTIPLIER = 2.2


class BytesBudget(Protocol):
    """One in-flight download's reservation against ``Pool.DOWNLOAD_BYTES``.

    Scoped to a single download attempt by the caller — construct a new
    instance per attempt so a retry doesn't inherit a stale reservation.
    A no-op implementation (backed by no gate) is expected when no
    ``ResourceGovernor`` is configured, so callers can use this
    unconditionally instead of branching on "governor present or not".
    """

    async def reserve(self, content_length: int | None) -> None: ...

    async def ensure(self, total_bytes_so_far: int) -> None: ...

    def release(self) -> None: ...


def estimate_reservation(content_length: int | None) -> int:
    """Conservative resident-bytes estimate for a response of size
    ``content_length`` (``None``/non-positive when unknown)."""
    if content_length is None or content_length <= 0:
        return DEFAULT_RESERVE_BYTES
    return max(
        DEFAULT_RESERVE_BYTES, int(content_length * CONTENT_LENGTH_RESERVE_MULTIPLIER)
    )


class GatedBytesBudget:
    """``BytesBudget`` backed by an ``AdmissionGate`` for ``Pool.DOWNLOAD_BYTES``.

    ``gate`` is ``None`` when no ``ResourceGovernor`` is configured, in
    which case every method is a no-op — the byte budget is opt-in and
    changes nothing for deployments that haven't wired a governor.
    """

    def __init__(self, gate: "AdmissionGate | None") -> None:
        self._gate = gate
        self._reserved = 0

    async def reserve(self, content_length: int | None) -> None:
        if self._gate is None:
            return
        cost = estimate_reservation(content_length)
        await self._gate.acquire(cost)
        self._reserved += cost

    async def ensure(self, total_bytes_so_far: int) -> None:
        """Top up the reservation once actual bytes catch up to it — a
        response with no (or an understated) ``Content-Length`` must still
        not be able to grow past its budget unnoticed."""
        if self._gate is None:
            return
        if total_bytes_so_far <= self._reserved * 0.9:
            return
        target = estimate_reservation(total_bytes_so_far)
        additional = target - self._reserved
        if additional <= 0:
            return
        await self._gate.acquire(additional)
        self._reserved += additional

    def release(self) -> None:
        if self._gate is None or self._reserved == 0:
            return
        self._gate.release(self._reserved)
        self._reserved = 0

    @property
    def reserved_bytes(self) -> int:
        return self._reserved

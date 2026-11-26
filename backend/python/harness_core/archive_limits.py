"""Bounds on what an archive may expand to (SEC-FS-03, C6.40): one meter for every place the harness unpacks.

A header's declared size is a claim; the meter counts the bytes actually produced. Three limits hold at once:
the bytes produced (``max_bytes``, each call site's own cap), the ratio of produced to consumed bytes past a
floor (a zip or hard-link bomb is a small input that produces a large output), and the number of members.
"""

from __future__ import annotations

import os
import zlib
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Final

from harness_core.refusals import HarnessRefusal

if TYPE_CHECKING:
    from collections.abc import Mapping

ENV_MAX_RATIO: Final = "HARNESS_ARCHIVE_MAX_RATIO"
ENV_RATIO_FLOOR_BYTES: Final = "HARNESS_ARCHIVE_RATIO_FLOOR_BYTES"
ENV_MAX_MEMBERS: Final = "HARNESS_ARCHIVE_MAX_MEMBERS"
DEFAULT_MAX_RATIO: Final = 100.0
# Below this many produced bytes the ratio is not checked: a small archive of zeros is no threat, and a
# snapshot the capture cap admitted (64 MiB) is never refused on restore for compressing well.
DEFAULT_RATIO_FLOOR_BYTES: Final = 64 * 1024**2
DEFAULT_MAX_MEMBERS: Final = 50_000
_CHUNK: Final = 64 * 1024

EXPANDED_TOO_LARGE: Final = "expanded_too_large"
RATIO_TOO_HIGH: Final = "ratio_too_high"
TOO_MANY_MEMBERS: Final = "too_many_members"


class ExpansionRefused(HarnessRefusal):
    """An archive would expand past a limit; ``reason_code`` says which."""

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason_code = reason


class InvalidArchiveLimit(ValueError):
    """A ``HARNESS_ARCHIVE_*`` setting is not a positive number: the process refuses to start rather than guess."""


@dataclass(frozen=True, slots=True)
class ExpansionLimits:
    max_bytes: int
    max_ratio: float = DEFAULT_MAX_RATIO
    ratio_floor_bytes: int = DEFAULT_RATIO_FLOOR_BYTES
    max_members: int = DEFAULT_MAX_MEMBERS

    def __post_init__(self) -> None:
        if self.max_bytes <= 0 or self.max_ratio <= 0 or self.ratio_floor_bytes < 0 or self.max_members <= 0:
            raise ValueError("expansion limits must be positive")

    def capped(self, max_bytes: int) -> ExpansionLimits:
        return replace(self, max_bytes=max_bytes)

    @classmethod
    def from_env(cls, max_bytes: int, env: Mapping[str, str] | None = None) -> ExpansionLimits:
        values = os.environ if env is None else env
        return cls(
            max_bytes=max_bytes,
            max_ratio=_env_float(ENV_MAX_RATIO, DEFAULT_MAX_RATIO, values),
            ratio_floor_bytes=_env_int(ENV_RATIO_FLOOR_BYTES, DEFAULT_RATIO_FLOOR_BYTES, values),
            max_members=_env_int(ENV_MAX_MEMBERS, DEFAULT_MAX_MEMBERS, values),
        )


def _env_float(name: str, default: float, env: Mapping[str, str]) -> float:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        value = 0.0
    if not 0 < value < float("inf"):
        raise InvalidArchiveLimit(f"{name} must be a positive number, got {raw!r}")
    return value


def _env_int(name: str, default: int, env: Mapping[str, str]) -> int:
    value = _env_float(name, default, env)
    if value != int(value):
        raise InvalidArchiveLimit(f"{name} must be a positive whole number, got {value!r}")
    return int(value)


class ExpansionMeter:
    """Counts what one archive consumed and produced; raises ``ExpansionRefused`` the moment a limit is crossed."""

    def __init__(self, limits: ExpansionLimits, *, consumed: int = 0) -> None:
        self.limits = limits
        self.consumed = consumed
        self.produced = 0
        self.members = 0

    def allowance(self) -> int:
        """How many bytes may be produced in all, given what was consumed so far."""
        by_ratio = max(self.limits.ratio_floor_bytes, int(self.limits.max_ratio * self.consumed))
        return min(self.limits.max_bytes, by_ratio)

    def fits(self, size: int) -> bool:
        return self.produced + size <= self.allowance()

    def consume(self, size: int) -> None:
        self.consumed += size

    def produce(self, size: int) -> None:
        self.produced += size
        if self.produced > self.limits.max_bytes:
            raise ExpansionRefused(
                f"the archive expands past {self.limits.max_bytes} bytes", reason=EXPANDED_TOO_LARGE
            )
        if self.produced > self.allowance():
            raise ExpansionRefused(
                f"the archive expands more than {self.limits.max_ratio:g} times its size", reason=RATIO_TOO_HIGH
            )

    def member(self) -> None:
        self.members += 1
        if self.members > self.limits.max_members:
            raise ExpansionRefused(
                f"the archive has over {self.limits.max_members} members", reason=TOO_MANY_MEMBERS
            )


class BoundedInflater:
    """A zlib stream inflated in bounded steps, every produced byte counted by ``meter`` before the next step.

    ``most``: what this one stream may produce (a member's declared size), on top of the meter's limits; several
    inflaters may share one meter (the objects of one pack)."""

    def __init__(self, meter: ExpansionMeter, *, wbits: int = zlib.MAX_WBITS, most: int | None = None) -> None:
        self.meter = meter
        self.produced = 0
        self._most = most
        self._inflater = zlib.decompressobj(wbits)
        self._after = b""

    @property
    def eof(self) -> bool:
        return self._inflater.eof

    @property
    def unused_data(self) -> bytes:
        """What followed the end of the stream in the bytes fed."""
        return self._inflater.unused_data + self._after

    def feed(self, data: bytes | memoryview) -> bytes:
        """Inflates ``data``; raises ``zlib.error`` on a corrupt stream and ``ExpansionRefused`` past a limit."""
        out: list[bytes] = []
        pending: bytes | memoryview = data
        while pending and not self._inflater.eof:
            step = pending[:_CHUNK]
            pending = pending[_CHUNK:]
            self.meter.consume(len(step))
            while True:
                # One past the allowance, so crossing it is seen without inflating the rest of a bomb.
                room = min(_CHUNK, self.meter.allowance() - self.meter.produced + 1)
                if self._most is not None:
                    room = min(room, self._most - self.produced + 1)
                room = max(1, room)
                produced = self._inflater.decompress(step, room)
                self.produced += len(produced)
                if self._most is not None and self.produced > self._most:
                    raise ExpansionRefused(
                        f"a member expands past the {self._most} bytes it declares", reason=EXPANDED_TOO_LARGE
                    )
                self.meter.produce(len(produced))
                out.append(produced)
                step = self._inflater.unconsumed_tail
                # A full step may leave output inside zlib with no input left: ask again until it runs dry.
                if self._inflater.eof or (not step and len(produced) < room):
                    break
        if self._inflater.eof:
            self._after += bytes(pending)
        return b"".join(out)


def inflate(data: bytes, limits: ExpansionLimits, *, wbits: int = zlib.MAX_WBITS) -> tuple[bytes, BoundedInflater]:
    """``data`` inflated whole under ``limits``; the inflater says whether the stream ended and what followed."""
    inflater = BoundedInflater(ExpansionMeter(limits), wbits=wbits)
    return inflater.feed(data), inflater


__all__ = [
    "DEFAULT_MAX_MEMBERS",
    "DEFAULT_MAX_RATIO",
    "DEFAULT_RATIO_FLOOR_BYTES",
    "ENV_MAX_MEMBERS",
    "ENV_MAX_RATIO",
    "ENV_RATIO_FLOOR_BYTES",
    "EXPANDED_TOO_LARGE",
    "RATIO_TOO_HIGH",
    "TOO_MANY_MEMBERS",
    "BoundedInflater",
    "ExpansionLimits",
    "ExpansionMeter",
    "ExpansionRefused",
    "InvalidArchiveLimit",
    "inflate",
]

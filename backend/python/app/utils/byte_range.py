"""The ``Range`` header of the record stream route: one strict range of bounded size, or the whole body.

Only ``bytes=a-b`` and ``bytes=a-`` are honoured, and only when the span is at most ``MAX_RANGE_SPAN``.
A suffix range, a multi-range, a malformed header or a larger span is not an error: RFC 9110 section 14.2
lets a server ignore a ``Range`` it does not support, and it then answers 200 with the whole body.
Only a range that starts at or after the end of the object is refused (416).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

#: The largest span the stream route answers as a partial response (8 MiB).
MAX_RANGE_SPAN = 8 * 1024 * 1024

_STRICT_RANGE = re.compile(r"bytes=(\d{1,15})-(\d{0,15})")


@dataclass(frozen=True)
class ByteRange:
    """An inclusive byte range, as in ``Content-Range: bytes start-end/total``."""

    start: int
    end: int

    @property
    def length(self) -> int:
        return self.end - self.start + 1


Unsatisfiable = Literal["unsatisfiable"]


def resolve_range(header: str | None, total: int) -> ByteRange | Unsatisfiable | None:
    """The range to serve out of ``total`` bytes; None for the whole body; ``"unsatisfiable"`` for a 416."""
    if not header or total < 0:
        return None
    match = _STRICT_RANGE.fullmatch(header.strip())
    if match is None:
        return None
    start = int(match.group(1))
    last = int(match.group(2)) if match.group(2) else None
    if last is not None and last < start:
        return None
    if start >= total:
        return "unsatisfiable"
    end = total - 1 if last is None else min(last, total - 1)
    resolved = ByteRange(start, end)
    return resolved if resolved.length <= MAX_RANGE_SPAN else None

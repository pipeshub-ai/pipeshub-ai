"""Whether a parse worker's stop is a verdict on the file."""
from __future__ import annotations

from app.modules.parsers.parse_pool import ParseWorkerError


def worker_gave_up_on(exc: BaseException | None) -> bool:
    """Whether a parse worker ran out of time or memory on this file, anywhere in *exc*'s causes.

    A verdict on what the file costs to read: the record fails with that reason
    instead of being read again, which would cost the same.
    """
    while exc is not None:
        if isinstance(exc, ParseWorkerError) and exc.cause in ("timed_out", "crashed"):
            return True
        exc = exc.__cause__
    return False

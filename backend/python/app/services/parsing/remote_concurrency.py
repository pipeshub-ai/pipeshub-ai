"""How many parses one indexing process may have in flight against the remote parsing service.

With ``USE_PARSING_SERVICE=true`` the parse runs in another pod, so the indexing pod's own CPUs
say nothing about how many it can drive. ``PARSING_SERVICE_CONCURRENCY`` sets that number; unset
(the default) keeps the CPU-derived governor slots. It sizes the parse admission, the cluster
parse lease, the index permits that hold a parse (``lift_ceilings``) and the client's connection
pool. Local memory and download gates are untouched.
"""
from __future__ import annotations

import asyncio
import math
import os

from app.services.resource_governor.models import Ceilings
from app.services.resource_governor.policy import INDEX_HEADROOM

PARSING_SERVICE_CONCURRENCY_ENV = "PARSING_SERVICE_CONCURRENCY"


def remote_parse_concurrency() -> int | None:
    """The configured remote parse concurrency, or ``None`` when parsing is local or it is unset."""
    if os.getenv("USE_PARSING_SERVICE", "false").lower() != "true":
        return None
    try:
        value = int((os.getenv(PARSING_SERVICE_CONCURRENCY_ENV) or "").strip())
    except ValueError:
        return None
    return value if value > 0 else None


def lift_ceilings(ceilings: Ceilings, concurrency: int) -> Ceilings:
    """*ceilings* raised (never lowered) to hold *concurrency* remote parses and the records around them."""
    index = math.ceil(concurrency * INDEX_HEADROOM)
    return Ceilings(
        heavy=max(ceilings.heavy, concurrency),
        light=max(ceilings.light, concurrency),
        index_heavy=max(ceilings.index_heavy, index),
        index_light=max(ceilings.index_light, index),
    )


class RemoteParseSlots:
    """The process's admission for remote parses, one permit per call in flight."""

    def __init__(self, size: int) -> None:
        self.size = size
        self._sem = asyncio.Semaphore(size)

    async def acquire(self, timeout: float | None) -> bool:
        try:
            await asyncio.wait_for(self._sem.acquire(), timeout=timeout)
        except TimeoutError:
            return False
        return True

    def release(self) -> None:
        self._sem.release()


_slots: RemoteParseSlots | None = None


def remote_parse_slots(size: int) -> RemoteParseSlots:
    """The process-wide slots, rebuilt when the configured size changes."""
    global _slots  # noqa: PLW0603
    if _slots is None or _slots.size != size:
        _slots = RemoteParseSlots(size)
    return _slots


def reset_remote_parse_slots() -> None:
    global _slots  # noqa: PLW0603
    _slots = None

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
from app.services.resource_governor.policy import (
    INDEX_HEADROOM,
    INDEX_HEAVY_MAX_SHARE,
    INDEX_MIN_PER_TIER,
)

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


def lift_ceilings(ceilings: Ceilings, concurrency: int, env_index: int | None = None) -> Ceilings:
    """*ceilings* raised (never lowered) to hold *concurrency* remote parses and the records around them.

    An explicit ``MAX_CONCURRENT_INDEXING`` (*env_index*) is the operator's total in-flight budget and
    still holds after the raise, split between the tiers as ``resolve_ceilings`` splits it; a parse
    ceiling is never above its index tier. The cluster parse lease is not taken from these ceilings:
    ``parse_ceiling`` sizes it to *concurrency* itself.
    """
    index = math.ceil(concurrency * INDEX_HEADROOM)
    index_heavy = max(ceilings.index_heavy, index)
    index_light = max(ceilings.index_light, index)
    heavy = max(ceilings.heavy, concurrency)
    light = max(ceilings.light, concurrency)
    if env_index is not None:
        allowed = max(1, env_index)
        if index_heavy + index_light > allowed:
            light_reserve = min(INDEX_MIN_PER_TIER, index_light, allowed // 2)
            heavy_cap = max(1, int(allowed * INDEX_HEAVY_MAX_SHARE))
            index_heavy = min(index_heavy, allowed - light_reserve, heavy_cap)
            index_light = min(index_light, allowed - index_heavy)
        heavy = max(1, min(heavy, index_heavy))
        light = max(1, min(light, index_light)) if index_light else 1
    return Ceilings(heavy=heavy, light=light, index_heavy=index_heavy, index_light=index_light)


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

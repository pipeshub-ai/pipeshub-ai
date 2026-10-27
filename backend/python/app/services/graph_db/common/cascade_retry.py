"""Retry of a delete cascade step that lost to a concurrent writer.

A push to a knowledge base that is being deleted writes the same records the cascade is
removing. The store aborts one of the two (an Arango write-write conflict, a Neo4j deadlock);
the cascade is idempotent, so it runs the step again instead of leaving the KB half deleted.
"""

from __future__ import annotations

import asyncio
import random

CASCADE_ATTEMPTS = 5
_BASE_SECONDS = 0.05


async def backoff(attempt: int) -> None:
    """Sleep 50 ms * 2^attempt with jitter before attempt ``attempt + 1``."""
    await asyncio.sleep(_BASE_SECONDS * (2 ** attempt) * (0.5 + random.random()))

"""Deterministic ids for run, invocation and event identifiers in tests."""

from __future__ import annotations

import random
import uuid
from collections import defaultdict


class SeededIds:
    """Implements :class:`harness_core.ids.IdSource` deterministically.

    ``new_id`` counts per prefix (``run_000001``) so ids stay readable in
    assertions; ``uuid4`` draws from a seeded RNG for code that needs UUIDs.
    """

    def __init__(self, seed: int = 0) -> None:
        self._rng = random.Random(seed)
        self._counters: defaultdict[str, int] = defaultdict(int)

    def new_id(self, prefix: str) -> str:
        self._counters[prefix] += 1
        return f"{prefix}_{self._counters[prefix]:06d}"

    def uuid4(self) -> uuid.UUID:
        return uuid.UUID(int=self._rng.getrandbits(128), version=4)


__all__ = ["SeededIds"]

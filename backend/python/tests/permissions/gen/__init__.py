"""T3: generated worlds. Fixed seeds make a run repeatable; a disagreement is shrunk and filed as a regression."""

from __future__ import annotations

import os
import random
from typing import TYPE_CHECKING

from .adversarial import adversarial_world
from .proptest import random_world

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..model import World

GENERATORS: dict[str, Callable[[random.Random], World]] = {
    "proptest": random_world,
    "adversarial": adversarial_world,
}


def world_count() -> int:
    """Worlds per generator: 50 on a pull request, PERM_GEN_WORLDS (e.g. 600) in a nightly run."""
    return int(os.environ.get("PERM_GEN_WORLDS", "50"))


def seeds() -> list[int]:
    """PERM_GEN_SEED picks one seed; otherwise the pull-request seed (nightly runs loop over 7, 11 and 2026)."""
    chosen = os.environ.get("PERM_GEN_SEED")
    return [int(chosen)] if chosen else [7]


def generate(kind: str, seed: int, count: int) -> list[World]:
    rng = random.Random(seed)
    return [GENERATORS[kind](rng) for _ in range(count)]

"""The deltas table: where the product oracle knowingly differs from the vendor.

A scenario lists the vendor's verdicts (the source oracle). Every listed pair
the product oracle does not give must be named in `scenarios/deltas.yaml` with
its direction, and every table entry must still be observed. Anything else is
either a wrong scenario or a regression.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import yaml

from .runner import Miss, scenario_misses
from .scenario import SCENARIOS_ROOT, Scenario

if TYPE_CHECKING:
    from pathlib import Path

DIRECTIONS = ("over-share", "under-share")


@dataclass(frozen=True)
class Delta:
    id: str
    direction: str
    scenarios: tuple[str, ...]
    pairs: tuple[str, ...]  # "<scenario>/<viewer>/<node>"
    reason: str
    fix: str


def load_deltas(path: Path = SCENARIOS_ROOT / "deltas.yaml") -> list[Delta]:
    rows = yaml.safe_load(path.read_text())["deltas"]
    out = [Delta(r["id"], r["direction"], tuple(r["scenarios"]), tuple(r["pairs"]), r["reason"], r["fix"]) for r in rows]
    for d in out:
        if d.direction not in DIRECTIONS:
            raise ValueError(f"{d.id}: direction must be one of {DIRECTIONS}")
    return out


def pair_key(miss: Miss) -> str:
    return f"{miss.scenario}/{miss.viewer}/{miss.node}"


def check(scenarios: list[Scenario], deltas: list[Delta]) -> list[str]:
    """Problems: unexplained misses, wrong directions, scenarios not pointing at their delta, stale entries."""
    problems: list[str] = []
    by_pair = {p: d for d in deltas for p in d.pairs}
    seen: set[str] = set()
    for s in scenarios:
        for miss in scenario_misses(s):
            key = pair_key(miss)
            seen.add(key)
            delta = by_pair.get(key)
            if delta is None:
                problems.append(f"unexplained difference {key} ({miss.direction}, {miss.reason})")
            elif delta.direction != miss.direction:
                problems.append(f"{key}: delta {delta.id} says {delta.direction}, the oracle gives {miss.direction}")
            elif s.expect.source_delta != delta.id:
                problems.append(f"{s.id}: expect.source_delta must be {delta.id}")
    for d in deltas:
        problems += [f"stale delta {d.id}: {p} is no longer a difference" for p in d.pairs if p not in seen]
    return problems

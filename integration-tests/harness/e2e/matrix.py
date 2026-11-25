"""The rows of the e2e covering arrays and what the oracle expects of each (04 §4.3).

The rows are the committed arrays (``docs/test-plan/data/e2e-{pairwise,threeway-core}.json``) and the expectation is
``oracle()`` of ``scripts/e2e_covering_array.py`` itself, imported, so the generated matrix and this runner cannot
disagree. Standard library only.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

REPO = Path(__file__).resolve().parents[3]
DATA = REPO / "docs/test-plan/data"
sys.path.insert(0, str(REPO / "scripts"))

import e2e_covering_array as covering  # noqa: E402

ARRAYS = {"pairwise": "e2e-pairwise.json", "threeway-core": "e2e-threeway-core.json"}
STACKS: Mapping[str, Mapping[str, str]] = {sid: dict(zip(covering.STACK_FACTORS, t, strict=True)) for sid, t in covering.STACKS.items()}
SINKS = frozenset(covering.SINKS)


@dataclass(frozen=True)
class Expected:
    route: str
    reason: str
    executor: str
    card: bool
    outcome: str
    effects: int
    child: str

    @property
    def durable(self) -> bool:
        return self.route == "durable"


@dataclass(frozen=True)
class Row:
    id: str
    array: str
    stack: str
    factors: Mapping[str, str]

    def __getitem__(self, factor: str) -> str:
        return self.factors[factor]

    @property
    def expected(self) -> Expected:
        return Expected(**covering.oracle(dict(self.factors)))

    @property
    def test_id(self) -> str:
        """Stack first, so ``-k ST-3`` selects one stack's rows."""
        return f"{self.stack}-{self.id}"


def rows(array: str) -> list[Row]:
    data = json.loads((DATA / ARRAYS[array]).read_text())
    out = []
    for raw in data["rows"]:
        factors = {k: v for k, v in raw.items() if k not in ("id", "stack")}
        if covering.stack_of(factors) != raw["stack"]:
            raise ValueError(f"{raw['id']} names {raw['stack']}, its store factors are {covering.stack_of(factors)}")
        out.append(Row(id=raw["id"], array=array, stack=raw["stack"], factors=factors))
    return out


def all_rows() -> list[Row]:
    return [row for array in ARRAYS for row in rows(array)]


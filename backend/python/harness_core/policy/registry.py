"""The dimension registry: ``policy-dimensions.json`` (generated as ``POLICY_DIMENSIONS``) read once and checked,
with each row's order built. A row's index is its refusal order. Node's ``domain/policy/registry.ts`` is the twin."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING, Final, cast

from harness_core.contracts import dump_contract
from harness_core.policy.orders import Order, order_of

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pydantic import JsonValue


class InvalidDimensionTable(ValueError):
    """A dimension table that breaks a rule; the message names the row."""


@dataclass(frozen=True, slots=True)
class Dimension:
    path: str
    """Where the value sits in the release layer and in the effective config."""
    layer_path: str
    """Where a layer below the release carries it."""
    kind: str
    order: Order | None
    within: str | None
    widening: str
    exposure: frozenset[str]


def _check(ok: bool, path: str, message: str) -> None:  # noqa: FBT001 - the condition checked
    if not ok:
        raise InvalidDimensionTable(f"{path}: {message}")


def _checked(rows: Sequence[Mapping[str, JsonValue]]) -> None:
    paths: set[str] = set()
    ceilings = {cast("str", row["path"]) for row in rows if row["kind"] == "ceiling"}
    for row in rows:
        path = cast("str", row["path"])
        exposure = cast("list[str]", row["exposure"])
        order = cast("Mapping[str, JsonValue] | None", row.get("order"))
        _check(path not in paths, path, "appears twice")
        paths.add(path)
        _check(("order" in row) == (row["kind"] == "ceiling"), path, "a ceiling and only a ceiling has an order")
        _check(("within" in row) == (row["kind"] == "choice"), path, "a choice and only a choice is within a ceiling")
        if "within" in row:
            _check(row["within"] in ceilings, path, f"{row['within']} is not a ceiling")
        if row["kind"] == "operator":
            _check(exposure == ["deployment"], path, "an operator row is set by the deployment only")
        _check("release" not in exposure, path, "the release is not a layer that narrows")
        _check(row.get("widening", "refuse") != "meet" or row["kind"] == "ceiling", path, "only a ceiling meets")
        ranks = cast("list[list[str]]", (order or {}).get("ranks", []))
        ranked = [value for group in ranks for value in group]
        _check(len(set(ranked)) == len(ranked), path, "a ranked value appears twice")
        _check(
            ((order or {}).get("kind") in ("rank", "rank-raise", "rank-raise-rules")) == bool(ranks),
            path,
            "a rank order and only a rank order lists ranks",
        )


def _relative(path: str, prefix: str) -> str:
    return path if not prefix else path[len(prefix) + 1 :]


class DimensionRegistry:
    def __init__(self, dimensions: Sequence[Dimension]) -> None:
        self._dimensions = tuple(dimensions)
        self._by_path = {d.path: d for d in self._dimensions}

    @classmethod
    def from_table(cls, table: Mapping[str, JsonValue], prefix: str = "") -> DimensionRegistry:
        """Every row of ``table`` (its wire form), checked; refuses a table that breaks a rule, naming the row.
        With ``prefix``, only the rows under it, their paths relative to it."""
        rows = cast("list[Mapping[str, JsonValue]]", table["dimensions"])
        _checked(rows)
        under = [row for row in rows if not prefix or cast("str", row["path"]).startswith(f"{prefix}.")]
        return cls(
            [
                Dimension(
                    path=_relative(cast("str", row["path"]), prefix),
                    layer_path=_relative(cast("str", row.get("narrowedBy", row["path"])), prefix),
                    kind=cast("str", row["kind"]),
                    order=None if "order" not in row else order_of(cast("Mapping[str, JsonValue]", row["order"])),
                    within=None if "within" not in row else _relative(cast("str", row["within"]), prefix),
                    widening=cast("str", row.get("widening", "refuse")),
                    exposure=frozenset(cast("list[str]", row["exposure"])),
                )
                for row in under
            ]
        )

    def all(self) -> tuple[Dimension, ...]:
        return self._dimensions

    def find(self, path: str) -> Dimension | None:
        return self._by_path.get(path)

    def get(self, path: str) -> Dimension:
        if path not in self._by_path:
            raise InvalidDimensionTable(f"{path}: no such dimension")
        return self._by_path[path]


def policy_table() -> dict[str, JsonValue]:
    """The release's dimension table in its wire form."""
    from harness_core.contracts.generated.policy_data import POLICY_DIMENSIONS

    return cast("dict[str, JsonValue]", dump_contract(POLICY_DIMENSIONS))


@cache
def policy_registry(prefix: str = "") -> DimensionRegistry:
    """The release's dimension table (rows under ``prefix``, relative to it)."""
    return DimensionRegistry.from_table(policy_table(), prefix)


ENGINE_PROFILE: Final = "engineProfile"

__all__ = ["ENGINE_PROFILE", "Dimension", "DimensionRegistry", "InvalidDimensionTable", "policy_registry", "policy_table"]

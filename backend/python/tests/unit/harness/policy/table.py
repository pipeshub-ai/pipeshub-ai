"""QA-10: ``policy_table.yaml`` read into scenarios (TGW-17)."""

from __future__ import annotations

import dataclasses
import re
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, cast, get_args

import yaml

from harness_core.engines.types import ProfileApprovals
from tests.unit.harness.policy.paths import (
    UNREADABLE,
    Address,
    Context,
    Grant,
    PathName,
    Scenario,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

TABLE: Final = Path(__file__).with_name("policy_table.yaml")
INPUTS: Final = ("tool", "context", "document", "deployment", "profile", "grant", "address")
VERDICT: Final = re.compile(r"^(allow|ask/[a-z_]+|deny/[a-z_]+)$")
MANDATORY: Final = frozenset(
    {
        "deny-over-ask-over-allow",
        "unclassified-is-external-write",
        "fail-closed",
        "grant-never-past-org-rule",
        "deny-absorbing",
    }
)


class _Loader(yaml.SafeLoader):
    """``!unreadable`` is an approvals document the configuration store fails to read."""


_Loader.add_constructor("!unreadable", lambda _loader, _node: UNREADABLE)


@dataclasses.dataclass(frozen=True)
class Row:
    id: str
    path: PathName
    scenario: Scenario
    expect: str
    xfail: str | None = None
    mandatory: str | None = None


def _pick(presets: Mapping[str, Any], name: str, row_id: str) -> Any:  # noqa: ANN401
    if name not in presets:
        raise ValueError(f"{row_id}: unknown preset {name!r}")
    return presets[name]


def _row(raw: Mapping[str, Any], presets: Mapping[str, Mapping[str, Any]]) -> Row:
    row_id = str(raw["id"])
    unknown = set(raw) - {"id", "path", "expect", "xfail", "mandatory", *INPUTS}
    if unknown:
        raise ValueError(f"{row_id}: unknown columns {sorted(unknown)}")
    if raw["path"] not in get_args(PathName):
        raise ValueError(f"{row_id}: unknown path {raw['path']!r}")
    context, grant, address = raw.get("context", "public"), raw.get("grant", "none"), raw.get("address", "made-up")
    if context not in get_args(Context) or grant not in get_args(Grant) or address not in get_args(Address):
        raise ValueError(f"{row_id}: unknown context, grant or address")
    if not VERDICT.match(str(raw["expect"])):
        raise ValueError(f"{row_id}: unreadable expectation {raw['expect']!r}")
    mandatory = raw.get("mandatory")
    if mandatory is not None and mandatory not in MANDATORY:
        raise ValueError(f"{row_id}: unknown mandatory class {mandatory!r}")
    scenario = Scenario(
        tool=str(raw.get("tool", "write/external")),
        context=cast("Context", context),
        document=_pick(presets["document"], raw.get("document", "none"), row_id),
        deployment=dict(_pick(presets["deployment"], raw.get("deployment", "default"), row_id)),
        profile=ProfileApprovals(**_pick(presets["profile"], raw.get("profile", "release"), row_id)),
        grant=cast("Grant", grant),
        address=cast("Address", address),
    )
    return Row(row_id, raw["path"], scenario, str(raw["expect"]), raw.get("xfail"), mandatory)


@cache
def rows() -> tuple[Row, ...]:
    document = yaml.load(TABLE.read_text(encoding="utf-8"), Loader=_Loader)
    return tuple(_row(raw, document["presets"]) for raw in document["rows"])


@cache
def as_written() -> Mapping[str, Any]:
    """The table as plain strings, defaults left out: what an author wrote, for checks of its shape."""
    return cast("Mapping[str, Any]", yaml.load(TABLE.read_text(encoding="utf-8"), Loader=yaml.BaseLoader))


__all__ = ["INPUTS", "MANDATORY", "TABLE", "Row", "as_written", "rows"]

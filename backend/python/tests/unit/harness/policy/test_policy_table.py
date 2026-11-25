"""TGW-17 (QA-10): every row of ``policy_table.yaml`` decides as the table says, on its path's production composition.

The table is the specification of approvals and the flow guard across the durable loop, today's loop and engine runs;
``paths.py`` builds each path's policy as production does. A row marked ``xfail`` states behaviour another WP or an
open finding brings; it is strict, so the row fails the day that behaviour lands until the marker is removed.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

import pytest

from tests.unit.harness.policy.paths import PATHS, UNCLASSIFIED, WORLD_PREFIX
from tests.unit.harness.policy.table import INPUTS, MANDATORY, as_written, rows

if TYPE_CHECKING:
    from tests.unit.harness.policy.table import Row

ROWS = rows()
EFFECT_SINKS = {
    f"{kind}/{egress}"
    for kind in ("write", "execute")
    for egress in ("none", "self", "internal_audience", "external")
} | {"read/none", "read/self", "read", "read/external", "workspace/none", UNCLASSIFIED}


def _param(row: Row) -> object:
    marks = [pytest.mark.xfail(strict=True, reason=row.xfail)] if row.xfail else []
    return pytest.param(row, id=row.id, marks=marks)


@pytest.mark.parametrize("row", [_param(row) for row in ROWS])
async def test_tgw17_every_row_decides_as_the_table_says(row: Row) -> None:
    assert await PATHS[row.path].decide(row.scenario) == row.expect


def test_tgw17_the_table_is_about_two_hundred_rows_or_more_with_unique_ids() -> None:
    ids = [row.id for row in ROWS]
    assert len(ids) >= 200
    assert [i for i, n in Counter(ids).items() if n > 1] == []


def test_tgw17_no_two_rows_decide_the_same_inputs() -> None:
    keys = Counter((r["path"], *(r.get(name, "") for name in INPUTS)) for r in as_written()["rows"])
    assert [key for key, n in keys.items() if n > 1] == []


def test_tgw17_every_mandatory_row_class_is_present() -> None:
    assert {row.mandatory for row in ROWS if row.mandatory} == MANDATORY


@pytest.mark.parametrize("path", sorted(PATHS))
def test_tgw17_every_effect_class_and_sink_is_decided_on_every_path(path: str) -> None:
    for context in ("public", "private", "unknown"):
        tools = {row.scenario.tool for row in ROWS if row.path == path and row.scenario.context == context}
        assert tools >= EFFECT_SINKS, (path, context)


def test_tgw17_every_preset_and_grant_is_used() -> None:
    for column, names in as_written()["presets"].items():
        used = {r.get(column, next(iter(names))) for r in as_written()["rows"]}
        assert used == set(names), column
    assert {row.scenario.grant for row in ROWS if row.path == "engine"} == {
        row.scenario.grant for row in ROWS if row.path == "durable"
    }
    assert any(row.scenario.tool.startswith(WORLD_PREFIX) for row in ROWS)


def test_tgw17_each_xfail_names_the_wp_or_finding_that_changes_it() -> None:
    reasons = {row.xfail.split(" ", 1)[0].rstrip(":") for row in ROWS if row.xfail}
    assert reasons <= {"UX4", "QA10-F1", "QA10-F2"}

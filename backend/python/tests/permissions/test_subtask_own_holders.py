"""A sub-task's own Browse holders (its assignee, a user field) against the read rule.

Jira shows a sub-task to the project's readers and to the people the project's Browse Projects names on the sub-task
itself (JSWCLOUD-25762), not to the people it names on the parent (the parent's own assignee). The sub-task inherits its
parent. Of the earlier rules none gives that set: OPEN makes the sub-task a child the parent's own holders read below the
parent (a seed for them), and the others do not let the sub-task's own holders in without the project. STRICT_OR_GRANT
does: read from the App as STRICT, or by its own grant alone, and never below a seed."""

from __future__ import annotations

import pytest
import yaml

from tests.permissions.runner import OracleLayer
from tests.permissions.scenario import World, world_from_data

WANTED = {"pr": True, "pa": False, "sa": True}  # project reader, parent's own assignee, sub-task's own assignee


def _world(rule: str, granted: bool, parents: str = "[P]", inherits: str = "[P]") -> World:
    return world_from_data(yaml.safe_load(f"""
principals: [{{id: pr}}, {{id: pa}}, {{id: sa}}]
nodes:
  - {{id: A, kind: app, app_type: JIRA}}
  - {{id: pg, kind: group, connector: A, rule: STRICT, parents: [A], belongs: [A]}}
  - {{id: P, kind: record, connector: A, rule: OPEN, parents: [pg], inherits: [pg], belongs: [pg]}}
  - {{id: S, kind: record, connector: A, rule: {rule}, parents: {parents}, inherits: {inherits}, belongs: [pg]}}
grants: [[pr, pg], [pa, P]{", [sa, S]" if granted else ""}]
gates: [[pr, A], [pa, A], [sa, A]]
"""))


async def _reads(rule: str, granted: bool, parents: str = "[P]", inherits: str = "[P]") -> dict[str, bool]:
    oracle, world = OracleLayer(), _world(rule, granted, parents, inherits)
    return {who: "S" in await oracle.admitted(world, who, ["S"]) for who in WANTED}


async def test_an_open_sub_task_is_read_by_its_parents_own_assignee() -> None:
    """The shape a grant from the sub-task's own holders would need (OPEN, so the grant alone opens it) lets the parent's
    own assignee read the sub-task below the parent: an over-share."""
    reads = await _reads("OPEN", granted=True)
    assert reads["pa"] is True and reads["sa"] is True


async def test_the_strict_sub_task_is_read_by_the_project_alone() -> None:
    """What the connector writes with no own holder on the sub-task: STRICT, no grant of its own. The parent's own assignee does not read it; neither does
    the sub-task's own assignee: there is none."""
    assert await _reads("STRICT", granted=False) == {"pr": True, "pa": False, "sa": False}


@pytest.mark.parametrize("rule", ["OPEN", "STRICT", "RESTRICTED", "PARENT_AND_GRANT"])
async def test_no_earlier_rule_of_the_sub_task_gives_the_project_and_its_own_holders_alone(rule: str) -> None:
    assert await _reads(rule, granted=True) != WANTED


async def test_strict_or_grant_gives_the_project_and_its_own_holders_alone() -> None:
    """What the connector writes for an unleveled sub-task with own holders, listed under its parent as before."""
    assert await _reads("STRICT_OR_GRANT", granted=True) == WANTED


async def test_a_sub_task_under_its_parent_that_inherits_the_project_is_not_read_by_the_project() -> None:
    """Hierarchy parent the issue, inheritance the project (what the processor writes for a record that takes its
    permissions from its group while it is listed under its parent): inheritance passes only along a hierarchy edge
    (R6), so the project's readers lose the sub-task."""
    assert await _reads("OPEN", granted=True, parents="[P]", inherits="[pg]") == {"pr": False, "pa": False, "sa": True}


async def test_the_set_needs_the_project_as_a_hierarchy_parent_too() -> None:
    """With the project also a hierarchy parent of the sub-task, inheriting the project alone, OPEN gives the wanted set:
    the processor removes that second hierarchy edge (a group edge would list the record twice)."""
    assert await _reads("OPEN", granted=True, parents="[P, pg]", inherits="[pg]") == WANTED

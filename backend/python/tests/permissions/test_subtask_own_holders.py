"""A sub-task's own Browse holders (its assignee, a user field) against the read rule.

Jira shows a sub-task to the project's readers and to the people the project's Browse Projects names on the sub-task
itself (JSWCLOUD-25762), not to the people it names on the parent (the parent's own assignee). The sub-task inherits its
parent. No access rule of a single node gives that set: OPEN makes the sub-task a child the parent's own holders read
below the parent (a seed for them), and the other rules do not let the sub-task's own holders in without the project.
The sub-task therefore stays STRICT with no grant of its own until the read rule can say "project, plus its own holders"."""

from __future__ import annotations

import pytest
import yaml

from tests.permissions.runner import OracleLayer
from tests.permissions.scenario import world_from_data

WANTED = {"pr": True, "pa": False, "sa": True}  # project reader, parent's own assignee, sub-task's own assignee


def _world(rule: str, granted: bool):
    return world_from_data(yaml.safe_load(f"""
principals: [{{id: pr}}, {{id: pa}}, {{id: sa}}]
nodes:
  - {{id: A, kind: app, app_type: JIRA}}
  - {{id: pg, kind: group, connector: A, rule: STRICT, parents: [A], belongs: [A]}}
  - {{id: P, kind: record, connector: A, rule: OPEN, parents: [pg], inherits: [pg], belongs: [pg]}}
  - {{id: S, kind: record, connector: A, rule: {rule}, parents: [P], inherits: [P], belongs: [pg]}}
grants: [[pr, pg], [pa, P]{", [sa, S]" if granted else ""}]
gates: [[pr, A], [pa, A], [sa, A]]
"""))


async def _reads(rule: str, granted: bool) -> dict[str, bool]:
    oracle, world = OracleLayer(), _world(rule, granted)
    return {who: "S" in await oracle.admitted(world, who, ["S"]) for who in WANTED}


async def test_an_open_sub_task_is_read_by_its_parents_own_assignee() -> None:
    """The shape a grant from the sub-task's own holders would need (OPEN, so the grant alone opens it) lets the parent's
    own assignee read the sub-task below the parent: an over-share."""
    reads = await _reads("OPEN", granted=True)
    assert reads["pa"] is True and reads["sa"] is True


async def test_the_strict_sub_task_is_read_by_the_project_alone() -> None:
    """What the connector writes: STRICT, no grant of its own. The parent's own assignee does not read it; neither does
    the sub-task's own assignee (an under-share the rule cannot avoid without the over-share above)."""
    assert await _reads("STRICT", granted=False) == {"pr": True, "pa": False, "sa": False}


@pytest.mark.parametrize("rule", ["OPEN", "STRICT", "RESTRICTED", "PARENT_AND_GRANT"])
async def test_no_rule_of_the_sub_task_gives_the_project_and_its_own_holders_alone(rule: str) -> None:
    assert await _reads(rule, granted=True) != WANTED

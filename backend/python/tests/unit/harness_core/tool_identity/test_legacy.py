"""OJ-1 (P2.2): a granted instance, type-keyed or not, resolved against what the definition binds now."""

from __future__ import annotations

import pytest

from harness_core.tool_identity import (
    BoundInstance,
    ToolInstanceAmbiguous,
    ToolInstanceMissing,
    match_grant,
)

UNBOUND = BoundInstance("jira", None)
X = BoundInstance("jira", "inst-x")
Y = BoundInstance("Jira", "inst-y")
SLACK = BoundInstance("slack", "inst-s")


@pytest.mark.parametrize(
    ("granted", "bound", "expected"),
    [
        ("inst-x", [X, Y, SLACK], X),
        ("default", [UNBOUND, SLACK], UNBOUND),
        ("default", [X, SLACK], X),
        ("default", [UNBOUND, X], UNBOUND),
        ("inst-y", [X, Y], Y),
    ],
    ids=["instance", "default-unbound", "default-migrated", "default-prefers-unbound", "case-insensitive-type"],
)
def test_oj1_matches(granted: str, bound: list[BoundInstance], expected: BoundInstance) -> None:
    assert match_grant("jira", granted, bound) == expected


def test_oj1_two_candidates_are_refused_never_guessed() -> None:
    with pytest.raises(ToolInstanceAmbiguous) as refused:
        match_grant("jira", "default", [X, Y])
    assert refused.value.reason_code == "tool_instance_ambiguous"
    with pytest.raises(ToolInstanceAmbiguous):
        match_grant("jira", "default", [UNBOUND, BoundInstance("jira", None)])


@pytest.mark.parametrize(("granted", "bound"), [("default", [SLACK]), ("inst-z", [X, Y]), ("inst-x", [])])
def test_oj1_no_binding_is_missing(granted: str, bound: list[BoundInstance]) -> None:
    with pytest.raises(ToolInstanceMissing) as refused:
        match_grant("jira", granted, bound)
    assert refused.value.reason_code == "tool_instance_missing"

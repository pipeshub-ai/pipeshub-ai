"""Unit tests (no store) for the runner: it must report over- and under-admits apart, with the oracle's reason."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .model import Gate, Grant, Node, Parent, Principal, World
from .runner import ConformanceRunner, OracleLayer

if TYPE_CHECKING:
    from .graph import Graph


class _Flipped(OracleLayer):
    """An oracle layer whose answer for one pair is wrong, in the chosen direction."""

    def __init__(self, node: str, leak: bool) -> None:
        super().__init__(name="flipped")
        self._node, self._leak = node, leak

    async def admitted(self, world: World, viewer: str, node_ids: list[str]) -> set[str]:
        found = await super().admitted(world, viewer, node_ids)
        return found | {self._node} if self._leak else found - {self._node}

    def shape(self, graph: Graph) -> Graph:
        return graph


def _world() -> World:
    return World(
        principals=(Principal("u"),),
        nodes=(
            Node("A", "app"),
            Node("g", "group", "A", "OPEN", (Parent("A"),), (), ("A",)),
            Node("hidden", "record", "A", "STRICT", (Parent("g"),)),
            Node("shown", "record", "A", "OPEN", (Parent("g"),), ("g",)),
        ),
        grants=(Grant("u", "g"),),
        gates=(Gate("u", "A"),),
    )


async def test_over_admit_is_reported_with_the_oracle_reason() -> None:
    report = await ConformanceRunner(_Flipped("hidden", leak=True)).check_world(_world(), "t")
    assert [f.node for f in report.over_admits] == ["hidden"] and report.under_admits == []
    assert report.over_admits[0].reason.startswith("R6:STRICT-no-inherit-no-grant")


async def test_under_admit_is_reported_apart() -> None:
    report = await ConformanceRunner(_Flipped("shown", leak=False)).check_world(_world(), "t")
    assert [f.node for f in report.under_admits] == ["shown"] and report.over_admits == []


async def test_the_oracle_equals_itself() -> None:
    report = await ConformanceRunner(OracleLayer(name="same")).check_world(_world(), "t")
    assert report.findings == [] and report.pairs == 4


def test_a_wrong_expected_citation_is_a_miss() -> None:
    from dataclasses import replace

    from .runner import scenario_misses
    from .scenario import Cite, Expect, Scenario

    world = World(
        principals=(Principal("u"),),
        nodes=(
            Node("A", "app"),
            Node("hidden", "record", "A", "STRICT", (Parent("A"),), vrid="same"),
            Node("shown", "record", "A", "OPEN", (Parent("A"),), ("A",), vrid="same"),
        ),
        gates=(Gate("u", "A"),),
    )
    right = Scenario("t", "t", world, expect=Expect(cite=(Cite("u", "same", "shown"),)))
    assert scenario_misses(right) == []
    wrong = replace(right, expect=Expect(cite=(Cite("u", "same", "hidden"),)))
    assert [m.node for m in scenario_misses(wrong)] == ["cite:same"]


def test_denials_name_the_decisive_fact() -> None:
    from .scenario import load_all

    by_id = {s.id: s for s in load_all()}
    ref = OracleLayer()
    deep = by_id["REGR-01"]
    reasons = {n: v.reason for n, v in ref.explain(deep.world, "u", [n.id for n in deep.world.nodes]).items()}
    assert any(r == "R7:depth-cap" for r in reasons.values()), reasons
    rgl = by_id["REGR-04"]
    skipped = ref.explain(rgl.world, "u", [n.id for n in rgl.world.nodes])
    assert any(v.reason.startswith("R7:rgl-parent-skipped") for v in skipped.values()), skipped


def test_deactivating_a_person_only_drops_their_memberships() -> None:
    from . import mutations
    from .model import Membership, Mutation

    world = World(principals=(Principal("u"), Principal("g", "group")), memberships=(Membership("u", "g"),),
                  grants=(Grant("u", "n"), Grant("org", "n")), gates=(Gate("u", "A"),),
                  nodes=(Node("A", "app"), Node("n", "record", "A", "OPEN")))
    after = mutations.apply(world, Mutation("user_deactivated", "u"))
    assert after.memberships == () and after.grants == world.grants and after.gates == world.gates
    assert after.principals == world.principals


def test_withdrawing_the_gate_of_a_person_changes_nothing_else() -> None:
    from . import mutations
    from .model import Membership, Mutation

    world = World(principals=(Principal("u"), Principal("v"), Principal("g", "group")),
                  memberships=(Membership("u", "g"),), grants=(Grant("u", "n"),), gates=(Gate("u", "A"), Gate("v", "A")),
                  nodes=(Node("A", "app"), Node("n", "record", "A", "OPEN")))
    after = mutations.apply(world, Mutation("gate_withdrawn", "u"))
    assert after.gates == (Gate("v", "A"),) and after.memberships == world.memberships and after.grants == world.grants


def test_lifecycle_flags_reach_the_compiled_graph() -> None:
    from .graph import compile_world

    world = World(principals=(Principal("u", active=False), Principal("g", "group", deleted=True)),
                  nodes=(Node("A", "app", active=False), Node("S", "group", "A", "OPEN", hide_children=True)))
    by_kind = {n["kind"]: n["props"] for n in compile_world(world, "t")["nodes"]}
    user, group, app, rg = by_kind["User"], by_kind["Group"], by_kind["App"], by_kind["RecordGroup"]
    assert user["isActive"] is False and group["isDeleted"] is True and app["isActive"] is False and rg["hideChildren"] is True

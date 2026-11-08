"""The conformance runner: one oracle, any number of access layers.

An `AccessLayer` answers "which of these nodes may this viewer read" for a
world. The runner checks a layer against the product oracle on every
viewer x node pair of a scenario's final world, separately reports
over-admits (the layer shows more than the oracle) and under-admits
(it shows less), and checks the scenario's own listed verdicts against the
oracle so the expectations stay honest.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from . import oracle
from .graph import Graph, Namespace, compile_world

if TYPE_CHECKING:
    from .model import World
    from .scenario import Cite, Scenario

ShapeFn = Callable[[Graph], Graph]


class AccessLayer(Protocol):
    """A mechanism that decides read access: the graph check, later a scope filter."""

    name: str

    def shape(self, graph: Graph) -> Graph:
        """The graph as this layer can store it (identity unless its schema is narrower)."""
        ...

    async def admitted(self, world: World, viewer: str, node_ids: list[str]) -> set[str]: ...

    async def cited(
        self, world: World, viewer: str, vrids: list[str], indexed_only: bool
    ) -> dict[str, str | None]: ...


def _identity(graph: Graph) -> Graph:
    return graph


class OracleLayer:
    """The product oracle as a layer; `shape` lets it mirror a narrower store."""

    PREFIX = "o"

    def __init__(self, shape: ShapeFn = _identity, name: str = "oracle") -> None:
        self.name = name
        self._shape = shape
        self._ns = Namespace(self.PREFIX)
        self._stores: dict[World, tuple[Graph, oracle.Store]] = {}

    def shape(self, graph: Graph) -> Graph:
        return self._shape(graph)

    def _store(self, world: World) -> tuple[Graph, oracle.Store]:
        if world not in self._stores:
            graph = self._shape(compile_world(world, self.PREFIX))
            self._stores[world] = (graph, oracle.Store(graph))
        return self._stores[world]

    def explain(self, world: World, viewer: str, node_ids: list[str]) -> dict[str, oracle.Verdict]:
        _, store = self._store(world)
        who = oracle.viewer(store, self._ns.id(viewer), self._ns.org)
        found = {n: oracle.verdict(store, who, self._ns.id(n)) for n in node_ids}
        return {n: oracle.Verdict(v.admitted, v.reason.replace(f"@{self.PREFIX}-", "@")) for n, v in found.items()}

    def _cited_now(self, world: World, c: Cite) -> str | None:
        graph, _ = self._store(world)
        found = oracle.cited(graph, self._ns.id(c.viewer), self._ns.org, [self._ns.id(c.vrid)],
                             indexed_only=c.indexed_only)
        record = found[self._ns.id(c.vrid)]
        return None if record is None else self._ns.local(record)

    async def admitted(self, world: World, viewer: str, node_ids: list[str]) -> set[str]:
        return {n for n, v in self.explain(world, viewer, node_ids).items() if v.admitted}

    async def cited(
        self, world: World, viewer: str, vrids: list[str], indexed_only: bool
    ) -> dict[str, str | None]:
        graph, _ = self._store(world)
        found = oracle.cited(graph, self._ns.id(viewer), self._ns.org,
                             [self._ns.id(v) for v in vrids], indexed_only=indexed_only)
        return {self._ns.local(k): None if v is None else self._ns.local(v) for k, v in found.items()}


@dataclass(frozen=True)
class Finding:
    """A layer disagreeing with the oracle on one pair."""

    scenario: str
    layer: str
    viewer: str
    node: str
    kind: str  # over-admit | under-admit | cite
    reason: str  # the oracle's reason for its verdict


@dataclass(frozen=True)
class Miss:
    """The scenario's listed verdict differs from the product oracle."""

    scenario: str
    viewer: str
    node: str
    expected_admit: bool
    reason: str

    @property
    def direction(self) -> str:
        return "under-share" if self.expected_admit else "over-share"


@dataclass
class Report:
    pairs: int = 0
    findings: list[Finding] = field(default_factory=list)
    misses: list[Miss] = field(default_factory=list)

    @property
    def over_admits(self) -> list[Finding]:
        return [f for f in self.findings if f.kind == "over-admit"]

    @property
    def under_admits(self) -> list[Finding]:
        return [f for f in self.findings if f.kind == "under-admit"]

    def merge(self, other: Report) -> None:
        self.pairs += other.pairs
        self.findings += other.findings
        self.misses += other.misses

    def summary(self) -> str:
        return (f"pairs={self.pairs} over-admits={len(self.over_admits)} "
                f"under-admits={len(self.under_admits)} other={len(self.findings) - len(self.over_admits) - len(self.under_admits)} "
                f"expectation-misses={len(self.misses)}")


def targets(world: World) -> list[str]:
    return [n.id for n in world.nodes]


def vrids(world: World) -> list[str]:
    """The virtual record ids of the world's records (a record without one has its own)."""
    return sorted({n.vrid or f"v-{n.id}" for n in world.nodes if n.kind == "record"})


class ConformanceRunner:
    def __init__(self, layer: AccessLayer) -> None:
        self.layer = layer
        self._reference = OracleLayer(layer.shape)

    async def check_world(self, world: World, scenario_id: str = "", *, cites: bool = False) -> Report:
        """Every viewer x node pair of the world, layer against oracle. With `cites`, also the record cited for
        every virtual record id, with and without `indexed_only`."""
        report = Report()
        nodes = targets(world)
        for viewer in world.users():
            truth = self._reference.explain(world, viewer, nodes)
            got = await self.layer.admitted(world, viewer, nodes)
            report.pairs += len(nodes)
            for node in nodes:
                want = truth[node]
                if (node in got) != want.admitted:
                    kind = "over-admit" if node in got else "under-admit"
                    report.findings.append(
                        Finding(scenario_id, self.layer.name, viewer, node, kind, want.reason))
            if cites:
                report.findings += await self._compare_cites(world, viewer, scenario_id)
        return report

    async def _compare_cites(self, world: World, viewer: str, scenario_id: str) -> list[Finding]:
        found = vrids(world)
        out: list[Finding] = []
        for indexed_only in (False, True):
            want = await self._reference.cited(world, viewer, found, indexed_only)
            got = await self.layer.cited(world, viewer, found, indexed_only)
            out += [Finding(scenario_id, self.layer.name, viewer, v, "cite",
                            f"indexed_only={indexed_only} oracle={want[v]} layer={got[v]}")
                    for v in found if got[v] != want[v]]
        return out

    async def check_cites(self, scenario: Scenario) -> list[Finding]:
        out: list[Finding] = []
        world = scenario.final_world()
        for c in scenario.expect.cite:
            want = await self._reference.cited(world, c.viewer, [c.vrid], c.indexed_only)
            got = await self.layer.cited(world, c.viewer, [c.vrid], c.indexed_only)
            if got != want:
                out.append(Finding(scenario.id, self.layer.name, c.viewer, c.vrid, "cite",
                                   f"oracle={want[c.vrid]} layer={got[c.vrid]}"))
        return out

    async def run(self, scenario: Scenario) -> Report:
        world = scenario.final_world()
        report = await self.check_world(world, scenario.id)
        report.findings += await self.check_cites(scenario)
        report.misses += scenario_misses(scenario)
        return report


def scenario_misses(scenario: Scenario) -> list[Miss]:
    """Listed verdicts (and cites) that the product oracle does not give."""
    world = scenario.final_world()
    ref = OracleLayer()
    out: list[Miss] = []
    for v in scenario.expect.verdicts:
        got = ref.explain(world, v.viewer, [v.node])[v.node]
        if got.admitted != v.admit:
            out.append(Miss(scenario.id, v.viewer, v.node, v.admit, got.reason))
    for c in scenario.expect.cite:
        cited = ref._cited_now(world, c)
        if cited != c.record:
            out.append(Miss(scenario.id, c.viewer, f"cite:{c.vrid}", c.record is not None,
                            f"oracle cites {cited}, scenario expects {c.record}"))
    return out


def before_misses(scenario: Scenario) -> list[Miss]:
    """Listed pre-change verdicts that the oracle does not give on the base world."""
    ref = OracleLayer()
    out: list[Miss] = []
    for v in scenario.expect.before:
        got = ref.explain(scenario.world, v.viewer, [v.node])[v.node]
        if got.admitted != v.admit:
            out.append(Miss(scenario.id, v.viewer, v.node, v.admit, got.reason))
    return out


async def run_all(layer: AccessLayer, scenarios: Iterable[Scenario]) -> Report:
    runner = ConformanceRunner(layer)
    total = Report()
    for s in scenarios:
        total.merge(await runner.run(s))
    return total

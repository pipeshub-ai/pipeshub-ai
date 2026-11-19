"""Filing a generated disagreement: a citation mismatch is shrunk on the citation check and filed as a citation
expectation that reproduces (no stores: a stub layer stands for the failing one)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .runner import ConformanceRunner, OracleLayer, scenario_misses, vrids
from .scenario import WorldStore, load_scenarios
from .test_gen_graph import _file

if TYPE_CHECKING:
    from pathlib import Path

    from .model import World


class _StubLayer(OracleLayer):
    """Agrees with the oracle on access but never cites anything: a cite-only mismatch. Counts what is left loaded."""

    name = "stub"

    def __init__(self) -> None:
        super().__init__()
        self.loaded = 0

    async def cited(self, world: World, viewer: str, vrids: list[str], indexed_only: bool) -> dict[str, str | None]:
        self.loaded += 1
        return dict.fromkeys(vrids)

    async def release(self, worlds) -> None:
        self.loaded -= len(list(worlds))


async def test_a_citation_only_mismatch_is_shrunk_and_filed_as_a_citation_that_reproduces(tmp_path: Path) -> None:
    world = WorldStore().world("golden/confluence_hr")
    layer = _StubLayer()
    report = await ConformanceRunner(layer).check_world(world, "w", cites=True)
    assert report.findings and {f.kind for f in report.findings} == {"cite"}
    finding = report.findings[0]
    assert finding.node in vrids(world)                      # a virtual record id, not a node of the world

    layer.loaded = 0
    path = await _file(layer, world, finding, "unit", tmp_path)
    assert layer.loaded == 0                                  # every candidate world was released

    filed = load_scenarios(tmp_path / path.rsplit("/", 1)[-1], WorldStore())[0]
    assert len(filed.world.nodes) < len(world.nodes)          # shrunk
    assert [c.vrid for c in filed.expect.cite] == [finding.node] and not filed.expect.verdicts
    assert scenario_misses(filed) == []                       # the oracle gives the filed citation
    assert await ConformanceRunner(layer).check_cites(filed)  # and the failing layer still disagrees with it

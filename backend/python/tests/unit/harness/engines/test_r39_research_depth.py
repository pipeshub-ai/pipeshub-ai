"""R39-DEPTH-1..4: the research-depth guard on the Onyx research shape (ADR-RUN-01 D7 lane, ADR-RUN-06).

A research report came out less thorough than earlier runs of the same request. A live run mixes the
model's share with the harness's; the scripted model does exactly what its script says, so here any shortfall in
depth (reads issued and run, both clones used, the report's size and sections, every cited page one the run read,
one engine turn) is the harness cutting research short. ``ResearchDepth`` scores it (``tests/support/research_run.py``).

DEPTH-1 is that run as the harness now plays it: the engine asks to clone, the gateway's hold passes,
the engine goes on reading, the person allows the clones later, the decision reaches the running turn, and the
clones are fetched, checked out and cited in the same turn. On the base the held clone told the engine to end its
turn: the child suspended with half its reads done (DEPTH-1 fails there with ``engine turns``).
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any

import pytest
import pytest_asyncio

from tests.support import research_run as rr
from tests.support.longtask_rig.deeproute import deployment
from tests.unit.harness.engines.research_rig import ResearchLane, git_fixture

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

pytestmark = [pytest.mark.asyncio(loop_scope="module"), pytest.mark.timeout(300, method="thread")]

# As RUN01's in-process lane: 1,000 files a repository (every save scans the fake sandbox); nothing else scaled.
UNIT = dataclasses.replace(rr.ONYX, files_per_repo=1_000)
SMALL = dataclasses.replace(rr.ONYX, files_per_repo=0, web_reads=20, report_bytes=30 * 1024, appends=3)


def _ph_clone(url: str) -> list[str]:
    """The clone as the real ``ph`` makes it: one command that calls ``repository_fetch`` and checks the copy out."""
    return [f"shell ph git clone {url}"]


def _no_clone(url: str) -> list[str]:
    del url
    return []


@pytest.fixture(scope="module", autouse=True)
def _deployment() -> Iterator[None]:
    from collections import namedtuple

    usage = namedtuple("usage", "total used free")
    with deployment(), pytest.MonkeyPatch.context() as patch:
        patch.setattr("harness_core.repos.fetch.shutil.disk_usage", lambda _p: usage(1024**4, 100 * 1024**3, 924 * 1024**3))
        yield


@dataclasses.dataclass
class LateRun:
    observed: rr.Observed
    commits: dict[str, str]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def late(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[LateRun]:
    root = tmp_path_factory.mktemp("r39")
    git = await git_fixture(root / "git", UNIT.files_per_repo)
    try:
        lane = ResearchLane(UNIT, git, rr.research_script(UNIT, git.commits, _ph_clone), late_approval=True)
        yield LateRun(await lane.run(root / "run"), git.commits)
    finally:
        await git.host.stop()


async def test_r39_depth1_a_clone_allowed_later_never_stops_the_research(late: LateRun) -> None:
    depth = rr.research_depth(late.observed)
    assert depth.shortfalls(UNIT) == [], depth


async def test_r39_depth2_the_clone_allowed_later_is_checked_out_and_cited_at_its_commit(late: LateRun) -> None:
    o = late.observed
    held = [r for r in o.engine_results if r.startswith("ph: Waiting for a person to approve")]
    assert len(held) == 2, "each clone came back held first"
    assert sum(r.startswith("Cloned ") for r in o.engine_results) == 2
    assert o.report is not None
    for repository, commit in late.commits.items():
        assert f"https://{rr.GIT_HOST}/{repository}/blob/{commit}/" in o.report.decode(), repository
    assert [rule for _, rule, _ in rr.clone_cards(o.cards)] == ["tool", "tool"]


async def test_r39_depth3_the_guard_fails_a_run_the_harness_cut_short(tmp_path: Any) -> None:  # noqa: ANN401
    """The guard's own red: a run whose working time ends a third of the way through is scored short."""
    cut = dataclasses.replace(SMALL, working_s=60)
    script = rr.research_script(cut, {}, _no_clone)
    observed = await ResearchLane(cut, None, script).run(tmp_path)
    found = rr.research_depth(observed).shortfalls(SMALL)
    assert found, rr.research_depth(observed)


async def test_r39_depth4_an_uncut_small_run_meets_the_floor(tmp_path: Any) -> None:  # noqa: ANN401
    observed = await ResearchLane(SMALL, None, rr.research_script(SMALL, {}, _no_clone)).run(tmp_path)
    assert rr.research_depth(observed).shortfalls(SMALL) == [], rr.research_depth(observed)

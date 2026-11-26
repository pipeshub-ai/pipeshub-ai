"""R41CLONE-L1..L2: on the research-run lane (ADR-RUN-01 D7), a task naming two repositories reaches its engine as two
clone commands, which a model that follows its task runs; one that never clones is told back as not read.

The scripted model's ``clones`` runs each ``ph git clone`` command its task gives, so a clone here happens only because
the harness put the command in the task: a Codex job told only "may clone from github.com" never cloned.
"""

from __future__ import annotations

import dataclasses
import json
from collections import namedtuple
from typing import TYPE_CHECKING, Any

import pytest
import pytest_asyncio

from tests.support import research_run as rr
from tests.support.longtask_rig.deeproute import deployment
from tests.unit.harness.engines.research_rig import ResearchLane, git_fixture

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

pytestmark = [pytest.mark.asyncio(loop_scope="module"), pytest.mark.timeout(300, method="thread")]

SHAPE = dataclasses.replace(rr.ONYX, files_per_repo=200, web_reads=20, report_bytes=30 * 1024, appends=3)
NAMED_REQUEST = (
    f"Do a deep research on how Onyx ({rr.GIT_HOST}/onyx-dot-app/onyx) compares with PipesHub "
    f"({rr.GIT_HOST}/pipeshub-ai/pipeshub-ai). For both, check the docs, the code repo and the website. Cite every "
    "finding with its source URL. Write the final report as one Markdown file named report.md in your output folder."
)


def _named_script(follow: bool) -> str:
    """The research shape with the named request; ``follow``: where the clones go, the model runs its task's
    clone commands (one ``clones`` directive for both)."""
    first = [True]

    def clone(_url: str) -> list[str]:
        out = ["clones"] if follow and first[0] else []
        first[0] = False
        return out

    return rr.research_script(SHAPE, {}, clone).replace(rr.REQUEST, NAMED_REQUEST, 1)


@pytest.fixture(scope="module", autouse=True)
def _deployment() -> Iterator[None]:
    usage = namedtuple("usage", "total used free")
    with deployment(), pytest.MonkeyPatch.context() as patch:
        patch.setattr("harness_core.repos.fetch.shutil.disk_usage", lambda _p: usage(1024**4, 100 * 1024**3, 924 * 1024**3))
        yield


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def git(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Any]:
    fixture = await git_fixture(tmp_path_factory.mktemp("r41git"), SHAPE.files_per_repo)
    try:
        yield fixture
    finally:
        await fixture.host.stop()


async def test_r41clone_l1_two_named_repositories_are_two_clone_commands_and_two_clones(git: Any, tmp_path: Any) -> None:  # noqa: ANN401
    o = await ResearchLane(SHAPE, git, _named_script(follow=True), named_clones=True).run(tmp_path)
    task = next(t for t in o.engine_requests if NAMED_REQUEST[:40] in t)
    for repository in rr.REPOSITORIES:
        assert f"`ph git clone {rr.repository_url(repository)}`" in task, task[-600:]
    clones = [args for name, args in o.engine_calls if "ph git clone" in json.dumps(args)]
    assert len(clones) == 2, clones
    assert sum(r.startswith("Cloned ") for r in o.engine_results) == 2
    assert "repository.not_cloned" not in json.dumps([e.data for e in o.events], default=str)


async def test_r41clone_l2_a_named_repository_never_cloned_is_told_back_as_not_read(git: Any, tmp_path: Any) -> None:  # noqa: ANN401
    o = await ResearchLane(SHAPE, git, _named_script(follow=False), named_clones=True).run(tmp_path)
    assert not any(r.startswith("Cloned ") for r in o.engine_results)
    # The job's step (and so the parent's answer) leads with each, typed, from the child's journal (ADR-RUN-03 D5).
    met = [item for e in o.events if e.type == "x.pipeshub.tool.result_presented" for item in e.data.get("limits") or []]
    for repository in rr.REPOSITORIES:
        assert {"kind": "repository.not_cloned", "repository": f"{rr.GIT_HOST}/{repository}"} in met, met

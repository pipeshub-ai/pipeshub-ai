"""RUN01-SC-01..03, SC-08 (ADR-RUN-01 D7) on the real engine lane: the research run's shape end to end, no spend.

E1's lane (``durable_stack.durable_lane``): Node's journal server with its real workspace store (its 20,000-file
commit cap), the real egress proxy and tool gateway, the scripted model behind E3, and the release engine image
(opencode by default, ``E1_ENGINE=codex`` for Codex) under the Docker broker. The engine runs the same scenario as the
in-process lane (``tests/support/research_run.py``) at full size: two repositories of 10,000 files each, 150 public
reads through the worker's ``fetch_url`` and ``web_search`` (``catalog="research"``), and the 140 KB report appended
with the sandbox's own bash.

The clones go as production's do (WP94-FU-2): the engine runs ``ph git clone``, which calls the run's
``repository_fetch``; the worker fetches from a local https git host (``git_fixture``, dialled on loopback for the
public name it stands for), the person approves each card, the copy is handed onto the turn's shelf and ``ph`` checks
it out in the sandbox. The worker's source ledger and provenance stamp are production's, so SC-02b's cards and
SC-03c's ledger and repository citations are read here too. The working-time cut, the refused save, the failed child
and the leaked steer run on the in-process lane only: they need a virtual clock, an injected store refusal or a parent
loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from app.harness.engines.repository_citations import cite_repository_links
from app.harness.repositories import SessionRepositories
from harness_core.journal.types import JournalEvent
from harness_core.provenance.ledger import ProvenanceLedger
from harness_core.provenance.types import WebPage
from harness_core.repos.fetch import FetchConfig
from tests.support import research_run as rr
from tests.unit.harness.engines.durable_stack import DurableLane, durable_lane
from tests.unit.harness.engines.real_stack import engine_image, lane_available

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

pytestmark = [pytest.mark.docker, pytest.mark.timeout(2400, method="thread")]
ENGINE = os.environ.get("E1_ENGINE", "opencode")
SHAPE = rr.ONYX
SERVED = "/tmp/pxh-run01-srv"
_LINK = re.compile(r"\]\((https://[^)\s]+)\)")


def _clone_with_ph(url: str) -> list[str]:
    """A clone as an engine makes one: ``ph git clone`` (``repository_fetch`` on the worker, the checkout here)."""
    target = url.rstrip("/").rsplit("/", 1)[-1]
    return [f"shell ph git clone {url} && echo Cloned {url} at $(git -C {target} rev-parse HEAD)"]


def _went_on(events: list[dict[str, Any]], interaction: str) -> bool:
    """The engine ran another call after asking: its ask came back held, and it kept working (ADR-RUN-06 D1)."""
    asked = next((n for n, e in enumerate(events) if e["type"] == "interaction.requested"
                  and e["data"].get("interactionId") == interaction), None)
    return asked is not None and any(e["type"] == "tool.outcome" for e in events[asked + 1 :])


@contextlib.asynccontextmanager
async def _approver(
    lane: DurableLane, run_id: str, cards: list[dict[str, Any]], *, late: bool = False
) -> AsyncIterator[None]:
    """A person who approves each card for the conversation as soon as it opens; ``late``: only once the
    gateway's hold passed and the engine went on reading (a person who allows a clone long after it was asked)."""
    decided: set[str] = set()

    async def approve() -> None:
        while True:
            events = await lane.events(run_id)
            for event in events:
                interaction = str(event["data"].get("interactionId") or "")
                if event["type"] != "interaction.requested" or interaction in decided:
                    continue
                if late and not _went_on(events, interaction):
                    continue
                decided.add(interaction)
                cards.append(dict(event["data"]))
                await lane.resolve(run_id, interaction, "approved", scope="conversation")
            await asyncio.sleep(0.5)

    task = asyncio.ensure_future(approve())
    try:
        yield
    finally:
        _ = task.cancel()
        _ = await asyncio.gather(task, return_exceptions=True)


@pytest.fixture(autouse=True)
def _available() -> None:
    available, reason = lane_available(engine_image(ENGINE))
    if not available:
        pytest.skip(reason)


def _events(raw: list[dict[str, Any]]) -> list[Any]:
    return [SimpleNamespace(type=e["type"], data=e.get("data") or {}, run_id=e.get("runId")) for e in raw]


async def test_run01_d1_the_research_shape_on_the_real_engine(tmp_path: Path) -> None:
    """SC-01: both clones go through the worker's ``repository_fetch``, their 10,000-file checkouts stay out of every
    save, and Node never refuses one; SC-02a/b: 150 public reads run and none asks, and each clone asks once on its
    own rule; SC-03a/b: the report written in seven appends is delivered whole at its last version, every link one
    the run read; SC-03c: the session's provenance ledger holds every cited page and both repositories at their
    fetched commits, and every report link into a fetched file is cited."""
    await _research(tmp_path, late=False)


async def test_r39res_fu4_a_clone_allowed_after_the_hold_on_the_real_engine(tmp_path: Path) -> None:
    """R39RES-FU-4 (ADR-RUN-06, R39-DEPTH-1/2 on the Docker lane): the person allows each clone only after the
    gateway's hold passed and the engine went on reading. Each ``ph git clone`` comes back held first, the research
    goes on in the same engine turn, the decision reaches it, and both clones are fetched, checked out and cited at
    their commits, with every SC-01..03 check of the on-time run."""
    calls = await _research(tmp_path, late=True)
    held = [c for c in calls if "Waiting for a person to approve" in c]
    assert held, "a clone came back held before the person decided"


async def _research(tmp_path: Path, *, late: bool) -> list[str]:
    git = await rr.git_fixture(tmp_path / "git", SHAPE.files_per_repo)
    cache = tmp_path / "repo-cache"
    cards: list[dict[str, Any]] = []
    try:
        script = rr.research_script(SHAPE, git.commits, _clone_with_ph).replace("\nhold\n", "\n")
        async with durable_lane(tmp_path / "lane", engine=ENGINE) as lane:
            await lane.admit(
                "run-onyx", script, tools=(rr.FETCH_URL_ID, rr.WEB_SEARCH_ID), limits={"max_steps": 200},
                worker={
                    "catalog": "research", "approvals": "engine", "git_host": rr.GIT_HOST, "git_port": git.port,
                    "git_ca_file": git.ca_file, "git_cache_dir": str(cache),
                },
            )
            worker = await lane.worker("run-onyx")
            async with _approver(lane, "run-onyx", cards, late=late):
                result = await worker.wait(2100)
            log = worker.log()
            assert result is not None and result["status"] == "completed", (result, log[-4000:])
            raw = await lane.events("run-onyx")
            events = _events(raw)
            stored = lane.artifacts(lane.sessions["run-onyx"])
            report = stored.get(rr.REPORT_NAME, ())
            calls = [str(m.content) for c in lane.model.model_calls[-1:] for m in c.messages if m.role == "tool"]
            engine_turns = sum(1 for e in raw if e["type"] == "turn.started")
    finally:
        await git.host.stop()
    commits = git.commits
    tool_of = {e.data.get("invocationId"): e.data.get("toolId") for e in events if e.type == "tool.intent"}
    web_ids = (rr.FETCH_URL_ID, rr.WEB_SEARCH_ID)
    # SC-01
    for repository, commit in commits.items():
        assert any(f"Cloned {rr.repository_url(repository)} at {commit}" in r for r in calls), (
            repository, [r[:600] for r in calls if "clone" in r.lower()][:4], [ln for ln in log.splitlines() if "repository" in ln][:6],
        )
    fetched = [e.data for e in events if e.type == "x.pipeshub.repository.fetched"]
    assert len(fetched) == len(commits), ([e.type for e in events if "repository" in e.type], log[-3000:])
    saved = [p for e in events if e.type == "workspace.committed" for p in e.data.get("changed") or []]
    assert not [p for p in saved if p.lstrip("/").split("/", 1)[0] in ("onyx", "pipeshub-ai")], saved[:5]
    assert "engine.turn.save_refused" not in log and "workspace_full" not in log
    # SC-02a, SC-02b
    web_outcomes = [e.data for e in events if e.type == "tool.outcome" and tool_of.get(e.data.get("invocationId")) in web_ids]
    assert len(web_outcomes) == SHAPE.web_reads and all(d.get("status") == "ok" for d in web_outcomes), web_outcomes[:2]
    explained = [e.data for e in events if e.type == "interaction.explained"]
    rules = [(str(d.get("toolId") or ""), str(d.get("rule") or "")) for d in explained]
    assert not [r for r in rules if r[0] in web_ids], rules
    assert len(cards) == len(commits) and [r[1] for r in rules] == ["tool"] * len(commits), (rules, cards)
    # SC-03a, SC-03b
    expected = "".join(rr.report_sections(SHAPE, commits)).encode()
    assert report and report[-1] == expected, (len(report), len(report[-1]) if report else 0, sorted(stored))
    links = set(_LINK.findall(expected.decode()))
    read = set(rr.cited_web_urls(SHAPE))
    web = {u for u in links if not u.startswith(f"https://{rr.GIT_HOST}/")}
    assert web <= read
    # SC-03c
    ledger = ProvenanceLedger()
    ledger.apply([JournalEvent.from_envelope(e) for e in raw])
    pages = {s.url for s in ledger.citable() if isinstance(s, WebPage)}
    assert web and web <= pages, sorted(web - pages)[:5]
    held = {(r.url, r.commit) for r in ledger.repositories(public_only=True)}
    assert held == {(rr.repository_url(repo), commit) for repo, commit in commits.items()}, held
    worker_cache = rr.LoopbackFetcher(FetchConfig(cache_dir=cache, git_bin="git"), rr.NoResolver()).cache
    context = SimpleNamespace(tool_state={})
    linked = cite_repository_links(expected.decode(), context, SessionRepositories(ledger, lambda: worker_cache))
    code = links - web
    assert code and not any(u in linked for u in code), "every link into a fetched file is cited"
    records = context.tool_state.get("web_records") or []
    assert {r.get("url") for r in records} >= code, sorted(code - {r.get("url") for r in records})[:3]
    if late:
        # ADR-RUN-06 D1: a held clone never ends the engine's turn (R39-DEPTH-1's "engine turns" on the base).
        assert engine_turns == 1, engine_turns
    return calls


async def test_run01_d8_an_engine_that_gathers_nothing_is_never_said_to_have_saved_work(tmp_path: Path) -> None:
    """SC-08b on the real engine: run 8's "Everything it gathered is saved" when nothing had been gathered. Since D1
    (WP-95) the settled outcome offers a retry and the words claim nothing."""
    async with durable_lane(tmp_path / "lane", engine=ENGINE) as lane:
        await lane.admit("run-empty", rr.nothing_script(), tools=(rr.FETCH_URL_ID, rr.WEB_SEARCH_ID),
                         worker={"catalog": "research", "approvals": "engine"})
        worker = await lane.worker("run-empty")
        result = await worker.wait(600)
        assert result is not None and result["status"] == "completed", (result, worker.log()[-4000:])
        events = _events(await lane.events("run-empty"))
    settled = [f for f in rr.outcome_facts(events) if f["fact"] in ("Completed", "Partial", "Failed")]
    assert settled and settled[-1]["action"] == "retry", (settled, [e.type for e in events])
    answers = [str(e.data.get("answer") or "") for e in events if e.type == "x.pipeshub.answer.finalized"]
    assert all("gathered is saved" not in a and "pick up from here" not in a for a in answers), answers

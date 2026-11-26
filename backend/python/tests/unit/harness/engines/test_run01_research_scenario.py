"""RUN01-SC-01..09 (ADR-RUN-01 D7), in process: a long research run's shape on the real kernels, no model spend.

The scenario is ``tests/support/research_run.py``; the lane is ``research_rig.py`` (DEEPROUTE's parent and engine
child, the production ``repository_fetch`` over a local git host, the production approval composition, a virtual
clock). One run carries the whole shape (two clones, 150 public reads, the report in seven appends, the working-time
cut in the middle of the last step) and SC-01..04 and SC-09 read it; SC-05..08 are runs of their own (a refused save,
a child failure, a leaked steer, a child that gathers nothing); SC-11 reads D2's storage classes and SC-12 injects
D6's other degrade rows into a save mid-run.

A case today's code passes asserts what WP-81, WP-82, WP-83 and WP-93 made true; a case it fails is a strict
``xfail`` naming the ADR-RUN-01 decision whose package turns it green (it then fails as XPASS until its marker goes).

Scaled here: ``files_per_repo`` 1,000 instead of 10,000, because every save before a gateway call scans the sandbox
(run 6's cost, about 1 s a save at 20,000 files in the fake sandbox: 90 s for the run). The Docker lane carries
10,000 (``test_run01_research_scenario_docker.py``); nothing else is scaled.
"""

from __future__ import annotations

import dataclasses
import re
from collections import namedtuple
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
import pytest_asyncio

from app.harness.engines.repository_citations import cite_repository_links
from app.harness.repositories import REPOSITORY_FETCH, SessionRepositories
from harness_core.budget.run_budget import ANSWER_RESERVE_S
from harness_core.provenance.ledger import ProvenanceLedger
from harness_core.provenance.types import WebPage
from harness_core.sandbox.errors import SandboxNotFound
from harness_core.security.token_patterns import HIDDEN_CREDENTIAL
from harness_core.testing.clock import ManualClock
from harness_core.workspace.sync.storage_classes import decode_record
from harness_core.workspace.types import WorkspaceStoreUnavailable, WorkspaceWriterBusy
from tests.support import research_run as rr
from tests.support.longtask_rig.deeproute import deployment
from tests.unit.harness.engines.research_rig import ResearchLane, git_fixture

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

pytestmark = [pytest.mark.asyncio(loop_scope="module"), pytest.mark.timeout(300, method="thread")]

UNIT = dataclasses.replace(rr.ONYX, files_per_repo=1_000)
# The separate runs: the same shape without clones, smaller.
SMALL = dataclasses.replace(rr.ONYX, files_per_repo=0, web_reads=20, report_bytes=30 * 1024, appends=3)
_LINK = re.compile(r"\]\((https://[^)\s]+)\)")


def _clone_with_ph(url: str) -> list[str]:
    """A clone as an engine makes one: ``ph git clone`` calls ``repository_fetch`` and checks the copy out."""
    return [f'tool {REPOSITORY_FETCH} {{"url": "{url}"}}', f"shell ph git clone {url}"]


def _no_clone(url: str) -> list[str]:
    del url
    return []


@pytest.fixture(scope="module", autouse=True)
def _deployment() -> Iterator[None]:
    usage = namedtuple("usage", "total used free")
    with deployment(), pytest.MonkeyPatch.context() as patch:
        # The repository cache's disk guard reads the machine's disk: these runs need a few MB of it.
        patch.setattr("harness_core.repos.fetch.shutil.disk_usage", lambda _p: usage(1024**4, 100 * 1024**3, 924 * 1024**3))
        yield


@dataclasses.dataclass
class OnyxRun:
    observed: rr.Observed
    commits: dict[str, str]
    # The worker's repository fetcher: its cache holds the fetched trees repository citations read.
    fetcher: Any = None


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def onyx(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[OnyxRun]:
    root = tmp_path_factory.mktemp("run01")
    git = await git_fixture(root / "git", UNIT.files_per_repo)
    try:
        lane = ResearchLane(UNIT, git, rr.research_script(UNIT, git.commits, _clone_with_ph))
        yield OnyxRun(await lane.run(root / "run"), git.commits, lane.fetcher)
    finally:
        await git.host.stop()


async def _small(tmp_path: Any, **lane: Any) -> rr.Observed:  # noqa: ANN401
    script = lane.pop("script", rr.research_script(SMALL, {}, _no_clone))
    return await ResearchLane(SMALL, None, script, **lane).run(tmp_path)


def _of(events: list[Any], kind: str) -> list[dict[str, Any]]:
    return [dict(e.data) for e in events if e.type == kind]


def _settled(events: list[Any]) -> dict[str, Any]:
    """The run's settled outcome as the person's chat reads it (``x.pipeshub.outcome.settled``: the typed outcome, the
    deliverable, and how the answer was framed). WP-89: the answer itself holds only the prose; the UI words the rest."""
    (settled, *_) = [dict(e.data) for e in reversed(events) if e.type == "x.pipeshub.outcome.settled"] or [{}]
    return settled


def _sections(shape: rr.Shape, commits: dict[str, str]) -> bytes:
    return "".join(rr.report_sections(shape, commits)).encode()


# --- RUN01-SC-01: two clones of about 10,000 files each ---------------------------------------------------------------


async def test_run01_sc01_two_clones_go_through_repository_fetch_and_are_never_saved_as_the_persons_files(
    onyx: OnyxRun,
) -> None:
    """Runs 6 and 7: the clones succeed through the real fetch, and the checkouts stay out of the saved files
    (WP-82: run 6's 20,977 files were saved and Node refused every commit)."""
    o = onyx.observed
    fetched = [r for r in o.engine_results if '"readOnly": true' in r]
    assert len(fetched) == 2, o.engine_results[:3]
    for repository, commit in onyx.commits.items():
        assert any(commit in r and rr.repository_url(repository) in r for r in fetched), (repository, fetched)
    assert sum("Cloned " in r for r in o.engine_results) == 2
    checkouts = [p for p in o.saved_files if p.split("/", 1)[0] in ("onyx", "pipeshub-ai")]
    assert checkouts == [], checkouts[:5]
    assert rr.REPORT in o.saved_files and o.refused_saves == 0


# --- RUN01-SC-02: 150 public reads, before and after the clones -------------------------------------------------------


async def test_run01_sc02a_every_public_read_runs(onyx: OnyxRun) -> None:
    o = onyx.observed
    reads = [args for name, args in o.engine_calls if name in (rr.FETCH_URL, rr.WEB_SEARCH)]
    assert len(reads) == UNIT.web_reads
    assert not _of(o.engine_events, "tool.denied")
    outcomes = _of(o.engine_events, "tool.outcome")
    assert len(outcomes) == UNIT.web_reads + 2 and all(d.get("status") == "ok" for d in outcomes), outcomes[:2]


async def test_run01_sc02b_public_reads_never_ask_and_only_the_two_clones_do(onyx: OnyxRun) -> None:
    """Run 7: a successful clone made the context unknown, and every later public read asked (23 cards). Green since
    WP-83 (a public clone's outcome is public content)."""
    o = onyx.observed
    assert rr.flow_cards(o.cards) == [], rr.flow_cards(o.cards)
    assert [rule for _, rule, _ in rr.clone_cards(o.cards)] == ["tool", "tool"], o.cards
    assert len(o.cards) == 2


# --- RUN01-SC-03: a 140 KB report in appends --------------------------------------------------------------------------


async def test_run01_sc03a_the_report_written_in_appends_is_delivered_whole_at_its_last_revision(onyx: OnyxRun) -> None:
    """Run 5: the cut-short run's wrap-up rewrote the 143 KB report and the rewrite became the answer (WP-81)."""
    o = onyx.observed
    assert o.report == _sections(UNIT, onyx.commits) and len(o.report) >= UNIT.report_bytes
    assert (o.report_lifecycle, o.report_version) == ("final", str(UNIT.appends))
    assert o.answer.startswith("# Onyx vs PipesHub"), o.answer[:200]
    settled = _settled(o.events)
    assert settled["deliverable"] == {"kind": "file", "name": rr.REPORT_NAME}, settled
    assert settled["outcome"]["delivered"]["file"]["path"] == rr.REPORT and settled["presented"]["opening"] is True


async def test_run01_sc03b_every_source_the_report_cites_is_one_the_run_read(onyx: OnyxRun) -> None:
    """WP-83's criterion-2 reading: a web link is a page the run fetched; a repository link names a file of a
    repository the run fetched, at the fetched commit. Nothing is fabricated."""
    o = onyx.observed
    assert o.report is not None
    read = {str(args["url"]) for name, args in o.engine_calls if name == rr.FETCH_URL}
    links = set(_LINK.findall(o.report.decode()))
    web = {u for u in links if not u.startswith(f"https://{rr.GIT_HOST}/")}
    assert web and web <= read, sorted(web - read)[:5]
    files = {f"https://{rr.GIT_HOST}/{repo}/blob/{commit}/{rr.repository_file(n)}" for repo, commit in onyx.commits.items() for n in range(UNIT.files_per_repo)}
    code = links - web
    assert code and code <= files, sorted(code - files)[:5]


async def test_run01_sc03c_the_provenance_ledger_holds_every_cited_web_page_and_repository_file(onyx: OnyxRun) -> None:
    """Run 7: links into the fetched repositories were not recorded as sources; the chat listed web pages only. D3
    (WP-96): every result is recorded with its provenance, the session's ``ProvenanceLedger`` holds the pages the run
    read and each repository at its fetched commit, and a report link into a fetched file becomes a citation of that
    file's page at the commit (``cite_repository_links`` over ``SessionRepositories``, as the engine's answer path
    reads it)."""
    o = onyx.observed
    assert o.report is not None and onyx.fetcher is not None
    ledger = ProvenanceLedger()
    ledger.apply(o.engine_events)
    report = o.report.decode()
    cited = set(_LINK.findall(report))
    pages = {s.url for s in ledger.citable() if isinstance(s, WebPage)}
    web = {u for u in cited if not u.startswith(f"https://{rr.GIT_HOST}/")}
    assert web and web <= pages, sorted(web - pages)[:5]
    held = {(r.url, r.commit) for r in ledger.repositories(public_only=True)}
    assert held == {(rr.repository_url(repo), commit) for repo, commit in onyx.commits.items()}, held
    fetcher = onyx.fetcher
    context = SimpleNamespace(tool_state={})
    linked = cite_repository_links(report, context, SessionRepositories(ledger, lambda: fetcher.cache))
    code = {u for u in cited if u.startswith(f"https://{rr.GIT_HOST}/")}
    assert code and not any(u in linked for u in code), "every link into a fetched file is cited"
    records = context.tool_state.get("web_records") or []
    assert {r.get("url") for r in records} >= code, sorted(code - {r.get("url") for r in records})[:3]


async def test_run01_sc13_the_research_depth_meets_the_onyx_floor(onyx: OnyxRun) -> None:
    """R39-RESEARCH: the shared run scored against the research-depth criteria, the harness's share only (every
    read run, both clones used, the whole report with its sections, every cited page one the run read, one turn)."""
    depth = rr.research_depth(onyx.observed)
    assert depth.shortfalls(UNIT) == [], depth


# --- RUN01-SC-04: model steps of minutes, a working-time cut in the middle of a step -----------------------------------


async def test_run01_sc04a_the_writing_window_opens_two_steps_early_and_the_cut_delivers_the_written_report(
    onyx: OnyxRun,
) -> None:
    """Run 5: the 270 s reserve opened 47 s into a 318 s step and the steer never reached the engine (WP81-SYN); the
    cut landed mid-step and the person got the report it had written (WP81-CUT). The window now opens two of the
    longest steps before the limit, so the steer reaches the engine at the next step boundary with a step to spare."""
    o = onyx.observed
    assert o.completed == {"outcome": "answered", "truncatedBy": "time"}, o.completed
    assert o.steered_at_s is not None and o.steered_at_s <= UNIT.working_s - UNIT.append_step_s, o.steered_at_s
    last = o.step_starts_s[-1]
    assert last < UNIT.working_s < last + UNIT.last_step_s, (last, o.ended_at_s)
    outcome = _settled(o.events)["outcome"]
    assert (outcome["kind"], outcome["reason"], outcome["action"]) == ("partial", "time", "continue"), outcome
    assert outcome["delivered"]["file"]["path"] == rr.REPORT and o.answer.startswith("# Onyx vs PipesHub")


async def test_run01_sc04b_the_engines_brief_states_when_to_start_writing(onyx: OnyxRun) -> None:
    """D4 (WP-99): the engine is told its phase plan up front, in clock times: gather until ``write_by``, finish by
    ``stop_by``, both inside its working time. Built wording: "Time plan: gather until 00:25 UTC at the latest, then
    write; your work must be finished by 00:30 UTC." (the ADR's example said "start writing by 17:20")."""
    o = onyx.observed
    found = re.search(r"Time plan: gather until (\d\d):(\d\d) UTC at the latest, then write; your work must be "
                      r"finished by (\d\d):(\d\d) UTC", o.engine_system)
    assert found, o.engine_system[-800:]
    write_by, stop_by = (int(found[1]) * 60 + int(found[2])) * 60, (int(found[3]) * 60 + int(found[4])) * 60
    assert write_by < stop_by <= UNIT.working_s + 60, (write_by, stop_by)


async def test_run01_sc04c_the_outcome_ledger_records_the_cut_and_what_it_delivered(onyx: OnyxRun) -> None:
    facts = rr.outcome_facts(onyx.observed.engine_events)
    kinds = [f["fact"] for f in facts]
    assert "LimitReached" in kinds and "ArtifactWritten" in kinds, kinds
    (outcome,) = [f for f in facts if f["fact"] in ("Completed", "Partial", "Failed")]
    assert outcome["fact"] == "Partial" and rr.REPORT_NAME in str(outcome.get("delivered")), outcome


# --- RUN01-SC-09: the budget ledger ------------------------------------------------------------------------------------


async def test_run01_sc09a_the_run_is_charged_what_the_provider_billed_and_never_redriven(onyx: OnyxRun) -> None:
    o = onyx.observed
    assert o.billed_tokens > 0 and o.journaled_tokens == o.billed_tokens, (o.billed_tokens, o.journaled_tokens)
    child = next(iter(_of(o.events, "child.spawned")))["childRunId"]
    assert o.claims.get(child, 0) <= 1 and len(_of(o.engine_events, "run.claimed")) == 1


async def test_run01_sc09b_the_childs_budget_is_a_slice_of_its_parents(onyx: OnyxRun) -> None:
    """D4 (WP-99): a child's deadline is its parent's less the parent's answer reserve, and its task says so. As
    built the slice is taken at the claim (``RunBudget.of_claim`` down the run's ``treeDepth``) and told in the
    ``TaskSpec`` budget line; the child's pinned spec keeps its parent's budgets (recorded as a deviation of the
    first assertion this case made, which read the pinned spec)."""
    o = onyx.observed
    found = re.search(r"deliver your result by (\d\d):(\d\d) UTC \(about (\d+) minutes from now\)", o.engine_requests[0])
    assert found, o.engine_requests[0][-600:]
    assert o.parent_deadline_at_s is not None
    deliver_by = (int(found[1]) * 60 + int(found[2])) * 60
    parent_by = o.parent_deadline_at_s - ManualClock().now().timestamp()
    assert deliver_by <= parent_by - ANSWER_RESERVE_S, (deliver_by, parent_by)
    usd = re.search(r"at most USD (\d+\.\d\d) of model use", o.engine_requests[0])
    assert usd is None or float(usd[1]) <= 5.0, usd


# --- RUN01-SC-05: a refused save ---------------------------------------------------------------------------------------


def _refuse_mid_run() -> Any:  # noqa: ANN401
    """Node refuses the two saves after the first append (run 6's bare 400), then takes commits again."""
    seen = 0

    def refuse(changes: Any) -> bool:  # noqa: ANN401
        nonlocal seen
        if rr.REPORT.removeprefix("/") not in [p.removeprefix("/") for p in changes.paths]:
            return False
        seen += 1
        return seen in (1, 2)

    return refuse


async def test_run01_sc05a_a_refused_save_never_ends_the_turn_or_redrives_it(tmp_path: Any) -> None:  # noqa: ANN401
    """Run 6: a refused save raised out of the turn, the attempt was released and redriven from nothing (WP-82)."""
    o = await _small(tmp_path, refuse=_refuse_mid_run())
    assert o.refused_saves == 2
    assert o.completed == {"outcome": "answered"}, o.completed
    assert o.report == _sections(SMALL, {}) and o.report_lifecycle == "final"
    assert len(_of(o.engine_events, "run.claimed")) == 1


async def test_run01_sc05b_the_refused_save_is_a_fact_of_the_runs_outcome(tmp_path: Any) -> None:  # noqa: ANN401
    """D1/D6: the store's refusal is a fact of the run's ledger (``save_refused``), not only a log line."""
    o = await _small(tmp_path, refuse=_refuse_mid_run())
    refused = [f for f in rr.outcome_facts(o.engine_events) if f["fact"] == "SaveRefused"]
    assert refused and all(f["reason"] for f in refused), rr.outcome_facts(o.engine_events)[-4:]


# --- RUN01-SC-06: a child engine failure -------------------------------------------------------------------------------

# The engine runs its turn's calls and then ends the turn failed, on every attempt.
_CRASHES = tuple("after_tools:engine_crashed" for _ in range(4))


async def test_run01_sc06a_a_failed_child_is_told_as_partial_and_its_written_report_is_kept(tmp_path: Any) -> None:  # noqa: ANN401
    """WP-56/WP-65: the failed child's work is kept in the conversation's files, the person is told the result is
    partial, and nothing is redriven."""
    o = await _small(tmp_path, fail_with=_CRASHES)
    assert o.status == "completed" and _settled(o.events)["outcome"]["kind"] == "partial", _settled(o.events)
    assert [p for p in o.saved_files if p.endswith(rr.REPORT)], sorted(o.saved_files)
    assert len(_of(o.engine_events, "run.claimed")) == 1


async def test_run01_sc06b_the_report_a_failed_child_wrote_is_the_outcome_and_continue_picks_it_up(tmp_path: Any) -> None:  # noqa: ANN401
    """Before D1 (WP-95) the report sat in "Unfinished work/…", the person read "there is no finished answer" and was
    offered to try the task again. Now the outcome is Partial with the report delivered, and "continue" goes on."""
    o = await _small(tmp_path, fail_with=_CRASHES)
    (outcome,) = [f for f in rr.outcome_facts(o.engine_events) if f["fact"] in ("Completed", "Partial", "Failed")]
    assert outcome["fact"] == "Partial" and outcome["action"] == "continue", outcome
    assert outcome["delivered"]["file"]["path"] == rr.REPORT, outcome
    assert o.answer.startswith("# Onyx vs PipesHub"), o.answer[:200]
    assert _settled(o.events)["outcome"]["action"] == "continue", _settled(o.events)
    assert o.report_version is not None and o.report == _sections(SMALL, {}), (o.report_version, o.report_lifecycle)


# --- RUN01-SC-07: a leaked harness steer in the parent's notes ---------------------------------------------------------


def _leaky(steer: str, script: str) -> str:
    return f"{rr.leaked_steer(steer)}\n{script}"


async def test_run01_sc07_a_steer_in_the_parents_notes_never_reaches_the_engine(tmp_path: Any) -> None:  # noqa: ANN401
    """Run 8: the root model copied the harness's "hand it to the coding engine now" steer into code_task's goal; the
    engine, told to call a tool it does not have, refused the task. Green since WP-93 (ADR-RUN-01 D5: the harness's
    instructions ride its own channel, the task is a TaskSpec)."""
    o = await _small(tmp_path, brief=_leaky)
    assert all(rr.STEER_MARK not in r for r in o.engine_requests), [r[:200] for r in o.engine_requests]
    assert o.report == _sections(SMALL, {}), "the engine did the work instead of refusing the hand-off"


# --- RUN01-SC-08: a child that gathers nothing -------------------------------------------------------------------------


async def test_run01_sc08a_a_child_that_gathers_nothing_ends_without_a_report(tmp_path: Any) -> None:  # noqa: ANN401
    o = await _small(tmp_path, script=rr.nothing_script())
    assert o.status == "completed" and o.report is None
    assert not [p for p in o.saved_files if p.startswith("notes/")], sorted(o.saved_files)
    assert len(_of(o.engine_events, "run.claimed")) == 1


async def test_run01_sc08b_the_person_is_never_told_that_nothing_was_saved_work(tmp_path: Any) -> None:  # noqa: ANN401
    """Run 8: "Everything it gathered is saved in this conversation's files" when nothing had been gathered. Since D1
    (WP-95) the outcome says nothing was gathered and offers a retry."""
    o = await _small(tmp_path, script=rr.nothing_script())
    (outcome,) = [f for f in rr.outcome_facts(o.engine_events) if f["fact"] in ("Completed", "Partial", "Failed")]
    assert outcome["action"] == "retry" and outcome["delivered"]["sources"] == 0, outcome
    assert "gathered is saved" not in o.answer and "pick up from here" not in o.answer, o.answer


# --- RUN01-SC-11: storage classes (ADR-RUN-01 D2, WP-98) -----------------------------------------------------------


async def test_run01_sc11a_each_clone_is_saved_as_a_reference_its_address_and_commit_never_its_files(
    onyx: OnyxRun,
) -> None:
    """Run 6 saved both checkouts as the person's files (20,977 of them). D2: a clone is a reference, recorded in the
    store-only record as its address at its fetched commit, with no content, so a later turn materialises it again."""
    o = onyx.observed
    assert o.references_record is not None, sorted(o.saved_files)[:10]
    recorded = decode_record(o.references_record)
    assert {(r.address, r.version) for r in recorded} == {
        (rr.repository_url(repo), commit) for repo, commit in onyx.commits.items()
    }, recorded
    assert {r.path for r in recorded} == {repo.rsplit("/", 1)[-1] for repo in onyx.commits}, recorded
    # An address and a version per checkout, never a file of it.
    assert len(o.references_record) < 2 * 1024, len(o.references_record)


_DUMP = "notes/tool-results/fetch_url-1.json"


async def test_run01_sc11b_a_tool_output_dump_is_scratch_and_the_engines_own_notes_are_kept(tmp_path: Any) -> None:  # noqa: ANN401
    """Codex once dumped every page it read as ``notes/web/*.json`` and each dump became one of the person's
    files. D2: a dump ``ph`` saves under notes/tool-results/ is scratch (kept for the run, never saved; WP98-FU-4); a
    note the engine wrote for itself is durable."""
    dump = f"shell {rr.append_command(_DUMP, '{\"url\": \"https://docs.onyx.app/finding-1\"}')}"
    lines = rr.research_script(SMALL, {}, _no_clone).split("\n")
    script = "\n".join([lines[0], dump, *lines[1:]])
    o = await _small(tmp_path, script=script)
    assert o.completed == {"outcome": "answered"}, o.completed
    assert _DUMP not in o.saved_files, sorted(o.saved_files)
    assert "notes/polish.md" in o.saved_files and rr.REPORT in o.saved_files, sorted(o.saved_files)


# --- RUN01-SC-12: the other degrade rows (ADR-RUN-01 D6) --------------------------------------------------------------


def _once_on_the_report(error: BaseException, at: int = 2) -> Any:  # noqa: ANN401
    """``error`` on the ``at``-th commit that carries the report (after the first appends were saved), then none."""
    seen = 0

    def fault(changes: Any) -> BaseException | None:  # noqa: ANN401
        nonlocal seen
        if rr.REPORT.removeprefix("/") not in [p.removeprefix("/") for p in changes.paths]:
            return None
        seen += 1
        return error if seen == at else None

    return fault


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(WorkspaceStoreUnavailable("store down"), id="store_unavailable"),
        pytest.param(WorkspaceWriterBusy("w-other"), id="writer_busy"),
        pytest.param(SandboxNotFound("sandbox gone"), id="turn_lost"),
    ],
)
async def test_run01_sc12_a_save_fault_mid_run_never_ends_the_turn_and_the_next_save_keeps_the_report_whole(
    tmp_path: Any, error: BaseException  # noqa: ANN401
) -> None:
    """D6's other rows on the research shape, raised by the save before a gateway call after the first appends were
    saved: the autosave logs it and the turn goes on, so nothing waits, releases or redrives; the next save carries
    the appends the failed one did not, and the report is delivered whole and once. (A fault that ends the turn goes
    by the table: ``test_run01_d6_faults.py`` maps each, ``test_sandbox_wait.py`` and the CH-09 cases drive the waits.)"""
    o = await _small(tmp_path, fault=_once_on_the_report(error))
    assert o.injected_faults == [type(error).__name__], o.injected_faults
    assert o.completed == {"outcome": "answered"}, o.completed
    assert o.report == _sections(SMALL, {}) and o.report_lifecycle == "final", (o.report or b"")[-200:]
    types = [e.type for e in o.engine_events]
    assert not {"run.failed", "run.quarantined", "run.yielded", "run.released"} & set(types), types[-6:]
    assert len(_of(o.engine_events, "run.claimed")) == 1


# --- RUN01-SC-10: a public page's example token in the engine's model context (WP-103) ---------------------------------


async def _through_the_sandbox_gateways(context: str) -> tuple[bytes, Any, Any]:
    """The engine's next model call (Codex, OpenAI Responses) carrying ``context`` as a tool output, through the real
    egress proxy and then the real Model Gateway: what the gateway was sent, the provider's calls, the audit sink."""
    from harness_core.access.types import ENGINE_MODEL_ID
    from harness_core.gateway.refusal_reports import (
        InMemoryRefusalSink,
        RefusalReporter,
    )
    from harness_core.gateway.responses_wire import RESPONSES_WIRE
    from harness_core.sandbox.egress import (
        EgressBinding,
        EgressProxy,
        InMemoryEgressBindings,
        InMemoryTokenSource,
        ProxyRequest,
        StaticResolver,
    )
    from harness_core.sandbox.types import EgressDestination, EgressPolicy, TokenRoute
    from harness_core.testing.ids import SeededIds
    from harness_core.testing.model_gateway import GatewayRig, body_of

    placeholder = "ph-model-run01-0123456789abcdef"
    policy = EgressPolicy(
        destinations=(EgressDestination("model-gateway", (8097,)),),
        token_routes=(TokenRoute(placeholder, "model-gateway", "gateway:model"),),
    )
    bindings = InMemoryEgressBindings()
    await bindings.register(EgressBinding("sbx-run01", "10.213.0.2", "10.213.0.3", policy, {"runId": "run-engine"}))
    sink = InMemoryRefusalSink()
    reporter = RefusalReporter(sink)
    clock = ManualClock()
    proxy = EgressProxy(
        bindings=bindings,
        tokens=InMemoryTokenSource(b"run01-sc10", clock, ttl_s=600),
        resolver=StaticResolver(),
        upstreams={"model-gateway": "http://model-gateway-upstream:8096/"},
        clock=clock,
        ids=SeededIds(),
        refusals=reporter,
    )
    call = {
        "model": ENGINE_MODEL_ID,
        "store": False,
        "input": [
            {"role": "user", "content": rr.REQUEST},
            {"type": "function_call", "call_id": "c1", "name": "shell", "arguments": '{"command": ["cat", "notes/web/pat.txt"]}'},
            {"type": "function_call_output", "call_id": "c1", "output": context},
        ],
    }
    request = ProxyRequest(
        "POST", "/v1/responses", (("Host", "model-gateway:8097"), ("Authorization", f"Bearer {placeholder}")),
        __import__("json").dumps(call).encode(),
    )
    forward = await proxy.authorize(request, local="10.213.0.2", peer="10.213.0.3")
    rig = GatewayRig()
    _ = await rig.gateway.serve(RESPONSES_WIRE, rig.token(rig.pin()), {}, body_of(__import__("json").loads(forward.body)))
    await reporter.drain()
    return forward.body, rig.transport.calls, sink


async def test_run01_sc10_a_public_pages_example_token_never_stops_the_run_and_never_leaves(tmp_path: Any) -> None:  # noqa: ANN401
    """Run 12: the engine had fetched PipesHub's public personal-access-token page, whose example token has the
    ``pipeshub_pat`` shape, and the next model call carrying it was refused (``forbidden_pattern``), which ended a
    17-minute run as "the coding engine ran into an error". On the lane's real egress proxy the engine's model calls
    go on with the shape hidden, so the run delivers its report; behind it the real Model Gateway serves such a call,
    the provider never receives the shape, and the run's audit record names the kind hidden (WP-103)."""
    o = await _small(tmp_path)
    example = rr.secret_shaped_example()
    page = [r for r in o.engine_results if rr.SECRET_SHAPED_URL in r and "PIPESHUB_MCP_TOKEN=" in r]
    assert page and all(HIDDEN_CREDENTIAL in r for r in page), "the engine's model saw the page, with the shape hidden"
    assert all(example not in r for r in o.engine_results + o.engine_requests)
    assert o.status == "completed" and o.report == _sections(SMALL, {}), o.completed
    assert o.completed.get("truncatedBy") not in ("engine_error", "secret_blocked"), o.completed
    context = page[0].replace(HIDDEN_CREDENTIAL, example)
    sent, provider_calls, sink = await _through_the_sandbox_gateways(context)
    assert example.encode() not in sent and len(provider_calls) == 1
    assert example not in repr(provider_calls)
    assert [(h.gateway, h.kind) for _, h in sink.hidden_secrets] == [("egress", rr.SECRET_SHAPED_KIND)]
    assert sink.records == [], "nothing was refused"


# --- WP103-FU-3: a model call the egress proxy refuses ends the engine's turn -----------------------------------------


async def test_run01_fu3_a_model_call_the_egress_refuses_ends_the_turn_with_its_code(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch  # noqa: ANN401
) -> None:
    """SC-10's red run hung until pytest's timeout: the egress proxy's refusal of a model call reached the fake engine's
    loop as a connection error, which ended the loop without a turn end, so the kernel waited for one. The engine now
    reads the refusal as a real engine does (its ``[gateway:<code>]``); here an encoded body, PipesHub's own stop."""
    from harness_core.sandbox.egress import (
        EgressProxy,
        EgressRefusalReason,
        EgressRefused,
        ProxyRequest,
    )

    authorize = EgressProxy.authorize
    refused: list[str] = []

    async def refusing(self: EgressProxy, request: ProxyRequest, **kw: Any) -> Any:  # noqa: ANN401
        if "model-gateway" in request.target and request.body and b"hold" in request.body and len(refused) < 50:
            refused.append(request.target)
            raise EgressRefused(EgressRefusalReason.BODY_ENCODED, "encoded request bodies cannot be inspected")
        return await authorize(self, request, **kw)

    monkeypatch.setattr(EgressProxy, "authorize", refusing)
    o = await _small(tmp_path)
    assert refused, "a model call was refused"
    failed = [e for e in o.engine_events if e.type == "run.failed"]
    assert [e.data.get("errorType") for e in failed] == ["model_call_encoded"], [dict(e.data) for e in failed]
    assert o.status == "completed", "the parent still answers"

"""The in-process lane of the research-run scenario (ADR-RUN-01 D7, ``tests/support/research_run.py``).

DEEPROUTE's rig (``tests/support/longtask_rig/deeproute.py``): the real ``PipesHubRuntimeAssembler`` loop as
the parent, ``RunDriver`` and the durable kernels over the in-memory journal, the ``code_task`` child on the engine
kernel and ``EngineController`` with ``FakeEngine`` in the fake sandbox. Added for the research shape, and nothing else
faked:

* the web reads are the child's ``fetch_url`` and ``web_search`` (the fake web: ``page_text``, ``search_text``);
* ``repository_fetch`` is the production tool (``RepositoryFetchTools`` over ``RepositoryFetcher``): real git over
  https from a local ``git http-backend`` serving the two fixture repositories, its address check pinned to the
  loopback address it serves on (GITF's ``_LoopbackFetcher``); the hand-off lands in a host folder and the fake
  sandbox's ``ph git clone`` runs ph's own ``checkout_clone`` on it, then copies the checkout into the sandbox;
* the child's approvals are the production composition (``engine_guarded`` with the flow rule, ``RepositoryAsks``)
  over ``SessionSourceLedger``; a person approves every card for the conversation as soon as it opens, within the
  call's hold, as run 7's approver did;
* the clock is virtual: each model step moves it ``gather_step_s``, and a ``hold`` step moves it in small ticks for
  ``append_step_s`` (``last_step_s`` the last), so the controller's working-time watch sees it pass mid-step;
* a store can refuse commits as Node refused run 6's (``InvalidChangeSet``).
"""

from __future__ import annotations

import asyncio
import dataclasses
import io
import os
import posixpath
import re
import subprocess
import tarfile
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, override

from app.harness.approvals import engine_guarded
from app.harness.assembly.handoff_instructions import upfront
from app.harness.engines.capability_limits import LIMITS_KEY, refused_clones, uncloned
from app.harness.labels import SessionSourceLedger, call_provenance, label_from_wire
from app.harness.repositories import (
    REPOSITORY_FETCH,
    RepositoryAsks,
    RepositoryFetchTools,
    named_repositories,
    named_repositories_line,
    repository_effect,
)
from harness_core.contracts import validate_contract
from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.effects.flow import FlowRule, GrantingLedger
from harness_core.effects.types import ToolEffect, allow_all
from harness_core.engines.profiles import profile_json
from harness_core.engines.tool_folder import Shelf, ToolFolder
from harness_core.journal.types import RunNotClaimable
from harness_core.kernel.core.messages import AssistantMessage, TextPart, ToolCall
from harness_core.kernel.core.responses import StopReason, TokenUsage
from harness_core.kernel.durable.ports import SampledTurn
from harness_core.knowledge.bulk_export import ShelfRelay
from harness_core.logs import HarnessLog
from harness_core.provenance.rules import REPOSITORY_RULE
from harness_core.provenance.stamp import ProvenanceStamp
from harness_core.repos.fetch import FetchConfig, RepositoryFetcher
from harness_core.testing.clock import ManualClock
from harness_core.testing.contracts.tool_catalog import OfferedTool
from harness_core.testing.engines.durable import (
    EngineWorld,
    ScriptedEngineAdapter,
    engine_profile,
)
from harness_core.testing.engines.fake_engine import ToolOutcome
from harness_core.testing.engines.script import ModelReply, ScriptedBrain, current_turn
from harness_core.workspace.forks import session_scope, session_workspace_id
from harness_core.workspace.memory import InMemoryWorkspaceStore
from harness_core.workspace.sync.storage_classes import STORAGE_RECORD_PATH
from harness_core.workspace.types import InvalidChangeSet, ObjectScope
from harness_engines.pipeshub import ph
from tests.support import research_run as rr
from tests.support.longtask_rig.deeproute import (  # pyright: ignore[reportPrivateUsage]
    PARENT,
    Rig,
    _code_task_result,
    _spec,
)
from tests.support.research_run import GitFixture, git_fixture
from tests.unit.harness.assembly import world as w

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence

    from harness_core.effects.types import ApprovalPolicy
    from harness_core.journal.types import JournalEvent
    from harness_core.kernel.durable.ports import DeltaKind, ModelCall
    from harness_core.security.labels import SourceLedger
    from harness_core.testing.engines.script import ChatMessage
    from harness_core.testing.sandbox.fake import FakeSandboxHandle

LOG = HarnessLog.named("tests.run01")
SESSION = "ses-1"
REPOSITORY_ID = repository_effect().tool_id
TOOL_NAMES = {rr.FETCH_URL_ID: rr.FETCH_URL, rr.WEB_SEARCH_ID: rr.WEB_SEARCH, REPOSITORY_ID: REPOSITORY_FETCH}
# A web read leaves the boundary with its arguments (FG2): the flow rule holds it once the context is not public.
_WEB = SinkDescriptor(egress="external", args_leave_boundary=True)
TOOLS = (
    OfferedTool(rr.FETCH_URL, ToolEffect(rr.FETCH_URL_ID, "read", True, _WEB)),
    OfferedTool(rr.WEB_SEARCH, ToolEffect(rr.WEB_SEARCH_ID, "read", True, _WEB)),
    OfferedTool(REPOSITORY_FETCH, repository_effect()),
)
STEER = upfront("research").text
# Long enough that no attempt's lease lapses while the virtual clock runs a 318-second step.
LEASE_MS = 24 * 3600 * 1000
_TICK_S = 2.0
# The root's deadline, from admission (run 6: 3,600 s), and the virtual clock's start.
PARENT_DEADLINE_S = 3600.0
_EPOCH_START = ManualClock().now().timestamp()
# How the writing window's steer opens (``allowance.synthesis_text``).
SYNTHESIS_MARK = "Stop gathering"
_PRINTF = re.compile(r"^(?:mkdir -p \S+ && )?printf '%b' '(?P<text>.*)' >> (?P<path>\S+)$", re.DOTALL)


# --- the git host and the repository tool ----------------------------------------------------------------------------


@dataclass
class _Shelf:
    """The turn's repository folder: the handed-over bare copies, in a host folder ``ph git clone`` checks out from.
    The session's references go through the run's ``ShelfRelay`` to the turn's endpoint, as production's shelf does
    (``references=shelf``), so the sync records the checkouts the worker fetched (ADR-RUN-01 D2)."""

    root: Path
    relay: ShelfRelay = field(default_factory=ShelfRelay)
    handed: list[Path] = field(default_factory=list[Path])

    def reference_for(self, address: str) -> Any:  # noqa: ANN401
        return self.relay.reference_for(address)

    def fetched(self, address: str) -> None:
        self.relay.fetched(address)

    async def hand_off(self, archive: AsyncIterator[bytes], size_bytes: int) -> str | None:
        data = b"".join([chunk async for chunk in archive])
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            names = {m.name.split("/", 1)[0] for m in tar.getmembers()}
            tar.extractall(self.root, filter="data")
        (name,) = names
        self.handed.append(self.root / name)
        return "/pipeshub/repos"


_GIT_ENV = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}


def _head_commit(bare: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(bare), "rev-parse", "HEAD"], check=True, capture_output=True, text=True, env={**os.environ, **_GIT_ENV}
    ).stdout.strip()


def _ph_shell(
    shelf: _Shelf, fetch: Callable[[str], Awaitable[Any]] | None = None
) -> Callable[[FakeSandboxHandle, str, str], Awaitable[ToolOutcome]]:
    """The sandbox's shell for this scenario: ``ph git clone`` (ph's own checkout of the copy the tool handed over,
    each clone the next hand-off) and ``printf '%b' '…' >> path``. ``fetch``: ``ph git clone`` calls
    ``repository_fetch`` itself, as the real ``ph`` does, and a call held for the person exits with its words (75)."""
    clones = 0

    async def shell(sandbox: FakeSandboxHandle, workdir: str, command: str) -> ToolOutcome:
        nonlocal clones
        if command.startswith("ph git clone "):
            url = command.split()[3]
            if fetch is not None:
                fetched = await fetch(url)
                if fetched.disposition != "executed" and fetched.disposition != "recorded":
                    return ToolOutcome(f"ph: {fetched.text}", is_error=True)
            source = shelf.handed[clones]
            clones += 1
            target = url.rstrip("/").rsplit("/", 1)[-1]
            with tempfile.TemporaryDirectory() as scratch:
                checkout = Path(scratch) / target
                # As ``ph git clone`` does, the checkout is marked with the commit the fetch answered (D2's version).
                commit = await asyncio.to_thread(_head_commit, source)
                await asyncio.to_thread(ph.checkout_clone, str(source), str(checkout), url, None, full=False, commit=commit)
                for path in checkout.rglob("*"):
                    if path.is_file():
                        sandbox.files[posixpath.join(workdir, target, path.relative_to(checkout).as_posix())] = path.read_bytes()
            return ToolOutcome(f"Cloned {url} into {target}. It is a reference copy, not saved with the session's files.")
        found = _PRINTF.match(command)
        if found is None:
            return ToolOutcome(f"unsupported: {command[:80]}", is_error=True)
        path = posixpath.join(workdir, found["path"])
        text = found["text"].replace("\\n", "\n").replace("\\\\", "\\").encode()
        sandbox.files[path] = (sandbox.files[path] if path in sandbox.files else b"") + text
        return ToolOutcome("")

    return shell


# --- the scripted engine model and the parent ------------------------------------------------------------------------


@dataclass
class ResearchBrain(ScriptedBrain):
    """The conformance brain on the virtual clock: every model step moves it ``gather_s`` unless the step was a
    ``hold`` (the pacer moved it then). A task that tells the engine to call ``code_task`` is refused as run 8's
    engine refused it (it has no such tool)."""

    clock: Any = None
    gather_s: float = rr.ONYX.gather_step_s
    held: bool = False
    starts: list[float] = field(default_factory=list[float])
    steered_at: float | None = None

    @override
    def pending_hold(self, messages: Sequence[ChatMessage]) -> tuple[int, int] | None:
        # Every request comes here first: the steer reached the engine with the first request that carries it.
        if self.steered_at is None and any(m.role == "user" and m.content.startswith(SYNTHESIS_MARK) for m in messages):
            self.steered_at = self.clock.monotonic() if self.clock is not None else 0.0
        key = super().pending_hold(messages)
        self.held = key is not None
        return key

    @override
    def reply(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[str],
        call_id: str,
        properties: Mapping[str, frozenset[str]] | None = None,
    ) -> ModelReply:
        if not self.held and self.clock is not None:
            self.starts.append(self.clock.monotonic())
            _ = self.clock.advance(self.gather_s)
        self.held = False
        turn = current_turn(messages)
        task = next((m.content for m in turn if m.role == "user"), "")
        if rr.STEER_MARK in task and "call code_task" in task:
            return dataclasses.replace(super().reply(messages, (), call_id, properties), content=rr.REFUSED_HAND_OFF, tool_calls=())
        return super().reply(messages, tools, call_id, properties)


@dataclass
class ResearchParent:
    """The root model of runs 5-8: it hands the request to code_task at once (the harness asks it to, up front), with
    ``brief(steer)`` as the goal, and presents the engine's answer as it came back."""

    brief: Callable[[str], str]
    calls: list[ModelCall] = field(default_factory=list["ModelCall"])

    async def sample(self, call: ModelCall, on_delta: Callable[[DeltaKind, str], Awaitable[None]]) -> SampledTurn:
        self.calls.append(call)
        usage = TokenUsage(input_tokens=9_000, output_tokens=300)
        result = _code_task_result(call)
        if result is None:
            # Run 8's model copied the harness's hand-off instruction into the brief; ``brief`` decides what it copies.
            goal = self.brief(STEER)
            message = AssistantMessage(content=[], tool_calls=[ToolCall(id="ct1", name="code_task", arguments={"goal": goal})])
            return SampledTurn(message=message, usage=usage, stop_reason=StopReason.TOOL_USE)
        output = str(result.get("output") or result.get("error") or "")
        await on_delta("text_delta", output)
        return SampledTurn(message=AssistantMessage(content=[TextPart(text=output)]), usage=usage)

    def restore(self, raw: bytes, *, provider: str, transport: str) -> AssistantMessage | None:
        return None


class RefusingStore(InMemoryWorkspaceStore):
    """Refuses, as Node refused run 6's saves (``InvalidChangeSet``), every commit that ``refuse`` picks."""

    def __init__(self, *, clock: Any, refuse: Callable[[Any], bool]) -> None:  # noqa: ANN401
        super().__init__(clock=clock)
        self._refuse = refuse
        self.refused = 0

    @override
    async def commit(self, *args: Any, **kwargs: Any) -> Any:
        if self._refuse(kwargs["changes"]):
            self.refused += 1
            raise InvalidChangeSet("WORKSPACE_TOO_LARGE")
        return await super().commit(*args, **kwargs)


class FaultyStore(InMemoryWorkspaceStore):
    """Raises, at a commit, the fault ``fault`` picks for its changes (ADR-RUN-01 D6's other degrade rows: the store
    unreachable, another writer holding the workspace, the sandbox gone under the save); ``None`` commits."""

    def __init__(self, *, clock: Any, fault: Callable[[Any], BaseException | None]) -> None:  # noqa: ANN401
        super().__init__(clock=clock)
        self._fault = fault
        self.raised: list[str] = []

    @override
    async def commit(self, *args: Any, **kwargs: Any) -> Any:
        if (error := self._fault(kwargs["changes"])) is not None:
            self.raised.append(type(error).__name__)
            raise error
        return await super().commit(*args, **kwargs)


_TOOL_FOLDER = ToolFolder(
    "/pipeshub/tools", render=lambda _tools, _endpoints, _skills: {}, shelf=Shelf("shelf", "/pipeshub/tools/shelf", lambda _n: False)
)


# --- the lane --------------------------------------------------------------------------------------------------------


@dataclass
class ResearchLane:
    shape: rr.Shape
    git: GitFixture | None
    script: str
    brief: Callable[[str, str], str] = lambda steer, script: script
    refuse: Callable[[Any], bool] | None = None
    # D6: a fault the store raises at a commit (``FaultyStore``).
    fault: Callable[[Any], BaseException | None] | None = None
    fail_with: tuple[str | None, ...] = ()
    cards: list[tuple[str, str, str]] = field(default_factory=list[tuple[str, str, str]])
    # The worker's repository fetcher (its cache holds each fetched tree, which repository citations read).
    fetcher: RepositoryFetcher | None = None
    # ADR-RUN-06: ``ph git clone`` calls ``repository_fetch`` itself, and the person allows a clone only after the
    # gateway's hold has passed and the engine went on reading (a manual test allowed one 40 minutes later).
    late_approval: bool = False
    # R41-CLONE: ``ph git clone`` calls ``repository_fetch`` itself (as ``late_approval`` does), and the engine's first
    # task ends with the app's line naming the repositories its text names (``named_repositories_line``).
    named_clones: bool = False
    _decided: set[str] = field(default_factory=set[str])

    def world(self, scratch: Path) -> EngineWorld:
        profile = engine_profile(max_steps=400, max_wall_s=self.shape.working_s)
        shelf = _Shelf(scratch / "shelf")
        shelf.root.mkdir(parents=True, exist_ok=True)
        tools = self._repository_tools(scratch, shelf) if self.git is not None else None
        handlers = rr.web_handlers()
        if tools is not None:
            handlers[REPOSITORY_FETCH] = tools.execute

        def ledger() -> SourceLedger:
            # The parent read nothing before its up-front hand-off: the context it passes at the spawn is public.
            return GrantingLedger(SessionSourceLedger(tool_names=TOOL_NAMES, inherited=label_from_wire({"complete": True, "sources": []})))

        def guard(approvals: ApprovalPolicy, source: SourceLedger, run_id: str) -> ApprovalPolicy:
            assert isinstance(source, GrantingLedger)
            guarded = engine_guarded(approvals, allow_all, rules=(FlowRule("ask"),), ledger=source, run_id=run_id, log=LOG)
            if tools is None:
                return guarded
            return RepositoryAsks(
                guarded, source, run_id, source.grants, size_of=tools.size_of(w.ORG),
                cap_bytes=tools.fetcher.config.repo_max_bytes, log=LOG, references=shelf,
            )

        holder: list[EngineWorld] = []

        async def fetch(url: str) -> Any:  # noqa: ANN401
            return await holder[0].host.call(REPOSITORY_FETCH, {"url": url})

        adapter = ScriptedEngineAdapter(shell=_ph_shell(shelf, fetch if self.late_approval or self.named_clones else None), max_steps=500)
        adapter.fail_with = list(self.fail_with)
        world = EngineWorld(
            engine_authority=profile_json(profile), adapter=adapter, tools=TOOLS, handlers=handlers,
            ledger=ledger, guard=guard, lease_ms=LEASE_MS, provenance=_provenance, shelf_relay=shelf.relay,
        )
        holder.append(world)
        if self.named_clones:
            world.task_facts = lambda text: named_repositories_line(named_repositories(text, rr.git_hosts()))
            world.child_result = _uncloned_hand_back
        world.gateway.brain = ResearchBrain(clock=world.clock, gather_s=self.shape.gather_step_s)
        # Production's engine turns hold a tool folder with a shelf, and the controller attaches the run's relay to
        # the turn's endpoint only then: an empty one here, so the repository tool's fetches are vouched (D2).
        world.config = dataclasses.replace(world.config, tool_folder=_TOOL_FOLDER)
        if self.refuse is not None:
            world.store = RefusingStore(clock=world.clock, refuse=self.refuse)
        if self.fault is not None:
            world.store = FaultyStore(clock=world.clock, fault=self.fault)
        return world

    def _repository_tools(self, scratch: Path, shelf: _Shelf) -> RepositoryFetchTools:
        assert self.git is not None
        config = FetchConfig(cache_dir=scratch / "cache", git_bin="git", port=self.git.port, ca_file=self.git.ca_file)

        async def hosts() -> list[Any]:
            return rr.git_hosts()

        self.fetcher = rr.LoopbackFetcher(config, rr.NoResolver())
        return RepositoryFetchTools(fetcher=self.fetcher, hosts=hosts, shelf=shelf, references=shelf)

    async def run(self, scratch: Path) -> rr.Observed:
        world = self.world(scratch)
        parent = ResearchParent(lambda steer: self.brief(steer, self.script))
        rig = Rig(parent, world=world)  # type: ignore[arg-type]
        approver = asyncio.ensure_future(self._approve(rig))
        pacer = asyncio.ensure_future(self._pace(world))
        try:
            done = await self._drive(rig)
        finally:
            for task in (approver, pacer):
                _ = task.cancel()
            await asyncio.gather(approver, pacer, return_exceptions=True)
        return await self._observed(rig, done.status)

    async def _drive(self, rig: Rig) -> Any:  # noqa: ANN401
        """The parent to its end, each code_task child it joins on driven until no worker can claim it (a failed
        child is not driven again: the parent reads its failure)."""
        journal = rig.world.journal
        # Run 6's root: an hour from admission (the release's working time), so a child's slice of it is real.
        raw = _spec(rig.world.engine_authority).model_dump(by_alias=True, exclude_none=True)
        raw["budgets"]["calendarDeadline"] = (rig.world.clock.now() + timedelta(seconds=PARENT_DEADLINE_S)).strftime("%Y-%m-%dT%H:%M:%SZ")
        journal.admit(PARENT, org_id=w.ORG, session_id=SESSION, spec=validate_contract(RunSpec, raw), input_text=rr.REQUEST)
        done = await rig.attempt(PARENT)
        while (done.status, done.reason) == ("suspended", "join"):
            for child in journal.children_of(PARENT):
                while (claim := await _claim(journal, child)) is not None:
                    _ = await rig.driver.run_claimed(claim, claim_sent_at=rig.world.clock.suspend_aware())
            done = await rig.attempt(PARENT)
        return done

    async def _approve(self, rig: Rig) -> None:
        """A person who approves each card for the conversation as soon as it opens (run 7's approver)."""
        journal = rig.world.journal
        while True:
            for run_id in [PARENT, *journal.children_of(PARENT)]:
                for interaction in journal.open_interactions(run_id):
                    if interaction in self._decided:
                        continue
                    if self.late_approval and run_id != PARENT and not _went_on(rig.events(run_id), interaction):
                        continue
                    self._decided.add(interaction)
                    self.cards.append(_card(rig.events(SESSION if run_id == PARENT else run_id), interaction))
                    journal.resolve_interaction(run_id, interaction, "approved", scope="conversation")
                    rig.world.wakeup.wake(run_id)
            await asyncio.sleep(0.001)

    async def _pace(self, world: EngineWorld) -> None:
        """Each ``hold`` step takes ``append_step_s`` of the virtual clock (the last ``last_step_s``), in ticks the
        working-time watch can see; a step the limit interrupts stops the clock where it was cut."""
        gateway = world.gateway
        holds = 0
        while True:
            await gateway.holding.wait()
            holds += 1
            brain = gateway.brain
            assert isinstance(brain, ResearchBrain)
            spent = len(brain.holds)
            brain.starts.append(world.clock.monotonic())
            step = self.shape.last_step_s if holds > self.shape.appends else self.shape.append_step_s
            moved = 0.0
            while moved < step and len(brain.holds) == spent:
                _ = world.clock.advance(_TICK_S)
                moved += _TICK_S
                await asyncio.sleep(0.002)
            if len(brain.holds) == spent:
                gateway.release()
            # The hold is spent once the gateway records it (released or interrupted); the next may start at once.
            while len(brain.holds) == spent:  # noqa: ASYNC110 - the brain's set has no event to wait on
                await asyncio.sleep(0.001)

    async def _observed(self, rig: Rig, status: str) -> rr.Observed:
        world = rig.world
        events = rig.events()
        children = world.journal.children_of(PARENT)
        child = children[0] if children else None
        engine_events = rig.events(child) if child is not None else []
        (completed, *_) = [dict(e.data) for e in events if e.type == "run.completed" and e.run_id == PARENT] or [{}]
        answers = [e for e in events if e.type == "x.pipeshub.answer.finalized" and e.run_id == PARENT]
        answer = str(answers[-1].data.get("answer") or "") if answers else ""
        citations = [str(c.get("url") or c.get("webUrl") or c) if isinstance(c, dict) else str(c) for c in (answers[-1].data.get("citations") or [])] if answers else []
        head = await world.store.head(ObjectScope(w.ORG), session_workspace_id(SESSION))
        report = report_version = lifecycle = None
        stored = await world.artifacts.find_by_name(_binding(), rr.REPORT_NAME)
        if stored is None and child is not None:
            stored = await world.artifacts.find_by_name(_binding(child), rr.REPORT_NAME)
        if stored is not None:
            report_version, lifecycle = str(stored.version), stored.lifecycle
            report = await world.artifacts.content(_binding(), stored.artifact_id, stored.version)
        elif rr.REPORT in head.files:
            report = await world.objects.get(session_scope(w.ORG, SESSION), head.files[rr.REPORT].content)
        record = head.files.get(STORAGE_RECORD_PATH)
        references = await world.objects.get(session_scope(w.ORG, SESSION), record.content) if record is not None else None
        claims = Counter(str(e.run_id) for e in events if e.type == "run.claimed")
        brain = world.gateway.brain
        assert isinstance(brain, ResearchBrain)
        last = world.gateway.model_calls[-1].messages if world.gateway.model_calls else ()
        requests = [m.content for m in last if m.role == "user"]
        system = world.gateway.model_calls[0].system if world.gateway.model_calls else ""
        calls = [(c.name, c.args()) for m in last if m.role == "assistant" for c in m.tool_calls]
        return rr.Observed(
            status=status,
            completed=completed,
            answer=answer,
            report=report,
            report_version=report_version,
            report_lifecycle=lifecycle,
            saved_files=frozenset(head.files),
            refused_saves=getattr(world.store, "refused", 0),
            cards=list(self.cards),
            citations=citations,
            claims=dict(claims),
            engine_events=engine_events,
            engine_requests=requests,
            engine_system=system,
            parent_deadline_at_s=(_EPOCH_START + PARENT_DEADLINE_S),
            engine_calls=calls,
            engine_results=[m.content for m in last if m.role == "tool"],
            events=events,
            billed_tokens=sum(u.input_tokens + u.output_tokens for u in world.gateway.billed),
            journaled_tokens=_journaled_tokens(engine_events),
            steered_at_s=brain.steered_at,
            step_starts_s=list(brain.starts),
            ended_at_s=world.clock.monotonic(),
            references_record=references,
            injected_faults=list(getattr(world.store, "raised", [])),
        )


def _uncloned_hand_back(text: str) -> Callable[[dict[str, Any], Sequence[JournalEvent]], dict[str, Any]]:
    """The hand-back's could-not-do part as ``EngineChildResult`` builds it (R41-CLONE), from the child's own journal."""
    named = named_repositories(text, rr.git_hosts())

    def shape(document: dict[str, Any], events: Sequence[JournalEvent]) -> dict[str, Any]:
        refused = refused_clones(events, REPOSITORY_ID)
        limits = (*refused, *uncloned(events, named, refused, typed=True))
        return {**document, LIMITS_KEY: [limit.to_json() for limit in limits]} if limits else document

    return shape


def _went_on(events: Sequence[JournalEvent], interaction: str) -> bool:
    """The engine ran another call after asking: its ask came back held, and it kept working (ADR-RUN-06 D1)."""
    asked = next(
        (n for n, e in enumerate(events) if e.type == "interaction.requested" and e.data.get("interactionId") == interaction),
        None,
    )
    return asked is not None and any(e.type == "tool.outcome" for e in events[asked + 1 :])


async def _claim(journal: Any, run_id: str) -> Any:  # noqa: ANN401
    try:
        return await journal.claim(run_id, worker_id="w-run01")
    except RunNotClaimable:
        return None


def _binding(run_id: str = PARENT) -> Any:  # noqa: ANN401
    from harness_core.interactions.ports import RunBinding

    return RunBinding(w.ORG, SESSION, run_id)


def _provenance(names: Sequence[str]) -> ProvenanceStamp:
    """The production rules for this run's tools (the web's and ``repository_fetch``'s), stamped and journaled."""
    rules = {**rr.web_rules(), REPOSITORY_FETCH: REPOSITORY_RULE}
    return ProvenanceStamp.for_tools(names, rules, journal=True, sources_of=call_provenance)


def _card(events: Sequence[JournalEvent], interaction: str) -> tuple[str, str, str]:
    """The card as the person saw it (``interaction.explained``): the tool, the rule that asked, the destination."""
    for event in reversed(events):
        if event.type == "interaction.explained" and event.data.get("interactionId") == interaction:
            destination = event.data.get("destination")
            label = destination.get("label") if isinstance(destination, dict) else None
            return str(event.data.get("toolId") or ""), str(event.data.get("rule") or ""), str(label or "")
    return "", "", ""


def _journaled_tokens(events: Sequence[JournalEvent]) -> int:
    total = 0
    for event in events:
        usage = event.data.get("usage") if event.type in ("model.response", "engine.turn_completed", "usage.recorded") else None
        if isinstance(usage, dict):
            total += int(usage.get("inputTokens") or 0) + int(usage.get("outputTokens") or 0)
    return total


__all__ = ["STEER", "GitFixture", "ResearchLane", "git_fixture"]

"""One chat on the real durable harness, driven run after run, with what a long task needs around it.

Real: ``PipesHubRuntimeAssembler`` (tools, prompt, plan routes, memory wiring, ``run_code`` and its receipts), the
``RunDriver`` and durable kernel (journal folds, shaping, the compaction ladder, ``/compact``, re-drive), the
session workspace store with ``RunCodeWorkspace`` (``$WORKSPACE_DIR``), the local code sandbox (programs really run,
on this interpreter, in a fresh folder per attempt) and the memory recall step and ``remember`` tool. Faked: only the
outermost I/O ``tests/unit/harness/assembly/world.py`` fakes (graph, config, MCP discovery) and the model, which is
a transport (``brain.LongTaskModel`` here, the org's real model in S3).

Node's part is played here where the run needs it, and only there: it admits each run (with the ``/compact`` ask
when the person typed one), accepts what the model proposed to remember (an org with review ``auto``) and pins the
accepted memories into the next run, restores a revision for ``/undo`` (``WorkspaceService.restore``: a new head
with the earlier revision's files), and erases the chat's memories with the chat
(``session-erasure-steps.ts`` ``memories``).
"""

from __future__ import annotations

import dataclasses
import json
import os
import time
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import patch

from app.agent_loop_lib.sandbox.coding.local import LocalCodingSandbox
from app.harness.assembly.assembler import PipesHubRuntimeAssembler
from app.harness.assembly.model import (  # pyright: ignore[reportPrivateUsage]
    ResolvedModel,
    _port,
)
from app.harness.assembly.run_code_workspace import RunCodeWorkspace
from harness_core.budget.types import Cap, RunAccountRef
from harness_core.contracts import dump_contract, validate_contract
from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.driver import RunDriver
from harness_core.failpoints import Arm, Nth, Raise
from harness_core.failpoints import registry as failpoints
from harness_core.journal.types import CompactAsk
from harness_core.memories.memory import InMemoryMemorySource
from harness_core.memories.types import RecalledMemory
from harness_core.testing import ManualClock
from harness_core.workspace.sync import TAR
from tests.harness_replay.artifacts import ParityArtifactRegistry
from tests.support.sandbox_env import sandbox_backend, sandbox_env
from tests.unit.harness.assembly import world as w
from tests.unit.harness_core.workspace.sync.support import SESSION, FakeSyncHarness

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from contextlib import AbstractContextManager

    from app.harness.assembly.context import RunContext
    from harness_core.budget.memory import InMemoryBudgetLedger
    from harness_core.journal.types import Claim
    from harness_core.models.transports.base import LLMTransport
    from harness_core.workspace.types import ObjectScope

CHAT = "ses-1"
MEMORY_SCOPES = ("session", "user")
LEASE_MS = 30_000
# A run that keeps asking past this is reported as it stands, not approved forever.
MAX_APPROVALS = 50
UNKNOWN_OUTCOME = "ix.unknown."


class WorkerDied(BaseException):
    """A worker process killed mid-turn: nothing in the run catches it."""


@dataclass
class RunRecord:
    run_id: str
    task: str
    status: str
    seconds: float
    # Workers that died during the run (a ``crash <point>`` step armed the failpoint).
    crashes: int = 0
    compact: str | None = None
    # The calls the run held for the person, by tool, each approved as the person would.
    approvals: list[str] = field(default_factory=list)
    # Calls whose outcome was unknown after a crash, offered for a retry the person declined.
    retries_declined: list[str] = field(default_factory=list)
    # Where each dead worker died (the failpoint), and why each attempt gave its slot back (``run.yielded``).
    crash_points: list[str] = field(default_factory=list)
    yields: list[str] = field(default_factory=list)
    # Seconds of (manual) clock between the run asking the person and the person answering.
    waited_s: list[float] = field(default_factory=list)


# Memories a correction replaced (FIX-R2 C2), as Node keeps them: id -> (the memory, the chat it came from, the
# correction that superseded it). Never recalled; deleted and erased with their correction.
Superseded = dict[str, tuple[RecalledMemory, str, str]]


def settle_correction(store: dict[str, tuple[RecalledMemory, str]], superseded: Superseded, correction: str, replaces: str) -> bool:
    """Node's settlement of an accepted correction (``MemoryService.supersede``): the memory it replaces, in the same
    scope, leaves what is recalled and is kept for erasure."""
    old, new = store.get(replaces), store.get(correction)
    if old is None or new is None or old[0].scope != new[0].scope:
        return False
    del store[replaces]
    superseded[replaces] = (old[0], old[1], correction)
    return True


def drop_history(superseded: Superseded, gone: list[str]) -> list[str]:
    """What Node's delete and erasure take with memories: those they superseded, transitively."""
    dropped: list[str] = []
    frontier = set(gone)
    while frontier:
        found = [i for i, (_, _, by) in superseded.items() if by in frontier]
        for memory_id in found:
            del superseded[memory_id]
        dropped += found
        frontier = set(found)
    return dropped


@dataclass
class Erasure:
    """What the chat's erasure removed (Node's ``memories`` step: every memory derived from the session)."""

    memories_erased: list[str]
    left_for_chat: list[str]


@dataclass
class LongTaskChat:
    """One chat. ``model`` is the transport every model call goes through."""

    model: LLMTransport
    context_length: int
    chat_mode: str = "react"
    # The org's model entry as the product resolves it: S3 names the real one (``llm`` its chat model).
    provider: str = "openai"
    is_reasoning: bool = False
    llm: Any = None
    clock: ManualClock = field(default_factory=ManualClock)
    runs: list[RunRecord] = field(default_factory=list)
    # Node's memory store for this org, as the rig plays it: id -> (memory, the chat it came from).
    memory_store: dict[str, tuple[RecalledMemory, str]] = field(default_factory=dict)
    superseded: Superseded = field(default_factory=dict)
    sources: dict[str, InMemoryMemorySource] = field(default_factory=dict)
    # Each program the sandbox ran: the request it ran for, the sandbox folder, the code.
    executions: list[tuple[str | None, str, str]] = field(default_factory=list)
    # The journal's last seq when each of those programs ran (a delegate's runs are not presented calls of the run).
    execution_seqs: list[int] = field(default_factory=list)
    undo: list[tuple[int, int]] = field(default_factory=list)
    recalled: dict[str, list[str]] = field(default_factory=dict)
    # The workspace's head revision when each run ended.
    heads: dict[str, int] = field(default_factory=dict)
    # Tools (by the end of their id) the person declines when asked, instead of approving.
    declines: frozenset[str] = frozenset()
    # The chat's history as Node's admission snapshots it into each run's input (``previousConversations``): what
    # the intent step and the legacy prompt read; the loop's own history comes from the journal.
    turns: list[dict[str, Any]] = field(default_factory=list)
    previous: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    workers: int = 0
    # The chat's id as Node's memory store keys it (a second chat of the same person has another).
    chat_id: str = CHAT
    # Run ids are ``<run_prefix>-<n>``: a second chat of the same person names its runs apart.
    run_prefix: str = "run"
    # Tools the agent is granted beyond the built-ins (``toolset.tool``), and the agent document they come from.
    tool_ids: tuple[str, ...] = ()
    agent: dict[str, Any] | None = None
    # How long a held call waits before the person answers it (S2: an approval left overnight).
    answer_after_s: float = 0.0
    # Decides each held call (tool id, its arguments) -> approve; ``None``: approve all but ``declines``.
    decide: Callable[[str, dict[str, Any]], bool] | None = None
    # Each attempt's sandbox manager (S2's reaper ends their sandboxes), and how many more code runs the sandbox
    # broker refuses while it restarts.
    sandbox_managers: list[Any] = field(default_factory=list)
    broker_down: int = 0
    # More of the world, entered inside the deployment (a fake SaaS connector the task's agent is given).
    contexts: tuple[Callable[[], AbstractContextManager[Any]], ...] = ()
    # The org's configuration as an admin set it for the task (a toolset instance the agent's connector uses).
    configure: Callable[[w.Services], None] | None = None
    # Node's budget ledger (P6.5), when the case meters the runs: each run's account opens at admission with
    # ``run_tokens`` (S2's budget cases spend it out mid-task, BUDGETEND).
    ledger: InMemoryBudgetLedger | None = None
    run_tokens: int = 1_000_000
    # A task's own turn limit (S2's turn-limit case); the others get the chat's 40.
    turn_limits: dict[str, int] = field(default_factory=dict[str, int])
    # The run the person is waiting on (``ask``).
    current_run: str | None = None
    # DEEPROUTE's engine arm: the session workspace as Node's journal names it (a child's fork is made from it), and
    # what the arm adds to the journal, the worker's driver (children, the engine kernel) and the pinned spec.
    workspace_id: str = SESSION
    workspace_scope: ObjectScope | None = None
    journal_options: Callable[[LongTaskChat], dict[str, Any]] | None = None
    driver_options: Callable[[LongTaskChat], dict[str, Any]] | None = None
    spec_hook: Callable[[dict[str, Any]], None] | None = None

    def __post_init__(self) -> None:
        self.ws = FakeSyncHarness(TAR)
        extra = self.journal_options(self) if self.journal_options is not None else {}
        self.journal = w.pipeshub_journal(clock=self.clock, lease_ms=LEASE_MS, **extra)
        self.driver = self._worker()
        if hasattr(self.model, "journal_seq"):
            setattr(self.model, "journal_seq", self.last_seq)
            setattr(self.model, "arm_crash", arm_crash)

    def last_seq(self) -> int:
        events = self.events()
        return events[-1].seq if events else 0

    @property
    def scope(self) -> ObjectScope:
        return self.workspace_scope or self.ws.object_scope

    def workspace(self) -> RunCodeWorkspace:
        return RunCodeWorkspace(store=self.ws.store, objects=self.ws.objects, scope=self.scope, workspace_id=self.workspace_id)

    # --- the deployment -----------------------------------------------------------------------------------

    @staticmethod
    def deployment() -> ExitStack:
        stack = ExitStack()
        stack.enter_context(w.deployment())
        stack.enter_context(patch("app.services.artifact_registry.ArtifactRegistryService", new=ParityArtifactRegistry(w.ORG, CHAT)))
        stack.enter_context(patch.dict(os.environ, {**sandbox_env("host"), "SANDBOX_ALLOW_NETWORK": "false"}))
        stack.enter_context(sandbox_backend("host"))
        return stack

    def watching(self) -> ExitStack:
        """Counts every program the sandbox really runs (and where), calling through to the real sandbox."""
        stack = ExitStack()
        execute = LocalCodingSandbox.execute
        chat = self

        async def counted(sandbox: Any, request: Any) -> Any:  # noqa: ANN401
            if chat.broker_down > 0:
                from app.agent_loop_lib.sandbox.coding.base import CodingSandboxError
                from harness_core.sandbox.errors import BrokerUnavailable

                # As the broker backend raises it (``coding/broker.py``): the broker's own error as the cause.
                chat.broker_down -= 1
                raise CodingSandboxError("sandbox broker unavailable: the broker is restarting") from BrokerUnavailable("restarting")
            task = getattr(chat.model, "active", None)
            chat.executions.append((task, str(getattr(sandbox, "_working_dir", "")), str(getattr(request, "code", ""))))
            chat.execution_seqs.append(chat.last_seq())
            return await execute(sandbox, request)

        stack.enter_context(patch.object(LocalCodingSandbox, "execute", counted))
        from app.agents.agent_loop import sandbox_bridge

        build = sandbox_bridge.build_coding_sandbox_manager

        async def kept(**kwargs: Any) -> Any:  # noqa: ANN401
            manager = await build(**kwargs)
            chat.sandbox_managers.append(manager)
            return manager

        stack.enter_context(patch.object(sandbox_bridge, "build_coding_sandbox_manager", kept))
        for context in self.contexts:
            stack.enter_context(context())
        return stack

    def _worker(self) -> RunDriver:
        """A worker process: its own assembler and driver over the chat's journal and stores."""
        self.workers += 1
        transport = self.model

        async def model_factory(run: RunContext, services: object) -> ResolvedModel:
            from tests.unit.agents.adapter.conftest import FakeChatModel

            return ResolvedModel(
                raw_llm=self.llm if self.llm is not None else FakeChatModel(), provider=self.provider,
                is_multimodal=False, context_length=self.context_length, is_reasoning=self.is_reasoning,
                port=_port(transport, self.provider, "longtask"), transport=transport, transport_name="longtask",
            )

        async def workspace(claim: Any, run: RunContext) -> RunCodeWorkspace:  # noqa: ANN401
            return self.workspace()

        services = w.Services(self.agent)
        if self.configure is not None:
            self.configure(services)
        assembler = PipesHubRuntimeAssembler(services, model_factory=model_factory, run_code_workspace=workspace)
        extra = self.driver_options(self) if self.driver_options is not None else {}
        return RunDriver(journal=self.journal, assembler=_ClaimAssembler(assembler, self), clock=self.clock, **extra)

    def restart_worker(self) -> None:
        """A reload: a new worker process with nothing in memory, the same journal and stores."""
        self.driver = self._worker()

    # --- Node's part ----------------------------------------------------------------------------------------

    def _spec(self, recalled: list[str], task: str | None = None) -> RunSpec:
        pinned = cast("dict[str, Any]", dump_contract(w.pinned_spec(tool_ids=list(self.tool_ids), sandbox="agent-code")))
        pinned["budgets"]["maxTurns"] = self.turn_limits.get(task or "", 40)
        pinned["extensions"]["x.pipeshub"]["memory"] = {"recalled": recalled, "writeScopes": list(MEMORY_SCOPES)}
        if self.spec_hook is not None:
            self.spec_hook(pinned)
        return validate_contract(RunSpec, pinned)

    def _recalled_for(self, chat: str) -> list[RecalledMemory]:
        """What Node pins: the actor's accepted memories (session-scoped ones only in their own chat), newest first
        (``recallCandidates``); the store keeps them in the order they were accepted."""
        return [m for m, origin in reversed(self.memory_store.values()) if m.scope != "session" or origin == chat]

    def teach(self, memory: RecalledMemory, chat: str | None = None) -> None:
        """A memory the person already has (made in ``chat``, this chat by default), as Node's store holds it."""
        self.memory_store[memory.memory_id] = (memory, chat or self.chat_id)

    def forget(self, memory_id: str) -> bool:
        """The person deletes one memory (Node's memory DELETE), with what it superseded."""
        found = self.memory_store.pop(memory_id, None) is not None
        if found:
            _ = drop_history(self.superseded, [memory_id])
        return found

    def forget_matching(self, needle: str) -> list[str]:
        """The person deletes every memory whose text holds ``needle`` (as they would find it in their list)."""
        found = [i for i, (m, _) in self.memory_store.items() if needle.lower() in str(m.content).lower()]
        for memory_id in found:
            self.forget(memory_id)
        return found

    def _accept(self, run_id: str, chat: str) -> None:
        """Review ``auto``: what the run proposed is accepted when it ends."""
        for memory_id, proposal in self.sources[run_id].proposed.items():
            label = {"complete": True, "sources": []}
            self.memory_store[memory_id] = (RecalledMemory(memory_id, proposal.scope, proposal.kind, proposal.content, label), chat)
        for memory_id, proposal in self.sources[run_id].proposed.items():
            if proposal.replaces is not None:
                _ = settle_correction(self.memory_store, self.superseded, memory_id, proposal.replaces)

    def erase_chat(self, chat: str | None = None) -> Erasure:
        chat = chat or self.chat_id
        erased = [i for i, (_, origin) in self.memory_store.items() if origin == chat]
        for memory_id in erased:
            del self.memory_store[memory_id]
        old = [i for i, (_, origin, _) in self.superseded.items() if origin == chat]
        for memory_id in old:
            del self.superseded[memory_id]
        erased += old + drop_history(self.superseded, erased + old)
        return Erasure(erased, [i for i, (_, origin) in self.memory_store.items() if origin == chat])

    async def restore_previous_revision(self) -> tuple[int, int]:
        """``/undo`` of the last change: a new head with the files of the revision before it."""
        head = await self.ws.store.head(self.scope, self.workspace_id)
        restored = await self.ws.store.rollback(self.scope, self.workspace_id, to_revision=head.revision - 1)
        self.undo.append((head.revision, restored.revision))
        return head.revision, restored.revision

    async def seed(self, files: dict[str, bytes]) -> int:
        """Files the person put in the chat's workspace before the task (``/upload``): its first revision."""
        workspace = self.workspace()
        checkout = await workspace.checkout()
        assert checkout is not None
        saved = await workspace.save(checkout, {**checkout.files, **files})
        return saved.revision if saved is not None else checkout.base.revision

    # --- driving runs -------------------------------------------------------------------------------------

    async def ask(self, task: str, text: str, *, compact: str | None = None) -> RunRecord:
        run_id = f"{self.run_prefix}-{len(self.runs) + 1}"
        recalled = self._recalled_for(self.chat_id)
        self.recalled[run_id] = [m.memory_id for m in recalled]
        self.previous[run_id] = [dict(t) for t in self.turns]
        self.sources[run_id] = InMemoryMemorySource(recalled, write_scopes=MEMORY_SCOPES, run_id=run_id)
        self.journal.admit(
            run_id, org_id=w.ORG, session_id=CHAT, spec=self._spec([m.memory_id for m in recalled], task),
            input_text=text, compact=CompactAsk(focus=compact) if compact is not None else None,
        )
        self.current_run = run_id
        if self.ledger is not None:
            _ = await self.ledger.open_root(RunAccountRef(w.ORG, run_id), Cap(None, self.run_tokens))
        setattr(self.model, "active", task)
        started = time.monotonic()
        approvals: list[str] = []
        retries: list[str] = []
        crashes = 0
        crash_points: list[str] = []
        yields: list[str] = []
        waited: list[float] = []
        while True:
            # Each attempt has its own sandboxes: one the model saw in an earlier attempt is gone.
            setattr(self.model, "epoch", getattr(self.model, "epoch", 0) + 1)
            try:
                done = await self.driver.run_claimed(await w.claim_next(self.journal, run_id))
            except WorkerDied as died:
                crashes += 1
                crash_points.append(str(died))
                failpoints.reset()
                # The lease runs out; the sweeper re-queues the run, and a new worker process claims it.
                _ = self.clock.advance(LEASE_MS / 1000 + 1)
                _ = self.journal.sweep()
                self.restart_worker()
                continue
            if str(done.status) == "suspended" and str(done.reason) == "join":
                # The run handed work to a child (an engine arm's code_task): the child runs to its end, then the
                # parent is claimed again with the child's result.
                await self._run_children(run_id)
                continue
            if str(done.status) == "yielded":
                # The provider asked to wait (or the broker had no sandbox): the run is queued again once its
                # not-before passes, and a worker claims it then.
                yields.append(str(done.reason))
                _ = self.clock.advance(self._wait_s(run_id))
                continue
            asked = self.journal.open_interactions(run_id) if str(done.status) == "suspended" else {}
            if not asked or len(approvals) >= MAX_APPROVALS:
                break
            if self.answer_after_s:
                # The person answers later: the worker that asked is long gone, a new one takes the run up.
                _ = self.clock.advance(self.answer_after_s)
                waited.append(self.answer_after_s)
                self.restart_worker()
            for interaction_id in asked:
                # A call whose outcome a dead worker never recorded is not run again: the person checks and
                # declines the retry (its effect, the log line, is already there). Anything else is approved.
                unknown = interaction_id.startswith(UNKNOWN_OUTCOME)
                tool = self._held_tool(interaction_id)
                (retries if unknown else approvals).append(tool)
                if unknown:
                    declined = True
                elif self.decide is not None:
                    declined = not self.decide(tool, self._held_args(interaction_id))
                else:
                    declined = tool.endswith(tuple(self.declines))
                self.journal.resolve_interaction(run_id, interaction_id, "denied" if declined else "approved")
        self._accept(run_id, self.chat_id)
        self.heads[run_id] = await self.head_revision()
        self.turns += [{"role": "user_query", "content": text}, {"role": "bot_response", "content": self.answer_of(run_id)}]
        record = RunRecord(
            run_id, task, str(done.status), time.monotonic() - started, crashes, compact, approvals, retries,
            crash_points, yields, waited,
        )
        self.runs.append(record)
        return record

    async def _run_children(self, run_id: str, max_attempts: int = 20) -> None:
        """Each child the run joined on, to its end: a call it holds is approved, as the person would."""
        for child in self.journal.children_of(run_id):
            for _ in range(max_attempts):
                # A child journals on its own stream.
                if any(e.type in ("run.completed", "run.failed", "run.cancelled") for e in self.journal.events(child)):
                    break
                done = await self.driver.run_claimed(await w.claim_next(self.journal, child))
                if str(done.status) == "yielded":
                    _ = self.clock.advance(self._wait_s(child))
                elif str(done.status) == "suspended":
                    for interaction_id in self.journal.open_interactions(child):
                        self.journal.resolve_interaction(child, interaction_id, "approved")

    def answer_of(self, run_id: str) -> str:
        found = [e for e in self.events() if e.type == "x.pipeshub.answer.finalized" and e.run_id == run_id]
        return str(found[-1].data.get("answer") or "") if found else ""

    def _wait_s(self, run_id: str) -> float:
        """Seconds until the run's latest ``run.yielded`` lets it be claimed again."""
        yielded = [e for e in self.events() if e.type == "run.yielded" and e.run_id == run_id]
        not_before = str(yielded[-1].data.get("notBefore") or "") if yielded else ""
        try:
            at = datetime.fromisoformat(not_before.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return 121.0
        return max(0.0, at - self.clock.now().timestamp()) + 1.0

    def _held_args(self, interaction_id: str) -> dict[str, Any]:
        """The arguments of the call ``interaction_id`` holds, as the approval card shows them (a call made through
        ``call_tool`` shows its target's arguments)."""
        events = self.events()
        asked = next((e.seq for e in events if e.type == "interaction.requested" and e.data.get("interactionId") == interaction_id), None)
        shown = [e for e in events if e.type == "x.pipeshub.tool.call_presented" and (asked is None or e.seq < asked)]
        if not shown:
            return {}
        try:
            args = json.loads(str(shown[-1].data.get("argsJson") or "{}"))
        except ValueError:
            return {}
        if isinstance(args, dict) and isinstance(args.get("arguments"), dict) and "name" in args:
            args = args["arguments"]
        return dict(args) if isinstance(args, dict) else {}

    def _held_tool(self, interaction_id: str) -> str:
        for event in reversed(self.events()):
            if event.type == "interaction.explained" and event.data.get("interactionId") == interaction_id:
                return str(event.data.get("toolId") or interaction_id)
        return interaction_id

    # --- reading back -------------------------------------------------------------------------------------

    def events(self) -> list[Any]:
        return list(self.journal.events(CHAT))

    async def files(self, revision: int | None = None) -> dict[str, str]:
        store, scope, workspace_id = self.ws.store, self.scope, self.workspace_id
        manifest = await (
            store.head(scope, workspace_id) if revision is None else store.revision(scope, workspace_id, revision)
        )
        out: dict[str, str] = {}
        for path, entry in manifest.files.items():
            out[path] = (await self.ws.objects.get(scope, entry.content)).decode("utf-8", "replace")
        return out

    async def head_revision(self) -> int:
        return (await self.ws.store.head(self.scope, self.workspace_id)).revision


def arm_crash(point: str) -> None:
    """The worker dies the next time it reaches ``point``."""
    failpoints.arm(point, Arm(Raise(WorkerDied, point), Nth(1)))


class _ClaimAssembler:
    """The in-memory journal's claim with what Node's claim carries: the run snapshot and the run's memory route."""

    def __init__(self, inner: PipesHubRuntimeAssembler, chat: LongTaskChat) -> None:
        self._inner = inner
        self._chat = chat

    async def build(self, attempt: Claim) -> Any:  # noqa: ANN401
        node = w.claim(
            attempt.spec, query=attempt.input_text, run_id=attempt.run_id, chat_mode=self._chat.chat_mode,
            previous=self._chat.previous.get(attempt.run_id),
        )
        values = {f.name: getattr(attempt, f.name) for f in dataclasses.fields(attempt)}
        claim = type(node)(**values, run=node.run, memories=self._chat.sources[attempt.run_id], budget=self._chat.ledger)
        return await self._inner.build(claim)


def chat_session(chat: LongTaskChat) -> Iterator[LongTaskChat]:
    with chat.deployment(), chat.watching():
        yield chat


__all__ = ["CHAT", "Erasure", "LongTaskChat", "RunRecord", "WorkerDied", "arm_crash", "chat_session"]

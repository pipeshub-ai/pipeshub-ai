"""Shared world for durable-kernel tests: journal, blobs, a SaaS double, tools, a scripted model."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from harness_core.context.user_compact import CONTEXT_COMPACTED_EVENT
from harness_core.effects.reconcile import (
    LookupAbsent,
    LookupFound,
    ReconcileEvidence,
    ReconcileTiming,
)
from harness_core.journal import (
    InMemoryCheckpointStore,
    InMemoryContentStore,
    InMemoryJournal,
    InMemoryLiveSink,
    LegacyImport,
)
from harness_core.journal.commit_parts import MAX_COMMIT_EVENTS
from harness_core.kernel.core.messages import AssistantMessage, ToolMessage
from harness_core.kernel.core.tool_content import tool_message_content
from harness_core.kernel.durable import DurableKernel, EffectUnknown, ToolExecution
from harness_core.kernel.durable.transcript import (
    decode_assistant,
    decode_result,
)
from harness_core.testing import (
    FakeSaaS,
    ManualClock,
    ModelScript,
    Reply,
    ScriptedModel,
    ToolCallSpec,
    Turn,
)
from harness_core.testing.durable import FunctionToolPort, ScriptedModelPort
from harness_core.testing.fake_saas import AckLost, Effect
from harness_core.testing.run_spec import minimal_run_spec

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping
    from datetime import datetime
    from pathlib import Path

    from pydantic import JsonValue

    from harness_core.contracts.generated.run_spec import RunSpec
    from harness_core.effects.reconcile import LookupResult, UnknownEffect
    from harness_core.journal import Claim, JournalEvent
    from harness_core.journal.types import Writer
    from harness_core.kernel.core.messages import Message
    from harness_core.kernel.durable import Disposition, ToolContext
    from harness_core.workspace.ports import WorkspaceStore

LEASE_MS = 30_000
RUN = "run1"
SESSION = "ses1"
ORG = "org1"


class Crash(BaseException):
    """A simulated process death: not an Exception, so no kernel handler runs."""


def reply(
    text: str = "", *calls: tuple[str, dict[str, Any]], reasoning: str = ""
) -> Reply:
    specs = [ToolCallSpec(name=name, args=args) for name, args in calls]
    return Reply(
        text=text, tool_calls=specs, reasoning=reasoning, chunks=2 if text else None
    )


def script(*replies: Reply) -> ModelScript:
    return ModelScript(id="durable", turns=[Turn(reply=r) for r in replies])


@dataclass
class World:
    """One deployment: DB, CAS, a SaaS, and the tools that call it."""

    root: Path | None = None
    db_clock: ManualClock = field(default_factory=ManualClock)
    worker_clock: ManualClock | None = None
    journal: InMemoryJournal = field(init=False)
    content: InMemoryContentStore = field(init=False)
    checkpoints: InMemoryCheckpointStore = field(init=False)
    live: InMemoryLiveSink = field(default_factory=InMemoryLiveSink)
    saas: FakeSaaS = field(init=False)
    tools: FunctionToolPort = field(default_factory=FunctionToolPort)
    lose_ack_calls: int = 0
    on_read: Callable[[World], None] | None = None
    # The compaction row the kernel itself writes when the journal accepts it (SLASH2 C2, LC2FIX-3).
    extension_writers: Mapping[str, frozenset[Writer]] = field(
        default_factory=lambda: {CONTEXT_COMPACTED_EVENT: frozenset({"worker"})}
    )
    # Set: children granted the workspace mount get forks of it (P6.4).
    workspaces: WorkspaceStore | None = None
    # UX2: a journal that plays a Node older than the worker (what its claims accept, the event types it refuses).
    journal_accepts: frozenset[str] | None = None
    journal_refuses: frozenset[str] = frozenset()
    # PERF-A: the journal's commit limit, and the one this world's kernels split by (unset: the contract's).
    max_commit_events: int = MAX_COMMIT_EVENTS

    def __post_init__(self) -> None:
        root = self.root
        self.journal = InMemoryJournal(
            clock=self.db_clock,
            lease_ms=LEASE_MS,
            oplog=root / "journal.jsonl" if root else None,
            extension_writers=self.extension_writers,
            workspaces=self.workspaces,
            accepts=self.journal_accepts,
            refuses=self.journal_refuses,
            max_commit_events=self.max_commit_events,
        )
        self.content = InMemoryContentStore(root / "cas" if root else None)
        self.checkpoints = InMemoryCheckpointStore(root / "ckpt" if root else None)
        self.saas = FakeSaaS(
            clock=self.db_clock, log_path=root / "saas.jsonl" if root else None
        )
        self._install_tools()

    @property
    def clock(self) -> ManualClock:
        return self.worker_clock or self.db_clock

    def _install_tools(self) -> None:
        async def lookup(
            args: Mapping[str, JsonValue], ctx: ToolContext
        ) -> ToolExecution:
            if self.on_read is not None:
                self.on_read(self)
            await asyncio.sleep(0)
            return ToolExecution(status="ok", content={"found": args.get("q")})

        async def create_issue(
            args: Mapping[str, JsonValue], ctx: ToolContext
        ) -> ToolExecution:
            effect = self.call_saas("create_issue", dict(args), ctx.idempotency_key)
            return ToolExecution(
                status="ok",
                content={"issue": effect.receipt_id},
                receipt={"id": effect.receipt_id},
            )

        async def post_message(
            args: Mapping[str, JsonValue], ctx: ToolContext
        ) -> ToolExecution:
            effect = self.call_saas(
                "post_message", {**args, "invocation": ctx.invocation_id}, None
            )
            return ToolExecution(status="ok", content={"posted": effect.receipt_id})

        async def flaky(
            args: Mapping[str, JsonValue], ctx: ToolContext
        ) -> ToolExecution:
            raise EffectUnknown("socket closed after send")

        self.tools.add("lookup", lookup, tags=[("category", "read")])
        self.tools.add(
            "create_issue",
            create_issue,
            tags=[("type", "create"), ("idempotent", "true")],
        )
        self.tools.add("post_message", post_message, tags=[("category", "write")])
        self.tools.add("flaky_write", flaky, tags=[("category", "write")])

    def call_saas(
        self, op: str, payload: dict[str, JsonValue], key: str | None
    ) -> Effect:
        if self.lose_ack_calls > 0:
            self.lose_ack_calls -= 1
            self.saas.lose_ack_after_commit = True
            try:
                return self.saas.call(op, payload, idempotency_key=key)
            finally:
                self.saas.lose_ack_after_commit = False
        return self.saas.call(op, payload, idempotency_key=key)

    def admit(
        self,
        run_id: str = RUN,
        *,
        text: str = "please help",
        max_turns: int = 8,
        imported: LegacyImport | None = None,
        spec: RunSpec | None = None,
    ) -> None:
        self.journal.admit(
            run_id,
            org_id=ORG,
            session_id=SESSION,
            spec=spec or minimal_run_spec(max_turns=max_turns),
            input_text=text,
            imported=imported,
        )

    def kernel(self, port: ScriptedModelPort, **overrides: object) -> DurableKernel:
        return DurableKernel(
            journal=self.journal,
            content=self.content,
            model=port,
            tools=self.tools,
            clock=self.clock,
            checkpoints=self.checkpoints,
            live=self.live,
            **{"max_commit_events": self.max_commit_events, **overrides},  # type: ignore[arg-type]
        )

    async def claim(self, run_id: str = RUN) -> Claim:
        claim = await self.journal.claim(run_id, worker_id="w")
        assert claim is not None
        return claim

    async def run(self, kernel: DurableKernel, run_id: str = RUN) -> Disposition:
        return await kernel.run_claimed(await self.claim(run_id))

    async def crash(self, kernel: DurableKernel, run_id: str = RUN) -> None:
        """Run until the armed failpoint kills the worker, then let the lease lapse."""
        try:
            _ = await self.run(kernel, run_id)
        except Crash:
            pass
        else:
            raise AssertionError("the worker was expected to crash")
        self.expire()

    def expire(self) -> None:
        _ = self.db_clock.advance(LEASE_MS / 1000 + 1)
        _ = self.journal.sweep()

    def events(
        self, type_: str | None = None, run_id: str | None = None
    ) -> list[JournalEvent]:
        return [
            e
            for e in self.journal.events(SESSION)
            if (type_ is None or e.type == type_)
            and (run_id is None or e.run_id == run_id)
        ]

    def types(self, run_id: str = RUN) -> list[str]:
        return [e.type for e in self.events(run_id=run_id)]

    async def blob(self, ref: Mapping[str, JsonValue]) -> bytes:
        from harness_core.contracts.generated.common import ContentRef

        return await self.content.get(ContentRef.model_validate(ref))


@dataclass
class NoteLookup:
    """Finds a world's SaaS effect of ``op`` by the tag its write carried, as a provider's read would."""

    world: World
    timing: ReconcileTiming = field(
        default_factory=lambda: ReconcileTiming(tool_timeout_s=60.0, read_after_write=True)
    )
    op: str = "send_note"
    fail: bool = False
    calls: list[datetime] = field(default_factory=list["datetime"])

    async def lookup(self, effect: UnknownEffect) -> LookupResult:
        self.calls.append(self.world.clock.now())
        if self.fail:
            raise ConnectionError("provider down")
        for posted in self.world.saas.effects_for(self.op):
            if posted.payload.get("tag") == effect.tag:
                return LookupFound(ReconcileEvidence({"note": posted.receipt_id}, {"id": posted.receipt_id}))
        return LookupAbsent()


def stepping(world: World) -> Callable[[float], Awaitable[None]]:
    """A sleep that moves the manual clock in steps, letting the lease keeper renew while a reconcile waits."""

    async def sleep(seconds: float) -> None:
        remaining = seconds
        while remaining > 0:
            step = min(1.0, remaining)
            _ = world.clock.advance(step)
            remaining -= step
            for _ in range(4):
                await asyncio.sleep(0)

    return sleep


def scripted(*replies: Reply) -> tuple[ScriptedModel, ScriptedModelPort]:
    model = ScriptedModel(script(*replies))
    return model, ScriptedModelPort(model)


@dataclass
class _Response:
    run_id: str | None
    turn: int
    calls: list[str]
    results: dict[str, str] = field(default_factory=dict[str, str])


async def project(world: World) -> list[tuple[str, str]]:
    """A neutral projection of the journal, independent of the kernel's renderer:
    (role, text) pairs, tool results in each response's toolCalls order."""
    items: list[tuple[str, str] | _Response] = []
    turns: dict[str | None, int] = {}
    for event in world.journal.events(SESSION):
        data = event.data
        if event.type == "turn.started":
            turns[event.run_id] = int(str(data["turn"]))
        elif event.type == "message.completed" and data["role"] == "user":
            items.append(("user", (await world.blob(_ref(data["textRef"]))).decode()))
        elif event.type == "model.response":
            message = decode_assistant(await world.blob(_ref(data["messageRef"])))
            names = [c.name for c in message.tool_calls or []]
            items.append(
                ("assistant", json.dumps({"text": message.text, "calls": names}))
            )
            calls = [c.id for c in message.tool_calls or []]
            items.append(_Response(event.run_id, turns.get(event.run_id, 1), calls))
        elif event.type == "tool.outcome":
            turn, index = (
                int(x) for x in str(data["invocationId"]).removeprefix("t").split(".")
            )
            content, _ = decode_result(await world.blob(_ref(data["resultRef"])))
            for item in items:
                if isinstance(item, _Response) and (item.run_id, item.turn) == (
                    event.run_id,
                    turn,
                ):
                    _ = item.results.setdefault(
                        item.calls[index], str(tool_message_content(content))
                    )
    out: list[tuple[str, str]] = []
    for item in items:
        if isinstance(item, _Response):
            out.extend(
                ("tool", f"{c}:{item.results.get(c, 'cancelled')}") for c in item.calls
            )
        else:
            out.append(item)
    return out


def _ref(value: JsonValue) -> Mapping[str, JsonValue]:
    assert isinstance(value, dict)
    return value


def render_pairs(messages: list[Message]) -> list[tuple[str, str]]:
    """The kernel transcript in the same neutral shape as ``project``."""
    out: list[tuple[str, str]] = []
    for m in messages:
        if isinstance(m, AssistantMessage):
            out.append(
                (
                    "assistant",
                    json.dumps(
                        {"text": m.text, "calls": [c.name for c in m.tool_calls or []]}
                    ),
                )
            )
        elif isinstance(m, ToolMessage):
            out.append(("tool", f"{m.tool_call_id}:{m.text}"))
        else:
            content = m.content
            out.append(("user", content if isinstance(content, str) else ""))
    return out


def assert_paired(messages: list[Message]) -> None:
    """Provider pairing rule: every tool_call is answered, once, before the next turn."""
    open_ids: list[str] = []
    for m in messages:
        if isinstance(m, AssistantMessage):
            assert not open_ids, f"unanswered tool calls {open_ids}"
            open_ids = [c.id for c in m.tool_calls or []]
        elif isinstance(m, ToolMessage):
            assert m.tool_call_id in open_ids, (
                f"tool result {m.tool_call_id} answers nothing"
            )
            open_ids.remove(m.tool_call_id or "")
    assert not open_ids, f"unanswered tool calls {open_ids}"


__all__ = [
    "LEASE_MS",
    "ORG",
    "RUN",
    "SESSION",
    "AckLost",
    "Crash",
    "NoteLookup",
    "World",
    "assert_paired",
    "project",
    "render_pairs",
    "reply",
    "script",
    "scripted",
    "stepping",
]

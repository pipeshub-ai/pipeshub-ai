"""Ctx — the scoped execution environment passed to every @workflow and @step.

Provides journaled primitives for replay determinism:
- ctx.now()              → datetime (journaled)
- ctx.random()           → float (journaled)
- ctx.uuid()             → str (journaled)
- ctx.tool()             → calls the host via IPlatformBroker (journaled)
- ctx.agent()            → runs a sub-agent via IPlatformBroker (journaled)
- ctx.map()              → parallel deterministic fan-out
- ctx.sleep()            → deterministic sleep (journaled)
- ctx.wait_for_event()   → suspend workflow until a matching event (D7)
- ctx.request_approval() → durable suspension waiting for human review
- ctx.log()              → structured log entry (not journaled, best-effort)
"""
from __future__ import annotations

import asyncio
import logging
import uuid as _uuid_mod
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, Coroutine, TypeVar

from app.services.workflows.runtime.replay import ReplayDivergence

if TYPE_CHECKING:
    from app.services.workflows.interface.broker import BrokerCall, BrokerResult, IPlatformBroker, RunPrincipal
    from app.services.workflows.interface.journal import IExecutionJournal
    from app.services.workflows.domain.models import JournalEntry, StepOutcome
    from app.services.workflows.interface.broker import Capability

T = TypeVar("T")

_logger = logging.getLogger(__name__)
_MAP_COUNTER_KEY = "ctx.map"
_MAX_INLINE_SLEEP_SECONDS = 60.0

__all__ = ["Ctx", "ReplayDivergence"]


class Ctx:
    """Execution context for a workflow run.

    One instance per run, shared across all @step functions invoked from
    the entry workflow function. Thread-safe (asyncio, not threading).
    """

    def __init__(
        self,
        *,
        run_id: str,
        journal: "IExecutionJournal",
        broker: "IPlatformBroker",
        principal: "RunPrincipal",
        max_concurrency: int = 32,
        in_replay: bool = False,
        is_dry_run: bool = False,
    ) -> None:
        self._run_id = run_id
        self._journal = journal
        self._broker = broker
        self._principal = principal
        self._max_concurrency = max_concurrency
        self._in_replay = in_replay
        self._is_dry_run = is_dry_run
        """True when the runner is replaying a resumed run.  Set by CodeWorkflowRunner
        before handing the Ctx to the workflow function on a resumed execution."""
        self._call_counter: dict[str, int] = {}  # qualname -> call index
        self._key_prefix = ""
        self._logs: list[dict[str, Any]] = []

    @property
    def run_id(self) -> str:
        return self._run_id

    # -- Journaled primitives -----------------------------------------------

    def _next_step_key(self, qualname: str) -> str:
        """Monotonic call index per qualname → stable replay key.

        The counter is only stable while calls happen in program order, so
        every concurrent fan-out (`ctx.map`) runs against a *branch* Ctx with
        its own counter and a structural prefix -- see `_branch`.
        """
        idx = self._call_counter.get(qualname, 0)
        self._call_counter[qualname] = idx + 1
        return f"{self._key_prefix}{qualname}#{idx}"

    def _branch(self, suffix: str) -> "Ctx":
        """A Ctx sharing this run's journal/broker but with an isolated step-key
        namespace, so keys are a function of position rather than of the order
        the event loop happened to interleave awaits in."""
        child = Ctx(
            run_id=self._run_id,
            journal=self._journal,
            broker=self._broker,
            principal=self._principal,
            max_concurrency=self._max_concurrency,
            in_replay=self._in_replay,
            is_dry_run=self._is_dry_run,
        )
        child._key_prefix = f"{self._key_prefix}{suffix}/"
        child._logs = self._logs
        return child

    async def _journal_or_replay(
        self,
        step_key: str,
        entry_kind: str,
        execute: Callable[[], Coroutine[Any, Any, Any]],
        *,
        side_effect_write: bool = False,
    ) -> Any:
        existing = await self._journal.lookup(self._run_id, step_key)
        if existing is not None:
            if existing.outcome in ("succeeded", "skipped"):
                # A "skipped" step (e.g. a conditional branch not taken on the
                # original run) must not be re-executed on replay either --
                # only its presence in the journal matters, not its outcome.
                if existing.result_ref and existing.result_ref.inline is not None:
                    return existing.result_ref.inline
                return None
            if existing.outcome == "failed":
                raise RuntimeError(f"Step {step_key} previously failed: {existing.error}")
        else:
            # No journal entry exists yet — this is a FRESH call, not a replay.
            # A @step(side_effect=WRITE) step that has no journal entry during
            # what the runner identified as a replay path MUST NOT re-execute:
            # the write already happened in a prior attempt and executing again
            # would duplicate it.  The runner sets side_effect_write=True when
            # replaying a WRITE step to surface this as a hard error rather
            # than a silent double-write.
            if side_effect_write and self._in_replay:
                raise ReplayDivergence(
                    f"Replay divergence: WRITE step {step_key!r} has no journal entry "
                    "but the runner is in replay mode. The workflow code may have changed "
                    "after a WRITE step executed, which is unsafe to re-execute."
                )

            # Dry runs must never execute WRITE-side-effected steps.
            if side_effect_write and self._is_dry_run:
                import logging as _log
                _log.getLogger(__name__).info("dry_run: skipping WRITE step %r", step_key)
                await self._journal.append(
                    self._make_entry(step_key, entry_kind, None, outcome="skipped")
                )
                return None

        result = await execute()
        await self._journal.append(self._make_entry(step_key, entry_kind, result))
        return result

    def _make_entry(
        self, step_key: str, entry_kind: str, result: Any, *, outcome: str = "succeeded"
    ) -> "JournalEntry":
        import json
        from app.services.workflows.domain.models import JournalEntry, ResultRef, StepOutcome
        try:
            inline = json.loads(json.dumps(result, default=str))
        except (TypeError, ValueError):
            inline = str(result) if result is not None else None
        outcome_enum = StepOutcome(outcome) if isinstance(outcome, str) else outcome
        return JournalEntry(
            run_id=self._run_id,
            seq=0,  # Redis adapter assigns monotonic seq on append
            step_key=step_key,
            entry_kind=entry_kind,  # type: ignore[arg-type]
            idempotency_key=step_key,
            outcome=outcome_enum,
            result_ref=ResultRef(inline=inline),
        )

    async def now(self) -> datetime:
        """Journaled current time. Use this instead of datetime.now()."""
        step_key = self._next_step_key("ctx.now")

        async def _get_now() -> datetime:
            return datetime.now(timezone.utc)

        result = await self._journal_or_replay(step_key, "clock", _get_now)
        if isinstance(result, str):
            return datetime.fromisoformat(result)
        if isinstance(result, datetime):
            return result
        return datetime.now(timezone.utc)

    async def random(self) -> float:
        """Journaled random float [0, 1). Use this instead of random.random()."""
        import random as _random

        step_key = self._next_step_key("ctx.random")
        return await self._journal_or_replay(step_key, "random", lambda: _areturn(_random.random()))

    async def uuid(self) -> str:
        """Journaled UUID4. Use this instead of uuid.uuid4()."""
        step_key = self._next_step_key("ctx.uuid")
        return await self._journal_or_replay(step_key, "uuid", lambda: _areturn(str(_uuid_mod.uuid4())))

    # -- Tool / agent calls ------------------------------------------------

    async def tool(self, tool_name: str, **kwargs: Any) -> Any:
        """Call a PipesHub tool by its Tool.name (e.g. 'jira__create_issue').
        Journaled. Runs via the host RPC broker.

        The broker normalizes dot-path and URL-path forms at the boundary
        so generated code can use any of the three naming conventions."""
        from app.services.workflows.interface.broker import BrokerCall, Capability
        step_key = self._next_step_key(f"ctx.tool:{tool_name}")

        async def _execute() -> Any:
            call = BrokerCall(
                capability=Capability.TOOL,
                target=tool_name,
                arguments=kwargs,
                run_id=self._run_id,
                step_key=step_key,
            )
            result = await self._broker.dispatch(call, self._principal)
            if not result.success:
                raise RuntimeError(f"Tool {tool_name} failed: {result.error}")
            return result.data

        return await self._journal_or_replay(step_key, "tool", _execute)

    async def agent(self, agent_id: str) -> "_AgentHandle":
        """Return a handle for running an existing agent.

        Usage:
            result = await ctx.agent("agent-uuid").run(goal="Summarize...")
        """
        return _AgentHandle(agent_id=agent_id, ctx=self)

    async def create_agent(
        self,
        name: str,
        *,
        instructions: str = "",
        tools: list[str] | None = None,
        knowledge: list[str] | None = None,
        skills: list[str] | None = None,
        mcps: list[str] | None = None,
        model: str | None = None,
        persist: bool = False,
    ) -> "_AgentHandle":
        """Create a new Agent Builder agent and return a handle to run it.

        Args:
            name: Display name for the new agent.
            instructions: System prompt / instructions.
            tools: List of Tool.name values the agent should have.
            knowledge: List of collection_ids for knowledge access.
            skills: List of skill names.
            mcps: List of MCP server IDs (validated now, enabled when MCP
                registry exists).
            model: Optional model key override (None = use org default).
            persist: If True, the agent survives after this run.
                     If False (default), it is ephemeral — not yet implemented;
                     currently always persisted.

        Returns:
            An _AgentHandle you can call .run(goal=...) on.
        """
        from app.services.workflows.interface.broker import BrokerCall, Capability
        step_key = self._next_step_key(f"ctx.create_agent:{name}")

        if mcps:
            _logger.warning(
                "ctx.create_agent: mcps=%s received but MCP registry not yet available; "
                "the agent will be created without MCP toolsets.",
                mcps,
            )

        async def _execute() -> Any:
            call = BrokerCall(
                capability=Capability.AGENT_CREATE,
                target=name,
                arguments={
                    "instructions": instructions,
                    "tools": tools or [],
                    "knowledge": knowledge or [],
                    "skills": skills or [],
                    "models": [model] if model else [],
                    "persist": persist,
                },
                run_id=self._run_id,
                step_key=step_key,
            )
            result = await self._broker.dispatch(call, self._principal)
            if not result.success:
                raise RuntimeError(f"create_agent({name!r}) failed: {result.error}")
            return result.data

        data = await self._journal_or_replay(step_key, "agent", _execute)
        agent_id = (data or {}).get("agent_id", name)
        return _AgentHandle(agent_id=agent_id, ctx=self)

    async def _agent_run(self, agent_id: str, *, goal: str, **kwargs: Any) -> Any:
        """Internal: dispatch AGENT_RUN to the broker."""
        from app.services.workflows.interface.broker import BrokerCall, Capability
        step_key = self._next_step_key(f"ctx.agent:{agent_id}")

        async def _execute() -> Any:
            call = BrokerCall(
                capability=Capability.AGENT_RUN,
                target=agent_id,
                arguments={"goal": goal, **kwargs},
                run_id=self._run_id,
                step_key=step_key,
            )
            result = await self._broker.dispatch(call, self._principal)
            if not result.success:
                raise RuntimeError(f"Agent {agent_id} failed: {result.error}")
            return result.data

        return await self._journal_or_replay(step_key, "agent", _execute)

    # -- Control flow helpers -----------------------------------------------

    async def map(
        self,
        fn: Callable[["Ctx", Any], Coroutine[Any, Any, T]],
        items: list[Any],
        *,
        concurrency: int | None = None,
    ) -> list[T]:
        """Run fn over items with bounded concurrency. Deterministic fan-out.

        Each item gets its own branch Ctx keyed by the item's index, so a
        given call's journal key depends on its position in `items` and not
        on the nondeterministic order in which the gathered coroutines reach
        their await points. Results are returned in `items` order.
        """
        sem = asyncio.Semaphore(concurrency or self._max_concurrency)
        map_index = self._call_counter.get(_MAP_COUNTER_KEY, 0)
        self._call_counter[_MAP_COUNTER_KEY] = map_index + 1

        async def _run_one(index: int, item: Any) -> T:
            async with sem:
                return await fn(self._branch(f"map{map_index}[{index}]"), item)

        return list(await asyncio.gather(*(_run_one(i, it) for i, it in enumerate(items))))

    async def sleep(self, seconds: float) -> None:
        """Journaled sleep for a short pause. On replay, no-ops.

        The sleep holds the executor slot and burns the run's wall-clock
        timeout, so anything longer than `_MAX_INLINE_SLEEP_SECONDS` is
        rejected rather than silently shortened -- a workflow that asked for
        an hour and got a minute would go on to act as though the hour had
        passed. Use a `cron`/`interval` trigger or `ctx.wait_for_event()` for
        real waits; both park the run instead of occupying a worker.
        """
        if seconds > _MAX_INLINE_SLEEP_SECONDS:
            raise ValueError(
                f"ctx.sleep({seconds}) exceeds the {_MAX_INLINE_SLEEP_SECONDS}s inline limit. "
                "Split the workflow across a cron/interval trigger, or wait on an event with "
                "ctx.wait_for_event(), so the run is parked rather than holding a worker."
            )
        step_key = self._next_step_key("ctx.sleep")
        existing = await self._journal.lookup(self._run_id, step_key)
        if existing is not None:
            return
        await asyncio.sleep(max(seconds, 0.0))
        await self._journal.append(self._make_entry(step_key, "sleep", {"slept_s": seconds}))

    async def wait_for_event(self, event_type: str, *, timeout_s: float | None = None) -> dict[str, Any]:
        """Suspend the workflow until a matching event arrives (D7 — forever-running).

        On first call: parks in AWAITING_INPUT. On replay: returns the journaled event payload.
        """
        step_key = self._next_step_key(f"ctx.wait_for_event:{event_type}")
        existing = await self._journal.lookup(self._run_id, step_key)
        if existing is not None and existing.result_ref and existing.result_ref.inline is not None:
            return existing.result_ref.inline  # type: ignore[return-value]
        raise _WaitForEventSuspension(event_type=event_type, step_key=step_key, timeout_s=timeout_s)

    async def request_approval(self, label: str, *, payload: Any = None) -> bool:
        """Durable suspension waiting for human approval.

        Returns True if approved, False if denied.
        """
        step_key = self._next_step_key(f"ctx.request_approval:{label}")
        existing = await self._journal.lookup(self._run_id, step_key)
        if existing is not None and existing.result_ref and existing.result_ref.inline is not None:
            return bool(existing.result_ref.inline)
        raise _ApprovalSuspension(label=label, step_key=step_key, payload=payload)

    # -- Knowledge / search -------------------------------------------------

    async def search(
        self,
        query: str,
        *,
        collections: list[str] | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Search the knowledge base. Journaled.

        Args:
            query: Natural-language search query.
            collections: Optional list of collection_ids to scope the search.
                Defaults to all collections the run is granted access to.
            limit: Maximum number of results to return.
        """
        from app.services.workflows.interface.broker import BrokerCall, Capability
        step_key = self._next_step_key(f"ctx.search:{query[:40]}")

        async def _execute() -> Any:
            call = BrokerCall(
                capability=Capability.KNOWLEDGE_SEARCH,
                target=query,
                arguments={"collections": collections or [], "limit": limit},
                run_id=self._run_id,
                step_key=step_key,
            )
            result = await self._broker.dispatch(call, self._principal)
            if not result.success:
                raise RuntimeError(f"Knowledge search failed: {result.error}")
            return result.data

        return await self._journal_or_replay(step_key, "knowledge", _execute) or []

    # -- Durable per-workflow state -----------------------------------------

    @property
    def state(self) -> "_StateProxy":
        """Durable key/value store scoped to this workflow across all runs.

        Usage:
            last_id = await ctx.state.get("last_issue_id")
            await ctx.state.set("last_issue_id", "JRA-123")
        """
        return _StateProxy(ctx=self)

    async def _state_get(self, key: str) -> Any:
        from app.services.workflows.interface.broker import BrokerCall, Capability
        step_key = self._next_step_key(f"ctx.state.get:{key}")

        async def _execute() -> Any:
            call = BrokerCall(
                capability=Capability.STATE_GET,
                target=key,
                arguments={},
                run_id=self._run_id,
                step_key=step_key,
            )
            result = await self._broker.dispatch(call, self._principal)
            if not result.success:
                raise RuntimeError(f"State get for key {key!r} failed: {result.error}")
            return result.data

        return await self._journal_or_replay(step_key, "state", _execute)

    async def _state_set(self, key: str, value: Any) -> None:
        from app.services.workflows.interface.broker import BrokerCall, Capability
        step_key = self._next_step_key(f"ctx.state.set:{key}")

        async def _execute() -> Any:
            call = BrokerCall(
                capability=Capability.STATE_SET,
                target=key,
                arguments={"value": value},
                run_id=self._run_id,
                step_key=step_key,
            )
            result = await self._broker.dispatch(call, self._principal)
            if not result.success:
                raise RuntimeError(f"State set for key {key!r} failed: {result.error}")
            return None

        await self._journal_or_replay(step_key, "state", _execute, side_effect_write=True)

    # -- Conversation emit --------------------------------------------------

    async def emit(self, message: str, *, kind: str = "text") -> None:
        """Post a message back into the originating conversation.

        This is what makes execution results appear in chat.  The
        NodeConversationWriter on the host side handles the actual HTTP call
        to the Node.js API.

        Args:
            message: Text, markdown, or structured content.
            kind: "text" (default), "code", "error", "card".
        """
        from app.services.workflows.interface.broker import BrokerCall, Capability
        step_key = self._next_step_key(f"ctx.emit:{kind}")

        async def _execute() -> Any:
            call = BrokerCall(
                capability=Capability.CONVERSATION_EMIT,
                target=kind,
                arguments={"message": message},
                run_id=self._run_id,
                step_key=step_key,
            )
            result = await self._broker.dispatch(call, self._principal)
            if not result.success:
                _logger.warning("ctx.emit failed: %s", result.error)
            return None

        await self._journal_or_replay(step_key, "emit", _execute, side_effect_write=True)

    # -- Structured logging -------------------------------------------------

    def log(self, message: str, **data: Any) -> None:
        """Structured log (not journaled — best-effort trace)."""
        entry = {"run_id": self._run_id, "message": message, **data}
        self._logs.append(entry)
        _logger.info("[workflow:%s] %s %s", self._run_id, message, data)

    @property
    def logs(self) -> list[dict[str, Any]]:
        return list(self._logs)


class _AgentHandle:
    """Lightweight proxy returned by ctx.agent(agent_id).

    Usage:
        result = await ctx.agent("my-agent-uuid").run(goal="Summarize...", input={...})
    """
    __slots__ = ("_agent_id", "_ctx")

    def __init__(self, *, agent_id: str, ctx: "Ctx") -> None:
        self._agent_id = agent_id
        self._ctx = ctx

    async def run(self, *, goal: str, **kwargs: Any) -> Any:
        """Run the agent with a goal and optional kwargs forwarded as input."""
        return await self._ctx._agent_run(self._agent_id, goal=goal, **kwargs)


class _StateProxy:
    """Durable state accessor returned by ctx.state."""
    __slots__ = ("_ctx",)

    def __init__(self, *, ctx: "Ctx") -> None:
        self._ctx = ctx

    async def get(self, key: str) -> Any:
        return await self._ctx._state_get(key)

    async def set(self, key: str, value: Any) -> None:
        await self._ctx._state_set(key, value)


async def _areturn(value: Any) -> Any:
    return value


class _WaitForEventSuspension(Exception):
    def __init__(self, *, event_type: str, step_key: str, timeout_s: float | None) -> None:
        super().__init__(f"Workflow suspended waiting for event: {event_type}")
        self.event_type = event_type
        self.step_key = step_key
        self.timeout_s = timeout_s


class _ApprovalSuspension(Exception):
    def __init__(self, *, label: str, step_key: str, payload: Any) -> None:
        super().__init__(f"Workflow suspended waiting for approval: {label}")
        self.label = label
        self.step_key = step_key
        self.payload = payload

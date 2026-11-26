"""The systems a wave 1 suite runs on.

The runner hands every system the same :class:`SystemTask` and grades the
:class:`SystemRun` it returns, so a new system is one class implementing
:class:`System`; nothing in the suites, graders or runner changes. G2's
``engine_code_task`` system (stage 1) was retired with stage 1 (S1R); the
``engine_*`` fields of :class:`SystemRun` stay so its committed reports load.

:class:`TodayLoopSystem` is PipesHub's agent loop as it ships: the legacy
agent route (``run_agent_loop_stream``) in quick mode, driven through
``tests/harness_replay/legacy_driver``. Data files are seeded as
conversation artifacts and the knowledge fixture is served through the
product's knowledge search tool (see ``legacy_driver.DriveSpec``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, cast

from tests.contracts.golden_support import CONVERSATION_ID, ORG_ID
from tests.harness_replay.artifacts import ParityArtifactRegistry
from tests.harness_replay.knowledge_base import CLIENT_TIME, CLIENT_TIMEZONE
from tests.harness_replay.legacy_driver import DriveSpec, drive, final_result
from tests.harness_replay.world import WorldFixtures
from tests.support.parity_cases.wave1_cases import DATA_DIR, load_knowledge
from tests.support.sandbox_env import (
    SandboxKind,
    broker_lane,
    sandbox_backend,
    sandbox_env,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.agent_loop_lib.transport.base import LLMTransport
    from harness_core.testing import ModelScript
    from tests.harness_replay.legacy_driver import DriveResult
    from tests.harness_replay.world import Doc
    from tests.support.parity_cases.wave1_cases import Wave1Case

RECORD_PREFIX = "rec-"


@dataclass(frozen=True)
class InputFile:
    name: str
    content: bytes


@dataclass(frozen=True)
class SystemTask:
    case_id: str
    prompt: str
    trial: int
    inputs: tuple[InputFile, ...] = ()
    knowledge: tuple[Doc, ...] = ()
    script: ModelScript | None = None


@dataclass(frozen=True)
class OutputFile:
    name: str
    mime_type: str
    content: bytes


@dataclass(frozen=True)
class SystemRun:
    answer: str
    status: str
    files: tuple[OutputFile, ...] = ()
    cited_sources: tuple[str, ...] = ()
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    model_calls: int = 0
    tool_calls: tuple[str, ...] = field(default_factory=tuple[str, ...])
    first_progress_s: float | None = None
    total_s: float = 0.0
    error: str | None = None
    # The engine's share of the tokens above (``code_task``'s engine; 0 for a system without one), and what
    # each ``code_task`` call returned to the parent.
    engine_input_tokens: int = 0
    engine_output_tokens: int = 0
    engine_outputs: tuple[str, ...] = ()
    # EV4: the engine's cached reads (inside ``engine_input_tokens``) and what E3's ledger charged for its calls
    # (``None``: not known); the durable loop's reductions and its largest prompt.
    engine_cache_read_tokens: int = 0
    engine_metered_usd: float | None = None
    context_cleared: int = 0
    context_compacted: int = 0
    peak_input_tokens: int = 0
    # G2C: each engine model call's (uncached input, cached reads, output); ``None`` where the lane cannot tell.
    engine_calls: tuple[tuple[int, int, int], ...] | None = None
    # G2C-2: fixture docs the run's knowledge tools rendered to it, parent and engine (``None``: unknown).
    read_sources: tuple[str, ...] | None = None


class System(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str: ...

    async def run(self, task: SystemTask) -> SystemRun: ...


def cited_sources(sse_result: dict[str, Any]) -> tuple[str, ...]:
    """Fixture doc ids behind the product's ``citations`` list, in order."""
    out: list[str] = []
    citations: list[Any] = sse_result.get("citations") or []
    for citation in citations:
        if not isinstance(citation, dict):
            continue
        metadata: dict[str, Any] = cast("dict[str, Any]", citation).get("metadata") or {}
        record = str(metadata.get("recordId") or "")
        doc = metadata.get("virtualRecordId") or (
            record[len(RECORD_PREFIX) :] if record.startswith(RECORD_PREFIX) else record
        )
        if doc:
            out.append(str(doc))
    return tuple(out)


def _upload_turns(names: list[str]) -> list[dict[str, str]]:
    listed = ", ".join(names)
    return [
        {"role": "user_query", "content": f"I'm attaching {listed} for you to work with."},
        {"role": "bot_response", "content": f"Thanks - {listed} is saved in this conversation."},
    ]


class TodayLoopSystem:
    """PipesHub's agent loop today. Scripted when ``transport_factory`` is None."""

    def __init__(
        self,
        *,
        transport_factory: Callable[[], LLMTransport] | None = None,
        model: str = "scripted-model",
        sandbox: SandboxKind = "host",
        chat_mode: str = "quick",
        knowledge_snippets: bool = False,
    ) -> None:
        self._transport_factory = transport_factory
        self._model = model
        self._sandbox: SandboxKind = sandbox
        self._chat_mode = chat_mode
        self._knowledge_snippets = knowledge_snippets

    @property
    def name(self) -> str:
        return "today_loop"

    @property
    def model(self) -> str:
        return self._model

    async def run(self, task: SystemTask) -> SystemRun:
        return await in_sandbox_lane(self._sandbox, lambda suffix: self._run(task, suffix))

    async def _run(self, task: SystemTask, suffix: str) -> SystemRun:
        registry = _registry(task)
        env = sandbox_env(self._sandbox, suffix=suffix)
        spec = _spec(task, registry, chat_mode=self._chat_mode, model=self._model, env=env)
        spec.knowledge_snippets = self._knowledge_snippets
        _model_for(spec, task, self._transport_factory)
        try:
            with sandbox_backend(self._sandbox):
                result = await drive(spec)
        except Exception as exc:
            return _crashed(exc)
        return _system_run(result, registry)


async def in_sandbox_lane(kind: SandboxKind | None, run: Callable[[str], Awaitable[SystemRun]]) -> SystemRun:
    """``run`` inside the trial's sandbox lane: an in-process Docker broker per trial for ``broker`` (its suffix
    names the lane's network), nothing to open for the other kinds."""
    if kind != "broker":
        return await run("")
    try:
        async with broker_lane() as suffix:
            return await run(suffix)
    except Exception as exc:
        return _crashed(exc)


def task_for(case: Wave1Case, trial: int, script: ModelScript | None) -> SystemTask:
    """The task every system gets for a case: its prompt, its data files as uploads, the knowledge fixture."""
    return SystemTask(
        case_id=case.id,
        prompt=case.task,
        trial=trial,
        inputs=tuple(InputFile(n, (DATA_DIR / n).read_bytes()) for n in case.inputs),
        knowledge=load_knowledge() if case.knowledge else (),
        script=script,
    )


def _registry(task: SystemTask) -> ParityArtifactRegistry:
    registry = ParityArtifactRegistry(ORG_ID, CONVERSATION_ID)
    for item in task.inputs:
        registry.seed(item.name, item.content)
    return registry


def _spec(task: SystemTask, registry: ParityArtifactRegistry, *, chat_mode: str, model: str, env: dict[str, str]) -> DriveSpec:
    return DriveSpec(
        scenario=task.case_id,
        query=task.prompt,
        chat_mode=chat_mode,
        fixtures=WorldFixtures(knowledge=list(task.knowledge)),
        env=env,
        model_name=model,
        knowledge_search=bool(task.knowledge),
        # An agent with the knowledge base attached and code execution on,
        # and no connector toolsets: the wave 1 tasks need none.
        fake_toolsets=(),
        artifacts=registry,
        previous_turns=_upload_turns([i.name for i in task.inputs]) if task.inputs else [],
        timezone=CLIENT_TIMEZONE,
        current_time=CLIENT_TIME,
    )


def _model_for(spec: DriveSpec, task: SystemTask, transport_factory: Callable[[], LLMTransport] | None) -> None:
    if transport_factory is not None:
        spec.transport = transport_factory()
        return
    if task.script is None:
        raise ValueError(f"{task.case_id}: the scripted system needs a script")
    spec.script = task.script


def _crashed(exc: Exception) -> SystemRun:
    return SystemRun(answer="", status="crashed", error=f"{type(exc).__name__}: {exc}"[:300])


def _system_run(result: DriveResult, registry: ParityArtifactRegistry, *, extra_input: int = 0, extra_output: int = 0) -> SystemRun:
    recording = result.recording
    usage = result.usage
    errors = [s.error.kind for s in recording.model_steps() if s.error is not None]
    return SystemRun(
        answer=recording.final.answer,
        status=recording.final.status,
        files=tuple(OutputFile(f.name, f.mime_type, f.content) for f in registry.outputs()),
        cited_sources=cited_sources(final_result(result.sse)),
        input_tokens=usage.input_tokens + extra_input,
        output_tokens=usage.output_tokens + extra_output,
        cache_read_tokens=usage.cache_read_tokens,
        model_calls=len(recording.model_steps()),
        tool_calls=tuple(s.name for s in recording.tool_steps()),
        first_progress_s=result.first_progress_s,
        total_s=result.latency_s,
        error=f"model error: {', '.join(errors)}" if errors else None,
        engine_input_tokens=extra_input,
        engine_output_tokens=extra_output,
        peak_input_tokens=max(
            (s.response.usage.input_tokens for s in recording.model_steps() if s.response is not None), default=0
        ),
        read_sources=tuple(sorted(result.shown_records)) if result.shown_records is not None else None,
    )


__all__ = [
    "InputFile",
    "OutputFile",
    "System",
    "SystemRun",
    "SystemTask",
    "TodayLoopSystem",
    "cited_sources",
    "in_sandbox_lane",
    "task_for",
]

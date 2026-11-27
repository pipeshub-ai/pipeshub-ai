"""Today's durable loop as a wave 1 system (EV4): agent chat through ``RunDriver`` and the real assembler.

The same task :class:`~tests.support.parity_cases.wave1_systems.TodayLoopSystem` gets (the knowledge fixture behind the
product's search tool, data files as conversation artifacts with the "attached" turns), driven through
``tests/harness_replay/durable_driver`` instead of the legacy route. Without ``sandbox`` the spec is pinned
``sandboxProfile: none`` (no ``run_code``: the reports suite only, as EV4 ran it); with one it is pinned
``agent-code`` as Node pins an agent run once a code sandbox is proved (P5.11), and ``run_code`` runs in that
lane's backend, as ``TodayLoopSystem``'s does.
``context_compiler`` sets ``HARNESS_CONTEXT_COMPILER`` for the run, so the two arms of P4.6's comparison differ
in that flag alone. The journal gives what the legacy route cannot: how often the reduction ladder fired.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from app.utils.env_flags import HARNESS_CONTEXT_COMPILER
from tests.support.parity_cases.wave1_systems import (
    SystemRun,
    _crashed,  # pyright: ignore[reportPrivateUsage]
    _model_for,  # pyright: ignore[reportPrivateUsage]
    _registry,  # pyright: ignore[reportPrivateUsage]
    _spec,  # pyright: ignore[reportPrivateUsage]
    _system_run,  # pyright: ignore[reportPrivateUsage]
    cited_sources,
    in_sandbox_lane,
)
from tests.support.sandbox_env import sandbox_backend, sandbox_env

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from app.agent_loop_lib.transport.base import LLMTransport
    from tests.support.parity_cases.wave1_systems import SystemTask
    from tests.support.sandbox_env import SandboxKind

ANSWER_EVENT = "x.pipeshub.answer.finalized"
CLEARED = "context.cleared"
COMPACTED = "context.compacted"


def reductions(events: Sequence[Any]) -> tuple[int, int]:
    """``context.cleared`` and ``context.compacted`` events in a journal."""
    types = [e.type for e in events]
    return types.count(CLEARED), types.count(COMPACTED)


def finalized_citations(events: Sequence[Any]) -> tuple[str, ...]:
    """Fixture doc ids behind the last finalized answer's citations (the durable route's ``RUN_FINISHED``)."""
    answers = [e.data for e in events if e.type == ANSWER_EVENT]
    return cited_sources({"citations": answers[-1].get("citations") or []}) if answers else ()


class DurableLoopSystem:
    """PipesHub's durable agent path (P1a) in quick mode. Scripted when ``transport_factory`` is None."""

    def __init__(
        self,
        *,
        transport_factory: Callable[[], LLMTransport] | None = None,
        model: str = "scripted-model",
        chat_mode: str = "quick",
        knowledge_snippets: bool = False,
        context_compiler: bool = False,
        sandbox: SandboxKind | None = None,
    ) -> None:
        self._transport_factory = transport_factory
        self._model = model
        self._chat_mode = chat_mode
        self._knowledge_snippets = knowledge_snippets
        self._context_compiler = context_compiler
        self._sandbox: SandboxKind | None = sandbox

    @property
    def name(self) -> str:
        return "durable_loop"

    @property
    def model(self) -> str:
        return self._model

    async def run(self, task: SystemTask) -> SystemRun:
        return await in_sandbox_lane(self._sandbox, lambda suffix: self._run(task, suffix))

    async def _run(self, task: SystemTask, suffix: str) -> SystemRun:
        from tests.harness_replay.durable_driver import drive

        registry = _registry(task)
        env = {HARNESS_CONTEXT_COMPILER: "true" if self._context_compiler else "false"}
        if self._sandbox is not None:
            env |= sandbox_env(self._sandbox, suffix=suffix)
        spec = _spec(task, registry, chat_mode=self._chat_mode, model=self._model, env=env)
        spec.knowledge_snippets = self._knowledge_snippets
        if self._sandbox is not None:
            spec.sandbox_profile = "agent-code"
        _model_for(spec, task, self._transport_factory)
        try:
            if self._sandbox is None:
                result = await drive(spec)
            else:
                with sandbox_backend(self._sandbox):
                    result = await drive(spec)
        except Exception as exc:
            return _crashed(exc)
        events = result.events or []
        cleared, compacted = reductions(events)
        return replace(
            _system_run(result, registry),
            cited_sources=finalized_citations(events),
            context_cleared=cleared,
            context_compacted=compacted,
        )


__all__ = ["DurableLoopSystem", "finalized_citations", "reductions"]

"""Contract suite for ``AfterRunStep`` (DP19, UX5): work after a run's answer that never delays or changes its end.

Subclass :class:`AfterRunStepContract` once per adapter and provide an ``after_run_case`` fixture (a fresh
:class:`AfterRunCase` per test). The step is exercised alone (``prepare``/``run``/``discard``) and inside the durable
kernel over the in-memory journal, which commits the run's end before the step's work may happen.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest

from harness_core.journal import InMemoryContentStore, InMemoryJournal
from harness_core.journal.types import Complete
from harness_core.kernel.durable import DurableKernel
from harness_core.kernel.durable.driver import settle_after_run
from harness_core.testing.clock import ManualClock
from harness_core.testing.contracts.durable_ports import SLOT_KEY, ending
from harness_core.testing.durable import FunctionToolPort, ScriptedModelPort
from harness_core.testing.model_script import ModelScript, Reply, Turn
from harness_core.testing.run_spec import minimal_run_spec
from harness_core.testing.scripted_model import ScriptedModel

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from harness_core.kernel.durable.ports import AfterRunStep, RunEndingOutcome

RUN = "run-dp19"
ORG = "org-dp19"
SESSION = "ses-dp19"


@dataclass
class AfterRunCase:
    """An adapter under test and what the contract observes of it.

    ``step`` does its work when run; ``failing`` is the same kind of step whose work fails. ``works`` counts the
    times the step's effect happened (e.g. its result delivered); ``held`` and ``charged`` read the budget it reserved and
    the budget it spent (both 0 for a step that reserves nothing, which sets ``reserves`` False).
    """

    step: AfterRunStep
    failing: AfterRunStep
    works: Callable[[], int]
    held: Callable[[], Awaitable[int]]
    charged: Callable[[], Awaitable[int]]
    reserves: bool = True


class _Kernel:
    """One in-memory deployment running one run with the step, answering ``answers`` in order."""

    def __init__(self, *answers: str) -> None:
        self.journal = InMemoryJournal(clock=ManualClock(), lease_ms=30_000)
        self.journal.admit(RUN, org_id=ORG, session_id=SESSION, spec=minimal_run_spec(), input_text="please help")
        script = ModelScript(id="dp19", turns=[Turn(reply=Reply(text=text)) for text in answers])
        self.port = ScriptedModelPort(ScriptedModel(script))
        self.works_at_end: list[int] = []

    def observe(self, works: Callable[[], int], *, queue_once: bool = False) -> None:
        """Records, at each completion the journal accepts, whether the step's work had already happened."""
        original = self.journal.commit
        queued: list[bool] = []

        async def commit(request: Any) -> Any:  # noqa: ANN401 - the journal's own request and receipt
            if isinstance(request.transition, Complete):
                if queue_once and not queued:
                    queued.append(True)
                    _ = self.journal.queue_input(RUN, "and also")
                else:
                    self.works_at_end.append(works())
            return await original(request)

        self.journal.commit = commit  # type: ignore[method-assign]

    async def run(self, step: AfterRunStep) -> str:
        kernel = DurableKernel(
            journal=self.journal,
            content=InMemoryContentStore(),
            model=self.port,
            tools=FunctionToolPort(),
            clock=ManualClock(),
            after_run=[step],
        )
        claim = await self.journal.claim(RUN, worker_id="w")
        assert claim is not None
        disposition = await kernel.run_claimed(claim)
        return disposition.status

    def last_event(self) -> str:
        return self.journal.events(SESSION)[-1].type


class AfterRunStepContract:
    @pytest.fixture
    def after_run_case(self) -> AfterRunCase:
        raise NotImplementedError("provide an after_run_case fixture")

    def test_dp19_1_the_key_names_one_step(self, after_run_case: AfterRunCase) -> None:
        assert SLOT_KEY.match(after_run_case.step.key)

    @pytest.mark.parametrize("outcome", ["needs_input", "stopped", "error"])
    async def test_dp19_2_an_unanswered_run_prepares_nothing_and_holds_nothing(
        self, after_run_case: AfterRunCase, outcome: RunEndingOutcome
    ) -> None:
        assert await after_run_case.step.prepare(ending(outcome)) is None
        assert await after_run_case.held() == 0

    async def test_dp19_3_preparing_does_no_work_and_holds_the_budget(self, after_run_case: AfterRunCase) -> None:
        prepared = await after_run_case.step.prepare(ending("answered"))
        assert prepared is not None
        assert after_run_case.works() == 0
        assert (await after_run_case.held() > 0) is after_run_case.reserves
        await after_run_case.step.discard(prepared)

    async def test_dp19_4_the_work_settles_its_reservation(self, after_run_case: AfterRunCase) -> None:
        prepared = await after_run_case.step.prepare(ending("answered"))
        assert prepared is not None
        await after_run_case.step.run(prepared)
        assert after_run_case.works() == 1
        assert await after_run_case.held() == 0
        assert (await after_run_case.charged() > 0) is after_run_case.reserves

    async def test_dp19_5_a_discard_releases_without_work_or_charge(self, after_run_case: AfterRunCase) -> None:
        prepared = await after_run_case.step.prepare(ending("answered"))
        assert prepared is not None
        await after_run_case.step.discard(prepared)
        assert after_run_case.works() == 0
        assert await after_run_case.held() == 0
        assert await after_run_case.charged() == 0

    async def test_dp19_6_failed_work_releases_its_reservation(self, after_run_case: AfterRunCase) -> None:
        prepared = await after_run_case.failing.prepare(ending("answered"))
        assert prepared is not None
        # A step may raise (the kernel logs and drops it); its budget must be free either way.
        with contextlib.suppress(Exception):
            await after_run_case.failing.run(prepared)
        assert await after_run_case.held() == 0

    async def test_dp19_7_the_work_happens_only_after_the_terminal_commit(self, after_run_case: AfterRunCase) -> None:
        world = _Kernel("The deploy finished at 10:42.")
        world.observe(after_run_case.works)
        assert await world.run(after_run_case.step) == "completed"
        await settle_after_run()
        assert world.works_at_end == [0]
        assert after_run_case.works() == 1
        assert world.last_event() == "run.completed"
        assert await after_run_case.held() == 0

    async def test_dp19_8_a_refused_completion_discards_and_the_answer_after_it_works(
        self, after_run_case: AfterRunCase
    ) -> None:
        world = _Kernel("first answer", "second answer")
        world.observe(after_run_case.works, queue_once=True)
        assert await world.run(after_run_case.step) == "completed"
        await settle_after_run()
        assert world.works_at_end == [0]
        assert after_run_case.works() == 1
        assert await after_run_case.held() == 0

    async def test_dp19_9_a_failure_never_changes_the_run_s_end(self, after_run_case: AfterRunCase) -> None:
        world = _Kernel("The deploy finished at 10:42.")
        assert await world.run(after_run_case.failing) == "completed"
        await settle_after_run()
        assert world.last_event() == "run.completed"
        assert await after_run_case.held() == 0


__all__ = ["AfterRunCase", "AfterRunStepContract"]
